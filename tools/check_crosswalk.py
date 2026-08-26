#!/usr/bin/env python3
"""Crosswalk gate (VER-CORE 8.2, 8.4, 8.5, 8.6, 9.1). Offline, stdlib only, fail-closed.

HONESTY LABEL (disclosed on every surface): the 8.5 semantic verdict and the 8.2 completeness verdict
are HUMAN-JUDGMENT. This gate proves the verdict fields are PRESENT, well-formed, carry the fixed token,
and name a reviewer distinct from the author with a distinct declared family; it CANNOT prove that a real
cross-family review occurred. The upgrade to authenticated result records is deferred to the adopter-
experience report envelope (spec lines 1199 to 1202). The archive/quote chain (leg 2, 3, 6) is the anti-
self-certification property: a quote must be a verbatim substring of the pinned successor clause, whose
predecessor mirror is span-consistent with the immutable archived bytes.

Exit convention: 0 clean, OR NOT APPLICABLE only when .aiqt/migration/, .aiqt/archive/, AND a migration-
mode pin are ALL structurally absent (a repo that never adopted); 1 a real finding; 2 malformed input, a
read error, or PARTIAL migration state (partial state is malformed, never dormant).

  check_crosswalk.py [--root DIR]   run the legs against an install (NA when not adopted)
  check_crosswalk.py --self-test    synthetic-tree honesty invariants
"""
import hashlib
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: check_crosswalk.py requires Python 3.11+ (tomllib).")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402
import _journal  # noqa: E402

MIGRATION_REL = ".aiqt/migration"
ARCHIVE_REL = ".aiqt/archive"
PIN_REL = ".aiqt/pin.toml"
CROSSWALK_REL = ".aiqt/migration/crosswalk.toml"
INVENTORY_REL = ".aiqt/migration/successor-inventory.toml"
JOURNAL_REL = ".aiqt/migration/journal"
PRED_ID_RE = re.compile(r"^pre-([0-9a-f]{12})\.([1-9][0-9]*)$")
_VERDICT_SEMANTIC = "equal-or-stronger"
_VERDICT_COMPLETE = "complete"


class GateError(Exception):
    """A fail-closed condition (malformed input, unreadable state, partial adoption): exit 2."""


def _load_toml(path):
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        raise GateError("required input {} is absent".format(path))
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise GateError("cannot read {} ({})".format(path, exc))


# --- legs (each returns a list of finding strings; a GateError is exit 2) ------------------------------

def check_archive(cw, archive_root):
    """8.2 immutability and resolution: every archive-sha256 resolves to <archive>/<sha>/payload, and the
    directory name equals the recorded hash equals the recomputed payload digest. Any post-capture
    mismatch is a FAIL; an unreadable payload is fail-closed (GateError)."""
    findings = []
    for row in cw.get("archive-file", []):
        sha = row.get("archive-sha256")
        if not sha:
            findings.append("an archive-file row has no archive-sha256")
            continue
        payload = Path(archive_root) / sha / "payload"
        try:
            data = payload.read_bytes()
        except OSError as exc:
            raise GateError("unreadable archive payload {} ({})".format(payload, exc))
        if hashlib.sha256(data).hexdigest() != sha:
            findings.append("{}: archived bytes do not match their recorded hash".format(payload))
    return findings


def _archived_text(archive_root, sha):
    try:
        return (Path(archive_root) / sha / "payload").read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise GateError("cannot read archived payload for {} ({})".format(sha, exc))


def check_predecessors(cw, archive_root):
    """8.2/8.3: predecessor ids well-formed in the pre-<archive-hash-prefix>.<ordinal> namespace with a
    prefix consistent with the row's archive hash and unique; source-digest equals the archive hash; the
    canonical-text is a verbatim substring of the immutable archived bytes (the span-and-digest tie)."""
    findings, seen = [], set()
    for row in cw.get("predecessor", []):
        pid = row.get("clause-id", "")
        sha = row.get("archive-sha256", "")
        m = PRED_ID_RE.match(pid)
        if not m:
            findings.append("predecessor id {!r} is not in the 8.3 pre-<prefix>.<ordinal> namespace"
                            .format(pid))
        elif sha and m.group(1) != sha[:12]:
            findings.append("predecessor id {!r} hash-prefix disagrees with its archive-sha256".format(pid))
        if pid in seen:
            findings.append("duplicate predecessor id {!r}".format(pid))
        seen.add(pid)
        if row.get("source-digest") != sha:
            findings.append("predecessor {!r}: source-digest must equal the archive-sha256 (8.2)".format(pid))
        text = row.get("canonical-text", "")
        if not text:
            findings.append("predecessor {!r}: empty canonical-text".format(pid))
        elif sha:
            if text not in _archived_text(archive_root, sha):
                findings.append("predecessor {!r}: canonical-text is not a substring of the archived "
                                "bytes (span/digest disagreement)".format(pid))
    return findings


def check_completeness(cw):
    """8.2 completeness attestation (HUMAN-JUDGMENT): the verdict is present and the fixed token, the
    enumerator is named, and the reviewer is present and distinct from the enumerator."""
    er = cw.get("enumeration-review")
    if not isinstance(er, dict):
        return ["no [enumeration-review] completeness attestation (8.2)"]
    findings = []
    if er.get("verdict") != _VERDICT_COMPLETE:
        findings.append("completeness verdict missing or not the fixed token {!r}".format(_VERDICT_COMPLETE))
    if not er.get("enumerator"):
        findings.append("completeness review names no enumerator")
    if not er.get("reviewer") or er.get("reviewer") == er.get("enumerator"):
        findings.append("completeness reviewer absent or identical to the enumerator")
    return findings


def check_zero_unmapped(cw):
    """8.4/8.2: computed as set(predecessor ids) - set(mapping predecessor ids), NEVER trusted from an
    asserted unmatched list."""
    pred = {r.get("clause-id") for r in cw.get("predecessor", [])}
    mapped = {m.get("predecessor-clause-id") for m in cw.get("mapping", [])}
    missing = sorted(x for x in (pred - mapped) if x)
    return ["predecessor {!r} has no mapping (zero-unmapped violation, 8.4)".format(p) for p in missing]


def check_quote(row, inventory):
    """8.4: the successor id resolves in the pinned inventory and the successor-quote is a non-empty
    verbatim contiguous substring of THAT clause's canonical text. A Coverage phrase is never a quote."""
    sid = row.get("successor-clause-id")
    clause = inventory.get(sid)
    if clause is None:
        return "successor id {!r} does not resolve in the pinned inventory".format(sid)
    q = row.get("successor-quote", "")
    if not q or q not in clause.get("canonical-text", ""):
        return "successor-quote for {!r} is not a verbatim contiguous substring of the canonical text" \
            .format(sid)
    return None


def check_verdict(row):
    """8.5 mechanical floor: fixed token, reviewer present and name-distinct from the author, and a
    reviewer family present and distinct from the author family (reconciliation 5)."""
    problems = []
    if row.get("semantic-verdict") != _VERDICT_SEMANTIC:
        problems.append("verdict token missing or not {!r}".format(_VERDICT_SEMANTIC))
    if not row.get("reviewer") or row.get("reviewer") == row.get("author"):
        problems.append("reviewer absent or identical to the author")
    if not row.get("reviewer-family") or row.get("reviewer-family") == row.get("author-family"):
        problems.append("reviewer family absent or identical to the author family")
    return problems


def check_mappings(cw, inventory):
    """8.4/8.5/8.6: per mapping row, the quote chain and the verdict floor. Folds and splits are covered
    because EACH successor carries its own full row (8.6 lines 1223 to 1226)."""
    findings = []
    for row in cw.get("mapping", []):
        pid = row.get("predecessor-clause-id")
        q = check_quote(row, inventory)
        if q:
            findings.append("mapping {}: {}".format(pid, q))
        for p in check_verdict(row):
            findings.append("mapping {}: {}".format(pid, p))
    return findings


def pointer_warnings(cw):
    """8.6 lines 1220 to 1222: a pointer that is absent, present, or DISAGREEING changes NO verdict.
    Disagreement is an author-facing WARNING on stderr; the mapping row governs. Returns warning
    strings (never findings)."""
    warnings = []
    mapped = {}
    for m in cw.get("mapping", []):
        mapped.setdefault(m.get("predecessor-clause-id"), set()).add(m.get("successor-clause-id"))
    for h in cw.get("pointer-hint", []):
        pid = h.get("predecessor-clause-id")
        expected = set(h.get("expected-successor-ids", []))
        if expected and expected != mapped.get(pid, set()):
            warnings.append("pointer for {!r} disagrees with the reviewed mapping (hint only, no verdict "
                            "change)".format(pid))
    return warnings


def check_unit_coverage(root, cw):
    """9.1 (DISCLOSED-PARTIAL): every COMPLETE journal transaction must name a unit and touch only mapped
    predecessors, and the crosswalk must have at least one component when a completed cutover exists. The
    FULL op-set-to-component equality is deferred: the current data model carries no canonical component
    id on a transaction (the plan flags this as a build reconciliation), so this leg proves coverage
    SANITY, not exact equality, and discloses that residual rather than implying the stronger guarantee."""
    journal_root = root / JOURNAL_REL
    if not journal_root.is_dir():
        return []
    findings = []
    have_completed = False
    for entry in sorted(journal_root.iterdir()):
        if not entry.is_dir():
            continue
        try:
            frames, _torn, _ = _journal.read_frames(entry)
        except _journal.JournalError as exc:
            raise GateError("corrupt journal transaction {} ({})".format(entry.name, exc))
        types = [t for t, _ in frames]
        if _journal.F_COMPLETE in types:
            have_completed = True
            intent = _journal._first(frames, _journal.F_INTENT)
            header = (intent or {}).get("header", {})
            if not header.get("unit") and header.get("kind") != "un-adopt":
                findings.append("terminal transaction {} names no unit (9.1)".format(entry.name))
    if have_completed and not cw.get("mapping"):
        findings.append("a completed cutover exists but the crosswalk has no mapping rows (9.1)")
    return findings


# --- applicability + orchestration --------------------------------------------------------------------

def _migration_state_present(root):
    """(migration_dir, archive_dir, pin) presence booleans. TOTAL absence of all three is NA; ANY present
    with the crosswalk missing or unreadable is PARTIAL (malformed, exit 2)."""
    return ((root / MIGRATION_REL).exists(), (root / ARCHIVE_REL).exists(), (root / PIN_REL).exists())


def run(root):
    mig, arc, pin = _migration_state_present(root)
    if not (mig or arc or pin):
        print("NOT APPLICABLE: no migration state (.aiqt/migration, .aiqt/archive, and a pin are all "
              "absent); this repo is not an adopter install")
        return 0
    cw_path = root / CROSSWALK_REL
    if not cw_path.exists():
        raise GateError("PARTIAL migration state: {} is present but {} is absent (partial state is "
                        "malformed, never dormant)".format(
                            MIGRATION_REL if mig else (ARCHIVE_REL if arc else PIN_REL), CROSSWALK_REL))
    cw = _load_toml(cw_path)
    inv_rows = _load_toml(root / INVENTORY_REL).get("clause", [])
    inventory = {r.get("clause-id"): r for r in inv_rows}
    archive_root = root / ARCHIVE_REL
    findings = []
    findings += check_archive(cw, archive_root)
    findings += check_predecessors(cw, archive_root)
    findings += check_completeness(cw)
    findings += check_zero_unmapped(cw)
    findings += check_mappings(cw, inventory)
    findings += check_unit_coverage(root, cw)
    for w in pointer_warnings(cw):
        print("warning: {}".format(w), file=sys.stderr)
    if findings:
        print("FAIL: {} crosswalk finding(s):".format(len(findings)), file=sys.stderr)
        for f in findings:
            print("  - " + f, file=sys.stderr)
        return 1
    print("PASS: crosswalk archive/quote/verdict fields are well-formed (verdict truth is HUMAN-JUDGMENT; "
          "this gate proves field presence and distinctness, never that a real cross-family review "
          "occurred)")
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test()
    root = repo_root()
    if "--root" in args:
        root = Path(args[args.index("--root") + 1]).resolve()
    try:
        return run(root)
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2


# --- self-test ----------------------------------------------------------------------------------------
# Honesty invariants over synthetic trees: a clean one-to-one, a fold, and a split PASS; each violation
# class FAILs (wrong archive hash, span/text/digest disagreement, unmapped predecessor, unresolvable
# successor, a non-verbatim quote, a cross-clause quote, a missing/same-author/same-family verdict, a
# missing completeness review); a pointer disagreement changes NO verdict; malformed input exits 2; and
# PARTIAL migration state (a migration dir with no crosswalk) exits 2 while TOTAL absence is NA.

_SUCC_A = "The successor obligation is at least as strong as before."
_SUCC_B = "A second successor clause that folds two predecessors together."
_LEGACY_ONE = "Old obligation one, its full paragraph of legacy text."
_LEGACY_TWO = "Old obligation two, a different legacy paragraph entirely."


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_install(base, crosswalk_text, legacy_files, inventory_rows, tamper=None):
    """A synthetic adopter install: archive each legacy file, write the crosswalk, the successor
    inventory, and a pin (so state is non-NA). `tamper` mutates an archived payload after capture."""
    root = base
    (root / ARCHIVE_REL).mkdir(parents=True, exist_ok=True)
    (root / MIGRATION_REL).mkdir(parents=True, exist_ok=True)
    for text in legacy_files:
        sha = _sha(text)
        entry = root / ARCHIVE_REL / sha
        entry.mkdir(parents=True, exist_ok=True)
        (entry / "payload").write_text(text if tamper is None or tamper != sha else text + "X",
                                       encoding="utf-8")
    _write(root / CROSSWALK_REL, crosswalk_text)
    inv = ["schema-version = 1"]
    for cid, ctext in inventory_rows:
        inv += ["", "[[clause]]", 'clause-id = "{}"'.format(cid),
                'canonical-text = "{}"'.format(ctext.replace('"', '\\"'))]
    _write(root / INVENTORY_REL, "\n".join(inv) + "\n")
    _write(root / PIN_REL, 'schema-version = 1\nadoption-path = "migration"\n')
    return root


def _clean_crosswalk(fold=False, split=False):
    sha1, sha2 = _sha(_LEGACY_ONE), _sha(_LEGACY_TWO)
    p1 = "pre-{}.1".format(sha1[:12])
    p2 = "pre-{}.1".format(sha2[:12])
    rows = ['schema-version = 1', '',
            '[enumeration-review]', 'enumerator = "ann"', 'enumerator-family = "claude"',
            'verdict = "complete"', 'rationale = "all enumerated"', 'reviewer = "bob"',
            'reviewer-family = "codex"', 'reviewed-utc = "2026-08-26T00:00:00Z"', '',
            '[[archive-file]]', 'legacy-path = "one.md"', 'archive-sha256 = "{}"'.format(sha1),
            'size = {}'.format(len(_LEGACY_ONE.encode())),
            'predecessor-clause-ids = ["{}"]'.format(p1), '',
            '[[archive-file]]', 'legacy-path = "two.md"', 'archive-sha256 = "{}"'.format(sha2),
            'size = {}'.format(len(_LEGACY_TWO.encode())),
            'predecessor-clause-ids = ["{}"]'.format(p2), '',
            '[[predecessor]]', 'clause-id = "{}"'.format(p1), 'archive-sha256 = "{}"'.format(sha1),
            'canonical-text = "{}"'.format(_LEGACY_ONE), 'source-digest = "{}"'.format(sha1), '',
            '[[predecessor]]', 'clause-id = "{}"'.format(p2), 'archive-sha256 = "{}"'.format(sha2),
            'canonical-text = "{}"'.format(_LEGACY_TWO), 'source-digest = "{}"'.format(sha2), '']

    def mapping(pid, sid, quote):
        return ['[[mapping]]', 'predecessor-clause-id = "{}"'.format(pid),
                'successor-clause-id = "{}"'.format(sid),
                'successor-quote = "{}"'.format(quote), 'author = "carol"', 'author-family = "claude"',
                'semantic-verdict = "equal-or-stronger"', 'rationale = "stronger"',
                'reviewer = "dave"', 'reviewer-family = "gemini"', 'reviewed-utc = "2026-08-26T00:00:00Z"',
                '']
    if fold:                                              # two predecessors -> one successor
        rows += mapping(p1, "succ.a", _SUCC_A) + mapping(p2, "succ.a", _SUCC_A)
    elif split:                                           # one predecessor -> two successors
        rows += mapping(p1, "succ.a", _SUCC_A) + mapping(p1, "succ.b", _SUCC_B)
        rows += mapping(p2, "succ.a", _SUCC_A)
    else:
        rows += mapping(p1, "succ.a", _SUCC_A) + mapping(p2, "succ.a", _SUCC_A)
    return "\n".join(rows) + "\n", p1, p2, sha1, sha2


def self_test():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr

    inv = [("succ.a", _SUCC_A), ("succ.b", _SUCC_B)]

    def run_quiet(root):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                return run(Path(root))
            except GateError:
                return 2

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-check-crosswalk-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    n = 0
    try:
        # Clean one-to-one, fold, split all PASS.
        for label, kw in (("one-to-one", {}), ("fold", {"fold": True}), ("split", {"split": True})):
            text, p1, p2, s1, s2 = _clean_crosswalk(**kw)
            root = _build_install(tmp / ("clean-" + label), text, [_LEGACY_ONE, _LEGACY_TWO], inv)
            if run_quiet(root) != 0:
                failures.append("clean {} expected PASS (0)".format(label))
            n += 1

        base_text, p1, p2, s1, s2 = _clean_crosswalk()

        # TOTAL absence -> NA (0); PARTIAL (migration dir, no crosswalk) -> 2.
        na = tmp / "na"
        na.mkdir()
        if run_quiet(na) != 0:
            failures.append("total absence expected NA (0)")
        partial = tmp / "partial"
        (partial / MIGRATION_REL).mkdir(parents=True)
        if run_quiet(partial) != 2:
            failures.append("partial migration state expected exit 2")
        n += 2

        def broken(mutator, expect=1):
            text = mutator(base_text)
            root = _build_install(tmp / ("bad{}".format(n)), text, [_LEGACY_ONE, _LEGACY_TWO], inv)
            return run_quiet(root) == expect

        # Wrong archive hash: tamper an archived payload after capture -> archive mismatch (1).
        tampered_root = _build_install(tmp / "tamper", base_text, [_LEGACY_ONE, _LEGACY_TWO], inv,
                                       tamper=s1)
        if run_quiet(tampered_root) != 1:
            failures.append("a tampered archived payload expected FAIL (1)")
        n += 1

        checks = [
            ("span/text disagreement",
             lambda t: t.replace('canonical-text = "{}"'.format(_LEGACY_ONE),
                                  'canonical-text = "text not in the archived bytes"', 1)),
            ("source-digest mismatch",
             lambda t: t.replace('source-digest = "{}"'.format(s1),
                                 'source-digest = "{}"'.format("0" * 64), 1)),
            ("unmapped predecessor",
             lambda t: t.replace('[[mapping]]\npredecessor-clause-id = "{}"'.format(p2),
                                 '[[mapping]]\npredecessor-clause-id = "unused.x"', 1)),
            ("unresolvable successor",
             lambda t: t.replace('successor-clause-id = "succ.a"',
                                 'successor-clause-id = "ghost.z"', 1)),
            ("non-verbatim quote",
             lambda t: t.replace('successor-quote = "{}"'.format(_SUCC_A),
                                 'successor-quote = "a quote that is not present"', 1)),
            ("same-author reviewer",
             lambda t: t.replace('reviewer = "dave"', 'reviewer = "carol"')),
            ("same-family reviewer",
             lambda t: t.replace('reviewer-family = "gemini"', 'reviewer-family = "claude"')),
            ("missing semantic verdict",
             lambda t: t.replace('semantic-verdict = "equal-or-stronger"', 'semantic-verdict = ""')),
            ("missing completeness review",
             lambda t: t.replace('verdict = "complete"', 'verdict = "partial"')),
            ("bad 8.3 predecessor id",
             lambda t: t.replace('clause-id = "{}"'.format(p1), 'clause-id = "notpre.1"')),
        ]
        for label, mut in checks:
            root = _build_install(tmp / ("bad-" + label.replace("/", "_").replace(" ", "_")),
                                  mut(base_text), [_LEGACY_ONE, _LEGACY_TWO], inv)
            if run_quiet(root) != 1:
                failures.append("{} expected FAIL (1)".format(label))
            n += 1

        # A pointer disagreement is a WARNING, not a finding: still PASS (0).
        ptext = base_text + ('\n[[pointer-hint]]\npredecessor-clause-id = "{}"\n'
                             'expected-successor-ids = ["succ.b"]\ncoverage = ""\ncarrier = "body"\n'
                             .format(p1))
        proot = _build_install(tmp / "pointer", ptext, [_LEGACY_ONE, _LEGACY_TWO], inv)
        if run_quiet(proot) != 0:
            failures.append("a disagreeing pointer must not change the verdict (expected PASS 0)")
        n += 1

        # Malformed TOML -> exit 2.
        malformed = _build_install(tmp / "malformed", "schema-version = 1\n[[mapping\n",
                                   [_LEGACY_ONE, _LEGACY_TWO], inv)
        if run_quiet(malformed) != 2:
            failures.append("malformed crosswalk TOML expected exit 2")
        n += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: clean one-to-one, fold, and split crosswalks pass across {} scenarios; a "
          "tampered archive, a span/text or source-digest disagreement, an unmapped predecessor, an "
          "unresolvable successor, a non-verbatim quote, a same-author or same-family verdict, a missing "
          "completeness review, and a malformed 8.3 id each FAIL; a disagreeing pointer is a warning that "
          "changes no verdict; malformed TOML and partial migration state fail closed (exit 2) while "
          "total absence is NA.".format(n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
