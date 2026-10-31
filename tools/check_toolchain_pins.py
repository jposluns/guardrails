#!/usr/bin/env python3
"""Toolchain-pin gate (toolchain-pins): the toolchains a verification runs on are the versions the adopter
declares, and the CI files state those same versions. Offline, stdlib only, fail-closed.

The gate is ADOPTER-CONFIGURED: it ships no toolchain, version, CI path or job name of its own. The adopter
authors .aiqt/toolchain-pins.toml (the schema is documented in .aiqt/toolchain-pins.toml.example, which is
deliberately not the live path). On Python 3.14 or newer, with NO configuration the gate prints NOT
APPLICABLE and exits 0, but only when an lstat of the live path reports it absent; a configuration that is
present but unusable never does, and with --require-config an absent one is cannot-evaluate (exit 2), so an
adopter who has opted in cannot lose the gate by deleting the file.

  check_toolchain_pins.py [--require-config] [--declarations-only] [--only ID[,ID...]] [--lane ID]
  check_toolchain_pins.py --self-test
  check_toolchain_pins.py [--self-test] --execution-report ABS_PATH
                          the self-test, also writing the executed check ids, finalized at interpreter exit
                          (tools/_selftest_exit_report.py), for tools/check_selftest_execution.py

Prerequisites:
  Python 3.14 or newer runs the gate. Below 3.14 its prelude writes a refusal and exits 2 before it reads
  any configuration, so on such an interpreter even an absent configuration is cannot-evaluate, never NOT
  APPLICABLE. It follows that the python-interpreter runtime leg cannot hold a pin below 3.14: the gate
  does not run on that interpreter, and run on 3.14 or newer it observes that newer interpreter (a
  finding). A pre-3.14 version in CI text can still be held by the declarations leg with
  --declarations-only.
  Placement: the project root is the parent of the directory holding this script (its own resolved path,
  symlinks followed, parents[1]), and there is no root or configuration-path override, so the script must
  sit one directory below the project root, as tools/check_toolchain_pins.py. Placed anywhere else it reads
  .aiqt/toolchain-pins.toml under another directory and, without --require-config, can report NOT
  APPLICABLE.
  With tools/check_python_floor.py: that gate, where an adopter runs it over its own workflows, requires
  every python-version: pin there to equal the floor's MAJOR.MINOR ('3.14') and reports a patch pin
  ('3.14.4') as a finding. Its one patch exception is matched by workflow path, job name and host pin
  file, not by repository: a patch pin passes there only in .github/workflows/quality.yml, inside a job
  that gate's HOST_JOBS names (opf-host-patch and the git-fixture-env-*-host-patch jobs), and only when it
  equals the python-host-patch that .aiqt/python-host.toml names. The
  python-interpreter adapter takes only MAJOR.MINOR.PATCH, and a declaration's captured version must equal
  it, so no python-version: line satisfies both gates. An adopter running both declares the
  python-interpreter toolchain with no [[toolchain.declaration]], so the runtime leg alone holds the
  interpreter running the gate to the patch, keeps each python-version: pin at the floor's MAJOR.MINOR, and
  updates the declared patch when the interpreter that pin installs moves to another patch (until then the
  runtime leg reports the difference as a finding).

Legs, each over the selected toolchains (every [[toolchain]], or only those --only names):
  declarations  each [[toolchain.declaration]] file is read as LF lines, one trailing CR removed from each; a
                line that still carries a CR, or carries U+0085, U+2028 or U+2029, is cannot-evaluate,
                because a YAML reader may break a line there that this reader does not. Every line holding
                the declaration's mention (compared without regard to case) must, once stripped, be one of
                its indirect lines or match its pattern whole; any other such line, and one holding a ${{
                expression that is not an indirect line, is cannot-evaluate. The number of pattern lines must
                equal count and the number of indirect lines indirect-count (a finding otherwise: a lane lost
                or gained a pin), and each captured version must equal the version of the declaration's lane
                (a finding naming file and line otherwise).
  wiring        each [[wiring]] line must occur, stripped, exactly count times in its file (a finding
                otherwise). The same line screen applies. That is all it establishes: the named text is
                present that many times, not that it is an executable, required or correctly ordered CI
                step (NOT CHECKED below).
  runtime       skipped with --declarations-only, and the report says so. For each selected toolchain the
                adapter reads the version and compares it, as a string, with the baseline version, or with
                the version of the lane --lane names (a lane every selected toolchain must declare;
                baseline is the entry's own version). python-interpreter reads the interpreter running this
                gate (sys.version_info, which must also be a final release) and reports sys.executable.
                command resolves argv[0] on PATH and runs that file with the rest of argv, with no shell,
                in a new session whose process group is killed at its timeout (a descendant that moved
                to another process group or session is not killed); its output is then collected for at
                most COLLECT_SECONDS (1) more, after which the pipes are closed, so a surviving descendant
                that holds them cannot keep the gate waiting, and the timeout is cannot-evaluate either
                way. The report says which it was: a command that did not finish, or one that exited
                within its timeout while a descendant still held its output open. A failed kill is named
                in the report, and a command still not reaped once the collection ends is named and left
                running, never waited on. The command must exit 0, and its standard output, decoded as
                UTF-8 and stripped, must match pattern whole. A command that is not on PATH is a finding.

The report names, for each toolchain, its lane, the lane's role, the expected and the observed version and
the executable, and which legs ran.

Exit convention (matches the repo's gates):
  0  every selected leg holds, or a printed NOT APPLICABLE (the configuration is confirmed absent and
     --require-config was not given)
  1  a finding: a runtime version that differs from the declared one (or a python-interpreter that is not a
     final release), a pinned command not on PATH, a declared version that differs from its lane's version,
     a declaration, indirect or wiring count that differs from the configured count
  2  cannot evaluate: the interpreter is older than Python 3.14 (whatever the configuration); the
     configuration is absent under --require-config, cannot be inspected (an unreadable parent directory,
     say), is a symlink (a dangling one included), is not a regular file, is not UTF-8 or does not parse
     (tomllib's ValueError, RecursionError and MemoryError are each mapped here), or breaks the schema (an
     unknown or missing key, a format-version other than the integer 1, a duplicate id, an unknown adapter,
     lane or role, a version outside its adapter's grammar, a regex that does not compile or whose named
     groups are not exactly version, one with no named group included, a bad count or timeout, a path that
     is absolute, climbs or holds an empty part); a declaration or wiring file that is missing, reached
     through a symlink, not regular, unreadable or not UTF-8, a line-break character in it, a mentioned line
     outside the pattern, an unlisted ${{ expression; a command that times out, exits non-zero, dies by a signal, cannot be
     started or prints output outside the pattern; --only or --lane naming an id the configuration does not
     hold; an unobservable child status (the entry guard below); or arguments it does not recognize.

NOT CHECKED: a toolchain the gate does not itself run (a suite that starts another interpreter, a virtual
environment, a container or a remote runner is not judged); equal versions are not equal builds (a
distribution's downstream patches, the linked system libraries, the architecture and the operating system
can all differ); a binary that misreports its version passes; the declarations and wiring legs read text,
not a CI grammar, so whether a CI job runs, is required, comes before the suites or is skipped (an if:, a
continue-on-error:) is not seen, nor a version built at run time or set through the environment, and the
adopter's mention bounds what is read; a YAML block scalar's text is read as lines like any other, so a pin
or wiring line moved into a run: | script, where it is shell text and no longer a setting or a step,
still counts and passes; a descendant of a timed-out command that left its process group is not killed
and may outlive the gate, and so may a timed-out command the kill could not stop (one running under
another user id, or in an uninterruptible wait), which is reported and left unreaped; a lane's role is a
label only; without --require-config, deleting the configuration brings NOT APPLICABLE back; an adopter
regex that backtracks badly runs as written; and whether the declared version is what a developer's host
runs rests on a human updating the configuration when the host changes.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_toolchain_pins.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import contextlib
import errno
import importlib.util
import io
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import tomllib
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKS_MANIFEST = ROOT / "tools" / "selftest_checks.toml"
SUITE_ID = "toolchain-pins-selftest"
CONFIG_REL = ".aiqt/toolchain-pins.toml"
EXAMPLE_REL = ".aiqt/toolchain-pins.toml.example"
USAGE = ("usage: check_toolchain_pins.py [--require-config] [--declarations-only] [--only ID[,ID...]] "
         "[--lane ID] | --self-test | [--self-test] --execution-report ABS_PATH")
ID_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
# The python-interpreter version grammar: MAJOR.MINOR.PATCH, each component at most four digits, the
# grammar tools/check_python_host_pin.py reads its own pin with.
PATCH_RE = re.compile(r"([1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})")
# The command version grammar: one word of letters, digits and . + _ -.
WORD_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}")
ADAPTERS = {"python-interpreter": PATCH_RE, "command": WORD_RE}
BASELINE = "baseline"
ROLES = ("required", "exploratory")
TOP_KEYS = ({"format-version", "toolchain"}, {"wiring"})
TOOLCHAIN_KEYS = {"python-interpreter": ({"id", "adapter", "version"}, {"declaration", "lane"}),
                  "command": ({"id", "adapter", "version", "argv", "pattern"},
                              {"declaration", "lane", "timeout-seconds"})}
LANE_KEYS = ({"id", "version", "role"}, set())
DECLARATION_KEYS = ({"path", "mention", "pattern", "count"}, {"lane", "indirect", "indirect-count"})
WIRING_KEYS = ({"path", "line", "count"}, set())
TIMEOUT_DEFAULT = 10
TIMEOUT_MAX = 120
# How long the output of a timed-out command is still collected once its process group is killed.
COLLECT_SECONDS = 1
# Characters a YAML reader may take as a line break where this reader splits on LF alone; each is refused
# once a line's one trailing CR is removed.
LINE_BREAKS = ("\r", "\x85", "\u2028", "\u2029")
EXPRESSION = "${{"
# How much of a command's output a report quotes.
OUTPUT_QUOTE = 120


class CannotEvaluate(Exception):
    """An input the gate cannot read or judge: reported as exit 2, never as a pass."""


def _table(value, where):
    if not isinstance(value, dict):
        raise CannotEvaluate("{}: must be a table".format(where))
    return value


def _keys(table, keys, where):
    """Refuse a key outside keys (required, optional) and a missing required key."""
    required, optional = keys
    unknown = set(table) - required - optional
    if unknown:
        raise CannotEvaluate("{}: unknown key(s) {}".format(where, ", ".join(sorted(unknown))))
    missing = required - set(table)
    if missing:
        raise CannotEvaluate("{}: missing key(s) {}".format(where, ", ".join(sorted(missing))))


def _integer(value, low, high, where):
    if type(value) is not int or not low <= value <= high:
        raise CannotEvaluate("{}: must be an integer from {} to {}".format(where, low, high))
    return value


def _string(value, where):
    if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value:
        raise CannotEvaluate("{}: must be a non-empty string with no surrounding whitespace".format(where))
    return value


def _identifier(value, where):
    if not isinstance(value, str) or ID_RE.fullmatch(value) is None:
        raise CannotEvaluate("{}: {!r} is not a kebab-case identifier".format(where, value))
    return value


def _version(value, adapter, where):
    if not isinstance(value, str) or ADAPTERS[adapter].fullmatch(value) is None:
        raise CannotEvaluate("{}: {!r} is not a version in the {} grammar".format(where, value, adapter))
    return value


def _pattern(value, where):
    """A compiled regex whose only named group is version."""
    if not isinstance(value, str) or not value:
        raise CannotEvaluate("{}: must be a non-empty string".format(where))
    try:
        compiled = re.compile(value)
    except (re.error, RecursionError, OverflowError) as exc:
        raise CannotEvaluate("{}: does not compile ({})".format(where, exc))
    if set(compiled.groupindex) != {"version"}:
        raise CannotEvaluate("{}: must have exactly one named group, version".format(where))
    return compiled


def _relative(value, where):
    """A repo-relative path: no absolute path, no backslash, no empty, . or .. part."""
    _string(value, where)
    if value.startswith("/") or "\\" in value or any(part in ("", ".", "..") for part in value.split("/")):
        raise CannotEvaluate("{}: {!r} is not a plain repo-relative path".format(where, value))
    return value


def _array(value, where):
    if not isinstance(value, list) or not value:
        raise CannotEvaluate("{}: must be a non-empty array".format(where))
    return value


def load_config(text):
    """The validated configuration: {"toolchains": [...], "wiring": [...]}. Every schema breach is
    CannotEvaluate."""
    try:
        data = tomllib.loads(text)
    # tomllib raises TOMLDecodeError (a ValueError) on a syntax error, a BARE ValueError on an integer
    # literal past CPython's int-string limit, a RecursionError on a deeply nested array or inline table and
    # a MemoryError on a document too large for the memory left: each is cannot-evaluate, never a traceback.
    except (ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("{} does not parse ({}): {}".format(CONFIG_REL, type(exc).__name__, exc))
    _keys(data, TOP_KEYS, CONFIG_REL)
    if type(data["format-version"]) is not int or data["format-version"] != 1:
        raise CannotEvaluate("{}: format-version must be the integer 1".format(CONFIG_REL))
    toolchains, seen = [], set()
    for number, entry in enumerate(_array(data["toolchain"], CONFIG_REL + " [[toolchain]]"), 1):
        where = "{} [[toolchain]] {}".format(CONFIG_REL, number)
        _table(entry, where)
        adapter = entry.get("adapter")
        if not isinstance(adapter, str) or adapter not in ADAPTERS:
            raise CannotEvaluate("{}: adapter {!r} is not one of {}".format(where, adapter, ", ".join(ADAPTERS)))
        _keys(entry, TOOLCHAIN_KEYS[adapter], where)
        name = _identifier(entry["id"], where + " id")
        if name in seen:
            raise CannotEvaluate("{}: duplicate toolchain id {!r}".format(where, name))
        seen.add(name)
        where = "{} toolchain {!r}".format(CONFIG_REL, name)
        toolchain = {"id": name, "adapter": adapter,
                     "lanes": {BASELINE: (_version(entry["version"], adapter, where + " version"), "required")}}
        if adapter == "command":
            argv = _array(entry["argv"], where + " argv")
            for element in argv:
                _string(element, where + " argv element")
            if "/" in argv[0]:
                raise CannotEvaluate("{}: argv[0] {!r} must be a bare command name, resolved on PATH".format(
                    where, argv[0]))
            toolchain["argv"] = list(argv)
            toolchain["pattern"] = _pattern(entry["pattern"], where + " pattern")
            toolchain["timeout"] = _integer(entry.get("timeout-seconds", TIMEOUT_DEFAULT), 1, TIMEOUT_MAX,
                                            where + " timeout-seconds")
        for lane in _array(entry["lane"], where + " [[toolchain.lane]]") if "lane" in entry else ():
            _keys(_table(lane, where + " lane"), LANE_KEYS, where + " lane")
            lane_id = _identifier(lane["id"], where + " lane id")
            if lane_id in toolchain["lanes"]:
                raise CannotEvaluate("{}: lane {!r} is declared twice (baseline is the entry's own "
                                     "version)".format(where, lane_id))
            if lane["role"] not in ROLES:
                raise CannotEvaluate("{}: lane {!r} role {!r} is not one of {}".format(
                    where, lane_id, lane["role"], ", ".join(ROLES)))
            toolchain["lanes"][lane_id] = (_version(lane["version"], adapter, where + " lane version"),
                                           lane["role"])
        declarations = []
        for index, item in enumerate(_array(entry["declaration"], where + " [[toolchain.declaration]]")
                                     if "declaration" in entry else (), 1):
            place = "{} declaration {}".format(where, index)
            _keys(_table(item, place), DECLARATION_KEYS, place)
            lane = item.get("lane", BASELINE)
            if not isinstance(lane, str) or lane not in toolchain["lanes"]:
                raise CannotEvaluate("{}: lane {!r} is not a lane of this toolchain".format(place, lane))
            if ("indirect" in item) != ("indirect-count" in item):
                raise CannotEvaluate("{}: indirect and indirect-count are given together or not at all".format(
                    place))
            indirect = [_string(line, place + " indirect line") for line in
                        _array(item["indirect"], place + " indirect")] if "indirect" in item else []
            if len(set(indirect)) != len(indirect):
                raise CannotEvaluate("{}: indirect holds a line twice".format(place))
            declarations.append({
                "path": _relative(item["path"], place + " path"),
                "mention": _string(item["mention"], place + " mention").casefold(),
                "pattern": _pattern(item["pattern"], place + " pattern"),
                "count": _integer(item["count"], 1, 10000, place + " count"),
                "lane": lane,
                "indirect": tuple(indirect),
                "indirect-count": _integer(item.get("indirect-count", 0), 0 if not indirect else 1, 10000,
                                           place + " indirect-count")})
        toolchain["declarations"] = declarations
        toolchains.append(toolchain)
    wiring = []
    for number, entry in enumerate(_array(data["wiring"], CONFIG_REL + " [[wiring]]")
                                   if "wiring" in data else (), 1):
        where = "{} [[wiring]] {}".format(CONFIG_REL, number)
        _keys(_table(entry, where), WIRING_KEYS, where)
        wiring.append({"path": _relative(entry["path"], where + " path"),
                       "line": _string(entry["line"], where + " line"),
                       "count": _integer(entry["count"], 1, 10000, where + " count")})
    return {"toolchains": toolchains, "wiring": wiring}


def _read_config(root):
    """The configuration text, or None when an lstat of the live path reports it absent. Any other failure
    to inspect it is cannot-evaluate, never absence."""
    config = root / CONFIG_REL
    try:
        config_stat = os.lstat(config)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CannotEvaluate("{} cannot be inspected ({}); only a confirmed absence is NOT APPLICABLE".format(
            CONFIG_REL, exc))
    if stat.S_ISLNK(config_stat.st_mode):
        raise CannotEvaluate("{} is a symlink; a symlinked configuration is not followed".format(CONFIG_REL))
    if not stat.S_ISREG(config_stat.st_mode):
        raise CannotEvaluate("{} is not a regular file; it is not opened".format(CONFIG_REL))
    try:
        return config.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CannotEvaluate("{} cannot be read as UTF-8 ({})".format(CONFIG_REL, exc))


def _read_lines(root, rel, what):
    """The LF lines of the repo-relative file rel, one trailing CR removed from each, every part of the path
    reached through no symlink; a line that still carries a line-break character is cannot-evaluate."""
    path = root
    for part in rel.split("/"):
        path = path / part
        try:
            part_stat = os.lstat(path)
        except FileNotFoundError:
            raise CannotEvaluate("{} {} is missing".format(what, rel))
        except OSError as exc:
            raise CannotEvaluate("{} {} cannot be inspected ({})".format(what, rel, exc))
        if stat.S_ISLNK(part_stat.st_mode):
            raise CannotEvaluate("{} {} is reached through a symlink, which is not followed".format(what, rel))
    if not stat.S_ISREG(part_stat.st_mode):
        raise CannotEvaluate("{} {} is not a regular file".format(what, rel))
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CannotEvaluate("{} {} cannot be read as UTF-8 ({})".format(what, rel, exc))
    lines = [line[:-1] if line.endswith("\r") else line for line in text.split("\n")]
    for number, line in enumerate(lines, 1):
        if any(mark in line for mark in LINE_BREAKS):
            raise CannotEvaluate("{}:{} carries a carriage return, U+0085, U+2028 or U+2029, where a YAML "
                                 "reader may break the line".format(rel, number))
    return lines


def declaration_findings(root, toolchains):
    findings = []
    for toolchain in toolchains:
        for declaration in toolchain["declarations"]:
            expected = toolchain["lanes"][declaration["lane"]][0]
            rel = declaration["path"]
            pinned = indirect = 0
            for number, line in enumerate(_read_lines(root, rel, "declaration file"), 1):
                if declaration["mention"] not in line.casefold():
                    continue
                text = line.strip()
                if text in declaration["indirect"]:
                    indirect += 1
                    continue
                if EXPRESSION in text:
                    raise CannotEvaluate("{}:{} holds a {} expression that is not an indirect line of the {} "
                                         "declaration".format(rel, number, EXPRESSION, toolchain["id"]))
                match = declaration["pattern"].fullmatch(text)
                if match is None:
                    raise CannotEvaluate("{}:{} names the {} pin ({!r}) but is outside its pattern: {!r}".format(
                        rel, number, toolchain["id"], declaration["mention"], text))
                pinned += 1
                if match.group("version") != expected:
                    findings.append("{}:{}: {} lane {} declares {}, expected {}".format(
                        rel, number, toolchain["id"], declaration["lane"], match.group("version"), expected))
            if pinned != declaration["count"]:
                findings.append("{}: {} lane {} has {} pinned line(s), expected {}".format(
                    rel, toolchain["id"], declaration["lane"], pinned, declaration["count"]))
            if indirect != declaration["indirect-count"]:
                findings.append("{}: {} lane {} has {} indirect line(s), expected {}".format(
                    rel, toolchain["id"], declaration["lane"], indirect, declaration["indirect-count"]))
    return findings


def wiring_findings(root, wiring):
    findings = []
    for entry in wiring:
        found = sum(1 for line in _read_lines(root, entry["path"], "wiring file") if line.strip() == entry["line"])
        if found != entry["count"]:
            findings.append("{}: holds {} line(s) {!r}, expected {}".format(
                entry["path"], found, entry["line"], entry["count"]))
    return findings


def _row(toolchain, lane, role, expected, observed, executable):
    return "{} lane {} ({}): expected {}, observed {}, executable {}".format(
        toolchain, lane, role, expected, observed, executable)


def _seconds(count):
    """count seconds in words: "1 second", "2 seconds"."""
    return "{} second{}".format(count, "" if count == 1 else "s")


def _stop_group(proc, binary, timeout):
    """The cannot-evaluate reason for a command whose output was still open at its timeout. The process group
    of the new session the command runs in (start_new_session makes the command that group's leader) is
    killed, then its output is collected for at most COLLECT_SECONDS. A descendant that moved to another
    process group or session survives the kill and can hold the pipes open, so the caller then closes them
    rather than waiting on them. The command itself is not waited on past that bound either: a failed kill
    is named, and a command still not reaped then is named and left running."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
        kill = ""
    except OSError as exc:
        kill = "; killing its process group failed ({}: {})".format(type(exc).__name__, exc)
    try:
        proc.communicate(timeout=COLLECT_SECONDS)
        held = ""
    except subprocess.TimeoutExpired:
        held = ("; its output was still held open {} after its process group was killed (a descendant outside "
                "that group), so the pipes were closed".format(_seconds(COLLECT_SECONDS)))
    returncode = proc.poll()
    if returncode is None:
        # Not held: with the command still running, the pipes may be its own.
        return "{} did not finish within {}{}; it was still running {} later, so it is left running and not " \
               "waited on".format(binary, _seconds(timeout), kill, _seconds(COLLECT_SECONDS))
    if returncode == -signal.SIGKILL and not kill:
        return "{} did not finish within {}{}".format(binary, _seconds(timeout), held)
    # The command ended before the kill reached it: the output still open at the timeout was a descendant's.
    return "{} {} within {}, but its output was still open when the timeout ended (a descendant held it){}{}".format(
        binary, "was killed by signal {}".format(-returncode) if returncode < 0 else "exited {}".format(returncode),
        _seconds(timeout), kill, held)


def _command_version(toolchain, expected, path):
    """(observed version, the resolved executable, the raw output), or (None, None, None) when argv[0] is
    not on path. A command that cannot be judged is CannotEvaluate."""
    binary = shutil.which(toolchain["argv"][0], path=path)
    if binary is None:
        return None, None, None
    binary = os.path.abspath(binary)
    try:
        proc = subprocess.Popen([binary] + toolchain["argv"][1:], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ, PATH=path),
                                start_new_session=True)
    except OSError as exc:
        raise CannotEvaluate("{} could not be run ({})".format(binary, exc))
    # No with block: its exit waits on the command with no bound, and a command the kill could not stop must
    # not keep the gate waiting (_stop_group).
    try:
        output, _errors = proc.communicate(timeout=toolchain["timeout"])
    except subprocess.TimeoutExpired:
        raise CannotEvaluate(_stop_group(proc, binary, toolchain["timeout"]))
    except OSError as exc:
        raise CannotEvaluate("{} could not be read ({})".format(binary, exc))
    finally:
        proc.stdout.close()
        proc.stderr.close()
    if proc.returncode != 0:
        raise CannotEvaluate("{} {}".format(binary, "was killed by signal {}".format(-proc.returncode)
                                            if proc.returncode < 0 else "exited {}".format(proc.returncode)))
    try:
        text = output.decode("utf-8")
    except UnicodeDecodeError:
        raise CannotEvaluate("{} printed output that is not UTF-8".format(binary))
    match = toolchain["pattern"].fullmatch(text.strip())
    if match is None:
        raise CannotEvaluate("{} printed {!r}, which is outside the {} pattern".format(
            binary, text[:OUTPUT_QUOTE], toolchain["id"]))
    return match.group("version"), binary, text


def runtime_findings(toolchains, lane, version_info, path, executable, rows):
    """Findings of the runtime leg; each toolchain's report row is appended to rows."""
    findings = []
    for toolchain in toolchains:
        lane_id = lane or BASELINE
        expected, role = toolchain["lanes"][lane_id]
        if toolchain["adapter"] == "python-interpreter":
            observed = "{}.{}.{}".format(*version_info[:3])
            row = _row(toolchain["id"], lane_id, role, expected, observed + " " + str(version_info[3]), executable)
            if observed != expected:
                findings.append("runtime: " + row)
            elif version_info[3] != "final":
                findings.append("runtime: " + row + " (not a final release)")
            rows.append(row)
            continue
        observed, binary, raw = _command_version(toolchain, expected, path)
        if binary is None:
            findings.append("runtime: {} lane {} ({}): expected {}, but {} is not on PATH".format(
                toolchain["id"], lane_id, role, expected, toolchain["argv"][0]))
            continue
        row = _row(toolchain["id"], lane_id, role, expected, observed, binary)
        if observed != expected:
            findings.append("runtime: " + row + "; output {!r}".format(raw[:OUTPUT_QUOTE]))
        rows.append(row)
    return findings


def evaluate(root, version_info, path, executable, only=None, lane=None, declarations_only=False,
             require_config=False):
    """(exit code, report lines) for the configuration under root, with version_info standing for the
    running interpreter, path for PATH and executable for sys.executable."""
    try:
        text = _read_config(root)
        if text is None:
            if require_config:
                return 2, ["CANNOT EVALUATE: {} is absent and --require-config was given".format(CONFIG_REL)]
            return 0, ["NOT APPLICABLE: no {}; the toolchain-pins gate is adopter-declared and inert without "
                       "one".format(CONFIG_REL)]
        config = load_config(text)
        toolchains = config["toolchains"]
        if only is not None:
            known = {toolchain["id"] for toolchain in toolchains}
            unknown = [name for name in only if name not in known]
            if unknown:
                raise CannotEvaluate("--only names {} not declared in {}".format(", ".join(unknown), CONFIG_REL))
            toolchains = [toolchain for toolchain in toolchains if toolchain["id"] in only]
        if lane is not None:
            lacking = [toolchain["id"] for toolchain in toolchains if lane not in toolchain["lanes"]]
            if lacking:
                raise CannotEvaluate("--lane {} is not a lane of {}".format(lane, ", ".join(lacking)))
        findings, rows = [], []
        findings.extend(declaration_findings(root, toolchains))
        findings.extend(wiring_findings(root, config["wiring"]))
        if not declarations_only:
            findings.extend(runtime_findings(toolchains, lane, version_info, path, executable, rows))
    except CannotEvaluate as exc:
        return 2, ["CANNOT EVALUATE: {}".format(exc)]
    legs = "declarations and wiring legs ran; runtime leg {}".format(
        "NOT RUN (--declarations-only)" if declarations_only else "ran")
    if findings:
        return 1, ["FAIL: {} toolchain-pins finding(s); {}".format(len(findings), legs)] + [
            "  " + finding for finding in findings] + ["  " + row for row in rows]
    return 0, ["PASS: {} toolchain(s) hold their pins; {}".format(len(toolchains), legs)] + [
        "  " + row for row in rows]


def parse_args(args):
    """(options for evaluate, None), or (None, why not)."""
    options = {"only": None, "lane": None, "declarations_only": False, "require_config": False}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in ("--require-config", "--declarations-only") and not options[arg[2:].replace("-", "_")]:
            options[arg[2:].replace("-", "_")] = True
        elif arg in ("--only", "--lane") and options[arg[2:]] is None and index + 1 < len(args):
            index += 1
            names = args[index].split(",") if arg == "--only" else [args[index]]
            if any(ID_RE.fullmatch(name) is None for name in names) or len(set(names)) != len(names):
                return None, "{} {!r} is not a list of distinct kebab-case ids".format(arg, args[index])
            options[arg[2:]] = names if arg == "--only" else names[0]
        else:
            return None, "unrecognized or repeated argument {!r}".format(arg)
        index += 1
    return options, None


def main(argv=None, root=None):
    if argv is None:
        # The process entry: the command line, ended through the execution-report finalizer when the
        # self-test armed it.
        _code = main(sys.argv[1:], root)
        _finalizer = sys.modules.get("_selftest_exit_report")
        (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
    args = argv
    if args and args[0] in ("--self-test", "--execution-report"):
        rest = args[1:] if args[0] == "--self-test" else list(args)
        if not rest:
            return self_test()
        if len(rest) == 2 and rest[0] == "--execution-report" and os.path.isabs(rest[1]):
            return self_test(rest[1])
        print("CANNOT EVALUATE: the self-test takes only --execution-report and an absolute path; {}".format(
            USAGE), file=sys.stderr)
        return 2
    options, why = parse_args(args)
    if options is None:
        print("CANNOT EVALUATE: {}; {}".format(why, USAGE), file=sys.stderr)
        return 2
    code, lines = evaluate(ROOT if root is None else root, tuple(sys.version_info[:4]), os.environ.get("PATH", ""),
                           sys.executable or "unknown interpreter", only=options["only"], lane=options["lane"],
                           declarations_only=options["declarations_only"],
                           require_config=options["require_config"])
    for line in lines:
        print(line, file=sys.stderr if code == 2 else sys.stdout)
    return code


# ---------------------------------------------------------------------------------------------- self-test
# Synthetic trees in a temporary directory. Each case runs against this gate and, where it names a flip,
# against a copy of this gate loaded through importlib with one anchor replaced (_mutant): the case must fail
# under its flip, so it is shown to be able to fail.

SELF_TEST_MARKER = "\n# " + "-" * 94 + " self-test\n"
FAILURES = []
EXECUTED = []
_EXECUTED_SET = set()
HOST = (3, 14, 4, "final")
EXE = "/usr/local/bin/python3"
PIN = "(?P<version>[0-9]+\\\\.[0-9]+\\\\.[0-9]+)"
PY_DECL = ('[[toolchain.declaration]]\npath = "ci/main.yml"\nmention = "python-version"\n'
           'pattern = "python-version: \'' + PIN + '\'"\ncount = 2\n'
           'indirect = ["python-version: ${{ matrix.python }}"]\nindirect-count = 1\n\n')
CANARY_DECL = ('[[toolchain.declaration]]\npath = "ci/canary.yml"\nmention = "python-version"\n'
               'pattern = "python-version: \'' + PIN + '\'"\ncount = 1\nlane = "canary"\n\n')
PY = ('[[toolchain]]\nid = "python"\nadapter = "python-interpreter"\nversion = "3.14.4"\n\n'
      '[[toolchain.lane]]\nid = "canary"\nversion = "3.14.9"\nrole = "exploratory"\n\n' + PY_DECL + CANARY_DECL)
WIRING_LINE = "run: python3 -I -B tools/check_toolchain_pins.py --require-config"
WIRING = '[[wiring]]\npath = "ci/main.yml"\nline = "' + WIRING_LINE + '"\ncount = 1\n\n'
CONFIG = "format-version = 1\n\n" + PY + WIRING
MAIN_YML = ("jobs:\n  test:\n    steps:\n      - uses: x/setup@v1\n        with:\n          python-version: '3.14.4'\n"
            "      - name: Toolchain pins\n        " + WIRING_LINE + "\n"
            "  lint:\n    steps:\n      - with:\n          python-version: '3.14.4'\n"
            "  matrix:\n    steps:\n      - with:\n          python-version: ${{ matrix.python }}\n")
CANARY_YML = "jobs:\n  canary:\n    steps:\n      - with:\n          python-version: '3.14.9'\n"
FILES = {"ci/main.yml": MAIN_YML, "ci/canary.yml": CANARY_YML}
VERSION_PATTERN = "v?(?P<version>[0-9]+\\.[0-9]+\\.[0-9]+)"
# The escaped-descendant case: the stub's helper starts a child in a process group of its own, which inherits
# the stub's stdout and stderr and lives until a stop file appears or HELD_LIFETIME seconds pass, then the
# stub hangs. The gate must return within HELD_TIMEOUT, COLLECT_SECONDS and HELD_MARGIN seconds.
HELD_TIMEOUT = 2
HELD_MARGIN = 3
HELD_LIFETIME = 10
HELD_CHILD = ("import pathlib, sys, time\n"
              "deadline = time.monotonic() + {}\n"
              "while not pathlib.Path(sys.argv[1]).exists() and time.monotonic() < deadline:\n"
              "    time.sleep(0.05)\n").format(HELD_LIFETIME)
HELD_HELPER = ("import pathlib, subprocess, sys\n"
               "subprocess.Popen([sys.executable, '-I', '-c', " + repr(HELD_CHILD) + ", sys.argv[2]], "
               "process_group=0)\n"
               "pathlib.Path(sys.argv[1]).write_text('started')\n")


def check(name, got, want):
    if name in _EXECUTED_SET:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    _EXECUTED_SET.add(name)
    EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


def _write(root, rel, data):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def _copy(base, name, source):
    """A module named name, loaded through importlib from source written under base."""
    _write(base, name + ".py", source)
    spec = importlib.util.spec_from_file_location(name, base / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _section(text, rel):
    """The lines of text between its "# -------- begin minimal REL" and "# -------- end minimal REL" lines,
    each with its leading "# " (or a bare "#") removed, or None when the markers are not there once each or
    a line between them is neither."""
    lines = text.split("\n")
    begin, end = "# -------- begin minimal " + rel, "# -------- end minimal " + rel
    if lines.count(begin) != 1 or lines.count(end) != 1 or lines.index(begin) > lines.index(end):
        return None
    body = lines[lines.index(begin) + 1:lines.index(end)]
    if any(line != "#" and not line.startswith("# ") for line in body):
        return None
    return "".join(line[2:] + "\n" for line in body)


def _mutant(base, name, edits):
    """A copy of this gate with each (anchor, replacement) applied once to the gate code above the self-test
    marker (the anchors themselves are written below it); an anchor that does not occur there exactly once
    is cannot-evaluate."""
    source, marker, rest = Path(__file__).resolve().read_text(encoding="utf-8").partition(SELF_TEST_MARKER)
    if not marker:
        raise CannotEvaluate("the self-test marker line is missing")
    for anchor, replacement in edits:
        if source.count(anchor) != 1:
            raise CannotEvaluate("flip anchor {!r} occurs {} times".format(anchor, source.count(anchor)))
        source = source.replace(anchor, replacement)
    return _copy(base, "toolchain_pins_" + name, source + marker + rest)


def _tree(base, name, config=CONFIG, files=None):
    root = base / name
    root.mkdir()
    if config is not None:
        _write(root, CONFIG_REL, config)
    for rel, text in (FILES if files is None else files).items():
        _write(root, rel, text)
    return root


def _stub(base, name, body, command="tool"):
    """A directory holding one executable sh script named command, for a PATH of its own."""
    directory = base / ("bin-" + name)
    directory.mkdir()
    script = directory / command
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(0o755)
    return str(directory)


def _command_config(timeout=None, argv='["tool", "--version"]', version="1.2.3"):
    return ('format-version = 1\n\n[[toolchain]]\nid = "tool"\nadapter = "command"\nversion = "' + version
            + '"\nargv = ' + argv + "\npattern = '" + VERSION_PATTERN + "'\n"
            + ("" if timeout is None else "timeout-seconds = {}\n".format(timeout)))


def _code(module, root, version=HOST, path="", **options):
    """The exit code module.evaluate gives, or "raised NAME" when an exception escapes it."""
    try:
        return module.evaluate(root, version, path, EXE, **options)[0]
    except Exception as exc:  # noqa: BLE001  an escaping exception is the outcome a flip is read by
        return "raised " + type(exc).__name__


def _main(module, argv, root):
    """(exit code, stdout plus stderr) of module.main(argv, root)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = module.main(argv, root)
    return code, out.getvalue()


def _injected(module, root, kind):
    """The outcome of module.evaluate over root with tomllib.loads replaced by one that raises kind."""
    real = module.tomllib

    def loads(_text):
        raise kind("injected at the parse")

    module.tomllib = types.SimpleNamespace(loads=loads, TOMLDecodeError=real.TOMLDecodeError)
    try:
        return _code(module, root)
    finally:
        module.tomllib = real


class _RefusingKill:
    """A stand-in for a gate module's os whose killpg is refused, as it is for a process running under another
    user id; every other name is the real module's."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def killpg(self, _pid, _signal):
        raise PermissionError(errno.EPERM, "injected: not permitted")


def _cases(base):
    this = sys.modules[__name__]

    # Absence: NOT APPLICABLE, unless --require-config; flipped, absence reads as exit 2, and the flag is
    # ignored.
    empty = _tree(base, "absent", config=None, files={})
    lines = evaluate(empty, HOST, "", EXE)
    check("absent/not-applicable", (lines[0], lines[1][0].startswith("NOT APPLICABLE")), (0, True))
    check("absent/flip-absence-exit-2", _code(_mutant(base, "absence_exit_2", (
        ('return 0, ["NOT APPLICABLE', 'return 2, ["NOT APPLICABLE'),)), empty), 2)
    check("absent/require-config", _code(this, empty, require_config=True), 2)
    check("absent/flip-require-config-ignored", _code(_mutant(base, "require_ignored", (
        ("            if require_config:\n", "            if False:\n"),)), empty, require_config=True), 0)

    # A dangling symlink configuration is cannot-evaluate; flipped to a following stat, as exists() reads,
    # it is NOT APPLICABLE.
    dangling = _tree(base, "dangling", config=None, files={})
    (dangling / ".aiqt").mkdir()
    os.symlink("missing.toml", dangling / CONFIG_REL)
    check("config/dangling-symlink", _code(this, dangling), 2)
    check("config/flip-lstat-follows", _code(_mutant(base, "stat_follows", (
        ("config_stat = os.lstat(config)", "config_stat = os.stat(config)"),)), dangling), 0)

    # A parent that cannot be searched is cannot-evaluate; flipped so every OSError reads as absence, it is
    # NOT APPLICABLE. Permission bits do not bind the superuser, so the case cannot run as root.
    if os.geteuid() == 0:
        print("NOTE: the unsearchable-parent case did not run (root euid ignores permission bits)")
    else:
        locked = _tree(base, "locked", files={})
        (locked / ".aiqt").chmod(0)
        try:
            check("config/unsearchable-parent", _code(this, locked), 2)
            check("config/flip-oserror-absent", _code(_mutant(base, "oserror_absent", (
                ("    except FileNotFoundError:\n        return None\n",
                 "    except OSError:\n        return None\n"),)), locked), 0)
        finally:
            (locked / ".aiqt").chmod(0o755)

    # Schema: each breach is cannot-evaluate; flipped without the key-set check, an unknown key passes.
    controls = _tree(base, "controls")
    check("schema/control-passes", _code(this, controls), 0)
    # Every check id is a literal (here a literal loop row), so tools/check_selftest_execution.py can resolve
    # each call site to its registered id before it launches the suite.
    for name, config in (
            ("schema/unknown-key", CONFIG.replace('adapter = "python-interpreter"\n',
                                                  'adapter = "python-interpreter"\nnote = "x"\n')),
            ("schema/format-version-true", CONFIG.replace("format-version = 1", "format-version = true")),
            ("schema/format-version-float", CONFIG.replace("format-version = 1", "format-version = 1.0")),
            ("schema/duplicate-id", CONFIG + PY.split("[[toolchain.declaration]]")[0]),
            ("schema/unknown-adapter", CONFIG.replace('"python-interpreter"', '"node"')),
            ("schema/pattern-without-version-group", CONFIG.replace("(?P<version>", "(", 1)),
            ("schema/pattern-does-not-compile", CONFIG.replace("(?P<version>", "((?P<version>", 1)),
            ("schema/unknown-lane", CONFIG.replace('lane = "canary"', 'lane = "nightly"')),
            ("schema/unknown-role", CONFIG.replace('"exploratory"', '"advisory"')),
            ("schema/version-outside-grammar", CONFIG.replace('version = "3.14.4"', 'version = "3.14"')),
            ("schema/count-zero", CONFIG.replace("count = 2", "count = 0")),
            ("schema/climbing-path", CONFIG.replace('"ci/canary.yml"', '"ci/../ci/canary.yml"')),
            ("schema/no-toolchain", "format-version = 1\n"),
            ("schema/timeout-zero", _command_config(timeout=0)),
            ("schema/timeout-too-long", _command_config(timeout=TIMEOUT_MAX + 1)),
            ("schema/timeout-not-integer", _command_config(timeout='"10"')),
            ("schema/argv-path", _command_config(argv='["/usr/bin/tool"]'))):
        check(name, _code(this, _tree(base, name.replace("/", "-"), config=config)), 2)
    # Each end of the timeout range is accepted (the command is then simply absent from the empty PATH, a
    # finding); one past each end is refused above.
    for name, seconds in (("schema/timeout-at-low-end", 1), ("schema/timeout-at-high-end", TIMEOUT_MAX)):
        check(name, _code(this, _tree(base, name.replace("/", "-"), config=_command_config(timeout=seconds))), 1)
    unknown = _tree(base, "flip-unknown-key", config=CONFIG.replace(
        'adapter = "python-interpreter"\n', 'adapter = "python-interpreter"\nnote = "x"\n'))
    check("schema/flip-key-set-check-removed", _code(_mutant(base, "keyset", (
        ("    if unknown:\n        raise CannotEvaluate(\"{}: unknown key(s)",
         "    if False:\n        raise CannotEvaluate(\"{}: unknown key(s)"),)), unknown), 0)

    # The parse mapping: each class injected at the parse is cannot-evaluate, and escapes a copy whose
    # handler no longer maps it.
    anchor = "except (ValueError, RecursionError, MemoryError) as exc:"
    for name, flip, kind, kept in (
            ("parse/injected-recursionerror", "parse/flip-without-recursionerror", RecursionError,
             "ValueError, MemoryError"),
            ("parse/injected-memoryerror", "parse/flip-without-memoryerror", MemoryError,
             "ValueError, RecursionError"),
            ("parse/injected-valueerror", "parse/flip-without-valueerror", ValueError,
             "RecursionError, MemoryError")):
        check(name, _injected(this, controls, kind), 2)
        check(flip, _injected(_mutant(base, "without_" + kind.__name__.lower(), (
            (anchor, "except (" + kept + ") as exc:"),)), controls, kind), "raised " + kind.__name__)
    check("parse/syntax-error", _code(this, _tree(base, "syntax", config=CONFIG + "[[toolchain\n")), 2)

    # python-interpreter: the same final triple passes; another micro and the same triple as a candidate are
    # findings. Flipped without the runtime-leg call, and without the final-release check, each passes.
    check("interpreter/same-final", _code(this, controls), 0)
    check("interpreter/other-micro", _code(this, controls, (3, 14, 5, "final")), 1)
    other = evaluate(controls, (3, 14, 5, "final"), "", EXE)[1]
    check("interpreter/other-micro-names-both", any("expected 3.14.4" in line and "observed 3.14.5" in line
                                                    for line in other), True)
    check("interpreter/candidate", _code(this, controls, (3, 14, 4, "candidate")), 1)
    no_runtime = _mutant(base, "no_runtime", (
        ("findings.extend(runtime_findings(toolchains, lane, version_info, path, executable, rows))",
         "findings.extend(())"),))
    check("interpreter/flip-runtime-leg-removed", _code(no_runtime, controls, (3, 14, 5, "final")), 0)
    check("interpreter/flip-final-check-removed", _code(_mutant(base, "no_final", (
        ('elif version_info[3] != "final":', "elif False:"),)), controls, (3, 14, 4, "candidate")), 0)

    # command: stub binaries on a PATH of their own.
    command = _tree(base, "command", config=_command_config(), files={})
    timed = _tree(base, "command-timeout", config=_command_config(timeout=1), files={})
    pinned = _stub(base, "pinned", "printf 'v1.2.3\\n'\n")
    check("command/pinned", _code(this, command, path=pinned), 0)
    check("command/other-version", _code(this, command, path=_stub(base, "other", "printf '1.2.4'\n")), 1)
    check("command/absent", _code(this, command, path=str(base / "bin-pinned" / "none")), 1)
    check("command/empty-path", _code(this, command, path=""), 1)
    words = _stub(base, "words", "printf 'x 1.2.3 y'\n")
    fails = _stub(base, "fails", "printf '1.2.3'\nexit 3\n")
    for name, path in (("command/extra-words", words),
                       ("command/two-lines", _stub(base, "two", "printf '1.2.3\\n1.2.3\\n'\n")),
                       ("command/non-zero-exit", fails),
                       ("command/killed-by-signal", _stub(base, "signal", "kill -KILL $$\n")),
                       ("command/not-utf-8", _stub(base, "bytes", "printf '\\377'\n"))):
        check(name, _code(this, command, path=path), 2)
    loop = _stub(base, "loop", "while :; do :; done\n")
    check("command/timeout", _code(this, timed, path=loop), 2)

    # A one-second timeout reads "1 second"; flipped to the plural alone, it reads "1 seconds".
    def timeout_words(module):
        text = "\n".join(module.evaluate(timed, HOST, loop, EXE)[1])
        return "did not finish within 1 second" in text and "1 seconds" not in text
    check("command/timeout-says-1-second", timeout_words(this), True)
    check("command/flip-seconds-always-plural", timeout_words(_mutant(base, "plural", (
        ('"" if count == 1 else "s"', '"s"'),))), False)
    check("command/flip-fullmatch-to-search", _code(_mutant(base, "search", (
        ('toolchain["pattern"].fullmatch(', 'toolchain["pattern"].search('),)), command, path=words), 0)
    check("command/flip-return-code-ignored", _code(_mutant(base, "rc_ignored", (
        ("    if proc.returncode != 0:\n", "    if False:\n"),)), command, path=fails), 0)
    check("command/flip-timeout-passes", _code(_mutant(base, "timeout_passes", (
        ('        raise CannotEvaluate(_stop_group(proc, binary, toolchain["timeout"]))\n',
         '        _stop_group(proc, binary, toolchain["timeout"])\n'
         '        return expected, binary, ""\n'),)), timed, path=loop), 0)

    # A descendant in a process group of its own survives the kill and holds stdout open: the gate still
    # returns within the timeout, the collection bound and HELD_MARGIN seconds, as cannot-evaluate naming
    # the held pipes. Flipped to collect with no bound, it waits until the descendant ends.
    held_tree = _tree(base, "command-held", config=_command_config(timeout=HELD_TIMEOUT), files={})
    _write(base, "held_helper.py", HELD_HELPER)

    def held(module, label, then="while :; do :; done\n"):
        """(exit code, whether the descendant started, whether the gate returned within its bound, the
        report) of a stub that starts the descendant and then runs then."""
        started, stop = base / ("held-" + label + ".started"), base / ("held-" + label + ".stop")
        path = _stub(base, "held-" + label, '"{}" -I "{}" "{}" "{}"\n{}'.format(
            sys.executable, base / "held_helper.py", started, stop, then))
        begun = time.monotonic()
        try:
            code, lines = module.evaluate(held_tree, HOST, path, EXE)
        finally:
            elapsed = time.monotonic() - begun
            _write(base, "held-" + label + ".stop", "")
        return (code, started.exists(), elapsed <= HELD_TIMEOUT + COLLECT_SECONDS + HELD_MARGIN,
                "\n".join(lines))
    code, started, bounded, text = held(this, "bounded")
    check("command/escaped-descendant-bounded", (code, started, bounded, "still held open" in text),
          (2, True, True, True))
    check("command/flip-collection-unbounded", held(_mutant(base, "unbounded", (
        ("proc.communicate(timeout=COLLECT_SECONDS)", "proc.communicate()"),)), "unbounded")[:3], (2, True, False))

    # A command that prints its version and exits 0 within its timeout while the descendant still holds its
    # output is cannot-evaluate too, and the report says it exited, not that it did not finish. Flipped to
    # read every timeout as unfinished, it does not.
    def exited(module, label):
        code, started, bounded, text = held(module, label, then="printf '1.2.3\\n'\n")
        return (code, started, bounded, "exited 0 within {}".format(_seconds(HELD_TIMEOUT)) in text,
                "did not finish" in text)
    check("command/exited-while-held-says-exited", exited(this, "exited"), (2, True, True, True, False))
    check("command/flip-exited-read-as-unfinished", exited(_mutant(base, "exited_unfinished", (
        ("    if returncode == -signal.SIGKILL and not kill:\n", "    if True:\n"),)), "exited-flip")[3:],
        (False, True))

    # A kill that fails (os.killpg refused, as for a command running under another user id) is named, and
    # the command it did not stop is left running, not waited on, so the gate still returns within its
    # bound. Flipped to drop the failure from the report, or to wait on the command, each reads otherwise.
    # The command is a child that ends when its stop file appears, or after HELD_LIFETIME seconds.
    _write(base, "held_child.py", HELD_CHILD)

    def unkilled(module, label):
        """(exit code, whether the report names the failed kill, whether it says the command was left running,
        whether the gate returned within its bound) under an os.killpg that refuses."""
        stop = base / ("unkilled-" + label + ".stop")
        path = _stub(base, "unkilled-" + label, 'exec "{}" -I "{}" "{}"\n'.format(
            sys.executable, base / "held_child.py", stop))
        real = module.os
        module.os = _RefusingKill(real)
        begun = time.monotonic()
        try:
            code, lines = module.evaluate(timed, HOST, path, EXE)
        finally:
            elapsed = time.monotonic() - begun
            module.os = real
            _write(base, stop.name, "")
        text = "\n".join(lines)
        return (code, "killing its process group failed (PermissionError" in text, "left running" in text,
                elapsed <= 1 + COLLECT_SECONDS + HELD_MARGIN)
    check("command/kill-failure-named-and-bounded", unkilled(this, "named"), (2, True, True, True))
    check("command/flip-kill-failure-unnamed", unkilled(_mutant(base, "kill_unnamed", (
        ('        kill = "; killing its process group failed ({}: {})".format(type(exc).__name__, exc)\n',
         '        kill = ""\n'),)), "unnamed")[1], False)
    check("command/flip-exit-wait-unbounded", unkilled(_mutant(base, "exit_wait", (
        ("    returncode = proc.poll()\n", "    returncode = proc.wait()\n"),)), "waited")[3], False)

    # argv is passed with no shell: a shell metacharacter in an argument stays literal. Flipped to a shell
    # over the joined command, the marker appears (a redirection, which needs no command on the stub PATH).
    marker = base / "marker"
    literal = _tree(base, "literal", files={}, config=_command_config(
        argv="['tool', '--version;: >" + str(marker) + "']"))
    check("argv/no-shell", (_code(this, literal, path=pinned), marker.exists()), (0, False))
    shell = _mutant(base, "shell", (
        ('[binary] + toolchain["argv"][1:], stdin=',
         '" ".join([binary] + toolchain["argv"][1:]), shell=bool(toolchain), stdin='),))
    _code(shell, literal, path=pinned)
    check("argv/flip-shell-runs-metacharacters", marker.exists(), True)

    # Declarations: a literal equal to its lane's version passes; another is a finding naming file:line.
    # Flipped without the declarations-leg call, it passes.
    drift = _tree(base, "drift", files=dict(FILES, **{"ci/main.yml": MAIN_YML.replace("'3.14.4'", "'3.14.5'", 1)}))
    check("declarations/other-version", _code(this, drift), 1)
    check("declarations/names-file-line", any(line.strip().startswith("ci/main.yml:6:")
                                              for line in evaluate(drift, HOST, "", EXE)[1]), True)
    check("declarations/flip-leg-removed", _code(_mutant(base, "no_declarations", (
        ("findings.extend(declaration_findings(root, toolchains))", "findings.extend(())"),)),
        drift), 0)

    # Counts are exact in both directions: a lost and a gained pin line are each a finding. Flipped so only
    # one direction is read, each passes.
    lost = _tree(base, "lost", files=dict(FILES, **{"ci/main.yml": MAIN_YML.replace(
        "  lint:\n    steps:\n      - with:\n          python-version: '3.14.4'\n", "")}))
    gained = _tree(base, "gained", files=dict(FILES, **{"ci/canary.yml": CANARY_YML + CANARY_YML[5:]}))
    check("counts/lost-line", _code(this, lost), 1)
    check("counts/gained-line", _code(this, gained), 1)
    check("counts/flip-only-too-many", _code(_mutant(base, "count_gt", (
        ('if pinned != declaration["count"]:', 'if pinned > declaration["count"]:'),)), lost), 0)
    check("counts/flip-only-too-few", _code(_mutant(base, "count_lt", (
        ('if pinned != declaration["count"]:', 'if pinned < declaration["count"]:'),)), gained), 0)
    check("counts/indirect-lost", _code(this, _tree(base, "indirect-lost", files=dict(FILES, **{
        "ci/main.yml": MAIN_YML.replace("${{ matrix.python }}", "'3.14.4'")}))), 1)

    # A mentioned line outside the pattern is cannot-evaluate: the key in another case, a trailing comment
    # and an unlisted expression (here under a pattern loose enough to capture it). Flipped to skip such
    # lines, or without the expression screen, each reads otherwise.
    loose = CONFIG.replace("pattern = \"python-version: '" + PIN + "'\"\ncount = 2",
                           "pattern = \"python-version: (?P<version>.+)\"\ncount = 2")
    skip = _mutant(base, "skip_outside", (
        ("                if match is None:\n                    raise CannotEvaluate(",
         "                if match is None:\n                    continue\n"
         "                    raise CannotEvaluate("),))
    unscreened = _mutant(base, "no_expression_screen", (("                if EXPRESSION in text:\n",
                                                         "                if False:\n"),))
    for name, flip_name, flip, config, text in (
            ("mentions/key-other-case", "mentions/flip-key-other-case", skip, CONFIG,
             MAIN_YML.replace("python-version: '3.14.4'", "Python-Version: '3.14.4'", 1)),
            ("mentions/trailing-comment", "mentions/flip-trailing-comment", skip, CONFIG,
             MAIN_YML.replace("'3.14.4'", "'3.14.4'  # pin", 1)),
            ("mentions/unlisted-expression", "mentions/flip-unlisted-expression", unscreened, loose,
             MAIN_YML.replace("${{ matrix.python }}", "${{ matrix.other }}"))):
        root = _tree(base, name.replace("mentions/", "outside-"), config=config,
                     files=dict(FILES, **{"ci/main.yml": text}))
        check(name, _code(this, root), 2)
        check(flip_name, _code(flip, root) != 2, True)

    # A line-break character in a line that does not mention the pin is cannot-evaluate; flipped without
    # the screen, it passes.
    unbroken = _mutant(base, "no_line_screen", (("        if any(mark in line for mark in LINE_BREAKS):\n",
                                                 "        if False:\n"),))
    for name, flip_name, mark in (("lines/internal-cr", "lines/flip-internal-cr", "\r"),
                                  ("lines/line-separator", "lines/flip-line-separator", "\u2028")):
        root = _tree(base, name.replace("lines/", "break-"), files=dict(FILES, **{"ci/main.yml": MAIN_YML.replace(
            "jobs:\n", "jobs:" + mark + "x: y\n")}))
        check(name, _code(this, root), 2)
        check(flip_name, _code(unbroken, root), 0)
    check("lines/crlf-file", _code(this, _tree(base, "crlf", files={
        rel: text.replace("\n", "\r\n") for rel, text in FILES.items()})), 0)

    # A missing declaration file is cannot-evaluate; flipped to read as no lines, the count finding stands.
    missing = _tree(base, "missing-file", files={"ci/main.yml": MAIN_YML})
    check("files/missing", _code(this, missing), 2)
    check("files/flip-missing-read-as-empty", _code(_mutant(base, "missing_empty", (
        ('raise CannotEvaluate("{} {} is missing".format(what, rel))', "return []"),)), missing), 1)
    linked = _tree(base, "linked-file", files={"ci/main.yml": MAIN_YML})
    os.symlink("main.yml", linked / "ci" / "canary.yml")
    check("files/symlink", _code(this, linked), 2)

    # Wiring: a missing line is a finding; flipped without the wiring-leg call, it passes.
    unwired = _tree(base, "unwired", files=dict(FILES, **{"ci/main.yml": MAIN_YML.replace(
        "        " + WIRING_LINE + "\n", "")}))
    check("wiring/line-missing", _code(this, unwired), 1)
    check("wiring/flip-leg-removed", _code(_mutant(base, "no_wiring", (
        ('findings.extend(wiring_findings(root, config["wiring"]))', "findings.extend(())"),)), unwired), 0)

    # --declarations-only through main(): with an interpreter that does not match, it passes and says the
    # runtime leg did not run, while the full run is a finding. Flipped so main() drops the flag, or returns
    # 0 without evaluating, each reads otherwise.
    ancient = _tree(base, "ancient", config=CONFIG.replace('version = "3.14.4"', 'version = "1.0.0"'), files={
        "ci/main.yml": MAIN_YML.replace("'3.14.4'", "'1.0.0'"), "ci/canary.yml": CANARY_YML})
    code, text = _main(this, ["--declarations-only"], ancient)
    check("declarations-only/passes-and-says-so", (code, "runtime leg NOT RUN" in text), (0, True))
    check("declarations-only/full-run-finds", _main(this, [], ancient)[0], 1)
    code, text = _main(_mutant(base, "flag_dropped", (('declarations_only=options["declarations_only"]',
                                                       "declarations_only=False"),)), ["--declarations-only"],
                       ancient)
    check("declarations-only/flip-flag-dropped", code, 1)
    unevaluated = _mutant(base, "no_evaluate", (("    code, lines = evaluate(ROOT if root",
                                                 "    return 0\n    code, lines = evaluate(ROOT if root"),))
    code, text = _main(unevaluated, ["--declarations-only"], ancient)
    check("declarations-only/flip-main-skips-evaluation", (code, "runtime leg NOT RUN" in text), (0, False))
    check("declarations-only/flip-main-skips-evaluation-full", _main(unevaluated, [], ancient)[0], 0)

    # --lane compares against the lane's version; an unknown lane is cannot-evaluate. Flipped to ignore the
    # lane, the canary interpreter is a finding.
    canary = (3, 14, 9, "final")
    check("lane/canary-passes", _code(this, controls, canary, lane="canary"), 0)
    check("lane/canary-on-baseline-finds", _code(this, controls, canary), 1)
    check("lane/unknown", _code(this, controls, canary, lane="nightly"), 2)
    check("lane/flip-lane-ignored", _code(_mutant(base, "lane_ignored", (
        ("        lane_id = lane or BASELINE\n", "        lane_id = BASELINE\n"),)),
        controls, canary, lane="canary"), 1)
    check("lane/only-unknown", _code(this, controls, only=["node"]), 2)

    # A canary-lane declaration is held to the lane's version, not the baseline. Flipped to drop the lane
    # field, the canary literal is read against the baseline.
    check("lane/declaration-at-baseline-finds", _code(this, _tree(base, "canary-baseline", files=dict(
        FILES, **{"ci/canary.yml": CANARY_YML.replace("3.14.9", "3.14.4")}))), 1)
    check("lane/flip-declaration-lane-dropped", _code(_mutant(base, "decl_lane_dropped", (
        ('            lane = item.get("lane", BASELINE)\n', "            lane = BASELINE\n"),)), controls), 1)

    # The reports name id, lane, expected, observed and the executable, PASS and FAIL alike. Flipped without
    # the executable, they do not.
    def names_all(module, root, version, path, executable):
        lines = module.evaluate(root, version, path, EXE)[1]
        return any(all(part in line for part in ("python lane baseline", "expected 3.14.4", "observed",
                                                 "executable " + executable)) or
                   all(part in line for part in ("tool lane baseline", "expected 1.2.3", "observed",
                                                 "executable " + executable)) for line in lines)
    tool = os.path.join(pinned, "tool")
    other_tool = os.path.join(base, "bin-other", "tool")
    no_executable = _mutant(base, "no_executable", (("        toolchain, lane, role, expected, observed, executable)",
                                                     '        toolchain, lane, role, expected, observed, "")'),))
    for name, flip_name, root, version, path, executable in (
            ("report/pass", "report/flip-pass", controls, HOST, "", EXE),
            ("report/fail", "report/flip-fail", controls, (3, 14, 5, "final"), "", EXE),
            ("report/command-pass", "report/flip-command-pass", command, HOST, pinned, tool),
            ("report/command-fail", "report/flip-command-fail", command, HOST, os.path.dirname(other_tool),
             other_tool)):
        check(name, names_all(this, root, version, path, executable), True)
        check(flip_name, names_all(no_executable, root, version, path, executable), False)

    # The shipped example validates under the schema, and a tree holding only the example is NOT APPLICABLE.
    # Flipped so the example is the live path, it is not; and an unknown key in the example is refused.
    example = (Path(__file__).resolve().parents[1] / EXAMPLE_REL).read_bytes().decode("utf-8")
    try:
        validated = sorted(toolchain["id"] for toolchain in load_config(example)["toolchains"])
    except CannotEvaluate as exc:
        validated = "refused: {}".format(exc)
    check("example/validates", validated, ["gitleaks", "python"])
    only_example = _tree(base, "only-example", config=None, files={EXAMPLE_REL: example})
    check("example/not-applicable", _code(this, only_example), 0)
    check("example/flip-example-is-live", _code(_mutant(base, "example_live", (
        ('CONFIG_REL = ".aiqt/toolchain-pins.toml"\n', 'CONFIG_REL = ".aiqt/toolchain-pins.toml.example"\n'),)),
        only_example) != 0, True)
    try:
        load_config(example + "\nnote = 1\n")
        refused = False
    except CannotEvaluate:
        refused = True
    check("example/unknown-key-refused", refused, True)

    # The example's marked minimal configuration, its "# " prefixes removed, validates as one python
    # toolchain with one declaration of count 1, and passes over its own marked workflow; that workflow at
    # another patch is a finding.
    minimal, minimal_ci = (_section(example, rel) for rel in (CONFIG_REL, ".github/workflows/ci.yml"))
    try:
        validated = [(toolchain["id"], [d["count"] for d in toolchain["declarations"]])
                     for toolchain in load_config(minimal or "")["toolchains"]]
    except CannotEvaluate as exc:
        validated = "refused: {}".format(exc)
    check("example/minimal-validates", validated, [("python", [1])])
    for name, text, want in (("example/minimal-passes", minimal_ci or "", 0),
                             ("example/minimal-other-patch-finds",
                              (minimal_ci or "").replace("'3.14.4'", "'3.14.5'"), 1)):
        check(name, _code(this, _tree(base, name.replace("/", "-"), config=minimal or "",
                                      files={".github/workflows/ci.yml": text})), want)

    # An unknown argument is cannot-evaluate; flipped to fall through to a default scan, it is NOT APPLICABLE.
    check("arguments/unknown", _main(this, ["--bogus"], empty)[0], 2)
    check("arguments/repeated", _main(this, ["--lane", "a", "--lane", "b"], empty)[0], 2)
    check("arguments/missing-value", _main(this, ["--only"], empty)[0], 2)
    check("arguments/flip-unknown-default-scan", _main(_mutant(base, "unknown_default", (
        ('            return None, "unrecognized or repeated argument {!r}".format(arg)\n', "            pass\n"),)),
        ["--bogus"], empty)[0], 0)


def _expected_check_ids():
    """The expected-check-ids of this suite's row in CHECKS_MANIFEST, or None after printing why not."""
    try:
        manifest = tomllib.loads(CHECKS_MANIFEST.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError, MemoryError) as exc:
        print("SELF-TEST HARNESS ERROR: cannot read {}: {}".format(CHECKS_MANIFEST, exc), file=sys.stderr)
        return None
    for row in manifest.get("suite", []):
        if isinstance(row, dict) and row.get("id") == SUITE_ID:
            ids = row.get("expected-check-ids")
            if isinstance(ids, list) and ids and all(isinstance(item, str) and item for item in ids) \
                    and len(set(ids)) == len(ids):
                return set(ids)
            break
    print("SELF-TEST HARNESS ERROR: missing or malformed suite {!r} in {}".format(SUITE_ID, CHECKS_MANIFEST),
          file=sys.stderr)
    return None


def self_test(report_path=None):
    if report_path is not None:
        # The execution report is finalized at interpreter exit, after this run's cleanup
        # (tools/_selftest_exit_report.py); nothing writes it in band. Imported only here: the flipped copies
        # of this gate load from a temporary directory that does not carry it.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import _selftest_exit_report
        _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
    try:
        with tempfile.TemporaryDirectory(prefix="toolchain-pins-selftest-") as raw:
            _cases(Path(raw))
    except (CannotEvaluate, OSError) as exc:
        print("SELF-TEST HARNESS ERROR: {}".format(exc), file=sys.stderr)
        return 2
    # The in-run self-guard: the executed set must equal this suite's registered set, on a direct run too.
    expected = _expected_check_ids()
    if expected is None:
        return 2
    for check_id in sorted(expected - _EXECUTED_SET):
        FAILURES.append("execution-set/missing: {}".format(check_id))
    for check_id in sorted(_EXECUTED_SET - expected):
        FAILURES.append("execution-set/extra: {}".format(check_id))
    if FAILURES:
        print("SELF-TEST FAIL:")
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: {} unique checks executed (each case red under its flip); execution set reconciled "
          "against tools/selftest_checks.toml".format(len(EXECUTED)))
    return 0


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This gate reads the exit status of each
# pinned command, so under an ignored SIGCHLD it would read a failing command as a pass; it refuses at its
# entry line instead, before main() runs.
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
    sys.exit(_child_status_refusal_exit("check_toolchain_pins.py") or main())
