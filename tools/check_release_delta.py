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
deferred): each [[disposition]] row carries `kind` (one of behaviour-neutral, strengthened,
default-correction, class-change, version-impact, renderer-semantics), `subject` (the clause-id, path,
renderer-id, or profile the disposition targets), `release` (the bare SemVer the disposition applies
at; the field name matches the step-2 record, not the draft's `version`), `impact`, and `rationale`.
A default-correction row additionally carries the 6.6 evidence fields (captured-source, capture-date,
observed-measurement, observed-date, prefix-superset-reference); a renderer-semantics row's `impact`
is alters-obligations or byte-only (6.2/GD-89). A row that is malformed for its kind is exit 2: a
mis-stated control must not run the gate mis-configured.

Modes: default (genesis mode while releases.toml is zero-row and the manifest declares genesis;
whole-surface delta otherwise); --repin --target V (10.4 adopter mode, rollback branch keyed on the
pin-history match plus wholesale target validation plus recorded authorization); --self-test.

Exit: 0 clean / genesis / NOT APPLICABLE legs; 1 a real finding (an under-claimed bump, a rowless
change, an unconsumed or wrong row, a register incompleteness); 2 malformed or unreadable input
(a predecessor artifact, the dispositions record, a stale renderer declaration, a git failure).
"""
import io
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
from check_manifest import _min_for, CLASS_STRENGTH      # noqa: E402  the absolute-minimum table
import gen_manifest                                      # noqa: E402  ownership loader (single source)

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

DISPOSITION_KINDS = {"behaviour-neutral", "strengthened", "default-correction", "class-change",
                     "version-impact", "renderer-semantics"}
DEFAULT_CORRECTION_EVIDENCE = ("captured-source", "capture-date", "observed-measurement",
                               "observed-date", "prefix-superset-reference")


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


# --- record loading ---------------------------------------------------------------------------------

def _load(root, rel):
    try:
        return load_toml(root / rel)
    except (OSError, ValueError) as exc:
        raise GateError("cannot read {} ({})".format(rel, exc))


def load_release_rows(root):
    """The release-order rows the delta gate needs (2.4). Zero rows is a valid genesis state. The delta
    gate resolves the predecessor by commit_sha, so version and commit_sha are mandatory (matching the
    gen_manifest.read_genesis minimal guard); tag and tag_object_sha are validated only when present
    (check_release_build owns the full tag-resolution schema). A present row missing a mandatory field,
    or carrying a malformed version or a non-string optional field, is exit 2."""
    data = _load(root, RELEASES_REL)
    rows = data.get("release", [])
    if not isinstance(rows, list):
        raise GateError("{}: [[release]] is not an array".format(RELEASES_REL))
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise GateError("{} row #{}: not a table".format(RELEASES_REL, i))
        for key in ("version", "commit_sha"):
            v = row.get(key)
            if not isinstance(v, str) or not v:
                raise GateError("{} row #{}: missing or non-string {!r}".format(RELEASES_REL, i, key))
        for key in ("tag", "tag_object_sha"):
            if key in row and (not isinstance(row[key], str) or not row[key]):
                raise GateError("{} row #{}: {!r} is present but not a non-empty string".format(
                    RELEASES_REL, i, key))
        if _parse(row["version"]) is None:
            raise GateError("{} row #{}: malformed version {!r}".format(RELEASES_REL, i, row["version"]))
    return rows


def normalize_dispositions(rows):
    """Validate a list of disposition row dicts to full per-kind depth (6.5/6.6). Returns a list of
    normalized rows each carrying a private _consumed flag. A malformed row raises GateError."""
    if not isinstance(rows, list):
        raise GateError("{}: [[disposition]] is not an array".format(DISPOSITIONS_REL))
    out = []
    for i, row in enumerate(rows, 1):
        where = "{} row #{}".format(DISPOSITIONS_REL, i)
        if not isinstance(row, dict):
            raise GateError(where + ": not a table")
        kind, subject, release = row.get("kind"), row.get("subject"), row.get("release")
        if kind not in DISPOSITION_KINDS:
            raise GateError(where + ": kind must be one of {}".format(sorted(DISPOSITION_KINDS)))
        if not isinstance(subject, str) or not subject:
            raise GateError(where + ": missing or non-string subject")
        if not isinstance(release, str) or _parse(release) is None:
            raise GateError(where + ": release {!r} is not a bare SemVer".format(release))
        if not isinstance(row.get("rationale"), str) or not row.get("rationale"):
            raise GateError(where + ": missing or non-string rationale")
        if kind == "default-correction":
            for k in DEFAULT_CORRECTION_EVIDENCE:
                if not isinstance(row.get(k), str) or not row.get(k):
                    raise GateError(where + ": default-correction row lacks evidence field {!r} "
                                    "(6.6)".format(k))
        if kind == "renderer-semantics" and row.get("impact") not in ("alters-obligations", "byte-only"):
            raise GateError(where + ": renderer-semantics row needs impact = alters-obligations or "
                            "byte-only (6.2/GD-89)")
        out.append(dict(row, _consumed=False))
    return out


def load_dispositions(root):
    """The public record, strictly validated (6.5). Returns a list of normalized rows."""
    data = _load(root, DISPOSITIONS_REL)
    return normalize_dispositions(data.get("disposition", []))


def take_row(rows, kind, subject, release):
    """Consume exactly one matching unconsumed row; None if absent; GateError on duplicates."""
    matches = [r for r in rows if r["kind"] == kind and r["subject"] == subject
               and r["release"] == release and not r["_consumed"]]
    if len(matches) > 1:
        raise GateError("duplicate {} disposition rows for {!r} at {}".format(kind, subject, release))
    if matches:
        matches[0]["_consumed"] = True
        return matches[0]
    return None


# --- legs (each returns (events, findings)) ---------------------------------------------------------

def clause_text_leg(prev_inv, head_inv, register, rows, head_version):
    """6.5 CLAUSE TEXT: diff canonical text per clause-id; classify via the id-history register and the
    public dispositions. Span or source-digest movement without a text change is not a delta."""
    events, findings = [], []
    prev = {r["clause-id"]: r.get("canonical-text", "") for r in prev_inv if isinstance(r, dict)}
    head = {r["clause-id"]: r.get("canonical-text", "") for r in head_inv if isinstance(r, dict)}
    retired = {r.get("id") for k in ("tombstone", "successor")
               for r in register.get(k, []) if isinstance(r, dict)}
    for cid in sorted(prev.keys() - head.keys()):
        events.append(DeltaEvent("clause", cid, "removed", MAJOR))
        if cid not in retired:
            findings.append("clause-id {!r} disappeared with no tombstone or successor row".format(cid))
    for cid in sorted(head.keys() - prev.keys()):
        events.append(DeltaEvent("clause", cid, "added", MINOR))
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


def renderer_leg(root, prev_decl, head_decl, rows, head_version):
    """6.5 RENDERER SEMANTICS. Freshness first: gen_renderers.py --check re-derives every closure and
    framed digest (the single home of that computation); drift or an incomplete closure is exit 2. Then
    the anchored declaration diff (renderer_diff)."""
    proc = subprocess.run([sys.executable, str(root / "tools" / "gen_renderers.py"), "--check"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise GateError("renderer declaration is stale or a closure is incomplete "
                        "(gen_renderers.py --check rc={})".format(proc.returncode))
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


def _ownership_classes_at(root, commit):
    """Predecessor ownership classes, computed against a disposable detached worktree of the anchored
    release commit so gen_manifest's loader and classifier run over that release's real tree. The
    worktree is removed in a finally block."""
    import shutil
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-release-delta-own-"))
    co = tmp / "co"
    try:
        proc = _git(root, ["worktree", "add", "--detach", str(co), commit])
        if proc.returncode != 0:
            raise GateError("cannot materialize predecessor {} for ownership ({})".format(
                commit, proc.stderr.strip()))
        try:
            return _classes_via_gen_manifest(co)
        except gen_manifest.GateError as exc:
            raise GateError("cannot classify predecessor ownership ({})".format(exc))
    finally:
        _git(root, ["worktree", "remove", "--force", str(co)])
        shutil.rmtree(tmp, ignore_errors=True)


def _claimed_rank(prev_v, head_v):
    p, h = _parse(prev_v), _parse(head_v)
    if p is None or h is None or h <= p:
        raise GateError("head version {} does not increase over predecessor {}".format(head_v, prev_v))
    if h[0] > p[0]:
        return MAJOR
    if h[1] > p[1]:
        return MINOR
    return PATCH


# --- run --------------------------------------------------------------------------------------------

def run(root):
    try:
        release_rows = load_release_rows(root)
        head_manifest = _load(root, MANIFEST_REL)
        rows = load_dispositions(root)
        if not release_rows:
            if head_manifest.get("genesis") is not True:
                raise GateError("releases.toml is zero-row but the manifest does not declare "
                                "genesis = true (2.5)")
            # Genesis: validate the consumed records parse, compute no delta (2.5/6.5).
            _load(root, ORDER_REL)
            _load(root, RENDERERS_REL)
            _load(root, CLAUSES_REL)
            print("release-delta: GENESIS (zero release rows, manifest genesis = true); records "
                  "validate; no delta computed")
            return 0
        prev_row = release_rows[-1]
        commit = prev_row["commit_sha"]
        changelog = _load(root, CHANGELOG_REL).get("release", [])
        if not isinstance(changelog, list) or not changelog or not isinstance(changelog[-1], dict):
            raise GateError("{}: no [[release]] tables to read the head version from".format(CHANGELOG_REL))
        head_version = changelog[-1].get("version")
        if not isinstance(head_version, str) or _parse(head_version) is None:
            raise GateError("{}: latest release version {!r} is malformed".format(
                CHANGELOG_REL, head_version))
        claimed = _claimed_rank(prev_row["version"], head_version)
        prev_inv = _show_toml(root, commit, CLAUSES_REL).get("clause", [])
        head_inv = _load(root, CLAUSES_REL).get("clause", [])
        register = _load(root, IDHISTORY_REL)
        events, findings = [], []
        for ev, fs in (clause_text_leg(prev_inv, head_inv, register, rows, head_version),
                       path_keyset_leg(_show_toml(root, commit, MANIFEST_REL), head_manifest),
                       ownership_leg(_ownership_classes_at(root, commit),
                                     _ownership_classes_head(root), rows, head_version),
                       order_leg(_show(root, commit, ORDER_REL), (root / ORDER_REL).read_bytes()),
                       renderer_leg(root, _show_toml(root, commit, RENDERERS_REL),
                                    _load(root, RENDERERS_REL), rows, head_version)):
            events += ev
            findings += fs
        # Profiles/groups: dormant until the artifact exists (2.6 structural absence).
        if not (root / PROFILES_REL).is_file():
            print("release-delta: profiles/groups leg NOT APPLICABLE (adopter-experience artifact not "
                  "yet defined; arms when it ships)")
        floor = max((e.floor for e in events), default=PATCH)
        for r in rows:
            if r["release"] == head_version and not r["_consumed"]:
                findings.append("disposition row ({} {}) at {} matches no detected change "
                                "(unconsumed)".format(r["kind"], r["subject"], head_version))
        if claimed < floor:
            findings.append("claimed bump {} ({} -> {}) is below the required {} floor".format(
                BUMP_NAME[claimed], prev_row["version"], head_version, BUMP_NAME[floor]))
    except (GateError, OSError, KeyError, TypeError) as exc:
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


def self_test_main():  # noqa: C901  a flat sequence of independent classification cases
    failures = []

    def _rows(*specs):
        # specs: (kind, subject, release[, impact][, evidence-complete])
        out = []
        for spec in specs:
            kind, subject, release = spec[0], spec[1], spec[2]
            row = {"id": "d{}".format(len(out)), "kind": kind, "subject": subject,
                   "release": release, "impact": "x", "rationale": "r"}
            if kind == "renderer-semantics":
                row["impact"] = spec[3] if len(spec) > 3 else "byte-only"
            if kind == "default-correction":
                for k in DEFAULT_CORRECTION_EVIDENCE:
                    row[k] = "y"
            out.append(row)
        return normalize_dispositions(out)

    def _inv(*pairs):
        return [{"clause-id": cid, "canonical-text": text} for cid, text in pairs]

    # --- CLAUSE TEXT leg ---------------------------------------------------------------------------
    # removed with a tombstone: MAJOR event, no finding.
    reg = {"tombstone": [{"id": "calpha.1"}], "successor": []}
    ev, fs = clause_text_leg(_inv(("calpha.1", "x")), _inv(), reg, _rows(), "1.1.0")
    if fs or _floors(ev) != [("clause", "calpha.1", "removed", MAJOR)]:
        failures.append("clause removed-with-tombstone: expected a lone MAJOR event, no finding")
    # removed with no retirement row: MAJOR event AND a register-incompleteness finding.
    ev, fs = clause_text_leg(_inv(("calpha.1", "x")), _inv(), {"tombstone": [], "successor": []},
                             _rows(), "1.1.0")
    if not fs or _floors(ev) != [("clause", "calpha.1", "removed", MAJOR)]:
        failures.append("clause removed-no-row: expected MAJOR event and a finding")
    # added: MINOR.
    ev, fs = clause_text_leg(_inv(), _inv(("cbeta1.1", "y")), {"tombstone": [], "successor": []},
                             _rows(), "1.1.0")
    if fs or _floors(ev) != [("clause", "cbeta1.1", "added", MINOR)]:
        failures.append("clause added: expected a lone MINOR event")
    # same-id text change, behaviour-neutral row: PATCH.
    ev, fs = clause_text_leg(_inv(("c.1", "old")), _inv(("c.1", "new")), {"tombstone": [], "successor": []},
                             _rows(("behaviour-neutral", "c.1", "1.1.0")), "1.1.0")
    if fs or _floors(ev) != [("clause", "c.1", "behaviour-neutral", PATCH)]:
        failures.append("clause behaviour-neutral: expected PATCH")
    # same-id text change, strengthened row: MINOR.
    ev, fs = clause_text_leg(_inv(("c.1", "old")), _inv(("c.1", "new")), {"tombstone": [], "successor": []},
                             _rows(("strengthened", "c.1", "1.1.0")), "1.1.0")
    if fs or _floors(ev) != [("clause", "c.1", "strengthened", MINOR)]:
        failures.append("clause strengthened: expected MINOR")
    # same-id text change, default-correction row (evidence complete): MINOR.
    ev, fs = clause_text_leg(_inv(("c.1", "old")), _inv(("c.1", "new")), {"tombstone": [], "successor": []},
                             _rows(("default-correction", "c.1", "1.1.0")), "1.1.0")
    if fs or _floors(ev) != [("clause", "c.1", "default-correction", MINOR)]:
        failures.append("clause default-correction: expected MINOR")
    # same-id text change, NO disposition: undispositioned MAJOR floor (6.4 fail-closed).
    ev, fs = clause_text_leg(_inv(("c.1", "old")), _inv(("c.1", "new")), {"tombstone": [], "successor": []},
                             _rows(), "1.1.0")
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
    # strengthening with a row: MINOR event.
    ev, fs = ownership_leg({"x/y": "adopter-state"}, {"x/y": "pack-immutable"},
                           _rows(("class-change", "x/y", "1.1.0")), "1.1.0")
    if fs or ("ownership", "x/y", "strengthened", MINOR) not in _floors(ev):
        failures.append("ownership strengthening-with-row: expected a MINOR event, no finding")
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

    # --- take_row / disposition schema -------------------------------------------------------------
    try:
        take_row(_rows(("behaviour-neutral", "c.1", "1.1.0"), ("behaviour-neutral", "c.1", "1.1.0")),
                 "behaviour-neutral", "c.1", "1.1.0")
        failures.append("take_row: expected GateError on duplicate rows")
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

    # --- bump computation --------------------------------------------------------------------------
    if _claimed_rank("1.0.0", "1.0.1") != PATCH or _claimed_rank("1.0.0", "1.1.0") != MINOR \
            or _claimed_rank("1.0.0", "2.0.0") != MAJOR:
        failures.append("_claimed_rank: PATCH/MINOR/MAJOR mapping is wrong")
    try:
        _claimed_rank("1.1.0", "1.0.0")
        failures.append("_claimed_rank: expected GateError when head does not increase")
    except GateError:
        pass

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
        try:
            # (genesis) zero-row releases + manifest genesis=true -> exit 0.
            g = tmp / "genesis"
            (g / ".aiqt" / "core").mkdir(parents=True)
            (g / ".aiqt" / "manifest.toml").write_text("genesis = true\n", encoding="utf-8")
            for rel in (RELEASES_REL, ORDER_REL, RENDERERS_REL, CLAUSES_REL, DISPOSITIONS_REL):
                (g / rel).write_text("format-version = 1\n", encoding="utf-8")
            if _run_quiet_root(g) != 0:
                failures.append("genesis end-to-end: expected exit 0")

            # (genesis-mismatch) zero rows but manifest genesis not true -> exit 2.
            gm = tmp / "genesis-mismatch"
            (gm / ".aiqt" / "core").mkdir(parents=True)
            (gm / ".aiqt" / "manifest.toml").write_text("genesis = false\n", encoding="utf-8")
            for rel in (RELEASES_REL, ORDER_REL, RENDERERS_REL, CLAUSES_REL, DISPOSITIONS_REL):
                (gm / rel).write_text("format-version = 1\n", encoding="utf-8")
            if _run_quiet_root(gm) != 2:
                failures.append("genesis flag mismatch: expected fail-closed exit 2")

            # (unreachable predecessor) a one-row releases record whose commit cannot be resolved (the
            # tree is not a git repo) -> exit 2, never a silently conservative verdict.
            u = tmp / "unreachable"
            (u / ".aiqt" / "core").mkdir(parents=True)
            (u / ".aiqt" / "manifest.toml").write_text("genesis = false\n", encoding="utf-8")
            (u / RELEASES_REL).write_text(
                'format-version = 1\n\n[[release]]\nversion = "1.0.0"\ntag = "v1.0.0"\n'
                'tag_object_sha = "deadbeef"\ncommit_sha = "deadbeef"\n', encoding="utf-8")
            (u / CHANGELOG_REL).write_text(
                '[[release]]\nversion = "1.1.0"\n', encoding="utf-8")
            (u / DISPOSITIONS_REL).write_text("format-version = 1\n", encoding="utf-8")
            if _run_quiet_root(u) != 2:
                failures.append("unreachable predecessor: expected fail-closed exit 2")
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
            "the duplicate-row and malformed-disposition fail-closed cases, and the PATCH/MINOR/MAJOR "
            "bump computation")
    if e2e_ran:
        print("SELF-TEST PASS: {}; and the end-to-end genesis (exit 0), genesis-flag-mismatch (exit 2), "
              "and unreachable-predecessor (exit 2) cases hold".format(core))
    else:
        print("SELF-TEST PASS (PARTIAL): {}; the end-to-end cases were SKIPPED (no writable temp "
              "directory), so those paths are UNVERIFIED this run".format(core))
    return 0


def _parse_args(argv):
    opts = {"self_test": False, "repin": False, "target": None}
    i = 0
    while i < len(argv):
        if argv[i] == "--self-test":
            opts["self_test"] = True
            i += 1
        elif argv[i] == "--repin":
            opts["repin"] = True
            i += 1
        elif argv[i] == "--target" and i + 1 < len(argv):
            opts["target"] = argv[i + 1]
            i += 2
        else:
            print("usage: check_release_delta.py [--repin --target V] | --self-test", file=sys.stderr)
            return None
    return opts


def main():
    opts = _parse_args(sys.argv[1:])
    if opts is None:
        return 2
    if opts["self_test"]:
        return self_test_main()
    if opts["repin"]:
        # 10.4 adopter re-pin mode: the doctor packaging that drives it is adopter-experience-owned and
        # not part of this release. Rather than run a half-wired path, this fails closed with a clear
        # message (never a silent clean pass), so the mode is declared but does not falsely certify.
        print("error: --repin is the 10.4 adopter mode; its doctor packaging is adopter-experience-"
              "owned and not wired at this release; fail-closed", file=sys.stderr)
        return 2
    return run(repo_root())


if __name__ == "__main__":
    sys.exit(main())
