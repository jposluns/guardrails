#!/usr/bin/env python3
"""Child-status probe (child-status): refuse to run when this process cannot read a child's exit status.

With SIGCHLD ignored (SIG_IGN), the kernel reaps every child at exit, so a wait for it fails with ECHILD and
Python's subprocess module reports the failed child as exit 0. The ignore survives exec, so every gate, hook
and tool started by such a parent reads a failing child as a pass. This file holds the ONE canonical guard:
_child_status_refusal() reads the SIGCHLD disposition, then forks a child that exits with a known status and
requires that status back, and _child_status_refusal_exit(name) writes a refusal naming the caller and exits 2
when either layer fails. Nothing is reset here.

Two layers, because each sees what the other cannot: the disposition read sees a Python-level SIGCHLD handler
that the fork probe would pass, and the fork probe sees a lost status that the disposition read cannot see (a
handler or flag set below Python, such as SA_NOCLDWAIT on a kernel where it reaps children). On a platform
without SIGCHLD or without fork, the guard refuses nothing, and this file's PASS line says that nothing was
probed.

The guard runs first in both runners (tools/run_all_checks.sh and opf/tools/run_all_checks.sh) and in every CI
job of the three workflows, as this file's own launch; in the five host-patch jobs of quality.yml it runs right
after the Python host pin step, which tools/check_python_host_pin.py holds as the step right after
setup-python there. opf/tools/opf.py, tools/check_selftest_execution.py and
the hooks dispatcher (.aiqt/core/hooks/scripts/aiqt_hooks.py and its generated plugin copy) carry inline
copies, because each is a single-file unit that cannot import a sibling; opf/tools/_opf_adopt_complete.py,
opf/tools/_opf_adopt_state.py, opf/tools/_opf_import_complete.py, tools/check_gitleaks_pin.py,
tools/check_must_deliver.py, tools/check_rule_placement.py and tools/check_verifier_delivery.py carry the same
inline copy at their entry lines. tools/check_child_status_coverage.py pins every copy to this text by AST and
enumerates every entry point.

A refusal is itself a child exit status, so it reaches a verdict only through a reader that can still see
statuses: a bash parent reads it correctly under an inherited ignore, and a Python parent that ignores SIGCHLD
reads it as 0. That is why every Python launcher carries its own copy.

  _child_status.py               probe this process; exit 0 when a child's exit status is observable
  _child_status.py --self-test   the probe's own vectors, each child launched through an exec trampoline that
                                 sets the disposition, its status read by a bash parent where it is the
                                 refusal under test

Exit: 0 observable (or a passing self-test); 1 a failing self-test; 2 the refusal (cannot evaluate) or usage.

Disclosed residuals: an ignore or SA_NOCLDWAIT set below Python after interpreter start (a native extension,
ctypes or an embedding host) is not seen by the disposition read, and the fork probe runs only where a guard
is called: at entry and, in opf.py's opf-adopt-state leg, mid-process (that leg calls
opf/tools/_opf_adopt_state.py's self_test() in process, and the guard is its first statement); a child that
ignores SIGCHLD itself loses its own children's statuses, which the parent's guard cannot see; on a kernel
other than the one the plan measured, whether SA_NOCLDWAIT loses statuses is not established; a fork in a
multi-threaded process warns on Python 3.12 and later, so the guard runs at entry, before threads, except in
that mid-process call, which runs after opf.py's earlier self-test legs.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: _child_status.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import os
import signal
import subprocess

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


# ---------------------------------------------------------------------------------------------- self-test

NAME = "_child_status.py"
CHILD_TIMEOUT = 60
# The exec trampoline: set SIGCHLD to the named disposition, then exec the rest of argv, so the target starts
# with that disposition inherited across exec, exactly as from an ignoring launcher.
TRAMPOLINE = ("import os, signal, sys; "
              "signal.signal(signal.SIGCHLD, signal.SIG_IGN if sys.argv[1] == 'ignore' else signal.SIG_DFL); "
              "os.execvp(sys.argv[2], sys.argv[2:])")
# The bash reader: bash reads its own child's exit status correctly under an inherited ignore, and prints it
# as the last stdout line, so the status under test is read by a bash parent, never by Python.
BASH_READER = '"$@"; printf "rc=%s\\n" "$?"'
# A Python reader under the trampoline's disposition: it reports the status it reads from a child that exits 3.
PYTHON_READER = ("import subprocess, sys; "
                 "print(subprocess.run([sys.executable, '-I', '-B', '-c', 'raise SystemExit(3)']).returncode)")
_LOAD_PROBE = ("import importlib.util, os, signal, sys; "
               "spec = importlib.util.spec_from_file_location('child_status_probe', sys.argv[1]); "
               "probe = importlib.util.module_from_spec(spec); spec.loader.exec_module(probe); ")
# A Python-level SIGCHLD handler: the fork probe alone reads the child's status back, the disposition read
# refuses.
HANDLER_CHILD = (_LOAD_PROBE + "signal.signal(signal.SIGCHLD, lambda signum, frame: None); pid = os.fork(); "
                 "os._exit(probe._CHILD_STATUS_PROBE_CODE) if pid == 0 else None; "
                 "print(os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1])); print(probe._child_status_refusal())")
# A lost status the disposition read cannot see (getsignal reports SIG_DFL while the kernel reaps): the fork
# probe refuses.
LOST_CHILD = (_LOAD_PROBE + "signal.signal(signal.SIGCHLD, signal.SIG_IGN); "
              "signal.getsignal = lambda signum: signal.SIG_DFL; print(probe._child_status_refusal())")
# A platform without fork, then one without SIGCHLD (fork kept, so only the SIGCHLD test can say so): the guard
# refuses nothing, and the PASS line says that nothing was probed.
NO_FORK_CHILD = _LOAD_PROBE + "del os.fork; print(probe._child_status_refusal()); probe.main([])"
NO_SIGCHLD_CHILD = _LOAD_PROBE + "del signal.SIGCHLD; print(probe._child_status_refusal()); probe.main([])"
NOTHING_PROBED = ("PASS: child-status: this platform has no SIGCHLD or no fork, so nothing was probed and the "
                  "guard refuses nothing")
PROBE_CHECK_IDS = ("repro/ignored-reads-failed-child-as-zero", "repro/default-reads-failed-child",
                   "live/default-passes", "live/ignored-refuses-by-name", "layer/handler-seen-by-disposition",
                   "layer/lost-status-seen-by-probe", "layer/no-fork-nothing-probed",
                   "layer/no-sigchld-nothing-probed")


def child_env():
    return dict(PATH=os.environ.get("PATH", "/usr/bin:/bin"), LC_ALL="C.UTF-8")


def run_child(args, stdin=""):
    """Launch this interpreter, isolated, with args and captured output: (exit status as read here, stdout,
    stderr), or (an error, "", "")."""
    command = [sys.executable, "-I", "-B"]
    try:
        result = subprocess.run(command + list(args), input=stdin, capture_output=True, text=True,
                                timeout=CHILD_TIMEOUT, env=child_env())
    except (OSError, subprocess.SubprocessError) as exc:
        return "launch failed ({})".format(type(exc).__name__), "", ""
    return result.returncode, result.stdout, result.stderr


def through_bash(disposition, argv, stdin="", cwd=None):
    """Run argv under the trampoline's disposition ("ignore" or "default") as the child of bash, which reads its
    exit status: (status as bash read it or None, stdout without bash's status line, stderr)."""
    command = [sys.executable, "-I", "-B", "-c", TRAMPOLINE, disposition, "bash", "-c", BASH_READER, "bash"]
    try:
        result = subprocess.run(command + list(argv), input=stdin, capture_output=True, text=True,
                                timeout=CHILD_TIMEOUT, env=child_env(), cwd=cwd)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "launch failed ({})".format(type(exc).__name__), ""
    head, _, last = result.stdout.rstrip("\n").rpartition("\n")
    status_line = last if last.startswith("rc=") else ""
    if not status_line or not status_line[3:].isdigit():
        return None, result.stdout, result.stderr
    return int(status_line[3:]), head + "\n" if head else "", result.stderr


def refusal_text(name, reason="SIGCHLD is ignored (SIG_IGN) in this process"):
    """The first line the canonical refusal writes for name."""
    return "error: {} refuses to run: {}, so a failed child would read as a success.".format(name, reason)


def self_test_results(path=None):
    """[(check id, ok, detail)] for PROBE_CHECK_IDS, in that order, against the probe file at path."""
    path = os.path.abspath(path or __file__)
    out = []
    python = [sys.executable, "-I", "-B"]
    for check_id, disposition, want in (("repro/ignored-reads-failed-child-as-zero", "ignore", "0\n"),
                                        ("repro/default-reads-failed-child", "default", "3\n")):
        got = run_child(["-c", TRAMPOLINE, disposition] + python + ["-c", PYTHON_READER])
        out.append((check_id, got[:2] == (0, want), got))
    got = through_bash("default", python + [path])
    out.append(("live/default-passes", got[0] == 0 and got[1].startswith("PASS: child-status") and got[2] == "",
                got))
    got = through_bash("ignore", python + [path])
    out.append(("live/ignored-refuses-by-name", got[0] == 2 and got[1] == ""
                and got[2].startswith(refusal_text(os.path.basename(path))), got))
    got = run_child(["-c", HANDLER_CHILD, path])
    out.append(("layer/handler-seen-by-disposition", got[:2] == (0, "{}\n{}\n".format(
        _CHILD_STATUS_PROBE_CODE, "SIGCHLD is not at its default disposition in this process")), got))
    got = run_child(["-c", LOST_CHILD, path])
    out.append(("layer/lost-status-seen-by-probe", got[:2] == (
        0, "the probe child was reaped before its exit status could be read (ECHILD)\n"), got))
    for check_id, child in (("layer/no-fork-nothing-probed", NO_FORK_CHILD),
                            ("layer/no-sigchld-nothing-probed", NO_SIGCHLD_CHILD)):
        got = run_child(["-c", child, path])
        out.append((check_id, got[:2] == (0, "None\n" + NOTHING_PROBED + "\n"), got))
    return out


def self_test():
    _child_status_refusal_exit(NAME)
    results = self_test_results()
    failed = [(check_id, detail) for check_id, ok, detail in results if not ok]
    if failed or [check_id for check_id, _ok, _detail in results] != list(PROBE_CHECK_IDS):
        print("SELF-TEST FAIL: child-status {} of {} check(s) failed".format(len(failed), len(results)))
        for check_id, detail in failed:
            print("  {}: {!r}".format(check_id, detail))
        return 1
    print("SELF-TEST PASS: child-status {} checks (an ignoring Python reader reads a failed child as 0 and a "
          "default one reads its status; the probe passes at SIG_DFL and, under an inherited SIG_IGN read by a "
          "bash parent, refuses by name with exit 2; the disposition layer sees a Python-level handler the fork "
          "probe passes, and the fork layer sees a lost status the disposition read cannot; without fork, and "
          "without SIGCHLD, it refuses nothing and says that nothing was probed)".format(len(results)))
    return 0


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args:
        print("usage: _child_status.py [--self-test]", file=sys.stderr)
        return 2
    if not (hasattr(signal, "SIGCHLD") and hasattr(os, "fork")):
        print(NOTHING_PROBED)
        return 0
    print("PASS: child-status: SIGCHLD is at SIG_DFL and a probe child's exit status {} was read back".format(
        _CHILD_STATUS_PROBE_CODE))
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(_child_status_refusal_exit("_child_status.py") or main())
