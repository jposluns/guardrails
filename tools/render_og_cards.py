#!/usr/bin/env python3
"""Render each site's link-preview card (the og:image tools/gen_draft_site.py names) from its composition in
tools/og-cards/ with headless Chrome.

  render_og_cards.py               render every card this repository can render, with /usr/bin/google-chrome
  render_og_cards.py --self-test   check which cards are rendered and which are skipped (no Chrome is started)

Each card is a 1200 by 627 PNG under its site's live root, below 1 MB; a render that gives another size or a
larger file is refused (exit 1) and nothing is kept. Exit 2 when Chrome cannot be started.

The opfiles.ai card source, tools/og-cards/opfiles-og.html, is kept here only as source until OPFiles takes
it. It cannot render in this repository, because its assets (the fonts, palette and logo it loads from
opf/site/draft/assets/baseline/) now live in OPFiles. Its entry stays in CARDS, but the renderer skips it
with a message giving that reason, rather than writing its PNG into opf/site/, a tree this repository no
longer holds. A skipped card is not an error.
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
# Compositions kept only as source, each with the reason it cannot render here. The renderer skips each one
# and says why, so it never starts Chrome on a page whose assets are gone or writes into a tree this
# repository no longer holds.
SOURCE_ONLY = {
    "opfiles-og.html": ("the opfiles.ai card source is kept here only as source until OPFiles takes it; it "
                        "cannot render in this repository because its assets now live in OPFiles"),
}


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


def render_cards(render_one, out, err):
    """Render every card in CARDS through render_one(source, target), skipping each SOURCE_ONLY composition
    with a message on out that names it, its target and the reason; 1 when a render failed, otherwise 0."""
    code = 0
    for composition, target in CARDS:
        reason = SOURCE_ONLY.get(composition)
        if reason is not None:
            print("skipped {}: {}; nothing written to {}".format(composition, reason, target), file=out)
            continue
        error = render_one(ROOT / "tools" / "og-cards" / composition, target)
        if error:
            print("error: {}".format(error), file=err)
            code = 1
    return code


def self_test():
    """Run render_cards with a recording stand-in for render, so no Chrome starts and nothing is written: the
    opfiles.ai card is skipped with its message and never handed to the renderer, and the aiqt.ai card is
    still handed to it, so the skip cannot pass by skipping every card. 0 when every row holds, otherwise 1."""
    import io
    rendered = []

    def record(source, target):
        rendered.append((source.name, target))
        return None

    out, err = io.StringIO(), io.StringIO()
    code = render_cards(record, out, err)
    skip_line = ("skipped opfiles-og.html: the opfiles.ai card source is kept here only as source until OPFiles "
                 "takes it; it cannot render in this repository because its assets now live in OPFiles; "
                 "nothing written to opf/site/assets/opfiles-og.png\n")
    rows = (
        ("entry/opfiles-card-kept", ("opfiles-og.html", "opf/site/assets/opfiles-og.png") in CARDS,
         "the opfiles.ai card's entry is not in CARDS"),
        ("skip/opfiles-not-rendered", not any(name == "opfiles-og.html" for name, _target in rendered),
         "the opfiles.ai card was handed to the renderer: {!r}".format(rendered)),
        ("skip/opfiles-message", out.getvalue() == skip_line,
         "the skip message is {!r}, expected {!r}".format(out.getvalue(), skip_line)),
        ("render/aiqt-rendered", rendered == [("aiqt-og.html", "site/assets/aiqt-og.png")],
         "the renderer was handed {!r}, expected the aiqt.ai card alone".format(rendered)),
        ("render/exit-zero", code == 0 and err.getvalue() == "",
         "render_cards exited {} with {!r} on its error stream".format(code, err.getvalue())),
    )
    failed = [(name, why) for name, held, why in rows if not held]
    for name, why in failed:
        print("FAIL {}: {}".format(name, why))
    if failed:
        print("SELF-TEST FAIL: render_og_cards {} of {} row(s) failed".format(len(failed), len(rows)))
        return 1
    print("SELF-TEST PASS: render_og_cards {} rows".format(len(rows)))
    return 0


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv == ["--self-test"]:
        return self_test()
    if argv:
        print("error: unrecognized arguments {!r}; fail closed".format(argv), file=sys.stderr)
        return 2
    if not os.access(CHROME, os.X_OK):
        print("error: {} is not an executable; nothing rendered".format(CHROME), file=sys.stderr)
        return 2
    return render_cards(render, sys.stdout, sys.stderr)


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
