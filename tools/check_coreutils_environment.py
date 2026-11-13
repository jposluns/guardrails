#!/usr/bin/env python3
"""Report which implementation of each inventoried coreutils program a run resolves. Offline, stdlib only.

The repository's tests execute real coreutils programs (cp, mv, install, truncate, rm and others), and some of
their assertions depend on how a given implementation parses options or words its diagnostics. A local pass on
one implementation does not predict a CI run on another, so this gate records, for every program in the
reviewed inventory .aiqt/coreutils-tests.json and in every execution environment it declares, which file the
run actually selects and what that file says it is:

  check_coreutils_environment.py              probe and report; a recognised non-GNU program warns
  check_coreutils_environment.py --ci         the same, but a recognised non-GNU program fails (exit 1)
  check_coreutils_environment.py --self-test  the fixture and mutant vectors (no real coreutils program run)

A run is in CI mode with --ci or when the environment variable GITHUB_ACTIONS is exactly true (GitHub Actions
sets it on every step), so the Quality workflow step and the local runner launch the same command.

Environments. The inventory declares each one: the runner's own PATH (this process's PATH, the one the
launching runner passed on), and fixed PATH values such as the /usr/bin:/bin PATH under which the removal
differential self-test of .preview/record-remove-check.py runs Bash. Each program is resolved in each
environment on its own, never inferred from another program: the first entry of that PATH, in order, holding a
regular file of that name that this process may execute is selected (an empty entry is the current directory,
as execvp reads it; a directory or a non-executable file of that name is passed over). The report names the
selected absolute path and, separately, its fully resolved symlink target.

Probe. The selected file is run directly by its absolute path, never through a shell, as <path> --version,
with stdin from /dev/null, stderr discarded, an environment of exactly LC_ALL=C, LANG=C and the environment's
PATH (so POSIXLY_CORRECT and every other inherited variable is dropped), in a new session, for at most
PROBE_TIMEOUT seconds and OUTPUT_LIMIT bytes of stdout; past either bound its whole process group is killed.
The first stdout line must be exactly '<program> (GNU <family>) <version>' or '<program> (uutils <family>)
<version>', naming the program probed and the family (coreutils or diffutils) the inventory declares for it.

Outcomes, one per program and environment: OK (a recognised banner), MISSING (no executable of that name on
that PATH, or PATH unset), PROBE_FAILED (the file could not be started, exited other than 0, or printed more
than OUTPUT_LIMIT bytes), TIMEOUT, and UNRECOGNIZED (exit 0 but any other first line: another program's
banner, another family, another implementation, an empty or malformed line).

Each run prints one report: a header naming the probe and the mode, one block per environment with its PATH
and one line per program (name, outcome, path, symlink target, implementation, version and, for a non-OK
outcome, the reason), then one SCOPE: line (the implementation scope a caller can carry into its own result
line without probing again) and the verdict line.

Exit convention (matches the repo's gates):
  0  every program in every environment is recognised: all GNU (PASS), or, outside CI mode, at least one
     recognised non-GNU program (a WARNING line names each; the scope is local, not CI-equivalent)
  1  CI mode and at least one recognised program is not GNU
  2  cannot evaluate: the inventory is unreadable, not UTF-8 JSON, repeats a key or leaves its closed schema;
     any program is MISSING, PROBE_FAILED, TIMEOUT or UNRECOGNIZED in any environment; the arguments are not
     recognised

NOT CHECKED: a version banner is the program's own claim, not an authentication of the binary; matching
implementations do not imply matching versions or matching behaviour; PATH changes, a file replaced after the
probe, shell functions, aliases, builtins (the shell's own echo, for one) and wrappers a test reaches some other
way are outside a startup report; the inventory is a reviewed list, not derived from the tests, so a program a
test executes that the inventory omits is not probed; a descendant of a probe that closes its stdout and
outlives the probe is not stopped; a program reached through an absolute path a test hard-codes is probed only
where an environment's PATH selects that same file.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_coreutils_environment.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import importlib.util
import json
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from collections import namedtuple
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVENTORY_REL = ".aiqt/coreutils-tests.json"
PROBE_ARGUMENT = "--version"
PROBE_LOCALE = "C"
PROBE_TIMEOUT = 10.0
OUTPUT_LIMIT = 65536
CI_OPTION = "--ci"
CI_VARIABLE = "GITHUB_ACTIONS"
CI_VALUE = "true"
OK = "OK"
MISSING = "MISSING"
PROBE_FAILED = "PROBE_FAILED"
TIMEOUT = "TIMEOUT"
UNRECOGNIZED = "UNRECOGNIZED"
IMPLEMENTATIONS = ("GNU", "uutils")
FAMILIES = ("coreutils", "diffutils")
ROLES = ("preview-dependency", "ancillary")
RUNNER_SOURCE = "runner"
TOP_KEYS = frozenset(("format-version", "note", "expected-ci-implementation", "environments", "programs"))
PROGRAM_KEYS = frozenset(("name", "family", "role"))
RUNNER_ENV_KEYS = frozenset(("id", "path-source", "description"))
FIXED_ENV_KEYS = frozenset(("id", "path", "description"))
NAME_RE = re.compile(r"[a-z][a-z0-9_-]*", re.ASCII)
BANNER_RE = re.compile(r"([A-Za-z0-9_.+\[-]+) \((GNU|uutils) (coreutils|diffutils)\) ([0-9][0-9A-Za-z.+~-]*)",
                       re.ASCII)
# A report value printed bare; any other value is printed as a JSON string, so a control character or a space
# in a path cannot forge or split a report line.
BARE_RE = re.compile(r"[A-Za-z0-9_./+:@,=~-]+", re.ASCII)
SCOPE_LABELS = {
    "match": "coreutils matches CI ({}) by implementation, not by version",
    "differs": "coreutils differs from CI ({}); see the per-program report",
    "incomplete": "coreutils identity incomplete; see the per-program report",
}

Row = namedtuple("Row", "environment name path target implementation version outcome reason")


class InventoryError(ValueError):
    """The inventory is outside its closed schema."""


def _pairs(pairs):
    """A JSON object's pairs as a dict, refusing a repeated key (json keeps only the last copy)."""
    out = {}
    for key, value in pairs:
        if key in out:
            raise InventoryError("the key {!r} is repeated in one object".format(key))
        out[key] = value
    return out


def _text(value):
    return type(value) is str and value.strip() != ""


def load_inventory(text):
    """((expected implementation, programs, environments), None) for the inventory text, or (None, why not).
    programs is a tuple of (name, family, role); environments a tuple of (id, PATH or None for the runner's)."""
    try:
        data = json.loads(text, object_pairs_hook=_pairs)
    except InventoryError as exc:
        return None, str(exc)
    except (ValueError, RecursionError, MemoryError) as exc:
        return None, "not valid JSON: {}".format(type(exc).__name__)
    if type(data) is not dict or set(data) != TOP_KEYS:
        return None, "the top level is not an object with exactly the keys {}".format(", ".join(sorted(TOP_KEYS)))
    if type(data["format-version"]) is not int or data["format-version"] != 1:
        return None, "format-version is not the integer 1"
    if not _text(data["note"]):
        return None, "note is not a nonempty string"
    if data["expected-ci-implementation"] != "GNU":
        return None, "expected-ci-implementation is not GNU"
    programs = data["programs"]
    if type(programs) is not list or not programs:
        return None, "programs is not a nonempty list"
    seen = set()
    rows = []
    for index, program in enumerate(programs):
        if type(program) is not dict or set(program) != PROGRAM_KEYS:
            return None, "programs[{}] is not an object with exactly the keys family, name and role".format(index)
        name, family, role = program["name"], program["family"], program["role"]
        if type(name) is not str or NAME_RE.fullmatch(name) is None:
            return None, "programs[{}] has a name outside [a-z][a-z0-9_-]*".format(index)
        if name in seen:
            return None, "programs[{}] repeats the program {}".format(index, name)
        if family not in FAMILIES or role not in ROLES:
            return None, "programs[{}] ({}) has a family other than {} or a role other than {}".format(
                index, name, " or ".join(FAMILIES), " or ".join(ROLES))
        seen.add(name)
        rows.append((name, family, role))
    environments = data["environments"]
    if type(environments) is not list or not environments:
        return None, "environments is not a nonempty list"
    ids = set()
    envs = []
    for index, environment in enumerate(environments):
        if type(environment) is not dict or set(environment) not in (RUNNER_ENV_KEYS, FIXED_ENV_KEYS):
            return None, ("environments[{}] is not an object with exactly id, description and either path-source "
                          "or path".format(index))
        env_id = environment["id"]
        if type(env_id) is not str or NAME_RE.fullmatch(env_id) is None or env_id in ids:
            return None, "environments[{}] has an id outside [a-z][a-z0-9_-]* or a repeated one".format(index)
        if not _text(environment["description"]):
            return None, "environments[{}] ({}) has no description".format(index, env_id)
        if "path-source" in environment:
            if environment["path-source"] != RUNNER_SOURCE:
                return None, "environments[{}] ({}) has a path-source other than runner".format(index, env_id)
            envs.append((env_id, None))
        else:
            value = environment["path"]
            if (type(value) is not str or not value
                    or not all(entry.startswith("/") for entry in value.split(os.pathsep))):
                return None, "environments[{}] ({}) has a path that is not absolute directories joined by {}".format(
                    index, env_id, os.pathsep)
            envs.append((env_id, value))
        ids.add(env_id)
    if [path for _id, path in envs].count(None) != 1:
        return None, "environments does not hold exactly one environment with path-source runner"
    return ("GNU", tuple(rows), tuple(envs)), None


def resolve(name, path_value):
    """The absolute path of the first executable regular file named name in path_value's entries, in order, or
    None. An empty entry is the current directory, as execvp reads it."""
    for directory in path_value.split(os.pathsep):
        candidate = os.path.join(directory or ".", name)
        try:
            info = os.stat(candidate)
        except (OSError, ValueError):
            continue
        if stat.S_ISREG(info.st_mode) and os.access(candidate, os.X_OK):
            return os.path.abspath(candidate)
    return None


def _kill_group(proc):
    """Kill the probe's process group while its leader is not yet reaped, so the group id is still its own."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def probe(executable, path_value, timeout, limit):
    """(failure outcome or None, exit code, stdout bytes, reason) for one direct launch of executable --version."""
    env = {"LC_ALL": PROBE_LOCALE, "LANG": PROBE_LOCALE, "PATH": path_value}
    try:
        proc = subprocess.Popen([executable, PROBE_ARGUMENT], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, env=env, close_fds=True, start_new_session=True)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return PROBE_FAILED, None, b"", "it could not be started ({})".format(type(exc).__name__)
    with proc:
        chunks = []
        size = 0
        failure = None
        reason = ""
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failure, reason = TIMEOUT, "it did not finish within {:g} s".format(timeout)
                    break
                if not selector.select(remaining):
                    continue
                chunk = os.read(proc.stdout.fileno(), 4096)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    failure, reason = PROBE_FAILED, "it printed more than {} bytes".format(limit)
                    break
                chunks.append(chunk)
        returncode = None
        if failure is None:
            try:
                returncode = proc.wait(max(deadline - time.monotonic(), 0))
            except subprocess.TimeoutExpired:
                failure, reason = TIMEOUT, "it did not exit within {:g} s".format(timeout)
        if failure is not None:
            _kill_group(proc)
            proc.wait()
            return failure, None, b"", reason
    return None, returncode, b"".join(chunks), ""


def parse_banner(name, family, output):
    """(implementation, version) when the first line of output is the recognised banner of program name in
    family, otherwise None."""
    first = output.split(b"\n", 1)[0].decode("ascii", "replace")
    match = BANNER_RE.fullmatch(first)
    if match is None or match.group(1) != name or match.group(3) != family:
        return None
    return match.group(2), match.group(4)


def identify(environment, program, path_value, timeout, limit):
    """The report row of one program in one environment, resolved and probed on its own."""
    name, family = program[0], program[1]
    if path_value is None:
        return Row(environment, name, "", "", "", "", MISSING, "PATH is unset")
    selected = resolve(name, path_value)
    if selected is None:
        return Row(environment, name, "", "", "", "", MISSING, "no executable {} on this PATH".format(name))
    target = os.path.realpath(selected)
    failure, returncode, output, reason = probe(selected, path_value, timeout, limit)
    if failure is not None:
        return Row(environment, name, selected, target, "", "", failure, reason)
    if returncode != 0:
        return Row(environment, name, selected, target, "", "", PROBE_FAILED,
                   "{} {} exited {}".format(name, PROBE_ARGUMENT, returncode))
    parsed = parse_banner(name, family, output)
    if parsed is None:
        first = output.split(b"\n", 1)[0][:120].decode("ascii", "replace")
        return Row(environment, name, selected, target, "", "", UNRECOGNIZED,
                   "its first line {} is not '{} (GNU|uutils {}) <version>'".format(json.dumps(first), name, family))
    return Row(environment, name, selected, target, parsed[0], parsed[1], OK, "")


def verdict(rows, expected, ci):
    """(exit code, verdict line, scope) for the rows."""
    incomplete = [row for row in rows if row.outcome != OK]
    if incomplete:
        return 2, "CANNOT EVALUATE: {} program probe(s) did not identify an implementation: {}".format(
            len(incomplete), ", ".join("{} in {} {}".format(row.name, row.environment, row.outcome)
                                       for row in incomplete)), "incomplete"
    differs = [row for row in rows if row.implementation != expected]
    named = ", ".join("{} in {} is {} {}".format(row.name, row.environment, row.implementation, row.version)
                      for row in differs)
    if differs and ci:
        return 1, "FAIL: CI mode requires {} for every program, and {} differ: {}".format(
            expected, len(differs), named), "differs"
    if differs:
        return 0, ("WARNING: {} recognised program(s) are not {}, the CI implementation, so this local run does "
                   "not predict CI: {}".format(len(differs), expected, named)), "differs"
    return 0, "PASS: every program in every environment reports {}".format(expected), "match"


def _show(value):
    return value if BARE_RE.fullmatch(value) else json.dumps(value)


def scope_label(scope, ci, expected):
    return "{} scope; {}".format("CI" if ci else "local", SCOPE_LABELS[scope].format(expected))


def evaluate(programs, environments, expected, ci, timeout=PROBE_TIMEOUT, limit=OUTPUT_LIMIT):
    """(exit code, report lines, rows, scope) for the programs in each (id, PATH or None) environment."""
    rows = []
    lines = ["COREUTILS ENVIRONMENT: each program run directly as <path> {} (no shell), LC_ALL={} LANG={}, at "
             "most {:g} s and {} bytes; expected CI implementation {}; {} mode".format(
                 PROBE_ARGUMENT, PROBE_LOCALE, PROBE_LOCALE, timeout, limit, expected, "CI" if ci else "local")]
    for env_id, path_value in environments:
        found = [identify(env_id, program, path_value, timeout, limit) for program in programs]
        rows.extend(found)
        lines.append("environment {}: PATH={}".format(env_id, "(unset)" if path_value is None else _show(path_value)))
        for row in found:
            lines.append("  {}: outcome={} path={} target={} implementation={} version={}{}".format(
                row.name, row.outcome, _show(row.path or "-"), _show(row.target or "-"), row.implementation or "-",
                _show(row.version or "-"), "" if not row.reason else " reason={}".format(json.dumps(row.reason))))
    code, line, scope = verdict(rows, expected, ci)
    lines.append("SCOPE: " + scope_label(scope, ci, expected))
    lines.append(line)
    return code, lines, rows, scope


def run(root=ROOT, environ=None, ci=False, timeout=PROBE_TIMEOUT, limit=OUTPUT_LIMIT):
    """Read the inventory under root, probe every environment and print the report; the exit code."""
    environ = os.environ if environ is None else environ
    try:
        text = (Path(root) / INVENTORY_REL).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print("CANNOT EVALUATE: {} cannot be read as UTF-8: {}".format(INVENTORY_REL, type(exc).__name__))
        return 2
    inventory, why = load_inventory(text)
    if inventory is None:
        print("CANNOT EVALUATE: {}: {}".format(INVENTORY_REL, why))
        return 2
    expected, programs, environments = inventory
    runner_path = environ.get("PATH")
    environments = tuple((env_id, runner_path if path_value is None else path_value)
                         for env_id, path_value in environments)
    ci = ci or environ.get(CI_VARIABLE) == CI_VALUE
    code, lines, _rows, _scope = evaluate(programs, environments, expected, ci, timeout, limit)
    print("\n".join(lines))
    return code


# --------------------------------------------------------------------------------------------- self-test

# The plan's reviewed inventory, held here so an edit to the inventory file is a reviewed self-test change too.
REVIEWED_PROGRAMS = (
    ("cp", "coreutils", "preview-dependency"), ("mv", "coreutils", "preview-dependency"),
    ("install", "coreutils", "preview-dependency"), ("tee", "coreutils", "preview-dependency"),
    ("truncate", "coreutils", "preview-dependency"), ("rm", "coreutils", "preview-dependency"),
    ("cat", "coreutils", "preview-dependency"), ("dd", "coreutils", "preview-dependency"),
    ("ln", "coreutils", "preview-dependency"), ("unlink", "coreutils", "preview-dependency"),
    ("shred", "coreutils", "preview-dependency"), ("env", "coreutils", "preview-dependency"),
    ("date", "coreutils", "preview-dependency"), ("wc", "coreutils", "preview-dependency"),
    ("ls", "coreutils", "preview-dependency"), ("sleep", "coreutils", "ancillary"),
    ("timeout", "coreutils", "ancillary"), ("nice", "coreutils", "ancillary"), ("echo", "coreutils", "ancillary"),
    ("touch", "coreutils", "ancillary"), ("dirname", "coreutils", "ancillary"), ("mkdir", "coreutils", "ancillary"),
    ("rmdir", "coreutils", "ancillary"), ("mktemp", "coreutils", "ancillary"), ("cmp", "diffutils", "ancillary"),
    ("chmod", "coreutils", "ancillary"))
REVIEWED_ENVIRONMENTS = (("runner-path", None), ("removal-oracle", "/usr/bin:/bin"))

# Each mutant reverts one handling with one literal substitution of this file's own source, and names the
# vector that must turn red under it.
MUTANTS = (
    ("independent-resolution",
     "        found = [identify(env_id, program, path_value, timeout, limit) for program in programs]\n",
     "        found = [identify(env_id, programs[0], path_value, timeout, limit)._replace(name=program[0])\n"
     "                 for program in programs]\n",
     "mixed-path"),
    ("path-precedence",
     "    for directory in path_value.split(os.pathsep):\n",
     "    for directory in reversed(path_value.split(os.pathsep)):\n",
     "path-precedence"),
    ("symlink-target",
     "    target = os.path.realpath(selected)\n",
     "    target = selected\n",
     "symlink"),
    ("missing-classified",
     "        return Row(environment, name, \"\", \"\", \"\", \"\", MISSING, \"no executable {} on this PATH\".format(name))\n",
     "        selected = os.path.join(path_value.split(os.pathsep)[0], name)\n",
     "missing"),
    ("failure-verdict",
     "    incomplete = [row for row in rows if row.outcome != OK]\n",
     "    incomplete = []\n",
     "missing"),
    ("nonzero-exit",
     "    if returncode != 0:\n",
     "    if False:\n",
     "nonzero-exit"),
    ("timeout-bound",
     "        deadline = time.monotonic() + timeout\n",
     "        deadline = time.monotonic() + timeout * 1000\n",
     "timeout"),
    ("output-bound",
     "                if size > limit:\n",
     "                if False:\n",
     "oversized-output"),
    ("banner-check",
     "    if match is None or match.group(1) != name or match.group(3) != family:\n        return None\n",
     "    if match is None:\n        return \"GNU\", \"0\"\n",
     "malformed-output"),
    ("probe-environment",
     "    env = {\"LC_ALL\": PROBE_LOCALE, \"LANG\": PROBE_LOCALE, \"PATH\": path_value}\n",
     "    env = dict(os.environ, PATH=path_value)\n",
     "gnu"),
    ("ci-mismatch-fails",
     "    if differs and ci:\n",
     "    if differs and False:\n",
     "uutils"),
    ("ci-variable",
     "    ci = ci or environ.get(CI_VARIABLE) == CI_VALUE\n",
     "    ci = ci\n",
     "run-ci-variable"),
)


def _load_mutant(base, label, old, new):
    """This file's own source with old replaced by new (old must occur exactly once), loaded from a scratch copy
    under base."""
    source = Path(__file__).read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise RuntimeError("mutant {}: its text occurs {} times, not once".format(label, source.count(old)))
    path = Path(base) / "mutant_{}.py".format(label.replace("-", "_"))
    path.write_text(source.replace(old, new), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("_coreutils_environment_mutant_" + label.replace("-", "_"),
                                                  str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixtures(base):
    """The executable fixtures under base: {key: directory}, and the probe log every fixture appends to. Each
    fixture is a /bin/sh script that logs its own path, its arguments, their count and the locale and
    POSIXLY_CORRECT values it received, then prints what its kind prints."""
    log = Path(base) / "probe.log"
    if "'" in str(log):
        raise RuntimeError("the self-test directory {} holds a quote".format(base))
    stamp = "printf '%s|%s|%s|%s|%s|%s\\n' \"$0\" \"$*\" \"$#\" \"${LC_ALL-unset}\" \"${LANG-unset}\" " \
            "\"${POSIXLY_CORRECT-unset}\" >> '" + str(log) + "'\n"
    sleeper = "'{}' -I -c 'import time; time.sleep(3)'\n".format(sys.executable)
    flood = "'{}' -I -c 'import sys; sys.stdout.write(\"x\" * 100000)'\n".format(sys.executable)
    kinds = {
        "gnu": lambda name, family: "printf '{} (GNU {}) 9.4\\n'\n".format(name, family),
        "uutils": lambda name, family: "printf '{} (uutils {}) 0.2.2\\n'\n".format(name, family),
        "nonzero": lambda name, family: "printf '{} (GNU {}) 9.4\\n'\nexit 3\n".format(name, family),
        "slow": lambda name, family: sleeper + "printf '{} (GNU {}) 9.4\\n'\n".format(name, family),
        "malformed": lambda name, family: "printf 'hello from {}\\n'\n".format(name),
        "empty": lambda name, family: "",
        "other-name": lambda name, family: "printf 'mv (GNU {}) 9.4\\n'\n".format(family),
        "other-family": lambda name, family: "printf '{} (GNU diffutils) 3.10\\n'\n".format(name),
        "busybox": lambda name, family: "printf 'BusyBox v1.36.1 (2024-01-01) multi-call binary.\\n'\n",
        "flood": lambda name, family: flood,
    }
    dirs = {}

    def make(key, kind, names, mode=0o755):
        directory = Path(base) / key
        directory.mkdir()
        for name in names:
            script = directory / name
            script.write_text("#!/bin/sh\n" + stamp + kinds[kind](name, "coreutils"))
            script.chmod(mode)
        dirs[key] = str(directory)

    make("gnu", "gnu", ("cp", "mv", "cat"))
    make("uutils", "uutils", ("cp", "mv"))
    make("mixed-a", "gnu", ("cp",))
    make("mixed-b", "uutils", ("mv",))
    make("first", "gnu", ("cp",))
    make("second", "uutils", ("cp",))
    make("noexec", "uutils", ("cp",), mode=0o644)
    make("nonzero", "nonzero", ("cp",))
    make("slow", "slow", ("cat",))
    make("malformed", "malformed", ("cp",))
    make("empty", "empty", ("cp",))
    make("other-name", "other-name", ("cp",))
    make("other-family", "other-family", ("cp",))
    make("busybox", "busybox", ("cp",))
    make("flood", "flood", ("cp",))
    make("real", "gnu", ("cp",))
    os.rename(os.path.join(dirs["real"], "cp"), os.path.join(dirs["real"], "gnu-cp"))
    linked = Path(base) / "linked"
    linked.mkdir()
    os.symlink(os.path.join(dirs["real"], "gnu-cp"), str(linked / "cp"))
    dirs["linked"] = str(linked)
    empty = Path(base) / "nothing"
    empty.mkdir()
    dirs["nothing"] = str(empty)
    (Path(base) / "notadir").mkdir()
    (Path(base) / "notadir" / "cp").mkdir()
    dirs["notadir"] = str(Path(base) / "notadir")
    return dirs, log


def _log_lines(log):
    try:
        text = log.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    finally:
        try:
            log.unlink()
        except FileNotFoundError:
            pass
    return [tuple(line.split("|")) for line in text.splitlines()]


CP = ("cp", "coreutils", "preview-dependency")
MV = ("mv", "coreutils", "preview-dependency")
CAT = ("cat", "coreutils", "preview-dependency")


def _vectors(m, dirs, log):
    """{vector name: (got, want)} for module m, every vector over the fixtures."""
    out = {}

    def rows_of(result):
        return [(row.name, row.outcome, row.implementation, row.version) for row in result[2]]

    _log_lines(log)
    gnu = m.evaluate((CP, MV), (("runner", dirs["gnu"]),), "GNU", False)
    out["gnu"] = ((gnu[0], gnu[3], rows_of(gnu), gnu[1][-1].split(":")[0], _log_lines(log)),
                  (0, "match", [("cp", "OK", "GNU", "9.4"), ("mv", "OK", "GNU", "9.4")], "PASS",
                   [(os.path.join(dirs["gnu"], "cp"), "--version", "1", "C", "C", "unset"),
                    (os.path.join(dirs["gnu"], "mv"), "--version", "1", "C", "C", "unset")]))
    gnu_ci = m.evaluate((CP, MV), (("runner", dirs["gnu"]),), "GNU", True)
    out["gnu-ci"] = ((gnu_ci[0], gnu_ci[3]), (0, "match"))
    local = m.evaluate((CP, MV), (("runner", dirs["uutils"]),), "GNU", False)
    ci = m.evaluate((CP, MV), (("runner", dirs["uutils"]),), "GNU", True)
    out["uutils"] = ((local[0], local[3], local[1][-1].split(":")[0], local[1][-2], rows_of(local),
                      ci[0], ci[1][-1].split(":")[0], ci[1][-2]),
                     (0, "differs", "WARNING", "SCOPE: local scope; coreutils differs from CI (GNU); see the "
                      "per-program report", [("cp", "OK", "uutils", "0.2.2"), ("mv", "OK", "uutils", "0.2.2")],
                      1, "FAIL", "SCOPE: CI scope; coreutils differs from CI (GNU); see the per-program report"))
    mixed = m.evaluate((CP, MV), (("runner", os.pathsep.join((dirs["mixed-a"], dirs["mixed-b"]))),), "GNU", False)
    out["mixed-path"] = ((mixed[0], rows_of(mixed), [row.path for row in mixed[2]]),
                         (0, [("cp", "OK", "GNU", "9.4"), ("mv", "OK", "uutils", "0.2.2")],
                          [os.path.join(dirs["mixed-a"], "cp"), os.path.join(dirs["mixed-b"], "mv")]))
    first = m.evaluate((CP,), (("runner", os.pathsep.join((dirs["first"], dirs["second"]))),), "GNU", False)
    skipped = m.evaluate((CP,), (("runner", os.pathsep.join((dirs["noexec"], dirs["notadir"], dirs["first"]))),),
                         "GNU", False)
    out["path-precedence"] = ((first[0], first[2][0].path, first[2][0].implementation, skipped[2][0].path),
                              (0, os.path.join(dirs["first"], "cp"), "GNU", os.path.join(dirs["first"], "cp")))
    linked = m.evaluate((CP,), (("runner", dirs["linked"]),), "GNU", False)
    out["symlink"] = ((linked[0], linked[2][0].path, linked[2][0].target),
                      (0, os.path.join(dirs["linked"], "cp"), os.path.realpath(os.path.join(dirs["real"], "gnu-cp"))))
    missing = m.evaluate((CP, MV), (("runner", dirs["mixed-a"]),), "GNU", False)
    unset = m.evaluate((CP,), (("runner", None),), "GNU", False)
    out["missing"] = ((missing[0], rows_of(missing), missing[3], unset[0], rows_of(unset)),
                      (2, [("cp", "OK", "GNU", "9.4"), ("mv", "MISSING", "", "")], "incomplete", 2,
                       [("cp", "MISSING", "", "")]))
    nonzero = m.evaluate((CP,), (("runner", dirs["nonzero"]),), "GNU", False)
    out["nonzero-exit"] = ((nonzero[0], rows_of(nonzero)), (2, [("cp", "PROBE_FAILED", "", "")]))
    started = time.monotonic()
    slow = m.evaluate((CAT,), (("runner", dirs["slow"]),), "GNU", False, timeout=0.5)
    elapsed = time.monotonic() - started
    out["timeout"] = ((slow[0], rows_of(slow), elapsed < 2.5), (2, [("cat", "TIMEOUT", "", "")], True))
    flood = m.evaluate((CP,), (("runner", dirs["flood"]),), "GNU", False)
    out["oversized-output"] = ((flood[0], rows_of(flood)), (2, [("cp", "PROBE_FAILED", "", "")]))
    bad = []
    for key in ("malformed", "empty", "other-name", "other-family", "busybox"):
        result = m.evaluate((CP,), (("runner", dirs[key]),), "GNU", False)
        bad.append((key, result[0], rows_of(result)))
    out["malformed-output"] = (bad, [(key, 2, [("cp", "UNRECOGNIZED", "", "")])
                                     for key in ("malformed", "empty", "other-name", "other-family", "busybox")])
    both = m.evaluate((CP,), (("runner", dirs["gnu"]), ("fixed", dirs["uutils"])), "GNU", False)
    out["two-environments"] = (
        (both[0], [(row.environment, row.implementation) for row in both[2]], both[1][1], both[1][3]),
        (0, [("runner", "GNU"), ("fixed", "uutils")], "environment runner: PATH=" + json.dumps(dirs["gnu"])
         if not BARE_RE.fullmatch(dirs["gnu"]) else "environment runner: PATH=" + dirs["gnu"],
         "environment fixed: PATH=" + (dirs["uutils"] if BARE_RE.fullmatch(dirs["uutils"])
                                        else json.dumps(dirs["uutils"]))))
    _log_lines(log)
    return out


def _run_vectors(base, dirs, log):
    """{vector name: (got, want)} for run() over a fixture root whose inventory names fixture directories."""
    root = Path(base) / "root"
    (root / ".aiqt").mkdir(parents=True)
    inventory = {"format-version": 1, "note": "fixture", "expected-ci-implementation": "GNU",
                 "environments": [{"id": "runner-path", "path-source": "runner", "description": "the runner's"},
                                  {"id": "fixed", "path": dirs["gnu"], "description": "a fixed PATH"}],
                 "programs": [{"name": "cp", "family": "coreutils", "role": "preview-dependency"}]}
    (root / INVENTORY_REL).write_text(json.dumps(inventory), encoding="utf-8")
    return root


def _inventory_vectors(m):
    """{vector name: (got, want)} for load_inventory: the live file, then each refusal."""
    out = {}
    live = (ROOT / INVENTORY_REL).read_bytes().decode("utf-8")
    inventory, why = m.load_inventory(live)
    out["live-inventory"] = ((why, inventory and inventory[1], inventory and inventory[2]),
                             (None, REVIEWED_PROGRAMS, REVIEWED_ENVIRONMENTS))
    good = json.loads(live)

    def variant(change):
        data = json.loads(json.dumps(good))
        change(data)
        return json.dumps(data)

    refusals = (
        ("not-json", "{"),
        ("repeated-key", live.replace('"format-version": 1,', '"format-version": 1, "format-version": 1,', 1)),
        ("unknown-key", variant(lambda d: d.update(extra=1))),
        ("version-bool", variant(lambda d: d.update({"format-version": True}))),
        ("version-two", variant(lambda d: d.update({"format-version": 2}))),
        ("expected-uutils", variant(lambda d: d.update({"expected-ci-implementation": "uutils"}))),
        ("no-programs", variant(lambda d: d.update(programs=[]))),
        ("repeated-program", variant(lambda d: d["programs"].append(dict(d["programs"][0])))),
        ("bad-name", variant(lambda d: d["programs"][0].update(name="../cp"))),
        ("bad-family", variant(lambda d: d["programs"][0].update(family="busybox"))),
        ("bad-role", variant(lambda d: d["programs"][0].update(role="other"))),
        ("program-extra-key", variant(lambda d: d["programs"][0].update(note="x"))),
        ("no-runner-env", variant(lambda d: d["environments"].pop(0))),
        ("two-runner-envs", variant(lambda d: d["environments"].append(
            {"id": "again", "path-source": "runner", "description": "x"}))),
        ("relative-path", variant(lambda d: d["environments"][1].update(path="/usr/bin:bin"))),
        ("empty-path-entry", variant(lambda d: d["environments"][1].update(path="/usr/bin::/bin"))),
        ("both-path-keys", variant(lambda d: d["environments"][1].update({"path-source": "runner"}))),
        ("repeated-env-id", variant(lambda d: d["environments"][1].update(id="runner-path"))),
        ("empty-description", variant(lambda d: d["environments"][1].update(description=" "))),
        ("empty-note", variant(lambda d: d.update(note=""))),
    )
    for name, text in refusals:
        out["refuses-" + name] = (m.load_inventory(text)[0], None)
    return out


def self_test():
    """The fixture vectors over this module, then each mutant red on its named vector."""
    _child_status_refusal_exit("check_coreutils_environment.py")
    failures = []
    count = 0
    saved = os.environ.get("POSIXLY_CORRECT")
    base = tempfile.mkdtemp(prefix="coreutils-environment-")
    try:
        os.environ["POSIXLY_CORRECT"] = "1"
        dirs, log = _fixtures(base)
        module = sys.modules[__name__]
        vectors = _vectors(module, dirs, log)
        vectors.update(_inventory_vectors(module))
        root = _run_vectors(base, dirs, log)
        for name, environ, ci, want in (
                ("run-local", {"PATH": dirs["gnu"]}, False, 0),
                ("run-local-uutils", {"PATH": dirs["uutils"]}, False, 0),
                ("run-ci-option", {"PATH": dirs["uutils"]}, True, 1),
                ("run-ci-variable", {"PATH": dirs["uutils"], CI_VARIABLE: CI_VALUE}, False, 1),
                ("run-ci-variable-other-value", {"PATH": dirs["uutils"], CI_VARIABLE: "1"}, False, 0),
                ("run-path-unset", {}, False, 2),
                ("run-missing-root", {"PATH": dirs["gnu"]}, False, 2)):
            with open(os.devnull, "w", encoding="utf-8") as sink:
                saved_stdout = sys.stdout
                sys.stdout = sink
                try:
                    got = run(Path(base) / "absent" if name == "run-missing-root" else root, environ, ci)
                finally:
                    sys.stdout = saved_stdout
            vectors[name] = (got, want)
        for args, want in ((["--bogus"], 2), (["--ci", "--self-test"], 2), (["--self-test", "--ci"], 2)):
            with open(os.devnull, "w", encoding="utf-8") as sink:
                saved_stdout = sys.stdout
                sys.stdout = sink
                try:
                    got = main(args)
                finally:
                    sys.stdout = saved_stdout
            vectors["arguments-" + " ".join(args)] = (got, want)
        for name, (got, want) in sorted(vectors.items()):
            count += 1
            if got != want:
                failures.append("{}: got {!r}, expected {!r}".format(name, got, want))
        # Each mutant reverts one handling; its named vector must turn red. run-ci-variable is a run() vector,
        # so that mutant's red is read through run() over the same fixture root.
        for label, old, new, target in MUTANTS:
            count += 1
            try:
                mutant = _load_mutant(base, label, old, new)
                if target == "run-ci-variable":
                    with open(os.devnull, "w", encoding="utf-8") as sink:
                        saved_stdout = sys.stdout
                        sys.stdout = sink
                        try:
                            got = mutant.run(root, {"PATH": dirs["uutils"], CI_VARIABLE: CI_VALUE}, False)
                        finally:
                            sys.stdout = saved_stdout
                    red = got != 1
                else:
                    got, want = _vectors(mutant, dirs, log)[target]
                    red = got != want
            except Exception as exc:  # a mutant that crashes is not a mutant the vector caught
                failures.append("mutant {}: raised {}: {}".format(label, type(exc).__name__, exc))
                continue
            if not red:
                failures.append("mutant {}: the {} vector stayed green with that handling reverted".format(
                    label, target))
    finally:
        if saved is None:
            os.environ.pop("POSIXLY_CORRECT", None)
        else:
            os.environ["POSIXLY_CORRECT"] = saved
        shutil.rmtree(base, ignore_errors=True)
    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: {} coreutils-environment vectors and mutants, each of the {} mutants red on its "
          "vector".format(count, len(MUTANTS)))
    return 0


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args == ["--self-test"]:
        return self_test()
    if args == [CI_OPTION]:
        return run(ci=True)
    if args:
        print("CANNOT EVALUATE: unrecognized arguments {!r}; only --self-test or {} is accepted".format(
            args, CI_OPTION))
        return 2
    return run()


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This gate reads the exit status of every
# probe, and its self-test reads the exit status of every fixture, so under an ignored SIGCHLD it would
# read a failing probe as a pass; it refuses at its entry line and at the start of self_test() instead.
# The status the probe child exits with; any value other than 0 and the refusal exit would do.
_CHILD_STATUS_PROBE_CODE = 77


def _child_status_refusal():
    """None when this process can read a child's exit status, otherwise the reason it cannot."""
    import os
    import signal
    if not hasattr(signal, "SIGCHLD"):
        return None
    disposition = signal.getsignal(signal.SIGCHLD)
    if disposition is not signal.SIG_DFL:
        return ("SIGCHLD is ignored (SIG_IGN) in this process" if disposition is signal.SIG_IGN
                else "SIGCHLD is not at its default disposition in this process")
    if not hasattr(os, "fork"):
        return None
    try:
        pid = os.fork()
    except OSError as exc:
        return "the child-status probe could not fork ({})".format(type(exc).__name__)
    if pid == 0:
        os._exit(_CHILD_STATUS_PROBE_CODE)
    try:
        waited, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return "the probe child was reaped before its exit status could be read (ECHILD)"
    except OSError as exc:
        return "the probe child's exit status could not be read ({})".format(type(exc).__name__)
    if waited != pid or os.waitstatus_to_exitcode(status) != _CHILD_STATUS_PROBE_CODE:
        return "a probe child that exited {} was read back as wait status {}".format(
            _CHILD_STATUS_PROBE_CODE, status)
    return None


def _child_status_refusal_exit(name):
    """0 when a child's exit status is observable; otherwise write the named refusal and exit 2 at once
    (os._exit, so no exit-time stream flush can replace the status)."""
    import os
    reason = _child_status_refusal()
    if reason is None:
        return 0
    try:
        sys.stderr.write("error: {} refuses to run: {}, so a failed child would read as a success. Nothing "
                         "was run (cannot evaluate). Start it from a parent that leaves SIGCHLD at "
                         "SIG_DFL.\n".format(name, reason))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(_child_status_refusal_exit("check_coreutils_environment.py") or main())
