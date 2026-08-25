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
whole-surface delta otherwise); --repin --target V (10.4 adopter mode, rollback branch keyed on the
pin-history match plus wholesale target validation plus recorded authorization); --self-test.

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, load_toml            # noqa: E402
from check_versions import _parse                        # noqa: E402  bare-SemVer (sibling idiom)
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

def _git(root, args, binary=False):
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=not binary)
    except OSError as exc:
        raise GateError("git is not available: {}".format(exc))


def _show(root, commit, path):
    proc = _git(root, ["show", "{}:{}".format(commit, path)], binary=True)
    if proc.returncode != 0:
        raise GateError("git show {}:{} failed: predecessor artifact unreachable".format(commit, path))
    return proc.stdout


def _show_toml(root, commit, path):
    try:
        return tomllib.loads(_show(root, commit, path).decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
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


def _tag_kind(root, tag):
    """'tag' for an annotated tag, 'commit' for a lightweight one; GateError if the tag does not resolve."""
    proc = _git(root, ["cat-file", "-t", "refs/tags/" + tag], binary=True)
    if proc.returncode != 0:
        raise GateError("tag {} does not resolve (unfetched or deleted)".format(tag))
    try:
        return proc.stdout.decode("ascii").strip()
    except UnicodeDecodeError:
        raise GateError("git cat-file output for tag {} is not valid ASCII; fail-closed".format(tag))


def _anchor_predecessor(root, prev_row):
    """Establish that the predecessor row names an ANCHORED release (round-7 finding 2): its commit_sha is a
    full object id resolving to a COMMIT equal to itself, and its annotated tag resolves to the recorded
    tag_object_sha and peels to that commit. A malformed/unanchored predecessor is cannot-evaluate, exit 2,
    never a silent delta over an unverifiable base."""
    commit, tag, tag_obj = prev_row["commit_sha"], prev_row["tag"], prev_row["tag_object_sha"]
    if not _release_schema.OBJECTID_RE.fullmatch(commit):
        raise GateError("predecessor commit_sha {!r} is not a full lowercase object id".format(commit))
    if _rev_parse(root, commit + "^{commit}") != commit:
        raise GateError("predecessor commit_sha {!r} does not resolve to a commit equal to itself "
                        "(a tree or non-commit is rejected)".format(commit))
    if _tag_kind(root, tag) != "tag":
        raise GateError("predecessor tag {} is not an annotated tag (2.1)".format(tag))
    if _rev_parse(root, "refs/tags/" + tag) != tag_obj:
        raise GateError("predecessor tag {} does not resolve to the recorded tag_object_sha".format(tag))
    if _rev_parse(root, "refs/tags/" + tag + "^{commit}") != commit:
        raise GateError("predecessor tag {} peels to a commit other than the recorded commit_sha".format(
            tag))


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


def normalize_dispositions(rows):
    """Validate a list of disposition row dicts to full per-kind depth (6.5/6.6). Returns a list of
    normalized rows each carrying a private _consumed flag. A malformed row raises GateError.

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
        raise GateError("{}: [[disposition]] is not an array".format(DISPOSITIONS_REL))
    out, seen_ids = [], set()
    for i, row in enumerate(rows, 1):
        where = "{} row #{}".format(DISPOSITIONS_REL, i)
        if not isinstance(row, dict):
            raise GateError(where + ": not a table")
        for field in DISPOSITION_COMMON_FIELDS:
            v = row.get(field)
            if not isinstance(v, str) or not v:
                raise GateError(where + ": missing or non-string {!r} (the Step-2 common schema)".format(
                    field))
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


def load_dispositions(root):
    """The public record, strictly validated (6.5): format-version == 1 and the exact top-level keyset
    {format-version, disposition} (round-2 finding 1), then the full per-row/per-kind normalization. Returns
    a list of normalized rows."""
    data = _load(root, DISPOSITIONS_REL)
    if data.get("format-version") != 1:
        raise GateError("{}: format-version must be exactly 1".format(DISPOSITIONS_REL))
    extra = set(data) - {"format-version", "disposition"}
    if extra:
        raise GateError("{}: unknown top-level key(s): {}".format(
            DISPOSITIONS_REL, ", ".join(sorted(extra))))
    return normalize_dispositions(data.get("disposition", []))


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
        proc = subprocess.run([sys.executable, str(root / "tools" / "gen_renderers.py"), "--check",
                               "--root", str(root)], capture_output=True)
    except OSError as exc:
        raise GateError("{} renderer freshness could not launch ({}); fail-closed".format(label, exc))
    if proc.returncode != 0:
        raise GateError("{} renderer declaration is stale or a closure is incomplete "
                        "(gen_renderers.py --check rc={})".format(label, proc.returncode))


def _predecessor_tree_checks(root, commit, prev_genesis):
    """Run the AUTHORITATIVE validators on the PREDECESSOR tree in non-genesis mode (round-4 findings 1, 2,
    4). The tree is materialized from RAW blob bytes (git ls-tree + cat-file, NO checkout, so NO smudge/
    clean filter or gitattributes transformation can substitute old bytes; round-4 finding 1), then
    gen_renderers.py --check recomputes every closure and framed digest, and check_clauses validates the
    full 7.2/7.3 inventory and register against the predecessor's own sources. Any nonzero is exit 2.
    Hermetic: a temp dir removed in finally; no worktree, no host mutation."""
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
        _renderer_freshness(dest, "predecessor")
        clause_args = ["--root", str(dest)] + (["--genesis"] if prev_genesis else [])
        _validate_via_tool(dest, "check_clauses.py", clause_args,
                           "predecessor clause inventory / id-history structure (7.2/7.3)")
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
    proc = _git(root, ["ls-tree", "-r", "-z", commit], binary=True)
    if proc.returncode != 0:
        raise GateError("cannot list predecessor tree {} ({})".format(
            commit, proc.stderr.decode("utf-8", "replace").strip()))
    if not proc.stdout:
        raise GateError("predecessor tree {} lists no entries; an empty tree is never assumed".format(commit))
    paths, seen_fold, seen_nfc = set(), {}, {}
    for record in proc.stdout.split(b"\x00"):
        if not record:
            continue
        try:
            meta, path = record.decode("utf-8").split("\t", 1)
            mode = meta.split(" ", 1)[0]
        except (UnicodeDecodeError, ValueError) as exc:
            raise GateError("malformed git ls-tree record ({}); fail-closed".format(exc))
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


# --- genesis structural validation (single-home validators, never re-implemented) -------------------

def _validate_via_tool(root, script, args, what):
    """Run a sibling single-home validator as a subprocess and raise GateError (exit 2) on any nonzero exit
    OR a launch failure, so a structural violation OR a cannot-evaluate fails this gate closed rather than
    passing (VC-4 QA #1; round-4 finding 4 launch propagation). The tool is located beside this gate and
    pointed at the target via its args."""
    try:
        # BYTES capture (round-5 finding 3): a child emitting invalid UTF-8 must not crash the gate; the
        # returncode is interpreted, and the diagnostic tail is decoded with replacement.
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / script), *args],
                              capture_output=True)
    except OSError as exc:
        raise GateError("{}: cannot launch {} ({}); fail-closed".format(what, script, exc))
    if proc.returncode != 0:
        diag = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()[:400]
        raise GateError("{}: {} rc={} ({})".format(what, script, proc.returncode, diag))


def _genesis_structural(root):
    """Validate the internal structure of the consumed records at genesis (2.5/6.5), reusing each record's
    authoritative single-home validator so this gate never re-implements or drifts from them: check_clauses
    --genesis asserts the clause inventory (7.2) and the id-history register (7.3, born rows only covering
    the whole inventory), and gen_renderers --check re-derives every renderer closure and framed digest
    (6.5). Dispositions are validated separately by load_dispositions (normalize) in run()."""
    _validate_via_tool(root, "check_clauses.py", ["--root", str(root), "--genesis"],
                       "genesis clause inventory / id-history register structure (7.2/7.3)")
    _validate_via_tool(root, "gen_renderers.py", ["--check", "--root", str(root)],
                       "renderer declaration freshness (6.5)")


# --- run --------------------------------------------------------------------------------------------

def run(root):
    try:
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
        # genesis and delta modes. lstat (round-5 finding 5): the path existing as ANY tree entry, including
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
            _strict(_release_schema.strict_renderers, _load(root, RENDERERS_REL), RENDERERS_REL)
            _strict(_release_schema.strict_clause_inventory, _load(root, CLAUSES_REL), CLAUSES_REL)
            _strict(_release_schema.strict_id_history, _load(root, IDHISTORY_REL), IDHISTORY_REL)
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
        claimed = _claimed_rank(prev_row["version"], head_version)
        # Strict-validate the consumed records on BOTH the predecessor object and the head object BEFORE any
        # delta computation (round-2 findings 1/4): a duplicate or incomplete clause row, a malformed
        # id-history register, or a bad order.toml (format-version, top-level shape) is exit 2, never a
        # silently collapsed dict or a computed delta over an unvalidated surface.
        prev_inv = _strict(_release_schema.strict_clause_inventory,
                           _show_toml(root, commit, CLAUSES_REL), "predecessor " + CLAUSES_REL)
        head_inv = _strict(_release_schema.strict_clause_inventory, _load(root, CLAUSES_REL), CLAUSES_REL)
        register = _strict(_release_schema.strict_id_history, _load(root, IDHISTORY_REL), IDHISTORY_REL)
        _strict(_release_schema.strict_order, _show_toml(root, commit, ORDER_REL),
                "predecessor " + ORDER_REL)
        _strict(_release_schema.strict_order, _load(root, ORDER_REL), ORDER_REL)
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
        # The subjects of the changes DETECTED this release, grouped by the disposition kind that would
        # target them, so a mis-dated row for a current change can be caught (round-3 finding 3).
        clause_subjects = {e.subject for e in events if e.surface == "clause" and e.change in (
            "behaviour-neutral", "strengthened", "default-correction", "undispositioned-text-change")}
        ownership_subjects = {e.subject for e in events if e.surface == "ownership"
                              and e.change in ("weakened", "strengthened")}
        renderer_subjects = {e.subject for e in events if e.surface == "renderer"}
        subjects_by_kind = {"behaviour-neutral": clause_subjects, "strengthened": clause_subjects,
                            "default-correction": clause_subjects, "class-change": ownership_subjects,
                            "renderer-semantics": renderer_subjects}
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
        if claimed < floor:
            findings.append("claimed bump {} ({} -> {}) is below the required {} floor".format(
                BUMP_NAME[claimed], prev_row["version"], head_version, BUMP_NAME[floor]))
    except (GateError, OSError, UnicodeError, KeyError, TypeError) as exc:
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
    import os
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_AUTHOR_NAME": "AIQT Self-Test", "GIT_AUTHOR_EMAIL": "selftest@example.invalid",
                "GIT_COMMITTER_NAME": "AIQT Self-Test", "GIT_COMMITTER_EMAIL": "selftest@example.invalid",
                "GIT_AUTHOR_DATE": "2000-01-01T00:00:00", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00"})
    if init:
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, capture_output=True, env=env)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", msg], check=True, capture_output=True,
                   env=env)


def _selftest_env():
    import os
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_AUTHOR_NAME": "AIQT Self-Test", "GIT_AUTHOR_EMAIL": "selftest@example.invalid",
                "GIT_COMMITTER_NAME": "AIQT Self-Test", "GIT_COMMITTER_EMAIL": "selftest@example.invalid",
                "GIT_AUTHOR_DATE": "2000-01-01T00:00:00", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    return env


def _archive_head(real):
    arch = subprocess.run(["git", "-C", str(real), "archive", "HEAD"], capture_output=True)
    return arch.stdout if arch.returncode == 0 and arch.stdout else None


def _extract(arch_bytes, dest):
    import io
    import tarfile
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(arch_bytes), mode="r:") as tf:
        tf.extractall(dest)
    return dest


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
        print("SELF-TEST NOTE: `git archive HEAD` unavailable; the real full-pack run() case was SKIPPED",
              file=sys.stderr)
        return False
    repo = tmp / "real-pack"
    try:
        _extract(arch, repo)
    except Exception as exc:  # noqa: BLE001  a bad archive is a skip, not a false pass
        print("SELF-TEST NOTE: could not extract the archive ({}); real full-pack case SKIPPED".format(exc),
              file=sys.stderr)
        return False
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "release 1.0.0", "--no-verify"]):
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
        return subprocess.run(["python3", "tools/gen_manifest.py", "--root", str(repo)],
                              capture_output=True, env=env).returncode == 0

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
        print("SELF-TEST NOTE: could not regenerate the head manifest; real full-pack case SKIPPED",
              file=sys.stderr)
        return False
    _disp("1.0.1")
    (repo / RELEASES_REL).write_text(_releases(1), encoding="utf-8")
    # (finding 2) a CONSISTENTLY-edited id-keyed PATCH change, fully dispositioned, with an ANCHORED
    # predecessor and a VERSION-BOUND head: clean exit 0.
    if _run_quiet_root(repo) != 0:
        failures.append("real full-pack run(): a consistently-edited dispositioned id-keyed PATCH change "
                        "expected exit 0 (finding 2)")
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
    orig_gcommon = (repo2 / gcommon).read_text(encoding="utf-8")
    (repo2 / gcommon).write_text(orig_gcommon + "\n# predecessor-only undeclared edit\n", encoding="utf-8")
    attrs = repo2 / ".gitattributes"
    attrs.write_text(attrs.read_text(encoding="utf-8") + "tools/_gen_common.py filter=hide\n",
                     encoding="utf-8")
    ok = True
    for args in (["init", "-q"],
                 ["config", "filter.hide.smudge", "sed '/predecessor-only undeclared edit/d'"],
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
        subprocess.run(["python3", "tools/gen_manifest.py", "--root", str(repo2)],
                       capture_output=True, env=env)
        if _run_quiet_root(repo2) != 2:
            failures.append("real full-pack run(): a smudge-filter-hidden predecessor renderer-closure edit "
                            "must be caught by the raw materialization, exit 2 (round-4 findings 1/4)")

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
        return dest

    gclean = _extract_genesis("genesis-clean")
    if gclean is not None and _run_quiet_root(gclean) != 0:
        failures.append("real genesis full-pack run(): the unmutated real tree expected a clean exit 0")
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


def self_test_main():  # noqa: C901  a flat sequence of independent classification cases
    failures = []

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
    def _opts(self_test=False, repin=False, target=None):
        return {"self_test": self_test, "repin": repin, "target": target}
    _cases = [(_opts(), "run"),
              (_opts(self_test=True), "self-test"),
              (_opts(repin=True, target="1.0.0"), "repin"),
              (_opts(target="1.0.0"), "error"),               # --target without --repin never runs
              (_opts(repin=True), "error"),                   # --repin without --target is incomplete
              (_opts(repin=True, target="1.0.0", self_test=True), "error"),   # mixed with --self-test
              (_opts(target="1.0.0", self_test=True), "error")]
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

    # --- _cat_file_batch grammar (round-7 finding 3) ----------------------------------------------
    class _FakeProc:
        def __init__(self, stdout):
            self.returncode, self.stdout, self.stderr = 0, stdout, b""
    _saved_run = subprocess.run
    oid = "a" * 40
    # a malformed (non-decimal) size, and a declared-10-but-2-byte short body, each raise SchemaError.
    for bad in (oid.encode() + b" blob NaN\nxx\n", oid.encode() + b" blob 10\nxx\n"):
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
    try:
        _claimed_rank("1.1.0", "1.0.0")
        failures.append("_claimed_rank: expected GateError when head does not increase")
    except GateError:
        pass

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
            if _git_available():
                real_ran = _real_pack_e2e(tmp, failures)
            else:
                real_ran = False
                print("SELF-TEST NOTE: git unavailable; the real full-pack two-release run() case was "
                      "SKIPPED", file=sys.stderr)
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
    opts = {"self_test": False, "repin": False, "target": None}
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
        else:
            print("usage: check_release_delta.py [--repin --target V] | --self-test (no option may be "
                  "repeated)", file=sys.stderr)
            return None
    return opts


def _resolve_mode(opts):
    """Classify an option set into exactly one dispatch mode BEFORE any work runs, so a mixed or incomplete
    invocation can never fall through to a real run (VC-4 QA #9). Any invocation touching the repin family
    (--repin or --target) resolves to 'repin' (the fail-closed 10.4 stub) or 'error', never 'run' or
    'self-test'. Returns one of: 'self-test', 'run', 'repin', 'error'."""
    repin_family = opts["repin"] or opts["target"] is not None
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
