#!/usr/bin/env python3
"""Render each site's link-preview card (the og:image tools/gen_draft_site.py names) from its composition in
tools/og-cards/ with headless Chrome.

  render_og_cards.py    render every card with /usr/bin/google-chrome

Each card is a 1200 by 627 PNG under its site's live root, below 1 MB; a render that gives another size or a
larger file is refused (exit 1) and nothing is kept. Exit 2 when Chrome cannot be started.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: render_og_cards.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import os
import signal
import struct
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHROME = "/usr/bin/google-chrome"
WIDTH, HEIGHT = 1200, 627
MAX_BYTES = 1 << 20
# (composition under tools/og-cards/, card path under the repository root)
CARDS = (
    ("aiqt-og.html", "site/assets/aiqt-og.png"),
    ("opfiles-og.html", "opf/site/assets/opfiles-og.png"),
)


def png_size(data):
    """(width, height) of PNG bytes, or None when they are not a PNG."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def render(source, target):
    """Render one composition to target; an error string, or None when the card is written."""
    with tempfile.TemporaryDirectory(prefix="og-card-") as scratch:
        shot = Path(scratch) / "card.png"
        command = ["/usr/bin/google-chrome", "--headless", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                   "--allow-file-access-from-files", "--force-device-scale-factor=1",
                   "--user-data-dir=" + str(Path(scratch) / "profile"), "--virtual-time-budget=5000",
                   "--window-size={},{}".format(WIDTH, HEIGHT), "--screenshot=" + str(shot), source.as_uri()]
        try:
            subprocess.run(command, check=False, capture_output=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return "cannot run {} ({})".format(CHROME, exc)
        try:
            data = shot.read_bytes()
        except OSError as exc:
            return "no screenshot from {} ({})".format(CHROME, exc)
    size = png_size(data)
    if size != (WIDTH, HEIGHT):
        return "{} rendered at {}, not {}x{}".format(source.name, size, WIDTH, HEIGHT)
    if len(data) >= MAX_BYTES:
        return "{} rendered to {} bytes, not below {}".format(source.name, len(data), MAX_BYTES)
    path = ROOT / target
    os.makedirs(path.parent, exist_ok=True)
    path.write_bytes(data)
    print("wrote {} ({} bytes)".format(target, len(data)))
    return None


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        print("error: unrecognized arguments {!r}; fail closed".format(argv), file=sys.stderr)
        return 2
    if not os.access(CHROME, os.X_OK):
        print("error: {} is not an executable; nothing rendered".format(CHROME), file=sys.stderr)
        return 2
    code = 0
    for composition, target in CARDS:
        error = render(ROOT / "tools" / "og-cards" / composition, target)
        if error:
            print("error: {}".format(error), file=sys.stderr)
            code = 1
    return code


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This script reads the exit status of
# the browser it launches, so under an ignored SIGCHLD it would read a failing render as a pass; it
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
    sys.exit(_child_status_refusal_exit("render_og_cards.py") or main())
