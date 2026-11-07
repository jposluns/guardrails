#!/usr/bin/env python3
"""Render candidate link-preview cards from their compositions in tools/og-cards/ with headless Chrome, for the
owner's review. The served cards, site/assets/aiqt-og.png and opf/site/assets/opfiles-og.png (the og:image
tools/gen_draft_site.py names), are the owner-approved PNGs from pull request #973; this script does not
produce them and refuses to write over them.

  render_og_cards.py --out DIR      render every candidate card into DIR with /usr/bin/google-chrome
  render_og_cards.py --self-test    assert the output-path decision over tempdir fixtures; Chrome is not started
  render_og_cards.py [--self-test] --execution-report ABS_PATH
  any other argument list is refused with exit 2

DIR is required; each candidate is written as DIR/aiqt-og.png and DIR/opfiles-og.png. An output path that
resolves (every symlink followed, a relative path taken from the working directory, real paths compared) to
either served card, or into site/assets/ or opf/site/assets/, or whose case-folded components end in
site/assets or opf/site/assets (any checkout's served assets directory, not only this one's), is refused with
exit 2 before Chrome is started, and each card path is checked again just before it is written. The write
never opens the card name: the directory is opened without following a final symlink and refused when its
device and inode match a served assets directory or any ancestor of one, and the bytes are renamed onto the
card name from a new exclusively created temporary file, so a hard link or a planted symlink at that name is
replaced as a directory entry, never written through. Each card is a 1200 by 627 PNG below 1 MB; a render
that gives another size or a larger file is refused (exit 1) and nothing is kept. Exit 2 when
/usr/bin/google-chrome is missing or not executable (checked before any render); a Chrome launch that then
fails (an OSError or a timeout from the subprocess) is reported and exits 1, like any other failed render.
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

import errno
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
# (composition under tools/og-cards/, the served card it is a candidate for, under the repository root). The
# served card is never written: a candidate is written under the caller's output directory with the served
# card's file name.
CARDS = (
    ("aiqt-og.html", "site/assets/aiqt-og.png"),
    ("opfiles-og.html", "opf/site/assets/opfiles-og.png"),
)
# The served assets directories, under the repository root: no candidate is written into either.
SERVED_ASSETS = ("site/assets", "opf/site/assets")


class Refused(Exception):
    """An output path that resolves to a served card or into a served assets directory, ends in a served
    assets directory name, or names a directory whose identity is a served assets directory or an ancestor."""


def _suffix_refusal(path):
    """None when the case-folded components of path, after normpath, do not end in site/assets or
    opf/site/assets, otherwise the plain reason it is refused. The comparison is textual and by whole
    components, so it refuses another checkout's served assets directories, which no real-path comparison
    against this checkout's ROOT can see; a component that merely shares the prefix (assets-candidates,
    mysite) does not match."""
    parts = tuple(part.casefold() for part in Path(os.path.normpath(os.fspath(path))).parts)
    for suffix in (("opf", "site", "assets"), ("site", "assets")):
        if parts[-len(suffix):] == suffix:
            return "{} ends in the served assets directory name {}".format(path, "/".join(suffix))
    return None


def path_refusal(path):
    """None when path resolves outside both served cards and both served assets directories and does not end
    in a served assets directory name, otherwise the plain reason it is refused. Real paths are compared
    first: every symlink is resolved, and a relative path is taken from the working directory; the trailing
    component comparison is textual, so another checkout's site/assets or opf/site/assets is refused too."""
    try:
        real = Path(os.path.realpath(path))
        served = [Path(os.path.realpath(ROOT / card)) for _composition, card in CARDS]
        assets = [Path(os.path.realpath(ROOT / directory)) for directory in SERVED_ASSETS]
    except (OSError, ValueError) as exc:
        return "{} cannot be resolved ({})".format(path, exc)
    if real in served:
        return "{} resolves to the served card {}".format(path, real)
    for directory in assets:
        if real.is_relative_to(directory):
            return "{} resolves into the served assets directory {}".format(path, directory)
    return _suffix_refusal(path)


def _assets_identities():
    """The (st_dev, st_ino) of each served assets directory under ROOT and of every ancestor of each, for
    those that exist. A device-and-inode match refuses an output directory that is a served assets directory
    (or an ancestor holding one) reached through a bind mount, a hard-linked alias or any other second path
    that no path comparison can see."""
    identities = set()
    for directory in SERVED_ASSETS:
        resolved = Path(os.path.realpath(ROOT / directory))
        for candidate in [resolved] + list(resolved.parents):
            try:
                status = os.stat(candidate)
            except OSError:
                continue
            identities.add((status.st_dev, status.st_ino))
    return identities


def card_targets(out_dir):
    """The candidate card path under out_dir for each composition, in CARDS order."""
    return [Path(out_dir) / Path(card).name for _composition, card in CARDS]


def output_refusal(out_dir):
    """None when out_dir and every candidate card path under it pass path_refusal, otherwise the reason."""
    if not isinstance(out_dir, str) or not out_dir:
        return "the output directory is empty"
    for path in [Path(out_dir)] + card_targets(out_dir):
        reason = path_refusal(path)
        if reason:
            return reason
    return None


def png_size(data):
    """(width, height) of PNG bytes, or None when they are not a PNG."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def _write_card(path, data):
    """Write data as the card at path without ever opening the card name for writing: the bytes go to a new
    temporary file created exclusively inside the directory and are renamed onto the card name, so a hard
    link or a symlink planted at that name (even between the last check and the write) is replaced as a
    directory entry, never followed or truncated. The directory is created only after the path refusals have
    passed, opened without following a final symlink, and refused when its device and inode match a served
    assets directory or any ancestor of one. Raises Refused for a refused directory; returns an error string
    for a plain I/O failure, or None when the card is written."""
    directory = path.parent
    reason = _suffix_refusal(directory)
    if reason:
        raise Refused(reason)
    os.makedirs(directory, exist_ok=True)
    try:
        dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise Refused("{} is a symlink, not opened as the output directory".format(directory)) from exc
        return "cannot open the output directory {} ({})".format(directory, exc)
    try:
        status = os.fstat(dir_fd)
        if (status.st_dev, status.st_ino) in _assets_identities():
            raise Refused("{} is a served assets directory or an ancestor of one (same device and inode), "
                          "whatever path reaches it".format(directory))
        temp_name = ".{}.{}.tmp".format(path.name, os.urandom(8).hex())
        try:
            fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644,
                         dir_fd=dir_fd)
        except OSError as exc:
            return "cannot create a temporary card file in {} ({})".format(directory, exc)
        try:
            try:
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view):]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(temp_name, path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except OSError as exc:
            try:
                os.unlink(temp_name, dir_fd=dir_fd)
            except OSError:
                pass
            return "cannot write {} through its directory {} ({})".format(path.name, directory, exc)
        return None
    finally:
        os.close(dir_fd)


def render(source, target):
    """Render one composition to target; an error string, or None when the card is written. Raises Refused,
    before Chrome is started and again just before the write, when target resolves to a served card or into
    a served assets directory, and from the write itself when the output directory is refused by name or by
    device and inode."""
    reason = path_refusal(target)
    if reason:
        raise Refused(reason)
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
    path = Path(target)
    reason = path_refusal(path)
    if reason:
        raise Refused(reason)
    error = _write_card(path, data)
    if error:
        return error
    print("wrote {} ({} bytes)".format(path, len(data)))
    return None


USAGE = "usage: render_og_cards.py --out DIR (a directory outside site/assets/ and opf/site/assets/)"


def main(argv=None):
    if argv is None:
        # The process entry: the command line, ended through the execution-report finalizer when the
        # self-test armed it.
        _code = main(sys.argv[1:])
        _finalizer = sys.modules.get("_selftest_exit_report")
        (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
    argv = list(argv)
    # The execution gate appends `--execution-report ABS_PATH` to the self-test launch; accepted only in
    # that trailing two-token form, with an absolute path, alone or after --self-test.
    report_path = None
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        report_path, argv = argv[-1], ["--self-test"]
    if argv == ["--self-test"]:
        if report_path is not None:
            # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
            sys.path.append(str(Path(__file__).resolve().parent))
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return _self_test()
    if len(argv) != 2 or argv[0] != "--out":
        print("error: unrecognized arguments {!r}; fail closed. {}".format(argv, USAGE), file=sys.stderr)
        return 2
    out_dir = argv[1]
    reason = output_refusal(out_dir)
    if reason:
        print("error: refused: {}. The served cards are the owner-approved PNGs and this script never writes "
              "them; name a scratch directory. Nothing rendered.".format(reason), file=sys.stderr)
        return 2
    if not os.access(CHROME, os.X_OK):
        print("error: {} is not an executable; nothing rendered".format(CHROME), file=sys.stderr)
        return 2
    code = 0
    for (composition, _card), target in zip(CARDS, card_targets(out_dir)):
        try:
            error = render(ROOT / "tools" / "og-cards" / composition, target)
        except Refused as exc:
            print("error: refused: {}. Nothing more rendered.".format(exc), file=sys.stderr)
            return 2
        if error:
            print("error: {}".format(error), file=sys.stderr)
            code = 1
    return code


# The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id record
# the instrumented check() helper appends to; the execution gate reconciles the report against the
# registry.
SUITE_ID = "og-cards-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


def _patched(name, value, function, *args):
    """function(*args) with this module's attribute name bound to value, restored afterwards."""
    module = sys.modules[__name__]
    saved = getattr(module, name)
    setattr(module, name, value)
    try:
        return function(*args)
    finally:
        setattr(module, name, saved)


def _main_exit(argv):
    """main()'s exit code for the argument list argv, its output captured."""
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return main(argv)


def _fake_png():
    """The 33 leading bytes of a WIDTH by HEIGHT PNG: enough for png_size, far below MAX_BYTES."""
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", WIDTH, HEIGHT)
            + b"\x08\x06\x00\x00\x00")


def _refusal_cases(tmp):
    """{case: a call giving the decision's reason or None} for each path that must be refused. Each call
    looks path_refusal up at call time, so the guard-removed pass sees the decision removed."""
    served_aiqt, served_opfiles = (ROOT / card for _composition, card in CARDS)
    link_dir = Path(tmp) / "out-with-link"
    os.makedirs(link_dir)
    os.symlink(served_aiqt, link_dir / Path(served_aiqt).name)
    os.symlink(ROOT / "site" / "assets", Path(tmp) / "assets-link")
    relative = os.path.relpath(ROOT / "opf" / "site" / "assets" / "review")
    return {
        "served-aiqt": lambda: path_refusal(served_aiqt),
        "served-opfiles": lambda: path_refusal(served_opfiles),
        "assets-aiqt": lambda: output_refusal(str(ROOT / "site" / "assets" / "candidates")),
        "assets-opfiles": lambda: output_refusal(str(ROOT / "opf" / "site" / "assets" / "review")),
        "symlink-entry": lambda: output_refusal(str(link_dir)),
        "symlink-dir": lambda: output_refusal(str(Path(tmp) / "assets-link" / "new")),
        "relative": (lambda: output_refusal(relative) if not os.path.isabs(relative)
                     else "fixture path is not relative"),
        "assets-dir": lambda: output_refusal(str(ROOT / "site" / "assets")),
    }


def _self_test():
    with tempfile.TemporaryDirectory(prefix="og-cards-selftest-") as tmp:
        cases = _refusal_cases(tmp)
        # Each case refused; then, with path_refusal accepting every path (the guard removed), the same case
        # is accepted, so its refusal check fails without the decision.
        for case, name, removed in (
                ("served-aiqt", "served/aiqt-card-refused", "guard-removed/served/aiqt-card-refused"),
                ("served-opfiles", "served/opfiles-card-refused", "guard-removed/served/opfiles-card-refused"),
                ("assets-aiqt", "assets/aiqt-path-refused", "guard-removed/assets/aiqt-path-refused"),
                ("assets-opfiles", "assets/opfiles-path-refused", "guard-removed/assets/opfiles-path-refused"),
                ("symlink-entry", "symlink/out-entry-to-served-card-refused",
                 "guard-removed/symlink/out-entry-to-served-card-refused"),
                ("symlink-dir", "symlink/out-dir-into-assets-refused",
                 "guard-removed/symlink/out-dir-into-assets-refused"),
                ("relative", "relative/into-assets-refused", "guard-removed/relative/into-assets-refused"),
                ("assets-dir", "bound/assets-dir-itself-refused", "guard-removed/bound/assets-dir-itself-refused")):
            reason = cases[case]()
            check(name, isinstance(reason, str) and "resolves" in reason, reason)
            reason = _patched("path_refusal", lambda path: None, cases[case])
            check(removed, reason is None, reason)

        scratch = Path(tmp) / "scratch"
        reason = output_refusal(str(scratch))
        check("scratch/ordinary-dir-accepted", reason is None, reason)

        # The assets-directory end, compared by path component: the directory itself is refused (above); a
        # sibling sharing its name as a prefix is outside it and accepted; a path one level inside is refused.
        reason = output_refusal(str(ROOT / "site" / "assets-candidates"))
        check("bound/assets-prefix-sibling-accepted", reason is None, reason)
        reason = output_refusal(str(ROOT / "site" / "assets" / "x"))
        check("bound/assets-child-refused", isinstance(reason, str) and "served assets directory" in reason,
              reason)

        reason = output_refusal("")
        check("refuse/empty-out-refused", reason == "the output directory is empty", reason)

        got = [_main_exit(argv) for argv in ([], ["--out"], ["--bogus"], ["--self-test", "--bogus"],
                                             ["--out", str(scratch), "--bogus"],
                                             ["--execution-report", "relative/report"])]
        check("refuse/unrecognized-argument", got == [2] * 6, got)

        # main(): a refused output directory exits 2 before render (the only place Chrome is started) is
        # called; an ordinary scratch directory reaches render once per card, each under that directory.
        calls = []

        def _record(source, target):
            calls.append((source, target))
            return None

        got = _patched("render", _record, _main_exit, ["--out", str(ROOT / "site" / "assets")])
        check("main/refused-out-exit-2-before-chrome", got == 2 and calls == [], (got, calls))
        calls.clear()
        got = _patched("CHROME", sys.executable, _patched, "render", _record, _main_exit, ["--out", str(scratch)])
        check("main/scratch-out-reaches-render",
              got == 0 and [target for _source, target in calls] == card_targets(str(scratch)), (got, calls))

        # render(): over a fixture root, a card path that is a symlink to a served card is refused before
        # Chrome is started, and one that becomes such a symlink while Chrome runs is refused before the
        # write, leaving the fixture's served card unchanged.
        fixture = Path(tmp) / "root"
        served = fixture / CARDS[0][1]
        os.makedirs(served.parent)
        os.makedirs(fixture / SERVED_ASSETS[1])
        served.write_bytes(b"approved")
        out = Path(tmp) / "out"
        os.makedirs(out)
        target = out / served.name
        launched = []
        real_run = subprocess.run

        def _fake_chrome(command, **kwargs):
            launched.append(command)
            shot = [arg for arg in command if arg.startswith("--screenshot=")][0][len("--screenshot="):]
            Path(shot).write_bytes(_fake_png())
            if not os.path.lexists(target):
                os.symlink(served, target)

        def _render_to(card_target):
            try:
                return render(ROOT / "tools" / "og-cards" / CARDS[0][0], card_target)
            except Refused as exc:
                return exc

        def _render():
            return _render_to(target)

        subprocess.run = _fake_chrome
        try:
            got = _patched("ROOT", fixture, _render)
            check("render/recheck-refuses-before-write", isinstance(got, Refused) and len(launched) == 1
                  and served.read_bytes() == b"approved", (got, launched))
            launched.clear()
            got = _patched("ROOT", fixture, _render)
            check("render/recheck-refuses-before-chrome", isinstance(got, Refused) and launched == []
                  and served.read_bytes() == b"approved", (got, launched))
        finally:
            subprocess.run = real_run

        # The textual trailing-component refusal: another checkout's served assets directory, which no
        # real-path comparison against this checkout's ROOT can see, is refused by name, case-folded and by
        # whole components; a component that merely shares the prefix is not a match.
        other = Path(tmp) / "other-checkout" / "site" / "assets"
        os.makedirs(other)
        reasons = [output_refusal(str(other)), output_refusal(str(other) + os.sep),
                   output_refusal(str(Path(tmp) / "other-checkout" / "opf" / "SITE" / "Assets"))]
        check("refuse/other-checkout-assets-suffix",
              all(isinstance(reason, str) and "served assets directory name" in reason for reason in reasons),
              reasons)
        reason = _patched("path_refusal", lambda path: None, output_refusal, str(other))
        check("guard-removed/refuse/other-checkout-assets-suffix", reason is None, reason)
        reasons = [output_refusal(str(Path(tmp) / "mysite" / "assets")),
                   output_refusal(str(Path(tmp) / "site" / "assets2"))]
        check("bound/suffix-component-boundary-accepted", reasons == [None, None], reasons)

        # The write path: the bytes are renamed onto the card name from a new exclusive temporary file
        # through the directory's descriptor, so an entry planted at the card name is replaced, never
        # written through, and an output directory that is a served assets directory (or an ancestor of
        # one) under a second path is refused by device and inode.
        def _plain_chrome(command, **kwargs):
            launched.append(command)
            shot = [arg for arg in command if arg.startswith("--screenshot=")][0][len("--screenshot="):]
            Path(shot).write_bytes(_fake_png())

        hard_target = Path(tmp) / "hard-out" / served.name
        os.makedirs(hard_target.parent)
        os.link(served, hard_target)
        race_target = Path(tmp) / "race-out" / served.name
        os.makedirs(race_target.parent)

        def _plant_then_pass(path):
            # A symlink that appears while the checks report nothing, i.e. between the last check and
            # the write: the rename must replace it, never follow it.
            if not os.path.lexists(race_target):
                os.symlink(served, race_target)
            return None

        subprocess.run = _plain_chrome
        try:
            got = _patched("ROOT", fixture, _render_to, hard_target)
            check("write/hard-link-to-served-card-left-unchanged",
                  got is None and served.read_bytes() == b"approved" and os.stat(served).st_nlink == 1
                  and hard_target.read_bytes() == _fake_png(),
                  (got, served.read_bytes(), os.stat(served).st_nlink))
            got = _patched("ROOT", fixture, _patched, "path_refusal", _plant_then_pass, _render_to,
                           race_target)
            check("write/symlink-planted-after-last-check-left-unchanged",
                  got is None and served.read_bytes() == b"approved" and not os.path.islink(race_target)
                  and race_target.read_bytes() == _fake_png(), (got, served.read_bytes()))
            os.symlink(fixture / "site", Path(tmp) / "mid")
            alias_target = Path(tmp) / "mid" / "assets" / served.name
            got = _patched("ROOT", fixture, _patched, "path_refusal", lambda path: None, _render_to,
                           alias_target)
            check("write/aliased-assets-dir-refused-by-inode",
                  isinstance(got, Refused) and "device and inode" in str(got)
                  and served.read_bytes() == b"approved", (got, served.read_bytes()))
            ancestor_target = fixture / served.name
            got = _patched("ROOT", fixture, _render_to, ancestor_target)
            check("write/assets-ancestor-dir-refused-by-inode",
                  isinstance(got, Refused) and "device and inode" in str(got)
                  and not os.path.exists(ancestor_target), (got,))
        finally:
            subprocess.run = real_run

    if FAILURES:
        print("SELF-TEST FAIL: render_og_cards {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: render_og_cards {} checks (both served cards, a path inside each served assets "
          "directory, a symlink in the output directory to a served card, a symlinked directory into assets, a "
          "relative path into assets and the assets directory itself are each refused, and each is accepted "
          "with the decision removed; an ordinary scratch directory and a prefix-sharing sibling are accepted; "
          "an empty or missing output directory and an unrecognized argument exit 2; a refused output directory "
          "exits 2 before Chrome; a card path redirected to a served card is refused before Chrome and again "
          "before the write; another checkout's assets directory is refused by its trailing components; a hard "
          "link or a late-planted symlink at a card name is replaced by the rename, the served card unchanged; "
          "an aliased assets directory and an ancestor of one are refused by device and inode; Chrome is never "
          "started)".format(len(EXECUTED)))
    return 0


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
