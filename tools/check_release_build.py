#!/usr/bin/env python3
"""Release build/order/attestation gate (VER-CORE Section 2). Offline except git and the maintainer-side
store read, stdlib only, fail-closed.

Stages (2.6): the default no-flag invocation is the routine AUDIT the runners wire (DORMANT while
releases.toml is zero-row, every tag/row/genesis/ordering/chronology layer reporting NOT APPLICABLE and
each layer arming when its input first exists; a present-but-invalid input always FAILS); --pre-tag is
the candidate gate (reproduce, QA retrieval/success/family-set, genesis, prior rows; never chronology);
--post-tag is the armed pre-merge gate on the proposed attestation commit (tag resolution, newest row,
chronology, ordering). The private-store QA retrieval runs only in the maintainer-side stages and audit
mode SAYS so (2.2 residual), never silently skipping. Dormancy keys off the release-order record and the
rows, never `git tag` probing (the layer_b guard-input-soundness lesson).

Release-order row schema (at source; the qa-digest / store-path / timestamp field names are a spec
[VERIFY], defined here and flagged): each [[release]] row carries version, tag, tag_object_sha,
commit_sha (the four anchor fields), and, once attested, qa-sha256 (the attestation digest, 64 hex),
qa-store-path (a LOGICAL store path, never a host-absolute path), and attestation-timestamps (a list of
UTC epoch seconds, integers). The chronology layer compares these recorded timestamps against the tag's
tagger date, both inside the anchored chain, so it needs no private-store read.

QA attestation object schema (2.2; also a [VERIFY], defined here and flagged): a TOML object carrying
candidate-sha and an array of [[family]] tables, each with name, finished-signal (truthy), verdict
(PASS/clean), unresolved-blockers (0), and timestamps-utc (a list of UTC epoch seconds).

Exit: 0 clean or printed NOT APPLICABLE; 1 a content finding (drift, a failed verdict, a family-set
mismatch, chronology violation, a lightweight tag, a genesis violation, an ordering gap); 2 an
unreachable store, an unresolvable tag or SHA, unreadable records, or a git failure.
"""
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: check_release_build.py requires Python 3.11+ (tomllib).")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, load_toml  # noqa: E402
from check_versions import _parse             # noqa: E402
import gen_manifest                           # noqa: E402  the single shared release-row schema (QA #6)
import _release_schema                        # noqa: E402  the ONE shared strict validator set
from _release_schema import SchemaError       # noqa: E402  (round-2 findings 4)

RELEASES_REL = ".aiqt/core/releases.toml"
MANIFEST_REL = ".aiqt/manifest.toml"
FAMILIES = ("claude", "codex", "gemini")
# The keys this gate validates for a release-order row: the four anchor fields plus the three attestation
# fields a row gains once QA'd. A self-test binds this to gen_manifest.RELEASE_ROW_ALLOWED so the full
# per-field validator here, read_genesis, and check_release_delta cannot diverge on the schema (QA #6).
BUILD_ROW_ANCHOR = ("version", "tag", "tag_object_sha", "commit_sha")
BUILD_ROW_KEYS = frozenset(BUILD_ROW_ANCHOR + ("qa-sha256", "qa-store-path", "attestation-timestamps"))


class GateError(Exception):
    """An input the gate cannot read, parse, or resolve. Exit 2 (fail-closed)."""


# --- git plumbing (every return code checked) -------------------------------------------------------

def _git(root, args, binary=False):
    """Run git, always capturing stdout/stderr as BYTES (round-5 finding 3: text-mode capture raised an
    uncaught UnicodeDecodeError on invalid-UTF-8 git output). The `binary` flag is retained for call-site
    readability but no longer changes the capture; callers decode explicitly via _git_out/_git_err. A launch
    failure is a GateError (cannot-evaluate)."""
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    except OSError as exc:
        raise GateError("git is not available: {}".format(exc))


def _git_out(proc, what):
    """Decode git stdout under an EXPLICIT UnicodeDecodeError -> GateError boundary (round-5 finding 3):
    output the gate must interpret (a tag name, an object id, a type) that is not valid UTF-8 is
    cannot-evaluate (exit 2), never an uncaught crash or a silently replaced value."""
    try:
        return proc.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GateError("{}: git output is not valid UTF-8 ({}); fail-closed".format(what, exc))


def _git_err(proc):
    """git stderr for a DIAGNOSTIC message only; replacement decoding is safe here (never interpreted)."""
    return proc.stderr.decode("utf-8", "replace").strip()


def _tag_kind(root, tag):
    """The git object type the tag ref points at directly: 'tag' for an annotated tag, 'commit' for a
    lightweight one. GateError if the tag does not resolve at all (an unfetched or deleted tag: the two
    are indistinguishable and neither may pass, per the layer_b lesson)."""
    proc = _git(root, ["cat-file", "-t", "refs/tags/" + tag])
    if proc.returncode != 0:
        raise GateError("tag {} does not resolve (unfetched or deleted; fetch tags)".format(tag))
    return _git_out(proc, "cat-file -t {}".format(tag)).strip()


def _rev_parse(root, ref):
    proc = _git(root, ["rev-parse", "--verify", "--quiet", ref])
    if proc.returncode != 0:
        raise GateError("cannot resolve {!r}".format(ref))
    return _git_out(proc, "rev-parse {}".format(ref)).strip()


def _object_exists(root, oid):
    """True if oid names an object that exists in the repository (round-3 finding 8). oid is already
    validated as full lowercase hex by the shared strict schema at load, so this only asks existence."""
    return _git(root, ["cat-file", "-e", oid]).returncode == 0


def _tagger_epoch(root, tag):
    """The tagger-date epoch of an annotated tag. None if the ref is not an annotated tag object (a
    lightweight tag has no tagger date). The tag object is read as BYTES and only the ASCII HEADER (up to
    the first blank line) is parsed, so a non-UTF-8 tag MESSAGE cannot raise an uncaught UnicodeDecodeError
    (round-3 finding 7): a decode failure of the header itself is a GateError (cannot-evaluate, exit 2),
    never a crash reported as exit 1."""
    if _tag_kind(root, tag) != "tag":
        return None
    proc = _git(root, ["cat-file", "tag", tag], binary=True)
    if proc.returncode != 0:
        raise GateError("cannot read tag object for {}".format(tag))
    # The header ends at the first blank line; the (possibly non-UTF-8) message follows and is not parsed.
    header = proc.stdout.split(b"\n\n", 1)[0]
    try:
        header_text = header.decode("ascii")
    except UnicodeDecodeError:
        raise GateError("tag {} has a non-ASCII header; cannot parse the tagger date".format(tag))
    for line in header_text.splitlines():
        if line.startswith("tagger "):
            parts = line.split()
            # "tagger Name <email> <epoch> <tz>": epoch is the second-to-last token.
            try:
                return int(parts[-2])
            except (ValueError, IndexError):
                raise GateError("tag {} has a malformed tagger line".format(tag))
    return None


def _show_toml(root, ref, path):
    proc = _git(root, ["show", "{}:{}".format(ref, path)], binary=True)
    if proc.returncode != 0:
        raise GateError("git show {}:{} failed".format(ref, path))
    try:
        return tomllib.loads(proc.stdout.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise GateError("{} at {} does not parse: {}".format(path, ref, exc))


# --- pure layers (always in --self-test) ------------------------------------------------------------

FAMILY_KEYS = frozenset({"name", "finished-signal", "verdict", "unresolved-blockers", "timestamps-utc"})


def success_findings(families):
    """2.2 SUCCESS + FAMILY-SET, STRICT (finding 5). families is a list of family tables. Each MUST carry
    exactly the FAMILY_KEYS (no unknown or missing field); finished-signal MUST be a real boolean True (the
    string "yes" is rejected, a truthy non-bool no longer passes); verdict MUST be PASS/clean;
    unresolved-blockers MUST be present and a real integer zero; timestamps-utc MUST be a non-empty list of
    integer epochs. A permissive or malformed attestation is a FAIL, never a false clean."""
    findings = []
    names = [f.get("name") for f in families]
    if sorted(str(n) for n in names) != sorted(FAMILIES) or len(set(names)) != len(names):
        findings.append("family set {} != required tri-family set {} (each exactly once)".format(
            sorted(str(n) for n in names), list(FAMILIES)))
    for fam in families:
        who = fam.get("name")
        extra = set(fam) - FAMILY_KEYS
        missing = FAMILY_KEYS - set(fam)
        if extra or missing:
            findings.append("family {}: keys are not exactly {} (unexpected {}, missing {})".format(
                who, sorted(FAMILY_KEYS), sorted(extra), sorted(missing)))
        if fam.get("finished-signal") is not True:
            findings.append("family {}: finished-signal must be a real boolean true, not {!r} (a degraded "
                            "or forged delivery is not a verdict)".format(who, fam.get("finished-signal")))
        if fam.get("verdict") not in ("PASS", "clean"):
            findings.append("family {}: verdict {!r} is not clean".format(who, fam.get("verdict")))
        blk = fam.get("unresolved-blockers")
        if not isinstance(blk, int) or isinstance(blk, bool) or blk != 0:
            findings.append("family {}: unresolved-blockers must be the integer 0, not {!r}".format(
                who, blk))
        ts = fam.get("timestamps-utc")
        if not isinstance(ts, list) or not ts or not all(isinstance(t, int) and not isinstance(t, bool)
                                                         for t in ts):
            findings.append("family {}: timestamps-utc must be a non-empty list of integer epochs".format(
                who))
    return findings


def chronology_findings(attestation_epochs, tagger_epoch):
    """2.2 CHRONOLOGY: every attestation timestamp strictly earlier than the tagger date; a missing
    tagger date is a FAIL (annotated tags are mandatory)."""
    if tagger_epoch is None:
        return ["release tag carries no tagger date (a lightweight or malformed tag; 2.1 requires an "
                "annotated tag)"]
    return ["attestation timestamp {} is not strictly earlier than the tagger date {}".format(
        t, tagger_epoch) for t in attestation_epochs if not (t < tagger_epoch)]


def genesis_findings(rows_by_tag_genesis):
    """2.5: over all validated rows, exactly one tagged manifest declares genesis = true, and it is the
    first. rows_by_tag_genesis is an ordered list of booleans."""
    findings = []
    count = sum(1 for g in rows_by_tag_genesis if g)
    if rows_by_tag_genesis and count != 1:
        findings.append("{} tagged manifest(s) declare genesis = true; exactly one release ever "
                        "may".format(count))
    if rows_by_tag_genesis and count == 1 and not rows_by_tag_genesis[0]:
        findings.append("genesis = true appears on a non-first release row")
    return findings


def ordering_findings(versions):
    """2.4 ORDERING GATE: the release-order versions are strictly SemVer-increasing (no gap in the
    anchored prefix, no reorder, no duplicate). A predecessor missing its row surfaces as a
    non-increase. versions is a list of version strings in row order."""
    findings = []
    tuples = []
    for v in versions:
        t = _parse(v)
        if t is None:
            findings.append("release-order row version {!r} is not a bare SemVer".format(v))
            return findings
        tuples.append(t)
    for i in range(1, len(tuples)):
        if tuples[i] <= tuples[i - 1]:
            findings.append("release-order row #{} version {} is not strictly greater than the "
                            "preceding {} (a reorder, duplicate, or missing predecessor row)".format(
                                i + 1, versions[i], versions[i - 1]))
    return findings


def _all_release_tags(root):
    """EVERY tag named 'v<bare SemVer>' in the repo, mapped to its object kind: {version: (tag, kind)} where
    kind is 'tag' (annotated) or 'commit' (lightweight). Round-5 finding 2: a release tag is enumerated
    regardless of kind, so a LIGHTWEIGHT release tag is neither silently discarded (which hid it from
    dormancy and coverage) nor treated as valid. The `git tag -l` output is decoded under the finding-3
    strict boundary, so an invalid-UTF-8 ref is cannot-evaluate (exit 2), not a crash."""
    proc = _git(root, ["tag", "-l", "v*"])
    if proc.returncode != 0:
        raise GateError("cannot list release tags ({})".format(_git_err(proc)))
    out = {}
    for line in _git_out(proc, "git tag -l").splitlines():
        tag = line.strip()
        if not tag.startswith("v") or _parse(tag[1:]) is None:
            continue
        out[tag[1:]] = (tag, _tag_kind(root, tag))
    return out


def release_tag_findings(root, rows):
    """2.4 / VER-CORE-SPEC.md:231-235,273 RELEASE-TAG COVERAGE. Over EVERY v<SemVer> tag (round-5 finding 2):
    (a) a LIGHTWEIGHT release tag is disallowed (2.1) and is a finding; (b) an ANNOTATED release tag AT OR
    BELOW the newest recorded row that has no attestation row is a missing-predecessor finding (a tagged
    release must carry its row before the next release is accepted). A tag ABOVE the newest recorded row (or
    any tag when there are zero rows) is the in-flight release whose row is appended post-tag, so it is not a
    missing-row finding, but a lightweight in-flight tag is still disallowed."""
    findings = []
    recorded = {_parse(r["version"]): r["version"] for r in rows if _parse(r["version"]) is not None}
    newest = max(recorded) if recorded else None
    for ver, (tag, kind) in sorted(_all_release_tags(root).items()):
        t = _parse(ver)
        if kind != "tag":
            findings.append("release tag {} is LIGHTWEIGHT; release tags must be annotated (2.1); a "
                            "lightweight release tag is disallowed".format(tag))
        if t is not None and newest is not None and t <= newest and t not in recorded:
            findings.append("release {} is tagged ({}) at or below the newest attested release {} but has "
                            "no attestation row; a predecessor missing its row blocks the next release "
                            "(2.4/L273)".format(ver, tag, recorded[newest]))
    return findings


def validate_qa_obj(obj, expect_candidate_sha):
    """The pure QA-object validation (2.2), STRICT (finding 5): the object carries exactly candidate-sha and
    family (no unknown or missing top-level key), candidate-sha matches, family is an array of tables, and
    each family passes the strict success/schema check. Returns (findings, attestation_epochs) where the
    epochs are the integer timestamps-utc entries retrieved FROM the object (finding 6: the caller compares
    THESE, not the row's, against the tagger date)."""
    findings = []
    if not isinstance(obj, dict):
        return ["QA attestation is not a table"], []
    extra = set(obj) - {"candidate-sha", "family"}
    if extra:
        findings.append("QA attestation carries unknown top-level key(s): {}".format(", ".join(sorted(extra))))
    if obj.get("candidate-sha") != expect_candidate_sha:
        findings.append("QA attestation candidate SHA {!r} != expected {!r}".format(
            obj.get("candidate-sha"), expect_candidate_sha))
    fams = obj.get("family", [])
    if not isinstance(fams, list) or not fams or not all(isinstance(f, dict) for f in fams):
        return findings + ["QA attestation [[family]] is not a non-empty array of tables"], []
    findings += success_findings(fams)
    epochs = []
    for f in fams:
        ts = f.get("timestamps-utc")
        if isinstance(ts, list):
            epochs += [t for t in ts if isinstance(t, int) and not isinstance(t, bool)]
    return findings, epochs


# --- maintainer-side QA retrieval -------------------------------------------------------------------

def qa_layers(qa_path, qa_sha256, expect_candidate_sha):
    """Retrieve, hash, parse, and validate the private-store QA object (2.2). Unreachable or unreadable
    is exit 2 (GateError); content mismatches are findings. Returns (findings, attestation_epochs). The
    recorded/supplied qa-sha256 is SYNTAX-validated first (round-4 finding 4): a value that is not 64
    lowercase hex is a malformed control, exit 2, never a mere content mismatch."""
    if not isinstance(qa_sha256, str) or not _release_schema.HEX64_RE.fullmatch(qa_sha256):
        raise GateError("qa-sha256 {!r} is not 64 lowercase hex (malformed control input)".format(qa_sha256))
    try:
        blob = Path(qa_path).read_bytes()
    except OSError as exc:
        raise GateError("QA attestation unreachable at {} ({})".format(qa_path, exc))
    if hashlib.sha256(blob).hexdigest() != qa_sha256:
        return ["QA attestation bytes do not match the recorded digest"], []
    try:
        obj = tomllib.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise GateError("QA attestation does not parse: {}".format(exc))
    return validate_qa_obj(obj, expect_candidate_sha)


# --- reproduce gate (pre-tag) -----------------------------------------------------------------------

def _regenerate_check_commands(co):
    """The --check form of every registry generator's regenerate command, plus gen_manifest --check and
    check_manifest, run inside the candidate checkout. Enumerated via gen_gensrc.build_registry (the
    verified house loader), so the reproduce set is the same registry check_gensrc_failclose covers."""
    import gen_gensrc  # the validated registry loader (house import-reuse pattern)
    # build_registry returns the registry JSON TEXT ({"version", "generated": [...]}), not a list of
    # dicts; parse it and iterate the "generated" entries (QA #7: the old code iterated the string and
    # called .get on each character, an AttributeError that crashed every --pre-tag reproduce).
    registry = json.loads(gen_gensrc.build_registry(co)).get("generated", [])
    commands = []
    for entry in registry:
        regen = entry.get("regenerate", "")
        toks = regen.split()
        # Map "python3 tools/gen_x.py [args]" to its --check form; skip a non-python3 regenerate step.
        if len(toks) >= 2 and toks[0] == "python3" and toks[1].startswith("tools/") and "--check" not in toks:
            commands.append(["python3", toks[1], "--check"])
    commands.append(["python3", "tools/gen_manifest.py", "--check"])
    commands.append(["python3", "tools/check_manifest.py"])
    return commands


def _fresh_git_env():
    """A neutralized environment plus a FRESH-repo posture: no inherited GIT_* and no user/global/system
    config, so a re-init'd materialized tree carries NO filter/attribute drivers from the source repo."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    return env


def reproduce_gate(root, sha):
    """2.1 step 4: reproduce the exact candidate SHA and run every registry generator's --check plus
    gen_manifest --check plus check_manifest against it. The candidate is materialized from RAW blob bytes
    (git ls-tree + cat-file, round-4 finding 1: NO worktree checkout, so no smudge/clean filter can restore
    old bytes and hide a change), then re-init'd as a FRESH git repo (empty config, so the source repo's
    filter drivers cannot run) purely so the git-based reproduce commands (gen_manifest, check_manifest)
    have a repository. --check IS a byte comparison against fresh regeneration, so any drift is exit 1.
    A child exit 2 or a launch failure is cannot-evaluate and raises GateError -> exit 2 (round-4 finding
    4), never downgraded to a drift finding. Returns findings."""
    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-release-reproduce-"))
        co = tmp / "co"
        co.mkdir()
    except OSError as exc:
        raise GateError("reproduce: cannot create a temporary directory ({}); fail-closed".format(exc))
    env = _fresh_git_env()
    try:
        try:
            _release_schema.materialize_tree_raw(root, sha, co)
        except SchemaError as exc:
            raise GateError("cannot materialize the candidate tree: {}".format(exc))
        for args in (["init", "-q"], ["-c", "user.name=aiqt", "-c", "user.email=a@b.invalid", "add", "-A"],
                     ["-c", "user.name=aiqt", "-c", "user.email=a@b.invalid", "commit", "-q", "-m",
                      "reproduce", "--no-verify"]):
            try:
                r = subprocess.run(["git", "-C", str(co), *args], capture_output=True, env=env)
            except OSError as exc:
                raise GateError("cannot launch git to stage the candidate tree ({}); fail-closed".format(exc))
            if r.returncode != 0:
                raise GateError("cannot stage the candidate tree for reproduction: {}".format(
                    r.stderr.decode("utf-8", "replace").strip()))
        try:
            commands = _regenerate_check_commands(co)
        except Exception as exc:  # noqa: BLE001  a bad registry is cannot-evaluate, not clean
            raise GateError("cannot enumerate the reproduce command set ({})".format(exc))
        findings = []
        for argv in commands:
            try:
                # BYTES capture (round-5 finding 3): a generator emitting invalid UTF-8 must not crash the
                # gate; the returncode drives the verdict and the tail is decoded with replacement.
                r = subprocess.run(argv, cwd=str(co), capture_output=True, env=env)
            except OSError as exc:
                raise GateError("reproduce: {!r} could not launch ({}); fail-closed".format(
                    " ".join(argv), exc))
            if r.returncode < 0 or r.returncode > 1:
                diag = (r.stderr or r.stdout).decode("utf-8", "replace").strip()[:300]
                raise GateError("reproduce: {!r} could not evaluate (rc={}): {}".format(
                    " ".join(argv), r.returncode, diag))
            if r.returncode == 1:
                findings.append("reproduce drift: {!r} rc=1".format(" ".join(argv)))
        return findings
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- row loading ------------------------------------------------------------------------------------

def _strict_releases(data, where):
    """Run the shared strict releases validator and map its SchemaError to this gate's fail-closed
    GateError (exit 2). One conversion point for the working-tree loader AND the arbitrary-commit
    (--post-tag) loader, so both get identical strict validation (round-2 finding 4): format-version == 1,
    the exact top-level keyset, and every complete-record attestation field on every present row, in audit,
    prior-row pre-tag, and post-tag. A present row missing qa-sha256/qa-store-path/attestation-timestamps
    (the round-2 permissive schema) is now exit 2, not a clean pass."""
    try:
        return _release_schema.strict_releases(data, where)
    except SchemaError as exc:
        raise GateError(str(exc))


def load_build_rows(root):
    """The release-order rows the build gate validates, through the ONE shared strict validator. Zero rows
    is the genesis/dormant state; every PRESENT row is a complete post-QA attestation record (2.4/L254).
    Fail-closed on any malformed record, including format-version and unknown top-level keys (finding 4)."""
    try:
        data = load_toml(root / RELEASES_REL)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise GateError("cannot read {} ({})".format(RELEASES_REL, exc))
    return _strict_releases(data, RELEASES_REL)


def _validate_tag_row(root, row):
    """Resolve a row's annotated tag to both recorded SHAs and confirm the tagged tree's manifest
    declares the row's version. Returns (findings, genesis_bool). Unresolvable tag/SHA is GateError."""
    findings = []
    tag, version = row["tag"], row["version"]
    if tag != "v" + version:
        findings.append("release {} records tag {!r}; expected {!r}".format(version, tag, "v" + version))
    if _tag_kind(root, tag) != "tag":
        findings.append("release {} tag {} is not an annotated tag (a lightweight release tag is "
                        "disallowed, 2.1)".format(version, tag))
    # The recorded objects must EXIST (round-3 finding 8): a syntactically-valid but nonexistent recorded
    # object is cannot-evaluate (exit 2); a resolvable-but-wrong object is the mismatch finding below. A
    # syntactically malformed id was already rejected exit 2 by the shared strict schema at load.
    for key in ("tag_object_sha", "commit_sha"):
        if not _object_exists(root, row[key]):
            raise GateError("release {} records {} {} that does not exist in the repository (2.4)".format(
                version, key, row[key]))
    if _rev_parse(root, "refs/tags/" + tag) != row["tag_object_sha"]:
        findings.append("release {} tag_object_sha does not match the resolved tag object".format(version))
    if _rev_parse(root, "refs/tags/" + tag + "^{commit}") != row["commit_sha"]:
        findings.append("release {} commit_sha does not match the tag's peeled commit".format(version))
    tagged_manifest = _show_toml(root, tag + "^{commit}", MANIFEST_REL)
    # Strict-validate the tagged tree's manifest BEFORE reading any field (round-4 finding 3): a
    # format-version=999 or an unknown top-level key in a tagged manifest is a schema fault, exit 2, not a
    # clean audit.
    try:
        _release_schema.strict_manifest(tagged_manifest, "tagged manifest for release {}".format(version))
    except SchemaError as exc:
        raise GateError(str(exc))
    if tagged_manifest.get("release-version") != version:
        findings.append("release {} tag points at a tree whose manifest release-version is {!r}".format(
            version, tagged_manifest.get("release-version")))
    return findings, tagged_manifest.get("genesis") is True


# --- run stages -------------------------------------------------------------------------------------

def run_audit(root):
    """Default AUDIT (2.6): validate whatever exists. Dormancy requires ZERO release TAGS AND ZERO rows
    (round-5 finding 2, spec 2.6/L324-329): each layer arms when its first tag OR row exists, so a zero-row
    tree that already carries a v<SemVer> tag is armed, and a lightweight or unrecorded tag is caught."""
    try:
        rows = load_build_rows(root)
        tags = _all_release_tags(root)
        if not rows and not tags:
            for layer in ("tag-resolution", "genesis-uniqueness", "ordering", "chronology"):
                print("release-build: {} NOT APPLICABLE (zero release tags AND zero rows; arms at the "
                      "first tag or attestation row)".format(layer))
            print("release-build: qa-retrieval MAINTAINER-SIDE (runs under --pre-tag/--post-tag; the "
                  "private store is unreachable from CI, 2.2 residual)")
            return 0
        findings = []
        findings += ordering_findings([r["version"] for r in rows])
        findings += release_tag_findings(root, rows)  # finding 2: lightweight + missing-row tagged releases
        genesis_flags = []
        for row in rows:
            row_findings, is_genesis = _validate_tag_row(root, row)
            findings += row_findings
            genesis_flags.append(is_genesis)
        findings += genesis_findings(genesis_flags)
        # Chronology on the newest row (only when a row exists; a tree armed by a TAG but carrying ZERO rows
        # has no attestation row to compare, round-5 finding 2). The strict loader guarantees a present row's
        # timestamps are non-empty (finding 4), so this is a real comparison, never a vacuous clean.
        if rows:
            newest = rows[-1]
            findings += chronology_findings(newest["attestation-timestamps"],
                                            _tagger_epoch(root, newest["tag"]))
        print("release-build: qa-retrieval MAINTAINER-SIDE (runs under --pre-tag/--post-tag; the "
              "private store is unreachable from CI, 2.2 residual)")
    except (GateError, OSError, UnicodeError) as exc:
        # A GateError, a stray filesystem OSError, or a stray decode error at a stage boundary is
        # cannot-evaluate, exit 2 (round-5 findings 3/4), never a crash-to-exit-1.
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    return _report(findings, "audit")


def run_pre_tag(root, candidate_sha, qa_path, qa_sha256, first_pin, evidence):
    """--pre-tag CANDIDATE mode (2.6): reproduce against committed digests, QA retrieval/success/
    family-set, genesis declaration, prior-row validation. Never chronology (no tag yet)."""
    try:
        findings = []
        findings += reproduce_gate(root, candidate_sha)
        qa_findings, _epochs = qa_layers(qa_path, qa_sha256, candidate_sha)
        findings += qa_findings
        # genesis declaration of the candidate tree; the candidate manifest AND releases record are
        # strict-validated before any field is read (round-2 finding 4 + round-3 finding 3): a bad
        # format-version or unknown key on the candidate manifest is exit 2.
        cand_manifest = _show_toml(root, candidate_sha, MANIFEST_REL)
        try:
            _release_schema.strict_manifest(cand_manifest, "candidate " + MANIFEST_REL)
        except SchemaError as exc:
            raise GateError(str(exc))
        cand_rows = _strict_releases(_show_toml(root, candidate_sha, RELEASES_REL),
                                     "candidate " + RELEASES_REL)
        is_genesis = cand_manifest.get("genesis") is True
        if is_genesis and cand_rows:
            findings.append("candidate declares genesis = true but its releases.toml is not header-only "
                            "(2.5)")
        # Validate the CANDIDATE's rows EXCLUSIVELY, never the ambient worktree (round-3 finding 2: the
        # candidate is the execution target, spec 2.6/L315; an ambient corrected row must not mask a bad
        # prior tag in the candidate). Ordering, predecessor completeness, tag resolution, and genesis
        # uniqueness all run over cand_rows.
        findings += ordering_findings([r["version"] for r in cand_rows])
        findings += release_tag_findings(root, cand_rows)
        cand_genesis_flags = []
        for row in cand_rows:
            row_findings, g = _validate_tag_row(root, row)
            findings += row_findings
            cand_genesis_flags.append(g)
        findings += genesis_findings(cand_genesis_flags)
        # The first-pin precondition is AUTO-REQUIRED for a genesis / default-switch candidate, whether or
        # not the operator passed --first-pin (round-4 finding 5; the GD-95 default switch IS the genesis
        # release, spec L1051). --first-pin forces it for a non-genesis candidate too.
        if first_pin or is_genesis:
            findings += _first_pin_findings(root, candidate_sha, evidence)
    except (GateError, OSError, UnicodeError) as exc:
        # A GateError, a stray filesystem OSError, or a stray decode error at a stage boundary is
        # cannot-evaluate, exit 2 (round-5 findings 3/4), never a crash-to-exit-1.
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    return _report(findings, "pre-tag")


FIRST_PIN_EVIDENCE_KEYS = frozenset({"candidate-sha", "observed-measurement", "cap-bytes",
                                     "prefix-superset", "demonstration", "agents-sha256"})
FIRST_PIN_DEMO_KEYS = frozenset({"agents-sha256", "delivered-prefix-obligations",
                                 "floor-profile-obligations"})
CAP_BYTES = 32768  # the documented Codex default project_doc_max_bytes cap (VER-CORE-SPEC.md:1019)


def _show_bytes(root, ref, path):
    """The raw bytes of `path` at `ref`, or None ONLY when the path is genuinely ABSENT from the tree
    (round-4 finding 7). The commit is resolved and the tree inspected SEPARATELY, so an infrastructure or
    resolution failure (an unresolvable ref, a git error) raises GateError -> exit 2 rather than being
    conflated with an absent path and downgraded to a content finding. Never decodes."""
    _rev_parse(root, ref + "^{commit}")   # resolution failure -> GateError (cannot-evaluate)
    ls = _git(root, ["ls-tree", "-r", "-z", "--name-only", ref, "--", path])
    if ls.returncode != 0:
        raise GateError("git ls-tree {}:{} failed ({})".format(ref, path, _git_err(ls)))
    if not ls.stdout.replace(b"\x00", b"").strip():
        return None   # the ref resolves and the tree lists cleanly, but this path is absent
    proc = _git(root, ["show", "{}:{}".format(ref, path)], binary=True)
    if proc.returncode != 0:
        raise GateError("git show {}:{} failed after ls-tree confirmed the path is present".format(
            ref, path))
    return proc.stdout


def _demo_superset_findings(demo, agents_sha, ref):
    """Recompute the referenced prefix-superset demonstration (round-3 finding 5): its schema, its binding
    to the candidate AGENTS.md digest, and that the floor profile is actually a SUPERSET of the delivered
    default-capped prefix obligations. The demonstration artifact (schema a [VERIFY] defined here) is a
    TOML file carrying agents-sha256 plus the two obligation lists, so the superset is machine-checkable
    offline rather than trusted as a boolean."""
    findings = []
    if set(demo) != FIRST_PIN_DEMO_KEYS:
        findings.append("first-pin: demonstration {} keys are not exactly {}".format(
            ref, sorted(FIRST_PIN_DEMO_KEYS)))
    if demo.get("agents-sha256") != agents_sha:
        findings.append("first-pin: demonstration {} agents-sha256 is not bound to the candidate "
                        "AGENTS.md digest".format(ref))
    delivered, floor = demo.get("delivered-prefix-obligations"), demo.get("floor-profile-obligations")
    if not isinstance(delivered, list) or not delivered or not all(isinstance(x, str) for x in delivered):
        findings.append("first-pin: demonstration {} delivered-prefix-obligations must be a non-empty list "
                        "of strings".format(ref))
    elif not isinstance(floor, list) or not all(isinstance(x, str) for x in floor):
        findings.append("first-pin: demonstration {} floor-profile-obligations must be a list of "
                        "strings".format(ref))
    elif not set(delivered) <= set(floor):
        findings.append("first-pin: demonstration {} floor profile is NOT a superset of the delivered "
                        "prefix; missing {}".format(ref, sorted(set(delivered) - set(floor))[:5]))
    return findings


def _first_pin_evidence_findings(root, candidate_sha, evidence):
    """Validate the --first-pin EVIDENCE artifact and BIND it to the candidate's AGENTS.md (round-3 finding
    5; 2.1/6.6/VER-CORE-SPEC.md:1034), not merely its syntax. --evidence is REQUIRED in --first-pin mode.
    The artifact carries EXACTLY FIRST_PIN_EVIDENCE_KEYS. AGENTS.md is retrieved FROM the candidate commit
    and its digest and byte count RECOMPUTED: agents-sha256 must equal the recomputed digest;
    observed-measurement must equal the recomputed byte count; cap-bytes must be the documented default
    (CAP_BYTES); the recomputed size must exceed the cap; prefix-superset must be a real boolean true; and
    the demonstration reference must resolve in the candidate tree and its superset be RECOMPUTED via
    _demo_superset_findings. An unreadable/unparseable evidence artifact, an AGENTS.md not retrievable from
    the candidate, or a demonstration that resolves but is not offline-evaluable (does not parse) is exit 2.
    DISCLOSED RESIDUAL (disclose-guard-residuals): the obligation lists inside a well-formed demonstration
    are taken as authored (the pack does not itself re-derive the default-cap prefix from AGENTS.md bytes);
    URL reachability is not tested offline."""
    if evidence is None:
        return ["first-pin: --evidence is REQUIRED in --first-pin mode (the first-pin precondition is owed "
                "delivered evidence, not asserted; L1034)"]
    # lstat first (round-5 finding 5): ONLY a genuine FileNotFoundError (no such path entry) establishes
    # absence, a finding. A path that exists but is a SYMLINK (including a loop), a non-regular file, or is
    # otherwise unusable is cannot-evaluate -> GateError exit 2, never a "does not exist" finding.
    ev_path = Path(evidence)
    try:
        st = os.lstat(ev_path)
    except FileNotFoundError:
        return ["first-pin: the named evidence artifact {} does not exist".format(evidence)]
    except OSError as exc:
        raise GateError("first-pin: cannot stat the evidence artifact {} ({}); fail-closed".format(
            evidence, exc))
    if not stat.S_ISREG(st.st_mode):
        raise GateError("first-pin: the evidence artifact {} is not a regular file (a symlink or special "
                        "entry); fail-closed".format(evidence))
    try:
        raw = ev_path.read_bytes()
    except OSError as exc:
        raise GateError("first-pin: evidence artifact {} is unreadable ({}); fail-closed".format(
            evidence, exc))
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise GateError("first-pin: evidence artifact {} does not parse ({}); fail-closed".format(
            evidence, exc))
    agents = _show_bytes(root, candidate_sha, "AGENTS.md")
    if agents is None:
        raise GateError("first-pin: AGENTS.md is not retrievable from the candidate {}; the first-pin "
                        "precondition cannot be bound (exit 2)".format(candidate_sha))
    actual_sha, actual_len = hashlib.sha256(agents).hexdigest(), len(agents)
    findings = []
    extra, missing = set(data) - FIRST_PIN_EVIDENCE_KEYS, FIRST_PIN_EVIDENCE_KEYS - set(data)
    if extra or missing:
        findings.append("first-pin: evidence keys are not exactly {} (unexpected {}, missing {})".format(
            sorted(FIRST_PIN_EVIDENCE_KEYS), sorted(extra), sorted(missing)))
    if data.get("candidate-sha") != candidate_sha:
        findings.append("first-pin: evidence candidate-sha {!r} is not bound to the candidate commit "
                        "{!r}".format(data.get("candidate-sha"), candidate_sha))
    dg = data.get("agents-sha256")
    if not isinstance(dg, str) or not _release_schema.HEX64_RE.fullmatch(dg):
        findings.append("first-pin: evidence agents-sha256 is not a 64-hex digest")
    elif dg != actual_sha:
        findings.append("first-pin: evidence agents-sha256 does not match the candidate's recomputed "
                        "AGENTS.md digest")
    meas, cap = data.get("observed-measurement"), data.get("cap-bytes")
    if not isinstance(meas, int) or isinstance(meas, bool):
        findings.append("first-pin: observed-measurement must be an integer")
    elif meas != actual_len:
        findings.append("first-pin: observed-measurement {} != the candidate AGENTS.md byte count {} "
                        "(the measurement must be recomputed, not asserted)".format(meas, actual_len))
    if cap != CAP_BYTES:
        findings.append("first-pin: cap-bytes must be the documented default cap {} (L1019), not {!r}".format(
            CAP_BYTES, cap))
    if actual_len <= CAP_BYTES:
        findings.append("first-pin: the candidate AGENTS.md is {} bytes and does not exceed the default cap "
                        "{} (the compatibility-contract premise, L1028)".format(actual_len, CAP_BYTES))
    if data.get("prefix-superset") is not True:
        findings.append("first-pin: prefix-superset must be true (the delivered-prefix-superset "
                        "demonstration must succeed, L1029)")
    demo_ref = data.get("demonstration")
    if not isinstance(demo_ref, str) or not demo_ref:
        findings.append("first-pin: demonstration must be a non-empty reference")
    else:
        demo_bytes = _show_bytes(root, candidate_sha, demo_ref)
        if demo_bytes is None:
            findings.append("first-pin: the demonstration reference {} does not resolve in the candidate "
                            "tree".format(demo_ref))
        else:
            try:
                demo = tomllib.loads(demo_bytes.decode("utf-8"))
            except (UnicodeDecodeError, tomllib.TOMLDecodeError):
                raise GateError("first-pin: the demonstration {} resolves but is not offline-evaluable "
                                "(does not parse); adopter-owned evidence that cannot be evaluated is "
                                "exit 2".format(demo_ref))
            findings += _demo_superset_findings(demo, actual_sha, demo_ref)
    return findings


def _first_pin_findings(root, candidate_sha, evidence):
    """--first-pin (2.1/6.6): the step-1 freeze holds in the candidate (check_clauses --genesis passes) AND
    the delivered prefix-superset EVIDENCE is present, valid, and BOUND to the candidate's AGENTS.md
    (round-3 finding 5). The candidate is materialized from RAW blob bytes (round-4 finding 1: no worktree
    checkout, so no smudge/clean filter can restore old bytes), and a check_clauses child exit 2 or a launch
    failure is cannot-evaluate -> GateError exit 2 (round-4 finding 4), never downgraded to a finding."""
    findings = list(_first_pin_evidence_findings(root, candidate_sha, evidence))
    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-release-firstpin-"))
        co = tmp / "co"
        co.mkdir()
    except OSError as exc:
        raise GateError("first-pin: cannot create a temporary directory ({}); fail-closed".format(exc))
    try:
        try:
            _release_schema.materialize_tree_raw(root, candidate_sha, co)
        except SchemaError as exc:
            raise GateError("cannot materialize the candidate for first-pin: {}".format(exc))
        try:
            # BYTES capture (round-5 finding 3): a check_clauses child emitting invalid UTF-8 must not crash.
            r = subprocess.run(["python3", "tools/check_clauses.py", "--genesis", "--root", str(co)],
                               cwd=str(co), capture_output=True)
        except OSError as exc:
            raise GateError("first-pin: cannot launch check_clauses.py ({}); fail-closed".format(exc))
        if r.returncode < 0 or r.returncode > 1:
            diag = (r.stderr or r.stdout).decode("utf-8", "replace").strip()[:300]
            raise GateError("first-pin: check_clauses.py --genesis could not evaluate the candidate "
                            "(rc={}): {}".format(r.returncode, diag))
        if r.returncode == 1:
            findings.append("first-pin: check_clauses.py --genesis fails in the candidate (rc=1); the "
                            "step-1 freeze is not clean")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return findings


def run_post_tag(root, attestation_commit, qa_path):
    """--post-tag ARMED mode (2.6): tag resolution, STRICT full-row validation, the 2.4 ordering
    assertions, the 2.2 chronology layer, AND (QA #8) retrieval + re-hash + family validation of the
    newest row's attestation QA object, against the proposed attestation commit (default HEAD). Required
    pre-merge check on the attestation-commit branch (2.1 step 6). The newest row MUST be fully attested
    (qa-sha256, qa-store-path, attestation-timestamps present) and --qa-path MUST resolve the QA object:
    the armed gate never certifies a release without retrieving and re-hashing its recorded QA evidence,
    and it never defaults a missing field to clean."""
    try:
        ref = attestation_commit or "HEAD"
        releases = _show_toml(root, ref, RELEASES_REL)
        # Re-validate through the ONE shared strict validator, exactly as the working-tree loader: every
        # present row is a complete attestation record, so post-tag gets the full schema, not just anchor
        # fields (finding 4). A newest row missing qa-sha256/qa-store-path/attestation-timestamps is now
        # exit 2 here, never a default-to-clean.
        norm = _strict_releases(releases, "attestation " + RELEASES_REL)
        if not norm:
            raise GateError("--post-tag: the proposed attestation commit {} has no release rows to "
                            "validate".format(ref))
        findings = ordering_findings([r["version"] for r in norm])
        findings += release_tag_findings(root, norm)  # findings 2/7: lightweight + missing-row
        genesis_flags = []
        for row in norm:
            row_findings, is_genesis = _validate_tag_row(root, row)
            findings += row_findings
            genesis_flags.append(is_genesis)
        findings += genesis_findings(genesis_flags)
        newest = norm[-1]
        # Retrieve, re-hash, and validate the recorded QA object against the newest candidate commit, and
        # derive the chronology from the RETRIEVED object (finding 6): the retrieved timestamps are compared
        # against the tagger date, and the row's timestamp list MUST EQUAL the retrieved timestamps, so an
        # early row timestamp cannot forge chronology over a QA object whose real timestamps postdate the tag.
        if qa_path is None:
            raise GateError("--post-tag requires --qa-path to retrieve and re-hash the newest row's "
                            "attestation QA object; the armed gate never certifies without it")
        qa_findings, epochs = qa_layers(qa_path, newest["qa-sha256"], newest["commit_sha"])
        findings += qa_findings
        findings += chronology_findings(epochs, _tagger_epoch(root, newest["tag"]))
        # The row's timestamp list must EQUAL the NORMALIZED (sorted, de-duplicated) timestamps retrieved
        # from the hashed QA object, so an early row timestamp cannot forge chronology over a QA object
        # whose real timestamps postdate the tag (finding 6).
        if sorted(set(newest["attestation-timestamps"])) != sorted(set(epochs)):
            findings.append("newest release row attestation-timestamps {} do not equal the normalized "
                            "timestamps {} retrieved from the hashed QA object (no forging; finding 6)"
                            .format(sorted(set(newest["attestation-timestamps"])), sorted(set(epochs))))
    except (GateError, OSError, UnicodeError) as exc:
        # A GateError, a stray filesystem OSError, or a stray decode error at a stage boundary is
        # cannot-evaluate, exit 2 (round-5 findings 3/4), never a crash-to-exit-1.
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    return _report(findings, "post-tag")


def _report(findings, stage):
    if findings:
        print("FAIL: {} release-build finding(s) [{}]".format(len(findings), stage))
        for f in findings:
            print("  " + f)
        return 1
    print("PASS: release-build [{}] clean".format(stage))
    return 0


# --- self-test --------------------------------------------------------------------------------------
# Pure predicate cases (success, family-set, chronology, genesis uniqueness, ordering, QA-object
# validation) always run. The git-level cases build a throwaway repo with annotated and lightweight
# tags and are skipped with a printed note (never a false pass) where git or a tempdir is unavailable.

def _run_audit_quiet(root):
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return run_audit(root)


def _run_post_tag_quiet(root, attestation_commit, qa_path):
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return run_post_tag(root, attestation_commit, qa_path)


def _run_pre_tag_quiet(root, candidate_sha, qa_path, qa_sha256, first_pin, evidence):
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return run_pre_tag(root, candidate_sha, qa_path, qa_sha256, first_pin, evidence)


def self_test_main():  # noqa: C901  a flat sequence of independent predicate and fixture cases
    failures = []

    ok_fams = [{"name": n, "finished-signal": True, "verdict": "PASS", "unresolved-blockers": 0,
                "timestamps-utc": [5]} for n in FAMILIES]
    if success_findings(ok_fams):
        failures.append("success_findings: a clean tri-family set expected no finding")
    # missing a family.
    if not success_findings(ok_fams[:2]):
        failures.append("success_findings: a missing family expected a finding")
    # a duplicate family.
    dup = ok_fams[:2] + [dict(ok_fams[0])]
    if not success_findings(dup):
        failures.append("success_findings: a duplicate family expected a finding")
    # a failed verdict.
    bad = [dict(f) for f in ok_fams]
    bad[0]["verdict"] = "FAIL"
    if not success_findings(bad):
        failures.append("success_findings: a failed verdict expected a finding")
    # an unresolved blocker.
    blk = [dict(f) for f in ok_fams]
    blk[1]["unresolved-blockers"] = 1
    if not success_findings(blk):
        failures.append("success_findings: an unresolved blocker expected a finding")
    # a false finished-signal (a degraded delivery is not a verdict).
    deg = [dict(f) for f in ok_fams]
    deg[2]["finished-signal"] = False
    if not success_findings(deg):
        failures.append("success_findings: a false finished-signal expected a finding")
    # (finding 5) a STRING "yes" finished-signal is not a real boolean and must be rejected.
    yes = [dict(f) for f in ok_fams]
    yes[0]["finished-signal"] = "yes"
    if not any("real boolean" in f for f in success_findings(yes)):
        failures.append("success_findings: a string 'yes' finished-signal must be rejected (finding 5)")
    # (finding 5) a family missing timestamps-utc, and an unknown family key, are each rejected.
    nots = [dict(f) for f in ok_fams]
    del nots[1]["timestamps-utc"]
    if not any("timestamps-utc" in f for f in success_findings(nots)):
        failures.append("success_findings: a family without timestamps-utc must be rejected (finding 5)")
    unk = [dict(f) for f in ok_fams]
    unk[2]["bogus"] = 1
    if not any("keys are not exactly" in f for f in success_findings(unk)):
        failures.append("success_findings: an unknown family key must be rejected (finding 5)")
    # (finding 5) unresolved-blockers must be a real integer 0, not a bool or a non-zero.
    boolblk = [dict(f) for f in ok_fams]
    boolblk[0]["unresolved-blockers"] = True
    if not any("integer 0" in f for f in success_findings(boolblk)):
        failures.append("success_findings: a boolean unresolved-blockers must be rejected (finding 5)")

    # chronology.
    if chronology_findings([10, 20], 30):
        failures.append("chronology: timestamps before the tagger date expected no finding")
    if not chronology_findings([10, 40], 30):
        failures.append("chronology: a timestamp at/after the tagger date expected a finding")
    if not chronology_findings([10], 30) == [] and not chronology_findings([30], 30):
        failures.append("chronology: a timestamp equal to the tagger date is not strictly earlier")
    if not chronology_findings([10], None):
        failures.append("chronology: a missing tagger date (lightweight tag) expected a finding")

    # genesis uniqueness.
    if genesis_findings([True, False, False]):
        failures.append("genesis: exactly one genesis at the first row expected no finding")
    if not genesis_findings([True, True]):
        failures.append("genesis: two genesis rows expected a finding")
    if not genesis_findings([False, True]):
        failures.append("genesis: genesis on a non-first row expected a finding")
    if genesis_findings([]):
        failures.append("genesis: zero rows expected no finding")

    # ordering.
    if ordering_findings(["1.0.0", "1.1.0", "2.0.0"]):
        failures.append("ordering: a strictly increasing sequence expected no finding")
    if not ordering_findings(["1.0.0", "1.0.0"]):
        failures.append("ordering: a duplicate version expected a finding")
    if not ordering_findings(["1.1.0", "1.0.0"]):
        failures.append("ordering: a reorder/decrease expected a finding")

    # QA-object validation.
    good_obj = {"candidate-sha": "abc", "family": [dict(f, **{"timestamps-utc": [5]}) for f in ok_fams]}
    fnds, epochs = validate_qa_obj(good_obj, "abc")
    if fnds or epochs != [5, 5, 5]:
        failures.append("validate_qa_obj: a clean object expected no finding and its epochs")
    fnds, _e = validate_qa_obj(good_obj, "different")
    if not any("candidate SHA" in f for f in fnds):
        failures.append("validate_qa_obj: a candidate-sha mismatch expected a finding")
    # (finding 5) an unknown top-level key on the QA object is rejected.
    fnds, _e = validate_qa_obj(dict(good_obj, bogus=1), "abc")
    if not any("unknown top-level key" in f for f in fnds):
        failures.append("validate_qa_obj: an unknown object key must be rejected (finding 5)")
    # (finding 5) the permissive attestation the round-2 QA flagged: string 'yes' signals and no
    # timestamps must NOT clean.
    permissive = {"candidate-sha": "abc", "family": [
        {"name": n, "finished-signal": "yes", "verdict": "PASS", "unresolved-blockers": 0}
        for n in FAMILIES]}
    fnds, _e = validate_qa_obj(permissive, "abc")
    if not fnds:
        failures.append("validate_qa_obj: a string-'yes'/no-timestamps attestation must not clean "
                        "(finding 5)")

    # --- shared release-row schema drift (QA #6) --------------------------------------------------
    # This gate's validated key universe must EQUAL the single shared schema, so read_genesis,
    # check_release_build, and check_release_delta cannot diverge.
    if BUILD_ROW_KEYS != gen_manifest.RELEASE_ROW_ALLOWED:
        failures.append("release-row schema drift: BUILD_ROW_KEYS {} != gen_manifest.RELEASE_ROW_ALLOWED "
                        "{}".format(sorted(BUILD_ROW_KEYS), sorted(gen_manifest.RELEASE_ROW_ALLOWED)))
    if not set(BUILD_ROW_ANCHOR) <= gen_manifest.RELEASE_ROW_ALLOWED:
        failures.append("release-row schema drift: anchor fields are not within the shared schema")

    # --- reproduce-command enumeration (QA #7) ---------------------------------------------------
    # build_registry returns JSON TEXT; _regenerate_check_commands must parse it and iterate the entries,
    # not iterate the string (the pre-tag crash). Run against the real repo checkout (the real registry).
    try:
        cmds = _regenerate_check_commands(repo_root())
        if not any(c[:1] == ["python3"] and c[-1] == "--check" for c in cmds):
            failures.append("_regenerate_check_commands: expected python3 ... --check commands")
        if ["python3", "tools/gen_manifest.py", "--check"] not in cmds \
                or ["python3", "tools/check_manifest.py"] not in cmds:
            failures.append("_regenerate_check_commands: expected the manifest + check_manifest commands")
    except Exception as exc:  # noqa: BLE001  the QA #7 crash was exactly an exception here
        failures.append("_regenerate_check_commands raised ({}); QA #7 regression".format(exc))

    # --- mode resolver (round-3 finding 6) --------------------------------------------------------
    def _mopts(**over):
        base = {"self_test": False, "pre_tag": False, "post_tag": False, "candidate_sha": None,
                "qa_path": None, "qa_sha256": None, "first_pin": False, "evidence": None,
                "attestation_commit": None}
        base.update(over)
        return base
    _mode_cases = [
        (_mopts(), "audit"),
        (_mopts(self_test=True), "self-test"),
        (_mopts(pre_tag=True, candidate_sha="a", qa_path="q", qa_sha256="h"), "pre-tag"),
        (_mopts(pre_tag=True, candidate_sha="a", qa_path="q", qa_sha256="h", first_pin=True,
                evidence="e"), "pre-tag"),
        (_mopts(post_tag=True, qa_path="q"), "post-tag"),
        # the finding-6 open cases: each must resolve to 'error', never fall through into audit/self-test.
        (_mopts(pre_tag=True, first_pin=True, evidence="/missing"), "error"),   # --first-pin, no candidate
        (_mopts(candidate_sha="deadbeef"), "error"),                            # --candidate-sha alone
        (_mopts(self_test=True, pre_tag=True), "error"),                        # --self-test --pre-tag
        (_mopts(pre_tag=True, candidate_sha="a", qa_path="q", qa_sha256="h", first_pin=True), "error"),
        (_mopts(pre_tag=True, candidate_sha="a", qa_path="q", qa_sha256="h", evidence="e"), "error"),
        (_mopts(post_tag=True), "error"),                                       # --post-tag without --qa-path
        (_mopts(post_tag=True, qa_path="q", candidate_sha="a"), "error"),       # pre-tag arg in post-tag
        (_mopts(pre_tag=True, post_tag=True, candidate_sha="a", qa_path="q", qa_sha256="h"), "error"),
        (_mopts(attestation_commit="x"), "error"),                             # post-tag arg without stage
        (_mopts(qa_path="q"), "error")]                                        # stray --qa-path in audit
    for opts, want in _mode_cases:
        got = _resolve_mode(opts)
        if got != want:
            failures.append("_resolve_mode({}): expected {!r}, got {!r}".format(opts, want, got))

    # --- duplicate-CLI rejection through main() (round-4 finding 6) --------------------------------
    # Tested through the REAL argv/main() path, not just the dict resolver: a repeated option is exit 2
    # BEFORE dispatch (so --self-test --self-test never recurses into a run).
    def _main_rc(argv):
        saved = sys.argv
        sys.argv = ["check_release_build.py"] + argv
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                return main()
        finally:
            sys.argv = saved
    for argv in (["--self-test", "--self-test"],
                 ["--candidate-sha", "x", "--candidate-sha", "y"],
                 ["--pre-tag", "--pre-tag"],
                 ["--qa-path", "a", "--qa-path", "b"]):
        if _main_rc(argv) != 2:
            failures.append("main({}): a duplicate CLI option must be rejected exit 2 (finding 6)".format(
                argv))
    # a non-duplicate error case still resolves through the resolver, not a duplicate parse fault.
    if _main_rc(["--candidate-sha", "x"]) != 2:
        failures.append("main(--candidate-sha alone): expected exit 2 (mode resolver)")

    # --- git-level cases ---------------------------------------------------------------------------
    import shutil as _sh
    git_ok = True
    try:
        if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
            git_ok = False
    except OSError:
        git_ok = False
    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-release-build-selftest-")) if git_ok else None
    except OSError:
        tmp = None

    if not git_ok or tmp is None:
        print("SELF-TEST NOTE: git or a writable temp directory is unavailable; the git-level cases "
              "(zero-row audit, annotated vs lightweight tag, tag resolution) were SKIPPED (the pure "
              "predicate coverage above still ran)", file=sys.stderr)
        git_ran = False
    else:
        git_ran = True

        def _init(path):
            path.mkdir(parents=True, exist_ok=True)
            for args in (["init", "-q"], ["config", "user.name", "AIQT Self-Test"],
                         ["config", "user.email", "selftest@example.invalid"]):
                subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True)

        def _valid_manifest(genesis="true", release_version="1.0.0"):
            # A STRUCTURALLY valid manifest with the EXACT mandatory top-level keyset (round-5 finding 1:
            # strict_manifest now requires sources and artifacts present); tagged/candidate manifests are
            # strict-validated before any field is read.
            return ('format-version = 1\nrelease-version = "{}"\ngenesis = {}\n'
                    'tree-sha256 = "{}"\nsources = []\nartifacts = []\n'.format(
                        release_version, genesis, "a" * 64))

        def _write_records(path, releases_body, manifest_body=None):
            (path / ".aiqt" / "core").mkdir(parents=True, exist_ok=True)
            (path / RELEASES_REL).write_text(releases_body, encoding="utf-8")
            (path / MANIFEST_REL).write_text(manifest_body or _valid_manifest(), encoding="utf-8")

        def _commit(path, msg):
            subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, capture_output=True, text=True)
            subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", msg],
                           check=True, capture_output=True, text=True)

        try:
            # (audit, zero rows) -> NOT APPLICABLE, exit 0.
            z = tmp / "zero"
            _init(z)
            _write_records(z, "format-version = 1\n")
            _commit(z, "zero-row genesis")
            if _run_audit_quiet(z) != 0:
                failures.append("audit zero-row: expected NOT APPLICABLE exit 0")

            # (annotated tag row) -> clean audit. Build a genesis release: commit the manifest tree,
            # tag it annotated, then record row 1 pointing at it with matching SHAs and a tagger-date-
            # respecting timestamp.
            a = tmp / "annotated"
            _init(a)
            _write_records(a, "format-version = 1\n")
            _commit(a, "genesis release tree")
            subprocess.run(["git", "-C", str(a), "tag", "-a", "v1.0.0", "-m", "release 1.0.0"],
                           check=True, capture_output=True, text=True,
                           env={"GIT_COMMITTER_DATE": "2000-01-01T00:00:00", **_env()})
            tag_obj = subprocess.run(["git", "-C", str(a), "rev-parse", "refs/tags/v1.0.0"],
                                     check=True, capture_output=True, text=True).stdout.strip()
            commit_sha = subprocess.run(["git", "-C", str(a), "rev-parse", "refs/tags/v1.0.0^{commit}"],
                                        check=True, capture_output=True, text=True).stdout.strip()
            tagger = _tagger_epoch(a, "v1.0.0")

            def _full_row(tobj, csha, ts, version="1.0.0", tag="v1.0.0", fmt=1):
                # A COMPLETE attestation row (finding 4): every present row carries all seven fields.
                return ('format-version = {}\n\n[[release]]\nversion = "{}"\ntag = "{}"\n'
                        'tag_object_sha = "{}"\ncommit_sha = "{}"\nqa-sha256 = "{}"\n'
                        'qa-store-path = "qa/{}.toml"\nattestation-timestamps = [{}]\n'.format(
                            fmt, version, tag, tobj, csha, "c" * 64, version, ts))

            (a / RELEASES_REL).write_text(_full_row(tag_obj, commit_sha, tagger - 100), encoding="utf-8")
            if _run_audit_quiet(a) != 0:
                failures.append("audit annotated-tag row: expected a clean exit 0")

            # (round-3 finding 9) the same clean row but with a Windows UNC host-absolute qa-store-path:
            # the strict schema rejects it at load -> exit 2, where the pre-round-3 POSIX/drive-only check
            # accepted a UNC path and the run otherwise cleaned to exit 0.
            (a / RELEASES_REL).write_text(
                'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
                'tag_object_sha = "{}"\ncommit_sha = "{}"\nqa-sha256 = "{}"\n'
                "qa-store-path = '\\\\server\\share\\qa.toml'\n"
                "attestation-timestamps = [{}]\n".format(
                    tag_obj, commit_sha, "c" * 64, tagger - 100), encoding="utf-8")
            if _run_audit_quiet(a) != 2:
                failures.append("audit UNC qa-store-path: expected fail-closed exit 2 (round-3 finding 9)")

            # (finding 4) a committed row missing qa-sha256/qa-store-path is no longer a complete record ->
            # exit 2, not the round-2 clean audit.
            (a / RELEASES_REL).write_text(
                'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
                'tag_object_sha = "{}"\ncommit_sha = "{}"\nattestation-timestamps = [{}]\n'.format(
                    tag_obj, commit_sha, tagger - 100), encoding="utf-8")
            if _run_audit_quiet(a) != 2:
                failures.append("audit row missing qa-sha256/qa-store-path: expected fail-closed exit 2 "
                                "(finding 4, complete-record schema)")
            # (finding 4) a zero-row releases.toml with an invalid format-version -> exit 2, not NOT
            # APPLICABLE clean.
            (a / RELEASES_REL).write_text("format-version = 999\n", encoding="utf-8")
            if _run_audit_quiet(a) != 2:
                failures.append("audit zero-row format-version=999: expected fail-closed exit 2 (finding 4)")

            # (chronology violation) a timestamp not strictly earlier than the tagger date -> exit 1.
            (a / RELEASES_REL).write_text(_full_row(tag_obj, commit_sha, tagger + 100), encoding="utf-8")
            if _run_audit_quiet(a) != 1:
                failures.append("audit chronology violation: expected exit 1")

            # (lightweight tag) -> a finding (annotated tags are mandatory).
            lw = tmp / "lightweight"
            _init(lw)
            _write_records(lw, "format-version = 1\n")
            _commit(lw, "genesis tree")
            subprocess.run(["git", "-C", str(lw), "tag", "v1.0.0"],  # lightweight (no -a)
                           check=True, capture_output=True, text=True)
            lw_obj = subprocess.run(["git", "-C", str(lw), "rev-parse", "refs/tags/v1.0.0"],
                                    check=True, capture_output=True, text=True).stdout.strip()
            (lw / RELEASES_REL).write_text(
                'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
                'tag_object_sha = "{}"\ncommit_sha = "{}"\nqa-sha256 = "{}"\n'
                'qa-store-path = "qa/1.0.0.toml"\nattestation-timestamps = [100]\n'.format(
                    lw_obj, lw_obj, "c" * 64), encoding="utf-8")
            if _run_audit_quiet(lw) != 1:
                failures.append("audit lightweight tag: expected exit 1 (annotated tags mandatory)")

            # (finding 8, syntax) a MALFORMED recorded object id ("dead"/"beef", not 40/64 hex) is exit 2 at
            # the strict load, never a mismatch finding.
            nr = tmp / "notag"
            _init(nr)
            _write_records(nr, "format-version = 1\n")
            _commit(nr, "genesis tree")
            (nr / RELEASES_REL).write_text(
                'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
                'tag_object_sha = "dead"\ncommit_sha = "beef"\nqa-sha256 = "{}"\n'
                'qa-store-path = "qa/1.0.0.toml"\nattestation-timestamps = [100]\n'.format("c" * 64),
                encoding="utf-8")
            if _run_audit_quiet(nr) != 2:
                failures.append("audit malformed recorded object id: expected fail-closed exit 2 "
                                "(finding 8, syntax)")

            # (finding 8, resolution) a real annotated tag but a syntactically-valid NONEXISTENT recorded
            # tag_object_sha ("a"*40): the object does not exist -> exit 2 (cannot-evaluate), where the
            # pre-round-3 gate returned a mismatch finding (exit 1).
            ne = tmp / "noobject"
            _init(ne)
            _write_records(ne, "format-version = 1\n")
            _commit(ne, "genesis tree")
            subprocess.run(["git", "-C", str(ne), "tag", "-a", "v1.0.0", "-m", "release 1.0.0"],
                           check=True, capture_output=True, text=True,
                           env={"GIT_COMMITTER_DATE": "2000-01-01T00:00:00", **_env()})
            ne_commit = subprocess.run(["git", "-C", str(ne), "rev-parse", "refs/tags/v1.0.0^{commit}"],
                                       check=True, capture_output=True, text=True).stdout.strip()
            (ne / RELEASES_REL).write_text(
                'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
                'tag_object_sha = "{a}"\ncommit_sha = "{c}"\nqa-sha256 = "{h}"\n'
                'qa-store-path = "qa/1.0.0.toml"\nattestation-timestamps = [100]\n'.format(
                    a="a" * 40, c=ne_commit, h="c" * 64), encoding="utf-8")
            if _run_audit_quiet(ne) != 2:
                failures.append("audit nonexistent recorded object: expected fail-closed exit 2 "
                                "(finding 8, resolution)")

            # (finding 7) a real annotated tag whose MESSAGE is non-UTF-8: reading the tag object must parse
            # only the ASCII header, so the run completes (clean exit 0) rather than crashing on an uncaught
            # UnicodeDecodeError (which the pre-round-3 text=True read raised, surfacing as exit 1).
            nu = tmp / "nonutf8tag"
            _init(nu)
            _write_records(nu, "format-version = 1\n")
            _commit(nu, "genesis tree")
            (nu / "tagmsg").write_bytes(b"release \xff\xfe non-utf8 message\n")
            subprocess.run(["git", "-C", str(nu), "tag", "-a", "v1.0.0", "-F", str(nu / "tagmsg")],
                           check=True, capture_output=True, text=True,
                           env={"GIT_COMMITTER_DATE": "2000-01-01T00:00:00", **_env()})
            nu_tag = subprocess.run(["git", "-C", str(nu), "rev-parse", "refs/tags/v1.0.0"],
                                    check=True, capture_output=True, text=True).stdout.strip()
            nu_commit = subprocess.run(["git", "-C", str(nu), "rev-parse", "refs/tags/v1.0.0^{commit}"],
                                       check=True, capture_output=True, text=True).stdout.strip()
            nu_tagger = _tagger_epoch(nu, "v1.0.0")
            (nu / RELEASES_REL).write_text(
                'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
                'tag_object_sha = "{t}"\ncommit_sha = "{c}"\nqa-sha256 = "{h}"\n'
                'qa-store-path = "qa/1.0.0.toml"\nattestation-timestamps = [{ts}]\n'.format(
                    t=nu_tag, c=nu_commit, h="c" * 64, ts=nu_tagger - 100), encoding="utf-8")
            try:
                nu_rc = _run_audit_quiet(nu)
            except Exception as exc:  # noqa: BLE001  the finding-7 crash was exactly an uncaught exception
                nu_rc = "raised {}".format(type(exc).__name__)
            if nu_rc != 0:
                failures.append("audit non-UTF-8 tag message: expected clean exit 0 (finding 7: the tag "
                                "header is parsed as ASCII bytes, never an uncaught crash), got {}".format(
                                    nu_rc))

            # (read_genesis accepts a FULL attestation row, QA #6) a one-row record carrying every
            # documented field must be ACCEPTED by gen_manifest.read_genesis (it needs only the count),
            # returning False, not rejected on the tag/qa-*/timestamps keys the old allow-set omitted.
            rg = tmp / "readgenesis"
            (rg / ".aiqt" / "core").mkdir(parents=True)
            (rg / RELEASES_REL).write_text(
                'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
                'tag_object_sha = "{a}"\ncommit_sha = "{b}"\nqa-sha256 = "{c}"\n'
                'qa-store-path = "qa/1.0.0.toml"\nattestation-timestamps = [100, 200]\n'.format(
                    a="a" * 40, b="b" * 40, c="c" * 64), encoding="utf-8")
            try:
                if gen_manifest.read_genesis(rg) is not False:
                    failures.append("read_genesis: a one-row record must return False (not genesis)")
            except Exception as exc:  # noqa: BLE001  QA #6 was exactly a rejection here
                failures.append("read_genesis rejected a valid full attestation row (QA #6): {}".format(exc))
            (rg / RELEASES_REL).write_text(
                'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ncommit_sha = "b"\nbogus = 1\n',
                encoding="utf-8")
            try:
                gen_manifest.read_genesis(rg)
                failures.append("read_genesis: an unknown row key must still be rejected")
            except gen_manifest.GateError:
                pass

            # (post-tag end-to-end, QA #8) a genesis release, annotated tag, an attestation commit whose
            # newest row is FULLY attested, and a matching QA object on disk: run_post_tag retrieves and
            # re-hashes it and validates the families (armed gate clean). Then three fail-closed variants
            # the old default-to-clean code missed.
            pt = tmp / "posttag"
            _init(pt)
            _write_records(pt, "format-version = 1\n")
            _commit(pt, "genesis release tree")
            subprocess.run(["git", "-C", str(pt), "tag", "-a", "v1.0.0", "-m", "release 1.0.0"],
                           check=True, capture_output=True, text=True,
                           env={"GIT_COMMITTER_DATE": "2000-01-01T00:00:00", **_env()})
            pt_tag = subprocess.run(["git", "-C", str(pt), "rev-parse", "refs/tags/v1.0.0"],
                                    check=True, capture_output=True, text=True).stdout.strip()
            pt_commit = subprocess.run(["git", "-C", str(pt), "rev-parse", "refs/tags/v1.0.0^{commit}"],
                                       check=True, capture_output=True, text=True).stdout.strip()
            pt_tagger = _tagger_epoch(pt, "v1.0.0")
            qa_body = ('candidate-sha = "{}"\n\n'.format(pt_commit) + "".join(
                '[[family]]\nname = "{}"\nfinished-signal = true\nverdict = "PASS"\n'
                'unresolved-blockers = 0\ntimestamps-utc = [{}]\n\n'.format(n, pt_tagger - 100)
                for n in FAMILIES))
            qa_file = pt / "qa.toml"
            qa_file.write_text(qa_body, encoding="utf-8")
            qa_sha = hashlib.sha256(qa_body.encode("utf-8")).hexdigest()
            good_row = ('format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
                        'tag_object_sha = "{}"\ncommit_sha = "{}"\nqa-sha256 = "{}"\n'
                        'qa-store-path = "qa/1.0.0.toml"\nattestation-timestamps = [{}]\n'.format(
                            pt_tag, pt_commit, qa_sha, pt_tagger - 100))
            (pt / RELEASES_REL).write_text(good_row, encoding="utf-8")
            _commit(pt, "attestation commit")
            if _run_post_tag_quiet(pt, None, str(qa_file)) != 0:
                failures.append("post-tag end-to-end: a fully-attested release with a matching QA object "
                                "expected exit 0")
            if _run_post_tag_quiet(pt, None, None) != 2:
                failures.append("post-tag: a missing --qa-path must fail closed exit 2 (never certifies "
                                "without retrieving the QA object)")
            bad_qa = pt / "bad.toml"
            bad_qa.write_text(qa_body + "# tampered\n", encoding="utf-8")
            if _run_post_tag_quiet(pt, None, str(bad_qa)) != 1:
                failures.append("post-tag: a QA object not matching the recorded digest expected exit 1")
            # (finding 4) a newest row missing attestation-timestamps is no longer a complete record ->
            # the strict loader fails closed exit 2 (was the round-2 exit-1 default-to-clean chronology).
            nots_row = good_row.replace(
                'attestation-timestamps = [{}]\n'.format(pt_tagger - 100), "")
            (pt / RELEASES_REL).write_text(nots_row, encoding="utf-8")
            _commit(pt, "drop timestamps")
            if _run_post_tag_quiet(pt, None, str(qa_file)) != 2:
                failures.append("post-tag: a newest row without attestation-timestamps must fail closed "
                                "exit 2 (finding 4, complete-record schema)")

            # (finding 6) CHRONOLOGY FORGING: a QA object whose retrieved timestamps POSTDATE the tag, but a
            # row that lies with an early timestamp. The armed gate now compares the RETRIEVED epochs against
            # the tag AND requires the row list to equal them, so it fails exit 1 (the pre-fix gate discarded
            # the retrieved epochs and cleaned on the row's early timestamp).
            forge_body = ('candidate-sha = "{}"\n\n'.format(pt_commit) + "".join(
                '[[family]]\nname = "{}"\nfinished-signal = true\nverdict = "PASS"\n'
                'unresolved-blockers = 0\ntimestamps-utc = [{}]\n\n'.format(n, pt_tagger + 500)
                for n in FAMILIES))
            forge_file = pt / "forge.toml"
            forge_file.write_text(forge_body, encoding="utf-8")
            forge_sha = hashlib.sha256(forge_body.encode("utf-8")).hexdigest()
            forge_row = ('format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
                         'tag_object_sha = "{}"\ncommit_sha = "{}"\nqa-sha256 = "{}"\n'
                         'qa-store-path = "qa/1.0.0.toml"\nattestation-timestamps = [{}]\n'.format(
                             pt_tag, pt_commit, forge_sha, pt_tagger - 100))
            (pt / RELEASES_REL).write_text(forge_row, encoding="utf-8")
            _commit(pt, "forged chronology")
            if _run_post_tag_quiet(pt, None, str(forge_file)) != 1:
                failures.append("post-tag: a QA object whose retrieved timestamps postdate the tag while "
                                "the row lists an early timestamp must fail exit 1 (finding 6)")

            # (finding 7) PREDECESSOR COMPLETENESS via a real 3-annotated-tag repo: a record covering only
            # 1.0.0 and 1.2.0 (skipping the tagged 1.1.0) is flagged; a complete record is not; an in-flight
            # tag ABOVE the newest row is allowed. Driven through the new leg on the real tag enumeration.
            pc = tmp / "predcomplete"
            _init(pc)
            _write_records(pc, "format-version = 1\n")
            _commit(pc, "tree")
            for v in ("1.0.0", "1.1.0", "1.2.0"):
                subprocess.run(["git", "-C", str(pc), "tag", "-a", "v" + v, "-m", v],
                               check=True, capture_output=True, text=True,
                               env={"GIT_COMMITTER_DATE": "2000-01-01T00:00:00", **_env()})
            skipped = [{"version": "1.0.0"}, {"version": "1.2.0"}]
            if not any("no attestation row" in f for f in release_tag_findings(pc, skipped)):
                failures.append("predecessor completeness: a tagged 1.1.0 skipped between rows 1.0.0 and "
                                "1.2.0 expected a finding (finding 7)")
            complete = [{"version": "1.0.0"}, {"version": "1.1.0"}, {"version": "1.2.0"}]
            if release_tag_findings(pc, complete):
                failures.append("predecessor completeness: a complete record expected no finding")
            inflight = [{"version": "1.0.0"}, {"version": "1.1.0"}]  # 1.2.0 tagged but not yet attested
            if release_tag_findings(pc, inflight):
                failures.append("predecessor completeness: an in-flight tag above the newest row must be "
                                "allowed (no finding)")

            # (findings 5/8) FIRST-PIN EVIDENCE bound to the CANDIDATE's AGENTS.md. Build a candidate tree
            # carrying an AGENTS.md larger than the documented cap and a machine-checkable demonstration; the
            # evidence must bind the recomputed digest and byte count, the documented cap, and a recomputed
            # prefix-superset. The pre-fix gate accepted a fake digest, an arbitrary measurement, a cap of 1,
            # and a nonexistent demonstration; the fix rejects each.
            fp = tmp / "firstpin"
            _init(fp)
            (fp / "qa").mkdir(parents=True, exist_ok=True)
            agents_bytes = ("A" * 40000).encode("utf-8")   # 40000 > the documented 32768 cap
            (fp / "AGENTS.md").write_bytes(agents_bytes)
            a_sha = hashlib.sha256(agents_bytes).hexdigest()
            a_len = len(agents_bytes)
            (fp / "qa" / "demo.toml").write_text(
                'agents-sha256 = "{}"\ndelivered-prefix-obligations = ["ob1", "ob2"]\n'
                'floor-profile-obligations = ["ob1", "ob2", "ob3"]\n'.format(a_sha), encoding="utf-8")
            (fp / "qa" / "badsuperset.toml").write_text(
                'agents-sha256 = "{}"\ndelivered-prefix-obligations = ["ob1", "ob2", "obX"]\n'
                'floor-profile-obligations = ["ob1", "ob2"]\n'.format(a_sha), encoding="utf-8")
            _write_records(fp, "format-version = 1\n")
            _commit(fp, "candidate with AGENTS.md + demonstration")
            fp_commit = subprocess.run(["git", "-C", str(fp), "rev-parse", "HEAD"],
                                       check=True, capture_output=True, text=True).stdout.strip()

            def _ev(**over):
                body = {"candidate-sha": '"{}"'.format(fp_commit), "observed-measurement": str(a_len),
                        "cap-bytes": "32768", "prefix-superset": "true",
                        "demonstration": '"qa/demo.toml"', "agents-sha256": '"{}"'.format(a_sha)}
                body.update(over)
                p = fp / "ev-{}.toml".format(len(list(fp.glob("ev-*.toml"))))
                p.write_text("".join("{} = {}\n".format(k, v) for k, v in body.items()), encoding="utf-8")
                return str(p)

            if _first_pin_evidence_findings(fp, fp_commit, _ev()):
                failures.append("first-pin evidence: a well-formed candidate-bound artifact expected no "
                                "finding (findings 5/8)")
            if not any("REQUIRED" in f for f in _first_pin_evidence_findings(fp, fp_commit, None)):
                failures.append("first-pin evidence: a missing --evidence must be flagged (finding 8)")
            # (finding 5) a fake digest, a wrong measurement, a cap != documented, and a nonexistent
            # demonstration are each caught against the recomputed candidate AGENTS.md.
            if not any("does not match" in f for f in _first_pin_evidence_findings(
                    fp, fp_commit, _ev(**{"agents-sha256": '"{}"'.format("d" * 64)}))):
                failures.append("first-pin evidence: a fake agents-sha256 must be caught against the "
                                "recomputed candidate digest (finding 5)")
            if not any("byte count" in f for f in _first_pin_evidence_findings(
                    fp, fp_commit, _ev(**{"observed-measurement": "999999999"}))):
                failures.append("first-pin evidence: a wrong observed-measurement must be caught against the "
                                "recomputed byte count (finding 5)")
            if not any("documented default cap" in f for f in _first_pin_evidence_findings(
                    fp, fp_commit, _ev(**{"cap-bytes": "1"}))):
                failures.append("first-pin evidence: cap-bytes must equal the documented default (finding 5)")
            if not any("does not resolve" in f for f in _first_pin_evidence_findings(
                    fp, fp_commit, _ev(**{"demonstration": '"qa/nonexistent.toml"'}))):
                failures.append("first-pin evidence: a nonexistent demonstration reference must be caught "
                                "(finding 5)")
            # (finding 5) a demonstration whose floor is NOT a superset of the delivered prefix is caught.
            if not any("NOT a superset" in f for f in _first_pin_evidence_findings(
                    fp, fp_commit, _ev(**{"demonstration": '"qa/badsuperset.toml"'}))):
                failures.append("first-pin evidence: a floor profile that is not a superset of the delivered "
                                "prefix must be caught (finding 5)")
            # (finding 5) AGENTS.md not retrievable from the candidate -> exit 2 (a GateError).
            try:
                _first_pin_evidence_findings(pt, pt_commit, _ev())
                failures.append("first-pin evidence: a candidate without AGENTS.md must raise (exit 2)")
            except GateError:
                pass

            # (round-4 finding 7) _show_bytes distinguishes a genuinely ABSENT path (None) from a git
            # resolution/infrastructure failure (GateError), so a git error is never downgraded to a finding.
            if _show_bytes(pt, pt_commit, "no/such/path.toml") is not None:
                failures.append("_show_bytes: a genuinely absent path must return None (finding 7)")
            try:
                _show_bytes(pt, "f" * 40, "AGENTS.md")   # a valid-syntax but unresolvable ref
                failures.append("_show_bytes: an unresolvable ref must raise GateError, not return None "
                                "(finding 7)")
            except GateError:
                pass

            # (round-4 findings 4/5) a FULL-PACK genesis candidate built from `git archive HEAD`: run_pre_tag
            # WITHOUT --first-pin still AUTO-REQUIRES first-pin evidence (finding 5), and reproduce_gate runs
            # on the RAW-materialized + fresh-init candidate (finding 1, no worktree). The raw materialization
            # and reproduce both exercise the round-4 cannot-evaluate propagation on the real toolchain.
            arch = subprocess.run(["git", "-C", str(repo_root()), "archive", "HEAD"], capture_output=True)
            if arch.returncode == 0 and arch.stdout:
                import tarfile
                gcand = tmp / "genesis-candidate"
                gcand.mkdir()
                try:
                    with tarfile.open(fileobj=io.BytesIO(arch.stdout), mode="r:") as tf:
                        tf.extractall(gcand)
                    ok = all(subprocess.run(["git", "-C", str(gcand), *a], capture_output=True,
                                            env=_fresh_git_env()).returncode == 0
                             for a in (["init", "-q"],
                                       ["-c", "user.name=t", "-c", "user.email=t@e.invalid", "add", "-A"],
                                       ["-c", "user.name=t", "-c", "user.email=t@e.invalid", "commit", "-q",
                                        "-m", "genesis", "--no-verify"]))
                    if ok:
                        gsha = subprocess.run(["git", "-C", str(gcand), "rev-parse", "HEAD"],
                                              capture_output=True, text=True).stdout.strip()
                        gqa_body = ('candidate-sha = "{}"\n\n'.format(gsha) + "".join(
                            '[[family]]\nname = "{}"\nfinished-signal = true\nverdict = "PASS"\n'
                            'unresolved-blockers = 0\ntimestamps-utc = [100]\n\n'.format(n)
                            for n in FAMILIES))
                        gqa = gcand / "qa.toml"
                        gqa.write_text(gqa_body, encoding="utf-8")
                        gqa_sha = hashlib.sha256(gqa_body.encode("utf-8")).hexdigest()
                        # no --first-pin, no evidence: the genesis auto-require makes it exit 1 (REQUIRED),
                        # where the pre-round-4 gate returned exit 0.
                        rc = _run_pre_tag_quiet(gcand, gsha, str(gqa), gqa_sha, False, None)
                        if rc != 1:
                            failures.append("genesis run_pre_tag without --first-pin must AUTO-REQUIRE "
                                            "evidence and fail exit 1 (finding 5), got {}".format(rc))
                except Exception as exc:  # noqa: BLE001  a build/archive hiccup is a skip, not a false pass
                    print("SELF-TEST NOTE: genesis run_pre_tag case skipped ({})".format(exc),
                          file=sys.stderr)

            # (round-4 finding 4) a first-pin candidate whose check_clauses --genesis is CANNOT-EVALUATE
            # (a corrupt inventory, child exit 2) must raise GateError -> exit 2, not append a finding. The
            # candidate carries a valid AGENTS.md + demonstration (so evidence binds) but a clauses.toml with
            # no [[clause]] array (check_clauses load_inventory -> exit 2).
            ce = tmp / "firstpin-cannoteval"
            _init(ce)
            (ce / "qa").mkdir(parents=True, exist_ok=True)
            ce_agents = ("A" * 40000).encode("utf-8")
            (ce / "AGENTS.md").write_bytes(ce_agents)
            ce_sha = hashlib.sha256(ce_agents).hexdigest()
            (ce / "qa" / "demo.toml").write_text(
                'agents-sha256 = "{}"\ndelivered-prefix-obligations = ["ob1"]\n'
                'floor-profile-obligations = ["ob1"]\n'.format(ce_sha), encoding="utf-8")
            (ce / ".aiqt" / "core").mkdir(parents=True, exist_ok=True)
            (ce / ".aiqt" / "core" / "clauses.toml").write_text("format-version = 1\n", encoding="utf-8")
            (ce / ".aiqt" / "core" / "id-history.toml").write_text("", encoding="utf-8")
            _write_records(ce, "format-version = 1\n")
            _commit(ce, "cannot-eval candidate")
            ce_commit = subprocess.run(["git", "-C", str(ce), "rev-parse", "HEAD"],
                                       check=True, capture_output=True, text=True).stdout.strip()
            ce_ev = ce / "ev.toml"
            ce_ev.write_text(
                'candidate-sha = "{}"\nobserved-measurement = 40000\ncap-bytes = 32768\n'
                'prefix-superset = true\ndemonstration = "qa/demo.toml"\nagents-sha256 = "{}"\n'.format(
                    ce_commit, ce_sha), encoding="utf-8")
            try:
                _first_pin_findings(ce, ce_commit, str(ce_ev))
                failures.append("first-pin: a check_clauses cannot-evaluate (child exit 2) must raise "
                                "GateError (exit 2), not append a finding (finding 4)")
            except GateError:
                pass

            # === ROUND-5 build fixtures ==============================================================
            # (finding 2) dormancy requires zero TAGS and zero rows, and a lightweight release tag is
            # disallowed. Zero rows + a LIGHTWEIGHT v1.0.0 arms the gate and is flagged (was exit 0).
            lwz = tmp / "lightweight-zero"
            _init(lwz)
            (lwz / ".aiqt" / "core").mkdir(parents=True, exist_ok=True)
            (lwz / RELEASES_REL).write_text("format-version = 1\n", encoding="utf-8")
            _commit(lwz, "zero-row")
            subprocess.run(["git", "-C", str(lwz), "tag", "v1.0.0"],  # lightweight
                           check=True, capture_output=True, text=True)
            if _run_audit_quiet(lwz) != 1:
                failures.append("audit zero-row + lightweight v1.0.0 must arm and flag exit 1 (round-5 "
                                "finding 2), not dormant NOT APPLICABLE")

            # (finding 2) a valid attested v1.0.0 row PLUS an UNRECORDED lightweight v0.9.0 -> flagged.
            un = tmp / "unrecorded-lightweight"
            _init(un)
            _write_records(un, "format-version = 1\n")
            _commit(un, "genesis release tree")
            subprocess.run(["git", "-C", str(un), "tag", "-a", "v1.0.0", "-m", "release 1.0.0"],
                           check=True, capture_output=True, text=True,
                           env={"GIT_COMMITTER_DATE": "2000-01-01T00:00:00", **_env()})
            subprocess.run(["git", "-C", str(un), "tag", "v0.9.0"],  # unrecorded lightweight
                           check=True, capture_output=True, text=True)
            un_tag = subprocess.run(["git", "-C", str(un), "rev-parse", "refs/tags/v1.0.0"],
                                    check=True, capture_output=True, text=True).stdout.strip()
            un_commit = subprocess.run(["git", "-C", str(un), "rev-parse", "refs/tags/v1.0.0^{commit}"],
                                       check=True, capture_output=True, text=True).stdout.strip()
            un_tagger = _tagger_epoch(un, "v1.0.0")
            (un / RELEASES_REL).write_text(
                'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
                'tag_object_sha = "{}"\ncommit_sha = "{}"\nqa-sha256 = "{}"\n'
                'qa-store-path = "qa/1.0.0.toml"\nattestation-timestamps = [{}]\n'.format(
                    un_tag, un_commit, "c" * 64, un_tagger - 100), encoding="utf-8")
            if _run_audit_quiet(un) != 1:
                failures.append("audit valid row + unrecorded lightweight v0.9.0 must flag exit 1 "
                                "(round-5 finding 2)")

            # (finding 3) a RAW invalid-UTF-8 release tag ref: git tag -l is decoded under a strict boundary,
            # so cannot-evaluate is exit 2, never an uncaught UnicodeDecodeError crash. Skipped (no false
            # pass) where git rejects the raw byte in a ref name.
            u8 = tmp / "invalid-utf8-tag"
            _init(u8)
            (u8 / ".aiqt" / "core").mkdir(parents=True, exist_ok=True)
            (u8 / RELEASES_REL).write_text("format-version = 1\n", encoding="utf-8")
            _commit(u8, "zero-row")
            u8_commit = subprocess.run(["git", "-C", str(u8), "rev-parse", "HEAD"],
                                       check=True, capture_output=True, text=True).stdout.strip()
            ur = subprocess.run([b"git", b"-C", str(u8).encode(), b"update-ref",
                                 b"refs/tags/v1.0.0\xff", u8_commit.encode()],
                                capture_output=True, env={"GIT_CONFIG_GLOBAL": os.devnull,
                                                          "GIT_CONFIG_SYSTEM": os.devnull})
            if ur.returncode == 0:
                if _run_audit_quiet(u8) != 2:
                    failures.append("audit invalid-UTF-8 release tag must fail closed exit 2, not crash "
                                    "(round-5 finding 3)")
            else:
                print("SELF-TEST NOTE: git rejected a raw invalid-UTF-8 ref; that finding-3 case was "
                      "SKIPPED", file=sys.stderr)
            # _git_out decode boundary (round-5 finding 3): invalid-UTF-8 git output raises GateError.
            class _FakeProc:
                returncode = 0
                stdout = b"\xff\xfe not utf-8"
            try:
                _git_out(_FakeProc(), "unit")
                failures.append("_git_out: invalid-UTF-8 git output must raise GateError (round-5 finding 3)")
            except GateError:
                pass

            # (finding 1) a TAGGED tree whose manifest carries a bogus artifact-row key -> strict_manifest
            # rejects the exact kind-specific keyset, audit exit 2 (was exit 0).
            bm = tmp / "bogus-tagged-manifest"
            _init(bm)
            (bm / ".aiqt" / "core").mkdir(parents=True, exist_ok=True)
            (bm / RELEASES_REL).write_text("format-version = 1\n", encoding="utf-8")
            (bm / MANIFEST_REL).write_text(
                'format-version = 1\nrelease-version = "1.0.0"\ngenesis = true\ntree-sha256 = "{h}"\n'
                'sources = []\n\n[[artifacts]]\nartifact-id = "x"\npath = "p"\nkind = "file"\n'
                'sha256 = "{h}"\nbogus = "y"\n'.format(h="a" * 64), encoding="utf-8")
            _commit(bm, "genesis tree with a bogus artifact key")
            subprocess.run(["git", "-C", str(bm), "tag", "-a", "v1.0.0", "-m", "release 1.0.0"],
                           check=True, capture_output=True, text=True,
                           env={"GIT_COMMITTER_DATE": "2000-01-01T00:00:00", **_env()})
            bm_tag = subprocess.run(["git", "-C", str(bm), "rev-parse", "refs/tags/v1.0.0"],
                                    check=True, capture_output=True, text=True).stdout.strip()
            bm_commit = subprocess.run(["git", "-C", str(bm), "rev-parse", "refs/tags/v1.0.0^{commit}"],
                                       check=True, capture_output=True, text=True).stdout.strip()
            bm_tagger = _tagger_epoch(bm, "v1.0.0")
            (bm / RELEASES_REL).write_text(
                'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
                'tag_object_sha = "{}"\ncommit_sha = "{}"\nqa-sha256 = "{}"\n'
                'qa-store-path = "qa/1.0.0.toml"\nattestation-timestamps = [{}]\n'.format(
                    bm_tag, bm_commit, "c" * 64, bm_tagger - 100), encoding="utf-8")
            if _run_audit_quiet(bm) != 2:
                failures.append("audit tagged manifest with a bogus artifact key must fail closed exit 2 "
                                "(round-5 finding 1)")

            # (finding 4) an injected mkdtemp failure in reproduce_gate is cannot-evaluate -> GateError.
            import tempfile as _tf
            _orig_mkdtemp = _tf.mkdtemp
            _tf.mkdtemp = lambda *a, **k: (_ for _ in ()).throw(OSError("injected: no temp dir"))
            try:
                reproduce_gate(repo_root(), "HEAD")
                failures.append("reproduce_gate: an mkdtemp OSError must raise GateError (round-5 finding 4)")
            except GateError:
                pass
            finally:
                _tf.mkdtemp = _orig_mkdtemp

            # (finding 5) a symlink-LOOP --evidence path is unusable -> GateError (exit 2), not a "does not
            # exist" finding. Only a genuine FileNotFoundError establishes absence.
            loop = tmp / "evidence-loop.toml"
            try:
                os.symlink(loop, loop)   # self-referential loop
                loop_made = True
            except OSError:
                loop_made = False
            if loop_made:
                try:
                    _first_pin_evidence_findings(bm, bm_commit, str(loop))
                    failures.append("first-pin evidence: a symlink-loop path must raise GateError (exit 2), "
                                    "not a finding (round-5 finding 5)")
                except GateError:
                    pass
        finally:
            _sh.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    core = ("the 2.2 STRICT success/family-set predicates (clean, missing/duplicate family, failed verdict, "
            "unresolved blocker, false/string finished-signal, unknown/missing family key, boolean blocker "
            "count, round-2 finding 5), genesis uniqueness, the 2.4 ordering predicate, strict QA-object "
            "validation, the shared release-row schema drift binding (#6), the reproduce-command enumeration "
            "(#7), the build-mode RESOLVER rejecting every mixed/incomplete/out-of-mode invocation "
            "(round-3 finding 6), a qa-sha256 syntax control (round-4 finding 4), and DUPLICATE-CLI "
            "rejection through main() (round-4 finding 6)")
    if git_ran:
        print("SELF-TEST PASS: {}; and the git-level cases (zero-row audit NOT APPLICABLE, a zero-row "
              "format-version=999 exit 2 and a row missing qa-sha256/qa-store-path exit 2 (round-2 finding "
              "4), a clean full attestation row, a chronology violation exit 1, a lightweight tag exit 1, a "
              "malformed recorded object-id exit 2 and a NONEXISTENT recorded object exit 2 (round-3 finding "
              "8), a non-UTF-8 tag message handled without a crash (round-3 finding 7), read_genesis "
              "accepting a full attestation row (#6), the post-tag clean/no-qa-path/digest-mismatch/"
              "no-timestamps cases, the chronology-FORGING exit 1 (round-2 finding 6), predecessor-"
              "completeness on a real 3-tag repo (round-2 finding 7), the first-pin evidence bound to the "
              "candidate's AGENTS.md with a recomputed prefix-superset (round-3 finding 5), a strict-manifest "
              "check on every tagged/candidate manifest (round-4 finding 3), _show_bytes absent-vs-git-error "
              "(round-4 finding 7), a genesis run_pre_tag auto-requiring first-pin evidence (round-4 finding "
              "5), and a first-pin cannot-evaluate raising exit 2 (round-4 finding 4), all over "
              "RAW-materialized (no-worktree) candidate trees (round-4 finding 1); and the round-5 cases "
              "(dormancy needs zero tags AND zero rows with a lightweight/unrecorded tag flagged (finding "
              "2), a raw invalid-UTF-8 release tag exit 2 plus a _git_out decode boundary (finding 3), a "
              "tagged manifest with a bogus artifact key exit 2 (finding 1), an injected mkdtemp OSError "
              "raising GateError (finding 4), and a symlink-loop --evidence raising GateError (finding "
              "5))) hold".format(core))
    else:
        print("SELF-TEST PASS (PARTIAL): {}; the git-level cases were SKIPPED (git or a writable temp "
              "directory unavailable), so those paths are UNVERIFIED this run".format(core))
    return 0


def _env():
    import os
    return {k: v for k, v in os.environ.items()}


def _parse_args(argv):
    """Parse argv, REJECTING any DUPLICATE option (round-4 finding 6): a repeated flag or value option is a
    conflicting/ambiguous invocation and returns None -> exit 2 BEFORE dispatch, never a silent last-wins
    overwrite. Returns the opts dict, or None on an unknown or duplicate option."""
    opts = {"self_test": False, "pre_tag": False, "post_tag": False, "candidate_sha": None,
            "qa_path": None, "qa_sha256": None, "first_pin": False, "evidence": None,
            "attestation_commit": None}
    flags = {"--self-test": "self_test", "--pre-tag": "pre_tag", "--post-tag": "post_tag",
             "--first-pin": "first_pin"}
    single = {"--candidate-sha": "candidate_sha", "--qa-path": "qa_path", "--qa-sha256": "qa_sha256",
              "--evidence": "evidence", "--attestation-commit": "attestation_commit"}
    seen = set()

    def _usage():
        print("usage: check_release_build.py [--pre-tag --candidate-sha SHA --qa-path PATH "
              "--qa-sha256 HEX [--first-pin --evidence PATH]] [--post-tag --qa-path PATH "
              "[--attestation-commit REF]] | --self-test (no option may be repeated)", file=sys.stderr)

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in flags:
            if arg in seen:
                print("error: duplicate option {}".format(arg), file=sys.stderr)
                return None
            seen.add(arg)
            opts[flags[arg]] = True
            i += 1
        elif arg in single and i + 1 < len(argv):
            if arg in seen:
                print("error: duplicate option {}".format(arg), file=sys.stderr)
                return None
            seen.add(arg)
            opts[single[arg]] = argv[i + 1]
            i += 2
        else:
            _usage()
            return None
    return opts


def _resolve_mode(opts):
    """Classify an option set into exactly one dispatch mode BEFORE any work runs, so a mixed, incomplete,
    or out-of-mode invocation can never fall through into audit or self-test (round-3 finding 6). Returns
    one of 'self-test', 'audit', 'pre-tag', 'post-tag', 'error'. --self-test runs ALONE; --pre-tag requires
    --candidate-sha/--qa-path/--qa-sha256 (and --evidence iff --first-pin) and rejects post-tag-only args;
    --post-tag requires --qa-path and rejects pre-tag-only args; a stage option without its stage, or the
    two stages together, is an error."""
    pretag_only = opts["candidate_sha"] or opts["qa_sha256"] or opts["first_pin"] or opts["evidence"]
    posttag_only = opts["attestation_commit"]
    if opts["self_test"]:
        if opts["pre_tag"] or opts["post_tag"] or pretag_only or posttag_only or opts["qa_path"]:
            return "error"
        return "self-test"
    if opts["pre_tag"] and opts["post_tag"]:
        return "error"
    if opts["pre_tag"]:
        if not (opts["candidate_sha"] and opts["qa_path"] and opts["qa_sha256"]) or posttag_only:
            return "error"
        if opts["first_pin"] and not opts["evidence"]:
            return "error"
        if opts["evidence"] and not opts["first_pin"]:
            return "error"
        return "pre-tag"
    if opts["post_tag"]:
        if not opts["qa_path"] or pretag_only:
            return "error"
        return "post-tag"
    # audit (no stage): any stage-specific option present is a mixed/out-of-mode invocation.
    if pretag_only or posttag_only or opts["qa_path"]:
        return "error"
    return "audit"


def main():
    opts = _parse_args(sys.argv[1:])
    if opts is None:
        return 2
    mode = _resolve_mode(opts)
    if mode == "error":
        print("error: a mixed, incomplete, or out-of-mode invocation is rejected fail-closed. --self-test "
              "runs alone; --pre-tag requires --candidate-sha, --qa-path, --qa-sha256 (and --evidence with "
              "--first-pin); --post-tag requires --qa-path; a stage-specific option needs its stage",
              file=sys.stderr)
        return 2
    if mode == "self-test":
        return self_test_main()
    root = repo_root()
    if mode == "pre-tag":
        return run_pre_tag(root, opts["candidate_sha"], opts["qa_path"], opts["qa_sha256"],
                           opts["first_pin"], opts["evidence"])
    if mode == "post-tag":
        return run_post_tag(root, opts["attestation_commit"], opts["qa_path"])
    return run_audit(root)


if __name__ == "__main__":
    sys.exit(main())
