"""Self-test harness: does a self-test entry point hand the caller's environment back exactly?

A self-test entry point wraps its isolated main in _git_fixture_env.fixture_git_lifecycle(), which
saves os.environ on entry, applies the in-place git fixture scrub itself, and restores the saved
mapping on exit, even on an exception. A scrub_git_environment() call placed BEFORE the lifecycle
makes it save the already scrubbed values instead, so an in-process call of the entry point leaves
the caller's HOME, XDG_CONFIG_HOME and GIT_CONFIG_* pins changed and drops the caller's own GIT_*
variables for good.

entry_restore_failures(tool, entry, inner) runs the entry point in a child interpreter that first
sets a caller environment (HOME at a fresh scratch directory; XDG_CONFIG_HOME, GIT_CONFIG_GLOBAL,
GIT_CONFIG_SYSTEM, GIT_CONFIG_NOSYSTEM and every other inherited GIT_* variable unset; a caller
variable GIT_AIQT_CALLER_PROBE=kept), loads the tool from its path, replaces the isolated main the
entry point delegates to with a stand-in, calls the entry point in-process, and requires os.environ
to equal its snapshot from before the call exactly. The stand-in replaces the inner work because the
full suite is too long to run twice, and running it again from inside its own self-test would
recurse; what is under test is the wrapper. The stand-in also records the environment it sees and
requires the complete fixture scrub to be in effect there and the lifecycle's git wrapper in effect.
The scrub values are checked against literals written here, never against git_fixture_env() itself,
so a wrong value in the shared scrub is not both applied and expected: HOME and XDG_CONFIG_HOME at a
fixture scratch home the probe creates and installs as the scrub's scratch home, GIT_CONFIG_GLOBAL
and GIT_CONFIG_SYSTEM equal to os.devnull, GIT_CONFIG_NOSYSTEM equal to "1", the maintenance pins
GIT_CONFIG_COUNT=3 with gc.auto=0, gc.autoDetach=false and maintenance.auto=false, and no other
GIT_* variable. The git wrapper must be in effect as fixture_git_lifecycle() sets it: PATH is a
fresh directory in front of the caller's PATH and git resolves to the wrapper there. The wrapper is
then run, not read: the caller's git is a stand-in the probe writes in its own scratch directory at
the front of the caller's PATH, so the wrapper's exec line points at it, and it prints the arguments
and the GIT_CONFIG* environment it received. The stand-in main runs the wrapper with fixed arguments
under its own environment stripped of every GIT_* variable (as a production helper that scrubs GIT_*
leaves it) and with GIT_CONFIG_NOSYSTEM=0 and GIT_CONFIG_SYSTEM at a decoy path, and requires the
stand-in git to be reached exactly once with the -c pins gc.auto=0, gc.autoDetach=false and
maintenance.auto=false, in that order, then the fixed arguments unchanged, and with
GIT_CONFIG_NOSYSTEM=1 and GIT_CONFIG_SYSTEM=os.devnull: these too are literals written here, not
_git_fixture_env._NO_AUTO_MAINTENANCE. So a wrapper that exits before its exec line, or overrides a
pin after setting it, fails, and so does an entry point that drops the scrub, or saves, scrubs and
restores by hand without the lifecycle. The call is then repeated around a stand-in that raises: the
caller's environment must again come back exactly, the stand-in's own exception must come out of the
entry point, and the scrub and the working wrapper are checked inside that call as inside the first,
so a lifecycle that isolates only its first use fails.

Residual: the probe installs its own scratch home (_git_fixture_env._SCRATCH_HOME), so the branch of
_scratch_home() that creates that directory and registers its removal is not exercised here.

The child is this module's _child_main, reached through a fixed -c bootstrap that puts this
directory on the module path; the module has no __main__ block of its own.

Imported only by a self-test; nothing here runs on a production path."""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROBE_KEY = "GIT_AIQT_CALLER_PROBE"
PROBE_VALUE = "kept"
_RESULT_TAG = "ENTRY-RESTORE "
_CHILD_BOOTSTRAP = ("import sys; sys.path.insert(0, sys.argv[1]); import _entry_restore_selftest; "
                    "sys.exit(_entry_restore_selftest._child_main(sys.argv[2:]))")
# The scrub values the lifecycle must apply, written out independently of _git_fixture_env so a
# wrong value there fails the probe instead of being expected by it. HOME and XDG_CONFIG_HOME are
# added per run: they must equal the fixture scratch home the probe creates.
_EXPECTED_PINS = (
    ("GIT_CONFIG_GLOBAL", os.devnull),
    ("GIT_CONFIG_SYSTEM", os.devnull),
    ("GIT_CONFIG_NOSYSTEM", "1"),
    ("GIT_CONFIG_COUNT", "3"),
    ("GIT_CONFIG_KEY_0", "gc.auto"),
    ("GIT_CONFIG_VALUE_0", "0"),
    ("GIT_CONFIG_KEY_1", "gc.autoDetach"),
    ("GIT_CONFIG_VALUE_1", "false"),
    ("GIT_CONFIG_KEY_2", "maintenance.auto"),
    ("GIT_CONFIG_VALUE_2", "false"),
)
# What the git wrapper must hand the caller's git, also written out independently: the
# auto-maintenance -c pins, in this order, ahead of the arguments the wrapper was given, and the
# system-config pins in its environment whatever the environment it was started with.
_EXPECTED_ARGV_PINS = ("gc.auto=0", "gc.autoDetach=false", "maintenance.auto=false")
_EXPECTED_WRAPPER_ENV = (("GIT_CONFIG_NOSYSTEM", "1"), ("GIT_CONFIG_SYSTEM", os.devnull))
# The arguments the probe runs the wrapper with; they must reach the caller's git unchanged.
_WRAPPER_ARGS = ("status", "--probe-argument", "two words")
_STAND_IN_TAG = "GIT-STAND-IN "


def entry_restore_failures(tool, entry, inner, timeout=300):
    """Run the restore probe for tool's entry point (a function name) whose isolated main is inner
    (the module-level function name the entry point calls inside the lifecycle). Returns a list of
    failure strings, empty when the caller's environment came back exactly, both after a return and
    after the stand-in's exception came through, and inside each of the two calls the scrub was in
    effect and the git wrapper ran the caller's git with the pins. A child that cannot be launched,
    times out, or reports no result is a failure, never a silent pass."""
    label = "{} {}()".format(os.path.basename(str(tool)), entry)
    argv = [sys.executable, "-I", "-B", "-c", _CHILD_BOOTSTRAP,
            os.path.dirname(os.path.abspath(__file__)), str(tool), entry, inner]
    try:
        child = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return ["{}: the restore probe child could not run: {}".format(label, exc)]
    records = [line for line in child.stdout.splitlines() if line.startswith(_RESULT_TAG)]
    if len(records) != 1:
        return ["{}: the restore probe child reported {} result records (rc={}): {}".format(
            label, len(records), child.returncode, (child.stdout + child.stderr).strip()[-600:])]
    try:
        problems = json.loads(records[0][len(_RESULT_TAG):])
    except ValueError:
        problems = None
    if not isinstance(problems, list) or not all(isinstance(p, str) for p in problems):
        return ["{}: the restore probe child reported a malformed result: {}".format(
            label, records[0][-600:])]
    if child.returncode != (1 if problems else 0):
        problems = problems + ["the restore probe child exited rc={}: {}".format(
            child.returncode, child.stderr.strip()[-600:])]
    return ["{}: {}".format(label, problem) for problem in problems]


def _diff(before, after):
    """The keys whose presence or value differs between two environment mappings, described."""
    out = []
    for key in sorted(set(before) | set(after)):
        if key not in after:
            out.append("{} removed (was {!r})".format(key, before[key]))
        elif key not in before:
            out.append("{} added ({!r})".format(key, after[key]))
        elif before[key] != after[key]:
            out.append("{} changed from {!r} to {!r}".format(key, before[key], after[key]))
    return out


class _StandInRaised(Exception):
    """What the raising stand-in throws; the entry point must let this very exception through."""


def _write_stand_in_git(directory):
    """Write the caller's stand-in git into directory: an executable that prints one record, tagged
    _STAND_IN_TAG, of the arguments and the GIT_CONFIG* environment it received, and exits 0."""
    code = ("import json, os, sys; print({!r} + json.dumps({{'argv': sys.argv[1:], 'env': "
            "{{k: v for k, v in os.environ.items() if k.startswith('GIT_CONFIG')}}}}))").format(
                _STAND_IN_TAG)
    path = Path(directory) / "git"
    path.write_text("#!/bin/sh\nexec {} -I -B -c {} \"$@\"\n".format(
        shlex.quote(sys.executable), shlex.quote(code)), encoding="utf-8")
    path.chmod(0o700)


def _wrapper_view():
    """Inside the lifecycle: the PATH in effect, what git resolves to on it, and the git file in
    PATH's first directory, where the lifecycle's wrapper must be."""
    path = os.environ.get("PATH", "")
    candidate = os.path.join(path.split(os.pathsep, 1)[0], "git")
    return {"path": path, "candidate": candidate, "which": shutil.which("git")}


def _run_wrapper(candidate, inside, decoy):
    """Inside the lifecycle: run the git wrapper candidate with _WRAPPER_ARGS under the environment
    inside stripped of every GIT_* variable, with GIT_CONFIG_NOSYSTEM=0 and GIT_CONFIG_SYSTEM=decoy
    added, so only the wrapper itself can supply the system-config pins. Returns the stand-in git's
    record (a dict), or a string saying why the stand-in git was not reached exactly once."""
    env = {key: value for key, value in inside.items() if not key.startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "0"
    env["GIT_CONFIG_SYSTEM"] = decoy
    try:
        run = subprocess.run([candidate] + list(_WRAPPER_ARGS), env=env, capture_output=True,
                             text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return "the git wrapper {!r} could not run: {}".format(candidate, exc)
    records = [line[len(_STAND_IN_TAG):] for line in run.stdout.splitlines()
               if line.startswith(_STAND_IN_TAG)]
    if run.returncode != 0 or len(records) != 1:
        return ("the git wrapper did not run the caller's git exactly once (rc={}, {} records): "
                "{}".format(run.returncode, len(records),
                            (run.stdout + run.stderr).strip()[-300:] or "no output"))
    try:
        record = json.loads(records[0])
    except ValueError:
        record = None
    if not (isinstance(record, dict) and isinstance(record.get("env"), dict)):
        return "the caller's stand-in git printed a malformed record: {}".format(records[0][-300:])
    return record


def _wrapper_problems(view, caller, record):
    """How the git wrapper seen inside the lifecycle (view, from _wrapper_view, and record, from
    _run_wrapper) differs from what fixture_git_lifecycle() sets up for the caller environment
    caller: a fresh directory in front of the caller's PATH, holding the git that git resolves to,
    which runs the caller's git with _EXPECTED_ARGV_PINS as -c options ahead of its own arguments
    and with _EXPECTED_WRAPPER_ENV in that git's environment."""
    caller_path = caller.get("PATH", os.defpath)
    directory, sep, rest = view["path"].partition(os.pathsep)
    problems = []
    if not sep or rest != caller_path or not directory or directory in caller_path.split(
            os.pathsep):
        problems.append("inside the lifecycle, PATH is {!r}, not a fresh git wrapper directory in "
                        "front of the caller's PATH {!r}".format(view["path"], caller_path))
    elif view["which"] != view["candidate"]:
        problems.append("inside the lifecycle, git resolves to {!r}, not the wrapper {!r}".format(
            view["which"], view["candidate"]))
    if not isinstance(record, dict):
        return problems + [record]
    want = []
    for pin in _EXPECTED_ARGV_PINS:
        want += ["-c", pin]
    want += list(_WRAPPER_ARGS)
    if record.get("argv") != want:
        problems.append("the git wrapper ran the caller's git with arguments {!r}, expected "
                        "{!r}".format(record.get("argv"), want))
    for key, value in _EXPECTED_WRAPPER_ENV:
        if record["env"].get(key) != value:
            problems.append("the git wrapper ran the caller's git with {} {!r}, expected "
                            "{!r}".format(key, record["env"].get(key), value))
    return problems


def _seen_problems(seen, caller, home, fixture_home):
    """What the stand-in main saw during one call of the entry point (seen: one entry per call of
    the isolated main) against what the lifecycle must set up for the caller environment caller:
    one call, the literal scrub pins and nothing else GIT_*, and the git wrapper in effect and
    working."""
    if len(seen) != 1:
        return ["the entry point called its isolated main {} times, expected 1".format(len(seen))]
    inside, view, record = seen[0]
    problems = []
    expected = dict((("HOME", fixture_home), ("XDG_CONFIG_HOME", fixture_home)) + _EXPECTED_PINS)
    for key, value in expected.items():
        if inside.get(key) != value:
            problems.append("inside the lifecycle, {} is {!r}, expected {!r}".format(
                key, inside.get(key), value))
    if inside.get("HOME") == home:
        problems.append("inside the lifecycle, HOME is still the caller's home")
    extra = sorted(k for k in inside if k.startswith("GIT_") and k not in expected)
    if extra:
        problems.append("inside the lifecycle, caller GIT_* variables survive the scrub: "
                        "{}".format(", ".join(extra)))
    return problems + _wrapper_problems(view, caller, record)


def _child(tool, entry, inner):
    """Child side: set the caller environment, call the entry point in-process with inner replaced
    by a stand-in, first one returning 0 and then one raising, and return the list of problems
    found."""
    import importlib.util
    problems = []
    home = tempfile.mkdtemp(prefix="aiqt-entry-restore-home-")
    fixture_home = tempfile.mkdtemp(prefix="aiqt-entry-restore-fixture-home-")
    stand_in_bin = tempfile.mkdtemp(prefix="aiqt-entry-restore-git-")
    try:
        for key in [k for k in os.environ if k.startswith("GIT_")]:
            del os.environ[key]
        os.environ.pop("XDG_CONFIG_HOME", None)
        os.environ["HOME"] = home
        os.environ[PROBE_KEY] = PROBE_VALUE
        sys.path.append(os.path.dirname(os.path.abspath(tool)))
        spec = importlib.util.spec_from_file_location("_entry_restore_target", tool)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        import _git_fixture_env
        if not callable(getattr(module, inner, None)):
            return ["{} has no function {}".format(tool, inner)]
        # The scrub's scratch home is the directory this probe created, so HOME and XDG_CONFIG_HOME
        # inside the lifecycle can be compared with a path known here rather than with the scrub's
        # own output.
        _git_fixture_env._SCRATCH_HOME = fixture_home
        # The caller's git is the stand-in, first on the caller's PATH, so the wrapper the
        # lifecycle writes execs it and what reaches the caller's git can be read back.
        _write_stand_in_git(stand_in_bin)
        os.environ["PATH"] = stand_in_bin + os.pathsep + os.environ.get("PATH", os.defpath)
        decoy = os.path.join(stand_in_bin, "decoy-system-gitconfig")
        seen = []
        raising = []

        def stand_in(*_args, **_kwargs):
            inside = dict(os.environ)
            view = _wrapper_view()
            seen.append((inside, view, _run_wrapper(view["candidate"], inside, decoy)))
            if raising:
                raise raising[0]
            return 0

        setattr(module, inner, stand_in)
        caller = dict(os.environ)
        rc = getattr(module, entry)()
        after = dict(os.environ)
        if rc != 0:
            problems.append("the entry point returned {!r} around a stand-in returning 0".format(rc))
        problems.extend("after the call, " + d for d in _diff(caller, after))
        problems.extend(_seen_problems(seen, caller, home, fixture_home))
        # The same call around a stand-in that raises: the environment comes back exactly on the
        # way out, the stand-in's own exception, not a return value or another exception, comes
        # out of the entry point, and the scrub and the wrapper are checked inside this call too.
        raising.append(_StandInRaised("raised by the restore probe's stand-in"))
        del seen[:]
        caller = dict(os.environ)
        try:
            rc = getattr(module, entry)()
        except BaseException as exc:
            if exc is not raising[0]:
                problems.append("around a raising stand-in, the entry point raised {!r} instead "
                                "of the stand-in's exception".format(exc))
        else:
            problems.append("around a raising stand-in, the entry point returned {!r} instead "
                            "of letting the exception through".format(rc))
        problems.extend("after a raising call, " + d for d in _diff(caller, dict(os.environ)))
        problems.extend("around a raising stand-in, " + problem
                        for problem in _seen_problems(seen, caller, home, fixture_home))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(fixture_home, ignore_errors=True)
        shutil.rmtree(stand_in_bin, ignore_errors=True)
    return problems


def _child_main(argv):
    """The child's entry, reached only through _CHILD_BOOTSTRAP: print the one result record and
    return the exit status (0 clean, 1 problems found, 2 a usage error)."""
    if len(argv) != 3:
        print("usage: _child_main([TOOL, ENTRY, INNER])", file=sys.stderr)
        return 2
    found = _child(argv[0], argv[1], argv[2])
    print(_RESULT_TAG + json.dumps(found))
    return 1 if found else 0
