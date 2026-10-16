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
requires the complete fixture scrub to be in effect there (HOME and XDG_CONFIG_HOME at the scratch
home, the global and system config pinned to os.devnull with GIT_CONFIG_NOSYSTEM=1, the maintenance
pins set, no other GIT_* variable), so an entry point cannot pass by dropping its isolation.

The child is this module's _child_main, reached through a fixed -c bootstrap that puts this
directory on the module path; the module has no __main__ block of its own.

Imported only by a self-test; nothing here runs on a production path."""

import json
import os
import shutil
import subprocess
import sys
import tempfile

PROBE_KEY = "GIT_AIQT_CALLER_PROBE"
PROBE_VALUE = "kept"
_RESULT_TAG = "ENTRY-RESTORE "
_CHILD_BOOTSTRAP = ("import sys; sys.path.insert(0, sys.argv[1]); import _entry_restore_selftest; "
                    "sys.exit(_entry_restore_selftest._child_main(sys.argv[2:]))")


def entry_restore_failures(tool, entry, inner, timeout=300):
    """Run the restore probe for tool's entry point (a function name) whose isolated main is inner
    (the module-level function name the entry point calls inside the lifecycle). Returns a list of
    failure strings, empty when the caller's environment came back exactly and the scrub was in
    effect inside. A child that cannot be launched, times out, or reports no result is a failure,
    never a silent pass."""
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


def _child(tool, entry, inner):
    """Child side: set the caller environment, call the entry point in-process with inner replaced
    by a stand-in, and return the list of problems found."""
    import importlib.util
    problems = []
    home = tempfile.mkdtemp(prefix="aiqt-entry-restore-home-")
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
        seen = []

        def stand_in(*_args, **_kwargs):
            seen.append((dict(os.environ), _git_fixture_env.git_fixture_env()))
            return 0

        setattr(module, inner, stand_in)
        caller = dict(os.environ)
        rc = getattr(module, entry)()
        after = dict(os.environ)
        if rc != 0:
            problems.append("the entry point returned {!r} around a stand-in returning 0".format(rc))
        problems.extend("after the call, " + d for d in _diff(caller, after))
        if len(seen) != 1:
            problems.append("the entry point called its isolated main {} times, expected 1".format(
                len(seen)))
        else:
            inside, expected = seen[0]
            pinned = ("HOME", "XDG_CONFIG_HOME", "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL",
                      "GIT_CONFIG_SYSTEM") + _git_fixture_env._MAINTENANCE_PIN_VARS
            for key in pinned:
                if inside.get(key) != expected[key]:
                    problems.append("inside the lifecycle, {} is {!r}, the scrub sets {!r}".format(
                        key, inside.get(key), expected[key]))
            if inside.get("HOME") == home:
                problems.append("inside the lifecycle, HOME is still the caller's home")
            extra = sorted(k for k in inside if k.startswith("GIT_") and k not in pinned)
            if extra:
                problems.append("inside the lifecycle, caller GIT_* variables survive the scrub: "
                                "{}".format(", ".join(extra)))
    finally:
        shutil.rmtree(home, ignore_errors=True)
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
