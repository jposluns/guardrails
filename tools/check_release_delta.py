#!/usr/bin/env python3
"""Whole-surface release-delta gate (VER-CORE 6.5): computes the minimum required SemVer bump from
machine-readable deltas over the governance surface and FAILs a claimed bump below it. Offline except
git, stdlib only, fail-closed.

Surfaces diffed HEAD vs the previous anchored release (read via `git show <commit>:<path>`, the
check_version_monotonicity idiom, so checkout conversion cannot alter the comparison): clause
canonical text (7.2), the full pack-owned path keyset (SOURCES union manifest-self union the derived
ROOT/snippet, R10-3), ownership classes with the absolute-minimum table (4.2, via
check_manifest._min_for), the declared order record, profiles/groups (dormant until the
adopter-experience artifact exists), and the renderer/generator declaration (freshness enforced by
gen_renderers.py --check, the single home of closure recomputation). Dispositions are read ONLY from
the public record .aiqt/core/dispositions.toml (6.5): a private disposition is no disposition.

Disposition row schema (at source, reconciled with check_manifest.check_dispositions, which validates
the common fields and unique id in the same roster; this gate performs the PER-KIND validation Step 2
deferred): each [[disposition]] row carries EXACTLY the Step-2 common fields `id`, `release`, `kind`,
`impact`, and `rationale`. `id` is BOTH the record's unique key AND the CONSUMPTION key: the Step-2 schema
has no separate target field, so a row's `id` names the target it dispositions (a clause-id, path, or
renderer-id) and each leg matches a detected change to its row by that `id` (round-2 finding 2; the
invented `subject` field is gone). `kind` is one of behaviour-neutral, strengthened, default-correction,
class-change, version-impact, renderer-semantics; `release` is the bare SemVer the disposition applies at
(the field name matches the step-2 record, not the draft's `version`). Per-kind fields are validated here
(Step 2 defers them): a default-correction row carries the 6.6 evidence fields (captured-source,
capture-date, observed-measurement, observed-date, prefix-superset-reference); a class-change row carries
old-class and new-class bound to the observed transition; a renderer-semantics row's `impact` is
alters-obligations or byte-only (6.2/GD-89). A row that is malformed for its kind is exit 2: a mis-stated
control must not run the gate mis-configured.

Modes: default (genesis mode while releases.toml is zero-row and the manifest declares genesis;
post-release mode while the changelog head version EQUALS the newest attested release row, accepted only
when the head COMMIT descends from that row's commit_sha and differs from it in nothing but the
post-release record paths (judged entirely over COMMITTED objects through the substitution-free funnel;
checkout drift is a stderr advisory, never the verdict), AND the attested release itself re-passes the
exact gate it faced before its row was appended (the whole-surface delta from release_rows[-2], or the genesis validation for a first release;
RELEASING steps 6a/6b; see _post_release_head); whole-surface delta otherwise, where the head version must
strictly increase over the newest row); --repin --target V (10.4 adopter mode, rollback branch keyed on the
pin-history match plus wholesale target validation plus recorded authorization); --self-test.

Launch (QA round 5, D-397-REEXEC-FROM-COMMITTED): a plain run re-executes the COMMITTED copy of this
gate. Stage 1 (the launched file: standard library only, no checkout import, no sys.path change)
materializes HEAD's committed tree through a verified re-hashing walk and re-executes
tools/check_release_delta.py from that materialization under -I -B with a fresh pycache prefix; stage 2
routes every branch from COMMITTED records and judges the post-release branch over committed objects
exclusively, while the genesis and delta branches judge the checkout by design. The launched file
itself is checkout code: an attacker who controls it controls the launch (the disclosed residual).

Exit: 0 clean / genesis / NOT APPLICABLE legs; 1 a real finding (an under-claimed bump, a rowless
change, an unconsumed or wrong row, a register incompleteness); 2 malformed or unreadable input
(a predecessor artifact, the dispositions record, a stale renderer declaration, a git failure).
"""
import io
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: check_release_delta.py requires Python 3.11+ (tomllib).")


# ===== STAGE 1: re-execute the COMMITTED gate (QA round 5; orchestrator decision ====================
# D-397-REEXEC-FROM-COMMITTED). Round 5 demonstrated that vetting checkout code from INSIDE a process
# that already imported it cannot close the class: an untracked shadow module imported later (an
# untracked tools/tempfile.py), a crafted __pycache__ .pyc behind a byte-identical .py, and a
# repo-config core.fsmonitor hook that plants a file mid-run each flipped a refusal to exit 0 under
# the exact CI launch. So the gate stops vetting the checkout from inside it: when launched as a plain
# run (no options), this block, which imports NOTHING from the checkout and makes NO sys.path change
# (standard library only), resolves HEAD through the pinned git funnel below, materializes HEAD's
# COMMITTED tree into a fresh private directory through a verified re-hashing walk, and re-executes
# the COMMITTED copy of this gate from that directory under -I -B -X pycache_prefix=<fresh empty dir>,
# passing the repository and the materialized tree as explicit arguments. Stage 2 (the committed copy)
# then routes every branch from committed records (see run()).
#
# DISCLOSED RESIDUAL (inherent): this launched file is ITSELF checkout code; an attacker who controls
# the checkout copy of this file, the interpreter binary, or the launching process controls the launch
# and can fabricate any verdict, and no file can vet its own launch. What the re-execution closes is
# everything AFTER the launch: no other checkout file (no shadow module, no planted .pyc, no sibling
# tool, no routing record) participates in the verdict. A launch without -P/-I additionally leaves the
# few stdlib imports above this block shadowable by files planted beside this one; CI launches with
# -I, which keeps the script directory off sys.path.

def _stage1_git(repo, args, input_bytes=None, c_locale=False):
    """The stage-1 pinned git funnel: the inherited GIT_* environment scrubbed; replace objects,
    grafts, the commit-graph cache and core.fsmonitor all disabled on EVERY call (QA round-5
    reproduction (c): a repo-config fsmonitor hook is attacker-chosen code and must never run
    mid-gate; plumbing object reads apply no clean/smudge filter). QA round-6 (codex blocker 2):
    GIT_NO_LAZY_FETCH=1 is set AFTER the scrub (the scrub would otherwise drop an inherited copy)
    and protocol.allow=never is pinned on the argv, so a promisor/partial-clone repository can
    never start a lazy fetch (and with it core.sshCommand, a remote helper, or a credential
    helper) while stage 1 reads objects: a missing object is a refusal, never a transport.
    `c_locale` pins LC_ALL=C (which gettext honors over LANGUAGE) for the one caller that matches
    a git MESSAGE, the no-repository probe, so a localized message cannot defeat the match.
    Raises OSError when git cannot be launched (callers map it to exit 2; QA round-6 claude 3:
    never a traceback)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_GRAFT_FILE"] = os.devnull
    env["GIT_NO_LAZY_FETCH"] = "1"
    if c_locale:
        env["LC_ALL"] = "C"
    return subprocess.run(["git", "--no-replace-objects", "-c", "core.commitGraph=false",
                           "-c", "core.fsmonitor=false", "-c", "protocol.allow=never",
                           "-C", repo, *args], capture_output=True, env=env, input=input_bytes)


def _stage1_is_oid(sv):
    return isinstance(sv, str) and len(sv) in (40, 64) and all(c in "0123456789abcdef" for c in sv)


def _stage1_objects(repo, oids):
    """{oid: (type, body)} via one pinned `git cat-file --batch`, EVERY returned body re-hashed
    against the id that requested it (the stage-1 twin of _release_schema's typed batch reader,
    duplicated here because stage 1 may import nothing from the checkout). ValueError on any protocol
    or re-hash failure (mapped to exit 2 by the caller)."""
    import hashlib
    uniq = list(dict.fromkeys(oids))
    if not uniq:
        return {}
    proc = _stage1_git(repo, ["cat-file", "--batch"],
                       input_bytes=("\n".join(uniq) + "\n").encode("ascii"))
    if proc.returncode != 0:
        raise ValueError("git cat-file --batch failed: "
                         + proc.stderr.decode("utf-8", "replace").strip()[:200])
    out, i, result = proc.stdout, 0, {}
    for want in uniq:
        nl = out.find(b"\n", i)
        if nl == -1:
            raise ValueError("truncated cat-file header for " + want)
        parts = out[i:nl].decode("ascii", "replace").split(" ")
        i = nl + 1
        if (len(parts) != 3 or parts[0] != want
                or parts[1] not in ("blob", "commit", "tree", "tag") or not parts[2].isdigit()):
            raise ValueError("malformed cat-file header for {}: {!r}".format(want, parts))
        otype, size = parts[1], int(parts[2])
        body = out[i:i + size]
        if len(body) != size or out[i + size:i + size + 1] != b"\n":
            raise ValueError("short cat-file body for " + want)
        algo = hashlib.sha1 if len(want) == 40 else hashlib.sha256
        if algo(otype.encode("ascii") + b" " + str(size).encode("ascii") + b"\x00"
                + body).hexdigest() != want:
            raise ValueError("git object {} re-hash mismatch in the stage-1 walk; a tampered "
                             "loose object or object-store overlay substituted the "
                             "bytes".format(want))
        result[want] = (otype, body)
        i += size + 1
    if i != len(out):
        raise ValueError("{} trailing byte(s) after the last requested object".format(
            len(out) - i))
    return result


def _stage1_materialize(repo, commit_oid, dest):
    """Write the committed tree at commit_oid into dest from verified objects only: the commit, every
    tree, and every blob re-hashed against the id that named it; symlinks, gitlinks and unknown modes
    refused; blob modes preserved."""
    otype, body = _stage1_objects(repo, [commit_oid])[commit_oid]
    if otype != "commit":
        raise ValueError("object {} is a {}, not a commit".format(commit_oid, otype))
    first = body.split(b"\n", 1)[0]
    tree = first[5:].decode("ascii", "replace") if first.startswith(b"tree ") else ""
    if not _stage1_is_oid(tree):
        raise ValueError("commit {} carries no well-formed tree header".format(commit_oid))
    entries, stack = [], [(tree, "")]
    while stack:
        tree_oid, prefix = stack.pop()
        otype, body = _stage1_objects(repo, [tree_oid])[tree_oid]
        if otype != "tree":
            raise ValueError("object {} is a {}, not a tree".format(tree_oid, otype))
        oid_len, i = len(tree_oid) // 2, 0
        while i < len(body):
            sp = body.find(b" ", i)
            nul = body.find(b"\x00", sp + 1) if sp != -1 else -1
            if sp == -1 or nul == -1 or len(body) < nul + 1 + oid_len:
                raise ValueError("malformed tree object " + tree_oid)
            mode = body[i:sp].decode("ascii", "replace")
            name = body[sp + 1:nul].decode("utf-8")
            child = body[nul + 1:nul + 1 + oid_len].hex()
            i = nul + 1 + oid_len
            if not name or "/" in name or name in (".", ".."):
                raise ValueError("bad tree entry name in " + tree_oid)
            if mode == "40000":
                stack.append((child, prefix + name + "/"))
            elif mode in ("100644", "100755"):
                entries.append((mode, child, prefix + name))
            else:
                raise ValueError("unsupported mode {} at {} (symlinks and gitlinks are "
                                 "refused)".format(mode, prefix + name))
    blobs = _stage1_objects(repo, [c for _m, c, _p in entries])
    dest_root = os.path.realpath(dest)
    for mode, child, path in entries:
        target = os.path.realpath(os.path.join(dest, path))
        if target != dest_root and not target.startswith(dest_root + os.sep):
            raise ValueError("path {!r} escapes the materialization root".format(path))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(blobs[child][1])
        os.chmod(target, 0o755 if mode == "100755" else 0o644)


def _stage1_dotgit_in_ancestry(repo):
    """True when a `.git` entry (directory, file or symlink; lstat, so a dangling symlink or a
    gitfile naming a missing gitdir still counts) exists at the gate root or ANY ancestor up to the
    filesystem root (QA round-7 codex blocker). Git discovery searches ancestor directories, and a
    DAMAGED ancestor repository (a corrupt .git/HEAD, a .git file whose gitdir is missing) makes
    discovery fail with the very "not a git repository" wording true absence produces, so absence
    may be concluded only when no `.git` entry exists anywhere discovery would have searched. Any
    lstat failure other than a clean does-not-exist counts as PRESENT: fail-closed."""
    d = os.path.abspath(repo)
    while True:
        try:
            os.lstat(os.path.join(d, ".git"))
            return True
        except FileNotFoundError:
            pass
        except OSError:
            return True
        parent = os.path.dirname(d)
        if parent == d:
            return False
        d = parent


def _stage1_no_committed_state(repo):
    """True ONLY when a positive probe establishes that NO COMMITTED STATE exists, the sole
    condition under which the single-stage checkout gate may run (QA round-6 codex blocker 1 /
    claude 3). Either (a) NOT A GIT REPOSITORY: git's own discovery refusal under a pinned C
    locale names no repository AND no `.git` entry exists at the gate root OR ANY ANCESTOR up to
    the filesystem root (QA round-7 codex blocker: git discovery searches ancestors, so a present
    `.git` entry git cannot use ANYWHERE on that walk, e.g. a corrupt ancestor .git/HEAD or an
    ancestor .git file naming a missing gitdir, is a DAMAGED repository and never a fallback); or
    (b) an UNBORN HEAD: the repository resolves, HEAD is a symbolic ref to a
    refs/ branch, and `git show-ref --verify` answers exactly "missing" (rc 1) for that branch.
    Every other git failure (dubious ownership, bad config, a corrupt object store, a detached or
    corrupt HEAD) returns False and the caller exits 2 without importing any checkout module.
    Raises OSError when git cannot be launched (the caller maps it to exit 2)."""
    probe = _stage1_git(repo, ["rev-parse", "--git-dir"], c_locale=True)
    if probe.returncode != 0:
        err = probe.stderr.decode("utf-8", "replace")
        return ("not a git repository" in err
                and not _stage1_dotgit_in_ancestry(repo))
    sym = _stage1_git(repo, ["symbolic-ref", "--quiet", "HEAD"])
    if sym.returncode != 0:
        return False
    try:
        ref = sym.stdout.decode("ascii").strip()
    except UnicodeDecodeError:
        return False
    if not ref.startswith("refs/"):
        return False
    return _stage1_git(repo, ["show-ref", "--verify", "--quiet", ref]).returncode == 1


def _stage1_main():
    """Returns the final exit code, or None to fall through to the single-stage checkout gate ONLY
    when _stage1_no_committed_state POSITIVELY establishes that no committed state exists (not a
    git repository, or an unborn branch; there is then no committed revision to certify and no
    attested release to defend, and the genesis and delta branches judge the checkout by design).
    Any OTHER git failure (QA round-6 codex blocker 1 / claude 3: dubious ownership, a corrupt
    HEAD, bad config, git not launchable) is exit 2 HERE, before any checkout module is imported:
    a git failure is never permission to execute checkout code."""
    import shutil
    import tempfile
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        proc = _stage1_git(repo, ["rev-parse", "--verify", "--quiet", "HEAD^{commit}"])
        if proc.returncode != 0:
            if _stage1_no_committed_state(repo):
                return None
            print("error: stage-1 re-execution: git cannot resolve HEAD ({}) and no probe "
                  "positively established an absent committed state; a refused or damaged "
                  "repository is cannot-evaluate and runs no checkout code; fail-closed".format(
                      proc.stderr.decode("utf-8", "replace").strip()[:200]
                      or "rc={}".format(proc.returncode)), file=sys.stderr)
            return 2
    except OSError as exc:
        print("error: stage-1 re-execution: cannot launch git ({}); the committed state cannot be "
              "evaluated and no checkout code runs; fail-closed".format(exc), file=sys.stderr)
        return 2
    head = proc.stdout.decode("ascii", "replace").strip()
    if not _stage1_is_oid(head):
        print("error: stage-1 re-execution: git rev-parse HEAD returned no full object id; "
              "fail-closed", file=sys.stderr)
        return 2
    tmp = tempfile.mkdtemp(prefix="aiqt-release-delta-stage2-")
    try:
        tree_dir = os.path.join(tmp, "tree")
        pyc_dir = os.path.join(tmp, "pyc")
        os.mkdir(tree_dir)
        os.mkdir(pyc_dir)
        try:
            _stage1_materialize(repo, head, tree_dir)
        except (ValueError, OSError, KeyError, UnicodeDecodeError) as exc:
            print("error: stage-1 re-execution: cannot materialize HEAD's committed tree ({}); "
                  "fail-closed".format(exc), file=sys.stderr)
            return 2
        gate = os.path.join(tree_dir, "tools", "check_release_delta.py")
        if not os.path.isfile(gate):
            print("error: stage-1 re-execution: HEAD's committed tree carries no "
                  "tools/check_release_delta.py; the committed revision must carry the gate that "
                  "judges it; fail-closed", file=sys.stderr)
            return 2
        try:
            child = subprocess.run([sys.executable, "-I", "-B", "-X", "pycache_prefix=" + pyc_dir,
                                    gate, "--stage2-repo", repo, "--stage2-tree", tree_dir])
        except OSError as exc:
            print("error: stage-1 re-execution: cannot launch the committed gate ({}); "
                  "fail-closed".format(exc), file=sys.stderr)
            return 2
        return child.returncode
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__" and sys.argv[1:] == []:
    _stage1_rc = _stage1_main()
    if _stage1_rc is not None:
        sys.exit(_stage1_rc)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, load_toml            # noqa: E402
from check_versions import _parse                        # noqa: E402  bare-SemVer (sibling idiom)
from check_clauses import split_clause_id                # noqa: E402  authoritative clause-id syntax (7.1)
from gen_rules import SLUG_RE                             # noqa: E402  the authoritative renderer-id slug syntax
from check_manifest import _min_for, CLASS_STRENGTH, DISPOSITION_KINDS  # noqa: E402  minimum table + the
#                              single-source disposition kind vocabulary (VC-4 QA #5; a self-test binds it)
import gen_manifest                                      # noqa: E402  ownership loader (single source)
import _release_schema                                   # noqa: E402  the ONE shared strict validator set
from _release_schema import SchemaError                  # noqa: E402  (round-2 findings 1/3/4)

PATCH, MINOR, MAJOR = 0, 1, 2
BUMP_NAME = {PATCH: "PATCH", MINOR: "MINOR", MAJOR: "MAJOR"}
RELEASES_REL = ".aiqt/core/releases.toml"
DISPOSITIONS_REL = ".aiqt/core/dispositions.toml"
ORDER_REL = ".aiqt/core/order.toml"
RENDERERS_REL = ".aiqt/core/renderers.toml"
CLAUSES_REL = ".aiqt/core/clauses.toml"
OWNERSHIP_REL = ".aiqt/core/ownership.toml"
MANIFEST_REL = ".aiqt/manifest.toml"
CHANGELOG_REL = "changelog.toml"
IDHISTORY_REL = ".aiqt/core/id-history.toml"
DERIVED_PATHS = (".aiqt/release/root.txt", ".aiqt/release/announce-snippet.txt")
# The ONLY paths a post-release head may change from the newest attested release's commit_sha (RELEASING
# steps 6a/6b): the appended release row, the three regenerated branch-integrity artifacts (the same set as
# check_release_build.ALLOWED_ATTESTATION_DELTA), and changelog.toml, where only the newest release's `tag`
# key equal to "v" + version may be added (checked byte-for-byte by _post_release_changelog_ok).
POST_RELEASE_PATHS = frozenset((RELEASES_REL, MANIFEST_REL, CHANGELOG_REL) + DERIVED_PATHS)
# Adopter-experience hook: dormant until the artifact exists (2.6 structural absence).
PROFILES_REL = ".aiqt/core/profiles.toml"

# DISPOSITION_KINDS is imported from check_manifest (the Step-2 schema owner), the single source of the
# kind vocabulary, so Step 2 and Step 4 cannot disagree on which kinds exist (VC-4 QA #5).
# The Step-2 common mandatory fields this gate MUST also accept (matching check_manifest.check_dispositions
# exactly, so a record valid at Step 2 loads here without a fail-closed exit 2). `id` is BOTH the record's
# unique key AND the consumption key: the Step-2 schema carries no separate target field, so a row's `id`
# names the target (a clause-id, path, or renderer-id) that each leg matches via take_row (finding 2).
DISPOSITION_COMMON_FIELDS = ("id", "release", "kind", "impact", "rationale")
DEFAULT_CORRECTION_EVIDENCE = ("captured-source", "capture-date", "observed-measurement",
                               "observed-date", "prefix-superset-reference")
# The EXACT per-kind disposition row keyset (round-6 finding 6): every kind carries exactly the common
# fields plus its own, no more and no less. A default-correction adds the 6.6 evidence fields; a
# class-change adds old-class/new-class; every other kind is common-only.
_COMMON = frozenset(DISPOSITION_COMMON_FIELDS)
DISPOSITION_KIND_KEYS = {
    "behaviour-neutral": _COMMON, "strengthened": _COMMON, "version-impact": _COMMON,
    "renderer-semantics": _COMMON,
    "default-correction": _COMMON | frozenset(DEFAULT_CORRECTION_EVIDENCE),
    "class-change": _COMMON | frozenset(("old-class", "new-class"))}
# The release ownership-class vocabulary a class-change row moves between (single-sourced from gen_manifest).
OWNERSHIP_CLASS_VOCAB = frozenset(gen_manifest.RELEASE_CLASSES + gen_manifest.NAMESPACE_CLASSES)


class GateError(Exception):
    """Unreadable or malformed evidence: the gate cannot answer its question. Exit 2, never a
    silently conservative verdict."""


class DeltaEvent:
    """One machine-detected change on the governance surface."""
    def __init__(self, surface, subject, change, floor, row_kind=None, detail=""):
        self.surface, self.subject, self.change = surface, subject, change
        self.floor, self.row_kind, self.detail = floor, row_kind, detail


# --- git plumbing (as check_version_monotonicity: every return code checked) ------------------------

def _substitution_free_env():
    """The env half of the ONE substitution-free read funnel (QA round-3 codex R3-1 / claude F1):
    GIT_NO_REPLACE_OBJECTS=1 disables refs/replace/* object substitution and GIT_GRAFT_FILE pinned to
    os.devnull disables <GIT_DIR>/info/grafts parent rewriting, belt-and-braces with the
    --no-replace-objects argv option _git and _git_raw also pass. QA round-4 (codex R4-2 / claude m2):
    the environment is FIRST stripped of every GIT_-prefixed variable (the same allowlist scrub
    _index_materialized_tree and gen_manifest.git_tracked use), so an inherited GIT_DIR cannot point the
    reads at a decoy repository and GIT_OBJECT_DIRECTORY / GIT_ALTERNATE_OBJECT_DIRECTORIES cannot
    overlay a forged object store; dropping a GIT_ variable can at most make a read FAIL (a refusal,
    e.g. trust supplied only through GIT_CONFIG_*), never substitute a byte. QA round-6 (codex
    blocker 2): GIT_NO_LAZY_FETCH=1 is set AFTER the scrub, so a promisor/partial-clone repository
    can never start a lazy fetch (and with it core.sshCommand, a remote helper, or a credential
    helper) while the gate reads objects: a missing object is a refusal (SchemaError/GateError,
    exit 2), never a transport; the _git/_git_raw launches additionally pin
    -c protocol.allow=never, belt-and-braces."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_GRAFT_FILE"] = os.devnull
    env["GIT_NO_LAZY_FETCH"] = "1"
    return env


def _git(root, args, binary=False, c_locale=False):
    """THE substitution-free read funnel for every git read this gate makes (rev-parse, cat-file, show,
    ls-tree, the ls-tree/diff-index --cached/ls-files drift advisory, worktree list; _git_raw is its
    diff-tree/merge-base twin): replace
    objects are disabled (--no-replace-objects AND GIT_NO_REPLACE_OBJECTS=1), commit grafts are disabled
    (GIT_GRAFT_FILE pinned to os.devnull), and the commit-graph cache is never read (core.commitGraph
    pinned false, so ancestry and parent answers come from the recorded commit objects, never from a
    substitutable cache file; the commit-graph pin is UNTESTED defence in depth, QA round-4 claude m5:
    probing current git produced no case where a forged commit-graph changes a funneled verdict, only
    unpinned `git log` parent output, so no self-test coverage is claimed for this one pin). A replace ref or graft that substitutes another blob, tree, or parent
    chain for HEAD or the release commit therefore cannot alter any byte this gate judges (QA round-3
    codex R3-1 / claude F1). _release_schema's raw materialization (ls-tree + cat-file --batch) carries
    the SAME pins, so every committed byte the verdict consumes is substitution-free. core.fsmonitor is
    pinned false on every call (QA round 5, reproduction (c)): a repo-config fsmonitor program is
    attacker-chosen code. No funneled call reads working-tree content through git (`git status` and
    `git diff-index HEAD` would also run clean filters, and status the post-index-change hook; see
    _warn_checkout_drift)."""
    env = _substitution_free_env()
    if c_locale:
        # LC_ALL=C (honored by gettext over LANGUAGE) for the ONE caller that matches a git
        # MESSAGE, _no_committed_state's no-repository probe: a localized message cannot defeat it.
        env["LC_ALL"] = "C"
    try:
        return subprocess.run(["git", "--no-replace-objects", "-c", "core.commitGraph=false",
                               "-c", "core.fsmonitor=false", "-c", "protocol.allow=never",
                               "-C", str(root), *args],
                              capture_output=True, text=not binary, env=env)
    except OSError as exc:
        raise GateError("git is not available: {}".format(exc))


def _show(root, commit, path):
    """The raw committed bytes of `path` at `commit`, read ONLY from RE-HASHED objects through the
    pinned, environment-scrubbed funnel (QA round-4 codex R4-2 / claude m2): the commit object, every
    tree object on the path, and the blob itself are each re-hashed against the id that named them, so
    a tampered loose object or an object-store overlay cannot substitute a byte this gate judges; it
    can only make the read fail (GateError, exit 2)."""
    try:
        return _release_schema.verified_path_blob(root, commit, path)
    except SchemaError as exc:
        raise GateError("git show {}:{} failed: {}".format(commit, path, exc))


def _show_toml(root, commit, path):
    try:
        return tomllib.loads(_show(root, commit, path).decode("utf-8"))
    # ValueError and RecursionError too: tomllib raises a BARE ValueError (not TOMLDecodeError) on an
    # integer literal past CPython's 4300-digit int-string limit, and a RecursionError (a RuntimeError)
    # on a deeply nested array or inline table (F-TOML-BARE-VALUEERROR-CLASS). run()'s ValueError
    # backstop covered only the first; both now fail closed here, at the parse locus.
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        raise GateError("predecessor {} at {} does not parse: {}".format(path, commit, exc))


def _rev_parse(root, ref):
    """The full object id `ref` resolves to, or GateError. Read as BYTES and decoded ASCII-strict (round-5
    finding 3): a git id is ASCII, so a non-ASCII output is cannot-evaluate, never a crash."""
    proc = _git(root, ["rev-parse", "--verify", "--quiet", ref], binary=True)
    if proc.returncode != 0:
        raise GateError("cannot resolve {!r}".format(ref))
    try:
        return proc.stdout.decode("ascii").strip()
    except UnicodeDecodeError:
        raise GateError("git rev-parse output for {!r} is not valid ASCII; fail-closed".format(ref))


def _object_type(root, oid):
    """The git object type ('tag' for an annotated tag, 'commit', 'tree', 'blob') of an IMMUTABLE object id,
    or GateError. Reads the object BY ITS OID (never a named ref), so the type inspection cannot race a
    moving tag: the anchor resolves the named ref once and inspects the recorded object here (this round's
    #1)."""
    proc = _git(root, ["cat-file", "-t", oid], binary=True)
    if proc.returncode != 0:
        raise GateError("git cat-file -t {} failed (object unreachable)".format(oid))
    try:
        return proc.stdout.decode("ascii").strip()
    except UnicodeDecodeError:
        raise GateError("git cat-file output for {} is not valid ASCII; fail-closed".format(oid))


def _anchor_predecessor(root, prev_row):
    """Establish that the predecessor row names an ANCHORED release (round-7 finding 2): its commit_sha is a
    full object id resolving to a COMMIT equal to itself, and its annotated tag resolves to the recorded
    tag_object_sha and peels to that commit. A malformed/unanchored predecessor is cannot-evaluate, exit 2,
    never a silent delta over an unverifiable base.

    The NAMED ref refs/tags/<tag> is read EXACTLY ONCE (this round's #1): it is resolved to its immutable
    OID, that OID is required to equal the recorded tag_object_sha, and every further check (the object's
    type, its peel to a commit) reads the IMMUTABLE recorded OID BY ID, never the named ref again. Reading
    the named ref a second time (for the type OR the peel) let a tag MOVED between the reads present a split
    view: one read seeing the recorded object and the other a different, now-current object."""
    commit, tag, tag_obj = prev_row["commit_sha"], prev_row["tag"], prev_row["tag_object_sha"]
    if not _release_schema.OBJECTID_RE.fullmatch(commit):
        raise GateError("predecessor commit_sha {!r} is not a full lowercase object id".format(commit))
    if _rev_parse(root, commit + "^{commit}") != commit:
        raise GateError("predecessor commit_sha {!r} does not resolve to a commit equal to itself "
                        "(a tree or non-commit is rejected)".format(commit))
    # The ONE and ONLY read of the named ref: resolve refs/tags/<tag> to its immutable OID.
    resolved = _rev_parse(root, "refs/tags/" + tag)
    if resolved != tag_obj:
        raise GateError("predecessor tag {} does not resolve to the recorded tag_object_sha".format(tag))
    # Everything below reads the IMMUTABLE recorded OID by id, never the named ref again.
    if _object_type(root, tag_obj) != "tag":
        raise GateError("predecessor tag {} is not an annotated tag (2.1)".format(tag))
    if _rev_parse(root, tag_obj + "^{commit}") != commit:
        raise GateError("predecessor tag {} peels to a commit other than the recorded commit_sha".format(
            tag))
    # QA round-4 (codex R4-2 / claude m2): the two reads above resolve through the object store
    # UNVERIFIED (rev-parse / cat-file -t do not re-hash), so additionally re-hash the recorded tag
    # OBJECT itself and parse its peel from the verified body: a tampered loose tag object cannot point
    # the anchor at a different commit than the recorded one. Refusal-only: this can never accept a
    # state the checks above rejected.
    try:
        otype, body = _release_schema.verified_object(root, tag_obj)
    except SchemaError as exc:
        raise GateError(str(exc))
    if otype != "tag":
        raise GateError("predecessor tag {} object {} is not an annotated tag object under the "
                        "re-hashed read; fail-closed".format(tag, tag_obj))
    headers = dict(
        (ln.partition(b" ")[0], ln.partition(b" ")[2])
        for ln in reversed(body.split(b"\n\n", 1)[0].split(b"\n")))
    if headers.get(b"type") != b"commit" or headers.get(b"object") != commit.encode("ascii"):
        raise GateError("predecessor tag {} verified body peels to {!r} (type {!r}), not the recorded "
                        "commit_sha; fail-closed".format(tag, headers.get(b"object"),
                                                          headers.get(b"type")))


# --- record loading ---------------------------------------------------------------------------------

def _load(root, rel):
    try:
        return load_toml(root / rel)
    except (OSError, ValueError) as exc:
        raise GateError("cannot read {} ({})".format(rel, exc))


def load_release_rows(root):
    """The release-order rows the delta gate needs (2.4), validated through the ONE shared strict validator
    (_release_schema.strict_releases): format-version == 1, the exact top-level keyset, and the full
    complete-record schema for every present row (round-2 finding 1: the loader must actually call the
    shared strict validator, not a lenient partial one). Zero rows is a valid genesis state; the delta gate
    resolves the predecessor by commit_sha, and a present row is a post-QA attestation row carrying the
    whole record, so a partial row (only version/commit) is exit 2, not a silently accepted predecessor."""
    data = _load(root, RELEASES_REL)
    try:
        return _release_schema.strict_releases(data, RELEASES_REL)
    except SchemaError as exc:
        raise GateError(str(exc))


def normalize_dispositions(rows, where_rel=DISPOSITIONS_REL):
    """Validate a list of disposition row dicts to full per-kind depth (6.5/6.6). Returns a list of
    normalized rows each carrying a private _consumed flag. A malformed row raises GateError. `where_rel`
    labels the record in error messages so the HEAD and the predecessor records are distinguishable (this
    round's #2, the predecessor now runs the same Step-4 validation).

    The COMMON mandatory fields are exactly DISPOSITION_COMMON_FIELDS = {id, release, kind, impact,
    rationale} (matching check_manifest.check_dispositions, the Step-2 record), and `id` IS the record's
    real unique key AND the consumption key (round-2 finding 2): the Step-2 schema carries no separate
    target field, so `id` names the target the row dispositions (a clause-id, path, or renderer-id), and
    each leg maps a detected change to its row by matching `id` via take_row. The invented `subject` field
    is gone; a row whose `id` names no detected target is later reported UNCONSUMED (a finding, not a
    crash). Per-kind depth is enforced here, exactly what Step 2 defers to Step 4 (finding 3): a
    class-change row must carry old-class and new-class (bound to the observed change in ownership_leg); a
    default-correction row's captured EVIDENCE is validated per 6.6 (a well-formed URL, valid dates, a real
    measurement, a prefix-superset reference), not merely checked nonempty; a renderer-semantics row's
    impact is alters-obligations or byte-only."""
    if not isinstance(rows, list):
        raise GateError("{}: [[disposition]] is not an array".format(where_rel))
    out, seen_ids = [], set()
    for i, row in enumerate(rows, 1):
        where = "{} row #{}".format(where_rel, i)
        if not isinstance(row, dict):
            raise GateError(where + ": not a table")
        for field in DISPOSITION_COMMON_FIELDS:
            v = row.get(field)
            if not isinstance(v, str) or not v:
                raise GateError(where + ": missing or non-string {!r} (the Step-2 common schema)".format(
                    field))
            # Every common field is a SINGLE-LINE value (id, release, kind, impact, rationale); a control
            # character in any is a malformed record (round-5 sweep: no descriptive string field is left
            # value-unvalidated). release/kind are further constrained below and id carries per-kind target
            # syntax; this closes the free-text impact/rationale (and a version-impact id, which has no
            # target syntax) against a smuggled 0x00-0x1F/0x7F byte.
            if _release_schema.has_control_char(v):
                raise GateError(where + ": {!r} carries a control character (0x00-0x1F/0x7F)".format(field))
        rid, kind, release = row["id"], row["kind"], row["release"]
        if rid in seen_ids:
            raise GateError(where + ": duplicate disposition id {!r}".format(rid))
        seen_ids.add(rid)
        if kind not in DISPOSITION_KINDS:
            raise GateError(where + ": kind must be one of {}".format(sorted(DISPOSITION_KINDS)))
        if _parse(release) is None:
            raise GateError(where + ": release {!r} is not a bare SemVer".format(release))
        # EXACT per-kind row keyset (round-6 finding 6): no extra or missing field for the kind.
        expected = DISPOSITION_KIND_KEYS.get(kind)
        if expected is not None and set(row) != expected:
            raise GateError(where + ": {} row keys must be EXACTLY {} (found {})".format(
                kind, sorted(expected), sorted(row)))
        try:
            if kind == "default-correction":
                _release_schema.default_correction_evidence_findings(row, where)
            elif kind == "class-change":
                # old-class and new-class are validated against the ownership-class VOCABULARY (round-6
                # finding 6): a malformed class is a mis-stated control, exit 2, never a MINOR finding. The
                # binding to the OBSERVED transition stays ownership_leg.
                for k in ("old-class", "new-class"):
                    if row[k] not in OWNERSHIP_CLASS_VOCAB:
                        raise SchemaError(where + ": class-change {} {!r} is not a release ownership class "
                                          "({})".format(k, row[k], sorted(OWNERSHIP_CLASS_VOCAB)))
            elif kind == "renderer-semantics" and row.get("impact") not in ("alters-obligations",
                                                                            "byte-only"):
                raise SchemaError(where + ": renderer-semantics row needs impact = alters-obligations or "
                                  "byte-only (6.2/GD-89)")
        except SchemaError as exc:
            raise GateError(str(exc))
        out.append(dict(row, _consumed=False))
    return out


# Which target SYNTAX each disposition kind's `id` must satisfy (this round's #3): the clause kinds target
# a canonical clause-id, class-change targets a canonical repo-relative path, renderer-semantics targets a
# renderer-id slug (existence against the declared renderers is checked separately in run()). version-impact
# is not consumed by any leg and names no on-disk target, so it keeps only the common non-empty check.
_CLAUSE_DISPOSITION_KINDS = frozenset({"behaviour-neutral", "strengthened", "default-correction"})


def _validate_disposition_target_syntax(rows, where_rel=DISPOSITIONS_REL):
    """Validate each disposition's `id` (its TARGET) by KIND (this round's #3), so a malformed target (a
    path-traversal class-change id like '../escape', a non-clause-id clause target, or a non-slug renderer
    target) is a malformed control, exit 2, not a row that silently matches nothing. Renderer EXISTENCE (the
    id names a DECLARED renderer) is checked in run() where the strict-validated renderer set is available."""
    for r in rows:
        kind, rid = r["kind"], r["id"]
        if kind in _CLAUSE_DISPOSITION_KINDS:
            if split_clause_id(rid) is None:
                raise GateError("{}: {} disposition id {!r} is not a canonical clause-id (7.1)".format(
                    where_rel, kind, rid))
        elif kind == "class-change":
            if not _release_schema.is_canonical_relpath(rid):
                raise GateError("{}: class-change disposition id {!r} is not a canonical repo-relative path "
                                "(no '.', '..', '//', backslash, control character, or host-absolute "
                                "form)".format(where_rel, rid))
        elif kind == "renderer-semantics":
            if not SLUG_RE.fullmatch(rid):
                raise GateError("{}: renderer-semantics disposition id {!r} is not a valid renderer-id "
                                "slug".format(where_rel, rid))


def _validate_dispositions_data(data, where_rel):
    """Full Step-4 per-kind validation of a PARSED dispositions record, applied to BOTH the HEAD and the
    PREDECESSOR objects (this round's #2): format-version == 1, the exact top-level keyset, per-row/per-kind
    normalization (exact keysets, values, 6.6 evidence), and per-kind target SYNTAX. Returns the normalized
    rows. Renderer-id EXISTENCE is NOT checked here (it is a consumption concern validated in run() against
    the appropriate declaration scope), so the predecessor record is not held to the current renderer set."""
    if data.get("format-version") != 1:
        raise GateError("{}: format-version must be exactly 1".format(where_rel))
    extra = set(data) - {"format-version", "disposition"}
    if extra:
        raise GateError("{}: unknown top-level key(s): {}".format(where_rel, ", ".join(sorted(extra))))
    rows = normalize_dispositions(data.get("disposition", []), where_rel)
    _validate_disposition_target_syntax(rows, where_rel)
    return rows


def _renderer_ids(renderers_data):
    """The set of renderer-ids in a strict-validated renderer declaration."""
    return {r["renderer-id"] for r in renderers_data.get("renderer", []) if isinstance(r, dict)}


def _nongenesis_renderer_target_scope(prev_renderers, head_renderers):
    """The renderer-ids a NON-GENESIS renderer-semantics disposition may target: the UNION of the strict-
    validated PREDECESSOR and HEAD declarations (this round's #6). A renderer REMOVED between the releases is
    declared only in the predecessor, and its removal is dispositioned by a renderer-semantics row; scoping
    the existence check to HEAD alone wrongly rejected that valid removal row. Genesis, having no
    predecessor, keeps HEAD-only."""
    return _renderer_ids(prev_renderers) | _renderer_ids(head_renderers)


def _assert_renderer_targets_declared(rows, declared_ids):
    """Every renderer-semantics disposition's `id` names a DECLARED renderer (this round's #3): a row that
    targets a renderer-id absent from `declared_ids` (the genesis HEAD set, or the non-genesis predecessor-
    plus-HEAD union) is a mis-stated control, exit 2, never a silently unconsumed row."""
    for r in rows:
        if r["kind"] == "renderer-semantics" and r["id"] not in declared_ids:
            raise GateError("{}: renderer-semantics disposition id {!r} names no declared renderer-id "
                            "({})".format(DISPOSITIONS_REL, r["id"], sorted(declared_ids)))


def load_dispositions(root):
    """The public HEAD record, strictly validated (6.5) through the shared parsed-data validator: format-
    version == 1, the exact top-level keyset, full per-row/per-kind normalization, and per-kind target
    syntax. Returns a list of normalized rows. The predecessor record is validated by the same function in
    run() (this round's #2)."""
    return _validate_dispositions_data(_load(root, DISPOSITIONS_REL), DISPOSITIONS_REL)


def _strict(fn, data, rel):
    """Run a shared _release_schema validator on an already-parsed record dict and map its SchemaError to
    this gate's fail-closed GateError (exit 2). The single conversion point for the run() body validations
    on BOTH head and predecessor objects (round-2 findings 1/4)."""
    try:
        return fn(data, rel)
    except SchemaError as exc:
        raise GateError(str(exc))


def take_row(rows, kind, target, release):
    """Consume exactly one unconsumed row whose (kind, id, release) matches the detected change, keying on
    the row's `id` field, which names the target (round-2 finding 2: the Step-2 schema's real key is `id`,
    not the invented `subject`). None if absent; GateError if two rows would disposition the same change
    (defensive: normalize_dispositions already rejects a duplicate id, so this cannot arise from a valid
    record, but the guard fails closed if a caller passes un-normalized rows)."""
    matches = [r for r in rows if r["kind"] == kind and r["id"] == target
               and r["release"] == release and not r["_consumed"]]
    if len(matches) > 1:
        raise GateError("two {} disposition rows disposition {!r} at {} (ambiguous)".format(
            kind, target, release))
    if matches:
        matches[0]["_consumed"] = True
        return matches[0]
    return None


# --- legs (each returns (events, findings)) ---------------------------------------------------------

def _register_lifecycle(register):
    """Extract from the id-history register (7.3), for BOTH corpus-ids and clause-ids: {id: born-release},
    {id: count of retirement (tombstone+successor) rows}, and {id: that row's retired-release}. Used to
    enforce that an add or a removal carries its 7.3 row at THIS release (VC-4 QA #3)."""
    born_at = {}
    for r in register.get("born", []):
        if isinstance(r, dict) and isinstance(r.get("id"), str):
            born_at[r["id"]] = r.get("born-release")
    retire_count, retire_release = {}, {}
    for section in ("tombstone", "successor"):
        for r in register.get(section, []):
            if isinstance(r, dict) and isinstance(r.get("id"), str):
                retire_count[r["id"]] = retire_count.get(r["id"], 0) + 1
                retire_release[r["id"]] = r.get("retired-release")
    return born_at, retire_count, retire_release


def clause_text_leg(prev_inv, head_inv, register, rows, head_version):
    """6.5 CLAUSE TEXT and 7.3 ID LIFECYCLE. Diff canonical text per clause-id; classify a same-id text
    change via the public dispositions. Span or source-digest movement without a text change is not a
    delta. For an ADD or a REMOVAL the accepted disposition is the id-history ROW (6.5 accepts exactly one
    of: an id-history row; or a same-id behaviour-neutral/strengthened/default-correction disposition), so
    this leg REQUIRES that row, at THIS release and unique (VC-4 QA #3): an added id needs a born row
    dated head_version; a removed id needs exactly one retirement row dated head_version. Corpus-ids (the
    family before the dot) are diffed INDEPENDENTLY of clause-ids, so a wholly new or fully-retired corpus
    family gains or needs its own 7.3 row (7.3 keeps one born row per corpus-id AND clause-id)."""
    events, findings = [], []
    prev = {r["clause-id"]: r.get("canonical-text", "") for r in prev_inv if isinstance(r, dict)}
    head = {r["clause-id"]: r.get("canonical-text", "") for r in head_inv if isinstance(r, dict)}
    born_at, retire_count, retire_release = _register_lifecycle(register)

    def _require_born(ident, kind_label):
        if born_at.get(ident) != head_version:
            findings.append("{} {!r} is new since the predecessor release but has no id-history born row "
                            "at {} (7.3/6.5)".format(kind_label, ident, head_version))

    def _require_retirement(ident, kind_label):
        n = retire_count.get(ident, 0)
        if n == 0:
            findings.append("{} {!r} disappeared with no tombstone or successor row (7.3)".format(
                kind_label, ident))
        elif n > 1:
            findings.append("{} {!r} carries {} retirement rows; exactly one is allowed (7.3)".format(
                kind_label, ident, n))
        elif retire_release.get(ident) != head_version:
            findings.append("{} {!r} retirement row is dated {!r}, not the release under build {} "
                            "(7.3)".format(kind_label, ident, retire_release.get(ident), head_version))

    for cid in sorted(prev.keys() - head.keys()):
        events.append(DeltaEvent("clause", cid, "removed", MAJOR))
        _require_retirement(cid, "clause-id")
    for cid in sorted(head.keys() - prev.keys()):
        events.append(DeltaEvent("clause", cid, "added", MINOR))
        _require_born(cid, "clause-id")
    for cid in sorted(prev.keys() & head.keys()):
        if prev[cid].encode("utf-8") == head[cid].encode("utf-8"):
            continue
        for kind, floor in (("behaviour-neutral", PATCH), ("strengthened", MINOR),
                            ("default-correction", MINOR)):
            if take_row(rows, kind, cid, head_version) is not None:
                events.append(DeltaEvent("clause", cid, kind, floor, kind))
                break
        else:
            events.append(DeltaEvent("clause", cid, "undispositioned-text-change", MAJOR,
                                     detail="no public disposition; MAJOR floor (6.4 fail-closed)"))
    prev_corpus = {c.partition(".")[0] for c in prev}
    head_corpus = {c.partition(".")[0] for c in head}
    for corp in sorted(prev_corpus - head_corpus):
        events.append(DeltaEvent("clause", corp, "corpus-removed", MAJOR))
        _require_retirement(corp, "corpus-id")
    for corp in sorted(head_corpus - prev_corpus):
        events.append(DeltaEvent("clause", corp, "corpus-added", MINOR))
        _require_born(corp, "corpus-id")
    return events, findings


def _manifest_keyset(man):
    """The 6.5 full pack-owned keyset: SOURCES union manifest-self union the derived ROOT/snippet
    (R10-3). Maps each path to its SOURCES sha256 (None for the manually added derived/self paths)."""
    srcs = {}
    for s in man.get("sources", []):
        if isinstance(s, dict) and isinstance(s.get("path"), str):
            srcs[s["path"]] = s.get("sha256")
    srcs[MANIFEST_REL] = None
    for d in DERIVED_PATHS:
        srcs[d] = None
    return srcs


def path_keyset_leg(prev_manifest, head_manifest):
    """6.5 PATH LAYOUT over the FULL keyset (R10-3). MOVED is paired only on a unique digest match; the
    outcome is MAJOR either way, so pairing affects reporting only."""
    events = []
    prev, head = _manifest_keyset(prev_manifest), _manifest_keyset(head_manifest)
    removed, added = sorted(prev.keys() - head.keys()), sorted(head.keys() - prev.keys())
    moved = set()
    for r in removed:
        digest = prev[r]
        twins = [a for a in added if digest is not None and head[a] == digest]
        if len(twins) == 1:
            moved.add((r, twins[0]))
    for r, a in sorted(moved):
        events.append(DeltaEvent("path", "{} -> {}".format(r, a), "moved", MAJOR))
    paired = {r for r, _a in moved} | {a for _r, a in moved}
    for r in removed:
        if r not in paired:
            events.append(DeltaEvent("path", r, "removed", MAJOR))
    for a in added:
        if a not in paired:
            events.append(DeltaEvent("path", a, "added", MINOR))
    return events, []


def ownership_leg(prev_classes, head_classes, rows, head_version):
    """6.5 OWNERSHIP: class transitions plus the absolute-minimum cap (never dispositionable). The cap
    reuses check_manifest._min_for, which returns (minimum-class, mode): an 'exact' path (manifest-self)
    must equal its minimum, any other must be at least as strong."""
    events, findings = [], []
    for path, cls in sorted(head_classes.items()):
        minimum, mode = _min_for(path)
        below = (cls != minimum) if mode == "exact" else \
            (CLASS_STRENGTH.get(cls, 0) < CLASS_STRENGTH.get(minimum, 0))
        if below:
            findings.append("{}: class {!r} is below its absolute minimum {!r}; no bump or row can "
                            "license this (4.2)".format(path, cls, minimum))
    for path in sorted(prev_classes.keys() & head_classes.keys()):
        old, new = prev_classes[path], head_classes[path]
        if old == new:
            continue
        row = take_row(rows, "class-change", path, head_version)
        weakening = CLASS_STRENGTH.get(new, 0) < CLASS_STRENGTH.get(old, 0)
        events.append(DeltaEvent("ownership", path, "weakened" if weakening else "strengthened",
                                 MAJOR if weakening else MINOR, "class-change"))
        if row is None:
            findings.append("{}: ownership class moved {} -> {} with no public class-change row "
                            "(6.5)".format(path, old, new))
        # Bind the class-change row's declared old/new classes to the OBSERVED transition (finding 3): a
        # row that names a different move than the one the tree actually makes does not license it.
        elif row.get("old-class") != old or row.get("new-class") != new:
            findings.append("{}: class-change row declares {} -> {} but the observed transition is {} -> {} "
                            "(the row must be bound to the observed change, 6.5)".format(
                                path, row.get("old-class"), row.get("new-class"), old, new))
    return events, findings


def order_leg(prev_bytes, head_bytes):
    """6.5 PRECEDENCE AND ORDERING: any change to the declared order record is MAJOR (6.2)."""
    if prev_bytes != head_bytes:
        return [DeltaEvent("order", ORDER_REL, "changed", MAJOR)], []
    return [], []


def renderer_diff(prev_decl, head_decl, rows, head_version):
    """6.5 RENDERER SEMANTICS declaration diff (pure). Any per-renderer change requires a public
    renderer-semantics row whose impact picks MAJOR (alters-obligations) or MINOR (byte-only); a
    rowless diff is a FAIL. Freshness (closure completeness) is enforced separately by the --check
    subprocess in renderer_leg, the single home of that recomputation."""
    events, findings = [], []
    prev = {r["renderer-id"]: r for r in prev_decl.get("renderer", []) if isinstance(r, dict)}
    head = {r["renderer-id"]: r for r in head_decl.get("renderer", []) if isinstance(r, dict)}
    for rid in sorted(prev.keys() | head.keys()):
        a, b = prev.get(rid), head.get(rid)
        if a == b:
            continue
        row = take_row(rows, "renderer-semantics", rid, head_version)
        if row is None:
            findings.append("renderer {!r}: declaration changed with no public renderer-semantics row "
                            "(6.5)".format(rid))
            events.append(DeltaEvent("renderer", rid, "changed", MAJOR))
        else:
            floor = MAJOR if row.get("impact") == "alters-obligations" else MINOR
            events.append(DeltaEvent("renderer", rid, "changed", floor, "renderer-semantics"))
    return events, findings


def _renderer_freshness(root, label):
    """Run gen_renderers.py --check against the tree at `root` (its own repo, resolved by the copied
    gen_renderers via repo_root). Drift or an incomplete closure is exit 2."""
    try:
        # BYTES capture (round-5 finding 3): a child emitting invalid UTF-8 must never crash the gate; only
        # the returncode is interpreted, and stderr is decoded with replacement for a diagnostic message.
        # -B: never write bytecode into the target tree (round-6 finding 1). gen_renderers imports the
        # tree's own renderer modules; a .pyc left in `root`/tools/__pycache__ could be captured by a
        # later index build and perturb a manifest SOURCES comparison.
        proc = subprocess.run([sys.executable, "-I", "-B", "-X", _child_pycache_x(),
                               str(root / "tools" / "gen_renderers.py"), "--check", "--root",
                               str(root)], capture_output=True)
    except OSError as exc:
        raise GateError("{} renderer freshness could not launch ({}); fail-closed".format(label, exc))
    if proc.returncode != 0:
        raise GateError("{} renderer declaration is stale or a closure is incomplete "
                        "(gen_renderers.py --check rc={})".format(label, proc.returncode))


def _index_materialized_tree(dest, label):
    """Build a throwaway git index over the raw-materialized tree at `dest` so the git-based validators
    (gen_manifest.git_tracked, and check_manifest through it) can enumerate its tracked path set. Only
    `git init` + a FORCE add are run: no commit and no identity are needed, and --force overrides any
    in-tree .gitignore so the index is EXACTLY the materialized (tracked) blob set. The env scrubs every
    GIT_* variable and pins the global/system config to os.devnull so host git config cannot steer it (test
    hermeticity). No clean/smudge filter is configured in this pack, and both validators read source bytes
    from the filesystem while git_tracked reads only the path set, so an index-side eol/text attribute
    cannot perturb their result. A launch or nonzero exit is cannot-evaluate (GateError, exit 2).
    Both calls pin core.fsmonitor=false and protocol.allow=never on the argv, with GIT_NO_LAZY_FETCH
    in the env (QA round-7 claude F3), matching the funnels: a repo-config fsmonitor hook must never
    run under a gate-driven git call, here included."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_NO_LAZY_FETCH"] = "1"
    for argv in (["init", "-q"], ["add", "--force", "-A"]):
        try:
            proc = subprocess.run(["git", "-C", str(dest), "-c", "core.fsmonitor=false",
                                   "-c", "protocol.allow=never", *argv],
                                  capture_output=True, env=env)
        except OSError as exc:
            raise GateError("{} manifest index: cannot launch git {} ({}); fail-closed".format(
                label, argv[0], exc))
        if proc.returncode != 0:
            raise GateError("{} manifest index: git {} failed rc={} ({}); fail-closed".format(
                label, argv[0], proc.returncode,
                proc.stderr.decode("utf-8", "replace").strip()[:200]))


def _predecessor_tree_checks(root, commit, prev_genesis):
    """Run the AUTHORITATIVE validators on the PREDECESSOR tree in non-genesis mode (round-4 findings 1, 2,
    4; round-8 finding 1). The tree is materialized from RAW blob bytes (git ls-tree + cat-file, NO checkout,
    so NO smudge/clean filter or gitattributes transformation can substitute old bytes; round-4 finding 1),
    then gen_renderers.py --check recomputes every closure and framed digest, check_clauses validates the
    full 7.2/7.3 inventory and register, and gen_manifest --check plus check_manifest validate the
    predecessor MANIFEST against its own tree (round-8 finding 1) so the path leg cannot trust an
    under-claimed keyset. Any nonzero is exit 2. Hermetic: a temp dir removed in finally; no worktree, no
    host mutation."""
    import shutil
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-release-delta-prev-"))
    dest = tmp / "tree"
    dest.mkdir()
    try:
        try:
            _release_schema.materialize_tree_raw(root, commit, dest)
        except SchemaError as exc:
            raise GateError(str(exc))
        # Build the throwaway index IMMEDIATELY after materialization, BEFORE any validator runs (round-6
        # finding 1, hermeticity): the validators below run child interpreters against modules INSIDE `dest`
        # (gen_renderers imports the tree's renderer closures), and with bytecode writing enabled a child
        # would deposit `tools/__pycache__/*.pyc` in `dest` that this `git add --force -A` (force overrides
        # the in-tree .gitignore) would then capture, so the predecessor manifest SOURCES set would disagree
        # with the real tree and the gate would fail-closed spuriously. Indexing the CLEAN materialized tree
        # first means no later-generated file can enter the index; the child interpreters are additionally
        # launched with -B so none is written at all (defence in depth).
        _index_materialized_tree(dest, "predecessor")
        _renderer_freshness(dest, "predecessor")
        clause_args = ["--root", str(dest)] + (["--genesis"] if prev_genesis else [])
        _validate_via_tool(dest, "check_clauses.py", clause_args,
                           "predecessor clause inventory / id-history structure (7.2/7.3)",
                           tools_root=dest)
        # round-8 finding 1: the path leg trusts the predecessor manifest keyset, so validate the
        # predecessor manifest against its OWN raw-materialized tree BEFORE it is trusted, running the
        # AUTHORITATIVE gen_manifest --check (fresh-regeneration drift) and check_manifest (exact SOURCES
        # set-equality plus raw re-hash) over the index built above. A manifest that omits a covered pack
        # path (an under-claimed removal that the pre-fix gate passed as PATCH) is now exit 2.
        _validate_via_tool(dest, "gen_manifest.py", ["--check", "--root", str(dest)],
                           "predecessor manifest freshness (gen_manifest --check)", tools_root=dest)
        _validate_via_tool(dest, "check_manifest.py", ["--root", str(dest)],
                           "predecessor manifest integrity (check_manifest SOURCES set-equality)",
                           tools_root=dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def renderer_leg(root, prev_decl, head_decl, rows, head_version):
    """6.5 RENDERER SEMANTICS. HEAD freshness: gen_renderers.py --check re-derives every closure and framed
    digest on the working tree; drift or an incomplete closure is exit 2. Then the anchored declaration diff
    (renderer_diff). Predecessor freshness is done by _predecessor_tree_checks (raw-materialized, round-4
    finding 1); the declaration SCHEMAS are strict-validated by the caller on both objects."""
    _renderer_freshness(root, "head")
    return renderer_diff(prev_decl, head_decl, rows, head_version)


# --- ownership class expansion (single-sourced through gen_manifest) ---------------------------------

def _classes_via_gen_manifest(co_root):
    """Compute the concern-1 per-path ownership classes for the tree at co_root, reusing gen_manifest's
    validated loader and classifier (single-source discipline; never a second parser)."""
    exclusions, release, namespace, _binary = gen_manifest.load_ownership(co_root)
    tracked = gen_manifest.git_tracked(co_root)
    classes, _excluded = gen_manifest.classify(tracked, exclusions, release, namespace, co_root)
    return classes


def _ownership_classes_head(root):
    try:
        return _classes_via_gen_manifest(root)
    except gen_manifest.GateError as exc:
        raise GateError("cannot classify HEAD ownership ({})".format(exc))


def _tracked_at(root, commit):
    """The tracked path set AND the OWN_OUTPUTS present at a git commit, read from `git ls-tree -r -z`
    PLUMBING (never a materialized worktree, so a checkout clean/smudge filter cannot alter the comparison;
    VC-4 QA #11). Fail-closed like gen_manifest.git_tracked: a symlink, a gitlink, an unknown mode, an
    empty tree, a duplicate, a case-fold or NFC/NFD collision, or a decode error is a cannot-evaluate, never
    a silent empty set."""
    import unicodedata
    # QA round-4 (codex R4-2 / claude m2): enumerated from RE-HASHED commit and tree objects
    # (walk_tree_verified), never from `git ls-tree` output a tampered object store could steer.
    try:
        listed = _release_schema.walk_tree_verified(root, commit)
    except SchemaError as exc:
        raise GateError("cannot list predecessor tree {} ({})".format(commit, exc))
    if not listed:
        raise GateError("predecessor tree {} lists no entries; an empty tree is never assumed".format(commit))
    paths, seen_fold, seen_nfc = set(), {}, {}
    for mode, _osha, path in listed:
        if mode == "120000":
            raise GateError("predecessor tracked symlink {!r}: symlinks are rejected in pack scope "
                            "(4.3)".format(path))
        if mode == "160000":
            raise GateError("predecessor tracked gitlink {!r}: submodules are unsupported".format(path))
        if mode not in ("100644", "100755"):
            raise GateError("predecessor tracked path {!r} has unsupported mode {}".format(path, mode))
        if path in paths:
            raise GateError("duplicate predecessor tracked path {!r}".format(path))
        fold, nfc = path.casefold(), unicodedata.normalize("NFC", path)
        if seen_fold.setdefault(fold, path) != path:
            raise GateError("predecessor case-fold collision: {!r} and {!r}".format(seen_fold[fold], path))
        if seen_nfc.setdefault(nfc, path) != path:
            raise GateError("predecessor unicode-normalization collision: {!r} and {!r}".format(
                seen_nfc[nfc], path))
        paths.add(path)
    outputs_present = {rel for rel in gen_manifest.OWN_OUTPUTS_REL if rel in paths}
    return paths, outputs_present


def _ownership_classes_at(root, commit):
    """Predecessor ownership classes from git PLUMBING only (VC-4 QA #11): the ownership map read via
    `git show <commit>:...` and validated by the SAME parser as the working tree (gen_manifest.parse_
    ownership), the tracked set and present outputs from `git ls-tree`, both fed to the pure classifier.
    No worktree is materialized, so no checkout filter runs and there is no cleanup to fail."""
    data = _show_toml(root, commit, OWNERSHIP_REL)
    try:
        exclusions, release, namespace, _binary = gen_manifest.parse_ownership(data)
    except gen_manifest.GateError as exc:
        raise GateError("predecessor ownership map at {} does not validate ({})".format(commit, exc))
    tracked, outputs_present = _tracked_at(root, commit)
    try:
        classes, _excluded = gen_manifest.classify(tracked, exclusions, release, namespace, root,
                                                   outputs_present=outputs_present)
    except gen_manifest.GateError as exc:
        raise GateError("cannot classify predecessor ownership at {} ({})".format(commit, exc))
    return classes


def _claimed_rank(prev_v, head_v):
    p, h = _parse(prev_v), _parse(head_v)
    if p is None or h is None or h <= p:
        raise GateError("head version {} does not increase over predecessor {}".format(head_v, prev_v))
    if h[0] > p[0]:
        return MAJOR
    if h[1] > p[1]:
        return MINOR
    return PATCH


# --- post-release head (RELEASING steps 6a/6b) -------------------------------------------------------

def _git_raw(root, args):
    """The binary-capture diff-tree/merge-base twin of the _git funnel, with the IDENTICAL substitution
    pins (--no-replace-objects plus GIT_NO_REPLACE_OBJECTS=1, GIT_GRAFT_FILE pinned to os.devnull, and
    the commit-graph cache disabled): a replace ref or graft that substitutes another tree or parent
    chain for HEAD or the release commit cannot alter what is compared (QA round-2; round-3 widened the
    same pins to EVERY git read via _git, so the two launches share one funnel policy; round-4 scrubbed
    the inherited GIT_* environment out of both). DISCLOSED RESIDUAL (QA round-4): diff-tree and
    merge-base read tree and intermediate commit objects internally without a per-object re-hash; the
    tree objects of BOTH endpoint commits are independently re-hashed by the verified reads and full
    materializations in the same run, so a forged tree still fails the run; and QA round-5 claude m1 added a parent-chain
    walk from HEAD to the release commit through RE-HASHED commit objects (_assert_head_ancestry), so
    a forged INTERMEDIATE ancestry commit object is refused too. merge-base and diff-tree stay
    refusal-capable conveniences, never the accepting authority for ancestry."""
    try:
        return subprocess.run(["git", "--no-replace-objects", "-c", "core.commitGraph=false",
                               "-c", "core.fsmonitor=false", "-c", "protocol.allow=never",
                               "-C", str(root), *args],
                              capture_output=True, env=_substitution_free_env())
    except OSError as exc:
        raise GateError("git is not available: {}".format(exc))


def _assert_head_ancestry(root, commit):
    """HEAD must DESCEND from the newest release row's commit_sha (QA round-2 codex 4 / claude m4),
    established by walking the parent chain from HEAD to the release commit EXCLUSIVELY through
    RE-HASHED commit objects (QA round-5 claude m1: git merge-base reads intermediate commit objects
    without verifying them, so a forged loose object for an intermediate commit could graft an
    unrelated head onto the release; the verified walk refuses it with a re-hash mismatch). The
    funneled merge-base runs first as a cheap refusal (and keeps the graft/replace pins exercised); it
    is never the accepting authority."""
    proc = _git_raw(root, ["merge-base", "--is-ancestor", commit, "HEAD"])
    if proc.returncode != 0:
        raise GateError("post-release head: the newest release row's commit_sha {} is not an ancestor of "
                        "HEAD; the head does not descend from the release it claims; fail-closed".format(
                            commit))
    head_oid = _rev_parse(root, "HEAD^{commit}")
    seen, frontier = set(), [head_oid]
    while frontier:
        oid = frontier.pop()
        if oid == commit:
            return
        if oid in seen:
            continue
        seen.add(oid)
        if len(seen) > 1048576:
            raise GateError("post-release head: the verified ancestry walk from HEAD exceeded 1048576 "
                            "commits without reaching {}; fail-closed".format(commit))
        try:
            otype, body = _release_schema.verified_object(root, oid)
        except SchemaError as exc:
            raise GateError("post-release head ancestry: {}".format(exc))
        if otype != "commit":
            raise GateError("post-release head ancestry: object {} is a {}, not a commit; "
                            "fail-closed".format(oid, otype))
        for line in body.split(b"\n\n", 1)[0].split(b"\n"):
            if line.startswith(b"parent "):
                try:
                    parent = line[7:].decode("ascii")
                except UnicodeDecodeError:
                    raise GateError("post-release head ancestry: commit {} carries a non-ASCII "
                                    "parent header; fail-closed".format(oid))
                if not _release_schema.OBJECTID_RE.fullmatch(parent):
                    raise GateError("post-release head ancestry: commit {} parent {!r} is not a "
                                    "full object id; fail-closed".format(oid, parent))
                frontier.append(parent)
    raise GateError("post-release head: the newest release row's commit_sha {} is not reachable from "
                    "HEAD through re-hashed commit objects; the head does not descend from the release "
                    "it claims; fail-closed".format(commit))


def _assert_only_post_release_paths(changed):
    """Every path differing between the release commit's tree and HEAD's tree must be a post-release record
    path; anything else is an undeclared release, exit 2."""
    extra = sorted(set(changed) - POST_RELEASE_PATHS)
    if extra:
        raise GateError("post-release head (the head version equals the newest release row) changes {} "
                        "beyond the post-release paths {}; declare a new version".format(
                            extra[:5], sorted(POST_RELEASE_PATHS)))


def _assert_tree_entry_regular(path, src_mode, dst_mode, status):
    """A differing tree entry must be an IN-PLACE content edit of a regular non-executable blob
    (100644 -> 100644, status M): an addition, a deletion, a type change (symlink/gitlink), or any
    executable-bit or other mode change is exit 2 on EVERY path, allowed or not (QA round-2 codex 3 /
    claude m2)."""
    if status != "M" or src_mode != "100644" or dst_mode != "100644":
        raise GateError("post-release head: {!r} must stay an in-place edit of a regular non-executable "
                        "blob (100644 -> 100644, status M) relative to the release commit; got status "
                        "{} mode {} -> {}; fail-closed".format(path, status, src_mode, dst_mode))


def _head_tree_diff(root, commit):
    """Every path whose RAW HEAD TREE entry differs from `commit`'s tree, read entirely from the recorded
    tree objects (git diff-tree -r -z --no-renames with replacement objects and grafts disabled), never
    from the working tree, the index, or any path piped line-wise: no smudge/clean filter, checkout
    conversion, or C-style path quoting (a name starting with a double quote) can hide a committed edit
    (QA round-2 codex 1 / claude B1, m1). Every differing path must be a post-release record path AND an
    in-place 100644 -> 100644 modification (_assert_only_post_release_paths, _assert_tree_entry_regular).
    Returns the changed path set."""
    proc = _git_raw(root, ["diff-tree", "-r", "-z", "--no-renames", commit, "HEAD"])
    if proc.returncode != 0:
        raise GateError("git diff-tree {}..HEAD failed: cannot compare the post-release head".format(commit))
    fields = proc.stdout.split(b"\0")
    changed = {}
    i = 0
    while i < len(fields) and fields[i]:
        try:
            meta = fields[i].decode("ascii")
            path = fields[i + 1].decode("utf-8")
            src_mode, dst_mode, _src_oid, _dst_oid, status = meta.lstrip(":").split(" ")
        except (IndexError, UnicodeDecodeError, ValueError) as exc:
            raise GateError("malformed git diff-tree record ({}); fail-closed".format(exc))
        changed[path] = (src_mode, dst_mode, status)
        i += 2
    _assert_only_post_release_paths(set(changed))
    for path in sorted(changed):
        src_mode, dst_mode, status = changed[path]
        _assert_tree_entry_regular(path, src_mode, dst_mode, status)
    return set(changed)


# The drift advisory's per-file size cap (QA round-6 codex major 4): a tracked checkout file whose
# fstat size exceeds this many bytes is NEVER read; it is reported as UNCHECKED instead, and the
# verdict is unaffected either way (the advisory never gates).
_DRIFT_ADVISORY_SIZE_CAP = 100 * 1024 * 1024


def _drift_entry_state(root, path_b, committed_symlink, want):
    """Compare ONE tracked path's checkout bytes against HEAD's blob id `want` for the advisory,
    following NO symlink anywhere on the path (QA round-6 codex majors 3/4 / claude 2) and refusing
    empty, `.` and `..` path components before any open (QA round-7 claude F1, classified as drift:
    O_NOFOLLOW stops symlinks, never a real dot-dot walk out of the root): every
    directory component is opened from a root descriptor with O_NOFOLLOW | O_DIRECTORY (a
    symlinked parent is ELOOP, classified as drift, so no byte outside the root is ever read); the
    final file is opened with O_NOFOLLOW | O_NONBLOCK (a FIFO or device swapped in cannot block
    the open); the opened DESCRIPTOR is fstat'ed and must be a regular file (the check binds to
    what was actually opened, closing the lstat-then-read race); the hash streams in bounded 1 MiB
    chunks (never a whole-file read); and a regular file over _DRIFT_ADVISORY_SIZE_CAP bytes
    (100 MiB) is never read at all. A committed-symlink entry is compared via readlink on the
    directory descriptor, with no open. Returns "match", "drift", or "unchecked"; OSError,
    ValueError, and MemoryError all classify as drift (the advisory never raises)."""
    import hashlib
    import stat
    algo = hashlib.sha1 if len(want) == 40 else hashlib.sha256
    parts = path_b.split(b"/")
    if any(comp in (b"", b".", b"..") for comp in parts):
        # QA round-7 claude F1: O_NOFOLLOW stops only symlinks, so a `..` component would walk the
        # descriptor chain OUT of the root through real directories; refuse empty, `.` and `..`
        # components before any open (classified as drift, the advisory's refusal state).
        return "drift"
    fds = []
    try:
        fds.append(os.open(str(root), os.O_RDONLY | os.O_DIRECTORY))
        for comp in parts[:-1]:
            fds.append(os.open(comp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fds[-1]))
        name = parts[-1]
        if committed_symlink:
            data = os.readlink(name, dir_fd=fds[-1])
            digest = algo(b"blob " + str(len(data)).encode("ascii") + b"\x00" + data).hexdigest()
            return "match" if digest == want else "drift"
        fds.append(os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fds[-1]))
        fd = fds[-1]
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return "drift"
        if st.st_size > _DRIFT_ADVISORY_SIZE_CAP:
            return "unchecked"
        h = algo(b"blob " + str(st.st_size).encode("ascii") + b"\x00")
        remaining = st.st_size
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 1 << 20))
            if not chunk:
                return "drift"  # the file shrank mid-read: not HEAD's bytes
            h.update(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            return "drift"  # the file grew mid-read
        return "match" if h.hexdigest() == want else "drift"
    except (OSError, ValueError, MemoryError):
        return "drift"
    finally:
        # Every descriptor this function opens enters `fds` at the open itself and is closed
        # EXACTLY here, once, on every path: no close-then-rebind along the walk and no descriptor
        # ever closed twice (the #378 close sweep's REBIND/TRY shapes both stay empty).
        for fd_ in fds:
            try:
                os.close(fd_)
            except OSError:
                pass


def _warn_checkout_drift(root):
    """ADVISORY ONLY, never part of the verdict (QA round-3 codex R3-3). The post-release branch judges
    HEAD's COMMITTED objects exclusively: every byte it reads comes through the substitution-free funnel
    over committed objects, and the manifest freshness/integrity legs run over a raw materialization of
    HEAD's committed tree, so the index, the working tree, checkout filters, and untracked files cannot
    change the verdict. This helper only prints a stderr note when a tracked file's checkout bytes do
    not re-hash to HEAD's blob id (computed HERE in Python from `git ls-tree -r -z HEAD`), when `git
    diff-index --cached --name-only -z HEAD` reports a staged change, or when `git ls-files -z --others
    --exclude-standard` reports untracked files, as a courtesy to a maintainer whose checkout drifted
    from the revision the verdict certifies. It is explicitly NOT a cleanliness guarantee, which
    is exactly why it does not gate: it cannot see an index entry carrying assume-unchanged or
    skip-worktree, an executable-bit change under core.filemode=false, or an untracked file hidden by
    .git/info/exclude or a .gitignore (codex R3-3's reproductions), and it compares raw bytes, so a
    checkout whose smudge filter or line-ending conversion rewrote a file is reported as drift. A git
    failure is likewise only a note. Content comparison is delegated to _drift_entry_state (QA
    round-6 codex majors 3/4 / claude 2): a no-follow descriptor walk from the root, an fstat-bound
    regular-file requirement on the opened descriptor, bounded chunked hashing, and tracked files
    over _DRIFT_ADVISORY_SIZE_CAP bytes (100 MiB) reported as UNCHECKED rather than read.
    WHY NO WORKTREE-COMPARING GIT CALL (QA round 5, reproduction (c) and its siblings): `git status`
    refreshes the index, so it RUNS repo-configured code mid-gate: the core.fsmonitor program, the clean
    filter of any stat-dirty tracked file (a filter.<driver>.clean named by .gitattributes or
    .git/info/attributes), and the post-index-change hook when it writes the refreshed index; -c
    core.fsmonitor=false stops only the first. `git diff-index HEAD` (no --cached) also runs the clean
    filter on a racily clean file. So git here only lists objects and paths (ls-tree, diff-index
    --cached, ls-files --others), none of which reads working-tree content, and the content comparison
    is done in Python (the self-test case "(R5 filter/hook)" plants all three and fails if any runs)."""
    proc_t = _git(root, ["ls-tree", "-r", "-z", "--full-tree", "HEAD"], binary=True)
    proc_s = _git(root, ["diff-index", "--cached", "--name-only", "-z", "HEAD"], binary=True)
    proc_u = _git(root, ["ls-files", "-z", "--others", "--exclude-standard"], binary=True)
    if proc_t.returncode != 0 or proc_s.returncode != 0 or proc_u.returncode != 0:
        print("post-release advisory: a git listing failed; checkout drift from the certified revision is "
              "unknown (the verdict reads committed objects only)", file=sys.stderr)
        return
    entries = set(rec.decode("utf-8", "replace")
                  for rec in (proc_s.stdout + b"\0" + proc_u.stdout).split(b"\0") if rec)
    unchecked = []
    for rec in proc_t.stdout.split(b"\0"):
        meta, tab, path_b = rec.partition(b"\t")
        fields = meta.split(b" ")
        if not tab or len(fields) != 3 or fields[1] != b"blob":
            continue
        path_s = path_b.decode("utf-8", "replace")
        want = fields[2].decode("ascii", "replace")
        state = _drift_entry_state(root, path_b, fields[0] == b"120000", want)
        if state == "drift":
            entries.add(path_s)
        elif state == "unchecked":
            unchecked.append(path_s)
    entries = sorted(entries)
    if entries:
        print("post-release advisory: the index, working tree, or untracked files differ from HEAD "
              "({} entr{}, e.g. {}); the verdict certifies the COMMITTED revision only, never this "
              "checkout".format(len(entries), "y" if len(entries) == 1 else "ies", entries[:5]),
              file=sys.stderr)
    if unchecked:
        print("post-release advisory: {} tracked file(s) over the {}-byte advisory size cap were "
              "left UNCHECKED for drift (e.g. {}); the verdict reads committed objects only, never "
              "this checkout".format(len(unchecked), _DRIFT_ADVISORY_SIZE_CAP, sorted(unchecked)[:5]),
              file=sys.stderr)


def _post_release_changelog_ok(prev_bytes, head_bytes, version):
    """True when the head changelog equals the predecessor's byte for byte, or differs ONLY by one added
    line `tag = "v<version>"` that lands in the newest [[release]] table as that release's own key
    (RELEASING step 6b: the line goes immediately after that release's `version` line, BEFORE any sub-table
    header such as [release.artifacts]). The LINE check (one added line whose removal restores the
    predecessor bytes) runs first; _changelog_tag_toml_ok then proves the line landed as the NEWEST
    release's own `tag` key. Any other edit, including a reformat, a comment, another key, or a different
    tag value, is False."""
    if head_bytes == prev_bytes:
        return True
    want = 'tag = "v{}"'.format(version)
    lines = head_bytes.decode("utf-8").splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.rstrip("\r\n") != want or "".join(lines[:i] + lines[i + 1:]).encode("utf-8") != prev_bytes:
            continue
        return _changelog_tag_toml_ok(prev_bytes, head_bytes, version)
    return False


def _changelog_tag_toml_ok(prev_bytes, head_bytes, version):
    """The PARSED proof behind the step 6b line check: the newest [[release]] table itself carries tag ==
    "v" + version and, with that one key removed, the head record EQUALS the predecessor's. A textually
    perfect tag line placed in an OLDER release's table, or inside a sub-table such as [release.artifacts],
    passes the line check and is rejected ONLY here (QA round-2: the TOML-only catch), so this is factored
    out for the self-test to discriminate."""
    head, prev = tomllib.loads(head_bytes.decode("utf-8")), tomllib.loads(prev_bytes.decode("utf-8"))
    newest = dict(head["release"][-1])
    if newest.pop("tag", None) != "v" + version:
        return False
    head["release"][-1] = newest
    return head == prev


def _assert_release_rows_prefix(prev_releases, release_rows, head_version):
    """HEAD's releases record must be the release commit's rows plus EXACTLY the newest row: an extra,
    altered, or reordered prior row is a rewrite of attested history, exit 2 (QA round-2 claude M1)."""
    if list(prev_releases) != list(release_rows[:-1]):
        raise GateError("post-release head: {} is not the predecessor's rows plus exactly the newest row "
                        "{}".format(RELEASES_REL, head_version))


def _assert_newest_manifest_binding(newest_manifest, head_version):
    """The release commit's own manifest must record the newest row's version (QA round-2 claude M1/M4):
    an anchored release whose manifest disagrees with its release-order row is inconsistent, exit 2."""
    if newest_manifest.get("release-version") != head_version:
        raise GateError("predecessor manifest release-version {!r} != release-order row version {!r}; the "
                        "anchored predecessor is inconsistent".format(
                            newest_manifest.get("release-version"), head_version))


def _gate_module_files():
    """Every pack-tree file this gate PROCESS executes or imports, enumerated from sys.modules (plus
    this file itself), never a hand list (QA round-4 codex R4-1 / claude m3): any module whose resolved
    __file__ lies under the pack root that owns this gate is included, so a new import is covered the
    moment it lands. Returns (pack root, sorted file paths)."""
    # The pack root is the parent of this file's tools directory in BOTH the checkout and the
    # stage-2 materialized tree (which carries no .git for repo_root() to find; QA round 5).
    pack = Path(__file__).resolve().parent.parent
    files = {Path(__file__).resolve()}
    for mod in list(sys.modules.values()):
        f = getattr(mod, "__file__", None)
        if not isinstance(f, str):
            continue
        try:
            q = Path(f).resolve()
        except OSError:
            continue
        if q == pack or pack in q.parents:
            files.add(q)
    return pack, sorted(files)


def _assert_gate_code_matches_head(root, head_oid):
    """POST-RELEASE ONLY (QA round-4 codex R4-1 / claude m3): the gate executes from the CHECKOUT, so
    an edited checkout module (this file, _release_schema, _gen_common, gen_manifest, check_manifest, or
    any other import) could otherwise turn a refusal into exit 0 while the advisory merely mentions the
    drift. Compare every file this process executes or imports from the pack tree (enumerated from
    sys.modules, never a hand list) byte-for-byte against the SAME path\x27s blob in HEAD\x27s committed
    tree through the re-hashed funnel, and exit 2 on any difference or any such file missing from HEAD.
    Refusal-only: a checkout difference here can never accept a state; it can only refuse.

    DISCLOSED RESIDUAL: this check is itself checkout code. An attacker who controls the gate\x27s own
    LAUNCH (the interpreter binary, PYTHONPATH / sitecustomize, a wrapping process, or a pre-import
    patch of this very function) can run arbitrary code and fabricate any verdict; no in-process
    self-comparison can defend a process against its own launcher. What this check closes is the
    demonstrated R4-1 class: an in-place checkout edit changing the verdict of an otherwise honest
    launch. CI runs the gate on a fresh clone, where the checkout and HEAD coincide. QA round 5
    (D-397-REEXEC-FROM-COMMITTED): under the two-stage launch this check runs INSIDE the committed
    copy (stage 2), where it pins the stage-2 materialized modules to the judged HEAD; the
    launch-control residual concentrates entirely in the stage-1 launched file."""
    pack, files = _gate_module_files()
    for q in files:
        rel = q.relative_to(pack).as_posix()
        try:
            committed = _release_schema.verified_path_blob(root, head_oid, rel)
        except SchemaError as exc:
            raise GateError("post-release head: the gate\x27s own code file {} is not readable from "
                            "HEAD\x27s committed tree ({}); every module the gate executes must be "
                            "committed at HEAD; fail-closed".format(rel, exc))
        try:
            disk = q.read_bytes()
        except OSError as exc:
            raise GateError("post-release head: cannot read the gate\x27s own code file {} ({}); "
                            "fail-closed".format(rel, exc))
        if disk != committed:
            raise GateError("post-release head: the gate\x27s own code file {} differs from HEAD\x27s "
                            "committed tree; on this branch the checkout may not change the verdict; "
                            "commit or revert the edit; fail-closed".format(rel))


def _assert_profiles_absent_committed(root, head_oid, release_commit):
    """The 2.6 profiles/groups arm-or-fail-closed decision on the post-release branch is taken from the
    COMMITTED trees, never the checkout (QA round-4 claude M1): deleting the checkout copy of a shipped
    artifact must never turn the fail-closed refusal into POST-RELEASE exit 0. ANY committed tree entry
    at PROFILES_REL (a file, an executable, a symlink, a gitlink, or a tree), in HEAD or in the newest
    release commit, resolved through re-hashed objects only, means the artifact has shipped -> exit 2.
    The run() lstat on the checkout stays as an ADDITIONAL refusal only (an artifact present in the
    checkout but not yet committed also fail-closes); it is never the deciding input on this branch."""
    for commit in (head_oid, release_commit):
        try:
            shipped = _release_schema.verified_tree_entry_exists(root, commit, PROFILES_REL)
        except SchemaError as exc:
            raise GateError(str(exc))
        if shipped:
            raise GateError("profiles/groups artifact {} has shipped (present in the COMMITTED tree at "
                            "{}) but its delta leg is not implemented; fail-closed until the diff lands "
                            "(2.6; QA round-4 claude M1: the committed trees decide, the checkout lstat "
                            "is only an additional refusal)".format(PROFILES_REL, commit))


def _post_release_head(root, prev_row, release_rows, head_version):
    """The head version EQUALS the newest attested row (the RELEASING step 6a attestation commit, main
    after it merges, and the step 6b tag-key commit). THE ONE AND ONLY RELAXATION over the gate the release
    faced BEFORE its row was appended is the version-increase requirement (QA round-2 claude B1): every
    other rejection is preserved, and the whole branch binds to the HEAD REVISION, never to working-tree
    bytes. Coverage of what the pre-row gate rejected and where each case is still rejected:

      pre-row rejection                            | post-release enforcement
      -------------------------------------------- | ----------------------------------------------------
      any change beyond the release commit         | raw tree diff HEAD vs commit_sha (_head_tree_diff,
                                                   | replace objects/grafts disabled): POST_RELEASE_PATHS
                                                   | only, with no path-quoting or filter hazard
      an addition, deletion, type or mode change   | every differing entry must stay 100644 -> 100644
                                                   | status M (_assert_tree_entry_regular), on every path
      a staged, dirty, or untracked state          | NOT JUDGED: the verdict binds to HEAD's COMMITTED
      presented as the head                        | objects only; checkout drift is a stderr ADVISORY
                                                   | (_warn_checkout_drift, never the verdict), and CI
                                                   | runs this gate on a fresh checkout
      a head not descending from the release       | git merge-base --is-ancestor commit_sha HEAD, raw
                                                   | (_assert_head_ancestry)
      the release's OWN whole-surface delta: the   | _release_checks_at_commit replays the full delta from
      version increase over ITS predecessor, the   | release_rows[-2] against the newest row's tagged tree
      strict record schemas, both anchored tree    | with the row's version as head; a finding exits 1 and
      checks, every classification leg, the        | malformed input exits 2, exactly as before the row
      disposition sweep, the claimed-bump floor    | existed
      a malformed FIRST release (the zero-row      | with exactly one row, _release_checks_at_commit runs
      genesis strict validation)                   | the same genesis checks over the tagged tree
      a stale or inconsistent HEAD manifest        | gen_manifest --check + check_manifest over a RAW
                                                   | materialization of HEAD's COMMITTED tree
                                                   | (_head_committed_manifest_integrity), plus the
                                                   | HEAD-byte version bindings below
      a changelog edit beyond the newest tag key   | _post_release_changelog_ok over HEAD's COMMITTED bytes
      a rewritten releases record                  | HEAD's committed rows == the release commit's rows
                                                   | plus exactly the newest row (_assert_release_rows_prefix)

    Every byte this branch judges comes from HEAD's or the release commit's COMMITTED objects through
    the substitution-free funnel (_git/_git_raw and the raw materialization: replace objects, grafts,
    and the commit-graph cache all disabled), and the manifest freshness/integrity legs run over a raw
    materialization of HEAD's committed tree, NEVER the working tree (QA round-3 codex R3-1/R3-2/R3-3,
    claude F1/F2/F3). The working tree, the index and its flags (assume-unchanged, skip-worktree,
    core.filemode=false), checkout clean/smudge filters, and exclude rules therefore cannot make a bad
    committed state print POST-RELEASE. QA round-5 (claude M1): run() ROUTES into this branch
    from HEAD's COMMITTED records alone, and this branch reads NO checkout record at all (the drift
    advisory prints, never gates), so a divergent checkout can neither prevent this branch from
    running, steer it, nor be certified by it; the committed copies are still re-read below and
    cross-checked against the routing reads as defence in depth.

    EXACTNESS of the coverage table (QA round-3 claude F5): "rejected" above means rejected at the
    RECORD level, not the byte level. Exactly three byte-level variants that the pre-row gate rejected
    (it rejected EVERY same-version head outright) are ACCEPTED here because the records they encode
    are identical and gen_manifest/check_manifest bind the exact committed bytes: (1) a committed
    releases.toml whose bytes differ from the release commit's rows plus the newest row only in TOML
    comments or formatting (rows are compared PARSED, _assert_release_rows_prefix); (2) a tag line
    terminated CRLF inside an LF changelog (the one-added-line check strips the terminator; the parsed
    proof is unaffected; reachable only through plumbing, because the generated .gitattributes pins the
    changelog to eol=lf and porcelain checkin normalizes the CRLF away); (3) the tag key placed elsewhere WITHIN the newest [[release]] table than
    immediately after its version line (the enforced property is the parsed one: the newest table
    itself carries tag == "v" + version and nothing else changed; RELEASING step 6b's placement advice
    exists so a hand-added line does not land inside a sub-table). Each variant is locked by a
    self-test case.

    QA round-4 (codex R4-1/R4-2/R4-3, claude M1/m2/m3): nothing in the checkout or the inherited
    environment may turn a refusal into exit 0 on this branch; a checkout or environment difference may
    only REFUSE (exit 2) or leave the verdict unchanged. Concretely: every module this process executes
    or imports from the pack tree must byte-equal HEAD\x27s committed copy
    (_assert_gate_code_matches_head, with its disclosed launch-control residual); the validators judging
    a raw materialization are the materialized tree\x27s OWN committed copies (tools_root in
    _validate_via_tool); the profiles arm-or-fail-closed decision is taken from the COMMITTED trees
    (_assert_profiles_absent_committed), the run() checkout lstat remaining an additional refusal only;
    and every funneled read scrubs the inherited GIT_* environment and re-hashes each commit, tree, and
    blob it consumes against the requesting id (_release_schema), so a decoy GIT_DIR, an
    object-directory overlay, or an overwritten loose object cannot substitute a judged byte.

    QA round-5 (D-397-REEXEC-FROM-COMMITTED): a plain launch re-executes the COMMITTED copy of this
    gate from a verified materialization of HEAD (stage 1), so no checkout Python file beyond the
    launched file itself (an untracked shadow module, a crafted __pycache__ .pyc, a planted sibling)
    participates in this branch; every child validator launches under -I -B with a fresh pycache
    prefix (_child_pycache_x); and the launched file remains the disclosed residual, because
    nothing can vet its own launch.
    Returns (sorted changed paths, replayed-release findings)."""
    commit = prev_row["commit_sha"]
    head_oid = _rev_parse(root, "HEAD^{commit}")
    # QA round-4 FIRST (codex R4-1 / claude m3): before any other judgment, the code this process runs
    # must byte-equal HEAD\x27s committed copy of it, else nothing below is trustworthy.
    _assert_gate_code_matches_head(root, head_oid)
    _assert_head_ancestry(root, commit)
    changed = _head_tree_diff(root, commit)
    # QA round-4 (claude M1): the profiles arm-or-fail-closed decision from the COMMITTED trees.
    _assert_profiles_absent_committed(root, head_oid, commit)
    _warn_checkout_drift(root)
    head_rows = _strict(_release_schema.strict_releases, _show_toml(root, head_oid, RELEASES_REL),
                        "HEAD " + RELEASES_REL)
    if head_rows != release_rows:
        raise GateError("post-release head: the committed {} at HEAD disagrees with the loaded record; "
                        "fail-closed".format(RELEASES_REL))
    head_cl_bytes = _show(root, head_oid, CHANGELOG_REL)
    try:
        head_cl = tomllib.loads(head_cl_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        raise GateError("HEAD {} does not parse: {}".format(CHANGELOG_REL, exc))
    cl_rel = head_cl.get("release", [])
    if (not isinstance(cl_rel, list) or not cl_rel or not isinstance(cl_rel[-1], dict)
            or cl_rel[-1].get("version") != head_version):
        raise GateError("post-release head: the committed {} head version disagrees with the loaded "
                        "record; fail-closed".format(CHANGELOG_REL))
    head_man = _show_toml(root, head_oid, MANIFEST_REL)
    _strict(_release_schema.strict_manifest, head_man, "HEAD " + MANIFEST_REL)
    if head_man.get("release-version") != head_version:
        raise GateError("HEAD manifest release-version {!r} != the newest release row version {!r}; "
                        "fail-closed".format(head_man.get("release-version"), head_version))
    prev_releases = _strict(_release_schema.strict_releases, _show_toml(root, commit, RELEASES_REL),
                            "predecessor " + RELEASES_REL)
    _assert_release_rows_prefix(prev_releases, release_rows, head_version)
    newest_manifest = _show_toml(root, commit, MANIFEST_REL)
    _strict(_release_schema.strict_manifest, newest_manifest, "predecessor " + MANIFEST_REL)
    _assert_newest_manifest_binding(newest_manifest, head_version)
    if CHANGELOG_REL in changed and not _post_release_changelog_ok(
            _show(root, commit, CHANGELOG_REL), head_cl_bytes, head_version):
        raise GateError("post-release head: {} differs from the release commit by more than the newest "
                        "release's tag = \"v{}\" key; declare a new version".format(CHANGELOG_REL,
                                                                                    head_version))
    _head_committed_manifest_integrity(root, head_oid)
    findings = _release_checks_at_commit(root, release_rows, newest_manifest)
    return sorted(changed), findings


def _release_checks_at_commit(root, release_rows, newest_manifest):
    """THE PRESERVED RELEASE CHECK (QA round-2 claude B1): the post-release head must never certify a
    release the gate would have rejected before its row was appended, so the release the newest row attests
    is re-checked here against ITS OWN predecessor, over committed objects only.

    - With two or more rows, the whole-surface delta is replayed from release_rows[-2] against the newest
      row's tagged commit tree (equal to HEAD outside POST_RELEASE_PATHS, enforced by the caller) with the
      newest row's version as the head version: predecessor anchoring, the version-increase/claimed rank,
      every strict record validation on BOTH objects, both authoritative raw-tree checks, every
      classification leg, the disposition sweep, and the claimed-bump floor. Findings are returned for
      exit 1; malformed input raises GateError (exit 2), exactly the pre-row verdicts.
    - With exactly one row, the newest row attests the FIRST release, whose pre-row state was the zero-row
      genesis path, so the SAME genesis validation runs over the tagged tree: manifest genesis = true, the
      strict record schemas, renderer-target existence, and the authoritative validators
      (check_clauses --genesis, gen_renderers --check, gen_manifest --check + check_manifest) over the
      RAW-materialized tree (_predecessor_tree_checks).

    Head-side records are read from the newest row's COMMIT (git show / raw materialization), never the
    working tree, and every read goes through the substitution-free funnel (replace objects, grafts, and
    the commit-graph cache disabled; QA round-3 claude F1). Returns the findings list (empty when
    clean)."""
    newest = release_rows[-1]
    n_commit, version = newest["commit_sha"], newest["version"]
    n_rows = _validate_dispositions_data(_show_toml(root, n_commit, DISPOSITIONS_REL),
                                         "release commit " + DISPOSITIONS_REL)
    n_genesis = newest_manifest.get("genesis") is True
    if len(release_rows) == 1:
        if not n_genesis:
            raise GateError("the first attested release's commit " + n_commit + " does not declare "
                            "genesis = true in its manifest while its own releases record is zero-row "
                            "(2.5); fail-closed")
        _strict(_release_schema.strict_order, _show_toml(root, n_commit, ORDER_REL),
                "release commit " + ORDER_REL)
        n_renderers = _show_toml(root, n_commit, RENDERERS_REL)
        _strict(_release_schema.strict_renderers, n_renderers, "release commit " + RENDERERS_REL)
        _strict(_release_schema.strict_clause_inventory, _show_toml(root, n_commit, CLAUSES_REL),
                "release commit " + CLAUSES_REL)
        _strict(_release_schema.strict_id_history, _show_toml(root, n_commit, IDHISTORY_REL),
                "release commit " + IDHISTORY_REL)
        _assert_renderer_targets_declared(n_rows, _renderer_ids(n_renderers))
        _predecessor_tree_checks(root, n_commit, True)
        return []
    prev_row = release_rows[-2]
    _anchor_predecessor(root, prev_row)
    p_commit = prev_row["commit_sha"]
    claimed = _claimed_rank(prev_row["version"], version)
    prev_inv = _strict(_release_schema.strict_clause_inventory, _show_toml(root, p_commit, CLAUSES_REL),
                       "predecessor " + CLAUSES_REL)
    head_inv = _strict(_release_schema.strict_clause_inventory, _show_toml(root, n_commit, CLAUSES_REL),
                       "release commit " + CLAUSES_REL)
    register = _strict(_release_schema.strict_id_history, _show_toml(root, n_commit, IDHISTORY_REL),
                       "release commit " + IDHISTORY_REL)
    _strict(_release_schema.strict_releases, _show_toml(root, p_commit, RELEASES_REL),
            "predecessor " + RELEASES_REL)
    _strict(_release_schema.strict_id_history, _show_toml(root, p_commit, IDHISTORY_REL),
            "predecessor " + IDHISTORY_REL)
    _strict(_release_schema.strict_order, _show_toml(root, p_commit, ORDER_REL),
            "predecessor " + ORDER_REL)
    _strict(_release_schema.strict_order, _show_toml(root, n_commit, ORDER_REL),
            "release commit " + ORDER_REL)
    prev_disp = _validate_dispositions_data(_show_toml(root, p_commit, DISPOSITIONS_REL),
                                            "predecessor " + DISPOSITIONS_REL)
    prev_manifest = _show_toml(root, p_commit, MANIFEST_REL)
    _strict(_release_schema.strict_manifest, prev_manifest, "predecessor " + MANIFEST_REL)
    if prev_manifest.get("release-version") != prev_row["version"]:
        raise GateError("predecessor manifest release-version {!r} != release-order row version {!r}; the "
                        "anchored predecessor is inconsistent".format(
                            prev_manifest.get("release-version"), prev_row["version"]))
    prev_renderers = _show_toml(root, p_commit, RENDERERS_REL)
    head_renderers = _show_toml(root, n_commit, RENDERERS_REL)
    _strict(_release_schema.strict_renderers, prev_renderers, "predecessor " + RENDERERS_REL)
    _strict(_release_schema.strict_renderers, head_renderers, "release commit " + RENDERERS_REL)
    _assert_renderer_targets_declared(n_rows, _nongenesis_renderer_target_scope(prev_renderers,
                                                                                head_renderers))
    _assert_renderer_targets_declared(prev_disp, _renderer_ids(prev_renderers))
    _predecessor_tree_checks(root, p_commit, prev_manifest.get("genesis") is True)
    # The release commit's OWN tree is held to the SAME authoritative validators the live head gets
    # (check_clauses, gen_renderers --check, gen_manifest --check + check_manifest), raw-materialized.
    _predecessor_tree_checks(root, n_commit, n_genesis)
    events, findings = [], []
    for ev, fs in (clause_text_leg(prev_inv, head_inv, register, n_rows, version),
                   path_keyset_leg(prev_manifest, newest_manifest),
                   ownership_leg(_ownership_classes_at(root, p_commit),
                                 _ownership_classes_at(root, n_commit), n_rows, version),
                   order_leg(_show(root, p_commit, ORDER_REL), _show(root, n_commit, ORDER_REL)),
                   renderer_diff(prev_renderers, head_renderers, n_rows, version)):
        events += ev
        findings += fs
    floor = max((e.floor for e in events), default=PATCH)
    findings += _disposition_findings(events, n_rows, version)
    if claimed < floor:
        findings.append("claimed bump {} ({} -> {}) is below the required {} floor".format(
            BUMP_NAME[claimed], prev_row["version"], version, BUMP_NAME[floor]))
    return findings


def _disposition_findings(events, rows, head_version):
    """The mis-dated and unconsumed disposition sweep over classified events (round-3 finding 3), shared by
    the live delta in run() and the post-release replay (_release_checks_at_commit) so they cannot drift.
    The subjects of the changes DETECTED this release are grouped by the disposition kind that would target
    them, so a mis-dated row for a current change is caught."""
    findings = []
    dispositioned_clause_changes = ("behaviour-neutral", "strengthened", "default-correction",
                                    "undispositioned-text-change")
    clause_subjects = set(e.subject for e in events
                          if e.surface == "clause" and e.change in dispositioned_clause_changes)
    ownership_subjects = set(e.subject for e in events
                             if e.surface == "ownership" and e.change in ("weakened", "strengthened"))
    renderer_subjects = set(e.subject for e in events if e.surface == "renderer")
    subjects_by_kind = dict((("behaviour-neutral", clause_subjects), ("strengthened", clause_subjects),
                             ("default-correction", clause_subjects), ("class-change", ownership_subjects),
                             ("renderer-semantics", renderer_subjects)))
    for r in rows:
        if r["_consumed"]:
            continue
        if r["id"] in subjects_by_kind.get(r["kind"], set()) and r["release"] != head_version:
            # A row that names a change detected THIS release but is dated for another release: it was
            # never consumed (take_row is exact-release) and the head_version sweep cannot see it, so a
            # mis-dated disposition could otherwise hide behind a coincidentally-adequate bump.
            findings.append("disposition row (kind {} id {}) is dated {}, not the release under build "
                            "{}, but names a change detected in this release (mis-dated "
                            "disposition)".format(r["kind"], r["id"], r["release"], head_version))
        elif r["release"] == head_version:
            findings.append("disposition row (kind {} id {}) at {} matches no detected change "
                            "(unconsumed)".format(r["kind"], r["id"], head_version))
    return findings


# --- genesis structural validation (single-home validators, never re-implemented) -------------------

_CHILD_PYCACHE = [None]


def _child_pycache_x():
    """The `-X pycache_prefix=<dir>` value for EVERY Python child this gate launches (QA round-5 claude
    M2): a process-lifetime FRESH, empty pycache directory so a crafted pre-existing __pycache__ .pyc
    cannot be READ behind a byte-identical .py (-B alone only stops WRITES; QA round-5 reproduction (b)).
    The children launch with a LITERAL sys.executable head and inline -I -B flags (so the
    launcher-isolation and maintenance-pin scans resolve the interpreter and its isolation statically):
    -I makes the child ignore PYTHONPATH, the user site and sitecustomize (round 5 reproduced a
    sitecustomize reached through an inherited PYTHONPATH flipping a child validator refusal to exit 0
    even though the gate ran under -I), and -B writes no bytecode into any judged tree."""
    if _CHILD_PYCACHE[0] is None:
        import atexit
        import shutil
        import tempfile
        d = tempfile.mkdtemp(prefix="aiqt-release-delta-pyc-")
        atexit.register(lambda: shutil.rmtree(d, ignore_errors=True))
        _CHILD_PYCACHE[0] = d
    return "pycache_prefix=" + _CHILD_PYCACHE[0]


def _validate_via_tool(root, script, args, what, tools_root=None):
    """Run a single-home validator as a subprocess and raise GateError (exit 2) on any nonzero exit
    OR a launch failure, so a structural violation OR a cannot-evaluate fails this gate closed rather than
    passing (VC-4 QA #1; round-4 finding 4 launch propagation). With `tools_root` set (ALWAYS set when the
    subject is a RAW MATERIALIZATION of a committed tree; QA round-4 codex R4-1 / claude m3), the tree
    under judgment is validated by its OWN committed tools/<script> copy, so an edit beside this gate in
    the CHECKOUT cannot neuter the validator that certifies committed bytes; a copy missing from the
    materialized tree is a refusal. With tools_root=None the checkout sibling runs, ONLY for the
    genesis/delta branches, whose subject IS the checkout."""
    if tools_root is not None:
        script_path = Path(tools_root) / "tools" / script
        if not script_path.is_file():
            raise GateError("{}: tools/{} is missing from the materialized committed tree; the committed "
                            "revision must carry the validator that judges it; fail-closed".format(
                                what, script))
    else:
        script_path = Path(__file__).resolve().parent / script
    try:
        # BYTES capture (round-5 finding 3): a child emitting invalid UTF-8 must not crash the gate; the
        # returncode is interpreted, and the diagnostic tail is decoded with replacement. -B keeps the child
        # from writing bytecode (round-6 finding 1 hermeticity), so no generated .pyc can enter a tree index.
        proc = subprocess.run([sys.executable, "-I", "-B", "-X", _child_pycache_x(),
                               str(script_path), *args], capture_output=True)
    except OSError as exc:
        raise GateError("{}: cannot launch {} ({}); fail-closed".format(what, script, exc))
    if proc.returncode != 0:
        diag = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()[:400]
        raise GateError("{}: {} rc={} ({})".format(what, script, proc.returncode, diag))


def _head_manifest_integrity(root):
    """Validate the HEAD manifest with the SAME strictness as the predecessor (this round's #1): the schema
    is strict-validated by the caller; here run the AUTHORITATIVE freshness (gen_manifest --check) and
    integrity (check_manifest SOURCES set-equality + raw re-hash) against the HEAD working tree, so a stale
    HEAD manifest that omits a staged pack path is exit 2. Mirrors _predecessor_tree_checks' manifest legs.
    GENESIS and DELTA branches only, whose subject IS the checkout; the POST-RELEASE branch judges the
    committed revision and uses _head_committed_manifest_integrity instead (QA round-3 codex R3-2)."""
    _validate_via_tool(root, "gen_manifest.py", ["--check", "--root", str(root)],
                       "head manifest freshness (gen_manifest --check)")
    _validate_via_tool(root, "check_manifest.py", ["--root", str(root)],
                       "head manifest integrity (check_manifest SOURCES set-equality)")


def _head_committed_manifest_integrity(root, head_oid):
    """The post-release manifest freshness + integrity legs over a RAW materialization of HEAD's
    COMMITTED tree, exactly as _predecessor_tree_checks runs them for older commits (QA round-3 codex
    R3-2 / claude F2): gen_manifest --check and check_manifest consume the materialized committed bytes,
    never the working tree, so a checkout clean/smudge filter, a skip-worktree or assume-unchanged index
    flag, or any other checkout drift can neither mask a stale/corrupt COMMITTED manifest nor substitute
    fresh working-tree bytes for stale committed ones. Hermetic like _predecessor_tree_checks: a temp
    dir removed in finally, the throwaway index built on the CLEAN materialized tree first."""
    import shutil
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-release-delta-head-"))
    dest = tmp / "tree"
    dest.mkdir()
    try:
        try:
            _release_schema.materialize_tree_raw(root, head_oid, dest)
        except SchemaError as exc:
            raise GateError(str(exc))
        _index_materialized_tree(dest, "head")
        # QA round-4 codex R4-1 / claude m3: the validators judging the COMMITTED tree are the tree\x27s
        # OWN committed copies (tools_root=dest), never the checkout siblings beside this gate, so a
        # checkout edit that neuters gen_manifest/check_manifest cannot certify a corrupt committed
        # manifest (_assert_gate_code_matches_head separately refuses a drifted gate import).
        _validate_via_tool(dest, "gen_manifest.py", ["--check", "--root", str(dest)],
                           "head manifest freshness (gen_manifest --check)", tools_root=dest)
        _validate_via_tool(dest, "check_manifest.py", ["--root", str(dest)],
                           "head manifest integrity (check_manifest SOURCES set-equality)",
                           tools_root=dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _genesis_structural(root):
    """Validate the internal structure of the consumed records at genesis (2.5/6.5), reusing each record's
    authoritative single-home validator so this gate never re-implements or drifts from them: check_clauses
    --genesis asserts the clause inventory (7.2) and the id-history register (7.3, born rows only covering
    the whole inventory), gen_renderers --check re-derives every renderer closure and framed digest (6.5),
    and the HEAD manifest freshness + integrity are validated too (this round's #1: HEAD is held to the same
    strictness in genesis as in non-genesis). Dispositions are validated separately by load_dispositions."""
    _validate_via_tool(root, "check_clauses.py", ["--root", str(root), "--genesis"],
                       "genesis clause inventory / id-history register structure (7.2/7.3)")
    _validate_via_tool(root, "gen_renderers.py", ["--check", "--root", str(root)],
                       "renderer declaration freshness (6.5)")
    _head_manifest_integrity(root)


# --- run --------------------------------------------------------------------------------------------

def _no_committed_state(root):
    """The stage-2 twin of _stage1_no_committed_state (QA round-6 codex blocker 1 / claude 3): True
    ONLY when a positive probe establishes that no committed state exists. Either (a) NOT A GIT
    REPOSITORY: git's own discovery refusal under a pinned C locale names no repository AND no
    `.git` entry exists at the root OR ANY ANCESTOR up to the filesystem root (QA round-7 codex
    blocker, via _stage1_dotgit_in_ancestry: discovery searches ancestors, so a damaged ancestor
    repository git cannot use is never a fallback); or (b) an UNBORN HEAD: the
    repository resolves, HEAD is a symbolic ref to a refs/ branch, and `git show-ref --verify`
    answers exactly "missing" (rc 1) for it. Every other git failure (dubious ownership, bad
    config, a corrupt object store, a detached or corrupt HEAD) returns False: cannot-evaluate."""
    probe = _git(root, ["rev-parse", "--git-dir"], binary=True, c_locale=True)
    if probe.returncode != 0:
        err = probe.stderr.decode("utf-8", "replace")
        return ("not a git repository" in err
                and not _stage1_dotgit_in_ancestry(str(root)))
    sym = _git(root, ["symbolic-ref", "--quiet", "HEAD"], binary=True)
    if sym.returncode != 0:
        return False
    try:
        ref = sym.stdout.decode("ascii").strip()
    except UnicodeDecodeError:
        return False
    if not ref.startswith("refs/"):
        return False
    return _git(root, ["show-ref", "--verify", "--quiet", ref]).returncode == 1


def _head_commit_or_none(root):
    """HEAD's commit id through the pinned funnel, or None ONLY when _no_committed_state POSITIVELY
    establishes that none exists (not a git repository, or an unborn branch). Routing uses this to
    decide whether there IS a committed state to certify: a repository carrying an attested release
    row necessarily has a resolvable HEAD, so an unresolvable HEAD never diverts a committed
    post-release state to a checkout-judged branch. Any OTHER git failure (QA round-6 codex blocker
    1: dubious ownership, a corrupt HEAD, bad config) re-raises as GateError, exit 2: a git failure
    is never a license to judge the checkout."""
    try:
        return _rev_parse(root, "HEAD^{commit}")
    except GateError:
        if _no_committed_state(root):
            return None
        raise GateError("git cannot resolve HEAD and no probe positively established an absent "
                        "committed state; a refused or damaged repository is cannot-evaluate, "
                        "never routed to a checkout-judged branch")


def _committed_post_release_state(root, head_oid):
    """The post-release ROUTING predicate over HEAD's COMMITTED objects ONLY (QA round-5 claude M1):
    (release rows, head version) when the committed releases record carries rows and the committed
    changelog's newest version EQUALS the newest row's version; None when the committed state is
    genesis- or delta-shaped. No checkout byte participates, so reverting the checkout copies of the
    routing records to the release state can no longer route a post-release head into the genesis or
    delta branch. With committed rows present, an unreadable or malformed committed routing record is
    cannot-evaluate (GateError, exit 2), never a silent fall-through to a checkout-judged branch."""
    rows = _strict(_release_schema.strict_releases, _show_toml(root, head_oid, RELEASES_REL),
                   "HEAD " + RELEASES_REL)
    if not rows:
        return None
    try:
        cl = tomllib.loads(_show(root, head_oid, CHANGELOG_REL).decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        raise GateError("HEAD {} does not parse: {}".format(CHANGELOG_REL, exc))
    rel = cl.get("release", [])
    if not isinstance(rel, list) or not rel or not isinstance(rel[-1], dict):
        raise GateError("HEAD {}: no [[release]] tables to read the committed head version "
                        "from".format(CHANGELOG_REL))
    head_version = rel[-1].get("version")
    if not isinstance(head_version, str) or _parse(head_version) is None:
        raise GateError("HEAD {}: latest release version {!r} is malformed".format(
            CHANGELOG_REL, head_version))
    if head_version != rows[-1]["version"]:
        return None
    return rows, head_version


def run(root):
    try:
        # BRANCH ROUTING FROM COMMITTED OBJECTS ONLY (QA round-5 claude M1; orchestrator decision
        # D-397-REEXEC-FROM-COMMITTED): whether the post-release branch runs is decided from HEAD's
        # COMMITTED copies of the routing records (the release rows and the changelog head version),
        # read through the verified funnel, never from the checkout.
        head_oid_route = _head_commit_or_none(root)
        pr_state = (_committed_post_release_state(root, head_oid_route)
                    if head_oid_route is not None else None)
        if pr_state is not None:
            release_rows, head_version = pr_state
            prev_row = release_rows[-1]
            # Anchor the attested predecessor exactly as the checkout branches do (round-7 finding 2).
            _anchor_predecessor(root, prev_row)
            changed, pr_findings = _post_release_head(root, prev_row, release_rows, head_version)
            if pr_findings:
                print("FAIL: {} release-delta finding(s) (post-release replay of the newest attested "
                      "release against its own predecessor)".format(len(pr_findings)))
                for f in pr_findings:
                    print("  " + f)
                return 1
            print("release-delta: POST-RELEASE (head version {} equals the newest attested release row {}; "
                  "the head differs from its commit_sha only in the post-release paths {}, and the attested "
                  "release itself re-passes the pre-row gate against its own predecessor); no delta "
                  "computed".format(head_version, prev_row["tag"], changed or "(none)"))
            return 0
        # GENESIS / DELTA CONTRACT (stated plainly; QA round 5): the two branches below JUDGE THE
        # CHECKOUT BY DESIGN. Their subject is the candidate surface in the working tree, and their
        # verdict certifies that checkout; CI runs them on a fresh clone where the checkout equals
        # HEAD. Only the post-release branch above certifies a COMMITTED revision, which is why its
        # routing and every byte it judges come from committed objects alone, and why a plain launch
        # re-executes the COMMITTED gate copy (stage 1) before any of this runs.
        release_rows = load_release_rows(root)
        head_manifest = _load(root, MANIFEST_REL)
        # Strict-validate the manifest BEFORE branching genesis/delta (round-3 finding 1): a bad
        # format-version, an unknown top-level key, or a malformed/duplicate source row is exit 2, never a
        # clean genesis over an unvalidated manifest (spec 2.5/L299).
        _strict(_release_schema.strict_manifest, head_manifest, MANIFEST_REL)
        rows = load_dispositions(root)
        # Profiles/groups (2.6 arm-or-fail-closed): the real diff leg is not built. While the artifact is
        # ABSENT the leg is legitimately NOT APPLICABLE; the moment it SHIPS a gate that cannot diff it must
        # FAIL CLOSED, never silently pass a change on the profile surface (VC-4 QA #4). Checked in BOTH
        # genesis and delta modes. The POST-RELEASE branch returns above and never reaches this lstat: its
        # decision is taken from the COMMITTED trees alone (_assert_profiles_absent_committed, QA round-4
        # claude M1; round 5 removed every checkout read from that branch). lstat (round-5 finding 5): the path existing as ANY tree entry, including
        # a symlink or a broken/loop symlink, means the artifact has shipped -> fail-closed. ONLY a genuine
        # FileNotFoundError establishes absence; any other OSError is cannot-evaluate.
        try:
            os.lstat(root / PROFILES_REL)
            _profiles_present = True
        except FileNotFoundError:
            _profiles_present = False
        except OSError as exc:
            raise GateError("cannot stat the profiles/groups artifact {} ({}); fail-closed".format(
                PROFILES_REL, exc))
        if _profiles_present:
            raise GateError("profiles/groups artifact {} has shipped (present as a tree entry) but its "
                            "delta leg is not implemented; fail-closed until the diff lands (2.6)".format(
                                PROFILES_REL))
        if not release_rows:
            if head_manifest.get("genesis") is not True:
                raise GateError("releases.toml is zero-row but the manifest does not declare "
                                "genesis = true (2.5)")
            # Genesis (2.5/6.5): validate the INTERNAL STRUCTURE of every consumed record (not merely that
            # it parses), and compute no delta. Dispositions are already normalized (rows, above); the order
            # and renderer records parse; the inventory + id-history register and renderer freshness are
            # validated through their single-home validators (VC-4 QA #1).
            _strict(_release_schema.strict_order, _load(root, ORDER_REL), ORDER_REL)
            genesis_renderers = _strict(_release_schema.strict_renderers, _load(root, RENDERERS_REL),
                                        RENDERERS_REL)
            _strict(_release_schema.strict_clause_inventory, _load(root, CLAUSES_REL), CLAUSES_REL)
            _strict(_release_schema.strict_id_history, _load(root, IDHISTORY_REL), IDHISTORY_REL)
            # A genesis renderer-semantics disposition must still name a DECLARED renderer (this round's #3):
            # genesis normalizes dispositions but runs no consumption leg, so this is the only place a
            # malformed renderer target is caught before a clean genesis return.
            _assert_renderer_targets_declared(rows, _renderer_ids(genesis_renderers))
            _genesis_structural(root)
            print("release-delta: GENESIS (zero release rows, manifest genesis = true); consumed records "
                  "validate their internal structure (dispositions, inventory, id-history register, "
                  "renderer freshness); no delta computed")
            return 0
        prev_row = release_rows[-1]
        commit = prev_row["commit_sha"]
        # Anchor the predecessor to a real commit + annotated tag before reading its tree (round-7 finding 2).
        _anchor_predecessor(root, prev_row)
        changelog = _load(root, CHANGELOG_REL).get("release", [])
        if not isinstance(changelog, list) or not changelog or not isinstance(changelog[-1], dict):
            raise GateError("{}: no [[release]] tables to read the head version from".format(CHANGELOG_REL))
        head_version = changelog[-1].get("version")
        if not isinstance(head_version, str) or _parse(head_version) is None:
            raise GateError("{}: latest release version {!r} is malformed".format(
                CHANGELOG_REL, head_version))
        # Bind the classified HEAD version to the head manifest (round-7 finding 2): the changelog version
        # the gate classifies against MUST equal the manifest's release-version, or the surface is
        # inconsistent and the delta cannot be trusted (exit 2).
        if head_manifest.get("release-version") != head_version:
            raise GateError("head manifest release-version {!r} != changelog head version {!r}; the "
                            "surface is inconsistent".format(head_manifest.get("release-version"),
                                                             head_version))
        # The post-release branch is entered ONLY through the committed routing above (QA round-5
        # claude M1). A checkout whose records claim the post-release state (a head version equal to
        # the newest attested row) while HEAD's COMMITTED records do not is never certified from
        # checkout inputs: refuse. Refusal-only: this can never accept a state the committed routing
        # rejected, and a head version BELOW the row still fails at _claimed_rank.
        if head_version == prev_row["version"]:
            raise GateError("the checkout claims the post-release state (head version {} equals the "
                            "newest attested release row) but HEAD's COMMITTED records do not; the "
                            "post-release branch routes from committed objects only; commit the "
                            "post-release records".format(head_version))
        claimed = _claimed_rank(prev_row["version"], head_version)
        # Strict-validate the consumed records on BOTH the predecessor object and the head object BEFORE any
        # delta computation (round-2 findings 1/4): a duplicate or incomplete clause row, a malformed
        # id-history register, or a bad order.toml (format-version, top-level shape) is exit 2, never a
        # silently collapsed dict or a computed delta over an unvalidated surface.
        prev_inv = _strict(_release_schema.strict_clause_inventory,
                           _show_toml(root, commit, CLAUSES_REL), "predecessor " + CLAUSES_REL)
        head_inv = _strict(_release_schema.strict_clause_inventory, _load(root, CLAUSES_REL), CLAUSES_REL)
        register = _strict(_release_schema.strict_id_history, _load(root, IDHISTORY_REL), IDHISTORY_REL)
        # Strict-validate the PREDECESSOR's OWN releases-order and id-history records too (this round's #1),
        # read via git show and run through the SAME shared strict validators (exact top-level keyset AND
        # values) BEFORE the predecessor-tree checks or any delta computation. The head releases record was
        # validated by load_release_rows and the head id-history just above; the predecessor's were NOT: a
        # malformed predecessor release row (e.g. an integer commit_sha) or an unknown id-history top-level
        # key (which the delegated clause loader in _predecessor_tree_checks ignores) is exit 2, never a
        # silent delta over an unvalidated predecessor.
        _strict(_release_schema.strict_releases, _show_toml(root, commit, RELEASES_REL),
                "predecessor " + RELEASES_REL)
        _strict(_release_schema.strict_id_history, _show_toml(root, commit, IDHISTORY_REL),
                "predecessor " + IDHISTORY_REL)
        _strict(_release_schema.strict_order, _show_toml(root, commit, ORDER_REL),
                "predecessor " + ORDER_REL)
        _strict(_release_schema.strict_order, _load(root, ORDER_REL), ORDER_REL)
        # Full Step-4 per-kind validation of the PREDECESSOR dispositions record too (this round's #2), the
        # SAME parsed-data validator load_dispositions applies to HEAD: the predecessor tree checks reach
        # only check_manifest's Step-2 common-field validator, so a predecessor default-correction row
        # missing its 6.6 evidence (valid at Step 2) is exit 2 here, symmetric with HEAD.
        prev_rows = _validate_dispositions_data(_show_toml(root, commit, DISPOSITIONS_REL),
                                                "predecessor " + DISPOSITIONS_REL)
        # Manifest and renderer declaration schemas on BOTH objects (round-3 findings 1/4).
        prev_manifest = _show_toml(root, commit, MANIFEST_REL)
        _strict(_release_schema.strict_manifest, prev_manifest, "predecessor " + MANIFEST_REL)
        # Bind the predecessor manifest version to the predecessor row (round-7 finding 2).
        if prev_manifest.get("release-version") != prev_row["version"]:
            raise GateError("predecessor manifest release-version {!r} != release-order row version {!r}; "
                            "the anchored predecessor is inconsistent".format(
                                prev_manifest.get("release-version"), prev_row["version"]))
        prev_renderers = _show_toml(root, commit, RENDERERS_REL)
        head_renderers = _load(root, RENDERERS_REL)
        _strict(_release_schema.strict_renderers, prev_renderers, "predecessor " + RENDERERS_REL)
        _strict(_release_schema.strict_renderers, head_renderers, RENDERERS_REL)
        # Every renderer-semantics disposition names a DECLARED renderer, scoped to the UNION of the strict-
        # validated PREDECESSOR and HEAD declarations (this round's #3 and #6): a valid disposition for a
        # renderer REMOVED between the releases (declared only in the predecessor) is accepted, while a
        # target in neither declaration is a mis-stated control, exit 2.
        _assert_renderer_targets_declared(
            rows, _nongenesis_renderer_target_scope(prev_renderers, head_renderers))
        # The PREDECESSOR's OWN renderer-semantics dispositions must ALSO name a renderer DECLARED at the
        # predecessor (round-5 finding 2): the predecessor rows were schema- and target-SYNTAX-validated at
        # _validate_dispositions_data above, but their renderer EXISTENCE was never checked (the returned
        # rows were discarded), so a predecessor row targeting 'nosuchrenderer' passed the gate clean. Scope
        # to the predecessor's own strict-validated renderer declaration (a predecessor disposition can only
        # reference a renderer that existed at that release).
        _assert_renderer_targets_declared(prev_rows, _renderer_ids(prev_renderers))
        # Run the FULL AUTHORITATIVE validators in non-genesis mode too (round-4 finding 2), not only in
        # genesis: check_clauses over the HEAD tree's own sources (the 7.2 span/text/digest legs and the
        # 7.3 register semantics), and the same over the RAW-materialized predecessor tree together with
        # gen_renderers freshness (round-4 findings 1/4). The exhaustive strict_* validators above guard the
        # record SCHEMAS on both objects; these run the source-consistency the schema cannot see.
        _validate_via_tool(root, "check_clauses.py",
                           ["--root", str(root)] + (["--genesis"] if head_manifest.get("genesis") is True
                                                    else []),
                           "head clause inventory / id-history structure (7.2/7.3)")
        _predecessor_tree_checks(root, commit, prev_manifest.get("genesis") is True)
        # Validate the HEAD manifest with the SAME strictness as the predecessor (this round's #1): the
        # schema was strict-validated above; now run the AUTHORITATIVE freshness + integrity on the HEAD
        # working tree, so a stale HEAD manifest that omits a staged pack path (an under-claimed path-add
        # the schema-only check passed as PATCH) is exit 2 before classification.
        _head_manifest_integrity(root)
        events, findings = [], []
        for ev, fs in (clause_text_leg(prev_inv, head_inv, register, rows, head_version),
                       path_keyset_leg(prev_manifest, head_manifest),
                       ownership_leg(_ownership_classes_at(root, commit),
                                     _ownership_classes_head(root), rows, head_version),
                       order_leg(_show(root, commit, ORDER_REL), (root / ORDER_REL).read_bytes()),
                       renderer_leg(root, prev_renderers, head_renderers, rows, head_version)):
            events += ev
            findings += fs
        # Profiles/groups is guaranteed ABSENT here (a present artifact fail-closed above): NOT APPLICABLE.
        print("release-delta: profiles/groups leg NOT APPLICABLE (adopter-experience artifact not "
              "yet defined; arms or fail-closes when it ships)")
        floor = max((e.floor for e in events), default=PATCH)
        findings += _disposition_findings(events, rows, head_version)
        if claimed < floor:
            findings.append("claimed bump {} ({} -> {}) is below the required {} floor".format(
                BUMP_NAME[claimed], prev_row["version"], head_version, BUMP_NAME[floor]))
    except (GateError, OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        # ValueError is the defensive backstop for an oversized SemVer component: _parse guards its int()
        # conversion and returns None, but a component that exceeds CPython's integer-string digit limit is
        # mapped to a cannot-evaluate here too, never a traceback exit 1. (UnicodeError is a ValueError
        # subclass, kept explicit for the decode-error intent it documents.)
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    if findings:
        print("FAIL: {} release-delta finding(s)".format(len(findings)))
        for f in findings:
            print("  " + f)
        return 1
    print("PASS: whole-surface delta requires {} and the claimed bump satisfies it ({} event(s) "
          "classified)".format(BUMP_NAME[floor], len(events)))
    return 0


# --- self-test --------------------------------------------------------------------------------------
# Pure classification cases (every 6.5 table row over in-memory inputs) always run and are deterministic.
# The git-independent genesis and unreachable-predecessor cases build minimal record trees in a private
# tempdir and are skipped with a printed note (never a false pass) where no writable tempdir exists.

def _run_quiet_root(root):
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return run(root)


def _floors(events):
    return sorted((e.surface, e.subject, e.change, e.floor) for e in events)


def _git_available():
    try:
        return subprocess.run(["git", "--version"], capture_output=True).returncode == 0
    except OSError:
        return False


def _git_init_commit(repo, msg, init=True):
    """Init (once) and commit a fixture repo with a fixed, neutralized identity so a self-test commit is
    deterministic and independent of the host git config (test hermeticity)."""
    from _git_fixture_env import git_fixture_env
    env = git_fixture_env()
    env.update({"GIT_AUTHOR_NAME": "AIQT Self-Test", "GIT_AUTHOR_EMAIL": "selftest@example.invalid",
                "GIT_COMMITTER_NAME": "AIQT Self-Test", "GIT_COMMITTER_EMAIL": "selftest@example.invalid",
                "GIT_AUTHOR_DATE": "2000-01-01T00:00:00", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00"})
    if init:
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, capture_output=True, env=env)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", msg], check=True, capture_output=True,
                   env=env)


def _selftest_env():
    from _git_fixture_env import git_fixture_env
    env = git_fixture_env()
    env.update({"GIT_AUTHOR_NAME": "AIQT Self-Test", "GIT_AUTHOR_EMAIL": "selftest@example.invalid",
                "GIT_COMMITTER_NAME": "AIQT Self-Test", "GIT_COMMITTER_EMAIL": "selftest@example.invalid",
                "GIT_AUTHOR_DATE": "2000-01-01T00:00:00", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00"})
    return env


def _archive_head(real):
    # A REAL-repository read reached from the scrubbed self-test: the caller's env (minus GIT_*)
    # keeps the safe.directory trust the in-place fixture scrub drops, so a foreign-owned checkout
    # does not silently skip the real full-pack cases.
    from _git_fixture_env import caller_env_without_git
    arch = subprocess.run(["git", "-C", str(real), "-c", "core.attributesFile=/dev/null", "archive", "HEAD"], capture_output=True,
                          env=caller_env_without_git())
    return arch.stdout if arch.returncode == 0 and arch.stdout else None


def _extract(arch_bytes, dest):
    import io
    import tarfile
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(arch_bytes), mode="r:") as tf:
        tf.extractall(dest)
    return dest


def _pin_fixture_version(repo, failures, version="1.0.0"):
    """Pin an archive-extracted fixture repo to a CHOSEN release-version (default "1.0.0") so its committed
    manifest release-version equals the fixture's 1.0.0 release-order row instead of inheriting the LIVE
    pack version. Without the pin, at any non-1.0.0 live version the version-binding check (the predecessor
    manifest release-version vs the release-order row, run() ~line 896) fails BEFORE the fixture's intended
    assertion, so every archive-extracting fixture that asserts a 1.0.0 predecessor row would pass for the
    WRONG reason and mask its real coverage. Writes VERSION + the changelog source at `version`, git-inits
    and stages the extracted tree, then regenerates the fixture manifest via gen_manifest --root
    (gen_manifest enumerates the tracked surface via `git ls-files`, so the tree is init + add'd first). A
    gen_manifest FAILURE is a real fixture-setup failure that FAILS the self-test (never a silent skip that
    could read as a pass of the intended assertion): it appends to `failures` (which makes self_test_main
    return nonzero) and returns False. Returns True on success, with the tree staged and the regenerated
    manifest ready for the caller to commit after any further per-fixture mutation."""
    env = _selftest_env()
    (repo / "VERSION").write_text(version + "\n", encoding="utf-8")
    (repo / CHANGELOG_REL).write_text('[[release]]\nversion = "{}"\n'.format(version), encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"]):
        if subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=env).returncode != 0:
            failures.append("fixture setup ({}): could not init/stage the archive-extracted tree for "
                            "version pinning; the archive-backed case cannot be normalized".format(repo.name))
            return False
    if subprocess.run(["python3", "tools/gen_manifest.py", "--root", str(repo)],
                      capture_output=True, env=env).returncode != 0:
        failures.append("fixture setup ({}): gen_manifest --root failed while pinning to {}; a fixture-setup "
                        "gen failure FAILS the self-test rather than silently passing as the intended "
                        "assertion".format(repo.name, version))
        return False
    return True


def _regen_fixture_manifest(root, case, failures, env):
    """Regenerate a fixture's manifest via gen_manifest --root as SELF-TEST SETUP, failing CLOSED on a
    nonzero gen exactly as _pin_fixture_version does. A setup gen-failure is a real fixture-setup failure:
    it appends a DISTINCT "fixture setup ... gen_manifest --root failed" entry to `failures` (which makes
    self_test_main return nonzero) and returns False, so the affected fixture does NOT proceed to its
    verdict assertion, where a coincidental exit (a stale/broken manifest ALSO drives exit 2) could
    otherwise be misread as the intended gate verdict and pass by luck. Returns True only when the manifest
    was actually regenerated."""
    if subprocess.run(["python3", "tools/gen_manifest.py", "--root", str(root)],
                      capture_output=True, env=env).returncode != 0:
        failures.append("fixture setup ({}): gen_manifest --root failed; a fixture-setup gen failure FAILS "
                        "the self-test rather than silently passing as the intended assertion".format(case))
        return False
    return True


def _edit_clause_consistently(repo):
    """Edit ONE clause's canonical-text CONSISTENTLY: pick a clause that is the sole coverer of every line
    in its span (so no sibling window breaks), rewrite those source lines, and recompute the whole-file
    source-digest for every clause in that source. The head tree then passes the authoritative check_clauses
    (the delta gate now runs it in non-genesis, round-4 finding 2), while the predecessor keeps the original
    text so clause_text_leg still sees a real text change. Returns the edited clause-id, or None."""
    import hashlib
    import re
    import tomllib
    from collections import defaultdict
    clp = repo / CLAUSES_REL
    raw = clp.read_text(encoding="utf-8")
    inv = tomllib.loads(raw).get("clause", [])
    spans = defaultdict(list)
    for c in inv:
        spans[c.get("source-path")].append((c.get("start-line"), c.get("end-line")))
    target = None
    for c in inv:
        if not isinstance(c.get("canonical-text"), str) or '"' in c["canonical-text"]:
            continue
        s, e, sp = c.get("start-line"), c.get("end-line"), c.get("source-path")
        if not (isinstance(s, int) and isinstance(e, int) and isinstance(sp, str)):
            continue
        if all(sum(1 for (a, b) in spans[sp] if isinstance(a, int) and a <= L <= b) == 1
               for L in range(s, e + 1)):
            target = c
            break
    if target is None:
        return None
    sp, s, e = target["source-path"], target["start-line"], target["end-line"]
    new_lines = ["EDITEDBYE2ETESTXYZ{}".format(k) for k in range(e - s + 1)]
    srcp = repo / sp
    src_lines = srcp.read_text(encoding="utf-8").split("\n")
    src_lines[s - 1:e] = new_lines
    new_src = "\n".join(src_lines)
    srcp.write_text(new_src, encoding="utf-8")
    new_dig = hashlib.sha256(new_src.encode("utf-8")).hexdigest()
    blocks = raw.split("[[clause]]")
    out = [blocks[0]]
    for b in blocks[1:]:
        if 'source-path = "{}"'.format(sp) in b:
            b = re.sub(r'source-digest = "[0-9a-f]{64}"', 'source-digest = "{}"'.format(new_dig), b)
        if 'clause-id = "{}"'.format(target["clause-id"]) in b:
            esc = "\\n".join(new_lines)
            b = re.sub(r'canonical-text = "[^"]*"',
                       lambda _m: 'canonical-text = "{}"'.format(esc), b, count=1)
        out.append("[[clause]]" + b)
    clp.write_text("".join(out), encoding="utf-8")
    return target["clause-id"]


def _real_pack_e2e(tmp, failures):
    """Build REAL full-pack two-release git repos from `git archive HEAD` of THIS repo and drive the real
    run() through the delta path: a clean CONSISTENTLY-edited id-keyed PATCH (finding 2), malformed-loader
    variants (findings 1/3), a wrong-version disposition (round-3 finding 3), and a smudge-filter predecessor
    renderer-closure attack that the raw-blob materialization defeats (round-4 findings 1/4), and genesis
    full-pack manifest/profiles/invalid-UTF-8 mutations (round-5 findings 1/3/5). Returns True if it ran,
    False if skipped (archive unavailable). Hermetic: private temp git repos, neutralized identity."""
    import re
    env = _selftest_env()
    arch = _archive_head(repo_root())
    if arch is None:
        failures.append("NOT RUN: the real full-pack run() cases (findings 1/2, rounds 3-5): "
                        "`git archive HEAD` is unavailable; coverage that did not run FAILS the "
                        "self-test, never a silent PASS (QA round-4 codex R4-3 / claude m4)")
        return False
    repo = tmp / "real-pack"
    try:
        _extract(arch, repo)
    except Exception as exc:  # noqa: BLE001  a bad archive FAILS, never a false pass (round-4 R4-3)
        failures.append("NOT RUN: the real full-pack run() cases: could not extract the archive ({}); "
                        "coverage that did not run FAILS the self-test (QA round-4 codex R4-3)".format(exc))
        return False
    # test-hermeticity: the archived tree carries the LIVE repo's release-version, so pin the extracted
    # predecessor tree to the fixture's chosen predecessor version (1.0.0) via the shared helper before
    # commit1 captures it (it writes VERSION + changelog, inits, stages, and regenerates the predecessor
    # manifest; a gen failure FAILS the self-test rather than passing silently). Without the pin the committed
    # predecessor manifest release-version tracks the live repo (e.g. 1.0.5) and no longer equals the v1.0.0
    # tag and the _releases() row, so the gate fails at the version-binding check before the intended
    # assertion.
    if not _pin_fixture_version(repo, failures):
        return False
    for args in (["add", "-A"], ["commit", "-q", "-m", "release 1.0.0", "--no-verify"]):
        if subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=env).returncode != 0:
            print("SELF-TEST NOTE: could not build the fixture git repo; real full-pack case SKIPPED",
                  file=sys.stderr)
            return False
    commit1 = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True,
                             text=True).stdout.strip()
    # Tag the predecessor 1.0.0 tree with a real ANNOTATED tag (round-7 finding 2: the delta now anchors the
    # predecessor to a resolvable commit + annotated tag whose object matches the recorded row).
    subprocess.run(["git", "-C", str(repo), "tag", "-a", "v1.0.0", "-m", "1.0.0"],
                   check=True, capture_output=True, env=env)
    tobj = subprocess.run(["git", "-C", str(repo), "rev-parse", "refs/tags/v1.0.0"],
                          capture_output=True, text=True).stdout.strip()
    cid = _edit_clause_consistently(repo)   # predecessor (commit1) keeps the original text
    if cid is None:
        print("SELF-TEST NOTE: no sole-coverer clause to edit; real full-pack case SKIPPED", file=sys.stderr)
        return False

    def _set_head_version(version):
        # Make HEAD a real release: bump VERSION + changelog and REGENERATE the head manifest so its
        # release-version equals the changelog version (round-7 finding 2 binds the two).
        (repo / "VERSION").write_text(version + "\n", encoding="utf-8")
        (repo / CHANGELOG_REL).write_text('[[release]]\nversion = "{}"\n'.format(version), encoding="utf-8")
        return _regen_fixture_manifest(repo, "real full-pack head @ {}".format(version), failures, env)

    def _regen_manifest():
        # Regenerate the HEAD manifest so it is FRESH after later writes to source records (dispositions,
        # releases): the HEAD manifest freshness check (this round's #1) now runs on it, so a case meant to
        # PROCEED past the manifest stage must present a fresh manifest. The records are already tracked, so
        # gen_manifest (which reads content from disk) sees the new bytes without a restage.
        return _regen_fixture_manifest(repo, "real full-pack head regen", failures, env)

    def _disp(release):
        (repo / DISPOSITIONS_REL).write_text(
            'format-version = 1\n\n[[disposition]]\nid = "{}"\nrelease = "{}"\n'
            'kind = "behaviour-neutral"\nimpact = "byte-only wording"\nrationale = "e2e test"\n'.format(
                cid, release), encoding="utf-8")

    def _releases(fmtver):
        return ('format-version = {}\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
                'tag_object_sha = "{t}"\ncommit_sha = "{c}"\nqa-sha256 = "{h}"\n'
                'qa-store-path = "qa/1.0.0.toml"\nattestation-timestamps = [100]\n'.format(
                    fmtver, t=tobj, c=commit1, h="a" * 64))

    if not _set_head_version("1.0.1"):
        return False   # the helper recorded a DISTINCT fixture-setup failure; do NOT skip past it silently
    _disp("1.0.1")
    (repo / RELEASES_REL).write_text(_releases(1), encoding="utf-8")
    # FRESH head manifest after the disposition + release-row writes (this round's #1)
    if not _regen_manifest():
        return False
    # (finding 2) a CONSISTENTLY-edited id-keyed PATCH change, fully dispositioned, with an ANCHORED
    # predecessor and a VERSION-BOUND head: clean exit 0.
    if _run_quiet_root(repo) != 0:
        failures.append("real full-pack run(): a consistently-edited dispositioned id-keyed PATCH change "
                        "expected exit 0 (finding 2)")
    # (round-6 finding 1) HERMETICITY: with child bytecode ENABLED, no generated file (.pyc/__pycache__) may
    # enter the predecessor index. _predecessor_tree_checks materializes the tree and runs gen_renderers,
    # which imports the tree's own renderer modules; the pre-fix order built the throwaway index AFTER that
    # child ran, so a `git add --force -A` captured the fresh __pycache__ and the predecessor manifest
    # SOURCES set disagreed with the real tree (the two fixtures above fail under a bare `python3 ...
    # --self-test`). We drop PYTHONDONTWRITEBYTECODE from the child env, spy on the index git actually
    # builds, and assert it carries no generated file AND that run() still returns the clean exit 0.
    _saved_pdwb = os.environ.pop("PYTHONDONTWRITEBYTECODE", None)
    _captured_index, _orig_index = [], _index_materialized_tree

    def _spy_index(dest, label):
        _orig_index(dest, label)
        from _git_fixture_env import git_fixture_env
        senv = git_fixture_env()
        ls = subprocess.run(["git", "-C", str(dest), "ls-files", "-z"], capture_output=True, env=senv)
        _captured_index.extend(p for p in ls.stdout.decode("utf-8", "replace").split("\x00") if p)
    globals()["_index_materialized_tree"] = _spy_index
    try:
        _rc_herm = _run_quiet_root(repo)
    finally:
        globals()["_index_materialized_tree"] = _orig_index
        if _saved_pdwb is not None:
            os.environ["PYTHONDONTWRITEBYTECODE"] = _saved_pdwb
    if _rc_herm != 0:
        failures.append("real full-pack run(): with child bytecode ENABLED the clean PATCH fixture must "
                        "still exit 0 (round-6 finding 1 hermeticity)")
    _generated = [p for p in _captured_index if "__pycache__" in p or p.endswith(".pyc")]
    if _generated:
        failures.append("HERMETICITY: a generated file entered the predecessor index {} (round-6 finding "
                        "1)".format(sorted(_generated)[:3]))
    if not _captured_index:
        failures.append("HERMETICITY regression did not observe the predecessor index build (round-6 "
                        "finding 1); the spy never fired")
    # (this round's #1) a STALE HEAD manifest: stage a NEW pack-owned path but do NOT regenerate the
    # manifest. Without the head manifest freshness/integrity check the path leg reads the stale manifest
    # keyset (missing the new path) and passes an under-claimed MINOR path-add as PATCH (exit 0); with it,
    # gen_manifest --check / check_manifest see the tracked-but-unlisted path and fail closed exit 2. The
    # file is removed afterward so the later cases see the clean tree.
    (repo / "tools" / "qa_new_path.txt").write_text("new pack path\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "tools/qa_new_path.txt"], capture_output=True, env=env)
    if _run_quiet_root(repo) != 2:
        failures.append("real full-pack run(): a staged pack path omitted from a stale HEAD manifest must "
                        "fail closed exit 2 (this round's #1)")
    (repo / "tools" / "qa_new_path.txt").unlink()
    subprocess.run(["git", "-C", str(repo), "add", "-A"], capture_output=True, env=env)
    # (this round's #4) a HEAD order.toml with an out-of-vocabulary presentation family in the NON-GENESIS
    # delta path: strict_order on the head object rejects it, exit 2 (pre-fix presentation-order entries
    # were validated only as strings). order.toml is restored so the later cases see the clean tree.
    _orig_order = (repo / ORDER_REL).read_text(encoding="utf-8")
    (repo / ORDER_REL).write_text(_orig_order.replace(
        'families = ["apex", "aiqt", "security"]', 'families = ["bogus"]', 1), encoding="utf-8")
    if _run_quiet_root(repo) != 2:
        failures.append("real full-pack run(): an out-of-vocabulary head order family must fail closed "
                        "exit 2 in the non-genesis delta path (this round's #4)")
    (repo / ORDER_REL).write_text(_orig_order, encoding="utf-8")
    # (this round's #5) a HEAD order.toml placing ACCUR in two precedence tiers in the NON-GENESIS path:
    # strict_order's GLOBAL member-uniqueness rejects the duplicate facet at exit 2 (pre-fix the global
    # duplicate slipped through and the gate reached the order_leg MAJOR-vs-claimed-PATCH finding, exit 1).
    (repo / ORDER_REL).write_text(_orig_order.replace(
        'members = ["PROGR"]', 'members = ["PROGR", "ACCUR"]', 1), encoding="utf-8")
    if _run_quiet_root(repo) != 2:
        failures.append("real full-pack run(): a HEAD order facet in two precedence tiers must fail closed "
                        "exit 2 in the non-genesis delta path (this round's #5)")
    (repo / ORDER_REL).write_text(_orig_order, encoding="utf-8")
    # === this round's #2: RELEASE-ROW VALUE VALIDATION. The HEAD releases row anchors the REAL predecessor
    # (valid commit_sha/tag), so WITHOUT the fix the row is accepted and the delta computes a clean PATCH
    # (exit 0); the single malformed VALUE is the only thing that flips it to exit 2, isolating the value
    # check from the anchoring path. Each case mutates one field of the otherwise-valid row and restores it.
    def _releases_val(**over):
        row = {"version": "1.0.0", "tag": "v1.0.0", "tobj": tobj, "csha": commit1,
               "qa": "a" * 64, "qapath": "qa/1.0.0.toml", "ts": "[100]"}
        row.update(over)
        return ('format-version = 1\n\n[[release]]\nversion = "{version}"\ntag = "{tag}"\n'
                'tag_object_sha = "{tobj}"\ncommit_sha = "{csha}"\nqa-sha256 = "{qa}"\n'
                'qa-store-path = "{qapath}"\nattestation-timestamps = {ts}\n').format(**row)
    # a non-'v' ANNOTATED tag on the SAME predecessor commit, so the tag field still RESOLVES in anchoring
    # while violating tag == 'v' + version (isolating the tag check from the anchoring path).
    subprocess.run(["git", "-C", str(repo), "tag", "-a", "rel-1.0.0", "-m", "1.0.0", commit1],
                   check=True, capture_output=True, env=env)
    tobj_rel = subprocess.run(["git", "-C", str(repo), "rev-parse", "refs/tags/rel-1.0.0"],
                              capture_output=True, text=True).stdout.strip()
    _val_cases = [
        (_releases_val(qapath="../escape"), "a qa-store-path traversal ('../escape')"),
        (_releases_val(qapath="qa//record"), "a non-canonical qa-store-path ('qa//record')"),
        # a 0x01 control character in the path, written as a TOML unicode escape so tomllib parses it to
        # the control byte (a raw byte would trip the TOML parser for the wrong reason): this round's #2.
        (_releases_val(qapath="qa/\\u0001record.toml"),
         "a qa-store-path with a 0x01 control character"),
        (_releases_val(ts="[-1]"), "a negative attestation epoch"),
        (_releases_val(tag="rel-1.0.0", tobj=tobj_rel),
         "a tag that does not equal 'v' + version (but still resolves)")]
    for body, desc in _val_cases:
        (repo / RELEASES_REL).write_text(body, encoding="utf-8")
        if _run_quiet_root(repo) != 2:
            failures.append("real full-pack run(): {} in the HEAD releases row must fail closed exit 2 "
                            "(this round's #2)".format(desc))
    (repo / RELEASES_REL).write_text(_releases(1), encoding="utf-8")   # restore the valid row
    # (round-7 finding 2) a predecessor commit_sha that is a TREE oid (not a commit) fails the anchoring,
    # exit 2 (the pre-round-7 gate used it directly and PASSED).
    tree_oid = subprocess.run(["git", "-C", str(repo), "rev-parse", commit1 + "^{tree}"],
                              capture_output=True, text=True).stdout.strip()
    (repo / RELEASES_REL).write_text(
        'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
        'tag_object_sha = "{t}"\ncommit_sha = "{tree}"\nqa-sha256 = "{h}"\n'
        'qa-store-path = "qa/1.0.0.toml"\nattestation-timestamps = [100]\n'.format(
            t=tobj, tree=tree_oid, h="a" * 64), encoding="utf-8")
    if _run_quiet_root(repo) != 2:
        failures.append("real full-pack run(): a predecessor commit_sha that is a tree oid must fail the "
                        "anchoring, exit 2 (round-7 finding 2)")
    # (round-7 finding 2) a head whose changelog version disagrees with the head manifest release-version:
    # exit 2 (the pre-round-7 gate classified against the changelog without binding it to the manifest).
    (repo / RELEASES_REL).write_text(_releases(1), encoding="utf-8")
    (repo / CHANGELOG_REL).write_text('[[release]]\nversion = "1.0.2"\n', encoding="utf-8")   # manifest is 1.0.1
    if _run_quiet_root(repo) != 2:
        failures.append("real full-pack run(): a changelog head version disagreeing with the head manifest "
                        "release-version must fail closed exit 2 (round-7 finding 2)")
    (repo / CHANGELOG_REL).write_text('[[release]]\nversion = "1.0.1"\n', encoding="utf-8")
    # (round-4 codex finding 1) an OVERSIZED head SemVer component: a 5000-digit major matches the bare-SemVer
    # grammar but exceeds CPython's integer-string digit limit. WITHOUT the _parse int() guard the conversion
    # raises ValueError and (pre-fix) escapes run()'s boundary as a traceback exit 1; WITH it _parse returns
    # None and the head-version malformation check fails closed exit 2. The releases row still anchors the
    # real predecessor, so the oversized changelog version is the sole thing under test. CHANGELOG restored.
    (repo / CHANGELOG_REL).write_text(
        '[[release]]\nversion = "{}.0.0"\n'.format("1" * 5000), encoding="utf-8")
    if _run_quiet_root(repo) != 2:
        failures.append("real full-pack run(): an oversized head SemVer component (5000-digit major) must "
                        "fail closed exit 2, not raise a ValueError traceback (round-4 codex finding 1)")
    (repo / CHANGELOG_REL).write_text('[[release]]\nversion = "1.0.1"\n', encoding="utf-8")
    # (finding 1) releases.toml / dispositions.toml format-version = 999: the loader fails closed exit 2.
    (repo / RELEASES_REL).write_text(_releases(999), encoding="utf-8")
    if _run_quiet_root(repo) != 2:
        failures.append("real full-pack run(): releases.toml format-version=999 must fail closed exit 2")
    (repo / RELEASES_REL).write_text(_releases(1), encoding="utf-8")
    (repo / DISPOSITIONS_REL).write_text(
        'format-version = 999\n\n[[disposition]]\nid = "{}"\nrelease = "1.0.1"\n'
        'kind = "behaviour-neutral"\nimpact = "x"\nrationale = "r"\n'.format(cid), encoding="utf-8")
    if _run_quiet_root(repo) != 2:
        failures.append("real full-pack run(): dispositions.toml format-version=999 must fail closed exit 2")

    # (round-3 finding 3) a wrong-version disposition (dated 1.0.1) for a MAJOR change (2.0.0), hidden behind
    # the MAJOR bump on the pre-fix gate, is flagged exit 1. HEAD is re-versioned to 2.0.0 so the head
    # manifest and changelog agree (round-7 finding 2).
    if _set_head_version("2.0.0"):
        _disp("1.0.1")   # mis-dated for the 2.0.0 change
        (repo / RELEASES_REL).write_text(_releases(1), encoding="utf-8")
        # FRESH head manifest after the disposition + release-row writes (this round's #1)
        if not _regen_manifest():
            return False
        if _run_quiet_root(repo) != 1:
            failures.append("real full-pack run(): a wrong-version disposition for the detected change must "
                            "be flagged exit 1 (round-3 finding 3)")

    # (round-4 findings 1/4) SMUDGE-FILTER PREDECESSOR ATTACK: commit-1 carries an undeclared edit to
    # tools/_gen_common.py (inside every renderer closure) AND a smudge filter that restores the old bytes
    # on checkout. A worktree checkout would smudge the edit away and pass (the pre-round-4 path); the raw
    # ls-tree/cat-file materialization reads the committed (edited) blob and fails closed exit 2.
    gcommon = "tools/_gen_common.py"
    repo2 = tmp / "real-pack-smudge"
    try:
        _extract(arch, repo2)
    except Exception:  # noqa: BLE001
        return True  # the primary cases already ran
    # Pin to 1.0.0 FIRST, on the CLEAN extracted tree (shared helper: writes VERSION + changelog, inits,
    # stages, and regenerates the manifest), THEN introduce the undeclared closure edit and the smudge
    # filter and re-commit. Pinning before the edit keeps the committed manifest's _gen_common.py digest at
    # the ORIGINAL bytes, so the raw predecessor materialization still detects the smudge-hidden edit (the
    # attack under test); it only binds release-version to 1.0.0 so the gate reaches that check instead of
    # failing at the version-binding rejection. A gen failure is recorded in `failures` by the helper.
    if not _pin_fixture_version(repo2, failures):
        return True  # the primary cases already ran; the setup failure is recorded in failures
    orig_gcommon = (repo2 / gcommon).read_text(encoding="utf-8")
    (repo2 / gcommon).write_text(orig_gcommon + "\n# predecessor-only undeclared edit\n", encoding="utf-8")
    attrs = repo2 / ".gitattributes"
    attrs.write_text(attrs.read_text(encoding="utf-8") + "tools/_gen_common.py filter=hide\n",
                     encoding="utf-8")
    ok = True
    for args in (["config", "filter.hide.smudge", "sed '/predecessor-only undeclared edit/d'"],
                 ["add", "-A"], ["commit", "-q", "-m", "release 1.0.0", "--no-verify"]):
        if subprocess.run(["git", "-C", str(repo2), *args], capture_output=True, env=env).returncode != 0:
            ok = False
            break
    if ok:
        commit1b = subprocess.run(["git", "-C", str(repo2), "rev-parse", "HEAD"], capture_output=True,
                                  text=True).stdout.strip()
        subprocess.run(["git", "-C", str(repo2), "tag", "-a", "v1.0.0", "-m", "1.0.0"],
                       check=True, capture_output=True, env=env)
        tobj2 = subprocess.run(["git", "-C", str(repo2), "rev-parse", "refs/tags/v1.0.0"],
                               capture_output=True, text=True).stdout.strip()
        # Confirm the smudge filter WOULD hide the edit under a checkout (the attack the raw path defeats).
        wt = tmp / "smudge-wt"
        if subprocess.run(["git", "-C", str(repo2), "worktree", "add", "--detach", "-q", str(wt), commit1b],
                          capture_output=True, env=env).returncode == 0:
            if "predecessor-only undeclared edit" in (wt / gcommon).read_text(encoding="utf-8"):
                failures.append("smudge fixture: the smudge filter did not hide the edit on checkout; the "
                                "attack is not set up, so the raw-materialization regression is not proven")
            subprocess.run(["git", "-C", str(repo2), "worktree", "remove", "--force", str(wt)],
                           capture_output=True, env=env)
        (repo2 / gcommon).write_text(orig_gcommon, encoding="utf-8")   # HEAD restores the original closure
        (repo2 / RELEASES_REL).write_text(
            'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
            'tag_object_sha = "{t}"\ncommit_sha = "{c}"\nqa-sha256 = "{h}"\n'
            'qa-store-path = "qa/1.0.0.toml"\nattestation-timestamps = [100]\n'.format(
                t=tobj2, c=commit1b, h="a" * 64), encoding="utf-8")
        (repo2 / "VERSION").write_text("1.0.1\n", encoding="utf-8")
        (repo2 / CHANGELOG_REL).write_text('[[release]]\nversion = "1.0.1"\n', encoding="utf-8")
        (repo2 / DISPOSITIONS_REL).write_text("format-version = 1\n", encoding="utf-8")
        # Regenerate the head manifest so its release-version (1.0.1) binds to the changelog (round-7
        # finding 2); the predecessor closure edit is caught later, at the raw predecessor materialization.
        if _regen_fixture_manifest(repo2, "real-pack-smudge head", failures, env) \
                and _run_quiet_root(repo2) != 2:
            failures.append("real full-pack run(): a smudge-filter-hidden predecessor renderer-closure edit "
                            "must be caught by the raw materialization, exit 2 (round-4 findings 1/4)")

    # (round-8 finding 1) UNDER-CLAIMED PREDECESSOR MANIFEST: commit-1 tracks the real NOTICE file but its
    # committed manifest DROPS the NOTICE [[sources]] row (schema-valid: strict_manifest does not recompute
    # tree-sha256 against sources). The pre-fix path leg trusted that under-claimed keyset, so a HEAD that
    # removed NOTICE passed PATCH; the fix runs gen_manifest --check AND check_manifest on the raw-
    # materialized predecessor, either of which fails the omission, exit 2.
    repo3 = tmp / "real-pack-omit"
    try:
        _extract(arch, repo3)
    except Exception:  # noqa: BLE001  the primary cases already ran
        return True
    # Pin to 1.0.0 FIRST (the shared helper regenerates a CLEAN, complete manifest at 1.0.0), THEN drop the
    # NOTICE [[sources]] row from that regenerated manifest so the ONLY inconsistency under test is the
    # under-claimed predecessor manifest, now reachable past the version-binding check. A gen failure is
    # recorded in `failures` by the helper.
    if not _pin_fixture_version(repo3, failures):
        return True  # the primary cases already ran; the setup failure is recorded in failures
    mpath = repo3 / MANIFEST_REL
    mblocks = mpath.read_text(encoding="utf-8").split("\n[[sources]]")
    kept = [mblocks[0]] + ["\n[[sources]]" + b for b in mblocks[1:] if 'path = "NOTICE"\n' not in b]
    if len(kept) == len(mblocks) - 1 and "[[artifacts]]" in "".join(kept):
        mpath.write_text("".join(kept), encoding="utf-8")
        ok3 = True
        for args in (["add", "-A"], ["commit", "-q", "-m", "release 1.0.0", "--no-verify"]):
            if subprocess.run(["git", "-C", str(repo3), *args], capture_output=True, env=env).returncode != 0:
                ok3 = False
                break
        if ok3:
            commit1c = subprocess.run(["git", "-C", str(repo3), "rev-parse", "HEAD"], capture_output=True,
                                      text=True).stdout.strip()
            subprocess.run(["git", "-C", str(repo3), "tag", "-a", "v1.0.0", "-m", "1.0.0"],
                           check=True, capture_output=True, env=env)
            tobj3 = subprocess.run(["git", "-C", str(repo3), "rev-parse", "refs/tags/v1.0.0"],
                                   capture_output=True, text=True).stdout.strip()
            # HEAD restores a CONSISTENT manifest and bumps to 1.0.1, so the ONLY inconsistency under test is
            # the predecessor manifest's omitted NOTICE row.
            (repo3 / "VERSION").write_text("1.0.1\n", encoding="utf-8")
            (repo3 / CHANGELOG_REL).write_text('[[release]]\nversion = "1.0.1"\n', encoding="utf-8")
            (repo3 / DISPOSITIONS_REL).write_text("format-version = 1\n", encoding="utf-8")
            (repo3 / RELEASES_REL).write_text(
                'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
                'tag_object_sha = "{t}"\ncommit_sha = "{c}"\nqa-sha256 = "{h}"\n'
                'qa-store-path = "qa/1.0.0.toml"\nattestation-timestamps = [100]\n'.format(
                    t=tobj3, c=commit1c, h="a" * 64), encoding="utf-8")
            # Regenerate the HEAD manifest AFTER the one-row releases write so it is FRESH (round-4 codex
            # finding 2): the head manifest freshness/integrity check (line ~905) then PASSES, so the ONLY
            # thing supplying exit 2 is the predecessor manifest guard under test. Regenerating before the
            # releases write left HEAD stale, and that stale HEAD independently gave exit 2 and MASKED the
            # predecessor guard (neutering it still left the fixture passing).
            if _regen_fixture_manifest(repo3, "real-pack-omit head", failures, env) \
                    and _run_quiet_root(repo3) != 2:
                failures.append("real full-pack run(): a predecessor manifest that under-claims a covered "
                                "pack path (NOTICE) must be caught by gen_manifest --check/check_manifest on "
                                "the raw predecessor, exit 2 (round-8 finding 1)")

    # (this round's #1) The PREDECESSOR's OWN releases and id-history records are now strict-validated. Each
    # fixture below mutates ONE predecessor record and REGENERATES the predecessor manifest so the mutated
    # record's digest matches (the predecessor manifest re-hash in _predecessor_tree_checks then PASSES),
    # isolating the fix: WITHOUT the new predecessor strict_releases / strict_id_history the predecessor
    # validates and the delta computes a clean PATCH (exit 0). The delegated clause loader ignores unknown
    # id-history top-level keys and never sees the releases record at all, so only the new checks catch these.
    def _pred_record_fixture(dirname, mutate, want_msg):
        rp = tmp / dirname
        try:
            _extract(arch, rp)
        except Exception:  # noqa: BLE001  the primary cases already ran
            return
        mutate(rp)
        # Pin to 1.0.0 AND regenerate the predecessor manifest via the shared helper, AFTER the mutation, so
        # the manifest source digests match the mutated tree (the predecessor manifest re-hash then PASSES)
        # AND the manifest release-version binds to the 1.0.0 release-order row (past the version-binding
        # check). A gen failure FAILS the self-test (recorded in `failures` by the helper) rather than
        # passing silently. Then re-stage and commit BOTH together (genesis stays true where the mutation
        # leaves a header-only / zero-row releases record).
        if not _pin_fixture_version(rp, failures):
            return  # the setup failure is recorded in failures
        for args in (["add", "-A"], ["commit", "-q", "-m", "release 1.0.0", "--no-verify"]):
            if subprocess.run(["git", "-C", str(rp), *args], capture_output=True, env=env).returncode != 0:
                return
        pcommit = subprocess.run(["git", "-C", str(rp), "rev-parse", "HEAD"], capture_output=True,
                                 text=True).stdout.strip()
        subprocess.run(["git", "-C", str(rp), "tag", "-a", "v1.0.0", "-m", "1.0.0"], check=True,
                       capture_output=True, env=env)
        ptobj = subprocess.run(["git", "-C", str(rp), "rev-parse", "refs/tags/v1.0.0"],
                               capture_output=True, text=True).stdout.strip()
        # HEAD restores VALID records (the SOLE malformation under test is the committed predecessor's),
        # bumps to 1.0.1, empties dispositions, regenerates the head manifest, and points a valid one-row
        # releases record at the predecessor.
        (rp / IDHISTORY_REL).write_text(orig_idh_ref[0], encoding="utf-8")
        (rp / RELEASES_REL).write_text("format-version = 1\n", encoding="utf-8")
        (rp / "VERSION").write_text("1.0.1\n", encoding="utf-8")
        (rp / CHANGELOG_REL).write_text('[[release]]\nversion = "1.0.1"\n', encoding="utf-8")
        (rp / DISPOSITIONS_REL).write_text("format-version = 1\n", encoding="utf-8")
        if not _regen_fixture_manifest(rp, "pred fixture ({}) head base".format(dirname), failures, env):
            return
        (rp / RELEASES_REL).write_text(
            'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
            'tag_object_sha = "{t}"\ncommit_sha = "{c}"\nqa-sha256 = "{h}"\n'
            'qa-store-path = "qa/1.0.0.toml"\nattestation-timestamps = [100]\n'.format(
                t=ptobj, c=pcommit, h="a" * 64), encoding="utf-8")
        # Regenerate the HEAD manifest AFTER the one-row releases so it is FRESH (genesis=false): the head
        # manifest freshness check (this round's #1) must PASS, so the committed PREDECESSOR record under
        # test is the sole failure. Without this the stale head manifest would fail #1's check and mask the
        # predecessor check each fixture targets.
        if not _regen_fixture_manifest(rp, "pred fixture ({}) head fresh".format(dirname), failures, env):
            return
        if _run_quiet_root(rp) != 2:
            failures.append(want_msg)

    # capture the real id-history so each fixture can restore a VALID head copy after mutating the predecessor.
    orig_idh_ref = [(repo_root() / IDHISTORY_REL).read_text(encoding="utf-8")]

    def _mut_idhist_topkey(rp):
        p = rp / IDHISTORY_REL
        p.write_text("bogus = 1\n" + p.read_text(encoding="utf-8"), encoding="utf-8")

    def _mut_releases_badrow(rp):
        # the finding's repro: a predecessor release ROW with an INTEGER commit_sha. read_genesis (used by
        # the predecessor tree checks) validates only the row KEYSET and presence, so it accepts this row
        # (genesis derives false from the row count); strict_releases is the only validator that type-checks
        # the field and rejects it. gen_manifest is regenerated AFTER the mutation, so the predecessor tree
        # stays consistent and the fix is isolated.
        (rp / RELEASES_REL).write_text(
            'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
            'tag_object_sha = "{h}"\ncommit_sha = 7\nqa-sha256 = "{q}"\n'
            'qa-store-path = "qa/1.0.0.toml"\nattestation-timestamps = [1]\n'.format(
                h="a" * 40, q="c" * 64), encoding="utf-8")

    def _mut_releases_ctrlpath(rp):
        # a predecessor release row whose qa-store-path carries a 0x01 control byte (a TOML unicode escape).
        # read_genesis accepts it (keyset/presence only); strict_releases via is_canonical_relpath now
        # rejects the FULL control range (this round's #2, predecessor object).
        (rp / RELEASES_REL).write_text(
            'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
            'tag_object_sha = "{h}"\ncommit_sha = "{h}"\nqa-sha256 = "{q}"\n'
            'qa-store-path = "qa/\\u0001rec.toml"\nattestation-timestamps = [1]\n'.format(
                h="a" * 40, q="c" * 64), encoding="utf-8")

    _pred_record_fixture(
        "real-pack-pred-idhist", _mut_idhist_topkey,
        "real full-pack run(): an unknown top-level key in the PREDECESSOR id-history must fail closed "
        "exit 2 (this round's #1)")
    _pred_record_fixture(
        "real-pack-pred-rel-badrow", _mut_releases_badrow,
        "real full-pack run(): a malformed row (integer commit_sha) in the PREDECESSOR releases record must "
        "fail closed exit 2 (this round's #1)")
    def _mut_disp_missing_evidence(rp):
        # a predecessor default-correction row with ONLY the Step-2 common fields (id/release/kind/impact/
        # rationale) and NONE of the 6.6 evidence fields. check_manifest's Step-2 validator (reached via
        # _predecessor_tree_checks) accepts it (common fields present); the Step-4 per-kind validator, now
        # applied to the PREDECESSOR too (this round's #2), rejects the incomplete keyset, exit 2.
        (rp / DISPOSITIONS_REL).write_text(
            'format-version = 1\n\n[[disposition]]\nid = "clause.1"\nrelease = "1.0.0"\n'
            'kind = "default-correction"\nimpact = "x"\nrationale = "r"\n', encoding="utf-8")

    def _mut_disp_renderer_nosuch(rp):
        # (round-5 finding 2) a PREDECESSOR renderer-semantics row whose target is a valid SLUG but names NO
        # renderer declared at the predecessor. Its target syntax passes _validate_dispositions_data, so the
        # pre-fix gate (which discarded the predecessor rows) never existence-checked it and passed clean;
        # the fix existence-checks the predecessor rows against the predecessor renderer declaration, exit 2.
        (rp / DISPOSITIONS_REL).write_text(
            'format-version = 1\n\n[[disposition]]\nid = "nosuchrenderer"\nrelease = "1.0.0"\n'
            'kind = "renderer-semantics"\nimpact = "byte-only"\nrationale = "r"\n', encoding="utf-8")

    _pred_record_fixture(
        "real-pack-pred-rel-ctrlpath", _mut_releases_ctrlpath,
        "real full-pack run(): a 0x01 control character in the PREDECESSOR releases qa-store-path must fail "
        "closed exit 2 (this round's #2)")
    _pred_record_fixture(
        "real-pack-pred-disp-noevidence", _mut_disp_missing_evidence,
        "real full-pack run(): a predecessor default-correction row missing its 6.6 evidence (valid at "
        "Step 2) must fail closed exit 2 (this round's #2)")
    _pred_record_fixture(
        "real-pack-pred-disp-renderer-nosuch", _mut_disp_renderer_nosuch,
        "real full-pack run(): a PREDECESSOR renderer-semantics disposition naming an undeclared renderer "
        "must fail closed exit 2 (round-5 finding 2)")

    # (round-6 finding 3) ARRAY-LEVEL releases invariants on the PREDECESSOR object. Each row is individually
    # valid, so per-row validation passes; strict_releases now rejects the array when its versions are not
    # unique + strictly increasing. read_genesis (keyset/presence only) accepts the multi-row record, so the
    # NEW array check is the sole thing that flips the fixture to exit 2.
    def _mut_releases_multi(rows_text):
        def _mut(rp):
            (rp / RELEASES_REL).write_text("format-version = 1\n\n" + rows_text, encoding="utf-8")
        return _mut

    def _rel_block(v):
        return ('[[release]]\nversion = "{v}"\ntag = "v{v}"\ntag_object_sha = "{h}"\ncommit_sha = "{h}"\n'
                'qa-sha256 = "{q}"\nqa-store-path = "qa/{v}.toml"\nattestation-timestamps = [1]\n'.format(
                    v=v, h="a" * 40, q="c" * 64))
    _pred_record_fixture(
        "real-pack-pred-rel-dup", _mut_releases_multi(_rel_block("1.0.0") + "\n" + _rel_block("1.0.0")),
        "real full-pack run(): two IDENTICAL predecessor release rows (a non-increasing version) must fail "
        "closed exit 2 (round-6 finding 3)")
    _pred_record_fixture(
        "real-pack-pred-rel-order", _mut_releases_multi(_rel_block("1.0.1") + "\n" + _rel_block("1.0.0")),
        "real full-pack run(): out-of-order predecessor release rows (versions not strictly increasing) "
        "must fail closed exit 2 (round-6 finding 3)")
    # NOTE: a predecessor ORDER facet-duplication is NOT given a run() fixture: strict_order runs on the
    # predecessor object too (the same validator the genesis and head #5 fixtures exercise), but any facet
    # duplication also breaks operative TIER_FACETS equality, which check_manifest.check_order_record catches
    # inside _predecessor_tree_checks regardless of this fix, so such a fixture would not discriminate.

    # === REAL GENESIS FULL-PACK run() (round-5 findings 1 and 5) ==================================
    # The archived HEAD is a genesis tree (zero-row releases, manifest genesis = true); run() takes the
    # genesis path where every structural validator PASSES on the real tree, so a mutation to ONE record is
    # the sole thing under test (no git needed for the genesis path). On the pre-round-5 gate each mutation
    # passed exit 0; the fix fails each closed exit 2.
    def _extract_genesis(name):
        dest = tmp / name
        try:
            _extract(arch, dest)
        except Exception:  # noqa: BLE001
            return None
        # git-init + add so the HEAD manifest freshness/integrity checks (this round's #1, now run in the
        # genesis path too) have a tracked path set to read; no commit or identity is needed. A malformation
        # fixture still exits at its earlier strict validator, before the genesis-structural manifest legs.
        for argv in (["init", "-q"], ["add", "-A"]):
            if subprocess.run(["git", "-C", str(dest), *argv], capture_output=True,
                              env=_selftest_env()).returncode != 0:
                return None
        return dest

    gclean = _extract_genesis("genesis-clean")
    if gclean is not None and _run_quiet_root(gclean) != 0:
        failures.append("real genesis full-pack run(): the unmutated real tree expected a clean exit 0")
    # (this round's #1, genesis) a STALE genesis HEAD manifest: stage a NEW pack-owned path but do NOT
    # regenerate the manifest. The genesis structural checks (schema, clauses, renderers) still pass, so
    # WITHOUT the head manifest freshness check genesis returns clean (exit 0); WITH it gen_manifest --check
    # sees the tracked-but-unlisted path and fails closed exit 2 (HEAD is held to the same manifest
    # strictness in genesis as in non-genesis).
    gstale = _extract_genesis("genesis-stale-manifest")
    if gstale is not None:
        (gstale / "tools" / "qa_new_path.txt").write_text("new pack path\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(gstale), "add", "tools/qa_new_path.txt"],
                       capture_output=True, env=_selftest_env())
        if _run_quiet_root(gstale) != 2:
            failures.append("real genesis full-pack run(): a staged pack path omitted from a stale genesis "
                            "HEAD manifest must fail closed exit 2 (this round's #1)")
    # (finding 1) a manifest with a bogus ARTIFACT-row key: strict_manifest rejects the exact kind-specific
    # keyset -> exit 2 (the pre-round-5 validator accepted unknown artifact keys and returned exit 0).
    gart = _extract_genesis("genesis-bogus-artifact")
    if gart is not None:
        mp = gart / ".aiqt" / "manifest.toml"
        mp.write_text(mp.read_text(encoding="utf-8").replace(
            "[[artifacts]]\n", "[[artifacts]]\nbogus = \"x\"\n", 1), encoding="utf-8")
        if _run_quiet_root(gart) != 2:
            failures.append("real genesis full-pack run(): a bogus artifact-row key must fail closed exit 2 "
                            "(round-5 finding 1)")
    # (finding 1) a manifest with EVERY [[sources]] row deleted: the mandatory `sources` section is absent,
    # so the exact top-level keyset is violated -> exit 2 (the pre-round-5 validator defaulted a missing
    # section to an empty list and returned exit 0).
    gsrc = _extract_genesis("genesis-no-sources")
    if gsrc is not None:
        mp = gsrc / ".aiqt" / "manifest.toml"
        stripped = re.sub(r'\[\[sources\]\]\npath = [^\n]*\nbytes = [^\n]*\nsha256 = [^\n]*\n\n', '',
                          mp.read_text(encoding="utf-8"))
        mp.write_text(stripped, encoding="utf-8")
        if _run_quiet_root(gsrc) != 2:
            failures.append("real genesis full-pack run(): a manifest missing its [[sources]] section must "
                            "fail closed exit 2 (round-5 finding 1)")
    # (round-6 finding 2) a managed-block artifact with a NON-STRING block-id: strict_manifest validates the
    # block-id VALUE, exit 2 (the pre-round-6 validator checked the keyset but not the value).
    gblk = _extract_genesis("genesis-bad-blockid")
    if gblk is not None:
        mp = gblk / ".aiqt" / "manifest.toml"
        mutated = mp.read_text(encoding="utf-8").replace('block-id = "RULES-INDEX"', "block-id = 7", 1)
        mp.write_text(mutated, encoding="utf-8")
        if _run_quiet_root(gblk) != 2:
            failures.append("real genesis full-pack run(): a non-string managed-block block-id must fail "
                            "closed exit 2 (round-6 finding 2)")
    # (round-6 finding 3) an UNKNOWN top-level key in clauses.toml: the exact top-level keyset is required
    # before rows are read, exit 2 (the pre-round-6 validator ignored top-level keys).
    gcla = _extract_genesis("genesis-clause-topkey")
    if gcla is not None:
        cp = gcla / CLAUSES_REL
        cp.write_text("bogus = 1\n" + cp.read_text(encoding="utf-8"), encoding="utf-8")
        if _run_quiet_root(gcla) != 2:
            failures.append("real genesis full-pack run(): an unknown clauses.toml top-level key must fail "
                            "closed exit 2 (round-6 finding 3)")
    # (round-6 finding 6) a class-change disposition whose old-class is NOT a release ownership class: the
    # malformed control is exit 2 at load (the pre-round-6 validator accepted any non-empty class string and
    # the ownership leg later produced a MINOR/binding finding, exit 1).
    gcc = _extract_genesis("genesis-bad-class")
    if gcc is not None:
        (gcc / DISPOSITIONS_REL).write_text(
            'format-version = 1\n\n[[disposition]]\nid = ".aiqt/x"\nrelease = "1.0.0"\n'
            'kind = "class-change"\nimpact = "x"\nrationale = "r"\nold-class = "not-a-class"\n'
            'new-class = "pack-immutable"\n', encoding="utf-8")
        if _run_quiet_root(gcc) != 2:
            failures.append("real genesis full-pack run(): a class-change with a non-vocabulary old-class "
                            "must fail closed exit 2 (round-6 finding 6)")
    # (this round's #3) a class-change disposition whose TARGET id is a path-traversal ('../escape'): the
    # per-kind target-syntax validator rejects it at load, exit 2 (pre-fix any non-empty id was accepted and
    # genesis returned clean). old/new class are valid so the SOLE malformation is the target path.
    gdte = _extract_genesis("genesis-disp-target-escape")
    if gdte is not None:
        (gdte / DISPOSITIONS_REL).write_text(
            'format-version = 1\n\n[[disposition]]\nid = "../escape"\nrelease = "1.0.0"\n'
            'kind = "class-change"\nimpact = "x"\nrationale = "r"\nold-class = "pack-immutable"\n'
            'new-class = "derived"\n', encoding="utf-8")
        if _run_quiet_root(gdte) != 2:
            failures.append("real genesis full-pack run(): a class-change disposition id '../escape' must "
                            "fail closed exit 2 (this round's #3)")
    # (this round's #3) a behaviour-neutral disposition whose target is NOT a canonical clause-id: exit 2.
    gdtc = _extract_genesis("genesis-disp-badclauseid")
    if gdtc is not None:
        (gdtc / DISPOSITIONS_REL).write_text(
            'format-version = 1\n\n[[disposition]]\nid = "notaclauseid"\nrelease = "1.0.0"\n'
            'kind = "behaviour-neutral"\nimpact = "x"\nrationale = "r"\n', encoding="utf-8")
        if _run_quiet_root(gdtc) != 2:
            failures.append("real genesis full-pack run(): a behaviour-neutral disposition with a "
                            "non-clause-id target must fail closed exit 2 (this round's #3)")
    # (this round's #3) a renderer-semantics disposition whose target is a valid slug but names NO declared
    # renderer: the existence check rejects it, exit 2 (pre-fix an undeclared target went silently
    # unconsumed, and genesis returned clean).
    gdtr = _extract_genesis("genesis-disp-renderer-undeclared")
    if gdtr is not None:
        (gdtr / DISPOSITIONS_REL).write_text(
            'format-version = 1\n\n[[disposition]]\nid = "nosuchrenderer"\nrelease = "1.0.0"\n'
            'kind = "renderer-semantics"\nimpact = "byte-only"\nrationale = "r"\n', encoding="utf-8")
        if _run_quiet_root(gdtr) != 2:
            failures.append("real genesis full-pack run(): a renderer-semantics disposition naming an "
                            "undeclared renderer must fail closed exit 2 (this round's #3)")
    # (this round's #4) an order.toml with an out-of-vocabulary presentation family ('bogus'): strict_order
    # rejects it, exit 2 (pre-fix presentation-order entries were validated only as strings).
    gof = _extract_genesis("genesis-order-badfamily")
    if gof is not None:
        op = gof / ORDER_REL
        op.write_text(op.read_text(encoding="utf-8").replace(
            'families = ["apex", "aiqt", "security"]', 'families = ["bogus"]', 1), encoding="utf-8")
        if _run_quiet_root(gof) != 2:
            failures.append("real genesis full-pack run(): an out-of-vocabulary order family must fail "
                            "closed exit 2 (this round's #4)")
    # (this round's #2) an artifact whose PATH carries a 0x01 control character: is_canonical_relpath now
    # rejects the full control range, exit 2 (pre-fix only NUL/tab/LF/CR were rejected, so 0x01 passed). The
    # artifact-id is bound to the same path so ONLY the control character is under test.
    gctl = _extract_genesis("genesis-artifact-ctrlpath")
    if gctl is not None:
        mp = gctl / MANIFEST_REL
        mp.write_text(mp.read_text(encoding="utf-8")
                      + '\n[[artifacts]]\nartifact-id = "genx:qa/\\u0001rec.toml"\n'
                      'path = "qa/\\u0001rec.toml"\nkind = "file"\nsha256 = "{}"\n'.format("a" * 64),
                      encoding="utf-8")
        if _run_quiet_root(gctl) != 2:
            failures.append("real genesis full-pack run(): an artifact path with a 0x01 control character "
                            "must fail closed exit 2 (this round's #2)")
    # (this round's #4) an artifact-id carrying a parsed NEWLINE (so it no longer binds to '<rid>:<path>'):
    # the artifact-id grammar/binding check rejects it, exit 2 (pre-fix any non-empty string was accepted).
    gaid = _extract_genesis("genesis-artifact-id-newline")
    if gaid is not None:
        mp = gaid / MANIFEST_REL
        mp.write_text(mp.read_text(encoding="utf-8")
                      + '\n[[artifacts]]\nartifact-id = "geny:qa/rec.toml\\n"\n'
                      'path = "qa/rec.toml"\nkind = "file"\nsha256 = "{}"\n'.format("b" * 64),
                      encoding="utf-8")
        if _run_quiet_root(gaid) != 2:
            failures.append("real genesis full-pack run(): an artifact-id carrying a newline (unbound from "
                            "its path) must fail closed exit 2 (this round's #4)")
    # (this round's #5) an order record placing ACCUR in TWO precedence tiers: the GLOBAL member-uniqueness
    # check rejects a facet at more than one rank, exit 2 (pre-fix uniqueness was only within each tier).
    godup = _extract_genesis("genesis-order-dupfacet")
    if godup is not None:
        op = godup / ORDER_REL
        op.write_text(op.read_text(encoding="utf-8").replace(
            'members = ["PROGR"]', 'members = ["PROGR", "ACCUR"]', 1), encoding="utf-8")
        if _run_quiet_root(godup) != 2:
            failures.append("real genesis full-pack run(): a facet placed in two precedence tiers must fail "
                            "closed exit 2 (this round's #5)")
    # (round-7 finding 1) a manifest source PATH that escapes the repo root ("../escape"): the canonical
    # logical-path validator rejects it, exit 2 (the pre-round-7 validator accepted any non-empty string).
    gesc = _extract_genesis("genesis-manifest-escape")
    if gesc is not None:
        mp = gesc / ".aiqt" / "manifest.toml"
        mp.write_text(mp.read_text(encoding="utf-8").replace(
            "[[sources]]\npath = ", "[[sources]]\npath = \"../escape\"\nbytes = 1\nsha256 = \"{}\"\n\n"
            "[[sources]]\npath = ".format("a" * 64), 1), encoding="utf-8")
        if _run_quiet_root(gesc) != 2:
            failures.append("real genesis full-pack run(): a manifest source path escaping the repo root "
                            "must fail closed exit 2 (round-7 finding 1)")
    # (round-7 finding 1) a manifest artifact kind that is a LIST (kind = ["file"]): the type-check before
    # the dict lookup raises SchemaError -> exit 2, not an uncaught TypeError -> exit 1.
    gkind = _extract_genesis("genesis-manifest-kind")
    if gkind is not None:
        mp = gkind / ".aiqt" / "manifest.toml"
        mp.write_text(mp.read_text(encoding="utf-8").replace('kind = "file"', 'kind = ["file"]', 1),
                      encoding="utf-8")
        if _run_quiet_root(gkind) != 2:
            failures.append("real genesis full-pack run(): a list-valued artifact kind must fail closed "
                            "exit 2, not crash (round-7 finding 1)")
    # (round-7 finding 8) a renderers.toml row with an INTEGER target: strict_renderers rejects a
    # non-canonical-path target, exit 2 (the pre-round-7 validator accepted any list element).
    grnd = _extract_genesis("genesis-renderer-badtarget")
    if grnd is not None:
        rp = grnd / RENDERERS_REL
        rp.write_text(rp.read_text(encoding="utf-8").replace(
            'targets = ["AGENTS.md"]', "targets = [7]", 1), encoding="utf-8")
        if _run_quiet_root(grnd) != 2:
            failures.append("real genesis full-pack run(): a renderers.toml integer target must fail closed "
                            "exit 2 (round-7 finding 8)")
    # (finding 5) a SELF-REFERENTIAL profiles.toml symlink (a loop): the artifact is present as a tree
    # entry, so the unbuilt-profiles leg must fail closed exit 2. The pre-round-5 is_file() saw a broken
    # loop as absent and returned NOT APPLICABLE -> exit 0.
    gprof = _extract_genesis("genesis-profiles-loop")
    if gprof is not None:
        try:
            os.symlink("profiles.toml", gprof / ".aiqt" / "core" / "profiles.toml")
            made = True
        except OSError:
            made = False
        if made and _run_quiet_root(gprof) != 2:
            failures.append("real genesis full-pack run(): a self-referential profiles.toml symlink must "
                            "fail closed exit 2 (round-5 finding 5)")

    # Regenerate a genesis fixture's manifest AFTER a record mutation so it stays FRESH (so the HEAD manifest
    # integrity leg PASSES and the strict validator under test is the SOLE source of exit 2, not a stale
    # manifest masking it, the round-4 lesson): stage the mutation, regenerate, restage the new manifest.
    def _genesis_regen(dest):
        genv = _selftest_env()
        subprocess.run(["git", "-C", str(dest), "add", "-A"], capture_output=True, env=genv)
        ok = _regen_fixture_manifest(dest, "genesis regen ({})".format(dest.name), failures, genv)
        subprocess.run(["git", "-C", str(dest), "add", "-A"], capture_output=True, env=genv)
        return ok

    # (round-5 finding 1) a HEAD clause whose source-path is NON-CANONICAL ('rules/../rules/...') but RESOLVES
    # to the real file: check_clauses resolves and PASSES it (the finding's escape, clean PATCH exit 0);
    # strict_clause_inventory now rejects the non-canonical form, exit 2. The manifest is regenerated so the
    # sole thing under test is the path form (neutering the source-path check makes this fixture pass).
    g1 = _extract_genesis("genesis-clause-noncanon-path")
    if g1 is not None:
        cp = g1 / CLAUSES_REL
        text = cp.read_text(encoding="utf-8")
        m = re.search(r'source-path = "([^"]+)"', text)
        parts = m.group(1).split("/") if m else []
        if len(parts) >= 2:
            noncanon = "/".join(parts[:-1] + ["..", parts[-2], parts[-1]])   # dir/sub/../sub/file (same file)
            cp.write_text(text.replace('source-path = "{}"'.format(m.group(1)),
                                       'source-path = "{}"'.format(noncanon), 1), encoding="utf-8")
            if _genesis_regen(g1) and _run_quiet_root(g1) != 2:
                failures.append("real genesis full-pack run(): a non-canonical HEAD clause source-path that "
                                "resolves to the real file must fail closed exit 2 (round-5 finding 1)")

    # (round-5 finding 3) a genesis default-correction row that is fully valid EXCEPT a captured-source whose
    # host carries a raw SPACE ('https://exa mple.com/...'): the pre-fix urlparse-only check accepted it (a
    # licensed MINOR); the deterministic URL grammar rejects it, exit 2. The row's target is a valid clause-id
    # SYNTAX (genesis checks syntax, not existence) so the ONLY malformation under test is the URL.
    g3 = _extract_genesis("genesis-dc-badurl")
    if g3 is not None:
        (g3 / DISPOSITIONS_REL).write_text(
            'format-version = 1\n\n[[disposition]]\nid = "prjint1.1"\nrelease = "1.0.0"\n'
            'kind = "default-correction"\nimpact = "x"\nrationale = "r"\n'
            'captured-source = "https://exa mple.com/s"\ncapture-date = "2026-08-24"\n'
            'observed-measurement = "1 byte"\nobserved-date = "2026-08-24"\n'
            'prefix-superset-reference = "qa/prefix.toml"\n', encoding="utf-8")
        if _genesis_regen(g3) and _run_quiet_root(g3) != 2:
            failures.append("real genesis full-pack run(): a default-correction captured-source with a "
                            "spaced host must fail closed exit 2 (round-5 finding 3)")

    # (round-6 finding 2) a genesis default-correction whose captured-source carries a MALFORMED PERCENT
    # ESCAPE ('%zz'): urlparse accepts it, but the strict grammar now rejects a '%' not followed by two hex
    # digits, exit 2. The row is otherwise fully valid so the escape is the sole thing under test.
    g2 = _extract_genesis("genesis-dc-badpct")
    if g2 is not None:
        (g2 / DISPOSITIONS_REL).write_text(
            'format-version = 1\n\n[[disposition]]\nid = "prjint1.1"\nrelease = "1.0.0"\n'
            'kind = "default-correction"\nimpact = "x"\nrationale = "r"\n'
            'captured-source = "https://example.com/%zz"\ncapture-date = "2026-08-24"\n'
            'observed-measurement = "1 byte"\nobserved-date = "2026-08-24"\n'
            'prefix-superset-reference = "qa/prefix.toml"\n', encoding="utf-8")
        if _genesis_regen(g2) and _run_quiet_root(g2) != 2:
            failures.append("real genesis full-pack run(): a default-correction captured-source with a "
                            "malformed percent escape must fail closed exit 2 (round-6 finding 2)")

    # (round-5 finding 3) a subprocess child emitting INVALID UTF-8 with a nonzero exit is captured as
    # BYTES: _renderer_freshness raises a clean GateError (exit 2), never an uncaught UnicodeDecodeError
    # (the pre-round-5 text=True capture crashed to exit 1). A tiny stub stands in for the generator.
    stubroot = tmp / "utf8-stub"
    (stubroot / "tools").mkdir(parents=True, exist_ok=True)
    (stubroot / "tools" / "gen_renderers.py").write_text(
        "import sys\nsys.stderr.buffer.write(b'\\xff\\xfe not utf-8\\n')\nsys.exit(1)\n", encoding="utf-8")
    try:
        _renderer_freshness(stubroot, "utf8-stub")
        failures.append("_renderer_freshness: an invalid-UTF-8 child must raise GateError, not crash "
                        "(round-5 finding 3)")
    except GateError:
        pass
    except UnicodeDecodeError:
        failures.append("_renderer_freshness: invalid-UTF-8 child output crashed with UnicodeDecodeError "
                        "(round-5 finding 3 regression)")
    return True


def _run_capture_root(root):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = run(root)
    return rc, out.getvalue() + err.getvalue()


def _post_release_e2e(tmp, failures, only=None):
    """RELEASING steps 6a/6b (PR #397 QA F1; round-2 full coverage): REAL full-pack fixtures drive run()'s
    POST-RELEASE branch over COMMITTED heads. The one-row fixture (the first attested release) covers the
    accepted states (the attestation row alone; the correctly placed tag key over a changelog whose newest
    release carries a [release.artifacts] sub-table), every revision-binding guard (a committed edit beyond
    the post-release paths, a smudge-masked committed edit, a committed edit to a path starting with a
    double quote, a mode-only +x change on EACH post-release path, a deleted post-release path, a
    staged-only edit, untracked files at the root and under .aiqt/core, a non-descendant head), the stale
    manifest, the extra prior row, the mis-placed tag key (EOF inside the artifacts table; the exact line in
    an OLDER release's table, which only the TOML check catches), and the replayed GENESIS validation over a
    malformed clauses.toml (round-2 B1 reproduction 2). Separate fixtures cover the predecessor-manifest
    version binding and the two-release replay (B1 reproduction 1: an undispositioned clause edit still
    exits 1 after its attestation row lands; an altered prior row exits 2).

    QA round-3 coverage (codex R3-1/R3-2/R3-3, claude F1/F2/F3/F5), each case failing alone when its
    guard is bypassed: a refs/replace blob substitution masking a forbidden committed changelog edit
    (plus a direct assertion that the funneled _show returns the RECORDED bytes, so bypassing the _git
    pins fails even where a redundant guard would still exit 2); a refs/replace substitution of a
    corrupt committed manifest blob by a fresh one (discriminates the raw-materialization pins); an
    info/grafts parent rewrite masking a non-descendant head (plus the direct _git_raw refusal); a
    clean/smudge-filter-masked corrupt committed manifest and a skip-worktree-masked stale committed
    manifest (both must exit 2 from the materialized COMMITTED tree); the checkout-drift ADVISORY
    (staged-only edit and untracked files now exit 0 with the stderr advisory, and an assume-unchanged
    working-tree overwrite of a tracked path never reaches the verdict); and the three ADMITTED
    byte-level variants locked by the _post_release_head exactness note (a releases.toml comment, a
    CRLF tag line, the tag key before the version line).

    `only` (an iterable of label substrings) runs a subset, for out-of-tree mutant-discrimination
    drivers and focused debugging. Returns True if it ran, False if skipped."""
    env = _selftest_env()
    arch = _archive_head(repo_root())
    if arch is None:
        failures.append("NOT RUN: the POST-RELEASE head cases (RELEASING 6a/6b, rounds 2-4): "
                        "`git archive HEAD` is unavailable; coverage that did not run FAILS the "
                        "self-test, never a silent PASS (QA round-4 codex R4-3 / claude m4)")
        return False

    def _sel(label):
        return only is None or any(s in label for s in only)

    def _extract_to(name):
        repo_ = tmp / name
        try:
            _extract(arch, repo_)
        except Exception as exc:  # noqa: BLE001  a bad archive FAILS, never a false pass (round-4 R4-3)
            failures.append("NOT RUN: the POST-RELEASE head cases: could not extract the archive ({}); "
                            "coverage that did not run FAILS the self-test (QA round-4 codex "
                            "R4-3)".format(exc))
            return None
        return repo_

    def _g(repo_, *args):
        return subprocess.run(["git", "-C", str(repo_), *args], capture_output=True, env=env)

    def _gout(repo_, *args):
        return _g(repo_, *args).stdout.decode("utf-8").strip()

    def _commit_all(repo_, label):
        for args in (("add", "-A"), ("commit", "-q", "--no-verify", "-m", "post-release case")):
            if _g(repo_, *args).returncode != 0:
                failures.append("fixture setup (post-release {}): could not commit".format(label))
                return False
        return True

    def _row_text(version, tag, tobj, csha, qa="a" * 64):
        return ('[[release]]\nversion = "{v}"\ntag = "{t}"\ntag_object_sha = "{to}"\n'
                'commit_sha = "{c}"\nqa-sha256 = "{q}"\nqa-store-path = "qa/{v}.toml"\n'
                'attestation-timestamps = [100]\n').format(v=version, t=tag, to=tobj, c=csha, q=qa)

    def _check(repo_, label, want, want_msg):
        rc, out = _run_capture_root(repo_)
        if rc != want or want_msg not in out:
            failures.append("post-release head {}: expected exit {} with {!r} (got rc={}: {})".format(
                label, want, want_msg, rc, out.strip()[-300:]))

    # ---- fixture 1: the first attested release (one row; replayed genesis) --------------------------
    base_cl = ('[[release]]\nversion = "0.9.0"\n\n[[release]]\nversion = "1.0.0"\n\n'
               '[release.artifacts]\nsbom = "sha256:0000"\n')
    quoted_rel = '''"TODO.md"'''
    v_line = 'version = "1.0.0"\n'
    tag_line = 'tag = "v1.0.0"\n'

    repo = _extract_to("post-release")
    if repo is None or not _pin_fixture_version(repo, failures):
        return False
    (repo / CHANGELOG_REL).write_text(base_cl, encoding="utf-8")
    (repo / quoted_rel).write_text("quoted-path fixture\n", encoding="utf-8")
    own_path = repo / OWNERSHIP_REL
    own_path.write_text(own_path.read_text(encoding="utf-8") +
                        "\n[[exclusion]]\npath = '" + quoted_rel + "'\nreason = \"QA round-2 "
                        "quoted-path fixture; tracked but excluded from pack scope.\"\n", encoding="utf-8")
    _g(repo, "add", "-A")
    if not _regen_fixture_manifest(repo, "post-release fixture", failures, env):
        return False
    for args in (("add", "-A"), ("commit", "-q", "--no-verify", "-m", "release 1.0.0"),
                 ("tag", "-a", "v1.0.0", "-m", "1.0.0")):
        if _g(repo, *args).returncode != 0:
            failures.append("NOT RUN: the POST-RELEASE head cases: could not build the fixture repo "
                            "(git {} failed); coverage that did not run FAILS the self-test (QA round-4 "
                            "codex R4-3)".format(args[0]))
            return False
    commit1 = _gout(repo, "rev-parse", "HEAD")
    tobj1 = _gout(repo, "rev-parse", "refs/tags/v1.0.0")
    row = "format-version = 1\n\n" + _row_text("1.0.0", "v1.0.0", tobj1, commit1)

    def _write(rel, text):
        (repo / rel).write_text(text, encoding="utf-8")

    def _append(rel, text):
        (repo / rel).write_text((repo / rel).read_text(encoding="utf-8") + text, encoding="utf-8")

    def _set_version(v):
        _write("VERSION", v + "\n")
        _write(CHANGELOG_REL, '[[release]]\nversion = "{}"\n'.format(v))

    def _chmod_x(rel):
        path_ = repo / rel
        path_.chmod(path_.stat().st_mode | 0o111)

    def _post_smudge():
        orig, edited = tmp / "agents-orig.bin", tmp / "agents-edited.bin"
        orig.write_bytes(_g(repo, "show", commit1 + ":AGENTS.md").stdout)
        edited.write_bytes((repo / "AGENTS.md").read_bytes())
        (repo / ".git" / "info" / "attributes").write_text("AGENTS.md filter=mask\n", encoding="utf-8")
        _g(repo, "config", "filter.mask.smudge", "cat " + str(orig))
        _g(repo, "config", "filter.mask.clean", "cat " + str(edited))
        (repo / "AGENTS.md").unlink()
        _g(repo, "checkout", "--", "AGENTS.md")

    def _post_staged():
        ab = (repo / "AGENTS.md").read_bytes()
        (repo / "AGENTS.md").write_bytes(ab + b"\nstaged-only edit\n")
        _g(repo, "add", "AGENTS.md")
        (repo / "AGENTS.md").write_bytes(ab)

    def _post_untracked_root():
        (repo / "qa-undeclared.txt").write_text("x\n", encoding="utf-8")

    def _post_untracked_core():
        (repo / ".aiqt" / "core" / "qa-extra.toml").write_text("format-version = 1\n", encoding="utf-8")

    def _post_orphan():
        orphan = _gout(repo, "commit-tree", _gout(repo, "rev-parse", "HEAD^{tree}"), "-m", "orphan")
        _g(repo, "checkout", "-q", orphan)

    def _post_assume_unchanged():
        (repo / "AGENTS.md").write_text("NOT THE COMMITTED BYTES\n", encoding="utf-8")
        _g(repo, "update-index", "--assume-unchanged", "--", "AGENTS.md")

    def _reset_case():
        # Undo every per-case mask so no case can leak into the next: filter config and attributes,
        # replace refs and grafts (round-3), assume-unchanged / skip-worktree index flags (cleared
        # BEFORE reset --hard, which would otherwise leave the flagged worktree bytes in place).
        for key in ("filter.mask.smudge", "filter.mask.clean"):
            _g(repo, "config", "--unset-all", key)
        for extra in ("attributes", "grafts"):
            p_ = repo / ".git" / "info" / extra
            if p_.exists():
                p_.unlink()
        for ref in _gout(repo, "for-each-ref", "--format=%(refname)", "refs/replace").splitlines():
            _g(repo, "update-ref", "-d", ref)
        for rel in sorted(POST_RELEASE_PATHS) + ["AGENTS.md"]:
            _g(repo, "update-index", "--no-assume-unchanged", "--", rel)
            _g(repo, "update-index", "--no-skip-worktree", "--", rel)
        for args in (("reset", "-q", "--hard", commit1), ("clean", "-q", "-fdx")):
            _g(repo, *args)

    # (label, mutate [pre-regen], regen, after_regen [post-regen pre-commit], commit, post [post-commit],
    #  want_rc, want_msg). Every case that asserts a committed-state guard COMMITS its mutation.
    cases = [
        ("(a) the attestation row alone, committed (step 6a, and main after it merges)",
         None, True, None, True, None, 0, "release-delta: POST-RELEASE"),
        ("(b) the tag key placed after the newest version line, before [release.artifacts] (step 6b)",
         lambda: _write(CHANGELOG_REL, base_cl.replace(v_line, v_line + tag_line)),
         True, None, True, None, 0, "release-delta: POST-RELEASE"),
        ("(d) the tag key appended at EOF lands inside [release.artifacts]",
         lambda: _write(CHANGELOG_REL, base_cl + tag_line), True, None, True, None,
         2, "by more than the newest"),
        ("(d) the exact tag line placed in an OLDER release's table (only the TOML check catches it)",
         lambda: _write(CHANGELOG_REL, base_cl.replace('version = "0.9.0"\n',
                                                       'version = "0.9.0"\n' + tag_line)),
         True, None, True, None, 2, "by more than the newest"),
        ("(c) the tag key plus another changed path, committed",
         lambda: (_write(CHANGELOG_REL, base_cl.replace(v_line, v_line + tag_line)),
                  _append("AGENTS.md", "\nedit\n")),
         True, None, True, None, 2, "beyond the post-release paths"),
        ("(d) a changelog edit other than the tag key (another key)",
         lambda: _write(CHANGELOG_REL, base_cl + 'date = "2000-01-01"\n'), True, None, True, None,
         2, "by more than the newest"),
        ("(d) the tag key plus a changelog comment",
         lambda: _write(CHANGELOG_REL, "# edited\n" + base_cl.replace(v_line, v_line + tag_line)),
         True, None, True, None, 2, "by more than the newest"),
        ("(d) a tag key that is not 'v' + version",
         lambda: _write(CHANGELOG_REL, base_cl.replace(v_line, v_line + 'tag = "v1.0.1"\n')),
         True, None, True, None, 2, "by more than the newest"),
        ("(e) a head version below the newest row",
         lambda: _set_version("0.9.0"), True, None, True, None, 2, "does not increase"),
        ("stale manifest: the row lands without regenerating the manifest",
         None, False, None, True, None, 2, "head manifest freshness"),
        ("an EXTRA prior release row the release commit never carried",
         lambda: _write(RELEASES_REL, "format-version = 1\n\n"
                        + _row_text("0.9.0", "v0.9.0", tobj1, commit1) + "\n"
                        + _row_text("1.0.0", "v1.0.0", tobj1, commit1)),
         True, None, True, None, 2, "plus exactly the newest row"),
        ("a DELETED post-release path (the announce snippet)",
         None, True, lambda: (repo / ".aiqt/release/announce-snippet.txt").unlink(), True, None,
         2, "stay an in-place edit"),
        ("a smudge-masked COMMITTED edit outside the post-release paths",
         lambda: _append("AGENTS.md", "\nCOMMITTED-HIDDEN-EDIT\n"), True, None, True, _post_smudge,
         2, "beyond the post-release paths"),
        ("a committed edit to a quoted path (a name starting with a double quote)",
         lambda: _write(quoted_rel, "EVIL CONTENT\n"), True, None, True, None,
         2, "beyond the post-release paths"),
        ("a staged-only edit is ADVISORY only; the committed verdict stands (QA round-3 codex R3-3)",
         None, True, None, True, _post_staged, 0, "post-release advisory"),
        ("an untracked file at the repository root is ADVISORY only (QA round-3)",
         None, True, None, True, _post_untracked_root, 0, "post-release advisory"),
        ("an untracked file under .aiqt/core is ADVISORY only (QA round-3)",
         None, True, None, True, _post_untracked_core, 0, "post-release advisory"),
        ("an assume-unchanged working-tree overwrite of a tracked path never reaches the verdict "
         "(QA round-3 codex R3-3)",
         None, True, None, True, _post_assume_unchanged, 0, "release-delta: POST-RELEASE"),
        ("(F5-1) a committed releases.toml comment: byte-level formatting is admitted when the parsed "
         "rows equal the release commit's plus exactly the newest row",
         lambda: _write(RELEASES_REL, "# byte-level comment (QA round-3 claude F5)\n" + row),
         True, None, True, None, 0, "release-delta: POST-RELEASE"),
        ("(F5-3) the tag key before the version line, inside the newest release table, is admitted",
         lambda: _write(CHANGELOG_REL, base_cl.replace(v_line, tag_line + v_line)),
         True, None, True, None, 0, "release-delta: POST-RELEASE"),
        ("a non-descendant head (an orphan commit with an identical tree)",
         None, True, None, True, _post_orphan, 2, "not an ancestor"),
    ] + [("a mode-only (+x) change on the post-release path " + rel, None, True,
          (lambda r=rel: _chmod_x(r)), True, None, 2, "stay an in-place edit")
         for rel in sorted(POST_RELEASE_PATHS)]

    for label, mutate, regen, after_regen, commit_it, post, want, want_msg in cases:
        if not _sel(label):
            continue
        _reset_case()
        _write(RELEASES_REL, row)
        if mutate:
            mutate()
        if regen and not _regen_fixture_manifest(repo, "post-release " + label, failures, env):
            continue
        if after_regen:
            after_regen()
        if commit_it and not _commit_all(repo, label):
            continue
        if post:
            post()
        _check(repo, label, want, want_msg)

    # ---- substitution attacks on fixture 1 (QA round-3 codex R3-1/R3-2, claude F1/F2/F3) -----------
    labelR = "a committed forbidden changelog edit masked by a blob replacement (refs/replace, R3-1)"
    if _sel(labelR):
        _reset_case()
        _write(RELEASES_REL, row)
        _write(CHANGELOG_REL, base_cl + 'date = "2000-01-01"\n')
        if (_regen_fixture_manifest(repo, "post-release " + labelR, failures, env)
                and _commit_all(repo, labelR)):
            bad_blob = _gout(repo, "rev-parse", "HEAD:" + CHANGELOG_REL)
            good_blob = _gout(repo, "rev-parse", commit1 + ":" + CHANGELOG_REL)
            if _g(repo, "replace", bad_blob, good_blob).returncode != 0:
                failures.append("fixture setup ({}): git replace failed".format(labelR))
            else:
                if b"2000-01-01" in _g(repo, "show", "HEAD:" + CHANGELOG_REL).stdout:
                    print("SELF-TEST NOTE: this git did not substitute the planted replace ref; the "
                          "blob-replacement case still asserts the funnel reads", file=sys.stderr)
                # The funnel itself must return the RECORDED committed bytes while the replacement is
                # active: this assertion fails ALONE when the _git pins are bypassed (claude F3), even
                # where a redundant downstream guard would still exit 2.
                try:
                    funneled = _show(repo, _gout(repo, "rev-parse", "HEAD"), CHANGELOG_REL)
                except GateError as exc:
                    funneled = b""
                    failures.append("blob replacement: funneled _show failed ({})".format(exc))
                if funneled and b"2000-01-01" not in funneled:
                    failures.append("blob replacement: the funneled _show returned the SUBSTITUTED "
                                    "bytes; the --no-replace-objects pins are not effective")
                _check(repo, labelR, 2, "by more than the newest")

    labelR2 = "a corrupt COMMITTED manifest masked by a blob replacement (raw materialization pins)"
    if _sel(labelR2):
        _reset_case()
        _write(RELEASES_REL, row)
        if _regen_fixture_manifest(repo, "post-release " + labelR2, failures, env):
            man_path = repo / MANIFEST_REL
            valid = man_path.read_text(encoding="utf-8")
            marker = 'sha256 = "'
            base = valid.find("[[sources]]")
            pos = valid.find(marker, base) if base != -1 else -1
            corrupt = (valid[:pos + len(marker)] + "0" * 64 + valid[pos + len(marker) + 64:]
                       if pos != -1 else valid)
            if corrupt == valid:
                failures.append("fixture setup ({}): could not corrupt a manifest sha256".format(
                    labelR2))
            else:
                man_path.write_text(corrupt, encoding="utf-8")
            if corrupt != valid and _commit_all(repo, labelR2):
                (tmp / "man-valid-r2.bin").write_text(valid, encoding="utf-8")
                corrupt_blob = _gout(repo, "rev-parse", "HEAD:" + MANIFEST_REL)
                valid_blob = _gout(repo, "hash-object", "-w", str(tmp / "man-valid-r2.bin"))
                if not valid_blob or _g(repo, "replace", corrupt_blob, valid_blob).returncode != 0:
                    failures.append("fixture setup ({}): git replace failed".format(labelR2))
                else:
                    # With the materialization pins, the COMMITTED corrupt manifest is what
                    # gen_manifest --check sees (exit 2); with them bypassed, the replacement feeds the
                    # fresh blob to cat-file --batch and the gate would pass, so this case fails alone.
                    _check(repo, labelR2, 2, "head manifest")

    labelGf = "a non-descendant head masked by a commit graft (info/grafts)"
    if _sel(labelGf):
        _reset_case()
        _write(RELEASES_REL, row)
        if (_regen_fixture_manifest(repo, "post-release " + labelGf, failures, env)
                and _commit_all(repo, labelGf)):
            _post_orphan()
            orphan_oid = _gout(repo, "rev-parse", "HEAD")
            (repo / ".git" / "info" / "grafts").write_text(orphan_oid + " " + commit1 + "\n",
                                                           encoding="utf-8")
            if _g(repo, "merge-base", "--is-ancestor", commit1, "HEAD").returncode != 0:
                print("SELF-TEST NOTE: this git ignores info/grafts; the graft case still asserts the "
                      "funnel reads", file=sys.stderr)
            # The funnel must refuse the grafted parent: fails ALONE when the GIT_GRAFT_FILE pin is
            # bypassed (claude F3).
            if _git_raw(repo, ["merge-base", "--is-ancestor", commit1, "HEAD"]).returncode == 0:
                failures.append("graft: the funneled merge-base honoured the planted graft; the "
                                "GIT_GRAFT_FILE pin is not effective")
            _check(repo, labelGf, 2, "not an ancestor")

    labelC = "(F5-2) a plumbing-committed CRLF tag line inside the LF changelog is admitted"
    if _sel(labelC):
        _reset_case()
        _write(RELEASES_REL, row)
        _write(CHANGELOG_REL, base_cl.replace(v_line, v_line + 'tag = "v1.0.0"\r\n'))
        # The generated .gitattributes pins changelog.toml to eol=lf, so a porcelain `git add` would
        # normalize the CRLF away at checkin; the committed blob must REALLY carry it for the exactness
        # note's variant (2), so the CRLF blob is committed through plumbing (hash-object --no-filters +
        # update-index --cacheinfo). The manifest is regenerated first against the CRLF working tree,
        # so the committed manifest binds the exact committed bytes.
        if _regen_fixture_manifest(repo, "post-release " + labelC, failures, env):
            blob = _gout(repo, "hash-object", "-w", "--no-filters", str(repo / CHANGELOG_REL))
            steps = ((("add", "-A"),) if blob else ())
            ok = bool(blob)
            for args in steps + (("update-index", "--cacheinfo",
                                  "100644," + blob + "," + CHANGELOG_REL),
                                 ("commit", "-q", "--no-verify", "-m", "post-release case")):
                if ok and _g(repo, *args).returncode != 0:
                    ok = False
            if not ok:
                failures.append("fixture setup ({}): could not plumbing-commit the CRLF blob".format(
                    labelC))
            else:
                _check(repo, labelC, 0, "release-delta: POST-RELEASE")

    labelF = "a corrupt COMMITTED manifest masked by a clean/smudge filter (codex R3-2)"
    if _sel(labelF):
        _reset_case()
        _write(RELEASES_REL, row)
        if _regen_fixture_manifest(repo, "post-release " + labelF, failures, env):
            man_path = repo / MANIFEST_REL
            valid = man_path.read_text(encoding="utf-8")
            marker = 'sha256 = "'
            base = valid.find("[[sources]]")
            pos = valid.find(marker, base) if base != -1 else -1
            corrupt = (valid[:pos + len(marker)] + "0" * 64 + valid[pos + len(marker) + 64:]
                       if pos != -1 else valid)
            if corrupt == valid:
                failures.append("fixture setup ({}): could not corrupt a manifest sha256".format(labelF))
            else:
                man_path.write_text(corrupt, encoding="utf-8")
                if _commit_all(repo, labelF):
                    (tmp / "man-valid.bin").write_text(valid, encoding="utf-8")
                    (tmp / "man-corrupt.bin").write_text(corrupt, encoding="utf-8")
                    (repo / ".git" / "info" / "attributes").write_text(
                        MANIFEST_REL + " filter=mask\n", encoding="utf-8")
                    _g(repo, "config", "filter.mask.smudge", "cat " + str(tmp / "man-valid.bin"))
                    _g(repo, "config", "filter.mask.clean", "cat " + str(tmp / "man-corrupt.bin"))
                    man_path.unlink()
                    _g(repo, "checkout", "--", MANIFEST_REL)
                    if _g(repo, "status", "--porcelain=v1").stdout.strip():
                        print("SELF-TEST NOTE: the filter mask did not clean the status; the masked-"
                              "manifest case still asserts the committed verdict", file=sys.stderr)
                    # The working tree now shows the VALID manifest and status is clean, yet the
                    # COMMITTED manifest is corrupt: only the materialized-committed-tree legs catch it.
                    _check(repo, labelF, 2, "head manifest")

    labelS = "a STALE committed manifest with fresh working-tree bytes hidden by skip-worktree (F2)"
    if _sel(labelS):
        _reset_case()
        _write(RELEASES_REL, row)
        # Commit WITHOUT regenerating: the committed manifest/root/snippet are stale for the new row;
        # then regenerate the WORKING TREE and hide the drift behind skip-worktree. The old working-tree
        # freshness check passed this (claude F2's H2); the committed verdict must exit 2.
        if _commit_all(repo, labelS):
            if _regen_fixture_manifest(repo, "post-release " + labelS + " (working tree)", failures,
                                       env):
                for rel in sorted(POST_RELEASE_PATHS):
                    _g(repo, "update-index", "--skip-worktree", "--", rel)
                _check(repo, labelS, 2, "head manifest freshness")

    # ---- fixture: the release commit's manifest disagreeing with its own row (claude M4) ------------
    labelM = "predecessor-manifest version binding (release-version 9.9.9 at the release commit)"
    if _sel(labelM):
        repoM = _extract_to("post-release-binding")
        if repoM is None or not _pin_fixture_version(repoM, failures):
            return False
        man = (repoM / MANIFEST_REL).read_text(encoding="utf-8")
        bad = man.replace('release-version = "1.0.0"', 'release-version = "9.9.9"', 1)
        if bad == man:
            failures.append("fixture setup ({}): could not rewrite release-version".format(labelM))
        else:
            (repoM / MANIFEST_REL).write_text(bad, encoding="utf-8")
            for args in (("add", "-A"), ("commit", "-q", "--no-verify", "-m", "release 1.0.0"),
                         ("tag", "-a", "v1.0.0", "-m", "1.0.0")):
                _g(repoM, *args)
            cM = _gout(repoM, "rev-parse", "HEAD")
            tM = _gout(repoM, "rev-parse", "refs/tags/v1.0.0")
            (repoM / RELEASES_REL).write_text(
                "format-version = 1\n\n" + _row_text("1.0.0", "v1.0.0", tM, cM), encoding="utf-8")
            if _regen_fixture_manifest(repoM, labelM, failures, env) and _commit_all(repoM, labelM):
                _check(repoM, labelM, 2, "anchored predecessor is inconsistent")

    # ---- fixture: B1 reproduction 2, a malformed genesis tree behind its attestation row ------------
    labelG = "(B1) a first release whose genesis tree carries a malformed clauses.toml"
    if _sel(labelG):
        repoG = _extract_to("post-release-genesis-bad")
        if repoG is None or not _pin_fixture_version(repoG, failures):
            return False
        with open(repoG / CLAUSES_REL, "a", encoding="utf-8") as fh:
            fh.write('\n[[clause]]\nbogus = "not the 7.2 schema"\n')
        _g(repoG, "add", "-A")
        if not _regen_fixture_manifest(repoG, labelG, failures, env):
            return False
        for args in (("add", "-A"), ("commit", "-q", "--no-verify", "-m", "release 1.0.0"),
                     ("tag", "-a", "v1.0.0", "-m", "1.0.0")):
            _g(repoG, *args)
        # pre-row control: the zero-row genesis gate rejects this tree outright.
        _check(repoG, labelG + " (pre-row control)", 2, "clause")
        cG = _gout(repoG, "rev-parse", "HEAD")
        tG = _gout(repoG, "rev-parse", "refs/tags/v1.0.0")
        (repoG / RELEASES_REL).write_text(
            "format-version = 1\n\n" + _row_text("1.0.0", "v1.0.0", tG, cG), encoding="utf-8")
        if _regen_fixture_manifest(repoG, labelG, failures, env) and _commit_all(repoG, labelG):
            _check(repoG, labelG, 2, "clause")

    # ---- fixture: B1 reproduction 1 (two releases) and an altered prior row -------------------------
    label2 = "(B1) a later release with an undispositioned clause change, after its attestation row"
    label2b = "an ALTERED prior release row"
    if _sel(label2) or _sel(label2b):
        r2 = _extract_to("post-release-two")
        if r2 is None or not _pin_fixture_version(r2, failures):
            return False
        for args in (("add", "-A"), ("commit", "-q", "--no-verify", "-m", "release 1.0.0"),
                     ("tag", "-a", "v1.0.0", "-m", "1.0.0")):
            _g(r2, *args)
        cA = _gout(r2, "rev-parse", "HEAD")
        tA = _gout(r2, "rev-parse", "refs/tags/v1.0.0")
        row1 = _row_text("1.0.0", "v1.0.0", tA, cA)
        (r2 / RELEASES_REL).write_text("format-version = 1\n\n" + row1, encoding="utf-8")
        if not (_regen_fixture_manifest(r2, "two-release attestation 1.0.0", failures, env)
                and _commit_all(r2, "two-release attestation 1.0.0")):
            return False
        if _edit_clause_consistently(r2) is None:
            failures.append("fixture setup (two-release): no clause admits a consistent edit")
            return True
        (r2 / "VERSION").write_text("1.0.1\n", encoding="utf-8")
        (r2 / CHANGELOG_REL).write_text(
            '[[release]]\nversion = "1.0.0"\n\n[[release]]\nversion = "1.0.1"\n', encoding="utf-8")
        if not (_regen_fixture_manifest(r2, "two-release release 1.0.1", failures, env)
                and _commit_all(r2, "two-release release 1.0.1")):
            return False
        _g(r2, "tag", "-a", "v1.0.1", "-m", "1.0.1")
        cX = _gout(r2, "rev-parse", "HEAD")
        tX = _gout(r2, "rev-parse", "refs/tags/v1.0.1")
        if _sel(label2):
            # pre-row control: before its row lands, the undispositioned release is exit 1.
            _check(r2, label2 + " (pre-row control)", 1, "below the required")
        rows2 = "format-version = 1\n\n" + row1 + "\n" + _row_text("1.0.1", "v1.0.1", tX, cX)
        (r2 / RELEASES_REL).write_text(rows2, encoding="utf-8")
        if not (_regen_fixture_manifest(r2, "two-release attestation 1.0.1", failures, env)
                and _commit_all(r2, "two-release attestation 1.0.1")):
            return False
        if _sel(label2):
            _check(r2, label2, 1, "below the required")
        if _sel(label2b):
            (r2 / RELEASES_REL).write_text(rows2.replace("a" * 64, "b" * 64, 1), encoding="utf-8")
            if (_regen_fixture_manifest(r2, label2b, failures, env)
                    and _commit_all(r2, label2b)):
                _check(r2, label2b, 2, "plus exactly the newest row")

    # ---- QA round-4: environment and object-store substitution (codex R4-2, claude m2) --------------
    # One shared geometry: acceptA is the ACCEPTED attestation commit (the exit-0 state), cloned as the
    # GIT_DIR decoy; badB is the forbidden-changelog commit (plain verdict exit 2, by more than the
    # newest); `forged` maps each post-release-path blob id AT badB to the acceptA bytes, so a forged
    # store serving them would reconstruct the accepted content under the bad ids.
    import zlib

    def _loose_zlib(body_):
        return zlib.compress(b"blob " + str(len(body_)).encode("ascii") + b"\x00" + body_, 9)

    labelD = "(R4 GIT_DIR) a decoy repository in GIT_DIR must not redirect the committed reads"
    labelO = "(R4 overlay) a forged GIT_OBJECT_DIRECTORY overlay must not substitute committed bytes"
    labelL = "(R4 loose) an overwritten loose object must fail the per-object re-hash"
    if any(_sel(x) for x in (labelD, labelO, labelL)):
        _reset_case()
        _write(RELEASES_REL, row)
        accept_a = None
        decoy = tmp / "r4-decoy"
        if (_regen_fixture_manifest(repo, "round-4 accepted base", failures, env)
                and _commit_all(repo, "round-4 accepted base")):
            accept_a = _gout(repo, "rev-parse", "HEAD")
            if _g(repo, "clone", "-q", "--no-hardlinks", str(repo), str(decoy)).returncode != 0:
                failures.append("fixture setup (round-4): could not clone the GIT_DIR decoy")
                accept_a = None
        if accept_a is None:
            failures.append("NOT RUN: the round-4 substitution cases (accepted-base setup failed)")
        else:
            good_bytes = {rel: _g(repo, "show", accept_a + ":" + rel).stdout
                          for rel in sorted(POST_RELEASE_PATHS)}
            good_oids = {rel: _gout(repo, "rev-parse", accept_a + ":" + rel)
                         for rel in sorted(POST_RELEASE_PATHS)}
            _reset_case()
            _write(RELEASES_REL, row)
            _write(CHANGELOG_REL, base_cl + "date = \"2000-01-01\"\n")
            if (_regen_fixture_manifest(repo, "round-4 forbidden-changelog base", failures, env)
                    and _commit_all(repo, "round-4 forbidden-changelog base")):
                bad_head = _gout(repo, "rev-parse", "HEAD")
                forged = {}
                for rel in sorted(POST_RELEASE_PATHS):
                    bad_oid = _gout(repo, "rev-parse", "HEAD:" + rel)
                    if bad_oid and good_oids[rel] and bad_oid != good_oids[rel]:
                        forged[bad_oid] = good_bytes[rel]
                if not forged:
                    failures.append("fixture setup (round-4): no differing blob to forge")
                if _sel(labelD):
                    # codex R4-2: with GIT_DIR exported, the UNSCRUBBED funnel reads the decoy (whose
                    # HEAD is the accepted state) and the gate printed POST-RELEASE; the scrubbed
                    # funnel reads the real repository and must refuse on the committed changelog edit.
                    os.environ["GIT_DIR"] = str(decoy / ".git")
                    try:
                        _check(repo, labelD, 2, "by more than the newest")
                    finally:
                        os.environ.pop("GIT_DIR", None)
                if _sel(labelO) and forged:
                    # claude m2 / codex R4-2 object overlay: forged loose objects serving the ACCEPTED
                    # bytes under the bad ids, fronted by GIT_OBJECT_DIRECTORY with the real store as
                    # the alternate. The scrubbed funnel must read the RECORDED bytes (asserted on the
                    # funneled _show directly, so a scrub bypass fails this case alone even where the
                    # re-hash still refuses) and the verdict must stay the refusal.
                    side = tmp / "r4-overlay-objects"
                    for oid_, body_ in forged.items():
                        d_ = side / oid_[:2]
                        d_.mkdir(parents=True, exist_ok=True)
                        (d_ / oid_[2:]).write_bytes(_loose_zlib(body_))
                    os.environ["GIT_OBJECT_DIRECTORY"] = str(side)
                    os.environ["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = str(
                        repo / ".git" / "objects")
                    try:
                        try:
                            funneled = _show(repo, bad_head, CHANGELOG_REL)
                        except GateError as exc:
                            funneled = b""
                            failures.append("object overlay: the funneled _show failed ({}); with the "
                                            "GIT_* scrub it must return the RECORDED bytes".format(exc))
                        if funneled and b"2000-01-01" not in funneled:
                            failures.append("object overlay: the funneled _show returned the "
                                            "SUBSTITUTED bytes; the GIT_* environment scrub is not "
                                            "effective (codex R4-2 / claude m2)")
                        _check(repo, labelO, 2, "by more than the newest")
                    finally:
                        os.environ.pop("GIT_OBJECT_DIRECTORY", None)
                        os.environ.pop("GIT_ALTERNATE_OBJECT_DIRECTORIES", None)
                if _sel(labelL) and forged:
                    # claude m2 overwritten-loose-object probe: the accepted bytes written INTO
                    # .git/objects under the bad ids, no environment at all; only the per-object
                    # re-hash can refuse (with it removed, the forged store reconstructs the accepted
                    # tree and the gate prints POST-RELEASE).
                    saved_, ok_l = {}, True
                    for oid_, body_ in forged.items():
                        q_ = repo / ".git" / "objects" / oid_[:2] / oid_[2:]
                        if not q_.is_file():
                            failures.append("fixture setup ({}): blob {} is not a loose object; the "
                                            "overwrite cannot be planted".format(labelL, oid_))
                            ok_l = False
                            break
                        saved_[q_] = q_.read_bytes()
                        q_.chmod(0o644)
                        q_.write_bytes(_loose_zlib(body_))
                        q_.chmod(0o444)
                    try:
                        if ok_l:
                            _check(repo, labelL, 2, "re-hash mismatch")
                    finally:
                        for q_, b_ in saved_.items():
                            q_.chmod(0o644)
                            q_.write_bytes(b_)
                            q_.chmod(0o444)

    # ---- QA round-4 claude M1: the profiles decision comes from the COMMITTED trees -----------------
    labelP = "(R4 profiles) a committed profiles artifact with the checkout copy deleted still refuses"
    if _sel(labelP):
        repo_p = _extract_to("post-release-profiles")
        if repo_p is None or not _pin_fixture_version(repo_p, failures):
            return False
        (repo_p / PROFILES_REL).write_text("format-version = 1\n", encoding="utf-8")
        _g(repo_p, "add", "-A")
        if not _regen_fixture_manifest(repo_p, labelP, failures, env):
            return False
        for args in (("add", "-A"), ("commit", "-q", "--no-verify", "-m", "release 1.0.0"),
                     ("tag", "-a", "v1.0.0", "-m", "1.0.0")):
            _g(repo_p, *args)
        c_p = _gout(repo_p, "rev-parse", "HEAD")
        t_p = _gout(repo_p, "rev-parse", "refs/tags/v1.0.0")
        (repo_p / RELEASES_REL).write_text(
            "format-version = 1\n\n" + _row_text("1.0.0", "v1.0.0", t_p, c_p), encoding="utf-8")
        if _regen_fixture_manifest(repo_p, labelP, failures, env) and _commit_all(repo_p, labelP):
            # Delete the CHECKOUT copy: the old gate (an os.lstat decision) then printed POST-RELEASE
            # exit 0 for a tree whose COMMITTED profiles artifact must fail closed (claude M1).
            (repo_p / PROFILES_REL).unlink()
            _check(repo_p, labelP, 2, "has shipped")

    # ---- QA round-4 codex R4-1 / claude m3: gate code and validator provenance ----------------------
    labelGC = "(R4 gate-code) a gate import diverging from the committed HEAD copy refuses"
    if _sel(labelGC):
        repo_g4 = _extract_to("post-release-gatecode")
        if repo_g4 is None:
            return False
        schema_p = repo_g4 / "tools" / "_release_schema.py"
        schema_p.write_text(schema_p.read_text(encoding="utf-8")
                            + "\n# QA round-4 gate-code divergence fixture marker\n", encoding="utf-8")
        if not _pin_fixture_version(repo_g4, failures):
            return False
        for args in (("add", "-A"), ("commit", "-q", "--no-verify", "-m", "release 1.0.0"),
                     ("tag", "-a", "v1.0.0", "-m", "1.0.0")):
            _g(repo_g4, *args)
        c_g4 = _gout(repo_g4, "rev-parse", "HEAD")
        t_g4 = _gout(repo_g4, "rev-parse", "refs/tags/v1.0.0")
        (repo_g4 / RELEASES_REL).write_text(
            "format-version = 1\n\n" + _row_text("1.0.0", "v1.0.0", t_g4, c_g4), encoding="utf-8")
        if _regen_fixture_manifest(repo_g4, labelGC, failures, env) and _commit_all(repo_g4, labelGC):
            # The RUNNING gate imports the live pack modules; this fixture HEAD commits a divergent
            # tools/_release_schema.py, so the byte comparison against the judged HEAD must refuse.
            _check(repo_g4, labelGC, 2, "differs from HEAD")

    labelV = "(R4 validators) the materialized committed tree runs its OWN validator copies"
    if _sel(labelV):
        repo_v = _extract_to("post-release-validators")
        if repo_v is None:
            return False
        # A path-conditional tripwire PREPENDED to the committed check_manifest.py: it exits 7 ONLY
        # when executed from a raw materialization (the aiqt-release-delta-head-/-prev- temp dirs), so
        # the fixture checkout and the gate CLI below behave normally while the gate must surface rc=7
        # from the COMMITTED copy it materializes. With the provenance fix reverted (the checkout
        # sibling running instead), the tripwire never fires and the gate prints POST-RELEASE exit 0.
        trip = ("import pathlib as _qa4_pl\n"
                "_qa4_p = str(_qa4_pl.Path(__file__).resolve())\n"
                "if \"aiqt-release-delta-head-\" in _qa4_p or \"aiqt-release-delta-prev-\" in _qa4_p:\n"
                "    raise SystemExit(7)\n")
        cm_p = repo_v / "tools" / "check_manifest.py"
        cm_p.write_text(trip + cm_p.read_text(encoding="utf-8"), encoding="utf-8")
        if not _pin_fixture_version(repo_v, failures):
            return False
        for args in (("add", "-A"), ("commit", "-q", "--no-verify", "-m", "release 1.0.0"),
                     ("tag", "-a", "v1.0.0", "-m", "1.0.0")):
            _g(repo_v, *args)
        c_v = _gout(repo_v, "rev-parse", "HEAD")
        t_v = _gout(repo_v, "rev-parse", "refs/tags/v1.0.0")
        (repo_v / RELEASES_REL).write_text(
            "format-version = 1\n\n" + _row_text("1.0.0", "v1.0.0", t_v, c_v), encoding="utf-8")
        if _regen_fixture_manifest(repo_v, labelV, failures, env) and _commit_all(repo_v, labelV):
            # The gate runs as a CLI FROM the fixture checkout (its code equals the fixture HEAD, so
            # the gate-code comparison passes and the validator provenance alone is under test).
            try:
                proc_v = subprocess.run(
                    [sys.executable, "-B", str(repo_v / "tools" / "check_release_delta.py")],
                    cwd=str(repo_v), capture_output=True, env=env, timeout=900)
            except (OSError, subprocess.TimeoutExpired) as exc:
                failures.append("fixture setup ({}): could not run the fixture gate CLI ({})".format(
                    labelV, exc))
            else:
                out_v = (proc_v.stdout + proc_v.stderr).decode("utf-8", "replace")
                if proc_v.returncode != 2 or "rc=7" not in out_v:
                    failures.append("{}: expected exit 2 with rc=7 from the materialized committed "
                                    "copy (got rc={}: {})".format(labelV, proc_v.returncode,
                                                                   out_v.strip()[-300:]))

    # ---- QA round 5 (D-397-REEXEC-FROM-COMMITTED): launch re-execution and committed routing --------
    # The launch-attack cases drive the EXACT CI launch (python3 -I -B tools/check_release_delta.py) so
    # the stage-1 re-execution of the COMMITTED gate copy is itself under test, not the in-process
    # run(). They use a DEDICATED fresh fixture (immune to any prior case's state) carrying a STALE
    # committed post-release state (a release row with no manifest regeneration), whose HONEST verdict
    # is exit 2 at the head-manifest-freshness leg. A baseline run with NO attack is asserted first, so
    # the discriminator target is proven valid; then each attack must leave that verdict unchanged.
    # With the round-5 fix reverted each attack flips to exit 0 (verified out-of-tree; see report).
    labelR5 = "(R5 launch) the stage-1 committed re-execution and committed routing"
    if _sel("(R5 ") or _sel("(R6 ") or _sel("(R7 "):
        r5 = _extract_to("post-release-r5")
        if r5 is None or not _pin_fixture_version(r5, failures):
            return False
        r5_base_cl = ('[[release]]\nversion = "0.9.0"\n\n[[release]]\nversion = "1.0.0"\n\n'
                      '[release.artifacts]\nsbom = "sha256:0000"\n')
        (r5 / CHANGELOG_REL).write_text(r5_base_cl, encoding="utf-8")
        _g(r5, "add", "-A")
        if not _regen_fixture_manifest(r5, "post-release-r5 base", failures, env):
            return False
        for a_ in (("add", "-A"), ("commit", "-q", "--no-verify", "-m", "release 1.0.0"),
                   ("tag", "-a", "v1.0.0", "-m", "1.0.0")):
            if _g(r5, *a_).returncode != 0:
                failures.append("fixture setup (R5): could not build the dedicated repo")
                return False
        r5_commit1 = _gout(r5, "rev-parse", "HEAD")
        r5_tobj = _gout(r5, "rev-parse", "refs/tags/v1.0.0")
        r5_row = "format-version = 1\n\n" + _row_text("1.0.0", "v1.0.0", r5_tobj, r5_commit1)

        def _r5_stale():
            # Reset to the release commit and append the row WITHOUT regenerating the manifest, so the
            # committed state is stale (honest exit 2 at head manifest freshness). Returns True on a
            # clean committed stale state whose baseline verdict is the expected exit 2.
            for a_ in (("reset", "-q", "--hard", r5_commit1), ("clean", "-q", "-fdx")):
                _g(r5, *a_)
            (r5 / RELEASES_REL).write_text(r5_row, encoding="utf-8")
            return _commit_all(r5, "R5 stale state")

        def _cli5(label_, extra_env=None):
            env_ = dict(env)
            env_.update(extra_env or {})
            try:
                proc_ = subprocess.run(
                    [sys.executable, "-I", "-B", str(r5 / "tools" / "check_release_delta.py")],
                    cwd=str(r5), capture_output=True, env=env_, timeout=1500)
            except (OSError, subprocess.TimeoutExpired) as exc:
                failures.append("fixture setup ({}): could not run the R5 gate CLI "
                                "({})".format(label_, exc))
                return None, ""
            return proc_.returncode, (proc_.stdout + proc_.stderr).decode("utf-8", "replace")

        def _cli5_expect(label_, want_rc, want_msg, extra_env=None):
            rc_, out_ = _cli5(label_, extra_env)
            if rc_ is None:
                return
            if rc_ != want_rc or want_msg not in out_:
                failures.append("{}: expected exit {} with {!r} under the CI launch "
                                "(got rc={}: {})".format(label_, want_rc, want_msg, rc_,
                                                                      out_.strip()[-300:]))

        # BASELINE: the stale committed state, no attack, must be exit 2 at head manifest freshness.
        # This proves the discriminator target for every attack below (and that stage 1/stage 2 and the
        # committed routing reach that leg over the committed revision).
        if _r5_stale():
            _cli5_expect(labelR5 + " baseline (stale, no attack)", 2, "head manifest freshness")

        _FAKE_PASS = "release-delta: POST-RELEASE"
        _SHADOW = ("import os\n"
                   "print(" + repr(_FAKE_PASS + " (R5 planted module, must never run)") + ")\n"
                   "os._exit(0)\n")

        label5a = "(R5 shadow) an untracked tools/tempfile.py shadow module never runs"
        if _sel(label5a) and _r5_stale():
            # Reproduction (a): the pre-fix gate imported the CHECKOUT tools/ first, so this untracked
            # shadow of a lazily imported stdlib module faked a pass and exited 0. Stage 2 runs from the
            # materialized COMMITTED tree (no untracked file), so the shadow never loads.
            (r5 / "tools" / "tempfile.py").write_text(_SHADOW, encoding="utf-8")
            try:
                _cli5_expect(label5a, 2, "head manifest freshness")
            finally:
                tp = r5 / "tools" / "tempfile.py"
                if tp.exists():
                    tp.unlink()

        label5b = "(R5 pyc) a crafted __pycache__ .pyc behind a byte-identical .py never loads"
        if _sel(label5b) and _r5_stale():
            # Reproduction (b): a crafted UNCHECKED_HASH .pyc for a gate module, planted in the CHECKOUT
            # tools/__pycache__ while the .py stays byte-identical to HEAD. A checkout-running gate under
            # -B still READS an existing .pyc; stage 2 runs from the materialized committed tree under -X
            # pycache_prefix=<fresh dir>, so the checkout .pyc is never consulted.
            import importlib.util as _ilu
            import py_compile as _pyc
            src_dir = tmp / "r5-pyc-src"
            src_dir.mkdir(exist_ok=True)
            src = src_dir / "_release_schema.py"
            src.write_text("import os\n"
                           "print(" + repr(_FAKE_PASS + " (R5 crafted pyc, must never load)") + ")\n"
                           "os._exit(0)\n", encoding="utf-8")
            cfile = _ilu.cache_from_source(str(src))
            ok_pyc = True
            try:
                _pyc.compile(str(src), cfile=cfile,
                             invalidation_mode=_pyc.PycInvalidationMode.UNCHECKED_HASH)
            except (OSError, _pyc.PyCompileError, ValueError) as exc:
                failures.append("fixture setup ({}): could not build the crafted .pyc "
                                "({})".format(label5b, exc))
                ok_pyc = False
            if ok_pyc:
                co_pyc = r5 / "tools" / "__pycache__"
                co_pyc.mkdir(parents=True, exist_ok=True)
                planted = co_pyc / Path(cfile).name
                planted.write_bytes(Path(cfile).read_bytes())
                try:
                    _cli5_expect(label5b, 2, "head manifest freshness")
                finally:
                    if planted.exists():
                        planted.unlink()

        label5c = "(R5 fsmonitor) a repo-config core.fsmonitor hook neither runs nor flips the verdict"
        if _sel(label5c) and _r5_stale():
            # Reproduction (c): a repo-config fsmonitor program that, when a worktree-scanning git call
            # runs it, plants tools/tempfile.py AND touches a marker. Every funneled git call now pins
            # core.fsmonitor=false, so the hook never runs (marker absent); the verdict stays exit 2.
            marker = r5 / "tools" / "r5-fsmon-ran.marker"
            hook = tmp / "fsmon-plant.sh"
            hook.write_text("#!/bin/sh\n"
                            ": > " + str(marker) + "\n"
                            ": > " + str(r5 / "tools" / "tempfile.py") + "\n"
                            "exit 1\n", encoding="utf-8")
            hook.chmod(0o755)
            _g(r5, "config", "core.fsmonitor", str(hook))
            try:
                _cli5_expect(label5c, 2, "head manifest freshness")
                if marker.exists():
                    failures.append("{}: the core.fsmonitor hook RAN under the gate launch; the "
                                    "core.fsmonitor=false pin is not effective".format(label5c))
            finally:
                _g(r5, "config", "--unset-all", "core.fsmonitor")
                for p_ in (marker, r5 / "tools" / "tempfile.py"):
                    if p_.exists():
                        p_.unlink()

        label5f = ("(R5 filter/hook) a repo-config clean filter and post-index-change hook never run "
                   "under the drift advisory")
        if _sel(label5f) and _r5_stale():
            # Sibling of reproduction (c): `git status` refreshes the index, so it runs the clean filter
            # of a stat-dirty tracked file and, when it writes the refreshed index, the post-index-change
            # hook; core.fsmonitor=false stops neither. The drift advisory therefore reads diff-index and
            # ls-files only. Untracked repo-local config: .git/info/attributes names the filter for every
            # path, core.hooksPath names a hook directory, and a tracked file is made stat-dirty with
            # identical content. Neither marker may appear and the verdict stays exit 2.
            m_filter = tmp / "r5-filter-ran.marker"
            m_hook = tmp / "r5-hook-ran.marker"
            hooks_dir = tmp / "r5-hooks"
            hooks_dir.mkdir(exist_ok=True)
            (hooks_dir / "post-index-change").write_text(
                "#!/bin/sh\n: > " + str(m_hook) + "\n", encoding="utf-8")
            (hooks_dir / "post-index-change").chmod(0o755)
            info_attr = r5 / ".git" / "info" / "attributes"
            info_attr.parent.mkdir(parents=True, exist_ok=True)
            info_attr.write_text("* filter=r5plant\n", encoding="utf-8")
            _g(r5, "config", "filter.r5plant.clean", "sh -c ': > " + str(m_filter) + "; cat'")
            _g(r5, "config", "core.hooksPath", str(hooks_dir))
            dirty = r5 / CHANGELOG_REL
            st_ = dirty.stat()
            os.utime(dirty, ns=(st_.st_atime_ns, st_.st_mtime_ns + 5 * 10 ** 9))
            for m_ in (m_filter, m_hook):
                if m_.exists():
                    m_.unlink()
            try:
                _cli5_expect(label5f, 2, "head manifest freshness")
                for m_, what_ in ((m_filter, "clean filter"), (m_hook, "post-index-change hook")):
                    if m_.exists():
                        failures.append("{}: the repo-config {} RAN under the gate launch; the drift "
                                        "advisory must not refresh the index".format(label5f, what_))
            finally:
                _g(r5, "config", "--unset-all", "filter.r5plant.clean")
                _g(r5, "config", "--unset-all", "core.hooksPath")
                for p_ in (info_attr, m_filter, m_hook):
                    if p_.exists():
                        p_.unlink()

        label5p = "(R5 PYTHONPATH) an inherited PYTHONPATH sitecustomize never reaches a child"
        if _sel(label5p) and _r5_stale():
            # Reproduction M2: a sitecustomize on PYTHONPATH that exits 0 when a validator child runs.
            # The gate runs under -I, but the pre-fix child launches dropped that isolation; every child
            # now launches under -I, so PYTHONPATH and sitecustomize are ignored and the verdict stays
            # exit 2.
            site_dir = tmp / "r5-sitecustomize"
            site_dir.mkdir(exist_ok=True)
            (site_dir / "sitecustomize.py").write_text(
                "import os, sys\n"
                "_a0 = sys.argv[0] if sys.argv else ''\n"
                "if _a0.endswith(('gen_manifest.py', 'check_manifest.py', 'gen_renderers.py',\n"
                "                 'check_clauses.py')):\n"
                "    os._exit(0)\n", encoding="utf-8")
            _cli5_expect(label5p, 2, "head manifest freshness",
                         extra_env={"PYTHONPATH": str(site_dir)})

        label5r = "(R5 routing) checkout routing records reverted to the release judged from committed"
        if _sel(label5r) and _r5_stale():
            # Reproduction M1: with the committed state stale (honest exit 2), the CHECKOUT copies of the
            # routing records (releases.toml, changelog.toml) are reverted to the release commit's
            # genesis-shaped bytes. The pre-fix router read the checkout and branched to GENESIS/DELTA
            # (exit 0); committed routing reads HEAD's committed records and still routes POST-RELEASE,
            # so the stale committed manifest is still caught (exit 2 head manifest freshness).
            for rel in (RELEASES_REL, CHANGELOG_REL):
                _g(r5, "checkout", r5_commit1, "--", rel)
            try:
                _cli5_expect(label5r, 2, "head manifest freshness")
            finally:
                _g(r5, "checkout", "HEAD", "--", RELEASES_REL, CHANGELOG_REL)

        label5s = "(R5 stage) the accepted committed state re-executes the committed copy and passes"
        if _sel(label5s):
            # Positive control: a clean one-row attestation state (manifest regenerated), through the
            # EXACT CI launch, must re-execute the committed gate copy (stage 1) and print POST-RELEASE
            # exit 0. A stage-1 materialization or re-exec regression surfaces here as a non-zero exit.
            for a_ in (("reset", "-q", "--hard", r5_commit1), ("clean", "-q", "-fdx")):
                _g(r5, *a_)
            (r5 / RELEASES_REL).write_text(r5_row, encoding="utf-8")
            if _regen_fixture_manifest(r5, "post-release-r5 accepted", failures, env) \
                    and _commit_all(r5, "R5 accepted state"):
                _cli5_expect(label5s, 0, "release-delta: POST-RELEASE")

        # ---- QA round-6 codex blocker 1 / claude 3: a git FAILURE is never the single-stage
        # fallback. Each case damages the repository (or removes git from PATH) and plants a
        # checkout tools/_gen_common.py tripwire that fakes a pass if ANY checkout module is
        # imported: the gate must exit 2 from stage 1 with the tripwire inert. Only a positively
        # identified absent committed state (no repository with no .git entry, or an unborn HEAD)
        # may reach the checkout imports.
        _R6_TRIP = ("import os\n"
                    "print(" + repr(_FAKE_PASS + " (R6 checkout import, must never run)")
                    + ", flush=True)\n"
                    "os._exit(0)\n")

        label6h = "(R6 corrupt HEAD) a damaged repository fails closed, never the checkout fallback"
        if _sel(label6h) and _r5_stale():
            head6 = r5 / ".git" / "HEAD"
            orig_head6 = head6.read_bytes()
            gc6 = r5 / "tools" / "_gen_common.py"
            orig_gc6 = gc6.read_bytes()
            gc6.write_text(_R6_TRIP, encoding="utf-8")
            head6.write_bytes(b"garbage, neither a ref nor an object id\n")
            try:
                _cli5_expect(label6h, 2, "no probe positively established")
            finally:
                head6.write_bytes(orig_head6)
                gc6.write_bytes(orig_gc6)

        label6b = "(R6 bad config) a config parse failure fails closed, never the checkout fallback"
        if _sel(label6b) and _r5_stale():
            cfg6 = r5 / ".git" / "config"
            orig_cfg6 = cfg6.read_bytes()
            gc6 = r5 / "tools" / "_gen_common.py"
            orig_gc6 = gc6.read_bytes()
            gc6.write_text(_R6_TRIP, encoding="utf-8")
            cfg6.write_bytes(orig_cfg6 + b"\n[broken\n")
            try:
                _cli5_expect(label6b, 2, "no probe positively established")
            finally:
                cfg6.write_bytes(orig_cfg6)
                gc6.write_bytes(orig_gc6)

        label6g = "(R6 no git) git absent from PATH is a clean exit 2, never a traceback"
        if _sel(label6g) and _r5_stale():
            empty6 = tmp / "r6-empty-path"
            empty6.mkdir(exist_ok=True)
            _cli5_expect(label6g, 2, "cannot launch git", extra_env={"PATH": str(empty6)})

        label6i = "(R6 stage2 -I) a PYTHONPATH sitecustomize keyed to the stage-2 launch never fires"
        if _sel(label6i) and _r5_stale():
            # QA round-6 claude 1: the (R5 PYTHONPATH) sitecustomize fires only for VALIDATOR
            # children, so removing -I from the stage-1 child launch went undetected. This payload
            # fires exactly for check_release_delta.py launched with --stage2-tree: with -I on the
            # stage-1 child it never runs; without it, stage 2 fakes a pass and the case goes red.
            site6 = tmp / "r6-sitecustomize"
            site6.mkdir(exist_ok=True)
            (site6 / "sitecustomize.py").write_text(
                "import os, sys\n"
                "_a0 = sys.argv[0] if sys.argv else ''\n"
                "if _a0.endswith('check_release_delta.py') and '--stage2-tree' in sys.argv:\n"
                "    print(" + repr(_FAKE_PASS + " (R6 sitecustomize, must never run)") + ")\n"
                "    os._exit(0)\n", encoding="utf-8")
            _cli5_expect(label6i, 2, "head manifest freshness",
                         extra_env={"PYTHONPATH": str(site6)})

        # ---- QA round-7 codex blocker: git DISCOVERY searches ancestor directories, so a damaged
        # ANCESTOR repository (a corrupt .git/HEAD, a .git file naming a missing gitdir) fails git
        # with the same "not a git repository" wording as true absence. Absence may hold only when
        # no .git entry exists at the gate root or ANY ancestor; each case plants the checkout
        # tripwire and the gate must exit 2 from stage 1 with the tripwire inert. The control case
        # (no .git anywhere in the ancestry) must still reach the checkout gate by design.
        label7a = "(R7 corrupt ancestor) a damaged ANCESTOR repository fails closed, never the fallback"
        label7g = ("(R7 dangling ancestor .git) an ancestor .git file naming a missing gitdir "
                   "fails closed, never the fallback")
        label7n = ("(R7 no repository) a root with no .git in any ancestor still reaches the "
                   "checkout gate")
        if _sel(label7a) or _sel(label7g) or _sel(label7n):
            gate_bytes7 = (r5 / "tools" / "check_release_delta.py").read_bytes()

            def _mini_gate7(base_):
                proj_ = base_ / "project"
                (proj_ / "tools").mkdir(parents=True)
                (proj_ / "tools" / "check_release_delta.py").write_bytes(gate_bytes7)
                (proj_ / "tools" / "_gen_common.py").write_text(_R6_TRIP, encoding="utf-8")
                return proj_

            def _gate_cli7(label_, proj_, want_rc, want_msg):
                try:
                    proc_ = subprocess.run(
                        [sys.executable, "-I", "-B",
                         str(proj_ / "tools" / "check_release_delta.py")],
                        cwd=str(proj_), capture_output=True, env=env, timeout=600)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    failures.append("fixture setup ({}): could not run the gate CLI ({})".format(
                        label_, exc))
                    return
                out_ = (proc_.stdout + proc_.stderr).decode("utf-8", "replace")
                if proc_.returncode != want_rc or want_msg not in out_:
                    failures.append("{}: expected exit {} with {!r} (got rc={}: {})".format(
                        label_, want_rc, want_msg, proc_.returncode, out_.strip()[-300:]))

        if _sel(label7a):
            anc7 = tmp / "r7-corrupt-ancestor"
            proj7 = _mini_gate7(anc7)
            _g(anc7, "init", "-q")
            (anc7 / ".git" / "HEAD").write_bytes(b"garbage, neither a ref nor an object id\n")
            _gate_cli7(label7a, proj7, 2, "no probe positively established")
            if _stage1_no_committed_state(str(proj7)) or _no_committed_state(proj7):
                failures.append(label7a + ": both absence predicates must refuse a damaged "
                                "ancestor repository (git discovery searches ancestors)")

        if _sel(label7g):
            dgl7 = tmp / "r7-dangling-ancestor"
            proj7g = _mini_gate7(dgl7)
            (dgl7 / ".git").write_text("gitdir: " + str(dgl7 / "missing-gitdir") + "\n",
                                       encoding="utf-8")
            _gate_cli7(label7g, proj7g, 2, "no probe positively established")
            if _stage1_no_committed_state(str(proj7g)) or _no_committed_state(proj7g):
                failures.append(label7g + ": both absence predicates must refuse a dangling "
                                "ancestor .git file")

        if _sel(label7n):
            import shutil as _shutil7
            import tempfile as _tempfile7
            base7 = None
            for cand7 in (tmp, Path(_tempfile7.gettempdir()), Path("/var/tmp")):
                if not _stage1_dotgit_in_ancestry(str(cand7)):
                    base7 = cand7
                    break
            if base7 is None:
                failures.append(label7n + ": every candidate temp path has a .git ancestor; the "
                                "true no-repository control needs a .git-free path (fixture-env)")
            else:
                nr7 = Path(_tempfile7.mkdtemp(prefix="aiqt-r7-norepo-", dir=str(base7)))
                try:
                    _gate_cli7(label7n, _mini_gate7(nr7), 0,
                               "(R6 checkout import, must never run)")
                finally:
                    _shutil7.rmtree(nr7, ignore_errors=True)

        # ---- QA round-7 claude F3: a repo-config core.fsmonitor hook is attacker-chosen code and
        # must never run on the checkout-judged branches either. gen_manifest.git_tracked (the one
        # tracked-set enumerator those branches consume, directly and through the gen_manifest /
        # check_manifest validator children) and _index_materialized_tree carry the funnels' pins;
        # this asserts the GENESIS route end-to-end (both enumerators are also asserted directly
        # in the isolated self-test; the delta branch reaches them through the same two sites).
        label7f = ("(R7 fsmonitor genesis) a repo-config fsmonitor hook never runs on a "
                   "checkout-judged branch")
        if _sel(label7f):
            gfx7 = _extract_to("r7-fsmon-genesis")
            if gfx7 is not None and _pin_fixture_version(gfx7, failures):
                _git_init_commit(gfx7, "r7 fsmonitor genesis fixture", init=False)

                def _cli7f(label_):
                    try:
                        proc_ = subprocess.run(
                            [sys.executable, "-I", "-B",
                             str(gfx7 / "tools" / "check_release_delta.py")],
                            cwd=str(gfx7), capture_output=True, env=env, timeout=1500)
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        failures.append("fixture setup ({}): could not run the gate CLI "
                                        "({})".format(label_, exc))
                        return None, ""
                    return proc_.returncode, (proc_.stdout + proc_.stderr).decode(
                        "utf-8", "replace")

                rc7b, out7b = _cli7f(label7f + " baseline")
                if rc7b is not None and (rc7b != 0 or "release-delta: GENESIS" not in out7b):
                    failures.append("{}: the genesis baseline must exit 0 on the GENESIS route "
                                    "(got rc={}: {})".format(label7f, rc7b, out7b.strip()[-300:]))
                elif rc7b is not None:
                    mark7 = tmp / "r7-fsmon-ran.marker"
                    hook7 = tmp / "r7-fsmon.sh"
                    hook7.write_text("#!/bin/sh\n: >> " + str(mark7) + "\nexit 1\n",
                                     encoding="utf-8")
                    hook7.chmod(0o755)
                    _g(gfx7, "config", "core.fsmonitor", str(hook7))
                    try:
                        rc7m, out7m = _cli7f(label7f)
                        if rc7m is not None and (rc7m != 0
                                                 or "release-delta: GENESIS" not in out7m):
                            failures.append("{}: the genesis verdict must be unchanged under the "
                                            "hook config (got rc={}: {})".format(
                                                label7f, rc7m, out7m.strip()[-300:]))
                        if mark7.exists():
                            failures.append("{}: the core.fsmonitor hook RAN on the genesis "
                                            "branch; the git_tracked / _index_materialized_tree "
                                            "pins are not effective".format(label7f))
                    finally:
                        _g(gfx7, "config", "--unset-all", "core.fsmonitor")


    # ---- QA round-5 claude m2: the per-object re-hash covers a commit, tree and tag, not only a blob -
    # The round-4 (R4 loose) case forged only a BLOB. verified_object re-hashes EVERY object type it
    # reads through cat-file --batch (git does not verify --batch bodies), so a tampered loose commit,
    # tree or tag must raise SchemaError "re-hash mismatch". These assert verified_object DIRECTLY (the
    # read path the materialization and ancestry walk use), so the re-hash is the sole catcher: a mutant
    # that re-hashes only blobs returns the tampered body with no error and each case fails (verified
    # out-of-tree).
    labelFA = "(R5 forged ancestry) a forged INTERMEDIATE commit object never grafts HEAD onto the release"
    if _sel(labelFA):
        # QA round-5 claude m1: release commit R; an orphan commit O carrying R's tree; HEAD H whose
        # parent is O. The loose object for O is then overwritten (filename kept) with O's body plus
        # `parent R`, so merge-base (which reads O unverified) answers "R is an ancestor of HEAD". The
        # verified parent-chain walk re-hashes O and must refuse; with the walk removed this passes.
        import zlib as _zlib_a
        anc = tmp / "r5-ancestry"
        anc.mkdir(exist_ok=True)

        def _ga(*a_):
            return subprocess.run(["git", "-C", str(anc), *a_], capture_output=True, env=env)

        _ga("init", "-q")
        (anc / "f.txt").write_text("release\n", encoding="utf-8")
        _ga("add", "-A")
        _ga("commit", "-q", "--no-verify", "-m", "release")
        a_rel = _gout(anc, "rev-parse", "HEAD")
        a_tree = _gout(anc, "rev-parse", "HEAD^{tree}")
        a_orphan = subprocess.run(["git", "-C", str(anc), "commit-tree", a_tree, "-m", "orphan"],
                                  capture_output=True, text=True, env=env).stdout.strip()
        a_head = subprocess.run(["git", "-C", str(anc), "commit-tree", a_tree, "-p", a_orphan,
                                 "-m", "head"], capture_output=True, text=True, env=env).stdout.strip()
        _ga("reset", "-q", "--hard", a_head)
        q_ = anc / ".git" / "objects" / a_orphan[:2] / a_orphan[2:]
        if not q_.is_file() or len(a_head) != len(a_rel):
            failures.append("fixture setup ({}): the orphan commit is not loose".format(labelFA))
        else:
            body_ = subprocess.run(["git", "-C", str(anc), "cat-file", "commit", a_orphan],
                                   capture_output=True, env=env).stdout
            hdr_, sep_, msg_ = body_.partition(b"\n\n")
            forged_ = hdr_.replace(b"\n", b"\nparent " + a_rel.encode("ascii") + b"\n", 1) + sep_ + msg_
            q_.chmod(0o644)
            q_.write_bytes(_zlib_a.compress(b"commit " + str(len(forged_)).encode("ascii") + b"\x00"
                                            + forged_, 9))
            q_.chmod(0o444)
            _release_schema._VERIFIED_OBJECT_CACHE.pop(a_orphan, None)
            mb_ = _git_raw(anc, ["merge-base", "--is-ancestor", a_rel, "HEAD"])
            if mb_.returncode != 0:
                failures.append("fixture setup ({}): the forged parent did not graft for merge-base, so "
                                "the case would not discriminate".format(labelFA))
            else:
                try:
                    _assert_head_ancestry(anc, a_rel)
                    failures.append("{}: the forged intermediate commit was accepted as ancestry; the "
                                    "verified parent-chain walk did not refuse".format(labelFA))
                except GateError as exc:
                    if "re-hash mismatch" not in str(exc):
                        failures.append("{}: expected a re-hash mismatch, got: {}".format(labelFA, exc))
            _release_schema._VERIFIED_OBJECT_CACHE.pop(a_orphan, None)

    labelFC = "(R5 forged commit) a tampered loose COMMIT object fails the per-object re-hash"
    labelFT = "(R5 forged tree) a tampered loose TREE object fails the per-object re-hash"
    labelFG = "(R5 forged tag) a tampered loose annotated TAG object fails the per-object re-hash"
    labelFB = "(R5 forged blob) a tampered loose BLOB object fails the per-object re-hash"
    if any(_sel(x) for x in (labelFC, labelFT, labelFG, labelFB)):
        import zlib as _zlib5

        def _loose_path(repo_, oid_):
            return repo_ / ".git" / "objects" / oid_[:2] / oid_[2:]

        def _raw_object(repo_, oid_):
            t_ = _gout(repo_, "cat-file", "-t", oid_)
            b_ = subprocess.run(["git", "-C", str(repo_), "cat-file", t_, oid_],
                                capture_output=True).stdout
            return t_, b_

        def _forge_one(label_, oid_, miss_):
            # Tamper the loose object at oid_ (same declared type, one appended byte), keeping the
            # FILENAME = the original id, then assert verified_object re-hashes and refuses. The
            # verified-object cache is cleared around the probe so a prior clean read cannot mask it.
            q_ = _loose_path(repo, oid_)
            if not q_.is_file():
                failures.append("fixture setup ({}): {}".format(label_, miss_))
                return
            t_, b_ = _raw_object(repo, oid_)
            saved_ = q_.read_bytes()
            _release_schema._VERIFIED_OBJECT_CACHE.pop(oid_, None)
            q_.chmod(0o644)
            q_.write_bytes(_zlib5.compress(t_.encode("ascii") + b" "
                                           + str(len(b_) + 1).encode("ascii") + b"\x00" + b_ + b"\n", 9))
            q_.chmod(0o444)
            try:
                _release_schema.verified_object(repo, oid_)
                failures.append("{}: verified_object accepted a tampered {} object; the "
                                "per-object re-hash did not refuse".format(label_, t_))
            except _release_schema.SchemaError as exc:
                if "re-hash mismatch" not in str(exc):
                    failures.append("{}: expected a re-hash mismatch, got: {}".format(
                        label_, exc))
            finally:
                q_.chmod(0o644)
                q_.write_bytes(saved_)
                q_.chmod(0o444)
                _release_schema._VERIFIED_OBJECT_CACHE.pop(oid_, None)

        _reset_case()
        _write(RELEASES_REL, row)
        if _regen_fixture_manifest(repo, "post-release forged-object base", failures, env) \
                and _commit_all(repo, "post-release forged-object base"):
            if _sel(labelFC):
                _forge_one(labelFC, _gout(repo, "rev-parse", "HEAD"), "HEAD commit is not loose")
            if _sel(labelFT):
                _forge_one(labelFT, _gout(repo, "rev-parse", "HEAD:.aiqt"), "subtree is not loose")
            if _sel(labelFG):
                _forge_one(labelFG, _gout(repo, "rev-parse", "refs/tags/v1.0.0"),
                           "tag object is not loose")
            if _sel(labelFB):
                _forge_one(labelFB, _gout(repo, "rev-parse", "HEAD:" + RELEASES_REL),
                           "release-row blob is not loose")

    return True


def self_test_main():
    from _git_fixture_env import fixture_git_lifecycle, scrub_git_environment
    scrub_git_environment()
    with fixture_git_lifecycle():
        return _self_test_main_isolated()


def _self_test_main_isolated():  # noqa: C901  a flat sequence of independent classification cases
    # Hermetic git fixtures (test-hermeticity): every fixture git call below inherits
    # os.environ, where an inherited GIT_INDEX_FILE / GIT_DIR (git exports these to hook
    # children) would redirect the fixture's init/add/commit into the CALLER's repository.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _git_fixture_env import scrub_git_environment
    scrub_git_environment()
    failures = []

    # F-TOML-BARE-VALUEERROR-CLASS: a predecessor TOML carrying an over-long integer literal (a BARE
    # ValueError) or a 1200-deep nested array (a RecursionError) must fail closed as GateError at the
    # _show_toml parse locus; run()'s ValueError backstop never covered the RecursionError member. The digit
    # and recursion limits are pinned to the CPython defaults (test-hermeticity) and restored in finally.
    import tempfile as _tempfile
    with _tempfile.TemporaryDirectory(prefix="aiqt-delta-toml-class-") as tc_dir:
        tc_repo = Path(tc_dir)
        (tc_repo / "bigint.toml").write_text("big = " + "9" * 4400 + "\n", encoding="utf-8")
        (tc_repo / "deep.toml").write_text("deep = " + "[" * 1200 + "]" * 1200 + "\n", encoding="utf-8")
        _git_init_commit(tc_repo, "toml class fixtures")
        prev_digits, prev_reclimit = sys.get_int_max_str_digits(), sys.getrecursionlimit()
        sys.set_int_max_str_digits(4300)
        sys.setrecursionlimit(1000)
        try:
            for tc_name in ("bigint.toml", "deep.toml"):
                try:
                    _show_toml(tc_repo, "HEAD", tc_name)
                    failures.append("_show_toml accepted {}".format(tc_name))
                except GateError:
                    pass
                except (ValueError, RecursionError) as exc:
                    failures.append("_show_toml let a bare {} escape on {}".format(type(exc).__name__, tc_name))
        finally:
            sys.setrecursionlimit(prev_reclimit)
            sys.set_int_max_str_digits(prev_digits)

    def _rows(*specs):
        # specs: (kind, target, release[, impact-or-old-class][, new-class]). The row's `id` IS the target
        # (finding 2: id is the consumption key). renderer-semantics carries impact in spec[3]; class-change
        # carries old-class in spec[3] and new-class in spec[4] (finding 3: bound to the observed change);
        # default-correction gets WELL-FORMED synthetic evidence (finding 3: real URL/dates/measurement).
        out = []
        for spec in specs:
            kind, target, release = spec[0], spec[1], spec[2]
            row = {"id": target, "kind": kind, "release": release, "impact": "x", "rationale": "r"}
            if kind == "renderer-semantics":
                row["impact"] = spec[3] if len(spec) > 3 else "byte-only"
            if kind == "class-change":
                row["old-class"] = spec[3] if len(spec) > 3 else "pack-immutable"
                row["new-class"] = spec[4] if len(spec) > 4 else "derived"
            if kind == "default-correction":
                row.update({"captured-source": "https://docs.example.invalid/codex-agents-cap",
                            "capture-date": "2026-08-24", "observed-measurement": "61117 bytes",
                            "observed-date": "2026-08-24", "prefix-superset-reference": "qa/prefix.toml"})
            out.append(row)
        return normalize_dispositions(out)

    def _inv(*pairs):
        return [{"clause-id": cid, "canonical-text": text} for cid, text in pairs]

    # --- CLAUSE TEXT + ID LIFECYCLE leg (VC-4 QA #3) ----------------------------------------------
    def _reg(born=(), tombstone=(), successor=()):
        # each arg is an iterable of (id, release); returns an id-history register dict.
        return {"born": [{"id": i, "born-release": r} for i, r in born],
                "tombstone": [{"id": i, "retired-release": r} for i, r in tombstone],
                "successor": [{"id": i, "retired-release": r, "successor": i + "x"} for i, r in successor]}

    # removed clause + its corpus, each with a retirement row dated the release: two MAJOR events, no finding.
    reg = _reg(tombstone=(("calpha.1", "1.1.0"), ("calpha", "1.1.0")))
    ev, fs = clause_text_leg(_inv(("calpha.1", "x")), _inv(), reg, _rows(), "1.1.0")
    if fs or _floors(ev) != [("clause", "calpha", "corpus-removed", MAJOR),
                             ("clause", "calpha.1", "removed", MAJOR)]:
        failures.append("clause removed-with-retirement: expected clause+corpus MAJOR events, no finding")
    # removed with NO retirement row: a register-incompleteness finding (7.3).
    ev, fs = clause_text_leg(_inv(("calpha.1", "x")), _inv(), _reg(), _rows(), "1.1.0")
    if not any("no tombstone or successor" in f for f in fs):
        failures.append("clause removed-no-row: expected a register-incompleteness finding")
    # removed with a retirement row dated the WRONG release: a release-specificity finding (7.3).
    ev, fs = clause_text_leg(_inv(("calpha.1", "x")), _inv(),
                             _reg(tombstone=(("calpha.1", "1.0.0"), ("calpha", "1.0.0"))), _rows(), "1.1.0")
    if not any("not the release under build" in f for f in fs):
        failures.append("clause removed wrong-release retirement: expected a release-specificity finding")
    # removed with TWO retirement rows for one id: a uniqueness finding (7.3).
    ev, fs = clause_text_leg(_inv(("calpha.1", "x")), _inv(),
                             _reg(tombstone=(("calpha.1", "1.1.0"), ("calpha", "1.1.0")),
                                  successor=(("calpha.1", "1.1.0"),)), _rows(), "1.1.0")
    if not any("exactly one is allowed" in f for f in fs):
        failures.append("clause removed duplicate-retirement: expected a uniqueness finding")
    # added clause + its corpus, each with a born row at the release: two MINOR events, no finding.
    ev, fs = clause_text_leg(_inv(), _inv(("cbeta1.1", "y")),
                             _reg(born=(("cbeta1.1", "1.1.0"), ("cbeta1", "1.1.0"))), _rows(), "1.1.0")
    if fs or _floors(ev) != [("clause", "cbeta1", "corpus-added", MINOR),
                             ("clause", "cbeta1.1", "added", MINOR)]:
        failures.append("clause added-with-born: expected clause+corpus MINOR events, no finding")
    # added with NO born row: a born-row finding (7.3 born-in-same-release).
    ev, fs = clause_text_leg(_inv(), _inv(("cbeta1.1", "y")), _reg(), _rows(), "1.1.0")
    if not any("no id-history born row" in f for f in fs):
        failures.append("clause added-no-born: expected a born-row finding")
    # adding a clause to an EXISTING corpus: only the clause is 'added' (the corpus persists, no new event).
    ev, fs = clause_text_leg(_inv(("cgamma.1", "a")), _inv(("cgamma.1", "a"), ("cgamma.2", "b")),
                             _reg(born=(("cgamma.2", "1.1.0"),)), _rows(), "1.1.0")
    if fs or _floors(ev) != [("clause", "cgamma.2", "added", MINOR)]:
        failures.append("clause added-existing-corpus: expected a lone clause MINOR event, no finding")
    # same-id text change, behaviour-neutral row: PATCH (no add/remove, corpus unchanged).
    ev, fs = clause_text_leg(_inv(("c.1", "old")), _inv(("c.1", "new")), _reg(),
                             _rows(("behaviour-neutral", "c.1", "1.1.0")), "1.1.0")
    if fs or _floors(ev) != [("clause", "c.1", "behaviour-neutral", PATCH)]:
        failures.append("clause behaviour-neutral: expected PATCH")
    # same-id text change, strengthened row: MINOR.
    ev, fs = clause_text_leg(_inv(("c.1", "old")), _inv(("c.1", "new")), _reg(),
                             _rows(("strengthened", "c.1", "1.1.0")), "1.1.0")
    if fs or _floors(ev) != [("clause", "c.1", "strengthened", MINOR)]:
        failures.append("clause strengthened: expected MINOR")
    # same-id text change, default-correction row (evidence complete): MINOR.
    ev, fs = clause_text_leg(_inv(("c.1", "old")), _inv(("c.1", "new")), _reg(),
                             _rows(("default-correction", "c.1", "1.1.0")), "1.1.0")
    if fs or _floors(ev) != [("clause", "c.1", "default-correction", MINOR)]:
        failures.append("clause default-correction: expected MINOR")
    # same-id text change, NO disposition: undispositioned MAJOR floor (6.4 fail-closed).
    ev, fs = clause_text_leg(_inv(("c.1", "old")), _inv(("c.1", "new")), _reg(), _rows(), "1.1.0")
    if fs or _floors(ev) != [("clause", "c.1", "undispositioned-text-change", MAJOR)]:
        failures.append("clause undispositioned: expected MAJOR floor")

    # --- PATH LAYOUT leg ---------------------------------------------------------------------------
    def _man(paths):
        return {"sources": [{"path": p, "sha256": s} for p, s in paths]}
    # added path: MINOR.
    ev, _fs = path_keyset_leg(_man([("a", "h1")]), _man([("a", "h1"), ("b", "h2")]))
    if ("path", "b", "added", MINOR) not in _floors(ev):
        failures.append("path added: expected a MINOR added event")
    # removed path: MAJOR.
    ev, _fs = path_keyset_leg(_man([("a", "h1"), ("b", "h2")]), _man([("a", "h1")]))
    if ("path", "b", "removed", MAJOR) not in _floors(ev):
        failures.append("path removed: expected a MAJOR removed event")
    # moved path (unique digest pairing): a single MAJOR moved event, not remove+add.
    ev, _fs = path_keyset_leg(_man([("a", "h1"), ("b", "h2")]), _man([("a", "h1"), ("c", "h2")]))
    changes = [e.change for e in ev]
    if changes != ["moved"] or ev[0].floor != MAJOR:
        failures.append("path moved: expected a lone MAJOR moved event")

    # --- OWNERSHIP leg -----------------------------------------------------------------------------
    # weakening with a class-change row: MAJOR event, no finding. The path is under .aiqt/release/**
    # (minimum 'derived'), so both pack-immutable and derived clear the absolute minimum and the move
    # itself (pack-immutable -> derived) is the sole event under test.
    wpath = ".aiqt/release/note.txt"
    ev, fs = ownership_leg({wpath: "pack-immutable"}, {wpath: "derived"},
                           _rows(("class-change", wpath, "1.1.0")), "1.1.0")
    if fs or ("ownership", wpath, "weakened", MAJOR) not in _floors(ev):
        failures.append("ownership weakening-with-row: expected a MAJOR event, no finding")
    # weakening with NO row: a finding.
    ev, fs = ownership_leg({wpath: "pack-immutable"}, {wpath: "derived"}, _rows(), "1.1.0")
    if not fs:
        failures.append("ownership weakening rowless: expected a finding")
    # strengthening with a row (old/new classes bound to the observed move): MINOR event.
    ev, fs = ownership_leg({"x/y": "adopter-state"}, {"x/y": "pack-immutable"},
                           _rows(("class-change", "x/y", "1.1.0", "adopter-state", "pack-immutable")),
                           "1.1.0")
    if fs or ("ownership", "x/y", "strengthened", MINOR) not in _floors(ev):
        failures.append("ownership strengthening-with-row: expected a MINOR event, no finding")
    # a class-change row whose declared old/new classes do NOT match the observed move: a binding finding
    # (finding 3), even though a row is present.
    ev, fs = ownership_leg({"x/y": "adopter-state"}, {"x/y": "pack-immutable"},
                           _rows(("class-change", "x/y", "1.1.0", "derived", "pack-immutable")), "1.1.0")
    if not any("must be bound to the observed change" in f for f in fs):
        failures.append("ownership class-change binding: a row declaring the wrong old/new class expected "
                        "a binding finding")
    # a class-change row missing old-class/new-class is malformed for its kind (exit 2 upstream).
    try:
        normalize_dispositions([{"id": "x/y", "kind": "class-change", "release": "1.1.0",
                                 "impact": "x", "rationale": "r"}])
        failures.append("normalize_dispositions: expected GateError on a class-change without old/new class")
    except GateError:
        pass
    # below the absolute minimum: an unconditional finding (a pack path assigned adopter-state).
    _ev, fs = ownership_leg({}, {"tools/x.py": "adopter-state"}, _rows(), "1.1.0")
    if not any("absolute minimum" in f for f in fs):
        failures.append("ownership below-minimum: expected an absolute-minimum finding")
    # the manifest-self path at the wrong (exact) class is below-minimum too.
    _ev, fs = ownership_leg({}, {MANIFEST_REL: "pack-immutable"}, _rows(), "1.1.0")
    if not any("absolute minimum" in f for f in fs):
        failures.append("ownership manifest-self exact-class: expected an absolute-minimum finding")

    # --- ORDER leg ---------------------------------------------------------------------------------
    if order_leg(b"a", b"a") != ([], []):
        failures.append("order unchanged: expected no event")
    ev, _fs = order_leg(b"a", b"b")
    if _floors(ev) != [("order", ORDER_REL, "changed", MAJOR)]:
        failures.append("order changed: expected a MAJOR event")

    # --- RENDERER diff (pure) ----------------------------------------------------------------------
    def _decl(rid, rev):
        return {"renderer": [{"renderer-id": rid, "semantics-revision": rev}]}
    # a diff with an alters-obligations row: MAJOR.
    ev, fs = renderer_diff(_decl("agents", 1), _decl("agents", 2),
                           _rows(("renderer-semantics", "agents", "1.1.0", "alters-obligations")), "1.1.0")
    if fs or ("renderer", "agents", "changed", MAJOR) not in _floors(ev):
        failures.append("renderer alters-obligations: expected MAJOR")
    # a diff with a byte-only row: MINOR.
    ev, fs = renderer_diff(_decl("agents", 1), _decl("agents", 2),
                           _rows(("renderer-semantics", "agents", "1.1.0", "byte-only")), "1.1.0")
    if fs or ("renderer", "agents", "changed", MINOR) not in _floors(ev):
        failures.append("renderer byte-only: expected MINOR")
    # a rowless renderer diff: a finding plus a conservative MAJOR event.
    ev, fs = renderer_diff(_decl("agents", 1), _decl("agents", 2), _rows(), "1.1.0")
    if not fs or ("renderer", "agents", "changed", MAJOR) not in _floors(ev):
        failures.append("renderer rowless diff: expected a finding and a MAJOR event")

    # --- take_row / disposition schema (findings 2/3) --------------------------------------------
    # take_row keys on the row's `id` (the target, finding 2): a behaviour-neutral row id="c.1" is consumed
    # for the c.1 change.
    consume_rows = _rows(("behaviour-neutral", "c.1", "1.1.0"))
    if take_row(consume_rows, "behaviour-neutral", "c.1", "1.1.0") is None:
        failures.append("take_row: an id-keyed row must be consumed for its target change (finding 2)")
    # take_row fails closed (GateError) on two un-normalized rows for one (kind, id, release). A VALID record
    # can never reach this (normalize_dispositions rejects a duplicate id), so the case is built raw.
    raw_dup = [{"id": "c.1", "kind": "behaviour-neutral", "release": "1.1.0", "_consumed": False},
               {"id": "c.1", "kind": "behaviour-neutral", "release": "1.1.0", "_consumed": False}]
    try:
        take_row(raw_dup, "behaviour-neutral", "c.1", "1.1.0")
        failures.append("take_row: expected GateError on two rows for one change")
    except GateError:
        pass
    # THE FINDING-2 REGRESSION GUARD: a Step-2-valid row (id/release/kind/impact/rationale) whose `id` names
    # a target that is NOT the detected change must LOAD (not fail closed exit 2) and be reported UNCONSUMED.
    step2_row = {"id": "d1", "release": "1.1.0", "kind": "behaviour-neutral", "impact": "x",
                 "rationale": "r"}
    try:
        loaded = normalize_dispositions([step2_row])
    except GateError as exc:
        loaded = None
        failures.append("normalize_dispositions: a Step-2-valid row must load, not raise ({})".format(exc))
    if loaded is not None and take_row(loaded, "behaviour-neutral", "c.1", "1.1.0") is not None:
        failures.append("take_row: a row whose id names another target must not match this change")
    # A default-correction row with JUNK evidence (finding 3): captured-source not a URL, an invalid date
    # -> GateError (exit 2), never a licensed MINOR.
    try:
        normalize_dispositions([{"id": "c.1", "kind": "default-correction", "release": "1.1.0",
                                 "impact": "x", "rationale": "r", "captured-source": "s",
                                 "capture-date": "notadate", "observed-measurement": "s",
                                 "observed-date": "x", "prefix-superset-reference": "s"}])
        failures.append("normalize_dispositions: expected GateError on junk default-correction evidence")
    except GateError:
        pass
    # the shared kind vocabulary is the SAME object Step 2 uses (single source, cannot diverge).
    import check_manifest as _cm
    if DISPOSITION_KINDS is not _cm.DISPOSITION_KINDS:
        failures.append("DISPOSITION_KINDS drift: the delta gate and check_manifest must share one set")
    # the delta common schema must be exactly Step 2's mandatory set (matching check_manifest.check_dispositions).
    if set(DISPOSITION_COMMON_FIELDS) != {"id", "release", "kind", "impact", "rationale"}:
        failures.append("DISPOSITION_COMMON_FIELDS drift from the Step-2 record schema")
    # duplicate id across rows is rejected (Step 2's real key).
    try:
        normalize_dispositions([dict(step2_row), dict(step2_row)])
        failures.append("normalize_dispositions: expected GateError on a duplicate disposition id")
    except GateError:
        pass
    # a missing common field (no impact) is rejected, matching Step 2.
    try:
        normalize_dispositions([{"id": "d", "release": "1.1.0", "kind": "behaviour-neutral",
                                 "rationale": "r"}])
        failures.append("normalize_dispositions: expected GateError on a missing common field")
    except GateError:
        pass
    # a default-correction row missing an evidence field is malformed input (exit 2 upstream).
    try:
        normalize_dispositions([{"id": "d", "kind": "default-correction", "subject": "c.1",
                                 "release": "1.1.0", "impact": "x", "rationale": "r"}])
        failures.append("normalize_dispositions: expected GateError on missing evidence field")
    except GateError:
        pass
    # a renderer-semantics row with a bad impact is malformed input.
    try:
        normalize_dispositions([{"id": "d", "kind": "renderer-semantics", "subject": "agents",
                                 "release": "1.1.0", "impact": "nonsense", "rationale": "r"}])
        failures.append("normalize_dispositions: expected GateError on a bad renderer impact")
    except GateError:
        pass

    # --- repin / self-test mode resolution (VC-4 QA #9) ------------------------------------------
    def _opts(self_test=False, repin=False, target=None, stage2_repo=None, stage2_tree=None):
        return {"self_test": self_test, "repin": repin, "target": target,
                "stage2_repo": stage2_repo, "stage2_tree": stage2_tree}
    _cases = [(_opts(), "run"),
              (_opts(self_test=True), "self-test"),
              (_opts(repin=True, target="1.0.0"), "repin"),
              (_opts(target="1.0.0"), "error"),               # --target without --repin never runs
              (_opts(repin=True), "error"),                   # --repin without --target is incomplete
              (_opts(repin=True, target="1.0.0", self_test=True), "error"),   # mixed with --self-test
              (_opts(target="1.0.0", self_test=True), "error"),
              # stage-2 re-execution mode (QA round 5): BOTH internal args resolve to 'stage2'; either
              # alone, or mixed with another mode, is 'error'.
              (_opts(stage2_repo="/r", stage2_tree="/t"), "stage2"),
              (_opts(stage2_repo="/r"), "error"),
              (_opts(stage2_tree="/t"), "error"),
              (_opts(stage2_repo="/r", stage2_tree="/t", self_test=True), "error"),
              (_opts(stage2_repo="/r", stage2_tree="/t", repin=True, target="1.0.0"), "error")]
    for opts, want in _cases:
        got = _resolve_mode(opts)
        if got != want:
            failures.append("_resolve_mode({}): expected {!r}, got {!r}".format(opts, want, got))

    # --- duplicate-CLI rejection through main() (round-7 finding 7) --------------------------------
    def _main_rc(argv):
        saved = sys.argv
        sys.argv = ["check_release_delta.py"] + argv
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                return main()
        finally:
            sys.argv = saved
    for argv in (["--self-test", "--self-test"], ["--repin", "--repin"],
                 ["--target", "1.0.0", "--target", "1.0.1"]):
        if _main_rc(argv) != 2:
            failures.append("main({}): a duplicate CLI option must be rejected exit 2 (round-7 finding "
                            "7)".format(argv))

    # --- single-named-ref-read predecessor tag anchor (this round's #1) ---------------------------
    # The named ref refs/tags/<tag> is read EXACTLY ONCE (resolved to an OID); its recorded OID is then
    # inspected for TYPE and PEELED BY ID, never the named ref again, so a tag moving between reads cannot
    # present a split view. Each scenario counts the named-ref reads and asserts exactly one; a revert that
    # reads the ref a second time (for the type or the peel) trips the count, and each move direction is
    # rejected. _rev_parse and _object_type are stubbed deterministically.
    _A, _B, _C, _X = "a" * 40, "b" * 40, "c" * 40, "d" * 40
    _named_ref_reads = [0]

    def _make_rev_parse(resolver):
        def rp(_root, ref):
            if ref.startswith("refs/tags/"):
                _named_ref_reads[0] += 1
            return resolver(ref)
        return rp

    _saved_rp, _saved_ot = _rev_parse, _object_type

    def _run_anchor(resolver, obj_type):
        _named_ref_reads[0] = 0
        globals()["_rev_parse"] = _make_rev_parse(resolver)

        def _ot(_r, oid):
            if oid.startswith("refs/"):   # a revert that types the NAMED ref (not the recorded OID) is counted
                _named_ref_reads[0] += 1
            return obj_type
        globals()["_object_type"] = _ot
        try:
            _anchor_predecessor(".", {"commit_sha": _C, "tag": "v9.9.9", "tag_object_sha": _A})
            return None
        except GateError:
            return "rejected"
        finally:
            globals()["_rev_parse"] = _saved_rp
            globals()["_object_type"] = _saved_ot

    # direction 1: the tag MOVED AWAY so the named ref resolves to B, not the recorded A -> rejected at the
    # resolved-vs-recorded comparison.
    def _resolver_moved(ref):
        return _C if ref == _C + "^{commit}" else (_B if ref == "refs/tags/v9.9.9" else "0" * 40)
    if _run_anchor(_resolver_moved, "tag") != "rejected":
        failures.append("_anchor_predecessor: a tag that resolves to a DIFFERENT oid than the recorded "
                        "tag_object_sha must be rejected (this round's #1)")
    if _named_ref_reads[0] != 1:
        failures.append("_anchor_predecessor read the named ref {} times (moved-away case); exactly ONE is "
                        "allowed (this round's #1)".format(_named_ref_reads[0]))
    # direction 2 (the inverse race): the ref resolves to the recorded A and A even peels to the recorded
    # commit, but the RECORDED object A is NOT an annotated tag (a lightweight tag / commit substituted).
    # Inspecting the recorded OID's type (not the ref's) rejects it; a two-read impl that typed the ref
    # after a move could be fooled.
    def _resolver_typematch(ref):
        if ref == _C + "^{commit}":
            return _C
        if ref == "refs/tags/v9.9.9":
            return _A
        if ref == _A + "^{commit}":
            return _C
        return "0" * 40
    if _run_anchor(_resolver_typematch, "commit") != "rejected":
        failures.append("_anchor_predecessor: a recorded tag_object that is not an annotated tag must be "
                        "rejected by inspecting the RECORDED oid's type, not the ref's (this round's #1)")
    if _named_ref_reads[0] != 1:
        failures.append("_anchor_predecessor read the named ref {} times (type-mismatch case); exactly ONE "
                        "is allowed (this round's #1)".format(_named_ref_reads[0]))
    # the recorded object is an annotated tag AND matches, but peels ELSEWHERE than the recorded commit
    # (the round-8 split): peeling the recorded OID rejects it.
    def _resolver_peel_elsewhere(ref):
        if ref == _C + "^{commit}":
            return _C
        if ref == "refs/tags/v9.9.9":
            return _A
        if ref == _A + "^{commit}":
            return _X
        return "0" * 40
    if _run_anchor(_resolver_peel_elsewhere, "tag") != "rejected":
        failures.append("_anchor_predecessor: a recorded tag object that peels elsewhere than the recorded "
                        "commit must be rejected (this round's #1)")
    if _named_ref_reads[0] != 1:
        failures.append("_anchor_predecessor read the named ref {} times (peel case); exactly ONE is "
                        "allowed (this round's #1)".format(_named_ref_reads[0]))

    # --- this round's #2 / #3 / #6 unit checks ----------------------------------------------------
    import gen_manifest as _gm
    # #2: is_canonical_relpath and gen_manifest._check_rel_path reject the FULL control range (0x00-0x1F and
    # DEL 0x7F), not only NUL/tab/LF/CR; a clean path still passes both.
    for _bad in ("qa/\x01record.toml", "qa/\x1frecord.toml", "qa/\x7frecord.toml"):
        if _release_schema.is_canonical_relpath(_bad):
            failures.append("is_canonical_relpath accepted a control character in {!r} (this round's "
                            "#2)".format(_bad))
        try:
            _gm._check_rel_path(_bad, "unit")
            failures.append("gen_manifest._check_rel_path accepted a control character in {!r} (this "
                            "round's #2)".format(_bad))
        except _gm.GateError:
            pass
    if not _release_schema.is_canonical_relpath("qa/record.toml"):
        failures.append("is_canonical_relpath wrongly rejected a clean path (this round's #2)")
    # #3: strict_renderers rejects a non-slug renderer-id ("Bad ID") on ANY object (head/predecessor share
    # this validator). A hand-crafted fresh renderers.toml cannot reach run() once gen_renderers rejects the
    # source RENDERER_DECL (gen_renderers self-test covers that), so this defence-in-depth layer is asserted
    # directly on the shared strict validator.
    def _rdecl(rid):
        return {"format-version": 1, "renderer": [{
            "renderer-id": rid, "entrypoint": "tools/x.py", "semantics-revision": 1,
            "targets": ["OUT.md"], "closure": ["tools/x.py"], "code-digest": "a" * 64}]}
    try:
        _release_schema.strict_renderers(_rdecl("Bad ID"), "unit renderers")
        failures.append("strict_renderers accepted a non-slug renderer-id 'Bad ID' (this round's #3)")
    except _release_schema.SchemaError:
        pass
    try:
        _release_schema.strict_renderers(_rdecl("good-id"), "unit renderers")
    except _release_schema.SchemaError as exc:
        failures.append("strict_renderers wrongly rejected a valid slug renderer-id ({}, this round's "
                        "#3)".format(exc))
    # #6: a NON-GENESIS renderer-semantics disposition may target a renderer declared ONLY in the
    # predecessor (a removal): the union scope accepts it while HEAD-only would reject it.
    _prev_dec = {"renderer": [{"renderer-id": "gonernd"}, {"renderer-id": "keeprnd"}]}
    _head_dec = {"renderer": [{"renderer-id": "keeprnd"}]}
    _scope = _nongenesis_renderer_target_scope(_prev_dec, _head_dec)
    if "gonernd" not in _scope:
        failures.append("_nongenesis_renderer_target_scope omits a PREDECESSOR-only renderer (this round's "
                        "#6)")
    _rmrow = _rows(("renderer-semantics", "gonernd", "1.1.0", "byte-only"))
    try:
        _assert_renderer_targets_declared(_rmrow, _scope)
    except GateError:
        failures.append("a disposition for a predecessor-only REMOVED renderer must be accepted under the "
                        "union (this round's #6)")
    try:
        _assert_renderer_targets_declared(_rmrow, _renderer_ids(_head_dec))
        failures.append("HEAD-only renderer scope wrongly ACCEPTED a predecessor-only renderer (proves the "
                        "union is required, this round's #6)")
    except GateError:
        pass

    # --- round-5 SWEEP: type-appropriate value validation of EVERY record field ------------------
    # These assert the shared strict validators directly (the discriminating layer for a field whose only
    # escape is at the record-schema level), complementing the run() fixtures below. The class the sweep
    # closes: a record FIELD whose VALUE was checked only non-empty, never for its type-appropriate grammar.

    # #1 source-path is a CANONICAL repo-relative path on head AND predecessor (strict_clause_inventory is
    # the ONE validator both objects run). A '..' path that RESOLVES to the real file (the finding's escape)
    # is rejected here even though check_clauses would resolve and accept it.
    def _clause_inv(source_path):
        return {"clause": [{"clause-id": "calpha.1", "corpus-id": "calpha", "source-path": source_path,
                            "start-line": 1, "end-line": 2, "canonical-text": "x",
                            "source-digest": "a" * 64}]}
    for _bad_sp in (".aiqt/core/rules/../rules/x.md", "../escape.md", "a//b.md", "a/\x01b.md"):
        try:
            _release_schema.strict_clause_inventory(_clause_inv(_bad_sp), "unit clauses")
            failures.append("strict_clause_inventory accepted a non-canonical source-path {!r} (round-5 "
                            "finding 1)".format(_bad_sp))
        except _release_schema.SchemaError:
            pass
    try:
        _release_schema.strict_clause_inventory(_clause_inv("aiqt/core/rules/x.md"), "unit clauses")
    except _release_schema.SchemaError as exc:
        failures.append("strict_clause_inventory wrongly rejected a clean source-path ({})".format(exc))

    # #3 captured-source is a DETERMINISTIC http(s) URL: a raw space in the host (the finding's escape), a
    # backslash, a non-http scheme, an empty host, and an out-of-range port are each rejected; real
    # documentation URLs (domain, IPv4, explicit port) are accepted.
    for _bad_url in ("https://exa mple.com/source", "https://ex\\ample.com/s", "ftp://example.com/s",
                     "https:///no-host", "https://example.com:99999/p", "notaurl", ""):
        if _release_schema._is_well_formed_url(_bad_url):
            failures.append("_is_well_formed_url accepted a malformed URL {!r} (round-5 finding 3)".format(
                _bad_url))
    for _ok_url in ("https://docs.example.invalid/x", "http://example.com", "https://example.com:8443/p",
                    "https://192.0.2.7/p"):
        if not _release_schema._is_well_formed_url(_ok_url):
            failures.append("_is_well_formed_url wrongly rejected a valid URL {!r} (round-5 finding "
                            "3)".format(_ok_url))

    # 6.6 evidence sweep: prefix-superset-reference is a canonical repo-relative path and observed-measurement
    # carries a digit AND no control character; the otherwise-valid row (good URL, dates, measurement, ref)
    # passes. default_correction_evidence_findings raises on each malformation.
    def _dc_row(over):
        row = {"captured-source": "https://docs.example.invalid/x", "capture-date": "2026-08-24",
               "observed-measurement": "61117 bytes", "observed-date": "2026-08-24",
               "prefix-superset-reference": "qa/prefix.toml"}
        row.update(over)
        return row
    try:
        _release_schema.default_correction_evidence_findings(_dc_row({}), "unit dc")
    except _release_schema.SchemaError as exc:
        failures.append("default_correction_evidence_findings wrongly rejected a valid row ({})".format(exc))
    for _label, _over in (("prefix-superset-reference '../escape'",
                           {"prefix-superset-reference": "../escape"}),
                          ("prefix-superset-reference '//x'", {"prefix-superset-reference": "qa//x"}),
                          ("observed-measurement with a control char",
                           {"observed-measurement": "611\x0117 bytes"})):
        try:
            _release_schema.default_correction_evidence_findings(_dc_row(_over), "unit dc")
            failures.append("default_correction_evidence_findings accepted {} (round-5 sweep)".format(_label))
        except _release_schema.SchemaError:
            pass

    # order rank is a NON-NEGATIVE integer: a negative rank is rejected, a zero/positive rank accepted.
    def _order(rank):
        return {"format-version": 1, "apex-corpus-id": "prjint1",
                "precedence-tier": [{"rank": rank, "members": ["ACCUR"], "members-are-equal": True}],
                "presentation-order": {"families": ["apex"], "aiqt-facets": ["ACCUR"],
                                       "security-facets": ["SECC"], "tie-breaker": "slug-bytewise"}}
    try:
        _release_schema.strict_order(_order(-1), "unit order")
        failures.append("strict_order accepted a NEGATIVE precedence rank (round-5 sweep)")
    except _release_schema.SchemaError:
        pass
    for _ok_rank in (0, 10):
        try:
            _release_schema.strict_order(_order(_ok_rank), "unit order")
        except _release_schema.SchemaError as exc:
            failures.append("strict_order wrongly rejected rank {} ({})".format(_ok_rank, exc))

    # disposition common fields carry no control character: a 0x01 byte in rationale (or any common field)
    # is a malformed record, exit 2; the same row without it loads.
    _cc_row = {"id": "c.1", "kind": "behaviour-neutral", "release": "1.1.0", "impact": "x",
               "rationale": "bad\x01rationale"}
    try:
        normalize_dispositions([dict(_cc_row)])
        failures.append("normalize_dispositions accepted a control character in a common field (round-5 "
                        "sweep)")
    except GateError:
        pass
    try:
        normalize_dispositions([dict(_cc_row, rationale="clean rationale")])
    except GateError as exc:
        failures.append("normalize_dispositions wrongly rejected a clean disposition row ({})".format(exc))

    # --- round-6 direct validator regressions -----------------------------------------------------
    # (finding 2) a malformed percent escape ('%zz', a truncated '%a', a trailing '%') is rejected; a valid
    # escape ('%2F') is accepted. urlparse alone accepts all of these, so this is the discriminating layer.
    for _bad_pct in ("https://example.com/%zz", "https://example.com/%a", "https://example.com/100%",
                     "https://example.com/%g0"):
        if _release_schema._is_well_formed_url(_bad_pct):
            failures.append("_is_well_formed_url accepted a malformed percent escape {!r} (round-6 finding "
                            "2)".format(_bad_pct))
    for _ok_pct in ("https://example.com/%2Fpath", "https://example.com/a%20b", "https://example.com/p"):
        if not _release_schema._is_well_formed_url(_ok_pct):
            failures.append("_is_well_formed_url wrongly rejected a valid percent escape {!r} (round-6 "
                            "finding 2)".format(_ok_pct))

    # (finding 3) strict_releases enforces UNIQUE + STRICTLY-INCREASING versions across the array: two
    # identical rows and an out-of-order pair each raise, a strictly-increasing pair is accepted.
    def _rel_row(v):
        return {"version": v, "tag": "v" + v, "tag_object_sha": "a" * 40, "commit_sha": "b" * 40,
                "qa-sha256": "c" * 64, "qa-store-path": "qa/{}.toml".format(v),
                "attestation-timestamps": [1]}
    for _label, _vers in (("two identical rows", ["1.0.0", "1.0.0"]),
                          ("out-of-order rows", ["1.0.1", "1.0.0"])):
        try:
            _release_schema.strict_releases(
                {"format-version": 1, "release": [_rel_row(v) for v in _vers]}, "unit releases")
            failures.append("strict_releases accepted {} (round-6 finding 3 array uniqueness/"
                            "monotonicity)".format(_label))
        except _release_schema.SchemaError:
            pass
    try:
        _release_schema.strict_releases(
            {"format-version": 1, "release": [_rel_row("1.0.0"), _rel_row("1.1.0")]}, "unit releases")
    except _release_schema.SchemaError as exc:
        failures.append("strict_releases wrongly rejected a strictly-increasing releases array ({})".format(
            exc))

    # (array-invariant audit) strict_id_history rejects a duplicate id WITHIN a section (tombstone here,
    # symmetric with the pre-existing born dedup); an id in born AND a retirement section is still valid.
    _dup_tomb = {"tombstone": [{"id": "prjint1.1", "retired-release": "1.1.0"},
                               {"id": "prjint1.1", "retired-release": "1.2.0"}]}
    try:
        _release_schema.strict_id_history(_dup_tomb, "unit id-history")
        failures.append("strict_id_history accepted a duplicate tombstone id (round-6 array-invariant audit)")
    except _release_schema.SchemaError:
        pass
    try:
        _release_schema.strict_id_history(
            {"born": [{"id": "prjint1.1", "born-release": "1.0.0"}],
             "tombstone": [{"id": "prjint1.1", "retired-release": "1.1.0"}]}, "unit id-history")
    except _release_schema.SchemaError as exc:
        failures.append("strict_id_history wrongly rejected an id in born AND tombstone ({})".format(exc))

    # --- _cat_file_batch grammar (round-7 finding 3) ----------------------------------------------
    class _FakeProc:
        def __init__(self, stdout):
            self.returncode, self.stdout, self.stderr = 0, stdout, b""
    _saved_run = subprocess.run
    oid = "a" * 40
    # a malformed (non-decimal) size, a declared-10-but-2-byte short body, and (round-8 finding 7) a VALID
    # blob response followed by trailing protocol garbage, each raise SchemaError.
    for bad in (oid.encode() + b" blob NaN\nxx\n", oid.encode() + b" blob 10\nxx\n",
                oid.encode() + b" blob 2\nhi\nUNEXPECTED-TRAILER"):
        subprocess.run = lambda *a, **k: _FakeProc(bad)
        try:
            _release_schema._cat_file_batch(".", [oid])
            failures.append("_cat_file_batch: malformed batch output must raise SchemaError (finding 3)")
        except _release_schema.SchemaError:
            pass
        finally:
            subprocess.run = _saved_run

    # --- materialize_tree_raw preserves git file modes (round-7 finding 4) ------------------------
    import stat as _stat
    if _git_available():
        import shutil as _sh4
        import tempfile as _tf4
        mtmp = Path(_tf4.mkdtemp(prefix="aiqt-delta-mode-"))
        try:
            mrepo = mtmp / "r"
            mrepo.mkdir()
            (mrepo / "exec.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
            (mrepo / "plain.txt").write_text("x\n", encoding="utf-8")
            os.chmod(mrepo / "exec.sh", 0o755)
            for a in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "m", "--no-verify"]):
                subprocess.run(["git", "-C", str(mrepo), *a], capture_output=True, env=_selftest_env())
            mc = subprocess.run(["git", "-C", str(mrepo), "rev-parse", "HEAD"],
                                capture_output=True, text=True).stdout.strip()
            mdest = mtmp / "dest"
            mdest.mkdir()
            _release_schema.materialize_tree_raw(mrepo, mc, mdest)
            if not (os.stat(mdest / "exec.sh").st_mode & _stat.S_IXUSR):
                failures.append("materialize_tree_raw: a 100755 blob must materialize executable (finding 4)")
            if os.stat(mdest / "plain.txt").st_mode & _stat.S_IXUSR:
                failures.append("materialize_tree_raw: a 100644 blob must materialize non-executable "
                                "(finding 4)")
        finally:
            _sh4.rmtree(mtmp, ignore_errors=True)

    # --- bump computation --------------------------------------------------------------------------
    if _claimed_rank("1.0.0", "1.0.1") != PATCH or _claimed_rank("1.0.0", "1.1.0") != MINOR \
            or _claimed_rank("1.0.0", "2.0.0") != MAJOR:
        failures.append("_claimed_rank: PATCH/MINOR/MAJOR mapping is wrong")
    # Every non-increase stays a GateError at _claimed_rank: a decrease, an EQUAL version (run() routes an
    # equal head to _post_release_head first, which accepts only the post-release paths), and a malformed
    # predecessor or head version.
    for _pv, _hv in (("1.1.0", "1.0.0"), ("1.0.0", "1.0.0"), ("x", "1.0.0"), ("1.0.0", "x")):
        try:
            _claimed_rank(_pv, _hv)
            failures.append("_claimed_rank: expected GateError when head {} does not increase over {}".format(
                _hv, _pv))
        except GateError:
            pass
    # (round-4 codex finding 1) _parse GUARDS its int() conversion: a component within the SemVer grammar can
    # still exceed CPython's integer-string-conversion digit limit (default 4300) and raise ValueError. An
    # oversized component is malformed input, so _parse returns None (a cannot-evaluate every gate handles as
    # fail-closed), never a propagating ValueError that would escape run()'s boundary as a traceback exit 1.
    if _parse("1" * 5000 + ".0.0") is not None:
        failures.append("_parse: an oversized major component (5000 digits) must return None, not raise")

    # --- combined multi-leg delta: dispositions consumed ACROSS legs, floor = max, no residue --------
    # A single release that both strengthens a clause and strengthens an ownership class: each leg consumes
    # its own row from ONE shared roster, both rows are consumed, and the floor is MINOR.
    shared = _rows(("strengthened", "c.1", "1.1.0"),
                   ("class-change", ".aiqt/release/n.txt", "1.1.0", "derived", "pack-immutable"))
    e_c, f_c = clause_text_leg(_inv(("c.1", "old")), _inv(("c.1", "new")), _reg(), shared, "1.1.0")
    e_o, f_o = ownership_leg({".aiqt/release/n.txt": "derived"},
                             {".aiqt/release/n.txt": "pack-immutable"}, shared, "1.1.0")
    combined_findings = f_c + f_o + [r["id"] for r in shared if not r["_consumed"]]
    if combined_findings:
        failures.append("combined multi-leg: both rows should consume with no finding ({})".format(
            combined_findings))
    if max((e.floor for e in e_c + e_o), default=PATCH) != MINOR:
        failures.append("combined multi-leg: floor should be MINOR")

    # --- git-independent end-to-end cases (minimal record trees in a tempdir) ----------------------
    import shutil
    import tempfile
    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-release-delta-selftest-"))
    except OSError:
        tmp = None

    if tmp is None:
        print("SELF-TEST NOTE: no writable temp directory; the genesis and unreachable-predecessor "
              "end-to-end cases were SKIPPED (the classification-leg coverage above still ran)",
              file=sys.stderr)
        e2e_ran = False
    else:
        e2e_ran = True

        def _valid_manifest(genesis):
            # A STRUCTURALLY valid manifest carrying the EXACT mandatory top-level keyset (round-5 finding
            # 1: strict_manifest now requires sources and artifacts to be PRESENT). Empty arrays are
            # structurally valid here; the full set-equality against the tracked tree is check_manifest's.
            return ('format-version = 1\nrelease-version = "1.0.0"\ngenesis = {}\n'
                    'tree-sha256 = "{}"\nsources = []\nartifacts = []\n'.format(genesis, "a" * 64))

        try:
            # (genesis structural, VC-4 QA #1) zero-row releases + a valid manifest genesis=true, but a
            # MINIMAL record tree whose renderer/inventory structure cannot be validated: genesis mode
            # fails CLOSED (exit 2) rather than passing exit 0. A genuine clean genesis (real inventory,
            # register, and renderer closure) is exercised by run_all_checks against the pack.
            g = tmp / "genesis"
            (g / ".aiqt" / "core").mkdir(parents=True)
            (g / ".aiqt" / "manifest.toml").write_text(_valid_manifest("true"), encoding="utf-8")
            for rel in (RELEASES_REL, ORDER_REL, RENDERERS_REL, CLAUSES_REL, DISPOSITIONS_REL):
                (g / rel).write_text("format-version = 1\n", encoding="utf-8")
            if _run_quiet_root(g) != 2:
                failures.append("genesis structural: a record tree whose structure cannot be validated "
                                "must fail closed exit 2 (QA #1), not pass")

            # (genesis-mismatch) zero rows but a valid manifest whose genesis is not true -> exit 2.
            gm = tmp / "genesis-mismatch"
            (gm / ".aiqt" / "core").mkdir(parents=True)
            (gm / ".aiqt" / "manifest.toml").write_text(_valid_manifest("false"), encoding="utf-8")
            for rel in (RELEASES_REL, ORDER_REL, RENDERERS_REL, CLAUSES_REL, DISPOSITIONS_REL):
                (gm / rel).write_text("format-version = 1\n", encoding="utf-8")
            if _run_quiet_root(gm) != 2:
                failures.append("genesis flag mismatch: expected fail-closed exit 2")

            # (unreachable predecessor) a one-row releases record (a COMPLETE attestation row, so the strict
            # loader passes and the failure is genuinely the unresolvable commit, not the schema) whose
            # commit cannot be resolved (the tree is not a git repo) -> exit 2, never a silently
            # conservative verdict.
            u = tmp / "unreachable"
            (u / ".aiqt" / "core").mkdir(parents=True)
            (u / ".aiqt" / "manifest.toml").write_text(_valid_manifest("false"), encoding="utf-8")
            (u / RELEASES_REL).write_text(
                'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
                'tag_object_sha = "{a}"\ncommit_sha = "{a}"\nqa-sha256 = "{h}"\n'
                'qa-store-path = "qa/1.0.0.toml"\nattestation-timestamps = [100]\n'.format(
                    a="a" * 40, h="a" * 64), encoding="utf-8")
            (u / CHANGELOG_REL).write_text(
                '[[release]]\nversion = "1.1.0"\n', encoding="utf-8")
            (u / DISPOSITIONS_REL).write_text("format-version = 1\n", encoding="utf-8")
            if _run_quiet_root(u) != 2:
                failures.append("unreachable predecessor: expected fail-closed exit 2")

            # === REAL run()/CLI MALFORMED-INPUT FIXTURES (finding 9) ===================================
            # Each drives the REAL run(root) on a real record tree (a genesis-base tree, or a real
            # two-commit git repo for the predecessor cases) and asserts the strict LOADER fails closed
            # exit 2. These are the tests that would have caught findings 1/3/4: the loader now actually
            # calls the shared strict validator on head AND predecessor objects, before any delta compute.
            def _genesis_base(dirname, **overrides):
                """A genesis-mode record tree (zero-row releases, manifest genesis=true) whose non-release
                records default to a minimal-but-well-formed shape; `overrides` replaces a record's body so
                one malformed record can be tested in isolation. run() reaches load_release_rows ->
                load_dispositions -> the genesis structural validators in that order."""
                base = tmp / dirname
                (base / ".aiqt" / "core").mkdir(parents=True)
                man = overrides.pop("_manifest", _valid_manifest("true"))
                (base / ".aiqt" / "manifest.toml").write_text(man, encoding="utf-8")
                bodies = {RELEASES_REL: "format-version = 1\n",
                          DISPOSITIONS_REL: "format-version = 1\n",
                          ORDER_REL: 'format-version = 1\napex-corpus-id = "prjint1"\n',
                          RENDERERS_REL: "format-version = 1\n",
                          CLAUSES_REL: "format-version = 1\n"}
                bodies.update(overrides)
                for rel, body in bodies.items():
                    (base / rel).write_text(body, encoding="utf-8")
                return base

            # manifest.toml malformed (round-3 finding 1): a bad format-version, an unknown top-level key,
            # and a duplicate source row each fail closed exit 2 BEFORE the genesis/delta branch.
            if _run_quiet_root(_genesis_base(
                    "m-man-fmtver", _manifest='format-version = 999\nrelease-version = "1.0.0"\n'
                    'genesis = true\ntree-sha256 = "{}"\n'.format("a" * 64))) != 2:
                failures.append("real run(): manifest format-version=999 must fail closed exit 2 (finding 1)")
            if _run_quiet_root(_genesis_base(
                    "m-man-topkey", _manifest=_valid_manifest("true") + "bogustop = 1\n")) != 2:
                failures.append("real run(): manifest unknown top-level key must fail closed exit 2 "
                                "(finding 1)")
            dup_src = (_valid_manifest("true")
                       + '\n[[sources]]\npath = "a.txt"\nbytes = 1\nsha256 = "{h}"\n'
                         '\n[[sources]]\npath = "a.txt"\nbytes = 2\nsha256 = "{h}"\n'.format(h="a" * 64))
            if _run_quiet_root(_genesis_base("m-man-dupsrc", _manifest=dup_src)) != 2:
                failures.append("real run(): manifest duplicate source row must fail closed exit 2 "
                                "(finding 1)")

            # releases.toml format-version = 999 (finding 1/4): exit 2 BEFORE the genesis structural stage.
            if _run_quiet_root(_genesis_base("m-rel-fmtver",
                                             **{RELEASES_REL: "format-version = 999\n"})) != 2:
                failures.append("real run(): releases.toml format-version=999 must fail closed exit 2")
            # releases.toml unknown top-level key (finding 1/4): exit 2.
            if _run_quiet_root(_genesis_base(
                    "m-rel-topkey", **{RELEASES_REL: "format-version = 1\nbogustop = 1\n"})) != 2:
                failures.append("real run(): releases.toml unknown top-level key must fail closed exit 2")
            # dispositions.toml format-version = 999 (finding 1): exit 2.
            if _run_quiet_root(_genesis_base(
                    "m-disp-fmtver", **{DISPOSITIONS_REL: "format-version = 999\n"})) != 2:
                failures.append("real run(): dispositions.toml format-version=999 must fail closed exit 2")
            # dispositions.toml unknown top-level key (finding 1): exit 2.
            if _run_quiet_root(_genesis_base(
                    "m-disp-topkey", **{DISPOSITIONS_REL: "format-version = 1\nbogustop = 1\n"})) != 2:
                failures.append("real run(): dispositions.toml unknown top-level key must fail closed "
                                "exit 2")
            # dispositions.toml junk default-correction evidence (finding 3): exit 2 at load, before any leg.
            if _run_quiet_root(_genesis_base("m-disp-junkdc", **{DISPOSITIONS_REL: (
                    'format-version = 1\n\n[[disposition]]\nid = "c.1"\nrelease = "1.1.0"\n'
                    'kind = "default-correction"\nimpact = "x"\nrationale = "r"\n'
                    'captured-source = "s"\ncapture-date = "notadate"\nobserved-measurement = "s"\n'
                    'observed-date = "x"\nprefix-superset-reference = "s"\n')})) != 2:
                failures.append("real run(): a junk default-correction row must fail closed exit 2 "
                                "(finding 3)")
            # dispositions.toml class-change missing old/new class (finding 3): exit 2 at load.
            if _run_quiet_root(_genesis_base("m-disp-classless", **{DISPOSITIONS_REL: (
                    'format-version = 1\n\n[[disposition]]\nid = ".aiqt/x"\nrelease = "1.1.0"\n'
                    'kind = "class-change"\nimpact = "x"\nrationale = "r"\n')})) != 2:
                failures.append("real run(): a class-change row without old/new class must fail closed "
                                "exit 2 (finding 3)")
            # order.toml format-version = 999 in GENESIS mode (finding 1: genesis AND non-genesis): exit 2.
            if _run_quiet_root(_genesis_base(
                    "m-order-fmtver", **{ORDER_REL: 'format-version = 999\napex-corpus-id = "prjint1"\n'})) \
                    != 2:
                failures.append("real run(): order.toml format-version=999 must fail closed exit 2 even at "
                                "genesis")

            # Predecessor malformed cases need a REAL two-commit git repo (finding 1 on the predecessor
            # object). Commit 1 carries a malformed clause inventory; the working tree (HEAD) carries a
            # one-row releases record pointing at commit 1, so run() resolves the predecessor via git show
            # and the strict inventory validator fails closed exit 2 BEFORE any delta leg runs.
            if _git_available():
                def _predecessor_dupclause(dirname):
                    repo = tmp / dirname
                    (repo / ".aiqt" / "core").mkdir(parents=True)
                    # commit 1: a clauses.toml with a DUPLICATE clause-id (the round-2 predecessor case).
                    (repo / CLAUSES_REL).write_text(
                        '[[clause]]\nclause-id = "cx.1"\ncorpus-id = "cx"\ncanonical-text = "a"\n\n'
                        '[[clause]]\nclause-id = "cx.1"\ncorpus-id = "cx"\ncanonical-text = "b"\n',
                        encoding="utf-8")
                    (repo / ".aiqt" / "manifest.toml").write_text(_valid_manifest("false"),
                                                                  encoding="utf-8")
                    (repo / ORDER_REL).write_text(
                        'format-version = 1\napex-corpus-id = "prjint1"\n', encoding="utf-8")
                    (repo / RENDERERS_REL).write_text("format-version = 1\n", encoding="utf-8")
                    (repo / DISPOSITIONS_REL).write_text("format-version = 1\n", encoding="utf-8")
                    (repo / IDHISTORY_REL).write_text("", encoding="utf-8")
                    _git_init_commit(repo, "release one")
                    commit1 = _git(repo, ["rev-parse", "HEAD"]).stdout.strip()
                    # HEAD (working tree): a one-row COMPLETE releases record pointing at commit 1.
                    (repo / RELEASES_REL).write_text(
                        'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
                        'tag_object_sha = "{c}"\ncommit_sha = "{c}"\nqa-sha256 = "{h}"\n'
                        'qa-store-path = "qa/1.0.0.toml"\nattestation-timestamps = [100]\n'.format(
                            c=commit1, h="a" * 64), encoding="utf-8")
                    (repo / CHANGELOG_REL).write_text(
                        '[[release]]\nversion = "1.0.1"\n', encoding="utf-8")
                    return repo
                if _run_quiet_root(_predecessor_dupclause("m-pred-dupclause")) != 2:
                    failures.append("real run(): a duplicate clause-id in the PREDECESSOR inventory must "
                                    "fail closed exit 2 (finding 1, predecessor object)")

            # (predecessor ownership via git PLUMBING, VC-4 QA #11) build a real two-commit git repo with
            # an ownership map and tracked files; _ownership_classes_at must read the FIRST commit's tree
            # via git show / git ls-tree (never a materialized worktree) and classify it, while HEAD carries
            # an extra file. Env is neutralized (git_tracked strips GIT_*; commits use a fixed identity).
            if _git_available():
                repo = tmp / "own-repo"
                (repo / ".aiqt" / "core").mkdir(parents=True)
                (repo / "pkg").mkdir()
                # selectors are stable across both commits (a directory prefix `<prefix>/**` or an exact
                # path; no bare '**'), so NO-STRAYS holds at each commit's tree.
                (repo / ".aiqt" / "core" / "ownership.toml").write_text(
                    'format-version = 1\n\n[[release-class]]\npattern = "pkg/**"\nclass = "pack-immutable"\n\n'
                    '[[release-class]]\npattern = ".aiqt/**"\nclass = "pack-immutable"\n\n'
                    '[adopter-extent]\nauthority = "adopter-experience-spec"\n', encoding="utf-8")
                (repo / "pkg" / "a.txt").write_text("a\n", encoding="utf-8")
                (repo / "pkg" / "b.txt").write_text("b\n", encoding="utf-8")
                _git_init_commit(repo, "release one")
                prev_commit = _git(repo, ["rev-parse", "HEAD"]).stdout.strip()
                (repo / "pkg" / "c.txt").write_text("c\n", encoding="utf-8")
                _git_init_commit(repo, "release two", init=False)
                try:
                    prev_classes = _ownership_classes_at(repo, prev_commit)
                    head_classes = _ownership_classes_head(repo)
                except GateError as exc:
                    prev_classes, head_classes = None, None
                    failures.append("predecessor ownership via git-show raised ({})".format(exc))
                if prev_classes is not None:
                    want_prev = {"pkg/a.txt": "pack-immutable", "pkg/b.txt": "pack-immutable",
                                 ".aiqt/core/ownership.toml": "pack-immutable"}
                    if prev_classes != want_prev:
                        failures.append("predecessor ownership (git-show): expected {}, got {}".format(
                            want_prev, prev_classes))
                    if "pkg/c.txt" in prev_classes or "pkg/c.txt" not in head_classes:
                        failures.append("predecessor ownership: pkg/c.txt must be in HEAD only, proving the "
                                        "predecessor tree came from the FIRST commit")
                    # no worktree may be left behind (the git-show path materializes none).
                    wt = _git(repo, ["worktree", "list"]).stdout.strip().splitlines()
                    if len(wt) != 1:
                        failures.append("predecessor ownership: a worktree was materialized ({} listed); "
                                        "the git-show path must create none".format(len(wt)))

            # === REAL FULL-PACK TWO-RELEASE run() (finding 9, findings 1 + 2) =========================
            # A COMPLETE pack tree (a `git archive HEAD` of this repo) is committed as the predecessor
            # release; the working tree then carries a real PATCH change (one clause's canonical-text) with
            # its id-keyed behaviour-neutral disposition, a 1.0.1 changelog, and a one-row releases record
            # pointing at the predecessor commit. Every OTHER surface stays valid, so the delta legs
            # (renderer freshness, ownership classify, path/order diffs) all run and pass, and the ONLY thing
            # under test is the loader/consumption fix. This is the test the round-2 pure-leg fixtures could
            # not be: on the pre-fix gate the CLEAN case FAILS exit 1 (finding 2: the id-keyed row was matched
            # by the invented `subject`, left unconsumed, so a PATCH became an undispositioned MAJOR) and the
            # format-version case PASSES exit 0 (finding 1: the loader never validated format-version). Both
            # now behave (0 and 2). Skipped with a printed note where git or archive is unavailable.
            # ---- QA round-6 codex blocker 1 / claude 3: a git REFUSAL is never the checkout
            # path. Simulated dubious-ownership refusals (a real safe.directory refusal needs a
            # foreign-OWNED repository, which an unprivileged self-test cannot create and the
            # funnels' GIT_* scrub makes uninjectable by environment; the end-to-end members of
            # the class, a corrupt HEAD, a bad config and a PATH without git, run as real CLI
            # launches in _post_release_e2e): the funnels are patched to answer rc=128 with git's
            # dubious-ownership refusal, and stage 1 and the routing must each fail closed.
            label6d = "(R6 dubious) a dubious-ownership refusal fails closed, never the fallback"
            _dubious6 = subprocess.CompletedProcess(
                ["git"], 128, b"",
                b"fatal: detected dubious ownership in repository at '/r6'\n")
            _real_s1g = _stage1_git
            globals()["_stage1_git"] = lambda *_a, **_k: _dubious6
            try:
                err6 = io.StringIO()
                with redirect_stderr(err6):
                    rc6 = _stage1_main()
            finally:
                globals()["_stage1_git"] = _real_s1g
            if rc6 != 2 or "no probe positively established" not in err6.getvalue():
                failures.append("{}: stage 1 must exit 2 on a dubious-ownership refusal (got "
                                "{!r}); a git failure is never permission to run checkout "
                                "code".format(label6d, rc6))
            _real_g6 = _git
            globals()["_git"] = lambda *_a, **_k: _dubious6
            try:
                try:
                    got6 = _head_commit_or_none(tmp / "r6-dubious")
                    failures.append("{}: routing must raise GateError on a dubious-ownership "
                                    "refusal, got {!r}".format(label6d, got6))
                except GateError as exc6:
                    if "no probe positively established" not in str(exc6):
                        failures.append("{}: wrong GateError: {}".format(label6d, exc6))
            finally:
                globals()["_git"] = _real_g6

            if _git_available():
                # ---- QA round-6 codex majors 3/4 / claude 2, extended round 7 (claude F1/F2):
                # the drift advisory reads nothing it must not, blocks on nothing, and classifies
                # every special-file swap as drift. The outside copies behind the symlinked parent
                # AND behind the swapped final-component symlink carry the COMMITTED bytes, so a
                # follow-and-read concludes "clean" (round-7 M7); a FIFO swapped in for a NON-EMPTY
                # blob is drift via the descriptor-bound regular-file check, and a FIFO swapped in
                # for an EMPTY blob is drift via that check ALONE (round-7 M9: size 0 hashes to
                # the empty blob); an over-cap sparse file is UNCHECKED, never read. The whole
                # advisory runs in a CHILD with its own timeout so a lost O_NONBLOCK pin (round-7
                # M11) is a NAMED failure, never a hung suite.
                dr6 = tmp / "r6-drift"
                (dr6 / "lnkdir").mkdir(parents=True)
                (dr6 / "lnkdir" / "leaf.txt").write_text("leaf bytes\n", encoding="utf-8")
                (dr6 / "lnkfile.txt").write_text("lnkfile bytes\n", encoding="utf-8")
                (dr6 / "fifo.txt").write_text("fifo bytes\n", encoding="utf-8")
                (dr6 / "emptyfifo.txt").write_text("", encoding="utf-8")
                (dr6 / "big.txt").write_text("big bytes\n", encoding="utf-8")
                _git_init_commit(dr6, "r6 drift fixture")
                outside6 = tmp / "r6-drift-outside"
                outside6.mkdir()
                (outside6 / "leaf.txt").write_text("leaf bytes\n", encoding="utf-8")
                (outside6 / "lnkfile.txt").write_text("lnkfile bytes\n", encoding="utf-8")
                shutil.rmtree(dr6 / "lnkdir")
                os.symlink(outside6, dr6 / "lnkdir")
                os.unlink(dr6 / "lnkfile.txt")
                os.symlink(outside6 / "lnkfile.txt", dr6 / "lnkfile.txt")
                os.unlink(dr6 / "fifo.txt")
                os.mkfifo(dr6 / "fifo.txt")
                os.unlink(dr6 / "emptyfifo.txt")
                os.mkfifo(dr6 / "emptyfifo.txt")
                os.unlink(dr6 / "big.txt")
                with open(dr6 / "big.txt", "wb") as bf6:
                    bf6.truncate(_DRIFT_ADVISORY_SIZE_CAP + 1)
                child7 = (
                    "import importlib.util, io, sys\n"
                    "from contextlib import redirect_stderr\n"
                    "sys.path.insert(0, {tools!r})\n"
                    "spec = importlib.util.spec_from_file_location('crd_r7_drift', {gate!r})\n"
                    "mod = importlib.util.module_from_spec(spec)\n"
                    "spec.loader.exec_module(mod)\n"
                    "buf = io.StringIO()\n"
                    "with redirect_stderr(buf):\n"
                    "    mod._warn_checkout_drift(mod.Path({root!r}))\n"
                    "sys.stdout.write(buf.getvalue())\n").format(
                        tools=str(Path(__file__).resolve().parent),
                        gate=str(Path(__file__).resolve()), root=str(dr6))
                try:
                    drift7 = subprocess.run([sys.executable, "-I", "-B", "-c", child7],
                                            capture_output=True, env=_selftest_env(), timeout=300)
                except subprocess.TimeoutExpired:
                    drift7 = None
                    failures.append("(R7 drift nonblock): the drift advisory BLOCKED for over "
                                    "300s on a FIFO open; the O_NONBLOCK pin on the final open is "
                                    "missing (a NAMED failure, never a hung suite)")
                if drift7 is not None:
                    out6d = drift7.stdout.decode("utf-8", "replace")
                    if drift7.returncode != 0:
                        failures.append("(R6 drift) the advisory child failed rc={}: {}".format(
                            drift7.returncode,
                            drift7.stderr.decode("utf-8", "replace").strip()[-300:]))
                    if "lnkdir/leaf.txt" not in out6d:
                        failures.append("(R6 drift symlink-parent): a symlinked parent directory "
                                        "must be drift WITHOUT following it (the outside copy "
                                        "carries the committed bytes, so a follow-and-read "
                                        "reports clean)")
                    if "lnkfile.txt" not in out6d:
                        failures.append("(R7 drift symlink-final): a tracked FILE swapped for a "
                                        "symlink to an outside copy of the committed bytes must "
                                        "be drift (the final open's O_NOFOLLOW pin)")
                    if "fifo.txt" not in out6d:
                        failures.append("(R6 drift FIFO): a FIFO swapped in for a tracked file "
                                        "must be drift (descriptor-bound regular-file check)")
                    if "emptyfifo.txt" not in out6d:
                        failures.append("(R7 drift empty-blob FIFO): a FIFO swapped in for an "
                                        "EMPTY committed blob must be drift (S_ISREG on the "
                                        "opened descriptor: size 0 hashes to the empty blob)")
                    if "UNCHECKED" not in out6d or "big.txt" not in out6d:
                        failures.append("(R6 drift cap): a tracked file over the advisory size "
                                        "cap must be reported UNCHECKED, never read")
                # QA round-7 claude F1: a `..` component must never walk out of the root. The
                # outside file carries exactly the bytes its blob id names, so the pre-fix walk
                # READ it and answered match; the component refusal answers drift. `.` is pinned
                # via the over-cap file (pre-fix answer unchecked, never drift); empty components
                # are refused the same way.
                import hashlib as _hl7
                outside_b7 = b"outside bytes, committed copy\n"
                (outside6 / "outside.txt").write_bytes(outside_b7)
                want7 = _hl7.sha1(b"blob " + str(len(outside_b7)).encode("ascii") + b"\x00"
                                  + outside_b7).hexdigest()
                if _drift_entry_state(dr6, b"../r6-drift-outside/outside.txt", False,
                                      want7) != "drift":
                    failures.append("(R7 drift dotdot): a `..` path component must be refused as "
                                    "drift; the walk read a file OUTSIDE the root")
                for bad7 in (b"./big.txt", b"big.txt/", b"a//b.txt"):
                    if _drift_entry_state(dr6, bad7, False, want7) != "drift":
                        failures.append("(R7 drift component) {!r}: an empty or `.` path "
                                        "component must be refused as drift".format(bad7))

                # ---- QA round-7 claude F3, asserted directly on the two tracked-set enumerators
                # the checkout-judged branches consume: gen_manifest.git_tracked and
                # _index_materialized_tree each pin core.fsmonitor=false (with the funnels'
                # protocol.allow / GIT_NO_LAZY_FETCH pins), so a repo-config fsmonitor hook never
                # runs through either. The genesis route is also asserted end-to-end in
                # _post_release_e2e (R7 fsmonitor genesis); the delta branch reaches the
                # enumerators through these same two call sites only.
                label7t = "(R7 fsmonitor git_tracked/_index_materialized_tree)"
                fsm7 = tmp / "r7-fsmon-tree"
                fsm7.mkdir()
                (fsm7 / "tracked.txt").write_text("tracked bytes\n", encoding="utf-8")
                _git_init_commit(fsm7, "r7 fsmonitor tree fixture")
                mark7t = tmp / "r7-fsmon-tree-ran.marker"
                hook7t = tmp / "r7-fsmon-tree.sh"
                hook7t.write_text("#!/bin/sh\n: >> " + str(mark7t) + "\nexit 1\n",
                                  encoding="utf-8")
                hook7t.chmod(0o755)
                subprocess.run(["git", "-C", str(fsm7), "config", "core.fsmonitor",
                                str(hook7t)], capture_output=True, env=_selftest_env())
                (fsm7 / "untracked.txt").write_text("untracked\n", encoding="utf-8")
                _index_materialized_tree(fsm7, "r7 fsmonitor")
                got7t = gen_manifest.git_tracked(fsm7)
                if "tracked.txt" not in got7t or "untracked.txt" not in got7t:
                    failures.append("{}: the throwaway index + tracked-set read is wrong "
                                    "(got {})".format(label7t, sorted(got7t)))
                if mark7t.exists():
                    failures.append("{}: the repo-config core.fsmonitor hook RAN under "
                                    "git_tracked / _index_materialized_tree; the "
                                    "core.fsmonitor=false pin is not effective".format(label7t))

                # ---- QA round-6 codex blocker 2: a promisor repository's missing object is a
                # refusal, never a lazy-fetch transport; the marker sshCommand must never run.
                label6p = "(R6 promisor) a missing object never starts a lazy fetch"
                for where6, env6 in (("funnel env", _substitution_free_env()),
                                     ("_release_schema env",
                                      _release_schema._substitution_free_env())):
                    if env6.get("GIT_NO_LAZY_FETCH") != "1":
                        failures.append("{}: {} does not pin GIT_NO_LAZY_FETCH=1".format(
                            label6p, where6))
                senv6 = _selftest_env()
                psrc6 = tmp / "r6-promisor-src"
                psrc6.mkdir()
                (psrc6 / "payload.txt").write_text("promisor payload\n", encoding="utf-8")
                _git_init_commit(psrc6, "promisor source")
                subprocess.run(["git", "-C", str(psrc6), "config", "uploadpack.allowfilter",
                                "true"], capture_output=True, env=senv6)
                pro6 = tmp / "r6-promisor"
                cl6 = subprocess.run(["git", "clone", "-q", "--no-checkout",
                                      "--filter=blob:none", "file://" + str(psrc6), str(pro6)],
                                     capture_output=True, env=senv6)
                if cl6.returncode != 0:
                    failures.append("fixture setup ({}): partial clone failed ({}); coverage "
                                    "that did not run FAILS the self-test".format(
                                        label6p,
                                        cl6.stderr.decode("utf-8", "replace").strip()[:200]))
                else:
                    mark6 = tmp / "r6-ssh-ran.marker"
                    ssh6 = tmp / "r6-ssh.sh"
                    ssh6.write_text("#!/bin/sh\n: > " + str(mark6) + "\nexit 1\n",
                                    encoding="utf-8")
                    ssh6.chmod(0o755)
                    for k6, v6 in (("remote.origin.url", "ssh://r6.invalid/none"),
                                   ("core.sshCommand", str(ssh6))):
                        subprocess.run(["git", "-C", str(pro6), "config", k6, v6],
                                       capture_output=True, env=senv6)
                    h6 = subprocess.run(["git", "-C", str(pro6), "rev-parse", "HEAD"],
                                        capture_output=True, env=senv6
                                        ).stdout.decode("ascii", "replace").strip()
                    try:
                        _release_schema.verified_path_blob(pro6, h6, "payload.txt")
                        failures.append("{}: the missing blob was READ; a promisor fetch must "
                                        "never satisfy a gate read".format(label6p))
                    except SchemaError as exc6p:
                        if "missing" not in str(exc6p):
                            failures.append("{}: expected a missing-object refusal, got: "
                                            "{}".format(label6p, exc6p))
                    if mark6.exists():
                        failures.append("{}: the core.sshCommand marker RAN; a lazy fetch "
                                        "started a transport mid-read".format(label6p))

            if _git_available():
                real_ran = _real_pack_e2e(tmp, failures)
                post_ran = _post_release_e2e(tmp, failures)
            else:
                real_ran = post_ran = False
                failures.append("NOT RUN: the real full-pack and POST-RELEASE run() cases: git is "
                                "unavailable; coverage that did not run FAILS the self-test (QA round-4 "
                                "codex R4-3 / claude m4)")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    core = ("the 6.5 classification legs (clause removed/added/behaviour-neutral/strengthened/"
            "default-correction/undispositioned, path added/removed/moved, ownership weakening/"
            "strengthening/below-minimum, order change, renderer alters-obligations/byte-only/rowless), "
            "the id-lifecycle born/retirement release-and-uniqueness rows and corpus-id diff (#3), the "
            "combined multi-leg consumption, the Step-2 disposition-schema acceptance and id-uniqueness "
            "(#5), the repin/self-test mode resolution (#9), and the PATCH/MINOR/MAJOR bump computation")
    if e2e_ran:
        full_pack = ("; and the REAL FULL-PACK two-release run() clean CONSISTENTLY-edited id-keyed PATCH "
                     "(exit 0, finding 2), its format-version variants (exit 2, finding 1), a wrong-version "
                     "disposition (exit 1, round-3 finding 3), a SMUDGE-FILTER predecessor closure attack "
                     "defeated by raw materialization (exit 2, round-4 findings 1/4), and the GENESIS "
                     "full-pack manifest bogus-artifact/missing-sources (exit 2, round-5 finding 1), a "
                     "self-referential profiles symlink (exit 2, round-5 finding 5), an invalid-UTF-8 "
                     "child capture (round-5 finding 3), the round-6 non-string block-id (finding 2), "
                     "an unknown clauses.toml top-level key (finding 3), and a non-vocabulary class-change "
                     "class (finding 6) each exit 2, and the round-7 predecessor anchoring/version-binding "
                     "(a tree-oid commit_sha and a changelog/manifest version disagreement each exit 2, "
                     "finding 2), a manifest ../escape path and a list-valued artifact kind (finding 1), and "
                     "a renderers.toml integer target (finding 8) each exit 2 hold") \
                    if real_ran else \
                    "; the REAL FULL-PACK two-release run() case was SKIPPED (git archive unavailable)"
        full_pack += ("; and the POST-RELEASE head (RELEASING steps 6a/6b, round-2 rewrite over COMMITTED "
                      "revisions: the attestation row alone and plus the correctly placed changelog tag key "
                      "exit 0; another committed path, a smudge-masked committed edit, a quoted-path edit, a "
                      "mode-only change or a deletion on each post-release path, a non-descendant head "
                      "(bare, and masked by an info/grafts parent rewrite), a stale manifest, an "
                      "extra or altered prior row, a predecessor-manifest version mismatch, a changelog edit "
                      "other than the tag key (bare, and masked by a refs/replace blob substitution, with "
                      "the funneled _show read asserted directly), a corrupt or stale COMMITTED manifest "
                      "masked by a blob replacement, a clean/smudge filter, or skip-worktree, "
                      "a mis-placed tag key (EOF/older table, the TOML-only catch), "
                      "and a head version below the newest row each fail; a staged-only edit and untracked "
                      "files are a stderr ADVISORY over an exit-0 committed verdict, an assume-unchanged "
                      "working-tree overwrite never reaches the verdict, and the three admitted byte-level "
                      "variants (a releases.toml comment, a CRLF tag line, the tag key before the version "
                      "line) exit 0 (QA round-3); the round-4 substitution and provenance cases (a "
                      "GIT_DIR decoy, a forged object-directory overlay with the funneled _show read "
                      "asserted directly, an overwritten loose object refused by the per-object "
                      "re-hash, a committed profiles artifact with the checkout copy deleted, a gate "
                      "import diverging from the committed HEAD copy, and the materialized committed "
                      "tree running its OWN validator copies) each refuse exit 2, and a skipped "
                      "archive/extract/build coverage set now FAILS the self-test; the QA round-5 "
                      "launch cases over a dedicated fixture (stage-1 re-execution of the committed "
                      "gate copy, with an untracked tools/tempfile.py shadow, a crafted __pycache__ "
                      ".pyc, a repo-config core.fsmonitor hook, and a repo-config clean filter and "
                      "post-index-change hook each left inert at exit 2, the "
                      "checkout routing records reverted to the release still routed POST-RELEASE "
                      "from committed objects, an inherited PYTHONPATH/sitecustomize ignored by "
                      "every -I child, and the accepted state re-executing to exit 0) and the "
                      "per-object re-hash refusing a forged commit, tree and tag (not only a blob), "
                      "and the verified ancestry walk refusing a forged intermediate commit, "
                      "each hold; the QA round-6 fail-closed launch (a corrupt HEAD, a bad config "
                      "and a missing git each exit 2 from stage 1 with a checkout tripwire inert; "
                      "a simulated dubious-ownership refusal fails closed in stage 1 and in "
                      "routing; and round 7: a corrupt ancestor .git/HEAD and a dangling ancestor "
                      ".git file each exit 2 with the tripwire inert while the no-.git-anywhere "
                      "control still reaches the checkout gate), the no-follow bounded drift "
                      "advisory (a symlinked parent, a swapped-in FIFO, an over-cap UNCHECKED "
                      "file; and round 7: a final-component symlink to an outside committed copy, "
                      "an empty-blob FIFO, a refused dot-dot/dot/empty component, a child-bounded "
                      "FIFO open, and the fsmonitor pins held on the genesis route and on "
                      "git_tracked/_index_materialized_tree), the promisor lazy-fetch "
                      "refusal (GIT_NO_LAZY_FETCH pinned, no transport, no sshCommand), and the "
                      "stage-2 sitecustomize isolation hold; and the replayed "
                      "pre-row gate "
                      "(round-2 B1: an undispositioned later release exits 1 after its row lands, with its "
                      "pre-row control; a malformed genesis tree exits 2) holds) hold") if post_ran else \
                     "; the POST-RELEASE head cases were SKIPPED (git archive unavailable)"
        print("SELF-TEST PASS: {}; the end-to-end genesis-structural fail-closed (exit 2, #1), "
              "genesis-flag-mismatch (exit 2), unreachable-predecessor (exit 2), and git-plumbing "
              "predecessor-ownership (#11, no worktree materialized) cases hold; the REAL run() "
              "malformed-input fixtures (releases/dispositions/order/MANIFEST format-version and unknown "
              "top-level key, a duplicate manifest source, junk default-correction and classless "
              "class-change, a duplicate predecessor clause row) each fail closed exit 2 (findings 1/3/4, "
              "round-3 finding 1){}".format(core, full_pack))
    else:
        print("SELF-TEST PASS (PARTIAL): {}; the end-to-end cases were SKIPPED (no writable temp "
              "directory), so those paths are UNVERIFIED this run".format(core))
    return 0


def _parse_args(argv):
    """Parse argv, REJECTING any DUPLICATE option (round-7 finding 7): a repeated flag or value option is a
    conflicting/ambiguous invocation and returns None -> exit 2 BEFORE dispatch, matching release-build's
    seen-option discipline. Returns the opts dict, or None on an unknown or duplicate option."""
    opts = {"self_test": False, "repin": False, "target": None,
            "stage2_repo": None, "stage2_tree": None}
    seen = set()
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--self-test", "--repin"):
            if arg in seen:
                print("error: duplicate option {}".format(arg), file=sys.stderr)
                return None
            seen.add(arg)
            opts["self_test" if arg == "--self-test" else "repin"] = True
            i += 1
        elif arg == "--target" and i + 1 < len(argv):
            if arg in seen:
                print("error: duplicate option {}".format(arg), file=sys.stderr)
                return None
            seen.add(arg)
            opts["target"] = argv[i + 1]
            i += 2
        elif arg in ("--stage2-repo", "--stage2-tree") and i + 1 < len(argv):
            # The INTERNAL stage-2 re-execution arguments (set by stage 1, never by hand): the judged
            # repository and the verified materialization of its HEAD this copy runs from.
            if arg in seen:
                print("error: duplicate option {}".format(arg), file=sys.stderr)
                return None
            seen.add(arg)
            opts["stage2_repo" if arg == "--stage2-repo" else "stage2_tree"] = argv[i + 1]
            i += 2
        else:
            print("usage: check_release_delta.py [--repin --target V] | --self-test (no option may be "
                  "repeated)", file=sys.stderr)
            return None
    return opts


def _resolve_mode(opts):
    """Classify an option set into exactly one dispatch mode BEFORE any work runs, so a mixed or incomplete
    invocation can never fall through to a real run (VC-4 QA #9). Any invocation touching the repin family
    (--repin or --target) resolves to 'repin' (the fail-closed 10.4 stub) or 'error', never 'run' or
    'self-test'. Returns one of: 'self-test', 'run', 'repin', 'stage2', 'error'."""
    repin_family = opts["repin"] or opts["target"] is not None
    if opts["stage2_repo"] is not None or opts["stage2_tree"] is not None:
        # The internal stage-2 mode: BOTH arguments are required and no other mode may be mixed in.
        if (opts["stage2_repo"] is not None and opts["stage2_tree"] is not None
                and not opts["self_test"] and not repin_family):
            return "stage2"
        return "error"
    if opts["self_test"]:
        return "error" if repin_family else "self-test"
    if repin_family:
        # 10.4 repin requires BOTH --repin and --target; anything less is an incomplete mode.
        return "repin" if (opts["repin"] and opts["target"] is not None) else "error"
    return "run"


def main():
    opts = _parse_args(sys.argv[1:])
    if opts is None:
        return 2
    mode = _resolve_mode(opts)
    if mode == "error":
        print("error: --repin is the 10.4 adopter mode; it requires --target V and runs alone. A "
              "--target without --repin, a --repin without --target, or a repin option combined with "
              "--self-test is a mixed or incomplete mode and is rejected fail-closed", file=sys.stderr)
        return 2
    if mode == "stage2":
        # Stage 2 of the re-execution (QA round 5, D-397-REEXEC-FROM-COMMITTED): this process IS the
        # committed copy, launched by stage 1 from a verified materialization of HEAD's tree. Sanity:
        # the running gate file must live under the named tree; then judge the named repository.
        tree = Path(opts["stage2_tree"]).resolve()
        if tree not in Path(__file__).resolve().parents:
            print("error: --stage2-tree does not contain the running gate file; the stage-2 copy "
                  "must run from the materialized committed tree; fail-closed", file=sys.stderr)
            return 2
        return run(Path(opts["stage2_repo"]).resolve())
    if mode == "self-test":
        return self_test_main()
    if mode == "repin":
        # 10.4 adopter re-pin mode: the doctor packaging that drives it is adopter-experience-owned and
        # not part of this release. Rather than run a half-wired path, this fails closed with a clear
        # message (never a silent clean pass), so the mode is declared but does not falsely certify.
        print("error: --repin is the 10.4 adopter mode; its doctor packaging is adopter-experience-"
              "owned and not wired at this release; fail-closed", file=sys.stderr)
        return 2
    return run(repo_root())


if __name__ == "__main__":
    sys.exit(main())
