#!/usr/bin/env python3
"""Registry-driven fail-close gate: prove every gen_*.py REJECTS a corrupted committed target (GS-2).

The gensrc-drift gate proves a generator reproduces its target byte-for-byte from clean sources. It does
NOT prove the generator actually CONSULTS that target when checking: a generator whose `--check` ignores
its output (deriving a verdict from sources alone, or short-circuiting to exit 0) passes drift while
silently failing to guard the shipped artefact. That is the F-154 class. This gate closes it: for every
entry in the .aiqt/gensrc.json registry it corrupts a throwaway copy of the target and asserts the
generator's `--check` refuses to pass clean. The uniform invariant, the true anti-F-154 property, is a
single assertion for every corruption shape: a corrupted target yields a NON-ZERO `--check`. An exit 0 on
a corrupt target is the violation; the exact non-zero code (drift, decode-error, crash) is not graded.

Enumeration reuses gen_gensrc's OWN validated in-memory loader (discover_declarations + collect_entries),
imported, never a second parser of .aiqt/gensrc.json, so this gate inherits every fail-closed enumeration
guarantee already proven in gen_gensrc --self-test (an unreadable tools/ raises, a bad declaration raises,
zero generators raises) instead of forking a parser that could drift.

Corruption strategy, kind-aware, all under the one "corrupt -> non-zero" assertion:
  - a "file" target, and EVERY regular-file member of a "tree" target, whose pristine bytes decode as
    UTF-8, is probed with two VALID-BUT-DIFFERENT UTF-8 shapes and one INVALID UTF-8 shape: the pristine
    bytes plus a distinctive appended marker line (a length-CHANGING content change); the pristine bytes
    with one interior byte swapped for a different valid one (a SAME-LENGTH content change, present only
    when an interior ASCII byte exists to swap); and an invalid-UTF-8 sequence. The valid-but-different
    probes are what prove CONTENT guarding rather than mere decoding: a generator that decodes its target
    but derives its verdict from sources would pass invalid-UTF-8 corruption yet still be caught here. The
    same-length probe further defeats a length-only pseudo-guard, which every length-changing probe would
    pass. Both valid-but-different probes also remove the false-violation a tolerant reader
    (errors="replace") would have drawn from an invalid-UTF-8-only strategy: such a reader legitimately
    still detects the different content and passes.
  - a binary target (pristine that is not UTF-8) is probed once with a one-byte tamper.
  - a "block" target is probed once with INVALID UTF-8 only. The generated REGION inside a block file is
    unknown to this gate, so a content change might land OUTSIDE it (a correct clean pass this gate must
    not assert on); invalid UTF-8 corrupts the WHOLE file and proves only the generator's SENSITIVITY to
    the invalid-byte replacement (that it reads the file and rejects the corruption), not that it decodes
    it or guards the region's content. Block region-content-guarding is therefore NOT tested here; it is
    covered by that generator's own drift `--check` step. See the disclosed residuals.

Isolation. The baseline and EACH corruption probe run in a SEPARATE FRESH, WHOLLY INDEPENDENT sandbox:
one pristine TEMPLATE copytree is made once, and every baseline/probe copies that template into its own
disposable tempdir created by a FRESH mkdtemp (not a child of one shared parent dir), identical to the
template except for the single corrupted target. Each call's subprocess also gets its OWN unique HOME and
TMPDIR pointing INSIDE that call's disposable tempdir (so a generator's writes under $HOME or $TMPDIR land
in the per-call throwaway), and repo_root().parent is that unique disposable tempdir. No generator-written
state (a marker dropped under the sandbox, $HOME, $TMPDIR, or repo_root().parent on one call) can carry
into another call, so a stateful generator cannot distinguish a baseline call from a probe call without
consulting the target (the F-154-via-state trick). Residual (NOT categorical): per-call unique
HOME/TMPDIR do not stop state shared through a filesystem location OUTSIDE the per-call sandbox that a
generator can still reach: a COMMON ANCESTOR of the per-call tempdirs (the system temp root), the parent
of its own $HOME/$TMPDIR, or any fixed path. Fully isolating a deliberately-adversarial generator would
require OS-level confinement (a namespace or container), which this gate does not provide. It runs the
project's OWN trusted in-repo generators, so that purely-adversarial case is out of scope and left to
code review (see the disclosed residuals).

Safety. All corruption happens in disposable copytree sandboxes; the REAL tree is STRICTLY read-only and
is never corrupted in place, so a SIGKILL, a full disk, or a power loss mid-run can never leave a corrupt
committed artefact (the worst case is an orphaned tempdir, never a corrupt real file), and a SIGKILL is
bounded to the disposable sandbox. No target is ever restored in place (each probe discards its whole
throwaway), so there is no restore-write to be redirected. Immediately before EVERY read or write of a
target the gate re-validates, with os.lstat (following no symlink), that the path is a regular file whose
os.path.realpath is inside the sandbox, and opens it with O_NOFOLLOW; and immediately AFTER each probe it
re-validates that the target is still a contained regular file, so a generator that swaps its target for
a symlink escaping the sandbox during `--check` is a containment violation (cannot-evaluate), never a
real-tree write. Symlinks and special nodes are dropped when copying. Each generator runs as a subprocess
with shell=False, the interpreter pinned to sys.executable, cwd=sandbox, a sanitized environment (ambient
GIT_*/PYTHONPATH and the like stripped, with HOME and TMPDIR REPLACED by per-call unique disposable dirs)
carrying PYTHONDONTWRITEBYTECODE=1, a per-probe timeout clamped to the time remaining before the
total-runtime backstop, and that bounded total-runtime backstop. `sys.dont_write_bytecode` is set at
import time so importing the gen_gensrc loader writes no .pyc into the real tools/ tree. A `sandbox/.git`
marker anchors each generator's repo_root() on the sandbox so a probe can never walk up into the real
repository. A manifest of the tracked/generated real tree (excluding .git, __pycache__, .venv,
node_modules), capturing per regular file its sha256, permission bits, and type (and every symlink/special
node's type), is compared before and after the sweep; ANY change (content, mode, type, a new or removed
path, a path becoming a symlink), a sandbox/copy failure, or a cleanup failure is exit 2.

Exit convention (matches the repo's gates):
  0  every entry's generator returned non-zero on every corrupted target.
  1  a generator VIOLATED the contract: it returned exit 0 on a corrupt target (the F-154 regression).
  2  cannot-evaluate: the registry is unreadable/malformed; a baseline `--check` is not clean; a declared
     target is absent (a file/block that is not a regular file, or a tree that is empty or untraversable);
     a `regenerate` command is not of the grammar `python3 tools/gen_<stem>.py`; a probe times out, cannot
     be launched, or leaves the target uncontained (a symlink/containment violation); the total-runtime
     backstop is exceeded; the sandbox setup, copy, or cleanup fails; or the real tree changed during the
     run.
When both a confirmed violation and a cannot-evaluate condition are present, the exit code is 1 (the
confirmed live bug is the higher-signal outcome and we DID evaluate it); every condition is still printed.
A real-tree integrity breach or a cleanup failure overrides both to exit 2, because a run that may have
touched the real tree, or could not fully dispose of its sandboxes, cannot be trusted to certify anything.

DISCLOSED RESIDUALS. The gate proves DETECTION (the generator refuses a corrupt target via --check), not
REPAIR (that regen emits correct bytes; the drift gate owns that). For a BLOCK target it corrupts only
with invalid UTF-8 (whole-file), proving the generator is SENSITIVE to the invalid-byte replacement (it
reads and rejects the file) but NOT that it decodes it or guards the generated region's CONTENT: a
valid-but-different change might land outside the region and correctly pass, so region-content-guarding
stays on that generator's own drift step and is not asserted here. The content-guarding proof for a text
or tree-member target is bounded to the SPECIFIC probed changes (an appended marker line and, where an
interior ASCII byte exists, one same-length byte swap): passing them shows the generator consults its
target's content, not that it guards against EVERY possible same-length change beyond the one probed. Only
the `python3 tools/gen_<stem>.py` grammar is recognized; a wrapper or non-Python generator fails coverage
(exit 2) until the grammar is extended. Encoding is OBSERVED (a UTF-8 decode of the pristine bytes), so an
ASCII-only binary would be classified as text (none exists today). The temp copy is corruption isolation,
NOT an OS security sandbox: a malicious generator could still reach absolute paths or the network (the
before/after manifest and the post-probe containment re-validation detect a real-tree write or an escaping
symlink after the fact, they do not prevent an arbitrary side effect). Per-call unique HOME/TMPDIR and a
fresh independent tempdir per call deny cross-call state via $HOME, $TMPDIR, or repo_root().parent, but
they cannot deny it categorically: a generator can still share state through a COMMON ANCESTOR of the
per-call tempdirs, the parent of its own $HOME/$TMPDIR, or any fixed path outside its sandbox. Full
isolation would require OS-level confinement (a namespace or container); that purely-adversarial case is
out of scope (the gate runs the project's own trusted generators) and is left to code review. Sources are not swept (a separate invariant). The gate tests a copy of the working
tree, so it captures local uncommitted edits; on CI, HEAD equals the tree.

  check_gensrc_failclose.py             sweep the real registry (default)
  check_gensrc_failclose.py --self-test build synthetic mini-repos and assert this gate's own invariants
"""
import sys

# Set BEFORE importing gen_gensrc (below): importing the loader must write no .pyc into the real tools/
# tree. The subprocess env also carries PYTHONDONTWRITEBYTECODE=1 for the generators it runs.
sys.dont_write_bytecode = True

import hashlib  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shlex  # noqa: E402
import shutil  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402
import gen_gensrc  # noqa: E402  reuse its validated, fail-closed registry loader (do not re-parse the JSON)

# Directory names never copied into a sandbox and never recorded in the real-tree manifest: version
# control and derived caches, which are not registry targets and would only add noise and cost.
IGNORE_DIRS = {".git", "__pycache__", ".venv", "node_modules"}
# An invalid-UTF-8 byte sequence: b"\xff" is never a valid UTF-8 start byte, so decoding it raises
# UnicodeDecodeError. The portable "unreadable text" corruption (see the strategy above).
INVALID_UTF8 = b"\xff\xfe\x00 not-utf8 \x80\x81"
# The valid-but-different corruption suffix: valid UTF-8 (plain ASCII) that changes the bytes without
# breaking a decode, so a content-guarding --check must report drift while a tolerant reader still passes.
VALID_MARKER = b"\n# aiqt-failclose: valid-but-different content marker\n"
# The regenerate grammar the gate recognizes: exactly `python3 tools/gen_<stem>.py`, validated with shlex.
REGEN_RE = re.compile(r"^tools/gen_([A-Za-z0-9_]+)\.py$")
PROBE_TIMEOUT = 120  # seconds per subprocess --check; a hung generator is cannot-evaluate, not a hang
TOTAL_TIMEOUT = 1800  # seconds backstop for the whole sweep; exceeding it is cannot-evaluate, never a hang
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)  # Linux/CI have it; degrade safely where the platform lacks it
_now = time.monotonic  # module-level indirection so the self-test can inject a monotonic clock (deadline)
_mkdtemp = tempfile.mkdtemp  # module-level indirection so the self-test can inject a sandbox-setup failure


class _ContainmentError(Exception):
    """A target is not a contained regular file (a symlink, a special node, or a path whose realpath
    escapes the sandbox). Mapped to cannot-evaluate (exit 2): a run that read or wrote through it could
    have touched the real tree, so its result cannot be trusted."""


class _DeadlineExceeded(Exception):
    """The total-runtime backstop elapsed mid-sweep. Mapped to cannot-evaluate (exit 2)."""


def _is_utf8(data):
    """True if data decodes as UTF-8 (the observed-encoding test): text targets decode, binary do not."""
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _tamper(data):
    """Return a byte string guaranteed to differ from data: flip the first byte, or (empty file) add one.
    A different-bytes tamper is what a byte-reconciled generator must report as non-zero on a binary
    target."""
    if not data:
        return b"\x00"
    out = bytearray(data)
    out[0] ^= 0xFF
    return bytes(out)


def _same_length_change(data):
    """Return valid UTF-8 bytes the SAME LENGTH as `data` with one INTERIOR byte swapped for a different
    valid one, or None when no interior ASCII byte exists to swap. Swapping an ASCII byte (< 0x80, always a
    standalone code point in UTF-8) for another ASCII byte preserves both the length and UTF-8 validity, so
    the result is a valid-but-different content change that a length-only pseudo-guard would pass but a
    content-guarding --check must reject."""
    out = bytearray(data)
    for i in range(1, len(out) - 1):  # interior positions only (not the first or last byte)
        if out[i] < 0x80:
            out[i] = 0x41 if out[i] != 0x41 else 0x42  # 'A', or 'B' where the byte already is 'A'
            return bytes(out)
    return None  # no interior ASCII byte to swap (too short, or all-multibyte interior)


def _within(child, parent):
    """True if the absolute path `child` is `parent` itself or lies beneath it, compared on whole path
    components (so '/a/bc' is not treated as beneath '/a/b'). Both are expected already realpath-resolved."""
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:  # different drives / one relative: never contained
        return False


def _validate_contained_regular(path, sandbox_real, where):
    """Re-validate, following no symlink, that `path` is a regular file whose realpath is inside
    `sandbox_real`. Raises _ContainmentError (naming `where`) on a symlink, a special node, or an escape;
    lets OSError (an absent path) propagate to the caller's cannot-evaluate handling."""
    st = os.lstat(path)  # no-follow; OSError (absent) -> caller maps to cannot-evaluate
    if not stat.S_ISREG(st.st_mode):
        raise _ContainmentError("{}: target is not a regular file (mode {:o})".format(where, st.st_mode))
    real = os.path.realpath(path)
    if not _within(real, sandbox_real):
        raise _ContainmentError("{}: target realpath {!r} escapes the sandbox {!r}"
                                .format(where, real, sandbox_real))


def _read_bytes_safe(path, sandbox_real, where):
    """Read a target's bytes after re-validating it is a contained regular file, opening O_NOFOLLOW so a
    final-component symlink cannot be followed."""
    _validate_contained_regular(path, sandbox_real, where)
    fd = os.open(path, os.O_RDONLY | _O_NOFOLLOW)
    try:
        chunks = []
        while True:
            block = os.read(fd, 1 << 20)
            if not block:
                break
            chunks.append(block)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _write_bytes_safe(path, data, sandbox_real, where):
    """Overwrite a target with `data` after re-validating it is a contained regular file, opening
    O_WRONLY|O_TRUNC|O_NOFOLLOW so a swapped-in symlink cannot redirect the write out of the sandbox."""
    _validate_contained_regular(path, sandbox_real, where)
    fd = os.open(path, os.O_WRONLY | os.O_TRUNC | _O_NOFOLLOW)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def _sanitized_env(overrides=None):
    """A minimal environment for a probe subprocess: PYTHONDONTWRITEBYTECODE=1 plus a small allowlist of
    safe variables carried from the parent. Ambient GIT_*, PYTHONPATH, and everything else are stripped,
    so an inherited environment cannot redirect a generator to a decoy repo or inject configuration. HOME
    and TMPDIR are DELIBERATELY NOT carried from the parent: a probe subprocess receives per-call unique
    HOME/TMPDIR through `overrides` (pointing inside its own disposable tempdir), so a generator's writes
    under $HOME or $TMPDIR land in the per-call throwaway and cannot carry state into another call."""
    env = {"PYTHONDONTWRITEBYTECODE": "1"}
    for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "SYSTEMROOT", "PATHEXT"):
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    if "PATH" not in env:
        env["PATH"] = os.defpath
    if overrides:
        env.update(overrides)
    return env


def _materialize_git(template, real_root):
    """D6: materialize a REAL minimal git repository in the sandbox TEMPLATE so a git-reading generator
    (for example gen_manifest.py, whose `git ls-files` enumerates the tracked surface) runs its baseline
    --check under the sweep. Identity is pinned per-call with -c (never user config), the template dir is
    forced empty so no user hooks load, and the environment is sanitized (ambient GIT_* stripped,
    global/system config sent to os.devnull).

    The template's PATH SET is EXACTLY real_root's tracked set, so a git-reading generator sees the same
    tracked surface the release-side --check saw (an on-disk but untracked build output stays untracked in
    the sandbox too). Two hardenings (F-234):
    (a) The real_root probe runs `-c core.fsmonitor=false --no-optional-locks` (both top-level options
        PRECEDE the ls-files subcommand, so the -c override beats real_root's own repo-LOCAL config): a
        repo-local core.fsmonitor is an arbitrary program git would otherwise EXECUTE during ls-files, and
        that program could read or write OUTSIDE the sandbox. The override refuses to run it.
    (b) A genuine probe FAILURE (a nonzero exit or an undecodable result) RAISES OSError, which the caller
        maps to cannot-evaluate (exit 2). There is no `git add -A` fallback: a fallback would certify a
        bogus baseline from a repository we could not enumerate. Every synthetic self-test fixture is a
        REAL git repo, so real_root enumeration always succeeds or is cannot-evaluate. After the add and
        commit the materialized index MUST equal real_root's tracked set: a tracked path missing from the
        template (a symlink copytree dropped, a tracked-but-deleted file) means the sandbox does not mirror
        the real tracked surface, so a git-reading baseline could not be trusted and the run raises."""
    env = _sanitized_env({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})

    def git(cwd, *args):
        return subprocess.run(
            ["git", "-C", str(cwd), "-c", "user.name=aiqt-failclose",
             "-c", "user.email=failclose@invalid", "-c", "commit.gpgsign=false",
             "-c", "init.defaultBranch=main", *args],
            capture_output=True, env=env, timeout=PROBE_TIMEOUT, shell=False)

    if git(template, "init", "-q", "--template=").returncode != 0:
        raise OSError("cannot git-init the failclose sandbox template")
    # -c core.fsmonitor=false and --no-optional-locks (top-level, before the subcommand) override any
    # repo-local fsmonitor hook program in real_root and skip the optional index-lock refresh (F-234a).
    probe = subprocess.run(["git", "-C", str(real_root), "-c", "core.fsmonitor=false",
                            "--no-optional-locks", "ls-files", "-z"],
                           capture_output=True, env=env, timeout=PROBE_TIMEOUT, shell=False)
    if probe.returncode != 0:
        raise OSError("cannot enumerate real_root's tracked set (git ls-files exit {}); fail-closed"
                      .format(probe.returncode))
    try:
        tracked = [p for p in probe.stdout.decode("utf-8").split("\x00") if p]
    except UnicodeDecodeError as exc:
        raise OSError("cannot decode real_root's tracked set ({}); fail-closed".format(exc))
    for rel in tracked:
        # A tracked path is always a clean repo-relative POSIX path; reject anything else rather than add it.
        if os.path.isabs(rel) or os.pardir in rel.split("/"):
            raise OSError("real_root reported an unexpected tracked path {!r}; fail-closed".format(rel))
    present = [p for p in tracked if (template / p).is_file()]
    if present and git(template, "add", "--", *present).returncode != 0:
        raise OSError("cannot git-add the tracked set into the failclose template")
    if git(template, "commit", "-q", "-m", "failclose template", "--no-verify").returncode != 0:
        raise OSError("cannot commit the failclose sandbox template")
    # The materialized index MUST equal real_root's tracked set (F-234b): any missing member is fail-closed.
    readback = git(template, "ls-files", "-z")
    if readback.returncode != 0:
        raise OSError("cannot read back the materialized template index (git ls-files exit {})"
                      .format(readback.returncode))
    try:
        materialized = {p for p in readback.stdout.decode("utf-8").split("\x00") if p}
    except UnicodeDecodeError as exc:
        raise OSError("cannot decode the materialized template index ({}); fail-closed".format(exc))
    if materialized != set(tracked):
        missing = sorted(set(tracked) - materialized)
        raise OSError("the materialized template index does not equal real_root's tracked set "
                      "({} member(s) missing, e.g. {}); fail-closed".format(len(missing), missing[:3]))
    # Pack the loose objects into a single packfile so each per-probe copytree of the template copies a
    # handful of .git files instead of one loose object per tracked blob (a large per-probe speed-up). A
    # gc failure is non-fatal: the repo still works with loose objects, only slower.
    git(template, "gc", "--quiet")


def _run_check(script_path, cwd, timeout=PROBE_TIMEOUT, env_overrides=None):
    """Run `python3 <script_path> --check` as a subprocess and return its exit code. shell=False, the
    interpreter pinned to sys.executable, a sanitized env carrying the per-call HOME/TMPDIR overrides, cwd
    anchored on the sandbox, a bounded timeout. Module-level so the self-test can substitute it. Raises
    subprocess.TimeoutExpired or OSError on a hung or unlaunchable probe; the caller maps that to
    cannot-evaluate."""
    proc = subprocess.run([sys.executable, str(script_path), "--check"], cwd=str(cwd),
                          env=_sanitized_env(env_overrides), capture_output=True, timeout=timeout,
                          shell=False)
    return proc.returncode


def _copy_ignore(dir_path, names):
    """copytree ignore callable: drop the ignore-dirs, and drop any symlink or special (non-file,
    non-dir) node so the sandbox carries only real files and directories."""
    ignored = set()
    for name in names:
        if name in IGNORE_DIRS:
            ignored.add(name)
            continue
        try:
            st = os.lstat(os.path.join(dir_path, name))
        except OSError:
            ignored.add(name)  # cannot stat it -> do not copy it
            continue
        if stat.S_ISLNK(st.st_mode) or not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
            ignored.add(name)
    return ignored


def _raise_oserror(exc):
    """os.walk onerror hook: re-raise so an unlistable directory fails closed instead of silently
    yielding nothing."""
    raise exc


def _tree_manifest(root):
    """Map every path under root (excluding IGNORE_DIRS, following no symlink) to a metadata tuple, so the
    REAL tree can be proven unchanged before and after the sweep. A regular file maps to
    ('file', permission-bits, sha256); a symlink to ('symlink', permission-bits, link-target); any other
    special node (FIFO, socket, device) to ('special', permission-bits, format-bits) WITHOUT opening it
    (a FIFO must never block a read). Any change to content, mode, type, or the set of paths makes two
    manifests unequal. Raises OSError on an unreadable input (fail-closed): a manifest that could not be
    built cannot certify the tree unchanged."""
    manifest = {}
    root = str(root)
    for dirpath, dirnames, filenames in os.walk(root, onerror=_raise_oserror):
        kept = []
        for name in dirnames:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):  # a symlinked dir: record it, do not descend it
                st = os.lstat(full)
                manifest[os.path.relpath(full, root)] = ("symlink", stat.S_IMODE(st.st_mode),
                                                         os.readlink(full))
                continue
            if name in IGNORE_DIRS:
                continue  # not descended, not recorded
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            if name in IGNORE_DIRS:
                continue  # a linked-worktree '.git' is a FILE, not a dir: exclude it, as the dirnames loop
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            st = os.lstat(full)  # no-follow
            mode = stat.S_IMODE(st.st_mode)
            if stat.S_ISLNK(st.st_mode):
                manifest[rel] = ("symlink", mode, os.readlink(full))
            elif stat.S_ISREG(st.st_mode):
                with open(full, "rb") as handle:  # lstat-gated to a regular file, so this cannot block
                    manifest[rel] = ("file", mode, hashlib.sha256(handle.read()).hexdigest())
            else:
                manifest[rel] = ("special", mode, stat.S_IFMT(st.st_mode))  # do NOT open a FIFO/socket/dev
    return manifest


def _validate_regenerate(regenerate):
    """Return the generator stem if `regenerate` is exactly `python3 tools/gen_<stem>.py`, else None. Uses
    shlex.split so the command is validated to shape (tool-argument-validation), never assembled or run as
    a shell string. A malformed quote, a wrong token count, a non-python3 launcher, or a path off the
    gen_<stem>.py shape all return None, which the caller treats as cannot-evaluate (exit 2)."""
    try:
        parts = shlex.split(regenerate)
    except ValueError:
        return None
    if len(parts) != 2 or parts[0] != "python3":
        return None
    match = REGEN_RE.match(parts[1])
    return match.group(1) if match else None


def _tree_members(tree_path):
    """Return the sorted regular-file members under a tree target (following no symlink), or raise OSError
    on an untraversable subtree (fail-closed)."""
    members = []
    for dirpath, dirnames, filenames in os.walk(tree_path, onerror=_raise_oserror):
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        for name in sorted(filenames):
            full = Path(dirpath) / name
            if not full.is_symlink() and full.is_file():
                members.append(full)
    members.sort()
    return members


def _variants(is_block, pristine):
    """The (shape-label, corrupt-bytes) probes for one target, all under the 'corrupt -> non-zero'
    assertion. A block target is invalid-UTF-8 only (region content is not asserted; see the residuals);
    a UTF-8 text target is probed with a length-changing valid-but-different shape, a same-length
    valid-but-different shape (where an interior ASCII byte exists to swap), AND invalid UTF-8; a binary
    target is a one-byte tamper."""
    if is_block:
        return [("invalid UTF-8", INVALID_UTF8)]
    if _is_utf8(pristine):
        variants = [("valid-but-different UTF-8 (an appended marker line)", pristine + VALID_MARKER)]
        same_length = _same_length_change(pristine)
        if same_length is not None and same_length != pristine:
            variants.append(("valid-but-different UTF-8 (a same-length interior byte swap)", same_length))
        variants.append(("invalid UTF-8", INVALID_UTF8))
        return variants
    return [("a one-byte binary tamper", _tamper(pristine))]


def _new_sandbox(template):
    """Copy the pristine TEMPLATE to a fresh disposable throwaway in its OWN unique tempdir (a fresh
    mkdtemp, NOT a child of one shared parent) and return (holder_dir, sandbox_dir, env_overrides). The
    caller removes holder_dir when done. Inside the same disposable holder it also creates a unique HOME
    and TMPDIR, returned as `env_overrides` for the probe subprocess, so every call's $HOME, $TMPDIR,
    sandbox, and repo_root().parent are wholly its own and no path is shared between two calls. The
    template already carries the sandbox/.git marker and has had symlinks/special nodes and ignore-dirs
    dropped, so the copy is a plain copytree. If any step after the mkdtemp (the copytree or the home/tmp
    creation) fails, the partial holder is removed here before the exception propagates, so a failed
    per-call setup leaves no throwaway on disk; the caller's sweep still maps the failure to
    cannot-evaluate (exit 2)."""
    holder = Path(tempfile.mkdtemp(prefix="aiqt-gensrc-failclose-call-"))
    try:
        sandbox = holder / "repo"
        shutil.copytree(template, sandbox, symlinks=False)
        home = holder / "home"
        tmp = holder / "tmp"
        home.mkdir()
        tmp.mkdir()
    except BaseException:  # any setup failure after mkdtemp must not leak the partial holder
        shutil.rmtree(holder)  # no ignore_errors: a cleanup failure surfaces rather than hides
        raise
    return holder, sandbox, {"HOME": str(home), "TMPDIR": str(tmp)}


def _cleanup(holder, cleanup_errors):
    """Remove a throwaway holder dir; a cleanup failure is recorded (never hidden with ignore_errors) and
    overrides the sweep to exit 2, because a sandbox that could not be disposed of leaves the run's
    footprint unaccounted for."""
    try:
        shutil.rmtree(holder)
    except OSError as exc:
        cleanup_errors.append("throwaway cleanup failed for {} ({})".format(holder, exc))


def _remaining_timeout(deadline):
    """Seconds left until the total-runtime deadline, clamped to at most PROBE_TIMEOUT. Raises
    _DeadlineExceeded when the backstop has already elapsed, so no subprocess is launched with a
    non-positive timeout and an overrun becomes cannot-evaluate rather than a fail-open pass."""
    remaining = deadline - _now()
    if remaining <= 0:
        raise _DeadlineExceeded()
    return min(PROBE_TIMEOUT, remaining)


def _run_baseline(template, script_rel, deadline, cleanup_errors):
    """Run a generator's `--check` on a FRESH pristine sandbox (no corruption) and return its exit code.
    Its own separate sandbox, so no state leaks into or out of a probe. The subprocess timeout is clamped
    to the time remaining before the total-runtime deadline IMMEDIATELY before the launch (after the
    sandbox copy), so the copytree cannot consume the budget while the subprocess still launches with a
    near-full timeout; an already-elapsed deadline at that point raises _DeadlineExceeded rather than
    launching. Raises the narrow probe errors and _DeadlineExceeded; the caller maps those to
    cannot-evaluate."""
    holder, sandbox, env_overrides = _new_sandbox(template)
    try:
        timeout = _remaining_timeout(deadline)  # clamp AFTER the copy, immediately before the launch
        return _run_check(sandbox / script_rel, sandbox, timeout=timeout, env_overrides=env_overrides)
    finally:
        _cleanup(holder, cleanup_errors)


def _run_probe(template, script_rel, rel_path, corrupt_bytes, deadline, cleanup_errors):
    """Copy the template to a FRESH throwaway, corrupt `rel_path` in it, run the generator's `--check`,
    and return the exit code. The subprocess timeout is clamped to the time remaining before the
    total-runtime deadline IMMEDIATELY before the launch (after the sandbox copy and the corruption write),
    so neither the copytree nor the write can consume the budget while the subprocess still launches with a
    near-full timeout; an already-elapsed deadline at that point raises _DeadlineExceeded rather than
    launching. Re-validates the target is a contained regular file before the write and again after the
    probe (a generator that swapped it for an escaping symlink raises _ContainmentError). Raises
    _ContainmentError, subprocess.TimeoutExpired/OSError, or _DeadlineExceeded; the caller maps all to
    cannot-evaluate."""
    holder, sandbox, env_overrides = _new_sandbox(template)
    try:
        sandbox_real = os.path.realpath(str(sandbox))
        tpath = str(sandbox / rel_path)
        where = "{} (in the sandbox)".format(rel_path)
        _write_bytes_safe(tpath, corrupt_bytes, sandbox_real, where)  # re-validate + O_NOFOLLOW write
        timeout = _remaining_timeout(deadline)  # clamp AFTER the copy+write, immediately before the launch
        rc = _run_check(sandbox / script_rel, sandbox, timeout=timeout, env_overrides=env_overrides)
        _validate_contained_regular(tpath, sandbox_real, where)  # post-probe symlink-swap detection
        return rc
    finally:
        _cleanup(holder, cleanup_errors)


def _process_entry(entry, template, baselines, deadline, violations, cannot, cleanup_errors):
    """Probe one registry entry's target(s) and record any finding. Appends to `violations` (a generator
    returned exit 0 on a corrupt target) or `cannot` (a fail-closed cannot-evaluate condition); lets
    _DeadlineExceeded propagate so the sweep can stop. The real tree is never touched: every probe runs in
    its own disposable throwaway copied from the pristine template."""
    target_rel = entry["target"]
    kind = entry["kind"]
    body = target_rel[:-1] if target_rel.endswith("/") else target_rel  # strip the tree marker
    is_block = kind == "block"

    stem = _validate_regenerate(entry["regenerate"])
    if stem is None:
        cannot.append("{}: regenerate {!r} is not of the grammar 'python3 tools/gen_<stem>.py'"
                      .format(target_rel, entry["regenerate"]))
        return
    script_rel = "tools/gen_{}.py".format(stem)
    if not (template / script_rel).is_file():
        cannot.append("{}: generator gen_{}.py is absent in the sandbox".format(target_rel, stem))
        return

    template_real = os.path.realpath(str(template))
    tbody = template / body
    # Target-existence probe on the pristine template (a shippable artefact that is not there cannot be
    # guarded; an empty declared tree is exactly the presence-test-then-skip shape the fail-closed rule
    # forbids). Collect the repo-relative path(s) to corrupt: every regular-file member for a tree.
    if kind == "tree":
        if tbody.is_symlink() or not tbody.is_dir():
            cannot.append("{}: declared tree target is absent or not a directory".format(target_rel))
            return
        try:
            members = _tree_members(tbody)
        except OSError as exc:
            cannot.append("{}: declared tree target is untraversable ({})".format(target_rel, exc))
            return
        if not members:
            cannot.append("{}: declared tree target is empty (no members to guard)".format(target_rel))
            return
        rel_paths = [os.path.relpath(str(m), str(template)) for m in members]
    else:
        if tbody.is_symlink() or not tbody.is_file():
            cannot.append("{}: declared {} target is absent or not a regular file"
                          .format(target_rel, kind))
            return
        rel_paths = [body]

    # Per-generator baseline (its own fresh sandbox): the pristine tree must pass --check, else a
    # corruption signal is unattributable (a broken generator, pre-existing drift, or no --check support).
    if stem not in baselines:
        try:
            baselines[stem] = _run_baseline(template, script_rel, deadline, cleanup_errors)
        except (subprocess.TimeoutExpired, OSError) as exc:
            baselines[stem] = "error: {}".format(exc)
    baseline = baselines[stem]
    if baseline != 0:
        cannot.append("{}: baseline 'gen_{} --check' on the pristine sandbox returned {!r}, not a clean 0 "
                      "(a corruption signal would be unattributable)".format(target_rel, stem, baseline))
        return

    for rel_path in rel_paths:
        try:
            pristine = _read_bytes_safe(str(template / rel_path), template_real,
                                        "{} (in the template)".format(rel_path))
        except (OSError, _ContainmentError) as exc:
            cannot.append("{}: cannot read pristine bytes of {} ({})".format(target_rel, rel_path, exc))
            continue
        for shape, corrupt in _variants(is_block, pristine):
            try:
                actual = _run_probe(template, script_rel, rel_path, corrupt, deadline,
                                    cleanup_errors)
            except _ContainmentError as exc:
                cannot.append("{}: probe of {} corrupted with {} left the target uncontained ({}); "
                              "fail-closed".format(target_rel, rel_path, shape, exc))
                continue
            except (subprocess.TimeoutExpired, OSError) as exc:
                cannot.append("{}: probe 'gen_{} --check' on {} corrupted with {} failed to complete ({})"
                              .format(target_rel, stem, rel_path, shape, exc))
                continue
            if actual == 0:
                violations.append("{}: gen_{} --check returned exit 0 on {} corrupted with {} (F-154: the "
                                  "generator does not consult its target)"
                                  .format(target_rel, stem, rel_path, shape))
            # any non-zero actual = the corrupt target was rejected = a pass; nothing recorded


def sweep(real_root):
    """Sweep the real registry: for every entry, corrupt a fresh sandbox copy of the target and assert the
    generator's --check rejects it with a non-zero exit. Returns the gate exit code (0/1/2). The real tree
    is never written."""
    real_root = Path(real_root).resolve()
    try:
        declarations = gen_gensrc.discover_declarations(real_root / "tools")
        entries = gen_gensrc.collect_entries(declarations, real_root)
    except (ValueError, OSError) as exc:
        print("cannot-evaluate: the gensrc registry is unreadable or malformed ({}); fail-closed"
              .format(exc), file=sys.stderr)
        return 2
    try:
        before = _tree_manifest(real_root)
    except OSError as exc:
        print("cannot-evaluate: cannot read the real tree to snapshot it ({}); fail-closed".format(exc),
              file=sys.stderr)
        return 2

    violations, cannot, cleanup_errors = [], [], []
    unexpected = None
    work = None  # created inside the try so a mkdtemp/copytree setup failure is cannot-evaluate, not a raise
    deadline = _now() + TOTAL_TIMEOUT
    try:
        try:
            work = Path(_mkdtemp(prefix="aiqt-gensrc-failclose-"))
            template = work / "template"
            shutil.copytree(real_root, template, symlinks=False, ignore=_copy_ignore)
            _materialize_git(template, real_root)  # D6: a real repo so git-reading generators baseline
        except (OSError, shutil.Error) as exc:
            cannot.append("template sandbox setup failed ({})".format(exc))
        else:
            baselines = {}
            try:
                for entry in entries:
                    _process_entry(entry, template, baselines, deadline, violations, cannot,
                                   cleanup_errors)
                # Check the deadline AFTER the final probe too: a probe that finished exactly at the
                # backstop must not let the sweep return a fail-open 0 when the total runtime overran.
                if _now() > deadline:
                    raise _DeadlineExceeded()
            except _DeadlineExceeded:
                cannot.append("total-runtime backstop of {}s exceeded; the sweep did not finish "
                              "(fail-closed)".format(TOTAL_TIMEOUT))
    except Exception as exc:  # noqa: BLE001  an unexpected error is fail-closed, never a silent pass
        unexpected = exc
    finally:
        cleanup_error = None
        if work is not None:
            try:
                shutil.rmtree(work)  # no ignore_errors: a cleanup failure must surface, not hide
            except OSError as exc:
                cleanup_error = exc
        try:
            after = _tree_manifest(real_root)
        except OSError as exc:
            after = None
            integrity_error = exc
        else:
            integrity_error = None

    if unexpected is not None:
        print("cannot-evaluate: unexpected error during the sweep ({}); fail-closed".format(unexpected),
              file=sys.stderr)
    if integrity_error is not None or after is None:
        print("cannot-evaluate: cannot re-read the real tree after the sweep ({}); fail-closed"
              .format(integrity_error), file=sys.stderr)
        return 2
    if after != before:
        print("cannot-evaluate: the real tree CHANGED during the sweep; fail-closed (this must never "
              "happen: the gate corrupts only disposable copies)", file=sys.stderr)
        return 2
    if cleanup_error is not None:
        print("cannot-evaluate: sandbox work-dir cleanup failed ({}); fail-closed".format(cleanup_error),
              file=sys.stderr)
        return 2
    if cleanup_errors:
        print("cannot-evaluate: {} throwaway sandbox cleanup(s) failed; fail-closed"
              .format(len(cleanup_errors)), file=sys.stderr)
        for line in cleanup_errors:
            print("  " + line, file=sys.stderr)
        return 2

    print("swept {} registry entries against their generators".format(len(entries)))
    if violations:
        print("FAIL: {} generator(s) violated the fail-close contract on a corrupted target"
              .format(len(violations)))
        for line in violations:
            print("  " + line)
        for line in cannot:
            print("  (also could not evaluate) " + line)
        return 1
    if unexpected is not None or cannot:
        print("CANNOT-EVALUATE: {} condition(s) could not be soundly evaluated".format(len(cannot)))
        for line in cannot:
            print("  " + line)
        return 2
    print("PASS: every corrupted target was rejected by its generator (a non-zero --check on every "
          "corruption shape; no generator returned exit 0 on a corrupt target)")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test_main()
    if args:
        print("usage: check_gensrc_failclose.py [--self-test]; fail-closed", file=sys.stderr)
        return 2
    return sweep(repo_root())


# --- self-test ----------------------------------------------------------------------------------------
# Proves this gate's own invariants against synthetic mini-repos assembled in a tempdir, each driving the
# real sweep() over real synthetic generators run as subprocesses (no mock of the mechanism under test):
#   (a) a conformant repo (a content-guarding file generator AND a content-guarding tree generator, plus
#       gen_gensrc's own registry target) passes the sweep (exit 0),
#   (b) a BYPASS generator that ignores its target is caught (exit 1) via VALID-BUT-DIFFERENT content, not
#       merely invalid UTF-8 - the F-154 regression, the whole point of the gate,
#   (c) a STATEFUL generator that drops a marker under $HOME on one call and keys off it is CAUGHT
#       (exit 1), proving the per-call unique HOME/TMPDIR and fresh independent sandbox deny it the
#       cross-call state it needs to fake a guard,
#   (d) a SYMLINK-SWAP generator that replaces its target with a symlink escaping the sandbox during
#       --check causes NO real-tree write and is cannot-evaluate (exit 2), via the post-probe containment
#       re-validation,
#   (e) a TOLERANT-READER generator (errors="replace") that DOES guard content passes (exit 0): the
#       valid-but-different probes draw no false violation from a reader that never raises on bad bytes,
#   (f) a TREE generator that guards only SOME members is caught (exit 1): every member is probed,
#   (g) a malformed registry, a missing declared target, a non-clean baseline, and an off-grammar
#       regenerate each fail closed (exit 2),
#   (h) the synthetic real tree is byte-AND-mode identical after a normal run AND after a run whose
#       corrupted probe RAISES (the baseline succeeds first, then the probe raises), and that raising run
#       returns the fail-closed exit 2,
#   (i) a generator that changes a real-tree file's MODE (via an absolute path out of its sandbox) is
#       detected by the before/after manifest and is cannot-evaluate (exit 2),
#   (j) a DECODE-ONLY generator (its --check decodes the target, so it fail-closes on invalid UTF-8, but
#       derives its verdict from the sources, so it returns 0 on valid-but-different content) is CAUGHT
#       (exit 1) SPECIFICALLY by a valid-but-different / same-length probe: it returns non-zero on the
#       invalid-UTF-8 probe, so only a content probe can catch it, which makes those probes load-bearing
#       (removing them would let this fixture pass and the self-test would fail),
#   (k) a total-runtime deadline overrun (via an injected monotonic clock) returns cannot-evaluate
#       (exit 2), never a fail-open 0,
#   (l) a sandbox-setup mkdtemp failure (injected OSError) returns cannot-evaluate (exit 2),
#   (m) a BLOCK-kind generator that reads+decodes the whole target and fail-closes (non-zero) on invalid
#       UTF-8 passes the sweep (exit 0): the gate probes a block target with invalid UTF-8 only and the
#       generator rejects it, exercising the is_block probe path no other fixture covers.
# Every fixture is synthetic; any invalid-UTF-8 corruption bytes are assembled from non-secret parts so
# this file never trips the repo secret scan.

_GOODFILE = '''import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, reconcile
GENSRC_OUTPUTS = (
    {"target": "out/goodfile.txt", "kind": "file",
     "sources": ("src/goodfile-src.txt",), "regenerate": "python3 tools/gen_goodfile.py"},
)
def run(root, check):
    text = (root / "src" / "goodfile-src.txt").read_text(encoding="utf-8")
    return 1 if reconcile(root / "out" / "goodfile.txt", "GENERATED\\n" + text, check) else 0
def main():
    return run(repo_root(), "--check" in sys.argv[1:])
if __name__ == "__main__":
    sys.exit(main())
'''

_GOODTREE = '''import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root
GENSRC_OUTPUTS = (
    {"target": "out/tree/", "kind": "tree",
     "sources": ("src/tree/",), "regenerate": "python3 tools/gen_goodtree.py"},
)
def run(root, check):
    src_dir = root / "src" / "tree"
    out_dir = root / "out" / "tree"
    desired = {}
    for p in sorted(src_dir.glob("*.txt")):
        desired[p.name] = "GEN\\n" + p.read_text(encoding="utf-8")
    drift = []
    for name, content in sorted(desired.items()):
        target = out_dir / name
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current != content:
            drift.append(name)
            if not check:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
    return 1 if (check and drift) else 0
def main():
    try:
        return run(repo_root(), "--check" in sys.argv[1:])
    except UnicodeError:
        print("error: invalid utf-8 tree member; fail-closed", file=sys.stderr)
        return 2
if __name__ == "__main__":
    sys.exit(main())
'''

# BLOCK: a kind="block" generator whose --check reads+decodes the WHOLE target and fail-closes (non-zero)
# on invalid UTF-8, mirroring how a real block generator (gen_secret_patterns) reads its block region. It
# does not compare region CONTENT (block region-content-guarding is NOT asserted by this gate; see the
# residuals), so it is the minimal conformant block generator: the gate probes a block target with invalid
# UTF-8 ONLY, this generator rejects it (non-zero), and the sweep passes (exit 0). It exercises the
# is_block branch (the invalid-UTF-8-only probe) that no other self-test fixture covers, and would catch a
# regression that mis-probed a block target as text (a decode-only reader returns 0 on valid-but-different
# content, which the gate would then flag as a violation). Write mode produces the target clean.
_BLOCKFILE = '''import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root
GENSRC_OUTPUTS = (
    {"target": "out/block.txt", "kind": "block",
     "sources": ("src/block-src.txt",), "regenerate": "python3 tools/gen_block.py"},
)
def run(root, check):
    text = (root / "src" / "block-src.txt").read_text(encoding="utf-8")
    desired = "GENERATED\\n" + text
    target = root / "out" / "block.txt"
    if not check:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(desired, encoding="utf-8")
        return 0
    target.read_text(encoding="utf-8")  # decode the whole block target: raises on invalid UTF-8
    return 0  # region content is not compared (block content-guarding is not asserted by this gate)
def main():
    try:
        return run(repo_root(), "--check" in sys.argv[1:])
    except UnicodeError:
        return 2  # fail-closed on invalid UTF-8, the whole-file corruption the gate applies to a block
if __name__ == "__main__":
    sys.exit(main())
'''

# BYPASS: never consults its target. In write mode it creates it; in --check it always passes, so even
# valid-but-different content slips through -> caught as a violation.
_BYPASS = '''import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root
GENSRC_OUTPUTS = (
    {"target": "out/bypass.txt", "kind": "file",
     "sources": ("src/bypass-src.txt",), "regenerate": "python3 tools/gen_bypass.py"},
)
def run(root, check):
    if not check:
        (root / "out" / "bypass.txt").write_text("BYPASS\\n", encoding="utf-8")
    return 0
def main():
    return run(repo_root(), "--check" in sys.argv[1:])
if __name__ == "__main__":
    sys.exit(main())
'''

# DECODE-ONLY: its --check DECODES the target (so it fail-closes to non-zero on invalid UTF-8) but derives
# its verdict from the SOURCES, ignoring the decoded content, so it returns 0 on valid-but-different (or
# same-length) content. Thus the invalid-UTF-8 probe alone would pass it (non-zero); only a
# valid-but-different / same-length content probe catches it (exit 0 on a corrupt target) -> caught. This
# makes those content probes load-bearing in the self-test.
_DECODEONLY = '''import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root
GENSRC_OUTPUTS = (
    {"target": "out/decode.txt", "kind": "file",
     "sources": ("src/decode-src.txt",), "regenerate": "python3 tools/gen_decode.py"},
)
def run(root, check):
    text = (root / "src" / "decode-src.txt").read_text(encoding="utf-8")
    desired = "GENERATED\\n" + text
    target = root / "out" / "decode.txt"
    if not check:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(desired, encoding="utf-8")
        return 0
    target.read_text(encoding="utf-8")  # decode only: raises on invalid UTF-8 -> fail-closed below
    return 0  # verdict "from the sources": the decoded target content is never compared
def main():
    try:
        return run(repo_root(), "--check" in sys.argv[1:])
    except UnicodeError:
        return 2  # fail-closed on invalid UTF-8
if __name__ == "__main__":
    sys.exit(main())
'''

# STATEFUL: on --check, if a marker file under $HOME is ABSENT it drops the marker and returns 0 WITHOUT
# consulting the target (the "first look, assume clean" bypass); only a SECOND call (marker present) would
# guard content. Under the OLD env-passthrough (a shared real $HOME) the baseline would drop the marker and
# the probe would key off it, missing the corruption; the per-call UNIQUE $HOME (plus fresh independent
# sandbox) denies that carry-over, so every call hits the marker-absent path and returns 0 on the corrupt
# target -> caught. Write mode (real env, no override) produces the correct target.
_STATEFUL = '''import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, reconcile
GENSRC_OUTPUTS = (
    {"target": "out/stateful.txt", "kind": "file",
     "sources": ("src/stateful-src.txt",), "regenerate": "python3 tools/gen_stateful.py"},
)
def run(root, check):
    text = (root / "src" / "stateful-src.txt").read_text(encoding="utf-8")
    desired = "GENERATED\\n" + text
    if not check:
        return 1 if reconcile(root / "out" / "stateful.txt", desired, False) else 0
    marker = Path(os.environ["HOME"]) / ".aiqt_seen"  # a $HOME marker: shared under the old passthrough
    if marker.exists():
        return 1 if reconcile(root / "out" / "stateful.txt", desired, True) else 0
    marker.write_text("seen\\n", encoding="utf-8")
    return 0
def main():
    return run(repo_root(), "--check" in sys.argv[1:])
if __name__ == "__main__":
    sys.exit(main())
'''

# SYMLINK-SWAP: on --check it replaces its target with a symlink to a file OUTSIDE the sandbox and returns
# 0 (a pretend pass). The gate's post-probe containment re-validation sees the target is no longer a
# contained regular file -> cannot-evaluate (exit 2), and no real-tree write occurs.
_SYMLINK_SWAP = '''import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root
GENSRC_OUTPUTS = (
    {"target": "out/swap.txt", "kind": "file",
     "sources": ("src/swap-src.txt",), "regenerate": "python3 tools/gen_swap.py"},
)
def run(root, check):
    target = root / "out" / "swap.txt"
    if not check:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("SWAP\\n", encoding="utf-8")
        return 0
    if target.exists() or target.is_symlink():
        target.unlink()
    target.symlink_to(sys.executable)  # a symlink to a real file outside the sandbox
    return 0
def main():
    return run(repo_root(), "--check" in sys.argv[1:])
if __name__ == "__main__":
    sys.exit(main())
'''

# TOLERANT-READER: reads its target with errors="replace" (never raising on bad bytes) and DOES guard
# content against the sources. Valid-but-different corruption differs from desired -> non-zero (pass);
# invalid-UTF-8 corruption is read as replacement chars, still != desired -> non-zero (pass). So a correct
# tolerant reader draws NO false violation.
_TOLERANT = '''import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root
GENSRC_OUTPUTS = (
    {"target": "out/tolerant.txt", "kind": "file",
     "sources": ("src/tolerant-src.txt",), "regenerate": "python3 tools/gen_tolerant.py"},
)
def run(root, check):
    text = (root / "src" / "tolerant-src.txt").read_text(encoding="utf-8")
    desired = "GENERATED\\n" + text
    target = root / "out" / "tolerant.txt"
    if not check:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(desired, encoding="utf-8")
        return 0
    current = target.read_text(encoding="utf-8", errors="replace") if target.exists() else None
    return 1 if current != desired else 0
def main():
    return run(repo_root(), "--check" in sys.argv[1:])
if __name__ == "__main__":
    sys.exit(main())
'''

# PARTIAL-TREE: guards only member leaf-a.txt in --check and ignores every other member. Probing leaf-b.txt
# (corrupt) draws exit 0 -> caught, proving the gate probes EVERY member, not one representative.
_PARTIALTREE = '''import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root
GENSRC_OUTPUTS = (
    {"target": "out/ptree/", "kind": "tree",
     "sources": ("src/tree/",), "regenerate": "python3 tools/gen_partialtree.py"},
)
def run(root, check):
    src_dir = root / "src" / "tree"
    out_dir = root / "out" / "ptree"
    if not check:
        out_dir.mkdir(parents=True, exist_ok=True)
        for p in sorted(src_dir.glob("*.txt")):
            (out_dir / p.name).write_text("GEN\\n" + p.read_text(encoding="utf-8"), encoding="utf-8")
        return 0
    only = out_dir / "leaf-a.txt"  # guards leaf-a ONLY; leaf-b is ignored (the partial-coverage bug)
    desired = "GEN\\n" + (src_dir / "leaf-a.txt").read_text(encoding="utf-8")
    current = only.read_text(encoding="utf-8") if only.exists() else None
    return 1 if current != desired else 0
def main():
    try:
        return run(repo_root(), "--check" in sys.argv[1:])
    except UnicodeError:
        return 2
if __name__ == "__main__":
    sys.exit(main())
'''

# BADCMD: a well-formed content-guard whose regenerate is off-grammar ("sh ...") -> cannot-evaluate.
_BADCMD = '''import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, reconcile
GENSRC_OUTPUTS = (
    {"target": "out/badcmd.txt", "kind": "file",
     "sources": ("src/badcmd-src.txt",), "regenerate": "sh tools/gen_badcmd.py"},
)
def run(root, check):
    text = (root / "src" / "badcmd-src.txt").read_text(encoding="utf-8")
    return 1 if reconcile(root / "out" / "badcmd.txt", "GENERATED\\n" + text, check) else 0
def main():
    return run(repo_root(), "--check" in sys.argv[1:])
if __name__ == "__main__":
    sys.exit(main())
'''

# MODECHANGER: on --check it reads an absolute victim path from a source file and chmods that REAL-tree
# file (escaping its sandbox), then guards content normally. It sets a FIXED mode (idempotent across the
# baseline and probe calls, so the change is not toggled back), which the before/after manifest detects
# as a real-tree change -> cannot-evaluate (exit 2).
_MODECHANGER = '''import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, reconcile
GENSRC_OUTPUTS = (
    {"target": "out/mode.txt", "kind": "file",
     "sources": ("src/mode-src.txt", "src/victim-path.txt"), "regenerate": "python3 tools/gen_mode.py"},
)
def run(root, check):
    text = (root / "src" / "mode-src.txt").read_text(encoding="utf-8")
    desired = "GENERATED\\n" + text
    if check:
        victim = (root / "src" / "victim-path.txt").read_text(encoding="utf-8").strip()
        try:
            os.chmod(victim, 0o400)  # a real-tree mode change out of the sandbox
        except OSError:
            pass
        return 1 if reconcile(root / "out" / "mode.txt", desired, True) else 0
    return 1 if reconcile(root / "out" / "mode.txt", desired, False) else 0
def main():
    return run(repo_root(), "--check" in sys.argv[1:])
if __name__ == "__main__":
    sys.exit(main())
'''

# GITREADER: a git-reading generator whose --check runs `git ls-files` and fail-closes (exit 2) when no
# usable repository is present, then guards its target content against the source. It models gen_manifest:
# under the D6 real-git template its baseline passes (a real repo is present), and a valid-but-different
# corruption of its target drifts to non-zero. Under the OLD bare-.git-marker template its baseline would
# have fail-closed (exit 2, no usable repo), so this fixture passing the sweep proves D6 materialized a
# real repository.
_GITREADER = '''import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, reconcile
GENSRC_OUTPUTS = (
    {"target": "out/gitreader.txt", "kind": "file",
     "sources": ("src/gitreader-src.txt",), "regenerate": "python3 tools/gen_gitreader.py"},
)
def run(root, check):
    text = (root / "src" / "gitreader-src.txt").read_text(encoding="utf-8")
    desired = "GEN\\n" + text
    target = root / "out" / "gitreader.txt"
    if not check:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(desired, encoding="utf-8")
        return 0
    proc = subprocess.run(["git", "-C", str(root), "ls-files"], capture_output=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        return 2  # no usable repository: fail closed, exactly what D6 must prevent for the baseline
    current = target.read_text(encoding="utf-8") if target.exists() else None
    return 1 if current != desired else 0
def main():
    return run(repo_root(), "--check" in sys.argv[1:])
if __name__ == "__main__":
    sys.exit(main())
'''

# A non-literal GENSRC_OUTPUTS (a bare Name): gen_gensrc's loader recovers the declaration with
# ast.literal_eval, which raises ValueError on a non-literal RHS, so enumeration fails closed.
_MALFORMED = "GENSRC_OUTPUTS = some_undefined_symbol\n"


def _write_gen(tools_dir, stem, body):
    (tools_dir / "gen_{}.py".format(stem)).write_text(body, encoding="utf-8")


def _run_write(root, stem):
    """Run a synthetic generator in WRITE mode to establish a clean baseline in a fixture repo."""
    subprocess.run([sys.executable, str(root / "tools" / "gen_{}.py".format(stem))],
                   cwd=str(root), env=_sanitized_env(), capture_output=True, timeout=PROBE_TIMEOUT,
                   check=False)


def _git_fixture(repo, *args):
    """Run git in a fixture repo with a pinned identity and no user config, matching _materialize_git.
    check=True: a setup failure surfaces loudly rather than a silently broken fixture."""
    env = _sanitized_env({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=aiqt-failclose",
         "-c", "user.email=failclose@invalid", "-c", "commit.gpgsign=false",
         "-c", "init.defaultBranch=main", *args],
        capture_output=True, env=env, timeout=PROBE_TIMEOUT, shell=False, check=True)


def _build_repo(base, gens):
    """Assemble a synthetic repo under base carrying the named generators plus the real gen_gensrc loader,
    generate every target into a clean state, then write the .aiqt/gensrc.json registry. `gens` maps a
    stem to its source body. A broad set of harmless sources is created so any generator finds its
    declared inputs; the modechanger fixture also gets a victim file plus the absolute path to it."""
    here = Path(__file__).resolve().parent
    tools = base / "tools"
    tools.mkdir(parents=True)
    shutil.copy2(here / "_gen_common.py", tools / "_gen_common.py")
    shutil.copy2(here / "gen_gensrc.py", tools / "gen_gensrc.py")
    (base / "src").mkdir()
    (base / "out").mkdir()
    (base / ".aiqt").mkdir()
    for name in ("goodfile", "bypass", "badcmd", "stateful", "swap", "tolerant", "mode", "decode",
                 "block", "gitreader"):
        (base / "src" / "{}-src.txt".format(name)).write_text("source for {}\n".format(name),
                                                              encoding="utf-8")
    tree_src = base / "src" / "tree"
    tree_src.mkdir()
    (tree_src / "leaf-a.txt").write_text("leaf a\n", encoding="utf-8")
    (tree_src / "leaf-b.txt").write_text("leaf b\n", encoding="utf-8")
    # The modechanger victim: a real-tree file whose mode the generator will change, plus a source file
    # carrying its absolute path (so the sandbox copy still points at the REAL victim, modelling an escape).
    victim = base / "victim.txt"
    victim.write_text("victim\n", encoding="utf-8")
    os.chmod(victim, 0o644)
    (base / "src" / "victim-path.txt").write_text(str(victim) + "\n", encoding="utf-8")
    for stem, body in gens.items():
        _write_gen(tools, stem, body)
    # Generate targets clean, then the registry last (it lists tools/ and every target).
    for stem in gens:
        _run_write(base, stem)
    _run_write(base, "gensrc")
    # Make the fixture a REAL git repository (F-234): _materialize_git enumerates real_root's tracked set
    # via `git ls-files` and no longer falls back to `git add -A` on a bare .git marker, so every fixture
    # that reaches materialization must be a genuine repo whose tracked set mirrors its on-disk files.
    _git_fixture(base, "init", "-q", "--template=")
    _git_fixture(base, "add", "-A")
    _git_fixture(base, "commit", "-q", "-m", "fixture baseline", "--no-verify")
    return base


def self_test_main():
    import io
    from contextlib import redirect_stdout, redirect_stderr

    def sweep_quiet(root):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return sweep(root)

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-gensrc-failclose-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    global _run_check, _now, _mkdtemp
    saved_run_check = _run_check
    saved_now = _now
    saved_mkdtemp = _mkdtemp
    cleanup_error = None
    try:
        # (a) A conformant repo (content-guarding file + tree generators) passes.
        good = _build_repo(tmp / "good", {"goodfile": _GOODFILE, "goodtree": _GOODTREE})
        if sweep_quiet(good) != 0:
            failures.append("conformant repo: expected the sweep to pass (exit 0)")

        # (b) A BYPASS generator is caught (exit 1) via valid-but-different content, not just invalid UTF-8.
        byp = _build_repo(tmp / "bypass", {"goodfile": _GOODFILE, "bypass": _BYPASS})
        if sweep_quiet(byp) != 1:
            failures.append("bypass generator: expected exit 1 (F-154 regression caught)")

        # (c) A STATEFUL generator is caught (exit 1): per-probe fresh sandboxes deny cross-call state.
        stf = _build_repo(tmp / "stateful", {"stateful": _STATEFUL})
        if sweep_quiet(stf) != 1:
            failures.append("stateful generator: expected exit 1 (per-probe isolation)")

        # (d) A SYMLINK-SWAP generator is cannot-evaluate (exit 2) and causes no real-tree write. The
        #     symlink points at sys.executable (outside the sandbox); assert it is untouched afterwards.
        swp = _build_repo(tmp / "swap", {"swap": _SYMLINK_SWAP})
        exe_before = os.stat(sys.executable)
        if sweep_quiet(swp) != 2:
            failures.append("symlink-swap generator: expected cannot-evaluate exit 2 (containment)")
        exe_after = os.stat(sys.executable)
        if (exe_before.st_mode, exe_before.st_size, exe_before.st_mtime_ns) != \
           (exe_after.st_mode, exe_after.st_size, exe_after.st_mtime_ns):
            failures.append("symlink-swap generator: the out-of-sandbox symlink target was written")

        # (e) A TOLERANT-READER that guards content passes (exit 0): no false violation.
        tol = _build_repo(tmp / "tolerant", {"tolerant": _TOLERANT})
        if sweep_quiet(tol) != 0:
            failures.append("tolerant-reader generator: expected exit 0 (no false violation)")

        # (f) A TREE generator guarding only SOME members is caught (exit 1): every member is probed.
        part = _build_repo(tmp / "partial", {"partialtree": _PARTIALTREE})
        if sweep_quiet(part) != 1:
            failures.append("partial-coverage tree generator: expected exit 1 (every member probed)")

        # (g) A non-literal GENSRC_OUTPUTS makes enumeration fail closed (exit 2).
        mal = tmp / "malformed"
        (mal / "tools").mkdir(parents=True)
        here = Path(__file__).resolve().parent
        shutil.copy2(here / "_gen_common.py", mal / "tools" / "_gen_common.py")
        shutil.copy2(here / "gen_gensrc.py", mal / "tools" / "gen_gensrc.py")
        (mal / ".git").write_text("marker\n", encoding="utf-8")
        _write_gen(mal / "tools", "malformed", _MALFORMED)
        if sweep_quiet(mal) != 2:
            failures.append("malformed registry: expected fail-closed exit 2")

        # (g) A declared target absent on disk fails closed (exit 2). Untrack it too, so real_root's
        #     tracked set still mirrors its on-disk files (the F-234 materialization equality stays clean)
        #     and the ABSENT-DECLARED-TARGET leg of _process_entry is what fails closed, not the new
        #     tracked-set guard (which the dedicated case below covers).
        miss = _build_repo(tmp / "missing-target", {"goodfile": _GOODFILE})
        (miss / "out" / "goodfile.txt").unlink()
        _git_fixture(miss, "rm", "-q", "--cached", "out/goodfile.txt")
        if sweep_quiet(miss) != 2:
            failures.append("missing declared target: expected fail-closed exit 2")

        # (g) A non-clean per-generator baseline (source edited so the target is stale) fails closed (2).
        stale = _build_repo(tmp / "stale-baseline", {"goodfile": _GOODFILE})
        (stale / "src" / "goodfile-src.txt").write_text("edited after generation\n", encoding="utf-8")
        if sweep_quiet(stale) != 2:
            failures.append("non-clean baseline: expected fail-closed exit 2")

        # (g) A regenerate off the grammar fails closed (exit 2).
        badcmd = _build_repo(tmp / "off-grammar", {"badcmd": _BADCMD})
        if sweep_quiet(badcmd) != 2:
            failures.append("off-grammar regenerate: expected fail-closed exit 2")

        # (h) Restoration: the synthetic real tree is byte-AND-mode identical after a normal run and after
        #     a run whose corrupted PROBE raises (the baseline succeeds first), and the raising run returns
        #     the fail-closed exit 2.
        rest = _build_repo(tmp / "restoration", {"goodfile": _GOODFILE})
        before = _tree_manifest(rest)
        if sweep_quiet(rest) != 0:
            failures.append("restoration: expected a clean normal run (exit 0)")
        if _tree_manifest(rest) != before:
            failures.append("restoration: the real tree changed after a normal run")

        calls = {"n": 0}

        def _raise_after_baseline(script_path, cwd, timeout=PROBE_TIMEOUT, env_overrides=None):
            # Let the first call (a per-generator baseline, run on a pristine sandbox) succeed, then raise
            # on the next call (the first corrupted probe), so restoration-after-raise is exercised.
            calls["n"] += 1
            if calls["n"] == 1:
                return saved_run_check(script_path, cwd, timeout, env_overrides)
            raise RuntimeError("injected probe failure after a clean baseline")

        _run_check = _raise_after_baseline
        rc = sweep_quiet(rest)
        _run_check = saved_run_check
        if rc != 2:
            failures.append("restoration: a run whose probe raises expected fail-closed exit 2, got "
                            "{!r}".format(rc))
        if calls["n"] < 2:
            failures.append("restoration: expected a baseline call then a raising probe call, saw "
                            "{} call(s)".format(calls["n"]))
        if _tree_manifest(rest) != before:
            failures.append("restoration: the real tree changed after a run whose probe raised")

        # (i) A generator that changes a real-tree file's MODE (out of its sandbox) is detected (exit 2).
        moded = _build_repo(tmp / "modechange", {"mode": _MODECHANGER})
        if sweep_quiet(moded) != 2:
            failures.append("real-tree mode change: expected cannot-evaluate exit 2 (manifest detected)")

        # (j) A DECODE-ONLY generator is caught (exit 1), SPECIFICALLY by a valid-but-different / same-length
        #     probe: it returns non-zero on the invalid-UTF-8 probe (it decodes and fail-closes there), so
        #     only a content probe can draw its exit-0 violation. Exit 1 therefore proves the content probe
        #     caught it, which makes those probes load-bearing: remove them and this fixture would pass.
        dec = _build_repo(tmp / "decode", {"decode": _DECODEONLY})
        if sweep_quiet(dec) != 1:
            failures.append("decode-only generator: expected exit 1 (caught by a valid-but-different / "
                            "same-length content probe, the invalid-UTF-8 probe alone would pass it)")

        # (k) A total-runtime deadline overrun (injected monotonic clock) returns cannot-evaluate (exit 2),
        #     never a fail-open 0. The clock returns a base on the first read (deadline setup) then jumps
        #     past the deadline, so the first baseline/probe deadline check fails closed.
        dline = _build_repo(tmp / "deadline", {"goodfile": _GOODFILE})
        clock_calls = {"n": 0}

        def _overrun_clock():
            clock_calls["n"] += 1
            if clock_calls["n"] == 1:
                return 0.0  # deadline := 0 + TOTAL_TIMEOUT
            return float(TOTAL_TIMEOUT) + 1000.0  # every later read is past the deadline

        _now = _overrun_clock
        rc_deadline = sweep_quiet(dline)
        _now = saved_now
        if rc_deadline != 2:
            failures.append("total-runtime deadline overrun: expected cannot-evaluate exit 2, got "
                            "{!r}".format(rc_deadline))

        # (l) A sandbox-setup mkdtemp failure (injected OSError) returns cannot-evaluate (exit 2), not an
        #     escaping raise (the work-dir mkdtemp now lives inside the try).
        setupfail = _build_repo(tmp / "setup-fail", {"goodfile": _GOODFILE})

        def _raise_mkdtemp(*args, **kwargs):
            raise OSError("injected sandbox-setup failure")

        _mkdtemp = _raise_mkdtemp
        rc_setup = sweep_quiet(setupfail)
        _mkdtemp = saved_mkdtemp
        if rc_setup != 2:
            failures.append("sandbox-setup mkdtemp failure: expected cannot-evaluate exit 2, got "
                            "{!r}".format(rc_setup))

        # (m) A BLOCK-kind generator that reads+decodes the whole target and fail-closes (non-zero) on
        #     invalid UTF-8 passes (exit 0): a block target is probed with invalid UTF-8 only, which this
        #     generator rejects. This exercises the is_block probe path that no other fixture covers.
        blk = _build_repo(tmp / "block", {"block": _BLOCKFILE})
        if sweep_quiet(blk) != 0:
            failures.append("block-kind generator: expected the sweep to pass (exit 0; a block target is "
                            "probed with invalid UTF-8 only and the generator rejects it)")

        # (n) D6: a git-reading generator whose --check runs `git ls-files` and fail-closes without a
        #     usable repo passes the sweep (exit 0), because the D6 template materialization plants a real
        #     git repository (its baseline succeeds and a corrupt target drifts to non-zero). Under the old
        #     bare-.git-marker template its baseline would have fail-closed (exit 2), so this proves D6.
        gitr = _build_repo(tmp / "gitreader", {"gitreader": _GITREADER})
        if sweep_quiet(gitr) != 0:
            failures.append("git-reading generator: expected the sweep to pass (exit 0; D6 materializes a "
                            "real git repo so the baseline git ls-files succeeds)")

        # (o) F-234a: a real_root whose repo-LOCAL core.fsmonitor points at a hook that writes an outside
        #     marker. The probe runs `-c core.fsmonitor=false --no-optional-locks`, so git NEVER executes
        #     the hook and the marker is never written; the sweep still completes (the goodfile fixture is
        #     otherwise clean). Without the override git would run the hook (arbitrary code out of scope).
        fsm = _build_repo(tmp / "fsmonitor", {"goodfile": _GOODFILE})
        fsmon_marker = tmp / "fsmonitor-executed.marker"
        hook = fsm / "fsmon-hook.sh"
        hook.write_text("#!/bin/sh\necho executed > '{}'\n".format(fsmon_marker), encoding="utf-8")
        os.chmod(hook, 0o755)
        _git_fixture(fsm, "config", "core.fsmonitor", str(hook))
        rc_fsm = sweep_quiet(fsm)
        if fsmon_marker.exists():
            failures.append("fsmonitor probe: the repo-local core.fsmonitor hook EXECUTED and wrote an "
                            "outside marker (the probe is missing -c core.fsmonitor=false)")
        if rc_fsm != 0:
            failures.append("fsmonitor probe: expected a clean sweep (exit 0) with the fsmonitor override "
                            "suppressing the hook, got {!r}".format(rc_fsm))

        # (p) F-234b: a FORCED probe failure (real_root's .git replaced by a bare marker, so `git ls-files`
        #     exits nonzero) is cannot-evaluate (exit 2), NEVER a silent `git add -A` fallback that would
        #     certify a bogus baseline. Without the fix the old fallback would materialize a repo and the
        #     goodfile fixture would sweep to exit 0.
        probefail = _build_repo(tmp / "probe-fail", {"goodfile": _GOODFILE})
        shutil.rmtree(probefail / ".git")
        (probefail / ".git").write_text("marker\n", encoding="utf-8")
        if sweep_quiet(probefail) != 2:
            failures.append("forced probe failure: expected cannot-evaluate exit 2 (no git add -A "
                            "fallback), a nonzero real_root ls-files must fail closed")

        # (q) F-234b: a TRACKED-SET MISMATCH (a file tracked in real_root's index but absent from the
        #     template) is detected as cannot-evaluate (exit 2). extra.txt is committed then deleted from
        #     disk, so copytree never copies it and the materialized template index lacks it. Without the
        #     equality guard the mismatch is unnoticed and the goodfile fixture sweeps to exit 0.
        mism = _build_repo(tmp / "tracked-mismatch", {"goodfile": _GOODFILE})
        (mism / "extra.txt").write_text("tracked extra\n", encoding="utf-8")
        _git_fixture(mism, "add", "extra.txt")
        _git_fixture(mism, "commit", "-q", "-m", "add extra", "--no-verify")
        (mism / "extra.txt").unlink()
        if sweep_quiet(mism) != 2:
            failures.append("tracked-set mismatch: expected cannot-evaluate exit 2 (the materialized "
                            "template index must equal real_root's tracked set)")
    finally:
        _run_check = saved_run_check
        _now = saved_now
        _mkdtemp = saved_mkdtemp
        try:
            shutil.rmtree(tmp)  # no ignore_errors: a self-test cleanup failure must surface, not hide
        except OSError as exc:
            cleanup_error = exc

    if cleanup_error is not None:
        print("SELF-TEST ERROR: could not remove the self-test tempdir ({}); fail-closed"
              .format(cleanup_error), file=sys.stderr)
        return 2
    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: a conformant repo (content-guarding file and tree generators) passes; a bypass "
          "generator is caught (exit 1) via valid-but-different content; a stateful generator keying off a "
          "$HOME marker is caught (exit 1) because per-call unique HOME/TMPDIR and fresh independent "
          "sandboxes deny cross-call state; a symlink-swap generator is cannot-evaluate (exit 2) with no "
          "out-of-sandbox write; a tolerant reader that guards content passes (exit 0, no false violation); "
          "a tree generator guarding only some members is caught (exit 1, every member probed); a malformed "
          "registry, a missing target, a non-clean baseline, and an off-grammar regenerate all fail closed "
          "(exit 2); the synthetic real tree is byte-and-mode identical after a normal run and after a run "
          "whose corrupted probe raises (itself exit 2); a generator changing a real-tree file's mode is "
          "detected (exit 2); a decode-only generator is caught (exit 1) specifically by a "
          "valid-but-different / same-length content probe; a total-runtime deadline overrun returns exit "
          "2; a sandbox-setup mkdtemp failure returns exit 2; a block-kind generator that fail-closes "
          "on invalid UTF-8 passes (exit 0), exercising the invalid-UTF-8-only block probe path; a "
          "git-reading generator passes (exit 0) because the D6 template materializes a real git "
          "repository so its baseline git ls-files succeeds; a repo-local core.fsmonitor hook is NEVER "
          "executed by the probe (no outside write) thanks to -c core.fsmonitor=false; and a forced probe "
          "failure and a tracked-set mismatch each fail closed (exit 2) with no git add -A fallback")
    return 0


if __name__ == "__main__":
    sys.exit(main())
