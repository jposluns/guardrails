#!/usr/bin/env python3
"""Fail on repository-internal Markdown links whose target does not exist.

Only relative links are checked. External URLs, mailto links, and pure anchors
are out of scope: this gate answers "does the path exist", not "is the URL live".
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_links.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import re
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files, read_text_nonblocking  # noqa: E402  fail-closed tree walk and non-blocking read
from _gen_common import precheck_special_files  # noqa: E402  SPECIAL-FILE-PRECHECK

LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)]+)\)")
SKIP_DIRS = {".git", "node_modules", "__pycache__"}
EXTERNAL = ("http://", "https://", "mailto:", "tel:", "#")


def main() -> int:
    root = precheck_special_files(Path(__file__).resolve().parents[1])
    findings = []
    try:
        for path in sorted(walk_files(root, SKIP_DIRS, suffixes={".md"})):
            try:
                # non-blocking: this walk reads ignored paths too, which the precheck does not cover.
                text = read_text_nonblocking(path)
            except UnicodeDecodeError:
                # a Markdown file that is not utf-8 is a real docs-integrity finding, not a fail-closed
                # read error; keep it a finding (exit 1). An OSError (unreadable) fails closed below.
                findings.append(f"{path.relative_to(root)}: cannot read as utf-8 (UnicodeDecodeError)")
                continue
            for number, line in enumerate(text.splitlines(), 1):
                for target in LINK.findall(line):
                    target = target.split()[0].strip()
                    if not target or target.startswith(EXTERNAL):
                        continue
                    clean = unquote(target.split("#", 1)[0])
                    if not clean:
                        continue
                    resolved = (path.parent / clean).resolve()
                    if not resolved.exists():
                        findings.append(
                            f"{path.relative_to(root)}:{number}: broken link -> {target}"
                        )
    except OSError as exc:
        print(f"error: cannot scan the tree ({exc}); fail-closed", file=sys.stderr)
        return 2
    if findings:
        print(f"FAIL: {len(findings)} broken internal link(s)")
        for finding in findings:
            print(f"  {finding}")
        return 1
    print("PASS: all internal Markdown links resolve")
    return 0


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This entry point reads a
# child's exit status (the git queries of the special-file precheck in tools/_gen_common.py
# among them), so under an ignored SIGCHLD it would read a failing child as a pass; it refuses
# at its entry line instead, before main() runs.
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
    sys.exit(_child_status_refusal_exit("check_links.py") or main())
