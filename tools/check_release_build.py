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


def _release_tags(root):
    """The annotated release tags in the repo: {version: tag} for every tag named 'v<bare SemVer>' that
    resolves to an ANNOTATED tag object (2.1). Lightweight or non-release tags are ignored here (each
    recorded row validates its own tag); this answers only 'is a tagged release missing its row'."""
    proc = _git(root, ["tag", "-l", "v*"])
    if proc.returncode != 0:
        raise GateError("cannot list release tags ({})".format(proc.stderr.strip()))
    out = {}
    for line in proc.stdout.splitlines():
        tag = line.strip()
        if not tag.startswith("v") or _parse(tag[1:]) is None:
            continue
        if _tag_kind(root, tag) == "tag":
            out[tag[1:]] = tag
    return out


def predecessor_completeness_findings(root, rows):
    """2.4 / VER-CORE-SPEC.md:273 PREDECESSOR COMPLETENESS. Strictly-increasing SemVer over the recorded
    rows cannot prove no TAGGED release was skipped (rows 1.0.0, 1.2.0 both increase while a tagged 1.1.0 is
    missing). Enumerate the annotated release tags and require every tagged release AT OR BELOW the newest
    recorded row to carry a row: a tagged predecessor lacking its attestation row is a FAIL (release N+1 is
    not accepted before row N exists). A tag ABOVE the newest recorded row is the in-flight release (its row
    is appended post-tag) and is allowed."""
    findings = []
    recorded = {}
    for r in rows:
        t = _parse(r["version"])
        if t is not None:
            recorded[t] = r["version"]
    if not recorded:
        return findings
    newest = max(recorded)
    for ver, tag in sorted(_release_tags(root).items()):
        t = _parse(ver)
        if t is not None and t <= newest and t not in recorded:
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
        findings += predecessor_completeness_findings(root, rows)  # finding 7: no skipped tagged release
        genesis_flags = []
        for row in rows:
            row_findings, is_genesis = _validate_tag_row(root, row)
            findings += row_findings
            genesis_flags.append(is_genesis)
        findings += genesis_findings(genesis_flags)
        # Chronology on the newest row, from the row's recorded timestamps vs its tagger date; the strict
        # loader already guarantees the timestamps are present and non-empty (finding 4), so this is a real
        # comparison, never a vacuous clean.
        newest = rows[-1]
        findings += chronology_findings(newest["attestation-timestamps"],
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
        # genesis declaration of the candidate tree; the candidate releases record is strict-validated
        # too (finding 4: format-version + top-level + full rows on the candidate object).
        cand_manifest = _show_toml(root, candidate_sha, MANIFEST_REL)
        cand_rows = _strict_releases(_show_toml(root, candidate_sha, RELEASES_REL),
                                     "candidate " + RELEASES_REL)
        is_genesis = cand_manifest.get("genesis") is True
        if is_genesis and cand_rows:
            findings.append("candidate declares genesis = true but its releases.toml is not header-only "
                            "(2.5)")
        # prior rows validate (they are already-anchored releases).
        for row in load_build_rows(root):
            row_findings, _g = _validate_tag_row(root, row)
            findings += row_findings
        if first_pin:
            findings += _first_pin_findings(root, candidate_sha, evidence)
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    return _report(findings, "pre-tag")


FIRST_PIN_EVIDENCE_KEYS = frozenset({"candidate-sha", "observed-measurement", "cap-bytes",
                                     "prefix-superset", "demonstration", "agents-sha256"})


def _first_pin_evidence_findings(candidate_sha, evidence):
    """Validate the --first-pin EVIDENCE artifact (2.1/6.6/VER-CORE-SPEC.md:1034), not merely is_file()
    (finding 8). --evidence is REQUIRED in --first-pin mode. The artifact (a TOML file, schema a [VERIFY]
    defined here) carries EXACTLY FIRST_PIN_EVIDENCE_KEYS: candidate-sha (bound to the candidate commit),
    agents-sha256 (the 64-hex digest of the shipped AGENTS.md bytes demonstrated against), observed-
    measurement and cap-bytes (positive integers with observed-measurement STRICTLY exceeding cap-bytes, the
    61,117 > 32,768 premise of L1017/L1028), prefix-superset (a real boolean true: the delivered-prefix-
    superset demonstration succeeded, L1029), and demonstration (a non-empty reference). An unreadable or
    unparseable artifact is exit 2. DISCLOSED RESIDUAL (disclose-guard-residuals): offline, this validates
    the artifact's schema, candidate binding, digest SYNTAX, and asserted superset OUTCOME; it does not
    re-derive the byte-superset against the live AGENTS.md (adopter-experience-owned) nor test reachability."""
    if evidence is None:
        return ["first-pin: --evidence is REQUIRED in --first-pin mode (the first-pin precondition is owed "
                "delivered evidence, not asserted; L1034)"]
    ev_path = Path(evidence)
    if not ev_path.is_file():
        return ["first-pin: the named evidence artifact {} does not exist".format(evidence)]
    try:
        data = tomllib.loads(ev_path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise GateError("first-pin: evidence artifact {} is unreadable or does not parse ({})".format(
            evidence, exc))
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
    meas, cap = data.get("observed-measurement"), data.get("cap-bytes")
    if not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in (meas, cap)):
        findings.append("first-pin: observed-measurement and cap-bytes must be positive integers")
    elif not meas > cap:
        findings.append("first-pin: observed-measurement {} does not exceed cap-bytes {} (the default-cap "
                        "premise, L1028)".format(meas, cap))
    if data.get("prefix-superset") is not True:
        findings.append("first-pin: prefix-superset must be true (the delivered-prefix-superset "
                        "demonstration must succeed, L1029)")
    if not isinstance(data.get("demonstration"), str) or not data.get("demonstration"):
        findings.append("first-pin: demonstration must be a non-empty reference")
    return findings


def _first_pin_findings(root, candidate_sha, evidence):
    """--first-pin (2.1/6.6): the step-1 freeze holds in the candidate checkout (check_clauses --genesis
    passes) AND the delivered prefix-superset EVIDENCE is present and valid (finding 8), required in
    first-pin mode regardless of genesis."""
    findings = list(_first_pin_evidence_findings(candidate_sha, evidence))
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
        findings += predecessor_completeness_findings(root, norm)  # finding 7
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
    except GateError as exc:
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

            def _full_row(tobj, csha, ts, version="1.0.0", tag="v1.0.0", fmt=1):
                # A COMPLETE attestation row (finding 4): every present row carries all seven fields.
                return ('format-version = {}\n\n[[release]]\nversion = "{}"\ntag = "{}"\n'
                        'tag_object_sha = "{}"\ncommit_sha = "{}"\nqa-sha256 = "{}"\n'
                        'qa-store-path = "qa/{}.toml"\nattestation-timestamps = [{}]\n'.format(
                            fmt, version, tag, tobj, csha, "c" * 64, version, ts))

            (a / RELEASES_REL).write_text(_full_row(tag_obj, commit_sha, tagger - 100), encoding="utf-8")
            if _run_audit_quiet(a) != 0:
                failures.append("audit annotated-tag row: expected a clean exit 0")

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
            _write_records(lw, "format-version = 1\n", "release-version = \"1.0.0\"\ngenesis = true\n")
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

            # (unresolvable tag) a recorded tag with no tag object -> exit 2, never a pass.
            nr = tmp / "notag"
            _init(nr)
            _write_records(nr, "format-version = 1\n", "release-version = \"1.0.0\"\ngenesis = true\n")
            _commit(nr, "genesis tree")
            (nr / RELEASES_REL).write_text(
                'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
                'tag_object_sha = "dead"\ncommit_sha = "beef"\nqa-sha256 = "{}"\n'
                'qa-store-path = "qa/1.0.0.toml"\nattestation-timestamps = [100]\n'.format("c" * 64),
                encoding="utf-8")
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
            _write_records(pc, "format-version = 1\n", 'release-version = "1.0.0"\ngenesis = true\n')
            _commit(pc, "tree")
            for v in ("1.0.0", "1.1.0", "1.2.0"):
                subprocess.run(["git", "-C", str(pc), "tag", "-a", "v" + v, "-m", v],
                               check=True, capture_output=True, text=True,
                               env={"GIT_COMMITTER_DATE": "2000-01-01T00:00:00", **_env()})
            skipped = [{"version": "1.0.0"}, {"version": "1.2.0"}]
            if not any("no attestation row" in f for f in predecessor_completeness_findings(pc, skipped)):
                failures.append("predecessor completeness: a tagged 1.1.0 skipped between rows 1.0.0 and "
                                "1.2.0 expected a finding (finding 7)")
            complete = [{"version": "1.0.0"}, {"version": "1.1.0"}, {"version": "1.2.0"}]
            if predecessor_completeness_findings(pc, complete):
                failures.append("predecessor completeness: a complete record expected no finding")
            inflight = [{"version": "1.0.0"}, {"version": "1.1.0"}]  # 1.2.0 tagged but not yet attested
            if predecessor_completeness_findings(pc, inflight):
                failures.append("predecessor completeness: an in-flight tag above the newest row must be "
                                "allowed (no finding)")

            # (finding 8) FIRST-PIN EVIDENCE schema. A well-formed evidence artifact bound to the candidate
            # clears; a missing --evidence, a candidate-binding mismatch, a bad digest, an observed<=cap, and
            # a false prefix-superset each produce findings.
            ev_ok = pt / "evidence.toml"
            ev_ok.write_text(
                'candidate-sha = "{}"\nobserved-measurement = 61117\ncap-bytes = 32768\n'
                'prefix-superset = true\ndemonstration = "qa/prefix-superset.md"\n'
                'agents-sha256 = "{}"\n'.format(pt_commit, "d" * 64), encoding="utf-8")
            if _first_pin_evidence_findings(pt_commit, str(ev_ok)):
                failures.append("first-pin evidence: a well-formed bound artifact expected no finding")
            if not any("REQUIRED" in f for f in _first_pin_evidence_findings(pt_commit, None)):
                failures.append("first-pin evidence: a missing --evidence must be flagged (finding 8)")
            if not any("not bound" in f for f in _first_pin_evidence_findings("OTHER-SHA", str(ev_ok))):
                failures.append("first-pin evidence: a candidate-binding mismatch must be flagged")
            ev_bad = pt / "evidence-bad.toml"
            ev_bad.write_text(
                'candidate-sha = "{}"\nobserved-measurement = 100\ncap-bytes = 32768\n'
                'prefix-superset = false\ndemonstration = "x"\nagents-sha256 = "nothex"\n'.format(pt_commit),
                encoding="utf-8")
            bad_findings = _first_pin_evidence_findings(pt_commit, str(ev_bad))
            if not (any("agents-sha256" in f for f in bad_findings)
                    and any("does not exceed" in f for f in bad_findings)
                    and any("prefix-superset must be true" in f for f in bad_findings)):
                failures.append("first-pin evidence: a bad digest, observed<=cap, and false prefix-superset "
                                "must each be flagged (finding 8)")
        finally:
            _sh.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    core = ("the 2.2 STRICT success/family-set predicates (clean, missing/duplicate family, failed verdict, "
            "unresolved blocker, false/string finished-signal, unknown/missing family key, boolean blocker "
            "count, finding 5), genesis uniqueness, the 2.4 ordering predicate, strict QA-object validation "
            "(finding 5), the shared release-row schema drift binding (#6), and the reproduce-command "
            "enumeration (#7)")
    if git_ran:
        print("SELF-TEST PASS: {}; and the git-level cases (zero-row audit NOT APPLICABLE, a zero-row "
              "format-version=999 exit 2 and a row missing qa-sha256/qa-store-path exit 2 (finding 4), a "
              "clean full attestation row, a chronology violation exit 1, a lightweight tag exit 1, an "
              "unresolvable tag exit 2, read_genesis accepting a full attestation row (#6), the post-tag "
              "end-to-end clean/no-qa-path/digest-mismatch cases and a no-timestamps row exit 2 (finding 4), "
              "the chronology-FORGING exit 1 (finding 6), predecessor-completeness on a real 3-tag repo "
              "(finding 7), and the first-pin evidence schema (finding 8)) hold".format(core))
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
        if opts["first_pin"] and not opts["evidence"]:
            print("error: --first-pin requires --evidence (the first-pin precondition is owed delivered "
                  "evidence; finding 8)", file=sys.stderr)
            return 2
        return run_pre_tag(root, opts["candidate_sha"], opts["qa_path"], opts["qa_sha256"],
                           opts["first_pin"], opts["evidence"])
    if opts["post_tag"]:
        return run_post_tag(root, opts["attestation_commit"], opts["qa_path"])
    return run_audit(root)


if __name__ == "__main__":
    sys.exit(main())
