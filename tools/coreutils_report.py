#!/usr/bin/env python3
"""Coreutils implementation report: which coreutils the suites on this host actually run.

  coreutils_report.py              report every tool in TOOLS; exit 0, or 2 when a tool cannot be run
  coreutils_report.py --self-test  assert the report over executable fixtures in a temporary directory
  coreutils_report.py [--self-test] --execution-report ABS_PATH
  any other argument list is refused with exit 2

For each tool in TOOLS, the report prints the path PATH resolves it to, that path's realpath and the first
line of its --version output that is not blank, then one summary line naming the implementations seen:
GNU, uutils, mixed, or unknown. A host can carry GNU coreutils and uutils coreutils side by side (a
multicall uutils binary behind some names, GNU binaries behind others), and a test that passes against
one implementation can fail against the other, so the local run and the CI run each record what they ran.

The report never fails the run on what it finds: any mix of implementations exits 0, and so does a version
line that names neither (reported as unknown). It exits 2, and says so, when it cannot run a tool it was
asked about: the name is not found on PATH, --version cannot be launched, exits nonzero, prints only blank
lines or nothing on standard output, or does not exit within VERSION_TIMEOUT seconds. It also exits 2 on
an argument list it does not recognize and on a Python older than 3.14.

Each tool is launched by its resolved absolute path in its own session, with standard input from
/dev/null and only PATH and LC_ALL=C in its environment. The bound is on the tool's own exit, and its
whole process group is killed once it exits or the bound passes, so a process it leaves holding standard
output neither stretches the wait nor outlives the report (one that starts a session of its own escapes
the kill). The version line is classified whole; only then are its non-printable characters replaced
with "?" and the line shortened to MAX_LINE characters for display.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: coreutils_report.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

# Coreutils tools the suites are known to reach by bare name, including every coreutils name a text
# search finds at a command position in the tracked shell scripts and workflow run steps (chmod and rmdir
# in opf/enforcement/precommit/install.sh among them). The list is not shown to be complete: that search
# does not parse Python launches or names built at run time, so a tool reached only that way can be
# missing. Kept in this order so two reports compare line by line.
TOOLS = (
    "cp", "mv", "rm", "install", "tee", "truncate", "head", "tail", "cut", "date", "ls", "env", "timeout",
    "dd", "ln", "sha256sum", "mktemp", "dirname", "mkdir", "sleep", "chmod", "rmdir",
)
# The implementation a --version first line names, by the marker it carries; the first match wins.
IMPLEMENTATIONS = (
    ("GNU", "(GNU coreutils)"),
    ("uutils", "(uutils coreutils)"),
)
UNKNOWN = "unknown"
MIXED = "mixed"
VERSION_TIMEOUT = 10
MAX_LINE = 200
# The most of a --version run's standard output that is read; the rest is never read.
MAX_OUTPUT = 65536


def classify(line):
    """The implementation named by a --version first line, or UNKNOWN."""
    for name, marker in IMPLEMENTATIONS:
        if marker in line:
            return name
    return UNKNOWN


def summarize(kinds):
    """One label for the implementations seen: the single one, MIXED for more than one, UNKNOWN for none."""
    seen = sorted(set(kinds))
    if not seen:
        return UNKNOWN
    if len(seen) > 1:
        return MIXED
    return seen[0]


def _printable(text):
    text = "".join(ch if ch.isprintable() else "?" for ch in text)
    return text if len(text) <= MAX_LINE else text[:MAX_LINE] + "[truncated]"


def version_line(text):
    """The first line of text that is not blank, stripped, or "" when every line is blank."""
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _kill_group(proc):
    """Kill the --version child's whole process group (it was started in its own session, so the group id is
    its pid and holds only what it started), or the child alone when the group cannot be signalled. After
    the child is reaped its group id stays taken while any member lives, so the kill reaches only what the
    child left behind; an empty group is no error."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass


def _require_found(tool, resolved):
    """The reason a tool cannot be run when PATH does not resolve it, else None."""
    if resolved is None:
        return "{}: not found on PATH".format(tool)
    return None


def _require_version_ok(tool, returncode, first_line):
    """The reason a finished --version run does not count, else None."""
    if returncode != 0:
        return "{}: --version exited {}".format(tool, returncode)
    if not first_line:
        return "{}: --version printed no line that is not blank on standard output".format(tool)
    return None


def probe(tool, path_env):
    """(row, None) for a tool that ran, or (None, reason) for one that cannot be run.

    A row is (tool, resolved path, realpath, --version first line, implementation)."""
    resolved = shutil.which(tool, path=path_env)
    reason = _require_found(tool, resolved)
    if reason is not None:
        return None, reason
    resolved = os.path.abspath(resolved)
    # Standard output goes to an unnamed file, not a pipe: the wait is on the child's exit alone, so a
    # process it leaves holding standard output cannot hold the wait open past that exit.
    with tempfile.TemporaryFile() as captured:
        try:
            proc = subprocess.Popen([resolved, "--version"], stdin=subprocess.DEVNULL, stdout=captured,
                                    stderr=subprocess.DEVNULL, env={"PATH": path_env, "LC_ALL": "C"},
                                    start_new_session=True)
        except OSError as exc:
            return None, "{}: --version could not be launched ({})".format(tool, exc)
        try:
            returncode = proc.wait(timeout=VERSION_TIMEOUT)
        except subprocess.TimeoutExpired:
            returncode = None
        finally:
            _kill_group(proc)
            proc.wait()
        if returncode is None:
            return None, "{}: --version did not exit within {} s".format(tool, VERSION_TIMEOUT)
        captured.seek(0)
        first = version_line(captured.read(MAX_OUTPUT).decode("utf-8", "replace"))
    reason = _require_version_ok(tool, returncode, first)
    if reason is not None:
        return None, reason
    return (tool, resolved, os.path.realpath(resolved), _printable(first), classify(first)), None


def report(tools, path_env, out):
    """Append the report lines for tools to out; the exit code: 0, or 2 when a tool cannot be run."""
    rows, reasons = [], []
    for tool in tools:
        row, reason = probe(tool, path_env)
        if reason is not None:
            reasons.append(reason)
            out.append("CANNOT RUN: " + reason)
            continue
        rows.append(row)
        out.append("{}: path={} realpath={} version={!r} implementation={}".format(*row))
    counts = {}
    for row in rows:
        counts[row[4]] = counts.get(row[4], 0) + 1
    detail = ", ".join("{} {}".format(name, counts[name]) for name in sorted(counts))
    out.append("SUMMARY: coreutils implementation {} ({} of {} tools reported{}{})".format(
        summarize(row[4] for row in rows), len(rows), len(tools), ": " if detail else "", detail))
    if reasons:
        out.append("EXIT 2: could not run {} of {} tools asked about: {}".format(
            len(reasons), len(tools), ", ".join(reason.split(":", 1)[0] for reason in reasons)))
        return 2
    return 0


def run_report():
    out = []
    code = report(TOOLS, os.environ.get("PATH", os.defpath), out)
    for line in out:
        print(line)
    return code


# The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id record
# the instrumented check() helper appends to; the execution gate reconciles the report against the
# registry.
SUITE_ID = "coreutils-report-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


# Fixture bodies, run by /bin/sh using only its builtins (printf, echo, read, exit, a loop and a
# redirect), so no coreutils tool, host or fixture, is reached from inside a fixture. Each appends its
# own name and arguments to the log beside it ($log), so a case can check every call the report made.
_FIXTURE = "#!/bin/sh\nlog='{log}'\nprintf '%s %s\\n' \"${{0##*/}}\" \"$*\" >> \"$log\"\n{body}\n"
GNU_LINE = "{} (GNU coreutils) 9.7"
UUTILS_LINE = "{} (uutils coreutils) 0.8.0"


def _write_fixture(directory, name, body, log):
    path = Path(directory) / name
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(_FIXTURE.format(log=log, body=body))
    os.chmod(path, 0o755)
    return str(path)


def _case(base, label, specs):
    """A fixture bin directory under base for specs, (name, body or a symlink target name) pairs, and its log.

    A body starting with '->' makes the name a symlink to that fixture, as a multicall binary is linked."""
    directory = Path(base) / label
    os.makedirs(directory)
    log = str(Path(base) / (label + ".log"))
    with open(log, "w", encoding="utf-8"):
        pass
    for name, body in specs:
        if body.startswith("->"):
            os.symlink(body[2:], directory / name)
        else:
            _write_fixture(directory, name, body, log)
    return str(directory), log


def _log(log):
    with open(log, encoding="utf-8") as handle:
        return handle.read().splitlines()


def _run_case(base, label, specs, tools):
    """(exit code, report lines, log lines) for tools over a PATH holding only the case's fixtures."""
    directory, log = _case(base, label, specs)
    out = []
    try:
        code = report(tools, directory, out)
    except Exception as exc:  # a removed guard can crash the report; the case then fails, never passes
        return None, ["EXCEPTION: {!r}".format(exc)], _log(log), directory
    return code, out, _log(log), directory


def _gnu_ok(base, label):
    tools = ("cp", "mv", "rm")
    code, out, log, d = _run_case(base, label, [(t, "echo '{}'".format(GNU_LINE.format(t))) for t in tools],
                                  tools)
    rows = ["{t}: path={d}/{t} realpath={r}/{t} version='{v}' implementation=GNU".format(
        t=t, d=d, r=os.path.realpath(d), v=GNU_LINE.format(t)) for t in tools]
    want = rows + ["SUMMARY: coreutils implementation GNU (3 of 3 tools reported: GNU 3)"]
    return (code, out, log) == (0, want, ["cp --version", "mv --version", "rm --version"]), (code, out, log)


def _uutils_ok(base, label):
    tools = ("tee", "truncate")
    code, out, log, d = _run_case(
        base, label, [(t, "echo '{}'".format(UUTILS_LINE.format(t))) for t in tools], tools)
    want = ["{t}: path={d}/{t} realpath={r}/{t} version='{v}' implementation=uutils".format(
        t=t, d=d, r=os.path.realpath(d), v=UUTILS_LINE.format(t)) for t in tools]
    want.append("SUMMARY: coreutils implementation uutils (2 of 2 tools reported: uutils 2)")
    return (code, out, log) == (0, want, ["tee --version", "truncate --version"]), (code, out, log)


def _mixed_ok(base, label):
    # GNU cp beside a multicall uutils binary that tee and head link to: the realpath shows the link.
    specs = [("cp", "echo '{}'".format(GNU_LINE.format("cp"))),
             ("coreutils", "echo \"${0##*/} (uutils coreutils) 0.8.0\""),
             ("tee", "->coreutils"), ("head", "->coreutils")]
    code, out, log, d = _run_case(base, label, specs, ("cp", "tee", "head"))
    r = os.path.realpath(d)
    want = ["cp: path={}/cp realpath={}/cp version='cp (GNU coreutils) 9.7' implementation=GNU".format(d, r),
            "tee: path={}/tee realpath={}/coreutils version='tee (uutils coreutils) 0.8.0' "
            "implementation=uutils".format(d, r),
            "head: path={}/head realpath={}/coreutils version='head (uutils coreutils) 0.8.0' "
            "implementation=uutils".format(d, r),
            "SUMMARY: coreutils implementation mixed (3 of 3 tools reported: GNU 1, uutils 2)"]
    return (code, out, log) == (0, want, ["cp --version", "tee --version", "head --version"]), (code, out, log)


def _missing_ok(base, label):
    code, out, log, d = _run_case(base, label, [("cp", "echo '{}'".format(GNU_LINE.format("cp")))],
                                  ("cp", "mkdir"))
    want = ["cp: path={d}/cp realpath={r}/cp version='cp (GNU coreutils) 9.7' implementation=GNU".format(
                d=d, r=os.path.realpath(d)),
            "CANNOT RUN: mkdir: not found on PATH",
            "SUMMARY: coreutils implementation GNU (1 of 2 tools reported: GNU 1)",
            "EXIT 2: could not run 1 of 2 tools asked about: mkdir"]
    return (code, out, log) == (2, want, ["cp --version"]), (code, out, log)


def _version_fails_ok(base, label):
    specs = [("date", "echo 'date: unrecognized option' >&2\nexit 3"),
             ("ls", "echo '{}'".format(UUTILS_LINE.format("ls")))]
    code, out, log, d = _run_case(base, label, specs, ("date", "ls"))
    want = ["CANNOT RUN: date: --version exited 3",
            "ls: path={d}/ls realpath={r}/ls version='ls (uutils coreutils) 0.8.0' "
            "implementation=uutils".format(d=d, r=os.path.realpath(d)),
            "SUMMARY: coreutils implementation uutils (1 of 2 tools reported: uutils 1)",
            "EXIT 2: could not run 1 of 2 tools asked about: date"]
    return (code, out, log) == (2, want, ["date --version", "ls --version"]), (code, out, log)


# Each case and the removal that must turn it red: the code under test is replaced by the same function
# or table without it, the case is run again in a fresh directory, and it must fail there.
def _no_gnu():
    return tuple(row for row in IMPLEMENTATIONS if row[0] != "GNU")


def _no_uutils():
    return tuple(row for row in IMPLEMENTATIONS if row[0] != "uutils")


def _summarize_without_mixed(kinds):
    seen = sorted(set(kinds))
    return seen[0] if seen else UNKNOWN


def _found_unchecked(tool, resolved):
    return None


def _version_unchecked(tool, returncode, first_line):
    return None


def _flip(name, value):
    """The previous value of the module global name, after binding it to value."""
    module = sys.modules[__name__]
    previous = getattr(module, name)
    setattr(module, name, value)
    return previous


def _red_with(base, label, case, name, value):
    """True when case fails with the module global name bound to value (the code under test removed)."""
    previous = _flip(name, value)
    try:
        ok, _detail = case(base, label)
    finally:
        _flip(name, previous)
    return not ok


def _row(tool, d, version, kind):
    return "{t}: path={d}/{t} realpath={r}/{t} version={v!r} implementation={k}".format(
        t=tool, d=d, r=os.path.realpath(d), v=version, k=kind)


GNU_SUMMARY = "SUMMARY: coreutils implementation GNU (1 of 1 tools reported: GNU 1)"


def _blank_lines_ok(base, label):
    # Blank and space-only lines come first and a later line names the other implementation: the first
    # line that is not blank is the one reported and classified.
    body = "printf '\\n   \\n%s\\n%s\\n' 'cp (GNU coreutils) 9.7' 'cp (uutils coreutils) 0.8.0'"
    code, out, log, d = _run_case(base, label, [("cp", body)], ("cp",))
    want = [_row("cp", d, "cp (GNU coreutils) 9.7", "GNU"), GNU_SUMMARY]
    return (code, out, log) == (0, want, ["cp --version"]), (code, out, log)


LONG_LINE = "cp " + "x" * 205 + " (GNU coreutils) 9.7"


def _long_line_ok(base, label):
    # The marker sits past the display bound: the line is classified whole and only then shortened.
    code, out, log, d = _run_case(base, label, [("cp", "echo '{}'".format(LONG_LINE))], ("cp",))
    want = [_row("cp", d, LONG_LINE[:200] + "[truncated]", "GNU"), GNU_SUMMARY]
    return (code, out, log) == (0, want, ["cp --version"]), (code, out, log)


def _printable_ok(base, label):
    # An escape and a tab in the version line are shown as "?", never passed through.
    body = "printf 'cp (GNU coreutils) 9.7\\033[1m\\tend\\n'"
    code, out, log, d = _run_case(base, label, [("cp", body)], ("cp",))
    want = [_row("cp", d, "cp (GNU coreutils) 9.7?[1m?end", "GNU"), GNU_SUMMARY]
    return (code, out, log) == (0, want, ["cp --version"]), (code, out, log)


SENTINEL = "COREUTILS_REPORT_SELFTEST_SENTINEL"
_LAUNCH_BODY = ('if read line; then input="read [$line]"; else input="eof [$line]"; fi\n'
                'echo "launch PATH=[$PATH] LC_ALL=[${LC_ALL-unset}] sentinel=[${' + SENTINEL + '-unset}] '
                'stdin=$input" >> "$log"\n'
                "echo 'cp (GNU coreutils) 9.7'")


def _with_stdin(data, thunk):
    """thunk() with this process's standard input a pipe holding data, the previous one restored after."""
    saved = os.dup(0)
    try:
        read_end, write_end = os.pipe()
        try:
            os.write(write_end, data)
        finally:
            os.close(write_end)
        try:
            os.dup2(read_end, 0)
        finally:
            os.close(read_end)
        return thunk()
    finally:
        os.dup2(saved, 0)
        os.close(saved)


def _launch_ok(base, label):
    # This process holds a sentinel variable in its environment and a line on its standard input while the
    # report runs; the fixture logs the PATH, LC_ALL and sentinel it was given and what a read of its
    # standard input returned: only PATH and LC_ALL=C may reach it, and its standard input is empty.
    previous = os.environ.get(SENTINEL)
    os.environ[SENTINEL] = "leaked"
    try:
        code, out, log, d = _with_stdin(b"leaked\n", lambda: _run_case(base, label, [("cp", _LAUNCH_BODY)],
                                                                       ("cp",)))
    except OSError as exc:
        return False, "standard input could not be replaced: {!r}".format(exc)
    finally:
        if previous is None:
            os.environ.pop(SENTINEL, None)
        else:
            os.environ[SENTINEL] = previous
    want_log = ["cp --version", "launch PATH=[{}] LC_ALL=[C] sentinel=[unset] stdin=eof []".format(d)]
    want = [_row("cp", d, "cp (GNU coreutils) 9.7", "GNU"), GNU_SUMMARY]
    return (code, out, log) == (0, want, want_log), (code, out, log)


def _gone(pid, deadline_s=5.0):
    """True once pid is no longer a live process (absent, or a zombie awaiting its reaper) within deadline_s."""
    import time
    end = time.monotonic() + deadline_s
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        try:
            with open("/proc/{}/stat".format(pid), encoding="ascii", errors="replace") as handle:
                if handle.read().rsplit(")", 1)[-1].split()[:1] == ["Z"]:
                    return True
        except OSError:
            pass
        if time.monotonic() >= end:
            return False
        time.sleep(0.05)


def _grandchild_ok(base, label):
    # The tool prints its line and exits at once, leaving a background child that holds its standard output
    # and never ends: the report reads the line without waiting for that child, and leaves it not running.
    body = ("(while :; do :; done) &\n"
            "echo \"grandchild $!\" >> \"$log\"\n"
            "echo 'cp (GNU coreutils) 9.7'")
    previous = _flip("VERSION_TIMEOUT", 3)
    try:
        code, out, log, d = _run_case(base, label, [("cp", body)], ("cp",))
    finally:
        _flip("VERSION_TIMEOUT", previous)
    pids = [int(line.split()[1]) for line in log if line.startswith("grandchild ")]
    gone = [_gone(pid) for pid in pids]
    for pid, ended in zip(pids, gone):
        if not ended:  # only a broken report leaves it; the fixture's own child is killed here by its pid
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    want = [_row("cp", d, "cp (GNU coreutils) 9.7", "GNU"), GNU_SUMMARY]
    ok = code == 0 and out == want and len(pids) == 1 and all(gone)
    return ok, (code, out, log, gone)


def _default_run_ok(base, label):
    # run_report() asks about every name in TOOLS, in order, on the PATH of this process's environment.
    directory, log = _case(base, label, [(t, "echo '{}'".format(GNU_LINE.format(t))) for t in TOOLS])
    saved = os.environ.get("PATH")
    os.environ["PATH"] = directory
    try:
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            code = run_report()
    finally:
        if saved is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = saved
    calls = _log(log)
    last = printed.getvalue().splitlines()[-1:]
    want_last = ["SUMMARY: coreutils implementation GNU ({0} of {0} tools reported: GNU {0})".format(len(TOOLS))]
    ok = code == 0 and calls == [t + " --version" for t in TOOLS] and last == want_last \
        and len(set(TOOLS)) == len(TOOLS)
    return ok, (code, calls, last)


def _self_test():
    with tempfile.TemporaryDirectory(prefix="coreutils-report-selftest-") as base:
        ok, detail = _gnu_ok(base, "gnu")
        check("case/gnu-set", ok, detail)
        check("revert/gnu-detection-removed-red", _red_with(base, "gnu-red", _gnu_ok, "IMPLEMENTATIONS",
                                                            _no_gnu()))

        ok, detail = _uutils_ok(base, "uutils")
        check("case/uutils-set", ok, detail)
        check("revert/uutils-detection-removed-red", _red_with(base, "uutils-red", _uutils_ok,
                                                               "IMPLEMENTATIONS", _no_uutils()))

        ok, detail = _mixed_ok(base, "mixed")
        check("case/mixed-set-exits-0", ok, detail)
        check("revert/mixed-summary-removed-red", _red_with(base, "mixed-red", _mixed_ok, "summarize",
                                                            _summarize_without_mixed))

        ok, detail = _missing_ok(base, "missing")
        check("case/missing-from-path-exits-2", ok, detail)
        check("revert/missing-check-removed-red", _red_with(base, "missing-red", _missing_ok,
                                                            "_require_found", _found_unchecked))

        ok, detail = _version_fails_ok(base, "verfail")
        check("case/version-fails-exits-2", ok, detail)
        check("revert/version-status-check-removed-red", _red_with(base, "verfail-red", _version_fails_ok,
                                                                   "_require_version_ok", _version_unchecked))

        # Reported, never failed: a --version line that names neither implementation is unknown, exit 0.
        code, out, log, _d = _run_case(base, "other", [("sleep", "echo 'sleep (other utilities) 1.0'")],
                                       ("sleep",))
        check("case/unknown-implementation-exits-0",
              code == 0 and out[-1] == "SUMMARY: coreutils implementation unknown (1 of 1 tools reported: "
              "unknown 1)" and log == ["sleep --version"], (code, out, log))

        # A --version that exits 0 but prints nothing on standard output cannot be classified: exit 2.
        code, out, log, _d = _run_case(base, "silent", [("env", "echo 'env 1.0' >&2")], ("env",))
        check("case/version-silent-exits-2",
              code == 2 and "CANNOT RUN: env: --version printed no line that is not blank on standard output"
              in out and out[-1] == "EXIT 2: could not run 1 of 1 tools asked about: env", (code, out, log))

        # A --version that never finishes is stopped at the bound and reported as not runnable.
        previous = _flip("VERSION_TIMEOUT", 1)
        try:
            code, out, log, _d = _run_case(base, "hang", [("dd", "while :; do :; done")], ("dd",))
        finally:
            _flip("VERSION_TIMEOUT", previous)
        check("case/version-hangs-exits-2",
              code == 2 and "CANNOT RUN: dd: --version did not exit within 1 s" in out, (code, out, log))

        # A name that resolves but cannot be launched (an executable bit on a file with no interpreter line
        # the kernel accepts) is reported, never raised.
        directory = Path(base) / "noexec"
        os.makedirs(directory)
        with open(directory / "ln", "wb") as handle:
            handle.write(b"\x00\x01not an executable\n")
        os.chmod(directory / "ln", 0o755)
        out = []
        code = report(("ln",), str(directory), out)
        check("case/launch-error-exits-2",
              code == 2 and len(out) == 3 and out[0].startswith("CANNOT RUN: ln: --version could not be "
                                                                "launched"), (code, out))

        # A name this host's PATH resolves is still not found when the PATH the report is given holds no
        # tool: the report looks only where it is told to.
        host_tool = next((t for t in TOOLS if shutil.which(t)), None)
        os.makedirs(Path(base) / "empty")
        out = []
        code = report((host_tool,), str(Path(base) / "empty"), out) if host_tool else None
        check("case/given-path-only",
              code == 2 and out[:1] == ["CANNOT RUN: {}: not found on PATH".format(host_tool)], (host_tool, out))

        ok, detail = _blank_lines_ok(base, "blank")
        check("case/blank-leading-lines-skipped", ok, detail)
        ok, detail = _long_line_ok(base, "long")
        check("case/long-line-classified-whole", ok, detail)
        ok, detail = _printable_ok(base, "printable")
        check("case/unprintable-shown-as-question-mark", ok, detail)
        ok, detail = _launch_ok(base, "launch")
        check("case/launch-stdin-null-path-and-lc-all-only", ok, detail)
        ok, detail = _grandchild_ok(base, "grandchild")
        check("case/grandchild-holding-output-killed", ok, detail)
        ok, detail = _default_run_ok(base, "roster")
        check("roster/default-run-asks-every-tool", ok, detail)

    got = [_main_exit(argv) for argv in (["--bogus"], ["--self-test", "--bogus"],
                                         ["--execution-report", "relative/report"])]
    check("refuse/unrecognized-argument", got == [2, 2, 2], got)

    if FAILURES:
        print("SELF-TEST FAIL: coreutils_report {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: coreutils_report {} checks (GNU, uutils and mixed fixture sets are reported with "
          "path, realpath and version line and exit 0, an unknown implementation exits 0, a tool missing "
          "from PATH, a --version that exits nonzero, prints nothing, hangs or cannot be launched exits 2; "
          "each of the five main cases fails with its code under test removed; only the given PATH is "
          "searched; the first line that is not blank is classified whole, then shown printable and "
          "shortened; the tool gets standard input from /dev/null and only PATH and LC_ALL=C; a background "
          "child holding its output neither delays the report nor outlives it; the default run asks about "
          "every tool in order; an unrecognized argument exits 2)".format(len(EXECUTED)))
    return 0


def _main_exit(argv):
    """main()'s exit code for the argument list argv, its output captured."""
    import contextlib
    import io
    saved = sys.argv
    sys.argv = [saved[0]] + list(argv)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main()
    finally:
        sys.argv = saved


def main():
    argv = sys.argv[1:]
    # The execution gate appends `--execution-report ABS_PATH` to the self-test launch; accepted only in
    # that trailing two-token form, with an absolute path, alone or after --self-test.
    report_path = None
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        report_path, argv = argv[-1], ["--self-test"]
    if argv not in ([], ["--self-test"]):
        print("error: unrecognized arguments {!r}; fail closed".format(argv), file=sys.stderr)
        return 2
    if argv == ["--self-test"]:
        if report_path is not None:
            # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
            sys.path.append(str(Path(__file__).resolve().parent))
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return _self_test()
    return run_report()


if __name__ == "__main__":
    _code = main()
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
