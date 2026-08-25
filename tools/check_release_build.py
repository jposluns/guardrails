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
import shutil
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

RELEASES_REL = ".aiqt/core/releases.toml"
MANIFEST_REL = ".aiqt/manifest.toml"
FAMILIES = ("claude", "codex", "gemini")
HEX64 = frozenset("0123456789abcdef")
# The keys this gate validates for a release-order row: the four anchor fields plus the three attestation
# fields a row gains once QA'd. A self-test binds this to gen_manifest.RELEASE_ROW_ALLOWED so the full
# per-field validator here, read_genesis, and check_release_delta cannot diverge on the schema (QA #6).
BUILD_ROW_ANCHOR = ("version", "tag", "tag_object_sha", "commit_sha")
BUILD_ROW_KEYS = frozenset(BUILD_ROW_ANCHOR + ("qa-sha256", "qa-store-path", "attestation-timestamps"))


class GateError(Exception):
    """An input the gate cannot read, parse, or resolve. Exit 2 (fail-closed)."""


# --- git plumbing (every return code checked) -------------------------------------------------------

def _git(root, args, binary=False):
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=not binary)
    except OSError as exc:
        raise GateError("git is not available: {}".format(exc))


def _tag_kind(root, tag):
    """The git object type the tag ref points at directly: 'tag' for an annotated tag, 'commit' for a
    lightweight one. GateError if the tag does not resolve at all (an unfetched or deleted tag: the two
    are indistinguishable and neither may pass, per the layer_b lesson)."""
    proc = _git(root, ["cat-file", "-t", "refs/tags/" + tag])
    if proc.returncode != 0:
        raise GateError("tag {} does not resolve (unfetched or deleted; fetch tags)".format(tag))
    return proc.stdout.strip()


def _rev_parse(root, ref):
    proc = _git(root, ["rev-parse", "--verify", "--quiet", ref])
    if proc.returncode != 0:
        raise GateError("cannot resolve {!r}".format(ref))
    return proc.stdout.strip()


def _tagger_epoch(root, tag):
    """The tagger-date epoch of an annotated tag, parsed from `git cat-file tag`. None if the ref is not
    an annotated tag object (a lightweight tag has no tagger date). GateError on a git failure."""
    if _tag_kind(root, tag) != "tag":
        return None
    proc = _git(root, ["cat-file", "tag", tag])
    if proc.returncode != 0:
        raise GateError("cannot read tag object for {}".format(tag))
    for line in proc.stdout.splitlines():
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

def success_findings(families):
    """2.2 SUCCESS + FAMILY-SET. families is a list of {name, finished-signal, verdict,
    unresolved-blockers} dicts. A present-but-failed verdict is a FAIL, never a pass."""
    findings = []
    names = [f.get("name") for f in families]
    if sorted(str(n) for n in names) != sorted(FAMILIES) or len(set(names)) != len(names):
        findings.append("family set {} != required tri-family set {} (each exactly once)".format(
            sorted(str(n) for n in names), list(FAMILIES)))
    for fam in families:
        if not fam.get("finished-signal"):
            findings.append("family {}: no finished-result signal (a degraded delivery is not a "
                            "verdict)".format(fam.get("name")))
        if fam.get("verdict") not in ("PASS", "clean"):
            findings.append("family {}: verdict {!r} is not clean".format(
                fam.get("name"), fam.get("verdict")))
        if fam.get("unresolved-blockers", 0) != 0:
            findings.append("family {}: unresolved blocker(s)".format(fam.get("name")))
    return findings


def chronology_findings(attestation_epochs, tagger_epoch):
    """2.2 CHRONOLOGY: every attestation timestamp strictly earlier than the tagger date; a missing
    tagger date is a FAIL (annotated tags are mandatory)."""
    if tagger_epoch is None:
        return ["release tag carries no tagger date (a lightweight or malformed tag; 2.1 requires an "
                "annotated tag)"]
    return ["attestation timestamp {} is not strictly earlier than the tagger date {}".format(
        t, tagger_epoch) for t in attestation_epochs if not (t < tagger_epoch)]


def chronology_input_findings(row):
    """2.2, QA #8: the newest anchored row must carry its attestation-timestamps, so the chronology layer
    is a real comparison rather than vacuously clean over an empty list. An absent or empty list is a
    finding, never a silent default-to-clean."""
    if not row.get("attestation-timestamps"):
        return ["newest release row carries no attestation-timestamps; chronology cannot be evaluated and "
                "is not defaulted clean (2.2 fail-closed)"]
    return []


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


def validate_qa_obj(obj, expect_candidate_sha):
    """The pure QA-object validation (2.2): candidate-sha match, success, and family-set. Returns
    (findings, attestation_epochs)."""
    findings = []
    if not isinstance(obj, dict):
        return ["QA attestation is not a table"], []
    if obj.get("candidate-sha") != expect_candidate_sha:
        findings.append("QA attestation candidate SHA {!r} != expected {!r}".format(
            obj.get("candidate-sha"), expect_candidate_sha))
    fams = obj.get("family", [])
    if not isinstance(fams, list) or not all(isinstance(f, dict) for f in fams):
        return findings + ["QA attestation [[family]] is not an array of tables"], []
    findings += success_findings(fams)
    epochs = []
    for f in fams:
        for t in f.get("timestamps-utc", []):
            if isinstance(t, int) and not isinstance(t, bool):
                epochs.append(t)
            else:
                findings.append("family {}: a timestamps-utc entry {!r} is not an integer epoch".format(
                    f.get("name"), t))
    return findings, epochs


# --- maintainer-side QA retrieval -------------------------------------------------------------------

def qa_layers(qa_path, qa_sha256, expect_candidate_sha):
    """Retrieve, hash, parse, and validate the private-store QA object (2.2). Unreachable or unreadable
    is exit 2 (GateError); content mismatches are findings. Returns (findings, attestation_epochs)."""
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


def reproduce_gate(root, sha):
    """2.1 step 4: a disposable detached worktree of the exact candidate SHA; every registry generator's
    --check plus gen_manifest --check plus check_manifest run inside it. --check IS a byte comparison
    against fresh regeneration, so any drift is a FAIL and the release is invalid. Returns findings."""
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-release-reproduce-"))
    co = tmp / "co"
    try:
        proc = _git(root, ["worktree", "add", "--detach", str(co), sha])
        if proc.returncode != 0:
            raise GateError("cannot materialize candidate checkout: " + proc.stderr.strip())
        try:
            commands = _regenerate_check_commands(co)
        except Exception as exc:  # noqa: BLE001  a bad registry is cannot-evaluate, not clean
            raise GateError("cannot enumerate the reproduce command set ({})".format(exc))
        findings = []
        for argv in commands:
            r = subprocess.run(argv, cwd=str(co), capture_output=True, text=True)
            if r.returncode != 0:
                findings.append("reproduce drift: {!r} rc={}".format(" ".join(argv), r.returncode))
        return findings
    finally:
        _git(root, ["worktree", "remove", "--force", str(co)])
        shutil.rmtree(tmp, ignore_errors=True)


# --- row loading ------------------------------------------------------------------------------------

def _req_str(row, key, where):
    v = row.get(key)
    if not isinstance(v, str) or not v:
        raise GateError("{}: missing or non-string {!r}".format(where, key))
    return v


def _validate_row_fields(row, where):
    """The FULL per-field validation of one release-order row, the single validator shared by
    load_build_rows (working tree) and _normalize_rows (an arbitrary commit's record), so --post-tag gets
    exactly the same strict schema as the working-tree loader (QA #8: post-tag no longer validates only the
    anchor fields). Unknown keys are rejected against the shared schema (QA #6). Raises GateError."""
    if not isinstance(row, dict):
        raise GateError(where + ": not a table")
    extra = set(row) - gen_manifest.RELEASE_ROW_ALLOWED
    if extra:
        raise GateError(where + ": unknown key(s): {} (not in the shared release-row schema)".format(
            ", ".join(sorted(extra))))
    for key in BUILD_ROW_ANCHOR:
        _req_str(row, key, where)
    if _parse(row["version"]) is None:
        raise GateError(where + ": malformed version {!r}".format(row["version"]))
    if "qa-sha256" in row:
        d = row["qa-sha256"]
        if not isinstance(d, str) or len(d) != 64 or set(d) - HEX64:
            raise GateError(where + ": qa-sha256 is not 64 lowercase hex")
    if "qa-store-path" in row:
        p = row["qa-store-path"]
        if not isinstance(p, str) or not p:
            raise GateError(where + ": qa-store-path is not a non-empty string")
        if p.startswith("/") or (len(p) > 1 and p[1] == ":"):
            raise GateError(where + ": qa-store-path {!r} is host-absolute; the row records a "
                            "logical store path (portability)".format(p))
    if "attestation-timestamps" in row:
        ts = row["attestation-timestamps"]
        if not isinstance(ts, list) or not all(isinstance(t, int) and not isinstance(t, bool)
                                               for t in ts):
            raise GateError(where + ": attestation-timestamps must be a list of integer epochs")


def load_build_rows(root):
    """The release-order rows the build gate validates. Zero rows is the genesis/dormant state. A present
    row must carry the four anchor fields; qa-sha256 (64 hex), qa-store-path (a logical path), and
    attestation-timestamps (a list of integer epochs) are validated when present. Fail-closed on any
    malformed row."""
    try:
        data = load_toml(root / RELEASES_REL)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise GateError("cannot read {} ({})".format(RELEASES_REL, exc))
    rows = data.get("release", [])
    if not isinstance(rows, list):
        raise GateError("{}: [[release]] is not an array".format(RELEASES_REL))
    for i, row in enumerate(rows, 1):
        _validate_row_fields(row, "{} row #{}".format(RELEASES_REL, i))
    return rows


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
    if _rev_parse(root, "refs/tags/" + tag) != row["tag_object_sha"]:
        findings.append("release {} tag_object_sha does not match the resolved tag object".format(version))
    if _rev_parse(root, "refs/tags/" + tag + "^{commit}") != row["commit_sha"]:
        findings.append("release {} commit_sha does not match the tag's peeled commit".format(version))
    tagged_manifest = _show_toml(root, tag + "^{commit}", MANIFEST_REL)
    if tagged_manifest.get("release-version") != version:
        findings.append("release {} tag points at a tree whose manifest release-version is {!r}".format(
            version, tagged_manifest.get("release-version")))
    return findings, tagged_manifest.get("genesis") is True


# --- run stages -------------------------------------------------------------------------------------

def run_audit(root):
    """Default AUDIT (2.6): validate whatever exists; dormancy keys off the record's rows, never a git
    tag probe. Zero rows: every armed layer prints NOT APPLICABLE and the run is clean."""
    try:
        rows = load_build_rows(root)
        if not rows:
            for layer in ("tag-resolution", "genesis-uniqueness", "ordering", "chronology"):
                print("release-build: {} NOT APPLICABLE (releases.toml is zero-row; arms at the first "
                      "attestation row)".format(layer))
            print("release-build: qa-retrieval MAINTAINER-SIDE (runs under --pre-tag/--post-tag; the "
                  "private store is unreachable from CI, 2.2 residual)")
            return 0
        findings = []
        findings += ordering_findings([r["version"] for r in rows])
        genesis_flags = []
        for row in rows:
            row_findings, is_genesis = _validate_tag_row(root, row)
            findings += row_findings
            genesis_flags.append(is_genesis)
        findings += genesis_findings(genesis_flags)
        # Chronology on the newest row, from the row's recorded timestamps vs its tagger date; the
        # timestamps must be present (no default-to-clean, QA #8).
        newest = rows[-1]
        findings += chronology_input_findings(newest)
        findings += chronology_findings(newest.get("attestation-timestamps", []),
                                        _tagger_epoch(root, newest["tag"]))
        print("release-build: qa-retrieval MAINTAINER-SIDE (runs under --pre-tag/--post-tag; the "
              "private store is unreachable from CI, 2.2 residual)")
    except GateError as exc:
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
        # genesis declaration of the candidate tree.
        cand_manifest = _show_toml(root, candidate_sha, MANIFEST_REL)
        cand_releases = _show_toml(root, candidate_sha, RELEASES_REL)
        cand_rows = cand_releases.get("release", [])
        is_genesis = cand_manifest.get("genesis") is True
        if is_genesis and cand_rows:
            findings.append("candidate declares genesis = true but its releases.toml is not header-only "
                            "(2.5)")
        # prior rows validate (they are already-anchored releases).
        for row in load_build_rows(root):
            row_findings, _g = _validate_tag_row(root, row)
            findings += row_findings
        if first_pin:
            findings += _first_pin_findings(root, candidate_sha, evidence, is_genesis)
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    return _report(findings, "pre-tag")


def _first_pin_findings(root, candidate_sha, evidence, is_genesis):
    """--first-pin (2.1/6.6): the step-1 freeze holds in the candidate checkout (check_clauses --genesis
    passes) and, when this is the genesis release, the evidence artifact the first-pin precondition
    names is present. The prefix-superset demonstration itself is adopter-experience-owned."""
    findings = []
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-release-firstpin-"))
    co = tmp / "co"
    try:
        proc = _git(root, ["worktree", "add", "--detach", str(co), candidate_sha])
        if proc.returncode != 0:
            raise GateError("cannot materialize candidate for first-pin: " + proc.stderr.strip())
        r = subprocess.run(["python3", "tools/check_clauses.py", "--genesis"], cwd=str(co),
                           capture_output=True, text=True)
        if r.returncode != 0:
            findings.append("first-pin: check_clauses.py --genesis fails in the candidate checkout "
                            "(rc={}); the step-1 freeze is not clean".format(r.returncode))
    finally:
        _git(root, ["worktree", "remove", "--force", str(co)])
        shutil.rmtree(tmp, ignore_errors=True)
    if evidence is not None and not Path(evidence).is_file():
        findings.append("first-pin: the named evidence artifact {} does not exist".format(evidence))
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
        rows = releases.get("release", [])
        if not isinstance(rows, list) or not rows:
            raise GateError("--post-tag: the proposed attestation commit {} has no release rows to "
                            "validate".format(ref))
        # Re-validate through the SAME strict full-row validator as the working-tree loader (QA #8).
        norm = _normalize_rows(rows)
        findings = ordering_findings([r["version"] for r in norm])
        genesis_flags = []
        for row in norm:
            row_findings, is_genesis = _validate_tag_row(root, row)
            findings += row_findings
            genesis_flags.append(is_genesis)
        findings += genesis_findings(genesis_flags)
        newest = norm[-1]
        # The newest row is THE attestation row and must carry every attestation field; a missing field is
        # a finding, never a default-to-clean (QA #8).
        for key in ("qa-sha256", "qa-store-path"):
            if key not in newest:
                findings.append("newest release row is not fully attested: missing {} (2.2)".format(key))
        findings += chronology_input_findings(newest)
        findings += chronology_findings(newest.get("attestation-timestamps", []),
                                        _tagger_epoch(root, newest["tag"]))
        # Retrieve, re-hash, and validate the recorded QA object against the newest candidate commit.
        if qa_path is None:
            raise GateError("--post-tag requires --qa-path to retrieve and re-hash the newest row's "
                            "attestation QA object; the armed gate never certifies without it")
        if "qa-sha256" in newest:
            qa_findings, _epochs = qa_layers(qa_path, newest["qa-sha256"], newest["commit_sha"])
            findings += qa_findings
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    return _report(findings, "post-tag")


def _normalize_rows(rows):
    """Validate rows parsed from an arbitrary commit's releases.toml through the SAME strict full-row
    validator as load_build_rows (working tree), so --post-tag applies the full schema, not just the
    anchor fields (QA #8)."""
    out = []
    for i, row in enumerate(rows, 1):
        _validate_row_fields(row, "attestation releases row #{}".format(i))
        out.append(row)
    return out


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


def self_test_main():  # noqa: C901  a flat sequence of independent predicate and fixture cases
    failures = []

    ok_fams = [{"name": n, "finished-signal": True, "verdict": "PASS", "unresolved-blockers": 0}
               for n in FAMILIES]
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
    # a missing finished-signal (a degraded delivery is not a verdict).
    deg = [dict(f) for f in ok_fams]
    deg[2]["finished-signal"] = False
    if not success_findings(deg):
        failures.append("success_findings: a missing finished-signal expected a finding")

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

    # chronology-input guard (QA #8): a newest row without attestation-timestamps is a finding, never a
    # vacuously-clean chronology.
    if chronology_input_findings({"attestation-timestamps": [1]}):
        failures.append("chronology_input: a row with timestamps expected no finding")
    if not chronology_input_findings({}) or not chronology_input_findings({"attestation-timestamps": []}):
        failures.append("chronology_input: a row with no/empty timestamps expected a finding (QA #8)")

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

        def _write_records(path, releases_body, manifest_body="genesis = true\n"):
            (path / ".aiqt" / "core").mkdir(parents=True, exist_ok=True)
            (path / RELEASES_REL).write_text(releases_body, encoding="utf-8")
            (path / MANIFEST_REL).write_text(manifest_body, encoding="utf-8")

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
            _write_records(a, "format-version = 1\n", "release-version = \"1.0.0\"\ngenesis = true\n")
            _commit(a, "genesis release tree")
            subprocess.run(["git", "-C", str(a), "tag", "-a", "v1.0.0", "-m", "release 1.0.0"],
                           check=True, capture_output=True, text=True,
                           env={"GIT_COMMITTER_DATE": "2000-01-01T00:00:00", **_env()})
            tag_obj = subprocess.run(["git", "-C", str(a), "rev-parse", "refs/tags/v1.0.0"],
                                     check=True, capture_output=True, text=True).stdout.strip()
            commit_sha = subprocess.run(["git", "-C", str(a), "rev-parse", "refs/tags/v1.0.0^{commit}"],
                                        check=True, capture_output=True, text=True).stdout.strip()
            tagger = _tagger_epoch(a, "v1.0.0")
            row = ('format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
                   'tag_object_sha = "{}"\ncommit_sha = "{}"\nattestation-timestamps = [{}]\n'.format(
                       tag_obj, commit_sha, tagger - 100))
            (a / RELEASES_REL).write_text(row, encoding="utf-8")
            if _run_audit_quiet(a) != 0:
                failures.append("audit annotated-tag row: expected a clean exit 0")

            # (chronology violation) a timestamp not strictly earlier than the tagger date -> exit 1.
            row_bad = ('format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
                       'tag_object_sha = "{}"\ncommit_sha = "{}"\nattestation-timestamps = [{}]\n'.format(
                           tag_obj, commit_sha, tagger + 100))
            (a / RELEASES_REL).write_text(row_bad, encoding="utf-8")
            if _run_audit_quiet(a) != 1:
                failures.append("audit chronology violation: expected exit 1")

            # (lightweight tag) -> a finding (annotated tags are mandatory).
            lw = tmp / "lightweight"
            _init(lw)
            _write_records(lw, "format-version = 1\n", "release-version = \"1.0.0\"\ngenesis = true\n")
            _commit(lw, "genesis tree")
            subprocess.run(["git", "-C", str(lw), "tag", "v1.0.0"],  # lightweight (no -a)
                           check=True, capture_output=True, text=True)
            lw_obj = subprocess.run(["git", "-C", str(lw), "rev-parse", "refs/tags/v1.0.0"],
                                    check=True, capture_output=True, text=True).stdout.strip()
            (lw / RELEASES_REL).write_text(
                'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
                'tag_object_sha = "{}"\ncommit_sha = "{}"\n'.format(lw_obj, lw_obj), encoding="utf-8")
            if _run_audit_quiet(lw) != 1:
                failures.append("audit lightweight tag: expected exit 1 (annotated tags mandatory)")

            # (unresolvable tag) a recorded tag with no tag object -> exit 2, never a pass.
            nr = tmp / "notag"
            _init(nr)
            _write_records(nr, "format-version = 1\n", "release-version = \"1.0.0\"\ngenesis = true\n")
            _commit(nr, "genesis tree")
            (nr / RELEASES_REL).write_text(
                'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
                'tag_object_sha = "dead"\ncommit_sha = "beef"\n', encoding="utf-8")
            if _run_audit_quiet(nr) != 2:
                failures.append("audit unresolvable tag: expected fail-closed exit 2")

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
            _write_records(pt, "format-version = 1\n", 'release-version = "1.0.0"\ngenesis = true\n')
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
            nots_row = good_row.replace(
                'attestation-timestamps = [{}]\n'.format(pt_tagger - 100), "")
            (pt / RELEASES_REL).write_text(nots_row, encoding="utf-8")
            _commit(pt, "drop timestamps")
            if _run_post_tag_quiet(pt, None, str(qa_file)) != 1:
                failures.append("post-tag: a newest row without attestation-timestamps must fail exit 1 "
                                "(QA #8 no default-to-clean chronology)")
        finally:
            _sh.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    core = ("the 2.2 success/family-set predicates (clean, missing/duplicate family, failed verdict, "
            "unresolved blocker, missing finished-signal), the chronology predicate and its input guard "
            "(#8), genesis uniqueness, the 2.4 ordering predicate, QA-object validation, the shared "
            "release-row schema drift binding (#6), and the reproduce-command enumeration (#7)")
    if git_ran:
        print("SELF-TEST PASS: {}; and the git-level cases (zero-row audit NOT APPLICABLE, a clean "
              "annotated-tag row, a chronology violation exit 1, a lightweight tag exit 1, an "
              "unresolvable tag exit 2, read_genesis accepting a full attestation row (#6), and the "
              "post-tag end-to-end clean/no-qa-path/digest-mismatch/no-timestamps cases (#8)) hold".format(
                  core))
    else:
        print("SELF-TEST PASS (PARTIAL): {}; the git-level cases were SKIPPED (git or a writable temp "
              "directory unavailable), so those paths are UNVERIFIED this run".format(core))
    return 0


def _env():
    import os
    return {k: v for k, v in os.environ.items()}


def _parse_args(argv):
    opts = {"self_test": False, "pre_tag": False, "post_tag": False, "candidate_sha": None,
            "qa_path": None, "qa_sha256": None, "first_pin": False, "evidence": None,
            "attestation_commit": None}
    single = {"--candidate-sha": "candidate_sha", "--qa-path": "qa_path", "--qa-sha256": "qa_sha256",
              "--evidence": "evidence", "--attestation-commit": "attestation_commit"}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--self-test":
            opts["self_test"] = True
            i += 1
        elif arg == "--pre-tag":
            opts["pre_tag"] = True
            i += 1
        elif arg == "--post-tag":
            opts["post_tag"] = True
            i += 1
        elif arg == "--first-pin":
            opts["first_pin"] = True
            i += 1
        elif arg in single and i + 1 < len(argv):
            opts[single[arg]] = argv[i + 1]
            i += 2
        else:
            print("usage: check_release_build.py [--pre-tag --candidate-sha SHA --qa-path PATH "
                  "--qa-sha256 HEX [--first-pin --evidence PATH]] [--post-tag --qa-path PATH "
                  "[--attestation-commit REF]] | --self-test", file=sys.stderr)
            return None
    return opts


def main():
    opts = _parse_args(sys.argv[1:])
    if opts is None:
        return 2
    if opts["self_test"]:
        return self_test_main()
    if opts["pre_tag"] and opts["post_tag"]:
        print("error: --pre-tag and --post-tag are mutually exclusive", file=sys.stderr)
        return 2
    root = repo_root()
    if opts["pre_tag"]:
        if not (opts["candidate_sha"] and opts["qa_path"] and opts["qa_sha256"]):
            print("error: --pre-tag requires --candidate-sha, --qa-path, and --qa-sha256",
                  file=sys.stderr)
            return 2
        return run_pre_tag(root, opts["candidate_sha"], opts["qa_path"], opts["qa_sha256"],
                           opts["first_pin"], opts["evidence"])
    if opts["post_tag"]:
        return run_post_tag(root, opts["attestation_commit"], opts["qa_path"])
    return run_audit(root)


if __name__ == "__main__":
    sys.exit(main())
