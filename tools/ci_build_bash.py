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
sha256, and the archive's byte count too) before anything is extracted. The patches are applied in PINS order
with patch -p0 --force, which asks nothing and does not reverse a patch that looks reversed, so a patch that does
not apply stops the build. The built bash must report BASH_VERSINFO 5, 3 and 9 (major, minor, patch level), in
the build tree before make install and again as installed, so the patch level the applied patches left is read
from bash itself. GITHUB_PATH is written only after both checks pass.

Exit of --build: 0 installed and added to GITHUB_PATH; 1 refused (a file that is missing or differs from its pin,
or a built bash that does not print 5 3 9 or exits non-zero); 2 not completed (RUNNER_TEMP or GITHUB_PATH unset,
relative or not an existing directory and file; the install or work directory already present; a fetch error; an
archive that does not extract; a patch, configure, make or make install step that fails; a built bash that cannot
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
it runs fails it.
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
import hashlib
import http.client
import io
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import tomllib
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
FETCH_TIMEOUT = 60  # seconds for each blocking network operation
FETCH_LIMIT = 64 << 20  # bytes per file; every pinned file is far smaller
READ_CHUNK = 1 << 16
STEP_TIMEOUT = 1800  # seconds for one patch, configure, make or make install
VERSION_TIMEOUT = 30
OUTPUT_TAIL = 40  # lines of a failed step's output shown


class Refused(Exception):
    """A file that is missing or differs from its pin, or a built bash of another version: exit 1."""


class NotCompleted(Exception):
    """A fetch, extract, patch or build step that did not complete, or an unusable environment: exit 2."""


def fetch(url, dest):
    """Download url to dest, created exclusively. A URL that is not https, a status other than 200, a body over
    FETCH_LIMIT bytes and every network error stop the run."""
    if not url.startswith("https://"):
        raise NotCompleted("{}: not an https URL".format(url))
    try:
        with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as response, open(dest, "xb") as out:
            if response.status != 200:
                raise NotCompleted("{}: HTTP status {}".format(url, response.status))
            total = 0
            while True:
                chunk = response.read(READ_CHUNK)
                if not chunk:
                    return
                total += len(chunk)
                if total > FETCH_LIMIT:
                    raise NotCompleted("{}: more than {} bytes".format(url, FETCH_LIMIT))
                out.write(chunk)
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise NotCompleted("{}: fetch failed ({})".format(url, exc)) from None


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


def provision(pins, fetcher, runner_temp, github_path, log=print):
    """Fetch, verify, extract, patch, build, check, install, check again, then append the install's bin directory
    to github_path. pins is a PINS-shaped table (the archive first, then the patches in order) and fetcher(url,
    dest) downloads one file. Returns the bin directory; raises Refused or NotCompleted, with nothing installed."""
    runner_temp = Path(runner_temp)
    prefix = runner_temp / INSTALL_NAME
    work = runner_temp / (INSTALL_NAME + "-build")
    for path in (prefix, work):
        if os.path.lexists(path):
            raise NotCompleted("{} already exists; nothing was changed".format(path))
    downloads = work / "downloads"
    os.makedirs(downloads)
    for name, url, _size, _digest in pins:
        fetcher(url, downloads / name)
        log("fetched {}".format(url))
    for name, _url, size, digest in pins:
        verify(downloads / name, size, digest)
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


def build(environ, fetcher=fetch, log=print):
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
        bindir = provision(PINS, fetcher, runner_temp, github_path, log)
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
    """Copy a fixture://NAME file from directory; a name in missing fails as a download error would."""
    def fetcher(url, dest):
        name = url[len("fixture://"):]
        if not url.startswith("fixture://") or name in missing:
            raise NotCompleted("{}: fetch failed (fixture)".format(url))
        shutil.copyfile(Path(directory) / name, dest)
    return fetcher


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
            got = _outcome(lambda: fetch("http://ftp.gnu.org/gnu/bash/bash-5.3.tar.gz", base / "plain"))
            check("fetch/non-https-refuses", got[0] == "not-completed" and "not an https URL" in got[1]
                  and not reached and not (base / "plain").exists(), got)

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
            def run(name, pins, fetcher=None):
                temp = base / name
                os.makedirs(temp)
                github_path = temp / "github_path"
                github_path.write_text("/already/listed\n", encoding="utf-8")
                got = _outcome(lambda: provision(pins, fetcher or _fixture_fetcher(fixtures), temp, github_path,
                                                 log=lambda line: None))
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
                                             temp / "github_path", log=lambda line: None))
            check("provision/existing-install-refuses", got[0] == "not-completed" and "already exists" in got[1]
                  and set(os.listdir(temp)) == {INSTALL_NAME, "github_path"} and not os.listdir(temp / INSTALL_NAME)
                  and (temp / "github_path").read_text(encoding="utf-8") == "", got)

            # the CI entry: an unusable RUNNER_TEMP or GITHUB_PATH stops it before any fetch
            fetched = []

            def recording(url, dest):
                fetched.append(url)
                raise NotCompleted("recorded")

            listed = base / "listed"
            listed.write_text("", encoding="utf-8")
            got = _outcome(lambda: build({"GITHUB_PATH": str(listed)}, recording))
            check("env/missing-runner-temp-exits-2", got == ("ok", 2) and not fetched, got)
            got = _outcome(lambda: build({"RUNNER_TEMP": str(base), "GITHUB_PATH": "github_path"}, recording))
            check("env/relative-github-path-exits-2", got == ("ok", 2) and not fetched, got)

            # an OSError the pipeline does not expect ends the CI entry with exit 2 and nothing installed
            def failing(url, dest):
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
          "nothing installed; a bare run is the self-test even with RUNNER_TEMP and GITHUB_PATH set, only --build "
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
