#!/usr/bin/env python3
"""Render each site's link-preview card (the og:image tools/gen_draft_site.py names) from its composition in
tools/og-cards/ with headless Chrome.

  render_og_cards.py    render every card with /usr/bin/google-chrome

Each card is a 1200 by 627 PNG under its site's live root, below 1 MB; a render that gives another size or a
larger file is refused (exit 1) and nothing is kept. Exit 2 when Chrome cannot be started.

A card is written as assets/<key>-og-<h>.png, <h> the first 8 lowercase hex digits of the SHA-256 of its
bytes, so its URL changes whenever the card does (a link preview caches the image by its URL). Every other
<key>-og*.png in that assets folder is then removed, so a render never leaves a stale or second card; the
pages name the one card left (tools/gen_draft_site.py refuses none, 2 or a name that is not its bytes' hash).
A new name is a new tracked path: git add it, update its 2 lines in .aiqt/core/ownership.toml (the binary
list, and the release class for the aiqt.ai card), then run tools/gen_draft_site.py and tools/gen_manifest.py.
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

import fnmatch
import hashlib
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
# The hex digits of the card's SHA-256 its name carries (tools/gen_draft_site.py CARD_HASH_DIGITS).
HASH_DIGITS = 8
# (composition under tools/og-cards/, the site's assets folder under the repository root, the site key)
CARDS = (
    ("aiqt-og.html", "site/assets", "aiqt"),
    ("opfiles-og.html", "opf/site/assets", "opfiles"),
)


def png_size(data):
    """(width, height) of PNG bytes, or None when they are not a PNG."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def card_name(key, data):
    """The card's file name for these bytes: <key>-og-<h>.png."""
    return "{}-og-{}.png".format(key, hashlib.sha256(data).hexdigest()[:HASH_DIGITS])


def keep_one_card(folder, key, data, root=ROOT):
    """Write data as the hashed card in folder (under root), then remove every other <key>-og*.png there.
    An error string, or None when the one card is in place."""
    path = Path(root) / folder
    name = card_name(key, data)
    os.makedirs(path, exist_ok=True)
    scratch = path / (".{}.tmp".format(name))
    try:
        with open(scratch, "xb") as handle:
            handle.write(data)
        os.replace(scratch, path / name)
    except OSError as exc:
        return "cannot write {}/{} ({})".format(folder, name, exc)
    print("wrote {}/{} ({} bytes)".format(folder, name, len(data)))
    try:
        others = sorted(entry for entry in os.listdir(path)
                        if fnmatch.fnmatchcase(entry, "{}-og*.png".format(key)) and entry != name)
        for entry in others:
            os.unlink(path / entry)
            print("removed {}/{}".format(folder, entry))
    except OSError as exc:
        return "cannot remove a stale card from {} ({})".format(folder, exc)
    return None


def render(source, folder, key):
    """Render one composition to the site's hashed card in folder; an error string, or None when the card is
    written and no other card of the site is left."""
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
    return keep_one_card(folder, key, data)


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
    for composition, folder, key in CARDS:
        error = render(ROOT / "tools" / "og-cards" / composition, folder, key)
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
