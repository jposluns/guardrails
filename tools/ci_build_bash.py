#!/usr/bin/env python3
"""Pinned bash build for CI (ci-build-bash): fetch GNU bash 5.3 and its official patches 001 to 009, verify every
file against its pin, apply the patches in order, build bash, install it under the runner's temporary directory and
add that directory's bin to GITHUB_PATH. The hooks behaviour self-test parses its heredoc cases with the bash it
finds on PATH and fails (never skips) below bash 5.3, so each CI job that runs it builds this one first, whatever
bash the runner image ships: the generated surfaces job, and the three git-fixture-env config lanes, which rerun
that self-test as a roster member.

  ci_build_bash.py [--self-test]
                                offline vectors over small fixture files in a temporary directory
  ci_build_bash.py [--self-test] --execution-report ABS_PATH
                                the self-test, also writing its execution report, finalized at interpreter
                                exit (tools/_selftest_exit_report.py), as the execution gate runs it
  ci_build_bash.py --build      fetch, verify, build and install; CI only (it needs RUNNER_TEMP and GITHUB_PATH)

The build runs only on the explicit --build. Started with no argument, the script runs its offline self-test, so a
test lane that starts this suite's runner bare (the git-fixture-env lanes start every suite runner directly) never
fetches and never writes GITHUB_PATH, even where RUNNER_TEMP and GITHUB_PATH are set. Any other argument is a usage
error.

Every URL, byte count and sha256 is a literal in PINS. Every file is fetched and checked against its pin (its
sha256, and the archive's byte count too) before anything is extracted. Each file gets up to FETCH_ATTEMPTS (4)
attempts, with waits of RETRY_DELAYS (2, 4 and 8 seconds) between them, but only after a transient transport
failure: a network or host that is unreachable, a connection reset (including a server that closes the connection
before it answers), a timeout, a temporary DNS failure (EAI_AGAIN) or a response read cut short (IncompleteRead),
each also when urllib wraps it in URLError. Nothing else is retried: no HTTP status (an HTTPError of any code), no
certificate or other TLS failure, no permanent DNS failure, no URL that is not https, no body over FETCH_LIMIT, no
local file error and no other exception. Every attempt writes into a fresh temporary file in the downloads
directory, created exclusively; a failed attempt's file is removed, and only that one. A completed attempt's file
is linked to its pinned name, which must not exist yet, and is checked against its pin there, outside the retry,
before the next file is fetched: a size or sha256 mismatch is a refusal at once and is never retried. All ten files,
their retries and their waits share one DOWNLOAD_DEADLINE (300 seconds) on a monotonic clock: no attempt starts once
it has passed, no wait starts that would end after it, each attempt's per-operation socket timeout is FETCH_TIMEOUT
cut down to the time left, and an attempt that finds it passed between two reads stops without a retry. That bound
is checked by this process between blocking operations, so one blocked operation can overrun it by up to its own
socket timeout; no separate process is terminated at the deadline. The patches are applied in PINS order
with patch -p0 --force, which asks nothing and does not reverse a patch that looks reversed, so a patch that does
not apply stops the build. The built bash must report BASH_VERSINFO 5, 3 and 9 (major, minor, patch level), in
the build tree before make install and again as installed, so the patch level the applied patches left is read
from bash itself. GITHUB_PATH is written only after both checks pass.

Exit of --build: 0 installed and added to GITHUB_PATH; 1 refused (a file that is missing or differs from its pin,
or a built bash that does not print 5 3 9 or exits non-zero); 2 not completed (RUNNER_TEMP or GITHUB_PATH unset,
relative or not an existing directory and file; the install or work directory already present; a fetch error that
is not retried, or a transient one still failing after the last attempt; the download deadline passed; an archive
that does not extract; a patch, configure, make or make install step that fails; a built bash that cannot
be started). On exit 1 or 2 nothing is installed: the install directory is removed if make install created it,
and GITHUB_PATH is not written. The work directory (the downloads and the build tree) is left under the runner's
temporary directory. Exit of the self-test: 0 every vector passed; 1 any failed; with --execution-report, 2 when
the report's finalizer refuses the run (tools/_selftest_exit_report.py). A usage error exits 2.

Not checked: the GNU signature of the archive and the patches (the pinned digests stand in for it, so a changed
upstream file is a refusal, never a silent update); the compiler, make and patch that the runner image supplies,
which run as trusted tools; and which bash a later step runs. The hooks self-test looks bash up on its PATH, and
the runner adds each GITHUB_PATH line to the front of PATH for the later steps of the same job; that is GitHub's
documented behaviour, which this script does not observe. In a config lane the execution gate and the lane pass
that PATH on to the rerun hooks self-test with only the lane's git wrapper directories in front. The self-test
drives the whole pipeline over fixture files with sh, patch and make; it never fetches, and a fetch attempted while
it runs fails it. The download attempt, the wait and the clock are injected (provision and build take fetcher, sleep
and clock), so the retry, the waits and the shared deadline are tested with fixture fetchers, a recording sleep and
a fake clock, never the network and never a real wait.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: ci_build_bash.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import contextlib
import errno
import hashlib
import http.client
import io
import os
import shutil
import socket
import ssl
import stat
import subprocess
import tarfile
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _selftest_exit_report  # noqa: E402  (the execution report, finalized at interpreter exit)

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_REL = "tools/selftest_checks.toml"
# This script's own self-test suite in the registry.
SUITE_ID = "ci-build-bash-selftest"

# (file name, URL, byte count or None, sha256): the GNU bash 5.3 archive, then its official patches in the order
# they apply. Every value is a literal, so a new patch level is a reviewed change to this table and to
# WANT_VERSINFO together.
PINS = (
    ("bash-5.3.tar.gz", "https://ftp.gnu.org/gnu/bash/bash-5.3.tar.gz", 11355854,
     "0d5cd86965f869a26cf64f4b71be7b96f90a3ba8b3d74e27e8e9d9d5550f31ba"),
    ("bash53-001", "https://ftp.gnu.org/gnu/bash/bash-5.3-patches/bash53-001", None,
     "1f608434364af86b9b45c8b0ea3fb3b165fb830d27697e6cdfc7ac17dee3287f"),
    ("bash53-002", "https://ftp.gnu.org/gnu/bash/bash-5.3-patches/bash53-002", None,
     "e385548a00130765ec7938a56fbdca52447ab41fabc95a25f19ade527e282001"),
    ("bash53-003", "https://ftp.gnu.org/gnu/bash/bash-5.3-patches/bash53-003", None,
     "f245d9c7dc3f5a20d84b53d249334747940936f09dc97e1dcb89fc3ab37d60ed"),
    ("bash53-004", "https://ftp.gnu.org/gnu/bash/bash-5.3-patches/bash53-004", None,
     "9591d245045529f32f0812f94180b9d9ce9023f5a765c039b852e5dfc99747d0"),
    ("bash53-005", "https://ftp.gnu.org/gnu/bash/bash-5.3-patches/bash53-005", None,
     "cca1ef52dbbf433bc98e33269b64b2c814028efe2538be1e2c9a377da90bc99d"),
    ("bash53-006", "https://ftp.gnu.org/gnu/bash/bash-5.3-patches/bash53-006", None,
     "29119addefed8eff91ae37fd51822c31780ee30d4a28376e96002706c995ff10"),
    ("bash53-007", "https://ftp.gnu.org/gnu/bash/bash-5.3-patches/bash53-007", None,
     "c0976bbfffa1453c7cfdd62058f206a318568ff2d690f5d4fa048793fa3eb299"),
    ("bash53-008", "https://ftp.gnu.org/gnu/bash/bash-5.3-patches/bash53-008", None,
     "097cd723cbfb8907674ac32214063a3fd85282657ec5b4e544d2c0f719653fb4"),
    ("bash53-009", "https://ftp.gnu.org/gnu/bash/bash-5.3-patches/bash53-009", None,
     "eee30fe78a4b0cb2fe20e010e00308899cfc613e0774ebb3c8557a1552f24f8c"),
)
SOURCE_DIR = "bash-5.3"  # the archive's top directory
# BASH_VERSINFO[0], [1] and [2] (major, minor, patch level) as VERSION_SCRIPT prints them.
WANT_VERSINFO = "5 3 9"
VERSION_SCRIPT = 'printf "%s %s %s\\n" "${BASH_VERSINFO[0]}" "${BASH_VERSINFO[1]}" "${BASH_VERSINFO[2]}"'
INSTALL_NAME = "bash-5.3.9"  # the install directory under RUNNER_TEMP; the work directory adds -build
FETCH_TIMEOUT = 60  # seconds for each blocking network operation, cut down to the time the deadline leaves
FETCH_LIMIT = 64 << 20  # bytes per file; every pinned file is far smaller
FETCH_ATTEMPTS = 4  # attempts per file, the first included
RETRY_DELAYS = (2, 4, 8)  # seconds waited before the second, third and fourth attempt
DOWNLOAD_DEADLINE = 300  # seconds on a monotonic clock for all the files, their retries and their waits together
# errno values of an OSError that are a transient network failure: a network or host that cannot be reached.
TRANSIENT_ERRNOS = frozenset((errno.ENETUNREACH, errno.EHOSTUNREACH))
READ_CHUNK = 1 << 16
STEP_TIMEOUT = 1800  # seconds for one patch, configure, make or make install
VERSION_TIMEOUT = 30
OUTPUT_TAIL = 40  # lines of a failed step's output shown


class Refused(Exception):
    """A file that is missing or differs from its pin, or a built bash of another version: exit 1."""


class NotCompleted(Exception):
    """A fetch, extract, patch or build step that did not complete, or an unusable environment: exit 2."""


class Transient(NotCompleted):
    """A transient transport failure of one download attempt: retried, and exit 2 once the attempts run out."""


def is_transient(exc):
    """Whether a network exception is a transient transport failure worth another attempt: a network or host that
    cannot be reached, a connection reset, a timeout, a temporary DNS failure or a response read cut short, also
    as the reason of a URLError. An HTTPError (any status), a TLS failure (a certificate one included), a permanent
    DNS failure and every other exception are not."""
    if isinstance(exc, urllib.error.HTTPError):
        return False
    if isinstance(exc, urllib.error.URLError):
        return isinstance(exc.reason, BaseException) and is_transient(exc.reason)
    if isinstance(exc, ssl.SSLError):
        return False
    if isinstance(exc, socket.gaierror):
        return exc.errno == socket.EAI_AGAIN
    if isinstance(exc, (TimeoutError, ConnectionResetError, http.client.IncompleteRead)):
        return True
    return isinstance(exc, OSError) and exc.errno in TRANSIENT_ERRNOS


def fetch_error(url, exc):
    """The exception that stands for a network exception exc of a download attempt of url: Transient when it is a
    transient transport failure, NotCompleted otherwise."""
    if isinstance(exc, urllib.error.HTTPError):
        return NotCompleted("{}: HTTP status {}".format(url, exc.code))
    kind = Transient if is_transient(exc) else NotCompleted
    return kind("{}: fetch failed ({}: {})".format(url, type(exc).__name__, exc))


def fetch_attempt(url, out, remaining):
    """One download attempt: write the body of url into out, an open binary file. remaining() gives the seconds
    the shared download deadline leaves; each blocking operation gets FETCH_TIMEOUT or that, whichever is less. A
    URL that is not https, a status other than 200, a body over FETCH_LIMIT bytes and a deadline that has passed
    (before the attempt or between two reads) raise NotCompleted; a network error raises what fetch_error gives
    for it. A local write error propagates as the OSError it is."""
    if not url.startswith("https://"):
        raise NotCompleted("{}: not an https URL".format(url))
    timeout = min(FETCH_TIMEOUT, remaining())
    if timeout <= 0:
        raise NotCompleted("{}: the {} s download deadline has passed".format(url, DOWNLOAD_DEADLINE))
    try:
        response = urllib.request.urlopen(url, timeout=timeout)
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise fetch_error(url, exc) from None
    with response:
        if response.status != 200:
            raise NotCompleted("{}: HTTP status {}".format(url, response.status))
        total = 0
        while True:
            if remaining() <= 0:
                raise NotCompleted("{}: the {} s download deadline passed during the download".format(
                    url, DOWNLOAD_DEADLINE))
            try:
                chunk = response.read(READ_CHUNK)
            except (OSError, http.client.HTTPException, ValueError) as exc:
                raise fetch_error(url, exc) from None
            if not chunk:
                return
            total += len(chunk)
            if total > FETCH_LIMIT:
                raise NotCompleted("{}: more than {} bytes".format(url, FETCH_LIMIT))
            out.write(chunk)


def _fetch_with_retries(name, url, downloads, fetcher, sleep, remaining, log):
    """Up to FETCH_ATTEMPTS attempts of fetcher(url, out, remaining), each into a fresh temporary file created
    exclusively in downloads; returns the path of the first attempt that completes. Only Transient is retried,
    after the RETRY_DELAYS wait; a failed attempt's own file is closed and removed. No attempt starts once the
    deadline has passed and no wait starts that would end after it; those, the last attempt failing and any other
    failure raise, NotCompleted for all but an OSError of the run itself."""
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        if remaining() <= 0:
            raise NotCompleted("{}: the {} s download deadline passed before attempt {}".format(
                url, DOWNLOAD_DEADLINE, attempt))
        handle, part = tempfile.mkstemp(prefix=name + ".attempt-", dir=downloads)
        try:
            with os.fdopen(handle, "wb") as out:
                fetcher(url, out, remaining)
        except Transient as exc:
            os.unlink(part)
            if attempt == FETCH_ATTEMPTS:
                raise NotCompleted("{} (still failing after {} attempts)".format(exc, FETCH_ATTEMPTS)) from None
            delay = RETRY_DELAYS[attempt - 1]
            if delay > remaining():
                raise NotCompleted("{} (not retried: a {} s wait would end after the {} s download deadline)".format(
                    exc, delay, DOWNLOAD_DEADLINE)) from None
            log("attempt {} of {} failed ({}); retrying in {} s".format(attempt, FETCH_ATTEMPTS, exc, delay))
            sleep(delay)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(part)
            raise
        else:
            return part
    raise AssertionError("unreachable: every attempt returns or raises")


def download(pins, downloads, fetcher, sleep, clock, log=print):
    """Fetch every file of a PINS-shaped table into the directory downloads, in order, with retries, all under one
    DOWNLOAD_DEADLINE on clock (a monotonic clock in seconds). Each completed file is linked to its pinned name,
    which must not exist yet, and checked against its pin there, outside the retry, before the next file is
    fetched. Raises Refused for a file that differs from its pin and NotCompleted when a fetch does not
    complete."""
    deadline = clock() + DOWNLOAD_DEADLINE

    def remaining():
        return deadline - clock()

    for name, url, size, digest in pins:
        part = _fetch_with_retries(name, url, downloads, fetcher, sleep, remaining, log)
        dest = Path(downloads) / name
        try:
            os.link(part, dest)
        except FileExistsError:
            raise NotCompleted("{} already exists; it was left as it was".format(dest)) from None
        finally:
            os.unlink(part)
        verify(dest, size, digest)
        log("fetched {}".format(url))


def verify(path, size, digest):
    """Refuse a missing, non-regular or unreadable file, one of another byte count (when size is pinned) and one
    of another sha256."""
    path = Path(path)
    hasher = hashlib.sha256()
    total = 0
    try:
        with open(path, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise Refused("{}: not a regular file".format(path.name))
            while True:
                chunk = handle.read(READ_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                hasher.update(chunk)
    except FileNotFoundError:
        raise Refused("{}: missing".format(path.name)) from None
    except OSError as exc:
        raise Refused("{}: unreadable ({})".format(path.name, type(exc).__name__)) from None
    if size is not None and total != size:
        raise Refused("{}: {} bytes, pinned {}".format(path.name, total, size))
    got = hasher.hexdigest()
    if got != digest:
        raise Refused("{}: sha256 {}, pinned {}".format(path.name, got, digest))


@contextlib.contextmanager
def _step(label):
    """A launch that cannot start or that times out stops the run."""
    try:
        yield
    except (OSError, subprocess.SubprocessError) as exc:
        raise NotCompleted("{}: could not run ({})".format(label, exc)) from None


def _require_success(label, proc):
    if proc.returncode != 0:
        tail = proc.stdout.decode("utf-8", "replace").splitlines()[-OUTPUT_TAIL:]
        raise NotCompleted("{}: exit {}\n{}".format(label, proc.returncode, "\n".join("    " + line for line in tail)))


def check_version(bindir, label):
    """Refuse unless bindir/bash, started with no startup file, prints WANT_VERSINFO and exits 0."""
    bash = Path(bindir) / "bash"
    with _step(label):
        proc = subprocess.run([str(bash), "--norc", "--noprofile", "-c", VERSION_SCRIPT], stdin=subprocess.DEVNULL,
                              capture_output=True, env={"PATH": os.defpath, "LC_ALL": "C"}, timeout=VERSION_TIMEOUT)
    if proc.returncode != 0 or proc.stdout != (WANT_VERSINFO + "\n").encode("ascii"):
        raise Refused("{}: BASH_VERSINFO printed {!r} with exit {} (stderr {!r}); want {!r}".format(
            label, proc.stdout.decode("utf-8", "replace"), proc.returncode,
            proc.stderr.decode("utf-8", "replace").strip(), WANT_VERSINFO))


def provision(pins, fetcher, runner_temp, github_path, log=print, sleep=time.sleep, clock=time.monotonic):
    """Fetch, verify, extract, patch, build, check, install, check again, then append the install's bin directory
    to github_path. pins is a PINS-shaped table (the archive first, then the patches in order); fetcher(url, out,
    remaining) is one download attempt as fetch_attempt is; sleep and clock serve the retries and the download
    deadline. Returns the bin directory; raises Refused or NotCompleted, with nothing installed."""
    runner_temp = Path(runner_temp)
    prefix = runner_temp / INSTALL_NAME
    work = runner_temp / (INSTALL_NAME + "-build")
    for path in (prefix, work):
        if os.path.lexists(path):
            raise NotCompleted("{} already exists; nothing was changed".format(path))
    downloads = work / "downloads"
    os.makedirs(downloads)
    download(pins, downloads, fetcher, sleep, clock, log)
    log("verified {} files against their pinned sha256".format(len(pins)))
    try:
        with tarfile.open(downloads / pins[0][0], "r:gz") as archive:
            archive.extractall(work, filter="data")
    except (tarfile.TarError, OSError, EOFError, ValueError) as exc:
        raise NotCompleted("{}: does not extract ({})".format(pins[0][0], exc)) from None
    src = work / SOURCE_DIR
    if not src.is_dir():
        raise NotCompleted("{}: holds no {} directory".format(pins[0][0], SOURCE_DIR))
    for name, _url, _size, _digest in pins[1:]:
        with _step("patch " + name):
            proc = subprocess.run(["patch", "-p0", "--force", "--no-backup-if-mismatch", "--input",
                                   str(downloads / name)], cwd=str(src), stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=STEP_TIMEOUT)
        _require_success("patch " + name, proc)
        log("applied {}".format(name))
    with _step("configure"):
        proc = subprocess.run([str(src / "configure"), "--prefix=" + str(prefix)], cwd=str(src),
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=STEP_TIMEOUT)
    _require_success("configure", proc)
    with _step("make"):
        proc = subprocess.run(["make"], cwd=str(src), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=STEP_TIMEOUT)
    _require_success("make", proc)
    check_version(src, "the built bash")
    log("built bash reports BASH_VERSINFO {}".format(WANT_VERSINFO))
    try:
        with _step("make install"):
            proc = subprocess.run(["make", "install"], cwd=str(src), stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=STEP_TIMEOUT)
        _require_success("make install", proc)
        bindir = prefix / "bin"
        check_version(bindir, "the installed bash")
        with open(github_path, "a", encoding="utf-8") as handle:
            handle.write(str(bindir) + "\n")
    except BaseException:
        shutil.rmtree(prefix, ignore_errors=True)
        raise
    return bindir


def build(environ, fetcher=fetch_attempt, log=print, sleep=time.sleep, clock=time.monotonic):
    """The CI entry: provision PINS under environ's RUNNER_TEMP and append to its GITHUB_PATH. Returns the exit."""
    runner_temp = environ.get("RUNNER_TEMP", "")
    github_path = environ.get("GITHUB_PATH", "")
    if not (os.path.isabs(runner_temp) and os.path.isdir(runner_temp)):
        print("error: RUNNER_TEMP must name an existing absolute directory (got {!r}); this build runs in CI only. "
              "Nothing was installed.".format(runner_temp), file=sys.stderr)
        return 2
    if not (os.path.isabs(github_path) and os.path.isfile(github_path)):
        print("error: GITHUB_PATH must name an existing absolute file (got {!r}); this build runs in CI only. "
              "Nothing was installed.".format(github_path), file=sys.stderr)
        return 2
    try:
        bindir = provision(PINS, fetcher, runner_temp, github_path, log, sleep, clock)
    except Refused as exc:
        print("REFUSED: {}. Nothing was installed.".format(exc), file=sys.stderr)
        return 1
    except (NotCompleted, OSError) as exc:
        print("NOT COMPLETED: {}. Nothing was installed.".format(exc), file=sys.stderr)
        return 2
    print("PASS: bash {} installed; {} is added to GITHUB_PATH for the later steps of this job".format(
        WANT_VERSINFO.replace(" ", "."), bindir))
    return 0


# ---------------------------------------------------------------------------------------------- self-test

FIXTURE_CONFIGURE = """#!/bin/sh
case "$1" in
--prefix=/*) ;;
*) echo "configure: an absolute --prefix is required" ; exit 1 ;;
esac
printf 'prefix = %s\\n' "${1#--prefix=}" > config.mk
"""
FIXTURE_MAKEFILE = """include config.mk
bash: bash.in patchlevel
\tsed "s/@LEVEL@/$$(cat patchlevel)/" bash.in > bash
\tchmod 755 bash
install: bash
\tmkdir -p $(prefix)/bin
\tsed 's/^echo "5 [0-9]* /echo "5 @INSTALLED_MINOR@ /' bash > $(prefix)/bin/bash
\tchmod 755 $(prefix)/bin/bash
"""
# The fixture bash prints its version as VERSION_SCRIPT would, with the level the patches leave in patchlevel.
FIXTURE_BASH_IN = '#!/bin/sh\necho "5 @MINOR@ @LEVEL@"\n'
FIXTURE_PATCH = "--- patchlevel\n+++ patchlevel\n@@ -1 +1 @@\n-{}\n+{}\n"


def _fixture_archive(path, minor="3", installed_minor=None):
    """A gzip tar archive holding a bash-5.3 tree that builds a fixture bash printing "5 <minor> <level>"; its
    make install writes one printing "5 <installed_minor> <level>" (installed_minor defaults to minor)."""
    makefile = FIXTURE_MAKEFILE.replace("@INSTALLED_MINOR@", installed_minor or minor)
    members = (("configure", FIXTURE_CONFIGURE, 0o755), ("Makefile", makefile, 0o644),
               ("bash.in", FIXTURE_BASH_IN.replace("@MINOR@", minor), 0o644), ("patchlevel", "0\n", 0o644))
    with tarfile.open(path, "w:gz") as archive:
        for name, text, mode in members:
            data = text.encode("ascii")
            info = tarfile.TarInfo(SOURCE_DIR + "/" + name)
            info.size, info.mode = len(data), mode
            archive.addfile(info, io.BytesIO(data))


def _fixture_pins(directory, archive="bash-5.3.tar.gz", order=range(1, 10), skew=None):
    """A PINS-shaped table over the fixture files in directory: the archive, then the patches in order. skew names
    one file whose pinned sha256 is replaced by the digest of other bytes."""
    rows = []
    for name in [archive] + ["bash53-{:03d}".format(level) for level in order]:
        data = (Path(directory) / name).read_bytes()
        digest = hashlib.sha256(b"other bytes" if name == skew else data).hexdigest()
        rows.append((name, "fixture://" + name, len(data) if name == archive else None, digest))
    return tuple(rows)


def _fixture_fetcher(directory, missing=()):
    """A download attempt that copies a fixture://NAME file from directory into out; a name in missing fails as a
    download error that is not retried would."""
    def fetcher(url, out, remaining):
        name = url[len("fixture://"):]
        if not url.startswith("fixture://") or name in missing:
            raise NotCompleted("{}: fetch failed (fixture)".format(url))
        out.write((Path(directory) / name).read_bytes())
    return fetcher


class _FakeClock:
    """A monotonic clock that moves only when told to: now() reads it, advance(seconds) moves it, and sleep(seconds)
    records the wait and moves it by that much."""

    def __init__(self, start=1000.0):
        self.now_value = start
        self.sleeps = []

    def now(self):
        return self.now_value

    def advance(self, seconds):
        self.now_value += seconds

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now_value += seconds


def _scripted_fetcher(script, watch, clock=None, cost=0):
    """A download attempt that follows script, a map from URL to a list of steps used one per attempt (the last one
    repeats): bytes are written whole; an exception is raised after b"partial" is written, so a failed attempt
    leaves partial bytes behind it. Each call moves clock by cost seconds first and is recorded in the returned
    list as (url, the attempt file's size when the attempt began, the sorted names in the directory watch). cost is
    a number of seconds, or a tuple of them used one per call (the last one repeats)."""
    calls = []

    def fetcher(url, out, remaining):
        if clock is not None:
            clock.advance(cost[min(len(calls), len(cost) - 1)] if isinstance(cost, tuple) else cost)
        calls.append((url, out.tell(), sorted(os.listdir(watch))))
        steps = script[url]
        step = steps[min(sum(1 for call in calls if call[0] == url), len(steps)) - 1]
        if isinstance(step, BaseException):
            out.write(b"partial")
            out.flush()
            raise step
        out.write(step)
    return fetcher, calls


def _fake_bash(directory, text):
    """A fixture bash in a directory of its own; returns that directory."""
    os.makedirs(directory)
    path = Path(directory) / "bash"
    path.write_text("#!/bin/sh\n" + text, encoding="ascii")
    path.chmod(0o755)
    return directory


def _outcome(call):
    """Run call with its stdout and stderr kept: ("ok", value), ("refused", message), ("not-completed",
    message) or ("crashed", message)."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            return "ok", call()
        except Refused as exc:
            return "refused", str(exc)
        except NotCompleted as exc:
            return "not-completed", str(exc)
        except Exception as exc:
            return "crashed", "{}: {}".format(type(exc).__name__, exc)


def _entry_outcome(args):
    """main(args) with its stdout kept: (exit, stderr text), or ("crashed", message) when it raises."""
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        try:
            return main(args), err.getvalue()
        except Exception as exc:
            return "crashed", "{}: {}".format(type(exc).__name__, exc)


def _registered_check_ids():
    """This suite's registered check ids, read through the execution gate's own schema validator: (set of ids,
    None), or (None, reason)."""
    import check_selftest_execution
    try:
        data = tomllib.loads((ROOT / REGISTRY_REL).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        return None, "{}: {}".format(REGISTRY_REL, exc)
    with contextlib.redirect_stderr(io.StringIO()):
        rows = check_selftest_execution._manifest_rows(data, REGISTRY_REL)
    row = next((row for row in rows or () if row["id"] == SUITE_ID), None)
    if row is None:
        return None, "{} registers no valid suite {}".format(REGISTRY_REL, SUITE_ID)
    return set(row["expected-check-ids"]), None


def self_test(report_path=None):
    results = []
    executed = []
    if report_path is not None:
        # The execution report is finalized at interpreter exit, after this run's cleanup
        # (tools/_selftest_exit_report.py); nothing writes it in band.
        _selftest_exit_report.arm(report_path, SUITE_ID, executed)

    def check(check_id, ok, detail=""):
        results.append((check_id, bool(ok), detail))
        executed.append(check_id)

    # The pin table: the archive, then patches 001 to 009 in order, each at its GNU URL with a sha256 digest.
    names = ["bash-5.3.tar.gz"] + ["bash53-{:03d}".format(level) for level in range(1, 10)]
    urls = (["https://ftp.gnu.org/gnu/bash/bash-5.3.tar.gz"]
            + ["https://ftp.gnu.org/gnu/bash/bash-5.3-patches/" + name for name in names[1:]])
    shape = [(len(row) == 4 and isinstance(row[3], str) and len(row[3]) == 64
              and set(row[3]) <= set("0123456789abcdef")) for row in PINS]
    check("pins/table-shape", [row[0] for row in PINS] == names and [row[1] for row in PINS] == urls
          and [row[2] for row in PINS] == [11355854] + [None] * 9 and all(shape)
          and len(set(row[3] for row in PINS)) == len(PINS), PINS)

    # Any fetch while the self-test runs is a failure: it never touches the network.
    reached = []

    def no_network(*args, **kwargs):
        reached.append(args[:1])
        raise OSError("the self-test never fetches")

    real_urlopen = urllib.request.urlopen
    urllib.request.urlopen = no_network
    try:
        with tempfile.TemporaryDirectory(prefix="ci-build-bash-selftest-") as base:
            base = Path(base)
            fixtures = base / "fixtures"
            os.makedirs(fixtures)
            _fixture_archive(fixtures / "bash-5.3.tar.gz")
            _fixture_archive(fixtures / "minor-2.tar.gz", minor="2")
            _fixture_archive(fixtures / "installed-minor-2.tar.gz", installed_minor="2")
            for level in range(1, 10):
                (fixtures / "bash53-{:03d}".format(level)).write_text(FIXTURE_PATCH.format(level - 1, level),
                                                                     encoding="ascii")
            sample = fixtures / "bash53-001"
            digest = hashlib.sha256(sample.read_bytes()).hexdigest()
            size = len(sample.read_bytes())

            # verify: a file that matches its pin passes; one byte off, another size, or no file at all refuses
            check("verify/match-passes", _outcome(lambda: verify(sample, size, digest)) == ("ok", None))
            changed = base / "changed"
            changed.write_bytes(sample.read_bytes()[:-1] + b"X")
            got = _outcome(lambda: verify(changed, size, digest))
            check("verify/sha256-mismatch-refuses", got[0] == "refused" and "sha256" in got[1], got)
            got = _outcome(lambda: verify(sample, size + 1, digest))
            check("verify/size-mismatch-refuses", got[0] == "refused" and "bytes, pinned" in got[1], got)
            got = _outcome(lambda: verify(base / "absent", None, digest))
            check("verify/missing-file-refuses", got == ("refused", "absent: missing"), got)

            # fetch refuses a URL that is not https before any network call
            plain = io.BytesIO()
            got = _outcome(lambda: fetch_attempt("http://ftp.gnu.org/gnu/bash/bash-5.3.tar.gz", plain,
                                                 lambda: DOWNLOAD_DEADLINE))
            check("fetch/non-https-refuses", got[0] == "not-completed" and "not an https URL" in got[1]
                  and not reached and not plain.getvalue(), got)

            # the version check: exactly 5 3 9 passes; the patch level one below or one above, another minor
            # version, a non-zero exit and a missing binary each refuse
            got = _outcome(lambda: check_version(_fake_bash(base / "v539", 'echo "5 3 9"\n'), "fixture"))
            check("version/match-passes", got == ("ok", None), got)
            got = _outcome(lambda: check_version(_fake_bash(base / "v538", 'echo "5 3 8"\n'), "fixture"))
            check("version/patch-below-refuses", got[0] == "refused" and "'5 3 8\\n'" in got[1], got)
            got = _outcome(lambda: check_version(_fake_bash(base / "v5310", 'echo "5 3 10"\n'), "fixture"))
            check("version/patch-above-refuses", got[0] == "refused" and "'5 3 10\\n'" in got[1], got)
            got = _outcome(lambda: check_version(_fake_bash(base / "v529", 'echo "5 2 9"\n'), "fixture"))
            check("version/minor-refuses", got[0] == "refused" and "'5 2 9\\n'" in got[1], got)
            got = _outcome(lambda: check_version(_fake_bash(base / "vexit", 'echo "5 3 9"\nexit 1\n'), "fixture"))
            check("version/nonzero-exit-refuses", got[0] == "refused" and "exit 1" in got[1], got)
            os.makedirs(base / "vnone")
            got = _outcome(lambda: check_version(base / "vnone", "fixture"))
            check("version/no-binary-refuses", got[0] == "not-completed" and "could not run" in got[1], got)

            # the whole pipeline over the fixtures: each run gets its own runner temp and GITHUB_PATH
            # (a fake clock serves every run, so no run ever really waits)
            def run(name, pins, fetcher=None, clock=None):
                clock = clock or _FakeClock()
                temp = base / name
                os.makedirs(temp)
                github_path = temp / "github_path"
                github_path.write_text("/already/listed\n", encoding="utf-8")
                got = _outcome(lambda: provision(pins, fetcher or _fixture_fetcher(fixtures), temp, github_path,
                                                 log=lambda line: None, sleep=clock.sleep, clock=clock.now))
                untouched = (github_path.read_text(encoding="utf-8") == "/already/listed\n"
                             and not os.path.lexists(temp / INSTALL_NAME))
                return got, temp, github_path, untouched

            got, temp, github_path, _untouched = run("success", _fixture_pins(fixtures))
            bindir = temp / INSTALL_NAME / "bin"
            check("provision/success-installs-and-appends", got == ("ok", bindir)
                  and github_path.read_text(encoding="utf-8") == "/already/listed\n" + str(bindir) + "\n"
                  and (bindir / "bash").is_file(), got)
            got, temp, _github_path, untouched = run("skewed", _fixture_pins(fixtures, skew="bash53-005"))
            check("provision/mismatch-installs-nothing", got[0] == "refused" and "bash53-005: sha256" in got[1]
                  and untouched and not (temp / (INSTALL_NAME + "-build") / SOURCE_DIR).exists(), got)
            got, _temp, _github_path, untouched = run("unfetched", _fixture_pins(fixtures),
                                                      _fixture_fetcher(fixtures, missing=("bash53-009",)))
            check("provision/download-error-installs-nothing", got[0] == "not-completed"
                  and "bash53-009" in got[1] and untouched, got)
            got, _temp, _github_path, untouched = run("reordered",
                                                      _fixture_pins(fixtures, order=(1, 3, 2, 4, 5, 6, 7, 8, 9)))
            check("provision/patch-order-installs-nothing", got[0] == "not-completed"
                  and got[1].startswith("patch bash53-003: exit") and untouched, got)
            got, _temp, _github_path, untouched = run("minor-2", _fixture_pins(fixtures, archive="minor-2.tar.gz"))
            check("provision/built-version-installs-nothing", got[0] == "refused"
                  and got[1].startswith("the built bash:") and untouched, got)
            got, _temp, _github_path, untouched = run("installed-minor-2",
                                                      _fixture_pins(fixtures, archive="installed-minor-2.tar.gz"))
            check("provision/installed-version-removes-install", got[0] == "refused"
                  and got[1].startswith("the installed bash:") and untouched, got)
            temp = base / "present"
            os.makedirs(temp / INSTALL_NAME)
            (temp / "github_path").write_text("", encoding="utf-8")
            got = _outcome(lambda: provision(_fixture_pins(fixtures), _fixture_fetcher(fixtures), temp,
                                             temp / "github_path", log=lambda line: None, sleep=_FakeClock().sleep,
                                             clock=_FakeClock().now))
            check("provision/existing-install-refuses", got[0] == "not-completed" and "already exists" in got[1]
                  and set(os.listdir(temp)) == {INSTALL_NAME, "github_path"} and not os.listdir(temp / INSTALL_NAME)
                  and (temp / "github_path").read_text(encoding="utf-8") == "", got)

            # the classification: only the named transient transport failures, bare or as a URLError's reason, are
            # retried; an HTTP status of any code, a TLS or certificate failure, a permanent DNS failure, a refused
            # connection, a local file error and an invalid URL are not
            def kind(exc):
                return type(fetch_error("https://fixture.invalid/f", exc)).__name__

            transient = [ConnectionResetError(errno.ECONNRESET, "reset"), http.client.RemoteDisconnected("closed"),
                         TimeoutError("timed out"), OSError(errno.ENETUNREACH, "network unreachable"),
                         OSError(errno.EHOSTUNREACH, "no route to host"),
                         socket.gaierror(socket.EAI_AGAIN, "temporary failure in name resolution"),
                         http.client.IncompleteRead(b"part", 10),
                         urllib.error.URLError(ConnectionResetError(errno.ECONNRESET, "reset")),
                         urllib.error.URLError(TimeoutError("timed out")),
                         urllib.error.URLError(OSError(errno.ENETUNREACH, "network unreachable")),
                         urllib.error.URLError(socket.gaierror(socket.EAI_AGAIN, "temporary failure"))]
            got = [(repr(exc), kind(exc)) for exc in transient]
            check("fetch/transient-errors-classified", all(name == "Transient" for _exc, name in got), got)
            permanent = [urllib.error.HTTPError("https://fixture.invalid/f", code, "status", None, None)
                         for code in (404, 408, 429, 500, 502, 503, 504)]
            permanent += [ssl.SSLCertVerificationError(1, "certificate verify failed"), ssl.SSLError(1, "tls"),
                          socket.gaierror(socket.EAI_NONAME, "name or service not known"),
                          urllib.error.URLError(socket.gaierror(socket.EAI_NONAME, "not known")),
                          urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed")),
                          urllib.error.URLError("unknown url type: fixture"),
                          ConnectionRefusedError(errno.ECONNREFUSED, "refused"), PermissionError(errno.EACCES, "no"),
                          FileNotFoundError(errno.ENOENT, "absent"), OSError(errno.ENOSPC, "disk full"),
                          OSError("no errno"), ValueError("unknown url type"), http.client.InvalidURL("bad")]
            got = [(repr(exc), kind(exc)) for exc in permanent]
            check("fetch/other-errors-not-retried", all(name == "NotCompleted" for _exc, name in got), got)

            # one attempt: each blocking operation gets FETCH_TIMEOUT cut down to the time left, and a deadline
            # found passed between two reads stops the attempt with NotCompleted, never Transient (so it is not
            # retried); a read cut short is Transient. urlopen is replaced by an offline fake for these two calls.
            def raised(call):
                try:
                    call()
                except Exception as exc:
                    return exc
                return None

            class FakeResponse:
                status = 200

                def __init__(self, steps, clock, cost):
                    self.steps, self.clock, self.cost = list(steps), clock, cost

                def __enter__(self):
                    return self

                def __exit__(self, *exc_info):
                    return False

                def read(self, size):
                    self.clock.advance(self.cost)
                    step = self.steps.pop(0) if self.steps else b""
                    if isinstance(step, BaseException):
                        raise step
                    return step

            opened = []
            fake = _FakeClock()
            ends = fake.now() + 45
            responses = [FakeResponse([b"a" * 10, b"b" * 10, b"c" * 10], fake, 30),
                         FakeResponse([b"x", http.client.IncompleteRead(b"", 5)], fake, 0)]

            def fake_urlopen(url, timeout=None):
                opened.append((url, timeout))
                return responses.pop(0)

            urllib.request.urlopen = fake_urlopen
            try:
                slow, cut = io.BytesIO(), io.BytesIO()
                slow_exc = raised(lambda: fetch_attempt(PINS[1][1], slow, lambda: ends - fake.now()))
                cut_exc = raised(lambda: fetch_attempt(PINS[2][1], cut, lambda: DOWNLOAD_DEADLINE))
            finally:
                urllib.request.urlopen = no_network
            check("fetch/deadline-stops-attempt-between-reads",
                  type(slow_exc) is NotCompleted and "passed during the download" in str(slow_exc)
                  and slow.getvalue() == b"a" * 10 + b"b" * 10 and type(cut_exc) is Transient
                  and opened == [(PINS[1][1], 45), (PINS[2][1], FETCH_TIMEOUT)], (slow_exc, cut_exc, opened))

            # the retry: a transient failure, then success. The archive's first attempt leaves partial bytes and
            # fails with a connection reset; one 2 s wait, then a second attempt into a new empty file completes and
            # the build installs. Each attempt saw only its own attempt file, and none is left behind.
            pins = _fixture_pins(fixtures)
            archive_url = pins[0][1]
            archive_bytes = (fixtures / pins[0][0]).read_bytes()
            script = {row[1]: [(fixtures / row[0]).read_bytes()] for row in pins}
            script[archive_url] = [fetch_error(archive_url, ConnectionResetError(errno.ECONNRESET, "reset")),
                                   archive_bytes]
            watch = base / "retry-success" / (INSTALL_NAME + "-build") / "downloads"
            fetcher, calls = _scripted_fetcher(script, watch)
            clock = _FakeClock()
            got, temp, github_path, _untouched = run("retry-success", pins, fetcher, clock)
            bindir = temp / INSTALL_NAME / "bin"
            archive_calls = [call for call in calls if call[0] == archive_url]
            check("retry/transient-then-success", got == ("ok", bindir) and len(calls) == len(pins) + 1
                  and len(archive_calls) == 2 and clock.sleeps == [2] and all(call[1] == 0 for call in calls)
                  and all(len([n for n in call[2] if ".attempt-" in n]) == 1 for call in calls)
                  and not [n for n in os.listdir(watch) if ".attempt-" in n]
                  and (watch / pins[0][0]).read_bytes() == archive_bytes
                  and github_path.read_text(encoding="utf-8") == "/already/listed\n" + str(bindir) + "\n",
                  (got, calls, clock.sleeps))

            # the CI entry over the real pin table with a scripted fetcher (nothing reaches the network): a
            # persistent transient failure, a digest mismatch and failures that are not transient
            def entry_run(name, script, cost=0):
                temp = base / name
                os.makedirs(temp)
                listed_here = temp / "github_path"
                listed_here.write_text("/already/listed\n", encoding="utf-8")
                work = temp / (INSTALL_NAME + "-build")
                fetcher, calls = _scripted_fetcher(script, work / "downloads")
                clock = _FakeClock()
                got = _outcome(lambda: build({"RUNNER_TEMP": str(temp), "GITHUB_PATH": str(listed_here)}, fetcher,
                                             log=lambda line: None, sleep=clock.sleep, clock=clock.now))
                untouched = (listed_here.read_text(encoding="utf-8") == "/already/listed\n"
                             and not os.path.lexists(temp / INSTALL_NAME) and not (work / SOURCE_DIR).exists()
                             and not [n for n in os.listdir(work / "downloads") if ".attempt-" in n])
                return got, calls, clock.sleeps, untouched

            url0 = PINS[0][1]
            got, calls, sleeps, untouched = entry_run(
                "retry-exhausted", {url0: [fetch_error(url0, TimeoutError("timed out"))]})
            check("retry/persistent-transient-exits-2", got == ("ok", 2) and [call[0] for call in calls] == [url0] * 4
                  and sleeps == [2, 4, 8] and untouched, (got, calls, sleeps))
            got, calls, sleeps, untouched = entry_run("retry-mismatch", {url0: [b"not the pinned archive"]})
            check("retry/digest-mismatch-not-retried", got == ("ok", 1) and [call[0] for call in calls] == [url0]
                  and sleeps == [] and untouched, (got, calls, sleeps))
            outcomes = []
            for index, exc in enumerate([ssl.SSLCertVerificationError(1, "certificate verify failed"),
                                         urllib.error.HTTPError(url0, 503, "unavailable", None, None),
                                         urllib.error.URLError(socket.gaierror(socket.EAI_NONAME, "not known"))]):
                outcomes.append(entry_run("retry-permanent-{}".format(index), {url0: [fetch_error(url0, exc)]}))
            check("retry/non-transient-not-retried", all(
                got == ("ok", 2) and [call[0] for call in calls] == [url0] and sleeps == [] and untouched
                for got, calls, sleeps, untouched in outcomes), outcomes)

            # a file already at a pinned name in the downloads directory is never replaced, even after a failed
            # attempt was cleaned up: the completed download is refused and the file keeps its bytes
            keep = base / "keep"
            os.makedirs(keep)
            (keep / "bash53-001").write_bytes(b"kept")
            one = _fixture_pins(fixtures)[1:2]
            fetcher, calls = _scripted_fetcher(
                {one[0][1]: [fetch_error(one[0][1], ConnectionResetError(errno.ECONNRESET, "reset")),
                             sample.read_bytes()]}, keep)
            clock = _FakeClock()
            got = _outcome(lambda: download(one, keep, fetcher, clock.sleep, clock.now, log=lambda line: None))
            check("retry/existing-destination-preserved", got[0] == "not-completed" and "already exists" in got[1]
                  and (keep / "bash53-001").read_bytes() == b"kept" and os.listdir(keep) == ["bash53-001"]
                  and len(calls) == 2 and clock.sleeps == [2], (got, calls, clock.sleeps, os.listdir(keep)))

            # the shared deadline across the files: each attempt costs 110 s of the 300, so three files complete,
            # the fourth attempt is never started, and nothing is installed
            pins = _fixture_pins(fixtures)
            script = {row[1]: [(fixtures / row[0]).read_bytes()] for row in pins}
            clock = _FakeClock()
            fetcher, calls = _scripted_fetcher(script, base / "deadline-files" / (INSTALL_NAME + "-build")
                                               / "downloads", clock, 110)
            got, _temp, _github_path, untouched = run("deadline-files", pins, fetcher, clock)
            check("deadline/no-attempt-after-deadline", got[0] == "not-completed"
                  and "deadline passed before attempt 1" in got[1] and pins[3][1] in got[1]
                  and [call[0] for call in calls] == [row[1] for row in pins[:3]] and clock.sleeps == []
                  and untouched, (got, calls, clock.sleeps))

            # no wait that would end after the deadline: the first two files take 200 s; the third fails at 295 s
            # (2 s wait, 5 s left: taken) and again at 299 s (4 s wait, 1 s left: not taken), so it stops at once
            script[pins[2][1]] = [fetch_error(pins[2][1], ConnectionResetError(errno.ECONNRESET, "reset"))]
            clock = _FakeClock()
            fetcher, calls = _scripted_fetcher(script, base / "deadline-wait" / (INSTALL_NAME + "-build")
                                               / "downloads", clock, (100, 100, 95, 2))
            got, _temp, _github_path, untouched = run("deadline-wait", pins, fetcher, clock)
            check("deadline/no-wait-past-deadline", got[0] == "not-completed" and "would end after" in got[1]
                  and len(calls) == 4 and clock.sleeps == [2] and untouched, (got, calls, clock.sleeps))

            # the CI entry: an unusable RUNNER_TEMP or GITHUB_PATH stops it before any fetch
            fetched = []

            def recording(url, out, remaining):
                fetched.append(url)
                raise NotCompleted("recorded")

            listed = base / "listed"
            listed.write_text("", encoding="utf-8")
            got = _outcome(lambda: build({"GITHUB_PATH": str(listed)}, recording))
            check("env/missing-runner-temp-exits-2", got == ("ok", 2) and not fetched, got)
            got = _outcome(lambda: build({"RUNNER_TEMP": str(base), "GITHUB_PATH": "github_path"}, recording))
            check("env/relative-github-path-exits-2", got == ("ok", 2) and not fetched, got)

            # an OSError the pipeline does not expect ends the CI entry with exit 2 and nothing installed
            def failing(url, out, remaining):
                raise PermissionError("refused by the self-test")

            os.makedirs(base / "os-error")
            got = _outcome(lambda: build({"RUNNER_TEMP": str(base / "os-error"), "GITHUB_PATH": str(listed)},
                                         failing, log=lambda line: None))
            check("env/os-error-exits-2", got == ("ok", 2) and listed.read_text(encoding="utf-8") == ""
                  and not os.path.lexists(base / "os-error" / INSTALL_NAME), got)

            # the entry: with no argument or --self-test (with or without --execution-report) it runs the
            # self-test, and it builds only on the explicit --build. self_test is stubbed for these calls, so the
            # self-test never runs itself again. A bare run with RUNNER_TEMP and GITHUB_PATH set, as on a CI
            # runner, runs the self-test and never fetches, writes GITHUB_PATH or creates anything under
            # RUNNER_TEMP, so a test lane that starts this suite's runner bare never starts the build.
            # Against an entry that builds on a bare run (as this script did before --build existed), four vectors
            # fail: main/bare-runs-self-test, whose bare run reaches the build and the stubbed urlopen;
            # main/build-without-env-exits-2, since --build was then a usage error, and also through the reached
            # list it shares with the bare run and the work directory that run leaves under RUNNER_TEMP;
            # main/unknown-argument-exits-2, through that shared reached list alone; and network/never-reached.
            # Resetting reached before the unknown-argument calls would let both of those last two pass against
            # that entry, so it is not reset. main/self-test-forms-run-self-test passes either way: it is a
            # regression guard for the self-test forms.
            from unittest.mock import patch
            entered = []

            def stub_self_test(report_path=None):
                entered.append(report_path)
                return 0

            this = sys.modules[__name__]
            bare = base / "bare"
            os.makedirs(bare)
            bare_listed = base / "bare-github-path"
            bare_listed.write_text("/already/listed\n", encoding="utf-8")
            with (patch.object(this, "self_test", stub_self_test),
                  patch.dict(os.environ, {"RUNNER_TEMP": str(bare), "GITHUB_PATH": str(bare_listed)})):
                got = _entry_outcome([])
            check("main/bare-runs-self-test", got == (0, "") and entered == [None] and not reached
                  and bare_listed.read_text(encoding="utf-8") == "/already/listed\n" and not os.listdir(bare),
                  (got, entered, reached))
            report = str(base / "report.json")
            entered.clear()
            with patch.object(this, "self_test", stub_self_test):
                got = [_entry_outcome(args) for args in (["--self-test"], ["--execution-report", report],
                                                         ["--self-test", "--execution-report", report])]
            check("main/self-test-forms-run-self-test", got == [(0, "")] * 3 and entered == [None, report, report],
                  (got, entered))

            # --build reaches the CI entry, which stops before any fetch while RUNNER_TEMP or GITHUB_PATH is
            # unset; every other argument list is a usage error. Both run with the two unset, so neither builds.
            entered.clear()
            with patch.object(this, "self_test", stub_self_test), patch.dict(os.environ):
                os.environ.pop("RUNNER_TEMP", None)
                os.environ.pop("GITHUB_PATH", None)
                unset = _entry_outcome(["--build"])
                os.environ["RUNNER_TEMP"] = str(bare)
                temp_only = _entry_outcome(["--build"])
                os.environ.pop("RUNNER_TEMP")
                unknown = [_entry_outcome(args) for args in (
                    ["--bogus"], ["--build", "--self-test"], ["--self-test", "--build"],
                    ["--build", "--execution-report", report], ["--execution-report", "report.json"])]
            check("main/build-without-env-exits-2",
                  unset[0] == 2 and unset[1].startswith("error: RUNNER_TEMP must name")
                  and temp_only[0] == 2 and temp_only[1].startswith("error: GITHUB_PATH must name")
                  and not entered and not reached and not os.listdir(bare), (unset, temp_only, entered, reached))
            check("main/unknown-argument-exits-2",
                  all(code == 2 and err.startswith("usage: ") for code, err in unknown) and not entered and not reached,
                  unknown)
    finally:
        urllib.request.urlopen = real_urlopen
    check("network/never-reached", not reached, reached)

    # the in-run self-guard: the executed set is exactly this suite's registered set
    expected, problem = _registered_check_ids()
    harness = []
    if problem is not None:
        harness.append(("execution-set/registry", problem))
    elif len(set(executed)) != len(executed) or set(executed) != expected:
        harness.append(("execution-set/reconciled", dict(missing=sorted(expected - set(executed)),
                                                         extra=sorted(set(executed) - expected))))

    failed = [(check_id, detail) for check_id, ok, detail in results if not ok] + harness
    if failed:
        print("SELF-TEST FAIL: ci-build-bash {} of {} check(s) failed".format(len(failed), len(results)))
        for check_id, detail in failed:
            print("  {}: {!r}".format(check_id, detail))
        return 1
    print("SELF-TEST PASS: ci-build-bash {} checks (the pin table; a sha256 or size mismatch and a missing file "
          "refuse; a non-https URL is refused before any fetch; only BASH_VERSINFO 5 3 9 passes; over fixture "
          "files, a full build installs and appends to GITHUB_PATH, and a mismatch, a failed download, patches out "
          "of order, a wrong built or installed version, an existing install and an unexpected OSError each leave "
          "nothing installed; only the named transient transport failures are retried, four attempts with 2, 4 "
          "and 8 s waits into fresh files, exit 2 once exhausted, a digest mismatch exits 1 without a retry, an "
          "existing download is never replaced, and no attempt or wait passes the shared 300 s deadline; a bare run is the self-test even with RUNNER_TEMP and GITHUB_PATH set, only --build "
          "reaches the build, which exits 2 without them, and any other argument exits 2; no fetch reached the "
          "network)".format(len(results)))
    return 0


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if (len(args) in (2, 3) and args[-2] == "--execution-report" and os.path.isabs(args[-1])
            and args[:-2] in ([], ["--self-test"])):
        return self_test(args[-1])
    if args in ([], ["--self-test"]):
        return self_test()
    if args == ["--build"]:
        return build(os.environ)
    print("usage: ci_build_bash.py [--self-test] [--execution-report ABS_PATH] | ci_build_bash.py --build",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    _selftest_exit_report.exit_with(main())
