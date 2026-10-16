"""Self-test harness: does a self-test entry point hand the caller's environment back exactly?

A self-test entry point wraps its isolated main in _git_fixture_env.fixture_git_lifecycle(), which
saves os.environ on entry, applies the in-place git fixture scrub itself, and restores the saved
mapping on exit, even on an exception. A scrub_git_environment() call placed BEFORE the lifecycle
makes it save the already scrubbed values instead, so an in-process call of the entry point leaves
the caller's HOME, XDG_CONFIG_HOME and GIT_CONFIG_* pins changed and drops the caller's own GIT_*
variables for good.

entry_restore_failures(tool, entry, inner, args, helpers) runs the entry point in a child
interpreter that first sets a caller environment (HOME at a fresh scratch directory;
XDG_CONFIG_HOME, GIT_CONFIG_GLOBAL, GIT_CONFIG_SYSTEM, GIT_CONFIG_NOSYSTEM and every other inherited
GIT_* variable unset; a caller variable GIT_AIQT_CALLER_PROBE=kept), loads the tool from its path,
replaces the isolated main the entry point delegates to with a stand-in, calls the entry point
in-process with the positional arguments args, and requires os.environ to equal its snapshot from
before the call exactly and every global binding of the tool's module to be the same object it was
before the call (so a cached git executable rebound to the lifecycle's wrapper must be put back).
helpers names helper calls the entry point makes, each as a module global of the tool and an
attribute of it (_selftest_exit_report and arm, for instance); each is replaced by a recorder, and
the entry point must call each exactly once, with the caller's environment unchanged at that call.
The probe checks the environment at the call, not where the call sits: a call made after the
lifecycle has exited also sees the caller's environment. What keeps the real entry points' helper
calls before the lifecycle is the scope allowlist's rule that nothing follows the lifecycle's with
(_lifecycle_scoped_first in tools/selftest_git_fixture_env.py). What a replaced helper itself does
is outside the probe. The stand-in replaces the inner work because the full suite is too long to
run twice, and running it again from inside its own self-test would recurse; what is under test is
the wrapper. The stand-in records the arguments it receives, which must be exactly args (same
values and types, in order) with no keywords, so an entry point that passes its isolated main a
constant in place of a parameter it was given fails whenever the probe passes a different value.
It also records the environment it sees and requires the complete fixture scrub to be in effect
there and the lifecycle's git wrapper in effect.
The scrub values are checked against literals written here, never against git_fixture_env() itself,
so a wrong value in the shared scrub is not both applied and expected: HOME and XDG_CONFIG_HOME at a
fixture scratch home the probe creates and installs as the scrub's scratch home, GIT_CONFIG_GLOBAL
and GIT_CONFIG_SYSTEM equal to os.devnull, GIT_CONFIG_NOSYSTEM equal to "1", the maintenance pins
GIT_CONFIG_COUNT=3 with gc.auto=0, gc.autoDetach=false and maintenance.auto=false, and no other
GIT_* variable. The git wrapper must be in effect as fixture_git_lifecycle() sets it: PATH is a
fresh directory in front of the caller's PATH and git resolves to the wrapper there. The wrapper is
then run, not read: the caller's git is a stand-in the probe writes in its own scratch directory at
the front of the caller's PATH, so the wrapper's exec line points at it. Before each run the probe
writes a fresh random nonce into a channel directory it creates, and removes the records file
there; the stand-in reads the nonce and appends one record, carrying it, the arguments and the
stand-in's whole GIT_* environment, to that records file, and prints nothing. The probe requires
exactly one record, and that record must carry this run's nonce, so a wrapper that only prints a
record, or plants one that does not carry this run's nonce, fails. The nonce shows only that the
record carries a value written for this run; it does not show that the stand-in wrote the record
(see the residual below). The stand-in main runs the wrapper with
fixed arguments under its own environment stripped of every GIT_* variable (as a production helper
that scrubs GIT_* leaves it) and with GIT_CONFIG_NOSYSTEM=0 and GIT_CONFIG_SYSTEM at a decoy path,
and requires the -c pins gc.auto=0, gc.autoDetach=false and maintenance.auto=false, in that order,
then the fixed arguments unchanged, and a GIT_* environment of exactly GIT_CONFIG_NOSYSTEM=1 and
GIT_CONFIG_SYSTEM=os.devnull: these too are literals written here, not
_git_fixture_env._NO_AUTO_MAINTENANCE. So a wrapper that exits before its exec line, runs the
caller's git twice, overrides a pin after setting it, or exports another GIT_* variable fails, and
so does an entry point that drops the scrub, or saves, scrubs and restores by hand without the
lifecycle. The call is then repeated around a stand-in that raises: the caller's environment and
the module's global bindings must again come back exactly, the stand-in's own exception must come
out of the entry point, and the helpers, the scrub and the working wrapper are checked inside that
call as inside the first, so a lifecycle that isolates only its first use fails.

wrapper_mutant_failures() checks that wrapper check itself: in a child with the same caller
environment and stand-in git, it enters the lifecycle, requires the lifecycle's own wrapper to pass,
and requires each wrapper _WRAPPER_MUTANTS names, made from the lifecycle's wrapper text by one
edit, to be refused for the reason the table gives and nothing else. A mutant given a second,
unrelated defect (_SECOND_DEFECT) must not count as refused for its own reason.

Residual: the nonce is not hidden from the wrapper under test. The probe, the stand-in and the
wrapper run as the same user, that user can read the channel directory, and the stand-in's script
names the channel in clear, so a wrapper written to read the nonce and append a matching record
itself passes the wrapper check without running the caller's git. A wrapper that runs the caller's
git correctly and also launches some other executable passes too, because only runs of the
stand-in leave records. Both are outside the probe, which refuses the wrapper defects
_WRAPPER_MUTANTS names, not a wrapper written to defeat it.

Residual: the probe installs its own scratch home (_git_fixture_env._SCRATCH_HOME), so the branch of
_scratch_home() that creates that directory and registers its removal is not exercised here.

The child is this module's _child_main, reached through a fixed -c bootstrap that puts this
directory on the module path; the module has no __main__ block of its own.

Imported only by a self-test; nothing here runs on a production path."""

import json
import os
import secrets
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
_MUTANT_MODE = "--wrapper-mutants"
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
# auto-maintenance -c pins, in this order, ahead of the arguments the wrapper was given, and, as
# its whole GIT_* environment, the system-config pins, whatever the environment it was started with.
_EXPECTED_ARGV_PINS = ("gc.auto=0", "gc.autoDetach=false", "maintenance.auto=false")
_EXPECTED_WRAPPER_ENV = (("GIT_CONFIG_NOSYSTEM", "1"), ("GIT_CONFIG_SYSTEM", os.devnull))
# The arguments the probe runs the wrapper with; they must reach the caller's git unchanged.
_WRAPPER_ARGS = ("status", "--probe-argument", "two words")
# The stand-in git's channel directory holds these two files: the nonce the probe writes before
# each wrapper run, and the records file the stand-in appends its one record line to.
_NONCE_FILE = "nonce"
_RECORDS_FILE = "records"
_NO_RECORD = "the git wrapper did not run the caller's git exactly once (rc=0, 0 records)"
# The wrappers the wrapper check must refuse, each made from the lifecycle's wrapper by one edit
# (_wrapper_mutants), with the start of the problem it must be refused for: a mutant counts as
# refused only when the wrapper check reports exactly one problem and that problem starts with it.
_WRAPPER_MUTANTS = (
    # Prints a well-formed stand-in record on stdout and exits 0 without running the caller's git.
    ("stdout-record", _NO_RECORD),
    # Appends a well-formed record that does not carry this run's nonce to the records file, and
    # exits 0 without running the caller's git.
    ("stale-nonce", "the caller's stand-in git record does not carry this run's nonce"),
    # Exports GIT_DIR to the caller's git besides the two system-config pins.
    ("git-dir", "the git wrapper ran the caller's git with GIT_* environment"),
    # Exits 0 before its exec line.
    ("exit-before-exec", _NO_RECORD),
    # Runs the caller's git once without exec, then execs it again.
    ("runs-twice", "the git wrapper did not run the caller's git exactly once (rc=0, 2 records)"),
    # Sets gc.auto=1 after the pins, ahead of the arguments it was given.
    ("pin-override", "the git wrapper ran the caller's git with arguments"),
)
# The git-dir mutant given a second, unrelated defect: it also makes pin-override's edit. The
# wrapper check reports both problems, and the mutant check must not count this wrapper as the
# git-dir mutant refused for its own reason.
_SECOND_DEFECT = "git-dir-and-pin-override"


def entry_restore_failures(tool, entry, inner, args=(), helpers=(), timeout=300):
    """Run the restore probe for tool's entry point (a function name) whose isolated main is inner
    (the module-level function name the entry point calls inside the lifecycle), calling the entry
    point with the positional arguments args (JSON values) and with each (module global, attribute)
    pair in helpers replaced by a recorder the entry point must call exactly once, with the
    caller's environment unchanged at that call. Only the environment at the call is checked, not
    whether the call comes before the lifecycle: a call after the lifecycle has exited would pass
    too, and the scope allowlist's rule that nothing follows the lifecycle's with is what keeps the
    real entry points' calls before it. Returns a list of failure strings, empty when the caller's
    environment and the tool's module globals came back exactly, both after a return and after the
    stand-in's exception came through, and inside each of the two calls the isolated main received
    exactly args and no keywords, the scrub was in effect and the git wrapper ran the caller's git
    with the pins. A child that cannot be launched, times out, or reports no result is a failure,
    never a silent pass."""
    label = "{} {}()".format(os.path.basename(str(tool)), entry)
    spec = json.dumps({"args": list(args), "helpers": [list(helper) for helper in helpers]})
    argv = [sys.executable, "-I", "-B", "-c", _CHILD_BOOTSTRAP,
            os.path.dirname(os.path.abspath(__file__)), str(tool), entry, inner, spec]
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


def wrapper_mutant_failures(timeout=120):
    """Check the probe's wrapper check against the lifecycle's own wrapper and the mutants of it
    _WRAPPER_MUTANTS names, in a child. Returns a dict from each name _mutant_result_names gives to
    a list of failure strings: empty for "lifecycle" when the wrapper check found nothing wrong
    with the lifecycle's wrapper, empty for a mutant when the wrapper check refused it with exactly
    one problem, starting with the table's reason, and empty for _SECOND_DEFECT when the wrapper
    check reported both of its defects and the mutant check did not count it as the git-dir mutant
    refused for its own reason. A child that cannot be launched, times out, or reports no
    well-formed result gives every name that failure, never a silent pass."""
    names = _mutant_result_names()
    argv = [sys.executable, "-I", "-B", "-c", _CHILD_BOOTSTRAP,
            os.path.dirname(os.path.abspath(__file__)), _MUTANT_MODE]
    try:
        child = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return {name: ["the wrapper mutant child could not run: {}".format(exc)] for name in names}
    records = [line for line in child.stdout.splitlines() if line.startswith(_RESULT_TAG)]
    found = None
    if len(records) == 1:
        try:
            found = json.loads(records[0][len(_RESULT_TAG):])
        except ValueError:
            found = None
    if not (isinstance(found, dict) and sorted(found) == sorted(names) and all(
            isinstance(value, list) and all(isinstance(p, str) for p in value)
            for value in found.values())):
        failure = "the wrapper mutant child reported no well-formed result (rc={}): {}".format(
            child.returncode, (child.stdout + child.stderr).strip()[-600:])
        return {name: [failure] for name in names}
    if child.returncode != (1 if any(found.values()) else 0):
        failure = "the wrapper mutant child exited rc={}: {}".format(
            child.returncode, child.stderr.strip()[-600:])
        found = {name: found[name] + [failure] for name in names}
    return found


def _mutant_result_names():
    """The names wrapper_mutant_failures reports on: "lifecycle", each mutant _WRAPPER_MUTANTS
    names, and _SECOND_DEFECT."""
    return ["lifecycle"] + [name for name, _reason in _WRAPPER_MUTANTS] + [_SECOND_DEFECT]


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


def _rebound(before, after):
    """The global names of a module whose presence or bound object differs between two snapshots of
    its namespace, described."""
    out = []
    for name in sorted(set(before) | set(after)):
        if name not in after:
            out.append("module global {} removed".format(name))
        elif name not in before:
            out.append("module global {} added ({!r})".format(name, after[name]))
        elif after[name] is not before[name]:
            out.append("module global {} rebound from {!r} to {!r}".format(
                name, before[name], after[name]))
    return out


class _StandInRaised(Exception):
    """What the raising stand-in throws; the entry point must let this very exception through."""


def _recorder(calls):
    """A stand-in for a helper call: records the environment at each call in calls, returns None."""
    def record(*_args, **_kwargs):
        calls.append(dict(os.environ))
    return record


def _set_caller_environment(home):
    """Make os.environ the probe's caller environment: no GIT_* variable and no XDG_CONFIG_HOME,
    HOME at home, and the caller's own variable PROBE_KEY=PROBE_VALUE."""
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        del os.environ[key]
    os.environ.pop("XDG_CONFIG_HOME", None)
    os.environ["HOME"] = home
    os.environ[PROBE_KEY] = PROBE_VALUE


def _install_stand_in_git(directory, channel):
    """Write the caller's stand-in git into directory and put directory first on the caller's PATH,
    so the wrapper the lifecycle writes execs it. The stand-in reads the nonce the probe left in
    channel and appends one JSON record line, carrying that nonce, the arguments and the whole
    GIT_* environment it received, to channel's records file, then exits 0. It prints nothing, so
    a wrapper's own output cannot stand in for it."""
    code = ("import json, os, sys\n"
            "from pathlib import Path\n"
            "channel = Path({channel!r})\n"
            "record = {{'nonce': (channel / {nonce!r}).read_text(encoding='utf-8'),\n"
            "           'argv': sys.argv[1:],\n"
            "           'env': {{k: v for k, v in os.environ.items() if k.startswith('GIT_')}}}}\n"
            "with open(channel / {records!r}, 'a', encoding='utf-8') as handle:\n"
            "    handle.write(json.dumps(record) + '\\n')\n").format(
                channel=str(channel), nonce=_NONCE_FILE, records=_RECORDS_FILE)
    path = Path(directory) / "git"
    path.write_text("#!/bin/sh\nexec {} -I -B -c {} \"$@\"\n".format(
        shlex.quote(sys.executable), shlex.quote(code)), encoding="utf-8")
    path.chmod(0o700)
    os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", os.defpath)


def _wrapper_view():
    """Inside the lifecycle: the PATH in effect, what git resolves to on it, and the git file in
    PATH's first directory, where the lifecycle's wrapper must be."""
    path = os.environ.get("PATH", "")
    candidate = os.path.join(path.split(os.pathsep, 1)[0], "git")
    return {"path": path, "candidate": candidate, "which": shutil.which("git")}


def _launch_wrapper(candidate, env):
    """Run the git wrapper candidate with _WRAPPER_ARGS under env. The module's one launch of a git
    wrapper, kept in its own function so the maintenance-pin scan's justification covers it
    alone."""
    return subprocess.run([candidate] + list(_WRAPPER_ARGS), env=env, capture_output=True,
                          text=True, timeout=60)


def _run_wrapper(candidate, inside, decoy, channel):
    """Inside the lifecycle: run the git wrapper candidate with _WRAPPER_ARGS under the environment
    inside stripped of every GIT_* variable, with GIT_CONFIG_NOSYSTEM=0 and GIT_CONFIG_SYSTEM=decoy
    added, so only the wrapper itself can supply the system-config pins. A fresh nonce is written
    to channel and its records file removed first, so a record that does not carry this call's
    nonce, or one printed instead of written, is refused. The nonce does not show that the stand-in
    git wrote the record: the wrapper under test can read the channel too (the module's residual).
    Returns the one record carrying this call's nonce (a dict), or a string saying why no such
    single record came back."""
    env = {key: value for key, value in inside.items() if not key.startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "0"
    env["GIT_CONFIG_SYSTEM"] = decoy
    nonce = secrets.token_hex(16)
    records = Path(channel) / _RECORDS_FILE
    try:
        records.unlink(missing_ok=True)
        (Path(channel) / _NONCE_FILE).write_text(nonce, encoding="utf-8")
        run = _launch_wrapper(candidate, env)
        lines = records.read_text(encoding="utf-8").splitlines() if records.exists() else []
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return "the git wrapper {!r} could not run: {}".format(candidate, exc)
    if run.returncode != 0 or len(lines) != 1:
        return ("the git wrapper did not run the caller's git exactly once (rc={}, {} records): "
                "{}".format(run.returncode, len(lines),
                            (run.stdout + run.stderr).strip()[-300:] or "no output"))
    try:
        record = json.loads(lines[0])
    except ValueError:
        record = None
    if not (isinstance(record, dict) and isinstance(record.get("env"), dict)):
        return "the caller's stand-in git wrote a malformed record: {}".format(lines[0][-300:])
    if record.get("nonce") != nonce:
        return ("the caller's stand-in git record does not carry this run's nonce (it carries "
                "{!r})".format(record.get("nonce")))
    return record


def _expected_argv():
    """The arguments the git wrapper must hand the caller's git: _EXPECTED_ARGV_PINS as -c options,
    then _WRAPPER_ARGS."""
    want = []
    for pin in _EXPECTED_ARGV_PINS:
        want += ["-c", pin]
    return want + list(_WRAPPER_ARGS)


def _wrapper_problems(view, caller, record):
    """How the git wrapper seen inside the lifecycle (view, from _wrapper_view, and record, from
    _run_wrapper) differs from what fixture_git_lifecycle() sets up for the caller environment
    caller: a fresh directory in front of the caller's PATH, holding the git that git resolves to,
    which runs the caller's git with _EXPECTED_ARGV_PINS as -c options ahead of its own arguments
    and with exactly _EXPECTED_WRAPPER_ENV as that git's GIT_* environment."""
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
    want = _expected_argv()
    if record.get("argv") != want:
        problems.append("the git wrapper ran the caller's git with arguments {!r}, expected "
                        "{!r}".format(record.get("argv"), want))
    want_env = dict(_EXPECTED_WRAPPER_ENV)
    if record["env"] != want_env:
        problems.append("the git wrapper ran the caller's git with GIT_* environment {!r}, "
                        "expected exactly {!r}".format(record["env"], want_env))
    return problems


def _seen_problems(seen, caller, home, fixture_home, args):
    """What the stand-in main saw during one call of the entry point (seen: one entry per call of
    the isolated main) against what the lifecycle must set up for the caller environment caller:
    one call, made with exactly the probe's arguments args (same values and types, in order) and
    no keywords, the literal scrub pins and nothing else GIT_*, and the git wrapper in effect and
    working."""
    if len(seen) != 1:
        return ["the entry point called its isolated main {} times, expected 1".format(len(seen))]
    inside, view, record, (received, keywords) = seen[0]
    problems = []
    typed = [(type(value), value) for value in received]
    if typed != [(type(value), value) for value in args] or keywords:
        problems.append("the entry point called its isolated main with arguments {!r} and keywords "
                        "{!r}, expected exactly the probe's arguments {!r} and no keywords".format(
                            received, keywords, list(args)))
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


def _call_problems(caller, bound, module, recorded, seen, home, fixture_home, args):
    """What one call of the entry point with args left wrong: os.environ after it against caller,
    the module's namespace against bound, each helper's recorded calls (exactly one, made with the
    caller's environment unchanged), and what the stand-in main saw inside (_seen_problems)."""
    problems = ["after the call, " + d for d in _diff(caller, dict(os.environ))]
    problems.extend("after the call, " + d for d in _rebound(bound, dict(vars(module))))
    for label, calls in sorted(recorded.items()):
        if len(calls) != 1:
            problems.append("the entry point called {}() {} times, expected 1".format(
                label, len(calls)))
        else:
            problems.extend("when {}() was called, ".format(label) + d
                            for d in _diff(caller, calls[0]))
    return problems + _seen_problems(seen, caller, home, fixture_home, args)


def _child(tool, entry, inner, args=(), helpers=()):
    """Child side: set the caller environment, call the entry point in-process with args, with inner
    replaced by a stand-in that records the arguments it receives and each helper by a recorder,
    first around a stand-in returning 0 and then around one raising, and return the list of
    problems found."""
    import importlib.util
    problems = []
    home = tempfile.mkdtemp(prefix="aiqt-entry-restore-home-")
    fixture_home = tempfile.mkdtemp(prefix="aiqt-entry-restore-fixture-home-")
    stand_in_bin = tempfile.mkdtemp(prefix="aiqt-entry-restore-git-")
    channel = tempfile.mkdtemp(prefix="aiqt-entry-restore-channel-")
    try:
        _set_caller_environment(home)
        sys.path.append(os.path.dirname(os.path.abspath(tool)))
        spec = importlib.util.spec_from_file_location("_entry_restore_target", tool)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        import _git_fixture_env
        if not callable(getattr(module, inner, None)):
            return ["{} has no function {}".format(tool, inner)]
        recorded = {}
        for owner, attribute in helpers:
            label = "{}.{}".format(owner, attribute)
            if not callable(getattr(getattr(module, owner, None), attribute, None)):
                return ["{} has no helper {}".format(tool, label)]
            setattr(getattr(module, owner), attribute, _recorder(recorded.setdefault(label, [])))
        # The scrub's scratch home is the directory this probe created, so HOME and XDG_CONFIG_HOME
        # inside the lifecycle can be compared with a path known here rather than with the scrub's
        # own output.
        _git_fixture_env._SCRATCH_HOME = fixture_home
        _install_stand_in_git(stand_in_bin, channel)
        decoy = os.path.join(stand_in_bin, "decoy-system-gitconfig")
        seen = []
        raising = []

        def stand_in(*received, **keywords):
            inside = dict(os.environ)
            view = _wrapper_view()
            seen.append((inside, view, _run_wrapper(view["candidate"], inside, decoy, channel),
                         (list(received), dict(keywords))))
            if raising:
                raise raising[0]
            return 0

        setattr(module, inner, stand_in)
        caller, bound = dict(os.environ), dict(vars(module))
        rc = getattr(module, entry)(*args)
        if rc != 0:
            problems.append("the entry point returned {!r} around a stand-in returning 0".format(rc))
        problems.extend(_call_problems(caller, bound, module, recorded, seen, home, fixture_home,
                                       args))
        # The same call around a stand-in that raises: the environment and the module's globals
        # come back exactly on the way out, the stand-in's own exception, not a return value or
        # another exception, comes out of the entry point, and the helpers, the scrub and the
        # wrapper are checked inside this call too.
        raising.append(_StandInRaised("raised by the restore probe's stand-in"))
        del seen[:]
        for calls in recorded.values():
            del calls[:]
        caller, bound = dict(os.environ), dict(vars(module))
        try:
            rc = getattr(module, entry)(*args)
        except BaseException as exc:
            if exc is not raising[0]:
                problems.append("around a raising stand-in, the entry point raised {!r} instead "
                                "of the stand-in's exception".format(exc))
        else:
            problems.append("around a raising stand-in, the entry point returned {!r} instead "
                            "of letting the exception through".format(rc))
        problems.extend("around a raising stand-in, " + problem for problem in _call_problems(
            caller, bound, module, recorded, seen, home, fixture_home, args))
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(fixture_home, ignore_errors=True)
        shutil.rmtree(stand_in_bin, ignore_errors=True)
        shutil.rmtree(channel, ignore_errors=True)
    return problems


def _wrapper_mutants(text, channel):
    """The wrappers _WRAPPER_MUTANTS names, then _SECOND_DEFECT, made from the lifecycle's wrapper
    text, as (name, text) pairs, or None when the text has not exactly one exec line ending in
    "$@" to make them from. channel is the stand-in git's channel, where the stale-nonce wrapper
    plants its record."""
    lines = text.splitlines(keepends=True)
    at = [index for index, line in enumerate(lines) if line.startswith("exec ")]
    tail_args = ' "$@"\n'
    if len(at) != 1 or not lines[at[0]].endswith(tail_args):
        return None
    head, line, tail = "".join(lines[:at[0]]), lines[at[0]], "".join(lines[at[0] + 1:])
    record = shlex.quote(json.dumps({"nonce": "stale", "argv": _expected_argv(),
                                     "env": dict(_EXPECTED_WRAPPER_ENV)}))
    records = shlex.quote(str(Path(channel) / _RECORDS_FILE))
    edits = {
        "stdout-record": "printf '%s\\n' {}\nexit 0\n{}".format(record, line),
        "stale-nonce": "printf '%s\\n' {} >> {}\nexit 0\n{}".format(record, records, line),
        "git-dir": "export GIT_DIR=/nonexistent\n" + line,
        "exit-before-exec": "exit 0\n" + line,
        "runs-twice": line[len("exec "):] + line,
        "pin-override": line[:-len(tail_args)] + " -c gc.auto=1" + tail_args,
    }
    edits[_SECOND_DEFECT] = "export GIT_DIR=/nonexistent\n" + edits["pin-override"]
    names = [name for name, _reason in _WRAPPER_MUTANTS] + [_SECOND_DEFECT]
    return [(name, head + edits[name] + tail) for name in names]


def _mutant_verdict(name, reason, problems):
    """The failures for the mutant wrapper name, given the wrapper check's problems with it: none
    when there is exactly one problem and it starts with reason, the mutant's own; otherwise one
    failure saying the wrapper check accepted the mutant, or refused it for something other than
    its own reason alone."""
    if not problems:
        return ["the wrapper check accepted the {} mutant wrapper".format(name)]
    if len(problems) != 1 or not problems[0].startswith(reason):
        return ["the wrapper check refused the {} mutant wrapper for other than its own reason "
                "alone: {}".format(name, "; ".join(problems))]
    return []


def _mutant_child():
    """Child side of wrapper_mutant_failures: the restore probe's caller environment and stand-in
    git, then, inside the lifecycle, the wrapper check over the lifecycle's own wrapper, over each
    mutant of it and over _SECOND_DEFECT. Returns a dict from each name _mutant_result_names gives
    to its failures."""
    names = _mutant_result_names()
    reasons = dict(_WRAPPER_MUTANTS)
    home = tempfile.mkdtemp(prefix="aiqt-entry-restore-home-")
    fixture_home = tempfile.mkdtemp(prefix="aiqt-entry-restore-fixture-home-")
    stand_in_bin = tempfile.mkdtemp(prefix="aiqt-entry-restore-git-")
    channel = tempfile.mkdtemp(prefix="aiqt-entry-restore-channel-")
    mutants = tempfile.mkdtemp(prefix="aiqt-entry-restore-mutants-")
    try:
        _set_caller_environment(home)
        import _git_fixture_env
        _git_fixture_env._SCRATCH_HOME = fixture_home
        _install_stand_in_git(stand_in_bin, channel)
        decoy = os.path.join(stand_in_bin, "decoy-system-gitconfig")
        caller = dict(os.environ)
        with _git_fixture_env.fixture_git_lifecycle():
            inside = dict(os.environ)
            view = _wrapper_view()
            found = {"lifecycle": _wrapper_problems(view, caller, _run_wrapper(
                view["candidate"], inside, decoy, channel))}
            built = _wrapper_mutants(Path(view["candidate"]).read_text(encoding="utf-8"), channel)
            if built is None:
                missing = ["the lifecycle's wrapper has no single exec line ending in \"$@\" to "
                           "make the mutant from"]
                return {name: found.get(name, missing) for name in names}
            for name, text in built:
                path = Path(mutants) / name
                path.write_text(text, encoding="utf-8")
                path.chmod(0o700)
                problems = _wrapper_problems(view, caller, _run_wrapper(
                    str(path), inside, decoy, channel))
                if name != _SECOND_DEFECT:
                    found[name] = _mutant_verdict(name, reasons[name], problems)
                    continue
                # The git-dir mutant with pin-override's edit too: the wrapper check must report
                # both defects, and the mutant check must not count it as git-dir refused for its
                # own reason.
                both = (reasons["git-dir"], reasons["pin-override"])
                if len(problems) != 2 or not all(
                        any(problem.startswith(reason) for problem in problems) for reason in both):
                    found[name] = ["the wrapper check refused the {} wrapper for {!r}, not for its "
                                   "two defects".format(name, problems)]
                elif not _mutant_verdict("git-dir", reasons["git-dir"], problems):
                    found[name] = ["the mutant check counted the {} wrapper as the git-dir mutant "
                                   "refused for its own reason".format(name)]
                else:
                    found[name] = []
        return found
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(fixture_home, ignore_errors=True)
        shutil.rmtree(stand_in_bin, ignore_errors=True)
        shutil.rmtree(channel, ignore_errors=True)
        shutil.rmtree(mutants, ignore_errors=True)


def _well_formed_spec(spec):
    """Whether spec is the call description entry_restore_failures hands the child: a dict with an
    args list and a helpers list of [module global, attribute] string pairs."""
    return (isinstance(spec, dict) and sorted(spec) == ["args", "helpers"]
            and isinstance(spec["args"], list) and isinstance(spec["helpers"], list)
            and all(isinstance(helper, list) and len(helper) == 2
                    and all(isinstance(part, str) for part in helper)
                    for helper in spec["helpers"]))


def _child_main(argv):
    """The child's entry, reached only through _CHILD_BOOTSTRAP: print the one result record and
    return the exit status (0 clean, 1 problems found, 2 a usage error). argv is
    [_MUTANT_MODE] for wrapper_mutant_failures, else [TOOL, ENTRY, INNER, SPEC] with SPEC the JSON
    call description from entry_restore_failures."""
    if argv == [_MUTANT_MODE]:
        found = _mutant_child()
        print(_RESULT_TAG + json.dumps(found))
        return 1 if any(found.values()) else 0
    spec = None
    if len(argv) == 4:
        try:
            spec = json.loads(argv[3])
        except ValueError:
            spec = None
    if not _well_formed_spec(spec):
        print("usage: _child_main([TOOL, ENTRY, INNER, SPEC]) or _child_main([{}])".format(
            _MUTANT_MODE), file=sys.stderr)
        return 2
    found = _child(argv[0], argv[1], argv[2], spec["args"],
                   [tuple(helper) for helper in spec["helpers"]])
    print(_RESULT_TAG + json.dumps(found))
    return 1 if found else 0
