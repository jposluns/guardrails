#!/usr/bin/env python3
"""Self-test for the OPF copy of the site template's palette gate (opf/.baseline/check_palette.py).

The copy is the template's tools/check_palette.py, vendored unchanged (CC0 1.0, from the baseline package
whose LICENSE and NOTICE.md sit beside its files under opf/site/draft/assets/baseline/). This suite runs
that copy from the OPF root it sits in, never from the parent repository, as a child process (python3 -I -B
with an explicit argv, a timeout and captured output; nothing is loaded into this process), and pins:

  - the copy's bytes (a SHA-256 of the vendored file), so an edit to the copy is a failure here;
  - the OPF runner's two registrations of the gate (this self-test and the live scan);
  - the live scan: the OPF site's own palettes.css passes for the opfiles site and for every site it names;
  - a contrast miss and a scheme disagreement, each a failure;
  - every missing or malformed input (an absent or unreadable file, an empty, unbalanced, unparsable or
    non-UTF-8 sheet, an unsupported @media condition, a value that is not a hex colour, a declaration that
    is not a palette token, an unknown site), each a failure and never a pass;
  - a run of the copy inside a tree holding only the OPF files the gate needs (the copy and palettes.css,
    with no parent repository file beside or above them), from that tree's OPF root, under an audit hook
    that records every file the run opens and every directory it lists: the run must pass, the tree must
    hold the same two files before and after it, the record must name both files, and every recorded path,
    resolved through symbolic links, must lie under that OPF root or in the interpreter's standard library,
    so a gate that reads any other file fails here; the same run given the live palettes.css, which lies
    outside that tree, must record that file as its one outside read, so a hook that records nothing fails.

The audited runs launch this file as a child in its AUDIT_ARG mode (not a user command), which arms the hook
and runs the copy as __main__ in that child with runpy, then writes the recorded paths to a report file
outside the tree. Every check id is a string literal and the set that ran is reconciled against EXPECTED at
the end. Runs as:

    python3 -I -B opf/tools/selftest_palette.py

Exit 0 clean, 1 on a failed check, 2 when the suite cannot evaluate (the copy is not a readable file, or a
child's exit status cannot be read here: the entry line runs the inline child-status guard first). A child
that cannot be started or does not finish inside CHILD_TIMEOUT seconds counts as neither a pass nor a
failure of the gate, so every check that ran it is red.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: selftest_palette.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import hashlib
import json
import os
import shutil
import subprocess
import sysconfig
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
OPF_ROOT = os.path.dirname(HERE)
COPY_REL = ".baseline/check_palette.py"
PALETTES_REL = "site/draft/assets/baseline/palettes.css"
RUNNER_REL = "tools/run_all_checks.sh"
# The wall-clock bound, in seconds, on each run of the copy; the gate reads one or two small sheets.
CHILD_TIMEOUT = 60
# The SHA-256 of the template 1.7.0 tools/check_palette.py, the same value the parent repository's baseline
# pins record for its own copy: the OPF copy is vendored unchanged.
COPY_SHA256 = "cae71e9a43e87c5687bc50dc75784e83b3a5b643a4e9a07298294b9d270971a4"
# The two lines the OPF runner must carry, each resolved from the runner's own directory.
RUNNER_LINES = (
    'run_gate "palette-selftest"            python3 -I -B "$here/selftest_palette.py"',
    'run_gate "palette-opfiles"             python3 -I -B "$here/../.baseline/check_palette.py" --site opfiles'
    ' --palettes "$here/../site/draft/assets/baseline/palettes.css"',
)
# The first argument of this file's audited-run mode: <file> AUDIT_ARG <report> <copy> <copy arguments>.
AUDIT_ARG = "--audited-run"
# The audit events through which a run reads a path: a file opened (builtins.open, io.open and os.open all
# raise "open") and a directory listed.
READ_EVENTS = frozenset(("open", "os.listdir", "os.scandir", "glob.glob", "glob.glob/2"))

EXPECTED = {
    "copy/sha256-unchanged",
    "runner/registers-selftest",
    "runner/registers-live-scan",
    "live/opfiles-passes",
    "live/every-site-passes",
    "fail/contrast-miss",
    "fail/scheme-disagreement",
    "missing/palettes-file",
    "missing/site-palette-file",
    "missing/palettes-is-directory",
    "malformed/empty-sheet",
    "malformed/unbalanced-braces",
    "malformed/text-after-last-rule",
    "malformed/unsupported-media",
    "malformed/non-hex-value",
    "malformed/site-palette-non-token",
    "malformed/non-utf8",
    "malformed/unknown-site",
    "standalone/reads-only-opf-tree",
    "standalone/outside-read-is-seen",
}

_ran = []
_failed = []


def check(cid, ok, detail=""):
    _ran.append(cid)
    if not ok:
        _failed.append(cid)
        print("FAIL %s: %s" % (cid, detail))


def _read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data if isinstance(data, bytes) else data.encode("utf-8"))


def _run(copy_path, argv, cwd):
    """(outcome, output) of the copy run as a child: outcome is its exit status (an exception the gate does
    not catch ends it with status 1 and a traceback), or None when the child could not be started or did not
    finish inside CHILD_TIMEOUT seconds; output is its stdout and stderr together."""
    try:
        child = subprocess.run([sys.executable, "-I", "-B", copy_path] + argv, cwd=cwd,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, errors="replace", timeout=CHILD_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "the child did not run: %s" % type(exc).__name__
    return child.returncode, child.stdout


def _failed_closed(outcome):
    """A failure, never clean: a nonzero exit status (the gate's exit 1, or an exception it did not catch),
    never a child that did not finish; and no pass line."""
    rc, text = outcome
    return rc != 0 and rc is not None and "RESULT: pass" not in text


def _cannot_check(outcome):
    rc, text = outcome
    return rc == 1 and "RESULT: fail (the input could not be checked)" in text


def run_checks(tmp):
    copy_path = os.path.join(OPF_ROOT, COPY_REL)
    palettes = os.path.join(OPF_ROOT, PALETTES_REL)

    def run(argv):
        return _run(copy_path, argv, tmp)

    digest = hashlib.sha256(_read_bytes(copy_path)).hexdigest()
    check("copy/sha256-unchanged", digest == COPY_SHA256, digest)

    runner = _read_bytes(os.path.join(OPF_ROOT, RUNNER_REL)).decode("utf-8").splitlines()
    check("runner/registers-selftest", runner.count(RUNNER_LINES[0]) == 1, RUNNER_LINES[0])
    check("runner/registers-live-scan", runner.count(RUNNER_LINES[1]) == 1, RUNNER_LINES[1])

    rc, text = run(["--site", "opfiles", "--palettes", palettes])
    check("live/opfiles-passes", rc == 0 and "RESULT: pass (1 site, 0 failing checks)" in text, (rc, text[-300:]))
    rc, text = run(["--palettes", palettes])
    check("live/every-site-passes", rc == 0 and "RESULT: pass (" in text and "opfiles / dark" in text,
          (rc, text[-300:]))

    def site(name, body):
        path = os.path.join(tmp, name)
        _write(path, body)
        return path

    base = ["--site", "opfiles", "--palettes", palettes]
    outcome = run(base + ["--site-palette", site("low.css", "html.site-opfiles { --text: #ffffff; }\n")])
    check("fail/contrast-miss", _failed_closed(outcome) and outcome[0] == 1 and "FAIL" in outcome[1]
          and "RESULT: fail (1 site" in outcome[1], outcome)
    outcome = run(base + ["--site-palette", site(
        "split.css", 'html.site-opfiles[data-scheme="dark"] { --bg: #000001; }\n')])
    check("fail/scheme-disagreement", _failed_closed(outcome) and "set the override in every form" in outcome[1],
          outcome)

    absent = os.path.join(tmp, "absent", "palettes.css")
    for cid, argv in (("missing/palettes-file", ["--site", "opfiles", "--palettes", absent]),
                      ("missing/site-palette-file", base + ["--site-palette", absent]),
                      ("missing/palettes-is-directory", ["--site", "opfiles", "--palettes", tmp])):
        outcome = run(argv)
        check(cid, _cannot_check(outcome) and "ERROR: cannot read " in outcome[1], outcome)

    for cid, body in (
            ("malformed/empty-sheet", ""),
            ("malformed/unbalanced-braces", "html.site-opfiles { --bg: #ffffff;\n"),
            ("malformed/text-after-last-rule", "html.site-opfiles { --bg: #ffffff; }\nstray\n"),
            ("malformed/unsupported-media", "@media print { html.site-opfiles { --bg: #ffffff; } }\n")):
        path = site(cid.replace("/", "-") + ".css", body)
        outcome = run(["--palettes", path])
        if cid == "malformed/empty-sheet":
            check(cid, _cannot_check(outcome) and "no .site-<name> palette found" in outcome[1], outcome)
        elif cid == "malformed/unbalanced-braces":
            check(cid, _cannot_check(outcome) and "unbalanced braces" in outcome[1], outcome)
        elif cid == "malformed/text-after-last-rule":
            check(cid, _cannot_check(outcome) and "text after the last rule" in outcome[1], outcome)
        else:
            check(cid, _cannot_check(outcome) and "unsupported @media condition" in outcome[1], outcome)

    outcome = run(base + ["--site-palette", site("named.css", "html.site-opfiles { --text: black; }\n")])
    check("malformed/non-hex-value", _failed_closed(outcome) and "evaluates #rgb or #rrggbb values only"
          in outcome[1], outcome)
    outcome = run(base + ["--site-palette", site("color.css", "html.site-opfiles { color: red; }\n")])
    check("malformed/site-palette-non-token", _cannot_check(outcome) and "is not a palette token" in outcome[1],
          outcome)
    # A sheet that is not UTF-8 ends the copied gate with an exception it does not catch (it catches OSError
    # only); the gate is vendored unchanged, so this pins that the run still fails and never passes.
    outcome = run(["--palettes", site("latin1.css", b"html.site-opfiles { --bg: #ffffff; } /* \xff */\n")])
    check("malformed/non-utf8", _failed_closed(outcome), outcome)
    outcome = run(["--site", "nosuchsite", "--palettes", palettes])
    check("malformed/unknown-site", _cannot_check(outcome) and "unknown site 'nosuchsite'" in outcome[1], outcome)

    only_ok, seen_ok = _standalone(tmp, palettes, copy_path)
    check("standalone/reads-only-opf-tree", only_ok, "see the lines above")
    check("standalone/outside-read-is-seen", seen_ok, "see the lines above")


def _tree(top):
    """The sorted relative paths of every entry under top (files and directories alike)."""
    found = []
    for dirpath, dirnames, filenames in os.walk(top):
        for name in dirnames + filenames:
            found.append(os.path.relpath(os.path.join(dirpath, name), top).replace(os.sep, "/"))
    return sorted(found)


def _audited(copy_path, argv, cwd, report):
    """(outcome, output, paths) of the copy run as a child under the audit hook: this file launched in its
    AUDIT_ARG mode, which runs the copy in that child and writes the recorded paths to report; paths is None
    when the report cannot be read as a JSON list of strings."""
    rc, text = _run(os.path.abspath(__file__), [AUDIT_ARG, report, copy_path] + argv, cwd)
    try:
        with open(report, "rb") as handle:
            paths = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError, RecursionError, MemoryError):
        return rc, text, None
    if type(paths) is not list or not all(type(path) is str for path in paths):
        return rc, text, None
    return rc, text, paths


def _outside(paths, root):
    """The recorded paths that, resolved through symbolic links, lie neither under root nor in the running
    interpreter's standard library (its stdlib and platstdlib directories, lib-dynload included)."""
    allowed = [os.path.realpath(root)]
    for name in ("stdlib", "platstdlib"):
        allowed.append(os.path.realpath(sysconfig.get_path(name)))
    found = []
    for path in paths:
        real = os.path.realpath(path)
        if not any(real == base or real.startswith(base + os.sep) for base in allowed):
            found.append(path)
    return found


def _standalone(tmp, palettes, copy_path):
    """Copy only the gate and the OPF site's palettes.css into a fresh tree whose top holds nothing but that
    opf/ directory (so no parent repository file lies beside or above the OPF files) and run the copy found
    there, under the audit hook, as a child whose working directory is the tree's OPF root. The first result
    requires a pass, the tree holding exactly those files before and after the run, both files among the
    recorded reads and no recorded read outside the tree's OPF root and the standard library. The second
    requires the same run given the live palettes.css (outside the tree) to record that file as its one
    outside read, so the hook is shown to see a read outside the tree."""
    top = os.path.join(tmp, "only")
    root = os.path.join(top, "opf")
    os.makedirs(os.path.join(root, ".baseline"))
    tree_copy = os.path.join(root, COPY_REL)
    tree_palettes = os.path.join(root, PALETTES_REL)
    shutil.copyfile(copy_path, tree_copy)
    os.makedirs(os.path.dirname(tree_palettes))
    shutil.copyfile(palettes, tree_palettes)
    expected = sorted(("opf", "opf/.baseline", "opf/" + COPY_REL, "opf/site", "opf/site/draft",
                       "opf/site/draft/assets", "opf/site/draft/assets/baseline", "opf/" + PALETTES_REL))
    before = _tree(top)
    rc, text, paths = _audited(tree_copy, ["--site", "opfiles", "--palettes", tree_palettes], root,
                               os.path.join(tmp, "only-reads.json"))
    after = _tree(top)
    real = set(os.path.realpath(path) for path in paths or ())
    outside = None if paths is None else _outside(paths, root)
    only_ok = (rc == 0 and "RESULT: pass (1 site" in text and before == expected and after == expected
               and outside == [] and os.path.realpath(tree_copy) in real
               and os.path.realpath(tree_palettes) in real)
    if not only_ok:
        print("standalone: rc=%r output=%r before=%r after=%r outside=%r" % (
            rc, text[-300:], before, after, outside))
    rc, text, paths = _audited(tree_copy, ["--site", "opfiles", "--palettes", palettes], root,
                               os.path.join(tmp, "outside-reads.json"))
    outside = None if paths is None else _outside(paths, root)
    seen_ok = (rc == 0 and "RESULT: pass (1 site" in text and outside is not None
               and [os.path.realpath(path) for path in outside] == [os.path.realpath(palettes)])
    if not seen_ok:
        print("standalone outside read: rc=%r output=%r outside=%r" % (rc, text[-300:], outside))
    return only_ok, seen_ok


def _audited_run(report, copy_path, argv):
    """This file's AUDIT_ARG mode: run the gate copy at copy_path as __main__ in this process, with argv as
    its arguments, under an audit hook that records each path a READ_EVENTS event names (made absolute
    against the working directory), then write those paths to report as a JSON list and return the copy's
    exit status. The report is opened before the hook records anything and written after it stops, so it is
    never among the recorded paths; the copy's own file is, since runpy opens it by path. An exception the
    copy does not catch still writes the report, then ends this process with status 1 and a traceback."""
    import runpy
    seen = []
    armed = []

    def hook(event, args):
        if armed and event in READ_EVENTS and args and isinstance(args[0], (str, bytes, os.PathLike)):
            seen.append(os.path.abspath(os.fsdecode(args[0])))

    with open(report, "w", encoding="utf-8") as out:
        sys.addaudithook(hook)
        sys.argv[:] = [copy_path] + argv
        armed.append(True)
        try:
            runpy.run_path(copy_path, run_name="__main__")
            status = 0
        except SystemExit as exc:
            status = exc.code
        finally:
            armed.clear()
            json.dump(seen, out)
    if status is None:
        return 0
    if type(status) is int:
        return status
    print(status, file=sys.stderr)
    return 1


def main():
    if len(sys.argv) >= 4 and sys.argv[1] == AUDIT_ARG:
        return _audited_run(sys.argv[2], sys.argv[3], sys.argv[4:])
    if len(sys.argv) > 1:
        print("usage: selftest_palette.py (no arguments)", file=sys.stderr)
        return 2
    copy_path = os.path.join(OPF_ROOT, COPY_REL)
    if not os.path.isfile(copy_path) or not os.access(copy_path, os.R_OK):
        print("CANNOT EVALUATE: the palette gate copy is not a readable file: %s" % copy_path)
        return 2
    if not sys.executable:
        print("CANNOT EVALUATE: no interpreter path to run the palette gate copy with")
        return 2
    tmp = tempfile.mkdtemp(prefix="opf-palette-selftest-")
    try:
        run_checks(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ran = set(_ran)
    if ran != EXPECTED or len(_ran) != len(ran):
        print("FAIL reconcile: missing %s, unexpected %s, repeated %s" % (
            sorted(EXPECTED - ran), sorted(ran - EXPECTED), sorted({c for c in _ran if _ran.count(c) > 1})))
        return 1
    if _failed:
        print("SELF-TEST FAIL: selftest_palette %d of %d checks failed: %s" % (len(_failed), len(_ran),
                                                                              ", ".join(_failed)))
        return 1
    print("SELF-TEST PASS: selftest_palette %d checks" % len(_ran))
    return 0


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This suite reads the exit status of each
# run of the palette gate copy, so under an ignored SIGCHLD it would read a failing gate as a pass; it
# refuses at its entry line instead, before main() runs.
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
    sys.exit(_child_status_refusal_exit("selftest_palette.py") or main())
