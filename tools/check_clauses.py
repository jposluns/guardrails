#!/usr/bin/env python3
"""Clause-inventory and identifier-stability gate for the pack (VER-CORE Section 7). Offline, stdlib
only, fail-closed.

gen_rules.py owns corpus-id uniqueness and frontmatter shape; check_version_monotonicity.py owns the
SemVer release history. This gate owns the CLAUSE layer: it verifies the per-release clause inventory
(`.aiqt/core/clauses.toml`) and the cumulative id-history register (`.aiqt/core/id-history.toml`) against
the rule sources under `.aiqt/core/rules/`, enforcing the 7.1 clause-id scheme, the 7.3 cumulative
resurrection and completeness rules, and the 7.2 per-row span/text/digest integrity.

SCHEMAS this gate reads (proposed here for VER-CORE Section 7; the orchestrator finalizes them against
gen_manifest.py at build time).

  `.aiqt/core/clauses.toml` (7.2), the per-release inventory, author-maintained, manifest-covered. An
  array of `[[clause]]` tables, one per live normative obligation this release:
    clause-id       = "<corpus-id>.<ordinal>"   the stable clause identifier (7.1)
    corpus-id       = "<corpus-id>"             the owning rule's frontmatter corpus-id
    source-path     = "repo/relative/path.md"   the rule SOURCE file (under .aiqt/core/rules/)
    start-line      = <int>                      1-based first line of the clause span in that file
    end-line        = <int>                      1-based last line of the clause span (>= start-line)
    canonical-text  = "..."                      the span's content verbatim, canonical bytes (3.1)
    source-digest   = "<64 hex>"                 SHA-256 of the WHOLE rule source file's raw bytes (3.2)
  The span content is defined as the source file's lines[start-line-1 : end-line] joined with LF, with no
  trailing newline; canonical-text must equal it exactly. (3.1 canonicalization of the source file itself
  is check_byte_canon.py's job, not re-scanned here.)

  `.aiqt/core/id-history.toml` (7.3), the pack-owned, append-only, cumulative register of every corpus-id
  AND clause-id ever assigned. Three arrays:
    [[born]]       id = "<id>"  born-release    = "<semver>"                       one per id ever assigned
    [[tombstone]]  id = "<id>"  retired-release = "<semver>"                       a permanent retirement
    [[successor]]  id = "<id>"  retired-release = "<semver>"  successor-id = "<id>"  a rename/fold/split
  An id is a corpus-id (no dot) or a clause-id (exactly one dot). Tombstones and successors are BOTH
  retirement rows; a retired id never returns to a live inventory (resurrection).

LEGS (all run at the default step-1 invocation; none needs a manifest):
  ID SCHEME (7.1):      every rule corpus-id under rules-dir appears in the inventory; every clause-id is
                        `<corpus-id>.<ordinal>` with an UNPADDED positive-decimal ordinal (no leading
                        zero); the row's corpus-id matches the frontmatter corpus-id of its source file;
                        no duplicate clause-id. Ordinals are compared NUMERICALLY, never lexicographically.
  PER-ROW (7.2):        the span resolves inside the named file; the canonical text is byte-identical to
                        the span content; the source digest is the SHA-256 of the whole source file. Any
                        disagreement among span/text/digest in any pairing is a FAIL.
  CUMULATIVE-MAX (7.1): each ordinal newly assigned this release (a born row at the newest release in the
                        register) strictly EXCEEDS the cumulative maximum ordinal EVER used for that
                        corpus-id, taken from the register's born rows (which persist for dead ids too), so
                        a dead ordinal's gap can never be refilled below the historical maximum (m-A).
  RESURRECTION (7.3):   the cumulative ever-retired id set is built FROM THE REGISTER; no id the inventory
                        asserts live (a clause-id or its corpus-id) equals any tombstoned or retired id.
  COMPLETENESS (7.3):   every id the inventory introduces has a born row; every retirement row's id has a
                        born row; no id carries more than one retirement row; a retired id is absent from
                        the live inventory. With --prev-inventory, the cross-release leg additionally
                        requires every id that DISAPPEARED since the predecessor inventory to carry exactly
                        one retirement row (zero unexplained disappearances). Without a predecessor that
                        cross-release leg reports NOT APPLICABLE (genesis / step-1 baseline).

DEFERRED manifest-SOURCES leg (7.2 + round-10 R10-2 / gemini R9-4): the leg that cross-checks each row's
source-digest against the manifest's SOURCES entry is OFF by default and reads no manifest. It runs only
when --with-manifest (or --manifest PATH) is passed, so a later build step can arm it AFTER gen_manifest.py
regenerates the release's OWN manifest. Running it at step 1 would compare an edited rule's new digest
against a PRIOR release's stale in-tree manifest and mis-fire. The assumed manifest shape is an array of
`[[sources]]` tables carrying `path` and `sha256`; the orchestrator reconciles this with gen_manifest.py.

GENESIS (2.5): at genesis there is no predecessor inventory and no tombstone; --genesis asserts that (the
register carries born rows only and covers the whole inventory) and passes cleanly. Absent --genesis and
--prev-inventory, the gate runs the same baseline with the cross-release disappearance leg NOT APPLICABLE.

RESIDUAL, disclosed (disclose-guard-residuals): this gate reads the register and the inventory in the
working tree; it does NOT here verify the register's append-only property across git history (that is the
prefix-identity discipline of check_version_monotonicity.py extended to the register, 2.4/7.3) nor that a
born row's release equals the release under build. A row mis-dated to an old release is not caught by the
in-file legs alone; the cross-history and manifest legs cover it at the release gate.

  check_clauses.py [--root DIR] [--rules-dir DIR] [--inventory FILE] [--register FILE]
                   [--prev-inventory FILE] [--genesis] [--with-manifest | --manifest FILE]
  check_clauses.py --self-test    deterministic self-test (synthetic fixtures in a private tempdir)

Exit convention (matches the repo's gates):
  0  clean, or a printed NOT APPLICABLE
  1  a real finding (a scheme, per-row, cumulative-max, resurrection, or completeness violation, or an
     inventory row naming a missing or non-UTF-8 source file, which is exactly what this gate asserts against)
  2  malformed or unreadable required input (the inventory, the register, the rules dir, an unparseable
     source, or a permission error), fail-closed: an input the gate cannot read never reads as clean.
"""
import hashlib
import io
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: check_clauses.py requires Python 3.11+ (tomllib).")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, load_toml  # noqa: E402
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)
from check_versions import _parse as _semver  # noqa: E402  reuse the shipped bare-SemVer parser
from gen_rules import parse_source, CID_RE  # noqa: E402  reuse the frontmatter parser and corpus-id regex

ORDINAL_RE = re.compile(r"^[1-9][0-9]*$")  # unpadded positive decimal: no leading zero, no sign, no zero


class GateError(Exception):
    """An input the gate cannot read, parse, or resolve. Caught at run() and reported as exit 2
    (fail-closed): an unreadable or unparseable required input is never treated as an empty or clean
    result."""


# --- pure logic (clause-id parsing, ordinal comparison, register shape; always run in --self-test) ----

def split_clause_id(clause_id):
    """Split a clause-id into (corpus-id, ordinal_int), or return None if it is not a well-formed
    clause-id. A corpus-id is `[a-z0-9]{6,}` with no dot, so a clause-id carries exactly one dot; the
    ordinal is an UNPADDED positive decimal compared NUMERICALLY. A padded ordinal ('01'), a zero, a
    sign, extra dots, or a bad corpus-id all return None (the caller reports the scheme finding)."""
    if not isinstance(clause_id, str) or clause_id.count(".") != 1:
        return None
    corpus, _dot, ordinal = clause_id.partition(".")
    if not CID_RE.match(corpus) or not ORDINAL_RE.match(ordinal):
        return None
    return corpus, int(ordinal)


def is_clause_id(any_id):
    """True if any_id parses as a well-formed clause-id (has an ordinal); False for a bare corpus-id or a
    malformed token. Used to pick clause-ids out of the mixed register id space for the ordinal legs."""
    return split_clause_id(any_id) is not None


def check_scheme(rows, rule_corpus_ids):
    """ID SCHEME (7.1). rows is a list of {'clause-id','corpus-id',...} dicts; rule_corpus_ids is the set
    of corpus-ids found in the rule sources. Returns a list of finding strings. Ordinals are validated by
    ORDINAL_RE (unpadded positive decimal) and never compared as text."""
    findings = []
    seen = set()
    inventory_corpus_ids = set()
    for i, row in enumerate(rows, 1):
        cid = row.get("clause-id")
        corpus = row.get("corpus-id")
        parsed = split_clause_id(cid)
        if parsed is None:
            findings.append("clause row #{}: clause-id {!r} is not <corpus-id>.<ordinal> with an unpadded "
                            "positive-decimal ordinal".format(i, cid))
            continue
        parsed_corpus, _ordinal = parsed
        if not isinstance(corpus, str) or corpus != parsed_corpus:
            findings.append("clause row #{}: corpus-id {!r} does not match the clause-id's corpus part "
                            "{!r}".format(i, corpus, parsed_corpus))
        if cid in seen:
            findings.append("clause row #{}: duplicate clause-id {!r}".format(i, cid))
        seen.add(cid)
        inventory_corpus_ids.add(parsed_corpus)
    for corpus in sorted(rule_corpus_ids - inventory_corpus_ids):
        findings.append("rule corpus-id {!r} has no clause row in the inventory".format(corpus))
    return findings


def cumulative_max_before(born_clause_ids, current_release):
    """Return {corpus-id: max ordinal ever assigned STRICTLY BEFORE current_release}. born_clause_ids is a
    list of (clause-id, born-release-tuple); current_release is a SemVer tuple. Ordinals compared
    numerically. Born rows persist for dead ids, so this includes tombstoned ordinals (m-A)."""
    prior = {}
    for clause_id, born in born_clause_ids:
        if born >= current_release:
            continue
        corpus, ordinal = split_clause_id(clause_id)
        if corpus not in prior or ordinal > prior[corpus]:
            prior[corpus] = ordinal
    return prior


def check_cumulative_max(born_clause_ids, current_release):
    """CUMULATIVE-MAX (7.1 / m-A). Each clause-id born AT current_release must have an ordinal strictly
    greater than the cumulative maximum ordinal ever used for its corpus-id before this release. Returns a
    list of finding strings. Inputs are (clause-id, born-release-tuple) pairs and a SemVer tuple."""
    findings = []
    prior = cumulative_max_before(born_clause_ids, current_release)
    for clause_id, born in born_clause_ids:
        if born != current_release:
            continue
        corpus, ordinal = split_clause_id(clause_id)
        ceiling = prior.get(corpus)
        if ceiling is not None and ordinal <= ceiling:
            findings.append("clause-id {!r} (ordinal {}) does not exceed the cumulative maximum ordinal {} "
                            "ever used for corpus-id {!r} (a dead ordinal's gap must not be refilled below "
                            "the historical maximum)".format(clause_id, ordinal, ceiling, corpus))
    return findings


def check_resurrection(inventory_ids, retired_ids):
    """RESURRECTION (7.3). inventory_ids is the set of ids the inventory asserts live (each clause-id and
    its corpus-id); retired_ids is the cumulative tombstoned/retired set from the register. Any overlap is
    a FAIL. Returns a list of finding strings."""
    findings = []
    for rid in sorted(inventory_ids & retired_ids):
        findings.append("id {!r} is live in the inventory but is tombstoned/retired in the id-history "
                        "register (resurrection of a dead id is forbidden)".format(rid))
    return findings


def check_completeness(inventory_ids, born_ids, retired_rows, live_ids, prev_ids):
    """COMPLETENESS (7.3). born_ids is the set with a born row; retired_rows is {id: count of retirement
    rows}; live_ids is the set of ids the inventory asserts live; prev_ids is the predecessor inventory id
    set or None. Returns (findings, na) where na is True when the cross-release disappearance leg did not
    run (no predecessor)."""
    findings = []
    for iid in sorted(inventory_ids - born_ids):
        findings.append("id {!r} appears in the inventory but has no born row in the register".format(iid))
    for rid, count in sorted(retired_rows.items()):
        if rid not in born_ids:
            findings.append("id {!r} has a retirement row but no born row in the register".format(rid))
        if count > 1:
            findings.append("id {!r} carries {} retirement rows; an id retires exactly once".format(rid, count))
        if rid in live_ids:
            findings.append("id {!r} is retired in the register but still live in the inventory".format(rid))
    na = prev_ids is None
    if not na:
        for gone in sorted(prev_ids - live_ids):
            if retired_rows.get(gone, 0) == 0:
                findings.append("id {!r} disappeared since the predecessor inventory with no tombstone or "
                                "successor row (unexplained disappearance)".format(gone))
    return findings, na


# --- register and inventory loading -----------------------------------------------------------------

def _require_str(table, key, where):
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GateError("{}: missing or non-string {!r}".format(where, key))
    return value


def _require_release(table, key, where):
    value = _require_str(table, key, where)
    if _semver(value) is None:
        raise GateError("{}: {!r} {!r} is not a bare SemVer".format(where, key, value))
    return value


def load_register(path):
    """Parse the id-history register. Returns (born, retired_rows, retired_ids, born_clause_ids, newest):
    born {id: born-release-str}, retired_rows {id: count}, retired_ids set, born_clause_ids list of
    (clause-id, born-release-tuple) for the ordinal legs, and newest the max SemVer over every born and
    retired release (the release under build) or None on an empty register. Fail-closed on any malformed
    row (an unreadable register must never read as an empty history)."""
    try:
        data = load_toml(path)
    except (OSError, ValueError) as exc:
        raise GateError("cannot read the id-history register {} ({})".format(path, exc))
    born, releases = {}, []
    for row in data.get("born", []):
        if not isinstance(row, dict):
            raise GateError("id-history born row is not a table: {!r}".format(row))
        rid = _require_str(row, "id", "id-history born row")
        rel = _require_release(row, "born-release", "id-history born row {!r}".format(rid))
        if rid in born:
            raise GateError("id-history: duplicate born row for {!r}".format(rid))
        born[rid] = rel
        releases.append(rel)
    retired_rows, retired_ids = {}, set()
    for kind in ("tombstone", "successor"):
        for row in data.get(kind, []):
            if not isinstance(row, dict):
                raise GateError("id-history {} row is not a table: {!r}".format(kind, row))
            rid = _require_str(row, "id", "id-history {} row".format(kind))
            rel = _require_release(row, "retired-release", "id-history {} row {!r}".format(kind, rid))
            if kind == "successor":
                _require_str(row, "successor-id", "id-history successor row {!r}".format(rid))
            retired_rows[rid] = retired_rows.get(rid, 0) + 1
            retired_ids.add(rid)
            releases.append(rel)
    born_clause_ids = [(rid, _semver(rel)) for rid, rel in born.items() if is_clause_id(rid)]
    newest = max((_semver(r) for r in releases), default=None)
    return born, retired_rows, retired_ids, born_clause_ids, newest


def _clause_rows(data, where):
    rows = data.get("clause")
    if not isinstance(rows, list):
        raise GateError("{}: no [[clause]] array".format(where))
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise GateError("{}: clause row #{} is not a table ({!r})".format(where, i, row))
    return rows


def load_inventory(path):
    """Parse the clause inventory into its list of row tables. Fail-closed on an unreadable file or a
    missing [[clause]] array."""
    try:
        data = load_toml(path)
    except (OSError, ValueError) as exc:
        raise GateError("cannot read the clause inventory {} ({})".format(path, exc))
    return _clause_rows(data, "clause inventory {}".format(path))


def load_prev_ids(path):
    """Read ONLY the id set (each clause-id and its corpus-id) from a predecessor inventory, for the
    cross-release disappearance leg. Spans and digests of a past release are not re-validated (they point
    at past sources this tree may not carry). Fail-closed on an unreadable file."""
    try:
        data = load_toml(path)
    except (OSError, ValueError) as exc:
        raise GateError("cannot read the predecessor inventory {} ({})".format(path, exc))
    ids = set()
    for row in _clause_rows(data, "predecessor inventory {}".format(path)):
        cid = row.get("clause-id")
        if isinstance(cid, str):
            ids.add(cid)
            parsed = split_clause_id(cid)
            if parsed is not None:
                ids.add(parsed[0])
    return ids


def load_rule_corpus_ids(rules_dir):
    """Return {corpus-id: source Path} for every rule source under rules_dir, using gen_rules.parse_source
    for the frontmatter. Fail-closed (GateError) on an unlistable dir or a source whose frontmatter has no
    valid corpus-id: an unreadable corpus must never read as an empty set of rules."""
    try:
        sources = sorted(walk_files(rules_dir, {".git", "__pycache__"}, suffixes={".md"}))
    except OSError as exc:
        raise GateError("cannot list the rules dir {} ({})".format(rules_dir, exc))
    out = {}
    for src in sources:
        try:
            fm = parse_source(src)
        except (OSError, ValueError) as exc:
            raise GateError("cannot parse rule source {} ({})".format(src, exc))
        corpus = fm.get("corpus-id")
        if not isinstance(corpus, str) or not CID_RE.match(corpus):
            raise GateError("rule source {} has a missing or malformed corpus-id".format(src))
        if corpus in out:
            raise GateError("rule corpus-id {!r} used by both {} and {}".format(corpus, out[corpus], src))
        out[corpus] = src
    return out


# --- per-row span / text / digest (7.2 in-file legs) ------------------------------------------------

def _span_content(text, start, end):
    """The source file's lines[start-1:end] joined with LF, no trailing newline. Returns None if the span
    does not resolve inside the file (start < 1, end < start, or end past the last content line). A
    canonical file ends in exactly one LF, so split('\\n') yields a trailing '' that is not a content
    line; content lines are 1..len-1."""
    lines = text.split("\n")
    content_lines = len(lines) - 1 if lines and lines[-1] == "" else len(lines)
    if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start or end > content_lines:
        return None
    return "\n".join(lines[start - 1:end])


def check_rows(root, rows, manifest_sources):
    """PER-ROW (7.2). For each row: the source file is read (its whole raw bytes hashed for the digest and
    its text sliced for the span); the span resolves inside the file; canonical-text equals the span
    content byte-for-byte; source-digest equals the file digest; the row's corpus-id equals the source
    file's frontmatter corpus-id. When manifest_sources is not None (the DEFERRED leg armed) the digest is
    also cross-checked against the manifest's SOURCES entry. Returns a list of finding strings; a required
    field of the wrong type is a GateError (fail-closed)."""
    findings = []
    digest_cache = {}
    for i, row in enumerate(rows, 1):
        where = "clause row #{} ({})".format(i, row.get("clause-id"))
        source_path = row.get("source-path")
        text_field = row.get("canonical-text")
        digest_field = row.get("source-digest")
        start, end = row.get("start-line"), row.get("end-line")
        if not isinstance(source_path, str) or not source_path.strip():
            raise GateError("{}: missing or non-string source-path".format(where))
        if not isinstance(text_field, str):
            raise GateError("{}: missing or non-string canonical-text".format(where))
        if not isinstance(digest_field, str):
            raise GateError("{}: missing or non-string source-digest".format(where))
        abs_path = root / source_path
        if source_path not in digest_cache:
            try:
                raw = abs_path.read_bytes()
            except FileNotFoundError:
                digest_cache[source_path] = ("missing", None, None)
            except OSError as exc:  # a permission or I/O error is environmental: fail closed
                raise GateError("{}: cannot read source file {} ({})".format(where, source_path, exc))
            else:
                try:
                    decoded = raw.decode("utf-8")
                except UnicodeDecodeError:
                    digest_cache[source_path] = ("non-utf8", None, None)
                else:
                    digest_cache[source_path] = ("ok", hashlib.sha256(raw).hexdigest(), decoded)
        status, digest, decoded = digest_cache[source_path]
        if status == "missing":
            findings.append("{}: source file {} does not exist".format(where, source_path))
            continue
        if status == "non-utf8":
            findings.append("{}: source file {} is not valid UTF-8".format(where, source_path))
            continue
        span = _span_content(decoded, start, end)
        if span is None:
            findings.append("{}: source span (start-line {!r}, end-line {!r}) does not resolve inside {}"
                            .format(where, start, end, source_path))
        elif span != text_field:
            findings.append("{}: canonical-text does not match the source span content in {}"
                            .format(where, source_path))
        if digest_field != digest:
            findings.append("{}: source-digest does not match the SHA-256 of {}".format(where, source_path))
        try:
            frontmatter = parse_source(abs_path)
            fm_corpus = frontmatter.get("corpus-id")
        except (OSError, ValueError):
            fm_corpus = None
        if fm_corpus != row.get("corpus-id"):
            findings.append("{}: source file {} frontmatter corpus-id {!r} does not match the row's "
                            "corpus-id {!r}".format(where, source_path, fm_corpus, row.get("corpus-id")))
        if manifest_sources is not None:
            expected = manifest_sources.get(source_path)
            if expected is None:
                findings.append("{}: source file {} has no SOURCES entry in the manifest".format(where, source_path))
            elif expected != digest_field:
                findings.append("{}: source-digest disagrees with the manifest SOURCES digest for {}"
                                .format(where, source_path))
    return findings


def load_manifest_sources(path):
    """The DEFERRED leg's input: {repo-relative path: sha256} from the manifest's SOURCES section (assumed
    shape: an array of `[[sources]]` tables with `path` and `sha256`). Fail-closed on an unreadable or
    malformed manifest. Reconciled with gen_manifest.py at finalize."""
    try:
        data = load_toml(path)
    except (OSError, ValueError) as exc:
        raise GateError("cannot read the manifest {} ({})".format(path, exc))
    sources = data.get("sources")
    if not isinstance(sources, list):
        raise GateError("manifest {}: no [[sources]] array".format(path))
    out = {}
    for row in sources:
        if not isinstance(row, dict):
            raise GateError("manifest {}: a sources row is not a table".format(path))
        out[_require_str(row, "path", "manifest sources row")] = _require_str(row, "sha256", "manifest sources row")
    return out


# --- orchestration ----------------------------------------------------------------------------------

def run(root, rules_dir, inventory_path, register_path, prev_inventory_path=None, genesis=False,
        manifest_path=None):
    """Run every leg. Returns the exit code 0/1/2. Parameterized on paths so the self-test drives synthetic
    tempdir fixtures, never the real corpus."""
    try:
        rule_corpus_ids = load_rule_corpus_ids(rules_dir)
        rows = load_inventory(inventory_path)
        born, retired_rows, retired_ids, born_clause_ids, newest = load_register(register_path)
        prev_ids = load_prev_ids(prev_inventory_path) if prev_inventory_path is not None else None
        manifest_sources = load_manifest_sources(manifest_path) if manifest_path is not None else None

        if genesis:
            if prev_inventory_path is not None:
                raise GateError("--genesis and --prev-inventory are mutually exclusive: genesis has no "
                                "predecessor inventory")
            if retired_ids:
                raise GateError("--genesis declared but the id-history register carries {} retirement "
                                "row(s); genesis has no tombstones yet".format(len(retired_ids)))

        live_ids, inventory_ids = set(), set()
        for row in rows:
            cid = row.get("clause-id")
            if isinstance(cid, str):
                live_ids.add(cid)
                inventory_ids.add(cid)
                parsed = split_clause_id(cid)
                if parsed is not None:
                    live_ids.add(parsed[0])
                    inventory_ids.add(parsed[0])
            corpus = row.get("corpus-id")
            if isinstance(corpus, str):
                inventory_ids.add(corpus)

        findings = []
        findings += check_scheme(rows, set(rule_corpus_ids))
        findings += check_rows(root, rows, manifest_sources)
        if newest is not None:
            findings += check_cumulative_max(born_clause_ids, newest)
        findings += check_resurrection(inventory_ids, retired_ids)
        completeness, na = check_completeness(inventory_ids, set(born), retired_rows, live_ids, prev_ids)
        findings += completeness
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2

    print("checked {} clause row(s) against {} rule source(s); cross-release disappearance leg {}; "
          "manifest-SOURCES leg {}".format(
              len(rows), len(rule_corpus_ids),
              "NOT APPLICABLE (no predecessor inventory)" if na else "ran",
              "armed" if manifest_path is not None else "deferred (off)"))
    if findings:
        print("FAIL: {} clause-inventory finding(s)".format(len(findings)))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    print("PASS: clause-id scheme, per-row span/text/digest, cumulative-max ordinal, resurrection, and "
          "completeness all hold")
    return 0


# --- self-test --------------------------------------------------------------------------------------
# Pure-logic cases (clause-id parsing, numeric ordinal comparison, the completeness/resurrection set
# logic) always run and are deterministic. The end-to-end fixture cases build a synthetic corpus in a
# private tempdir and are skipped with a printed note (never a false pass) where no writable tempdir
# exists. No wall clock, no randomness, no network.

def _canon(lines):
    """A canonical file body: LF-joined lines with exactly one trailing newline (3.1)."""
    return "\n".join(lines) + "\n"


def _rule_lines(corpus_id, obligations):
    """A tiny canonical rule source: frontmatter carrying corpus-id, then one body line per obligation.
    Returns (lines, {ordinal_index -> 1-based body line number}). Obligations are placed on their own
    lines so each clause span is a single line, keeping fixtures trivially verifiable."""
    lines = ["---", "corpus-id: {}".format(corpus_id), "origin: pack", "slug: fixture", "---", "",
             "# Fixture rule"]
    line_of = {}
    for idx, text in enumerate(obligations):
        lines.append(text)
        line_of[idx] = len(lines)  # 1-based line number of this obligation
    return lines, line_of


def _toml_clause(row):
    out = ["[[clause]]",
           'clause-id = "{}"'.format(row["clause-id"]),
           'corpus-id = "{}"'.format(row["corpus-id"]),
           'source-path = "{}"'.format(row["source-path"]),
           "start-line = {}".format(row["start-line"]),
           "end-line = {}".format(row["end-line"]),
           'canonical-text = "{}"'.format(row["canonical-text"]),
           'source-digest = "{}"'.format(row["source-digest"]),
           ""]
    return "\n".join(out)


def _toml_register(born, tombstones, successors):
    out = []
    for rid, rel in born:
        out += ["[[born]]", 'id = "{}"'.format(rid), 'born-release = "{}"'.format(rel), ""]
    for rid, rel in tombstones:
        out += ["[[tombstone]]", 'id = "{}"'.format(rid), 'retired-release = "{}"'.format(rel), ""]
    for rid, rel, succ in successors:
        out += ["[[successor]]", 'id = "{}"'.format(rid), 'retired-release = "{}"'.format(rel),
                'successor-id = "{}"'.format(succ), ""]
    return "\n".join(out)


def _sha_of(lines):
    return hashlib.sha256(_canon(lines).encode("utf-8")).hexdigest()


def _write_corpus(base, rules, clause_rows, born, tombstones, successors):
    """Write a synthetic corpus under base: rule sources under .aiqt/core/rules/, the inventory
    .aiqt/core/clauses.toml, and the register .aiqt/core/id-history.toml. rules maps corpus-id -> lines;
    clause_rows is the list of already-built clause-row dicts. Returns base."""
    rules_dir = base / ".aiqt" / "core" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    for corpus_id, lines in rules.items():
        (rules_dir / "{}.md".format(corpus_id)).write_text(_canon(lines), encoding="utf-8")
    (base / ".aiqt" / "core" / "clauses.toml").write_text(
        "".join(_toml_clause(r) + "\n" for r in clause_rows), encoding="utf-8")
    (base / ".aiqt" / "core" / "id-history.toml").write_text(
        _toml_register(born, tombstones, successors), encoding="utf-8")
    return base


def _good_genesis():
    """A well-formed genesis corpus description: two rules, two clauses each, all born at genesis 1.1.0,
    no tombstones. Returns (rules, clause_rows, born) with real spans and digests."""
    rules, clause_rows, born = {}, [], []
    plan = {"calpha": ["alpha obligation one", "alpha obligation two"],
            "cbeta1": ["beta obligation one", "beta obligation two"]}
    for corpus_id, obligations in plan.items():
        lines, line_of = _rule_lines(corpus_id, obligations)
        rules[corpus_id] = lines
        digest = _sha_of(lines)
        born.append((corpus_id, "1.1.0"))
        for idx, text in enumerate(obligations):
            ordinal = idx + 1
            clause_id = "{}.{}".format(corpus_id, ordinal)
            ln = line_of[idx]
            clause_rows.append({"clause-id": clause_id, "corpus-id": corpus_id,
                                "source-path": ".aiqt/core/rules/{}.md".format(corpus_id),
                                "start-line": ln, "end-line": ln, "canonical-text": text,
                                "source-digest": digest})
            born.append((clause_id, "1.1.0"))
    return rules, clause_rows, born


def _run_quiet(**kwargs):
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return run(**kwargs)


def _paths(base, **overrides):
    kw = {"root": base, "rules_dir": base / ".aiqt" / "core" / "rules",
          "inventory_path": base / ".aiqt" / "core" / "clauses.toml",
          "register_path": base / ".aiqt" / "core" / "id-history.toml"}
    kw.update(overrides)
    return kw


def self_test_main():  # noqa: C901  a flat sequence of independent fixture cases, one per fail mode
    failures = []

    # --- always-run pure-logic cases (no filesystem) ----------------------------------------------
    scheme_ok = [("calpha.1", ("calpha", 1)), ("calpha.10", ("calpha", 10)), ("calpha.100", ("calpha", 100))]
    for cid, expect in scheme_ok:
        if split_clause_id(cid) != expect:
            failures.append("split_clause_id({!r}) != {!r}".format(cid, expect))
    for bad in ["calpha.01", "calpha.0", "calpha.-1", "calpha.1.2", "calpha", "short.1", "calpha.", "calpha.1a"]:
        if split_clause_id(bad) is not None:
            failures.append("split_clause_id({!r}) should be None (malformed)".format(bad))

    # Numeric, not lexicographic, ordinal comparison in cumulative-max, both directions.
    lex_trap_pass = [("calpha.9", (1, 1, 0)), ("calpha.10", (1, 2, 0))]  # 10 > 9 numerically -> clean
    if check_cumulative_max(lex_trap_pass, (1, 2, 0)):
        failures.append("cumulative-max wrongly flagged ordinal 10 vs prior 9 (lexicographic trap)")
    lex_trap_fail = [("calpha.10", (1, 1, 0)), ("calpha.2", (1, 2, 0))]  # 2 <= 10 numerically -> finding
    if not check_cumulative_max(lex_trap_fail, (1, 2, 0)):
        failures.append("cumulative-max missed ordinal 2 not exceeding prior 10")

    if check_resurrection({"calpha", "calpha.1"}, {"cbeta1.9"}):
        failures.append("resurrection wrongly flagged a disjoint retired set")
    if not check_resurrection({"calpha.2"}, {"calpha.2"}):
        failures.append("resurrection missed a live id equal to a tombstoned id")

    comp, na = check_completeness({"calpha.1"}, set(), {}, {"calpha.1"}, None)
    if not comp or not na:
        failures.append("completeness: a missing born row was not flagged, or na not reported without a predecessor")
    comp, _na = check_completeness({"calpha.1"}, {"calpha.1"}, {}, {"calpha.1"}, {"calpha.1", "cbeta1.2"})
    if not any("unexplained disappearance" in f for f in comp):
        failures.append("completeness: an unexplained disappearance was not flagged with a predecessor")

    # --- end-to-end fixture cases (a synthetic corpus per fail mode in a private tempdir) ----------
    import copy
    import shutil
    import tempfile

    try:
        base_tmp = Path(tempfile.mkdtemp(prefix="aiqt-clauses-selftest-"))
    except OSError:
        base_tmp = None

    if base_tmp is None:
        print("SELF-TEST NOTE: no writable temp directory; end-to-end fixture cases SKIPPED (the pure "
              "clause-id, ordinal, resurrection, and completeness coverage above still ran)", file=sys.stderr)
        e2e_ran = False
    else:
        e2e_ran = True
        n = [0]

        def _fresh(rules, clause_rows, born, tombstones=(), successors=()):
            n[0] += 1
            base = _write_corpus(base_tmp / "case{}".format(n[0]), rules, clause_rows,
                                 list(born), list(tombstones), list(successors))
            return base

        try:
            # (0) the passing genesis corpus, with --genesis, is clean (exit 0).
            rules, rows, born = _good_genesis()
            base = _fresh(rules, copy.deepcopy(rows), born)
            if _run_quiet(**_paths(base, genesis=True)) != 0:
                failures.append("genesis: a well-formed genesis corpus expected exit 0")

            # The same corpus with the DEFERRED manifest leg armed and a matching manifest is clean; a
            # mismatching manifest fails; the default invocation reads no manifest at all.
            digest_alpha = _sha_of(rules["calpha"])
            digest_beta = _sha_of(rules["cbeta1"])
            manifest = base / ".aiqt" / "manifest.toml"
            manifest.write_text(
                '[[sources]]\npath = ".aiqt/core/rules/calpha.md"\nsha256 = "{}"\n\n'
                '[[sources]]\npath = ".aiqt/core/rules/cbeta1.md"\nsha256 = "{}"\n'.format(
                    digest_alpha, digest_beta), encoding="utf-8")
            if _run_quiet(**_paths(base, genesis=True, manifest_path=manifest)) != 0:
                failures.append("manifest leg: a matching manifest expected exit 0 when armed")
            bad_manifest = base / ".aiqt" / "manifest-bad.toml"
            bad_manifest.write_text(
                '[[sources]]\npath = ".aiqt/core/rules/calpha.md"\nsha256 = "{}"\n\n'
                '[[sources]]\npath = ".aiqt/core/rules/cbeta1.md"\nsha256 = "{}"\n'.format(
                    "0" * 64, digest_beta), encoding="utf-8")
            if _run_quiet(**_paths(base, genesis=True, manifest_path=bad_manifest)) != 1:
                failures.append("manifest leg: a mismatching manifest expected exit 1 when armed")

            # (1) padded ordinal -> scheme FAIL.
            rules, rows, born = _good_genesis()
            rows[0]["clause-id"] = "calpha.01"
            base = _fresh(rules, rows, born)
            if _run_quiet(**_paths(base, genesis=True)) != 1:
                failures.append("padded ordinal: expected exit 1")

            # (2) duplicate clause-id -> scheme FAIL.
            rules, rows, born = _good_genesis()
            rows[1] = copy.deepcopy(rows[0])  # a second row reusing calpha.1
            base = _fresh(rules, rows, born)
            if _run_quiet(**_paths(base, genesis=True)) != 1:
                failures.append("duplicate id: expected exit 1")

            # (3) corpus-id with no clause row in the inventory -> scheme FAIL.
            rules, rows, born = _good_genesis()
            rows = [r for r in rows if r["corpus-id"] != "cbeta1"]  # drop cbeta1's rows entirely
            base = _fresh(rules, rows, born)
            if _run_quiet(**_paths(base, genesis=True)) != 1:
                failures.append("corpus-id missing from inventory: expected exit 1")

            # (4) span out of range -> per-row FAIL.
            rules, rows, born = _good_genesis()
            rows[0]["end-line"] = 9999
            base = _fresh(rules, rows, born)
            if _run_quiet(**_paths(base, genesis=True)) != 1:
                failures.append("span out of range: expected exit 1")

            # (5) text mismatch -> per-row FAIL.
            rules, rows, born = _good_genesis()
            rows[0]["canonical-text"] = "not the span content"
            base = _fresh(rules, rows, born)
            if _run_quiet(**_paths(base, genesis=True)) != 1:
                failures.append("text mismatch: expected exit 1")

            # (6) digest mismatch -> per-row FAIL.
            rules, rows, born = _good_genesis()
            rows[0]["source-digest"] = "0" * 64
            base = _fresh(rules, rows, born)
            if _run_quiet(**_paths(base, genesis=True)) != 1:
                failures.append("digest mismatch: expected exit 1")

            # (7) missing born row -> completeness FAIL.
            rules, rows, born = _good_genesis()
            born = [b for b in born if b[0] != rows[0]["clause-id"]]  # drop one clause's born row
            base = _fresh(rules, rows, born)
            if _run_quiet(**_paths(base, genesis=True)) != 1:
                failures.append("missing born row: expected exit 1")

            # (8) resurrected tombstoned id -> resurrection FAIL (a non-genesis register with a tombstone).
            rules, rows, born = _good_genesis()
            dead = rows[0]["clause-id"]                       # this id is live in the inventory ...
            tomb = [(dead, "1.4.0")]                          # ... yet tombstoned in the register
            base = _fresh(rules, rows, born, tombstones=tomb)
            if _run_quiet(**_paths(base)) != 1:               # not --genesis: tombstones are allowed
                failures.append("resurrected tombstoned id: expected exit 1")

            # (9) cumulative-max: a new ordinal not exceeding the historical max -> FAIL. Register spans two
            # releases: calpha.1 and calpha.10 born at 1.1.0, calpha.2 born at 1.2.0 (2 <= 10 -> finding).
            lines, line_of = _rule_lines("calpha", ["ob one", "ob two", "ob ten"])
            digest = _sha_of(lines)
            sp = ".aiqt/core/rules/calpha.md"
            rows = [
                {"clause-id": "calpha.1", "corpus-id": "calpha", "source-path": sp,
                 "start-line": line_of[0], "end-line": line_of[0], "canonical-text": "ob one",
                 "source-digest": digest},
                {"clause-id": "calpha.10", "corpus-id": "calpha", "source-path": sp,
                 "start-line": line_of[2], "end-line": line_of[2], "canonical-text": "ob ten",
                 "source-digest": digest},
                {"clause-id": "calpha.2", "corpus-id": "calpha", "source-path": sp,
                 "start-line": line_of[1], "end-line": line_of[1], "canonical-text": "ob two",
                 "source-digest": digest},
            ]
            born = [("calpha", "1.1.0"), ("calpha.1", "1.1.0"), ("calpha.10", "1.1.0"), ("calpha.2", "1.2.0")]
            base = _fresh({"calpha": lines}, rows, born)
            if _run_quiet(**_paths(base)) != 1:
                failures.append("ordinal not exceeding cumulative max: expected exit 1")

            # (10) lexicographic-trap PASS: cumulative max 9, a new ordinal 10 -> clean (numeric compare).
            lines, line_of = _rule_lines("calpha", ["ob nine", "ob ten"])
            digest = _sha_of(lines)
            rows = [
                {"clause-id": "calpha.9", "corpus-id": "calpha", "source-path": sp,
                 "start-line": line_of[0], "end-line": line_of[0], "canonical-text": "ob nine",
                 "source-digest": digest},
                {"clause-id": "calpha.10", "corpus-id": "calpha", "source-path": sp,
                 "start-line": line_of[1], "end-line": line_of[1], "canonical-text": "ob ten",
                 "source-digest": digest},
            ]
            born = [("calpha", "1.1.0"), ("calpha.9", "1.1.0"), ("calpha.10", "1.2.0")]
            base = _fresh({"calpha": lines}, rows, born)
            if _run_quiet(**_paths(base)) != 0:
                failures.append("lexicographic trap: a new ordinal 10 over prior 9 expected exit 0")

            # (11) unexplained disappearance -> cross-release completeness FAIL. The predecessor inventory
            # carries an id (cbeta1.2) absent from the current inventory with no retirement row.
            rules, rows, born = _good_genesis()
            current = _fresh(rules, rows, born)
            prev = current / ".aiqt" / "core" / "prev-clauses.toml"
            prev_rows = copy.deepcopy(rows) + [{"clause-id": "cbeta1.9", "corpus-id": "cbeta1",
                                                "source-path": ".aiqt/core/rules/cbeta1.md",
                                                "start-line": 8, "end-line": 8,
                                                "canonical-text": "gone", "source-digest": "0" * 64}]
            prev.write_text("".join(_toml_clause(r) + "\n" for r in prev_rows), encoding="utf-8")
            if _run_quiet(**_paths(current, prev_inventory_path=prev)) != 1:
                failures.append("unexplained disappearance: expected exit 1")

            # (12) fail-closed: an unreadable/absent register is exit 2, never a clean empty history.
            rules, rows, born = _good_genesis()
            base = _fresh(rules, rows, born)
            (base / ".aiqt" / "core" / "id-history.toml").unlink()
            if _run_quiet(**_paths(base, genesis=True)) != 2:
                failures.append("absent register: expected fail-closed exit 2")

            # (13) fail-closed: --genesis with a register that carries a tombstone is exit 2.
            rules, rows, born = _good_genesis()
            base = _fresh(rules, rows, born, tombstones=[("calpha.1", "1.4.0")])
            if _run_quiet(**_paths(base, genesis=True)) != 2:
                failures.append("genesis with a tombstone: expected fail-closed exit 2")
        finally:
            shutil.rmtree(base_tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    core = ("clause-id parsing, numeric ordinal comparison (lexicographic trap both directions), "
            "resurrection and completeness set logic")
    if e2e_ran:
        print("SELF-TEST PASS: {}; and the end-to-end fixtures hold (passing genesis, deferred/armed "
              "manifest leg, padded ordinal, duplicate id, missing corpus-id, span out of range, text "
              "mismatch, digest mismatch, missing born row, resurrected id, cumulative-max fail, "
              "lexicographic-trap pass, unexplained disappearance, and the fail-closed register cases)"
              .format(core))
    else:
        print("SELF-TEST PASS (PARTIAL): {}; the end-to-end fixture cases were SKIPPED (no writable temp "
              "directory), so those invariants are UNVERIFIED this run".format(core))
    return 0


# --- argument handling (hand-rolled to match check_version_monotonicity.py / check_portability.py) --

def _parse_args(argv):
    opts = {"root": None, "rules_dir": None, "inventory": None, "register": None, "prev_inventory": None,
            "genesis": False, "manifest": None, "with_manifest": False, "self_test": False}
    i = 0
    single = {"--root": "root", "--rules-dir": "rules_dir", "--inventory": "inventory",
              "--register": "register", "--prev-inventory": "prev_inventory", "--manifest": "manifest"}
    while i < len(argv):
        arg = argv[i]
        if arg in single and i + 1 < len(argv):
            opts[single[arg]] = argv[i + 1]
            i += 2
        elif arg == "--genesis":
            opts["genesis"] = True
            i += 1
        elif arg == "--with-manifest":
            opts["with_manifest"] = True
            i += 1
        elif arg == "--self-test":
            opts["self_test"] = True
            i += 1
        else:
            print("usage: check_clauses.py [--root DIR] [--rules-dir DIR] [--inventory FILE] "
                  "[--register FILE] [--prev-inventory FILE] [--genesis] [--with-manifest | --manifest FILE] "
                  "| --self-test", file=sys.stderr)
            return None
    return opts


def main():
    opts = _parse_args(sys.argv[1:])
    if opts is None:
        return 2
    if opts["self_test"]:
        return self_test_main()
    root = Path(opts["root"]).resolve() if opts["root"] else repo_root()
    rules_dir = Path(opts["rules_dir"]) if opts["rules_dir"] else root / ".aiqt" / "core" / "rules"
    inventory = Path(opts["inventory"]) if opts["inventory"] else root / ".aiqt" / "core" / "clauses.toml"
    register = Path(opts["register"]) if opts["register"] else root / ".aiqt" / "core" / "id-history.toml"
    prev = Path(opts["prev_inventory"]) if opts["prev_inventory"] else None
    manifest = None
    if opts["manifest"]:
        manifest = Path(opts["manifest"])
    elif opts["with_manifest"]:
        manifest = root / ".aiqt" / "manifest.toml"
    return run(root, rules_dir, inventory, register, prev, opts["genesis"], manifest)


if __name__ == "__main__":
    sys.exit(main())
