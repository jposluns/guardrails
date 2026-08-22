#!/usr/bin/env python3
"""Registry-driven fail-close gate: prove every gen_*.py REJECTS a corrupted committed target (GS-2).

The gensrc-drift gate proves a generator reproduces its target byte-for-byte from clean sources. It does
NOT prove the generator actually CONSULTS that target when checking: a generator whose `--check` ignores
its output (deriving a verdict from sources alone, or short-circuiting to exit 0) passes drift while
silently failing to guard the shipped artefact. That is the F-154 class. This gate closes it: for every
entry in the .aiqt/gensrc.json registry it corrupts a throwaway copy of the target and asserts the
generator's `--check` refuses to pass clean. The uniform invariant, the true anti-F-154 property, is that
a corruption of a committed target NEVER yields exit 0.

Enumeration reuses gen_gensrc's OWN validated in-memory loader (discover_declarations + collect_entries),
imported, never a second parser of .aiqt/gensrc.json, so this gate inherits every fail-closed enumeration
guarantee already proven in gen_gensrc --self-test (an unreadable tools/ raises, a bad declaration raises,
zero generators raises) instead of forking a parser that could drift.

Encoding is OBSERVED, not declared: the gate reads the pristine committed target bytes and attempts a
UTF-8 decode. Decodes to text (corrupt with invalid UTF-8, expect the fail-closed exit 2); fails to binary
(tamper one byte, expect drift exit 1). No new registry field is authored or drift-gated; the gate
measures the artefact it will corrupt.

Safety. All corruption happens in a disposable copytree sandbox; the REAL tree is STRICTLY read-only and
is never corrupted in place, so a SIGKILL, a full disk, or a power loss mid-run can never leave a corrupt
committed artefact (the worst case is an orphaned tempdir, never a corrupt real file). Symlinks and
special nodes are skipped when copying. Each generator runs as a subprocess with shell=False, the
interpreter pinned to sys.executable, cwd=sandbox, a sanitized environment (ambient GIT_*/PYTHONPATH and
the like stripped) carrying PYTHONDONTWRITEBYTECODE=1, and a bounded timeout. A `sandbox/.git` marker
anchors each generator's repo_root() on the sandbox so a probe can never walk up into the real repository.
A whole-tree manifest of the real repo is compared before and after the sweep; ANY change, or a cleanup
failure, is exit 2 (no ignore_errors that hides a failure).

Exit convention (matches the repo's gates):
  0  every entry's generator honoured its exit contract on a corrupted target.
  1  a generator VIOLATED its exit contract: it returned exit 0 on a corrupt target (the F-154
     regression), or a code other than the encoding-appropriate expected code (text -> 2, binary -> 1).
  2  cannot-evaluate: the registry is unreadable/malformed; the baseline `gen_gensrc --check` is not
     clean; a declared target is absent (a file/block that is not a regular file, or a tree that is empty
     or untraversable); a `regenerate` command is not of the grammar `python3 tools/gen_<stem>.py`; a
     per-generator baseline `--check` is not clean (a corruption signal would be unattributable); a probe
     times out or cannot be launched; the sandbox setup or cleanup fails; or the real tree changed during
     the run.
When both a confirmed violation and a cannot-evaluate condition are present, the exit code is 1 (the
confirmed live bug is the higher-signal outcome and we DID evaluate it); every condition is still printed.
A real-tree integrity breach or a cleanup failure overrides both to exit 2, because a run that may have
touched the real tree cannot be trusted to certify anything.

DISCLOSED RESIDUALS. The gate proves DETECTION (the generator refuses a corrupt target via --check), not
REPAIR (that regen emits correct bytes; the drift gate owns that). It probes ONE representative leaf per
tree, not every member. For block targets it corrupts the WHOLE file (whole-file unreadable), never a
region-internal byte: region drift stays on that generator's own drift step, and a tamper outside a
block's generated region is correctly a clean pass this gate does not assert on. "Unreadable" is modeled
as a single invalid-UTF-8 shape, not every encoding, permission, truncation, or race; a future text
generator that reads its target as latin-1 or with errors="replace" would return drift (exit 1) not
exit 2, and this gate flags that under the strict contract. Only the `python3 tools/gen_<stem>.py` grammar
is recognized; a wrapper or non-Python generator fails coverage (exit 2) until the grammar is extended.
Encoding observation could misclassify an ASCII-only binary as text (none exists today). The temp copy is
corruption isolation, NOT an OS security sandbox: a malicious generator could still reach absolute paths
or the network. Sources are not swept (a separate invariant). The gate tests a copy of the working tree,
so it captures local uncommitted edits; on CI, HEAD equals the tree.

  check_gensrc_failclose.py             sweep the real registry (default)
  check_gensrc_failclose.py --self-test build synthetic mini-repos and assert this gate's own invariants
"""
import hashlib
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402
import gen_gensrc  # noqa: E402  reuse its validated, fail-closed registry loader (do not re-parse the JSON)

# Directory names never copied into the sandbox and never hashed in the real-tree manifest: version
# control and derived caches, which are not registry targets and would only add noise and cost.
IGNORE_DIRS = {".git", "__pycache__", ".venv", "node_modules"}
# A single invalid-UTF-8 byte sequence: b"\xff" is never a valid UTF-8 start byte, so read_text(utf-8)
# raises UnicodeDecodeError on it. This is the portable "unreadable text" representative (see residuals).
INVALID_UTF8 = b"\xff\xfe\x00 not-utf8 \x80\x81"
# The regenerate grammar the gate recognizes: exactly `python3 tools/gen_<stem>.py`, validated with shlex.
REGEN_RE = re.compile(r"^tools/gen_([A-Za-z0-9_]+)\.py$")
PROBE_TIMEOUT = 120  # seconds per subprocess --check; a hung generator is cannot-evaluate, not a hang


def _is_utf8(data):
    """True if data decodes as UTF-8 (the observed-encoding test): text targets decode, binary do not."""
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _tamper(data):
    """Return a byte string guaranteed to differ from data: flip the first byte, or (empty file) add one.
    A different-bytes tamper is what a byte-reconciled generator must report as drift (exit 1)."""
    if not data:
        return b"\x00"
    out = bytearray(data)
    out[0] ^= 0xFF
    return bytes(out)


def _sanitized_env():
    """A minimal environment for a probe subprocess: PYTHONDONTWRITEBYTECODE=1 plus a small allowlist of
    safe variables carried from the parent. Ambient GIT_*, PYTHONPATH, and everything else are stripped,
    so an inherited environment cannot redirect a generator to a decoy repo or inject configuration."""
    env = {"PYTHONDONTWRITEBYTECODE": "1"}
    for key in ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "SYSTEMROOT", "PATHEXT"):
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    if "PATH" not in env:
        env["PATH"] = os.defpath
    return env


def _run_check(script_path, cwd, timeout=PROBE_TIMEOUT):
    """Run `python3 <script_path> --check` as a subprocess and return its exit code. shell=False, the
    interpreter pinned to sys.executable, a sanitized env, cwd anchored on the sandbox, a bounded timeout.
    Module-level so the self-test can substitute it. Raises subprocess.TimeoutExpired or OSError on a hung
    or unlaunchable probe; the caller maps that to cannot-evaluate."""
    proc = subprocess.run([sys.executable, str(script_path), "--check"], cwd=str(cwd),
                          env=_sanitized_env(), capture_output=True, timeout=timeout, shell=False)
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


def _tree_manifest(root):
    """Map every regular file under root (excluding IGNORE_DIRS, following no symlink) to a sha256 of its
    bytes. Used to prove the REAL tree is byte-identical before and after the sweep. Raises OSError on an
    unreadable input (fail-closed): a manifest that could not be built cannot certify the tree unchanged."""
    manifest = {}
    for dirpath, dirnames, filenames in os.walk(root, onerror=_raise_oserror):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS
                       and not os.path.islink(os.path.join(dirpath, d))]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            with open(full, "rb") as handle:
                manifest[os.path.relpath(full, root)] = hashlib.sha256(handle.read()).hexdigest()
    return manifest


def _raise_oserror(exc):
    """os.walk onerror hook: re-raise so an unlistable directory fails closed instead of silently
    yielding nothing."""
    raise exc


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


def _process_entry(entry, sandbox, baseline_cache, violations, cannot):
    """Corrupt one registry entry's target in the sandbox and assert the generator rejects it. Appends to
    `violations` (a confirmed exit-contract breach) or `cannot` (a fail-closed cannot-evaluate condition);
    never raises for an expected condition. The target is always restored to its pristine bytes in a
    finally, so a later entry driven by the same generator sees a clean tree."""
    target_rel = entry["target"]
    kind = entry["kind"]
    body = target_rel[:-1] if target_rel.endswith("/") else target_rel  # strip the tree marker

    stem = _validate_regenerate(entry["regenerate"])
    if stem is None:
        cannot.append("{}: regenerate {!r} is not of the grammar 'python3 tools/gen_<stem>.py'"
                      .format(target_rel, entry["regenerate"]))
        return
    script = sandbox / "tools" / "gen_{}.py".format(stem)
    if not script.is_file():
        cannot.append("{}: generator {} is absent in the sandbox".format(target_rel, script.name))
        return

    tpath = sandbox / body
    # Target-existence probe (a shippable artefact that is not there cannot be guarded; an empty declared
    # tree is exactly the presence-test-then-skip shape the fail-closed rule forbids).
    if kind == "tree":
        if tpath.is_symlink() or not tpath.is_dir():
            cannot.append("{}: declared tree target is absent or not a directory".format(target_rel))
            return
        try:
            members = _tree_members(tpath)
        except OSError as exc:
            cannot.append("{}: declared tree target is untraversable ({})".format(target_rel, exc))
            return
        if not members:
            cannot.append("{}: declared tree target is empty (no members to guard)".format(target_rel))
            return
    else:
        if tpath.is_symlink() or not tpath.is_file():
            cannot.append("{}: declared {} target is absent or not a regular file"
                          .format(target_rel, kind))
            return

    # Per-generator baseline: the pristine sandbox must pass --check, else the corruption signal is
    # unattributable (a broken generator, pre-existing drift, or no --check support). Cached per stem.
    if stem not in baseline_cache:
        try:
            baseline_cache[stem] = _run_check(script, sandbox)
        except (subprocess.TimeoutExpired, OSError) as exc:
            baseline_cache[stem] = "error: {}".format(exc)
    baseline = baseline_cache[stem]
    if baseline != 0:
        cannot.append("{}: baseline 'gen_{} --check' on the pristine sandbox returned {!r}, not a clean 0 "
                      "(a corruption signal would be unattributable)".format(target_rel, stem, baseline))
        return

    # Choose the byte string to corrupt: the whole file for file/block, one representative member for a
    # tree. Observe its encoding to pick the corruption and the expected exit.
    if kind == "tree":
        rep = next((m for m in members if _is_utf8(m.read_bytes())), members[0])
        corrupt_path = rep
    else:
        corrupt_path = tpath
    pristine = corrupt_path.read_bytes()
    is_text = _is_utf8(pristine)
    if is_text:
        corrupt_path.write_bytes(INVALID_UTF8)
        expected = 2
        shape = "invalid UTF-8"
    else:
        corrupt_path.write_bytes(_tamper(pristine))
        expected = 1
        shape = "a one-byte tamper"

    try:
        actual = _run_check(script, sandbox)
    except (subprocess.TimeoutExpired, OSError) as exc:
        cannot.append("{}: probe 'gen_{} --check' on the corrupted target failed to complete ({})"
                      .format(target_rel, stem, exc))
        return
    finally:
        corrupt_path.write_bytes(pristine)  # restore the single mutated path from its pristine bytes

    if actual == expected:
        return
    if actual == 0:
        violations.append("{}: gen_{} --check returned exit 0 on a target corrupted with {} (F-154: the "
                          "generator does not consult its target)".format(target_rel, stem, shape))
    else:
        violations.append("{}: gen_{} --check returned exit {} on a target corrupted with {}, expected {} "
                          "for a {} target (exit-contract violation)"
                          .format(target_rel, stem, actual, shape, expected,
                                  "text" if is_text else "binary"))


def sweep(real_root):
    """Sweep the real registry: for every entry, corrupt a sandbox copy of the target and assert the
    generator's --check rejects it. Returns the gate exit code (0/1/2). The real tree is never written."""
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

    violations, cannot = [], []
    unexpected = None
    tmp = tempfile.mkdtemp(prefix="aiqt-gensrc-failclose-")
    sandbox = Path(tmp) / "repo"
    try:
        try:
            shutil.copytree(real_root, sandbox, symlinks=False, ignore=_copy_ignore)
            (sandbox / ".git").write_text("sandbox repo_root marker (not a real git dir)\n",
                                          encoding="utf-8")
        except (OSError, shutil.Error) as exc:
            cannot.append("sandbox setup failed ({})".format(exc))
        else:
            try:
                gensrc_baseline = _run_check(sandbox / "tools" / "gen_gensrc.py", sandbox)
            except (subprocess.TimeoutExpired, OSError) as exc:
                gensrc_baseline = "error: {}".format(exc)
            if gensrc_baseline != 0:
                cannot.append("baseline 'gen_gensrc --check' on the pristine sandbox returned {!r}, not a "
                              "clean 0".format(gensrc_baseline))
            else:
                baseline_cache = {"gensrc": 0}
                for entry in entries:
                    _process_entry(entry, sandbox, baseline_cache, violations, cannot)
    except Exception as exc:  # noqa: BLE001  an unexpected error is fail-closed, never a silent pass
        unexpected = exc
    finally:
        cleanup_error = None
        try:
            shutil.rmtree(tmp)  # no ignore_errors: a cleanup failure must surface, not hide
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
              "happen: the gate corrupts only a disposable copy)", file=sys.stderr)
        return 2
    if cleanup_error is not None:
        print("cannot-evaluate: sandbox cleanup failed ({}); fail-closed".format(cleanup_error),
              file=sys.stderr)
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
        print("CANNOT-EVALUATE: {} entr(y/ies) could not be soundly evaluated".format(len(cannot)))
        for line in cannot:
            print("  " + line)
        return 2
    print("PASS: every corrupted target was rejected by its generator (no exit 0 on a corrupt target; "
          "text targets fail closed to exit 2, the one binary target drifts to exit 1)")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test_main()
    if args:
        print("usage: check_gensrc_failclose.py [--self-test]", file=sys.stderr)
        return 2
    return sweep(repo_root())


# --- self-test ----------------------------------------------------------------------------------------
# Proves this gate's own invariants against synthetic mini-repos assembled in a tempdir, each driving the
# real sweep() over real synthetic generators run as subprocesses (no mock of the mechanism under test):
#   (a) a conformant repo (a target-consulting file generator and a target-consulting tree generator, plus
#       gen_gensrc's own registry target) passes the sweep (exit 0),
#   (b) a BYPASS generator that ignores its target (always exit 0 even on a corrupt target) is caught as a
#       violation (exit 1) - the F-154 regression, the whole point of the gate,
#   (c) an unreadable/malformed registry (a generator with a non-literal GENSRC_OUTPUTS) fails closed (2),
#   (d) a declared target absent on disk fails closed (2),
#   (e) a non-clean per-generator baseline (a source edited so the target is stale) fails closed (2),
#   (f) a regenerate off the 'python3 tools/gen_<stem>.py' grammar fails closed (2),
#   (g) the synthetic real tree is byte-identical after a normal run AND after a run whose probe raises
#       (the restoration/read-only guarantee), and that raising run returns the fail-closed exit 2.
# Every fixture is synthetic; the invalid-UTF-8 corruption bytes are assembled from non-secret parts so
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

_BYPASS = '''import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root
GENSRC_OUTPUTS = (
    {"target": "out/bypass.txt", "kind": "file",
     "sources": ("src/bypass-src.txt",), "regenerate": "python3 tools/gen_bypass.py"},
)
def run(root, check):
    # BYPASS: never consults the target. In write mode it creates it; in --check it always passes.
    if not check:
        (root / "out" / "bypass.txt").write_text("BYPASS\\n", encoding="utf-8")
    return 0
def main():
    return run(repo_root(), "--check" in sys.argv[1:])
if __name__ == "__main__":
    sys.exit(main())
'''

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


def _build_repo(base, gens, extra_sources=True):
    """Assemble a synthetic repo under base carrying the named generators plus the real gen_gensrc loader,
    generate every target into a clean state, then write the .aiqt/gensrc.json registry. `gens` maps a
    stem to its source body."""
    here = Path(__file__).resolve().parent
    tools = base / "tools"
    tools.mkdir(parents=True)
    shutil.copy2(here / "_gen_common.py", tools / "_gen_common.py")
    shutil.copy2(here / "gen_gensrc.py", tools / "gen_gensrc.py")
    (base / "src").mkdir()
    (base / "out").mkdir()
    (base / ".aiqt").mkdir()
    (base / ".git").write_text("marker\n", encoding="utf-8")
    # Sources every generator kind might reference; harmless extras are ignored by generators not using them.
    (base / "src" / "goodfile-src.txt").write_text("source one\n", encoding="utf-8")
    (base / "src" / "bypass-src.txt").write_text("source two\n", encoding="utf-8")
    (base / "src" / "badcmd-src.txt").write_text("source three\n", encoding="utf-8")
    tree_src = base / "src" / "tree"
    tree_src.mkdir()
    (tree_src / "leaf-a.txt").write_text("leaf a\n", encoding="utf-8")
    (tree_src / "leaf-b.txt").write_text("leaf b\n", encoding="utf-8")
    for stem, body in gens.items():
        _write_gen(tools, stem, body)
    # Generate targets clean, then the registry last (it lists tools/ and every target).
    for stem in gens:
        _run_write(base, stem)
    _run_write(base, "gensrc")
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
    global _run_check
    saved_run_check = _run_check
    try:
        # (a) A conformant repo (target-consulting file + tree generators) passes.
        good = _build_repo(tmp / "good", {"goodfile": _GOODFILE, "goodtree": _GOODTREE})
        if sweep_quiet(good) != 0:
            failures.append("conformant repo: expected the sweep to pass (exit 0)")

        # (b) A BYPASS generator that ignores its target is caught as a violation (exit 1): the F-154 case.
        byp = _build_repo(tmp / "bypass",
                          {"goodfile": _GOODFILE, "bypass": _BYPASS})
        if sweep_quiet(byp) != 1:
            failures.append("bypass generator: expected exit 1 (F-154 regression caught)")

        # (c) A non-literal GENSRC_OUTPUTS makes enumeration fail closed (exit 2).
        mal = tmp / "malformed"
        (mal / "tools").mkdir(parents=True)
        here = Path(__file__).resolve().parent
        shutil.copy2(here / "_gen_common.py", mal / "tools" / "_gen_common.py")
        shutil.copy2(here / "gen_gensrc.py", mal / "tools" / "gen_gensrc.py")
        (mal / ".git").write_text("marker\n", encoding="utf-8")
        _write_gen(mal / "tools", "malformed", _MALFORMED)
        if sweep_quiet(mal) != 2:
            failures.append("malformed registry: expected fail-closed exit 2")

        # (d) A declared target absent on disk fails closed (exit 2).
        miss = _build_repo(tmp / "missing-target", {"goodfile": _GOODFILE})
        (miss / "out" / "goodfile.txt").unlink()
        if sweep_quiet(miss) != 2:
            failures.append("missing declared target: expected fail-closed exit 2")

        # (e) A non-clean per-generator baseline (source edited so the target is stale) fails closed (2).
        stale = _build_repo(tmp / "stale-baseline", {"goodfile": _GOODFILE})
        (stale / "src" / "goodfile-src.txt").write_text("edited after generation\n", encoding="utf-8")
        if sweep_quiet(stale) != 2:
            failures.append("non-clean baseline: expected fail-closed exit 2")

        # (f) A regenerate off the grammar fails closed (exit 2).
        badcmd = _build_repo(tmp / "off-grammar", {"badcmd": _BADCMD})
        if sweep_quiet(badcmd) != 2:
            failures.append("off-grammar regenerate: expected fail-closed exit 2")

        # (g) Restoration guarantee: the synthetic real tree is byte-identical after a normal run and
        #     after a run whose probe RAISES, and the raising run returns the fail-closed exit 2.
        rest = _build_repo(tmp / "restoration", {"goodfile": _GOODFILE, "goodtree": _GOODTREE})
        before = _tree_manifest(rest)
        sweep_quiet(rest)
        if _tree_manifest(rest) != before:
            failures.append("restoration: the real tree changed after a normal run")

        def _raising_run_check(*_args, **_kwargs):
            raise RuntimeError("injected probe failure")

        _run_check = _raising_run_check
        rc = sweep_quiet(rest)
        _run_check = saved_run_check
        if rc != 2:
            failures.append("restoration: a run whose probe raises expected fail-closed exit 2, got "
                            "{!r}".format(rc))
        if _tree_manifest(rest) != before:
            failures.append("restoration: the real tree changed after a run whose probe raised")
    finally:
        _run_check = saved_run_check
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: a conformant repo (target-consulting file and tree generators) passes; a "
          "bypass generator that ignores its target is caught as exit 1 (the F-154 regression); and a "
          "malformed registry, a missing declared target, a non-clean per-generator baseline, and a "
          "regenerate off the 'python3 tools/gen_<stem>.py' grammar all fail closed (exit 2); the "
          "synthetic real tree is byte-identical after a normal run and after a run whose probe raises "
          "(which itself returns the fail-closed exit 2)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
