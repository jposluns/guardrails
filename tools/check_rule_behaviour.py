#!/usr/bin/env python3
"""Rule-behaviour gate (rule-behaviour): an obligation table encodes what selected rule clauses require, a
set of cases pins the outcomes that encoding gives, and a change that touches a clause must show, against
the encoding already on its base revision, that the clause decides a case.

Inputs, all under .aiqt/core/behaviour/ unless named otherwise:

  obligations.toml     [[row]] tables, one obligation each: clause-id, text-sha256 (the SHA-256 of that
                       clause's canonical-text in the same revision's .aiqt/core/clauses.toml), kind
                       (require, exempt or limit), when (a nonempty subset of the scenario facts), and the
                       one effect key of its kind (require, suppresses or cancels, a list of atoms). A
                       require row may carry params: exempt-vocab, never-exempt and exempt-on. A limit
                       row cancels only atoms some exempt row in the same table suppresses.
  cases/<id>.toml      one case or control each: a closed scenario vocabulary and the expected outcome at
                       HEAD (and, for a case, at its base revision); a control differs from its case in
                       exactly the scenario keys its delta names. A fact that grants an exemption carries
                       the evidence its clause names: a bounded claim lists its set (named-set: members
                       and the count the claim states), and a wording fix names the probe that found its
                       instances (probe: the properties of PROBE_PROPERTIES it has).
  waivers.toml         an append-only log of [[waiver]] rows; a row is live only in the change that
                       appends it.
  waiver-budget.toml   the per-change ceiling on live waiver rows of each kind, always read from the base.
  coverage-exclusions.toml
                       one [[exclusion]] per obligation row that no case covers: clause-id, row-sha256
                       (the digest the gate prints for that row) and a nonempty reason.

The matcher is deterministic and does not depend on row order. Facts come from the scenario, and a fact
that grants an exemption counts only with its evidence: claim:bounded holds only when the named set is
closed (the count the claim states equals the number of members it lists). A row triggers when its
when-set holds; required atoms are the union of the triggered require rows (item and instance atoms
expanded per item and per instance), minus what triggered exempt rows suppress, plus back what triggered
limit rows cancel; each remaining atom is met or unmet from the scenario's evidence. Instance atoms count
only over a probe that has every property the clause names (otherwise instance:probed is unmet), and an
exemption label disposes of an instance only when every triggered row requiring instance:dispositioned
admits it (a row with no exempt-vocab admits none). The outcome is ACCEPT, or REJECT followed by the
unmet atoms sorted bytewise; its decision is ACCEPT or REJECT.

Legs that always run: SCHEMA (malformed input exits 2; a stale text-sha256, an unknown clause, or a removes
id still in the HEAD register exits 1), PREMISE (a bounded claim with no named-set, or a wording fix or a
listed instance with no probe, exits 1: the scenario would assert what the rule requires proving), CASES
(every outcome matches byte for byte; a control differs from its case), ABLATION (each clause a case lists
changes that case's outcome when its rows are removed, each row of a listed clause is load bearing for
some listing case, and a row whose clause no case lists fails unless an exclusion with a reason names it;
an exclusion that names no such row fails), ENCODING CHANGED and UNENCODED INVENTORY (reports only), and
RETRO (an informational comparison with a pinned older revision, checked against git and never read by
BINDING).

BINDING (--base REF): for every rule file under .aiqt/core/rules/ that differs, difflib.SequenceMatcher
aligns the base and HEAD bytes. A clause is in the change set C when its canonical text changed, it was
removed, any changed range or insertion point touches its span on either side, or its register anchor
moved (its source path changed, its span is not the exact image of its base span under the alignment,
or its window's line bounds moved relative to its text). A new clause whose span holds no changed byte is
only registered. Every changed byte outside every clause span on its side is prose residue, which only
non-clause-text waivers in the frontmatter, heading or whitespace categories can tile (a heading tile lies
inside one ATX heading line: one to six '#' opening the line, then a space, a tab or the line end). Each
clause in C is bound by a new or changed case whose decision differs between the base encoding and HEAD
and flips back when that clause's base rows are restored, or carried by a live review waiver of the one
kind its change admits: wording-only (the text changed, the rows did not), unencoded (no base row), or anchor-only (the
anchor moved, the text and rows did not). A clause with no base encoding is bindable only when it is
wholly new. A base revision with neither this script nor the table is the genesis change, which passes
only when C and the residue are empty.

  check_rule_behaviour.py                 the always-run legs; binding is reported not applicable
  check_rule_behaviour.py --base REF      the always-run legs and binding against REF
  check_rule_behaviour.py --base REF --each-commit
                                          the push leg: every commit on HEAD's first-parent chain after
                                          REF, each read from history and judged by every leg against its
                                          own first parent (the working tree is not read)
  check_rule_behaviour.py --self-test     assert the gate's own vectors, mutants and live legs
  check_rule_behaviour.py [--self-test] --execution-report ABS_PATH
                                          the self-test, also writing its execution report, finalized at
                                          interpreter exit (tools/_selftest_exit_report.py)
Exit: 0 every leg holds; 1 findings; 2 cannot evaluate (a malformed or missing input, an unresolvable or
shallow base, missing retro history, a base with the gate script but no table, or for --each-commit a
base that is not on HEAD's first-parent chain or that leaves no commit to judge).

It does not show that any encoding means what the prose means or how a model reads it: a wrong encoding
that review accepted passes, including one landed before the edit it later supports. The matcher checks
the form of a scenario's evidence (a listed set and its stated count, a probe's named properties), never
that a scenario describes a real set or probe. An edit to a clause without an encoding fails unless it
carries an unencoded waiver, unchanged text is never examined, and a change that alters only which
obligations are unmet binds nothing. Waivers rest on reviewer judgement within ceilings; a new waiver may
repeat an earlier transition only by naming it. The base and push legs need full git history and do not
see a rewritten base; the push leg judges a merged branch's own commits only through their merge commit.
The span of a clause is located at the first occurrence of its canonical text inside its register window;
check_clauses.py owns the uniqueness and tightness of that window.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_rule_behaviour.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import bisect
import contextlib
import difflib
import hashlib
import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _selftest_exit_report  # noqa: E402  (the execution report, finalized at interpreter exit)
import check_selftest_execution  # noqa: E402  (the authoritative registry schema)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_REL = "tools/check_rule_behaviour.py"
RULES_DIR = ".aiqt/core/rules"
RULES_PREFIX = RULES_DIR + "/"
INVENTORY_REL = ".aiqt/core/clauses.toml"
CASES_DIR = ".aiqt/core/behaviour/cases"
CASES_PREFIX = CASES_DIR + "/"
TABLE_REL = ".aiqt/core/behaviour/obligations.toml"
WAIVERS_REL = ".aiqt/core/behaviour/waivers.toml"
BUDGET_REL = ".aiqt/core/behaviour/waiver-budget.toml"
EXCLUSIONS_REL = ".aiqt/core/behaviour/coverage-exclusions.toml"
REGISTRY_REL = "tools/selftest_checks.toml"
SUITE_ID = "rule-behaviour-selftest"

PREDICATES = ("op:record-change", "record:multi-item", "claim:completion", "claim:bounded",
              "claim:partial", "fix:wording")
ATOMS = ("set:enumerated", "count:reconciled", "item:accounted", "instance:dispositioned")
EFFECT_KEY = {"require": "require", "exempt": "suppresses", "limit": "cancels"}
LABELS = ("history", "warning", "correction")
WHERES = ("rule", "register-copy", "changelog-quote", "mistakes-register", "correction-note")
FORMS = ("operative", "quotation")
DISPOSITIONS = ("fixed", "unfixed", "exempt:history", "exempt:warning", "exempt:correction")
OPS = ("retire", "split", "merge", "remove-items", "wording-fix", "none")
RECORD_OPS = ("retire", "split", "merge", "remove-items")
CLAIMS = ("completion", "bounded", "partial", "none")
EVIDENCE = ("index-enumeration", "id-check", "count-check")
# What evgcmp.15 names for a wording fix's probe: derived from and validated against the correction that
# names the superseded wording, and matching that wording across line wraps and escapes.
PROBE_PROPERTIES = ("derived-from-correction", "validated-against-correction", "matches-across-line-wraps",
                    "matches-across-escapes")
SCENARIO_KEYS = ("op", "claim", "before", "after", "evidence", "item-evidence", "instances", "named-set", "probe")
NAME_RE = re.compile(r"[a-z0-9-]+")
SHA_RE = re.compile(r"[0-9a-f]{64}")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
CLAUSE_ID_RE = re.compile(r"[a-z0-9]{6,}\.[1-9][0-9]*")
ATOM_RE = re.compile(r"set:enumerated|count:reconciled|item:accounted:[a-z0-9-]+|instance:probed"
                     r"|instance:dispositioned:(?:{})".format("|".join(re.escape(w) for w in WHERES)))
CLAUSE_WAIVER_KINDS = ("wording-only", "unencoded", "anchor-only")
RANGE_WAIVER_KIND = "non-clause-text"
WAIVER_KINDS = CLAUSE_WAIVER_KINDS + (RANGE_WAIVER_KIND,)
# Kinds an earlier design of this log carried; each is refused by name so an old row never reads as live.
RETIRED_WAIVER_KINDS = {
    "untestable": "a reusable untestable waiver is retired; use unencoded, which binds to one change",
    "whole-edit": "one waiver no longer covers a whole edit; bind each clause and tile non-clause text",
}
CATEGORIES = ("frontmatter", "heading", "whitespace")
# An ATX heading line as CommonMark reads one from the first byte of a line: one to six '#', then a space, a
# tab or the line end. A line such as '#word', or one opening with seven '#', is paragraph text, never heading.
ATX_HEADING_RE = re.compile(rb"#{1,6}(?:[ \t]|\n|\Z)")
BUDGET_KEYS = {"wording-only": "wording-only-per-change", "unencoded": "unencoded-per-change",
               "anchor-only": "anchor-only-per-change", "non-clause-text": "non-clause-text-per-change"}
ABSENT = "absent"
CHILD_TIMEOUT = 120


class GateError(Exception):
    """An input the gate cannot read, parse, or resolve; the run exits 2 (fail-closed)."""


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _text_sha(text):
    return _sha(text.encode("utf-8"))


def _toml(data, label):
    """Parse one TOML input; a missing, undecodable or malformed input cannot be evaluated."""
    if data is None:
        raise GateError("{}: missing".format(label))
    try:
        return tomllib.loads(data.decode("utf-8"))
    except (ValueError, RecursionError, MemoryError) as exc:
        raise GateError("{}: malformed ({})".format(label, type(exc).__name__))


# --------------------------------------------------------------------------------------------- snapshots

class Snapshot:
    """The files of one revision that the gate reads, by repository-relative path."""

    def __init__(self, files, label):
        self.files = dict(files)
        self.label = label

    def read(self, rel):
        return self.files.get(rel)

    def under(self, prefix):
        return sorted(rel for rel in self.files if rel.startswith(prefix))


def _read_regular(path, label):
    """Read one working-tree file; anything but a regular file (a symlink, FIFO or directory) is refused."""
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise GateError("{}: cannot stat ({})".format(label, type(exc).__name__))
    if not stat.S_ISREG(mode):
        raise GateError("{}: not a regular file".format(label))
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise GateError("{}: unreadable ({})".format(label, type(exc).__name__))


def _walk_files(root, directory):
    """Every file under one working-tree directory, as repository-relative paths; a symlink or other
    non-regular entry is refused, and a missing directory holds nothing."""
    out = []
    pending = [directory]
    while pending:
        rel_dir = pending.pop()
        try:
            entries = sorted(os.scandir(Path(root) / rel_dir), key=lambda entry: entry.name)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise GateError("{}: cannot list ({})".format(rel_dir, type(exc).__name__))
        for entry in entries:
            rel = rel_dir + "/" + entry.name
            if entry.is_dir(follow_symlinks=False):
                pending.append(rel)
            elif entry.is_file(follow_symlinks=False):
                out.append(rel)
            else:
                raise GateError("{}: not a regular file or directory".format(rel))
    return out


def worktree_snapshot(root):
    """The working tree's view: every file under the rules and cases directories, and the named inputs."""
    files = {}
    for rel in _walk_files(root, RULES_DIR) + _walk_files(root, CASES_DIR):
        files[rel] = _read_regular(Path(root) / rel, rel)
    for rel in (INVENTORY_REL, TABLE_REL, WAIVERS_REL, BUDGET_REL, EXCLUSIONS_REL, SCRIPT_REL):
        data = _read_regular(Path(root) / rel, rel)
        if data is not None:
            files[rel] = data
    return Snapshot(files, "HEAD")


def _git_env():
    """The caller's environment with object replacement and grafts disabled for every history read."""
    return dict(os.environ, GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull)


class GitHistory:
    """Read-only access to one repository's history, with object substitution disabled."""

    def __init__(self, root):
        self.root = Path(root)

    def _rev_parse(self, spec):
        try:
            result = subprocess.run(["git", "-C", str(self.root), "--no-replace-objects", "rev-parse",
                                     "--verify", "--quiet", spec], capture_output=True, env=_git_env(),
                                    timeout=CHILD_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as exc:
            raise GateError("git is not available ({})".format(type(exc).__name__))
        oid = result.stdout.decode("ascii", "replace").strip()
        return oid if result.returncode == 0 and COMMIT_RE.fullmatch(oid) else None

    def resolve(self, ref):
        """The commit a ref names; an option-like, unresolvable or non-commit ref cannot be evaluated."""
        if not isinstance(ref, str) or not ref or ref.startswith("-"):
            raise GateError("base {!r} is not a revision".format(ref))
        oid = self._rev_parse(ref + "^{commit}")
        if oid is None:
            raise GateError("base {!r} does not resolve to a commit".format(ref))
        return oid

    def has_commit(self, oid):
        return self._rev_parse(oid + "^{commit}") == oid

    def head(self):
        oid = self._rev_parse("HEAD^{commit}")
        if oid is None:
            raise GateError("HEAD does not resolve to a commit")
        return oid

    def first_parent_range(self, base, head):
        """The (parent, commit) transitions of HEAD's first-parent chain after base, oldest first, read from
        the raw parent list of each listed commit; a base off that chain, or no commit after it, cannot be
        evaluated."""
        try:
            listing = subprocess.run(["git", "-C", str(self.root), "--no-replace-objects", "rev-list",
                                      "--first-parent", "--parents", head, "^" + base], capture_output=True,
                                     env=_git_env(), timeout=CHILD_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as exc:
            raise GateError("git is not available ({})".format(type(exc).__name__))
        if listing.returncode != 0:
            raise GateError("cannot list the commits from {} to {}".format(base, head))
        lines = [line.split(" ") for line in listing.stdout.decode("ascii", "replace").splitlines()]
        if not lines:
            raise GateError("base {} leaves no commit to judge on HEAD's first-parent chain".format(base))
        transitions, expected = [], head
        for fields in lines:
            if not all(COMMIT_RE.fullmatch(field) for field in fields) or fields[0] != expected:
                raise GateError("cannot read the first-parent chain from {} to {}".format(base, head))
            if len(fields) < 2:
                raise GateError("base {} is not on HEAD's first-parent chain".format(base))
            transitions.append((fields[1], fields[0]))
            expected = fields[1]
        if expected != base:
            raise GateError("base {} is not on HEAD's first-parent chain".format(base))
        return list(reversed(transitions))

    def require_full_history(self):
        try:
            result = subprocess.run(["git", "-C", str(self.root), "rev-parse", "--is-shallow-repository"],
                                    capture_output=True, env=_git_env(), timeout=CHILD_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as exc:
            raise GateError("git is not available ({})".format(type(exc).__name__))
        answer = result.stdout.decode("ascii", "replace").strip()
        if result.returncode != 0 or answer not in ("true", "false"):
            raise GateError("cannot tell whether the history is shallow")
        if answer == "true":
            raise GateError("the history is shallow; the base leg needs full history (fetch-depth: 0)")

    def is_ancestor(self, older, newer):
        try:
            result = subprocess.run(["git", "-C", str(self.root), "--no-replace-objects", "merge-base",
                                     "--is-ancestor", older, newer], capture_output=True, env=_git_env(),
                                    timeout=CHILD_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as exc:
            raise GateError("git is not available ({})".format(type(exc).__name__))
        if result.returncode not in (0, 1):
            raise GateError("cannot compare {} with {}".format(older, newer))
        return result.returncode == 0

    def snapshot(self, oid):
        """The gate's input files at one commit, read through ls-tree and one cat-file batch."""
        try:
            listing = subprocess.run(["git", "-C", str(self.root), "--no-replace-objects", "ls-tree", "-r",
                                      "-z", "--full-tree", oid, "--", RULES_DIR, CASES_DIR, INVENTORY_REL,
                                      TABLE_REL, WAIVERS_REL, BUDGET_REL, EXCLUSIONS_REL, SCRIPT_REL],
                                     capture_output=True, env=_git_env(), timeout=CHILD_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as exc:
            raise GateError("git is not available ({})".format(type(exc).__name__))
        if listing.returncode != 0:
            raise GateError("cannot list the tree of {}".format(oid))
        entries = []
        for record in listing.stdout.split(b"\0"):
            if not record:
                continue
            meta, _tab, name = record.partition(b"\t")
            mode, kind, blob = meta.decode("ascii", "replace").split(" ")
            rel = name.decode("utf-8", "surrogateescape")
            if kind != "blob" or mode not in ("100644", "100755"):
                raise GateError("{} at {}: not a regular file".format(rel, oid))
            entries.append((rel, blob))
        files = {}
        if entries:
            request = "".join(blob + "\n" for _rel, blob in entries).encode("ascii")
            try:
                batch = subprocess.run(["git", "-C", str(self.root), "--no-replace-objects", "cat-file",
                                        "--batch"], input=request, capture_output=True, env=_git_env(),
                                       timeout=CHILD_TIMEOUT)
            except (OSError, subprocess.SubprocessError) as exc:
                raise GateError("git is not available ({})".format(type(exc).__name__))
            if batch.returncode != 0:
                raise GateError("cannot read the blobs of {}".format(oid))
            data, at = batch.stdout, 0
            for rel, blob in entries:
                end = data.find(b"\n", at)
                header = data[at:end].decode("ascii", "replace").split(" ")
                if end < 0 or len(header) != 3 or header[0] != blob or header[1] != "blob":
                    raise GateError("{} at {}: unreadable blob".format(rel, oid))
                size = int(header[2])
                files[rel] = data[end + 1:end + 1 + size]
                at = end + 1 + size + 1
        return Snapshot(files, oid)


# ------------------------------------------------------------------------------------ register and spans

class Clause:
    """One register row: its anchor (source path and locating window) and canonical text."""

    def __init__(self, cid, path, start, end, text):
        self.cid, self.path, self.start, self.end, self.text = cid, path, start, end, text
        self.sha = _text_sha(text)
        self.anchor = _sha("{}\n{}\n{}\n{}".format(path, start, end, text).encode("utf-8"))


def load_register(snap):
    doc = _toml(snap.read(INVENTORY_REL), "{} at {}".format(INVENTORY_REL, snap.label))
    rows = doc.get("clause")
    if not isinstance(rows, list) or not rows:
        raise GateError("{} at {}: no [[clause]] array".format(INVENTORY_REL, snap.label))
    register = {}
    for number, row in enumerate(rows, 1):
        where = "{} at {} row {}".format(INVENTORY_REL, snap.label, number)
        if not isinstance(row, dict):
            raise GateError("{}: not a table".format(where))
        cid, path, text = row.get("clause-id"), row.get("source-path"), row.get("canonical-text")
        start, end = row.get("start-line"), row.get("end-line")
        if not (isinstance(cid, str) and CLAUSE_ID_RE.fullmatch(cid) and isinstance(path, str) and path
                and isinstance(text, str) and text and all(type(v) is int for v in (start, end))):
            raise GateError("{}: malformed clause row".format(where))
        if cid in register:
            raise GateError("{}: duplicate clause-id {}".format(where, cid))
        register[cid] = Clause(cid, path, start, end, text)
    return register


def _line_starts(data):
    starts = [0]
    at = data.find(b"\n")
    while at != -1:
        starts.append(at + 1)
        at = data.find(b"\n", at + 1)
    if starts[-1] == len(data):
        starts.pop()
    return starts


def _line_of(starts, pos):
    """The 1-based line holding byte pos."""
    return bisect.bisect_right(starts, pos)


def locate(data, clause, label):
    """The half-open byte range [s, e) of a clause's canonical text: its first occurrence inside its
    register window. A window outside the file, or text absent from it, cannot be evaluated here."""
    if data is None:
        raise GateError("clause {} at {}: source {} is missing".format(clause.cid, label, clause.path))
    starts = _line_starts(data)
    if clause.start < 1 or clause.end < clause.start or clause.end > len(starts):
        raise GateError("clause {} at {}: window {}-{} is outside {}".format(
            clause.cid, label, clause.start, clause.end, clause.path))
    low = starts[clause.start - 1]
    high = starts[clause.end] - 1 if clause.end < len(starts) else len(data.rstrip(b"\n"))
    needle = clause.text.encode("utf-8")
    at = data.find(needle, low, high)
    if at < 0:
        raise GateError("clause {} at {}: canonical text not found in its window of {}".format(
            clause.cid, label, clause.path))
    return at, at + len(needle)


# -------------------------------------------------------------------------------- obligation table rows

class Row:
    """One parsed obligation row."""

    def __init__(self, raw, clause, kind, when, effect, vocab, never, pairs):
        self.raw = raw
        self.clause, self.kind, self.when, self.effect = clause, kind, when, effect
        self.vocab, self.never, self.pairs = vocab, never, pairs
        self.sha = raw.get("text-sha256")
        self.key = json.dumps(raw, sort_keys=True)
        self.key_without_sha = json.dumps(dict((k, v) for k, v in raw.items() if k != "text-sha256"),
                                          sort_keys=True)


def _string_list(value, where, vocabulary=None, allow_empty=False):
    if (not isinstance(value, list) or (not value and not allow_empty)
            or not all(isinstance(item, str) for item in value) or len(set(value)) != len(value)):
        raise GateError("{}: not a list of distinct strings".format(where))
    if vocabulary is not None:
        unknown = sorted(set(value) - set(vocabulary))
        if unknown:
            raise GateError("{}: unknown value(s) {}".format(where, ", ".join(unknown)))
    return value


def parse_row(raw, where, clause_key="clause-id"):
    if not isinstance(raw, dict):
        raise GateError("{}: not a table".format(where))
    kind = raw.get("kind")
    if not isinstance(kind, str) or kind not in EFFECT_KEY:
        raise GateError("{}: kind {!r} is not require, exempt or limit".format(where, kind))
    allowed = {clause_key, "text-sha256", "kind", "when", EFFECT_KEY[kind]}
    if kind == "require":
        allowed.add("params")
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise GateError("{}: unknown key(s) {}".format(where, ", ".join(unknown)))
    clause = raw.get(clause_key)
    if not isinstance(clause, str) or not CLAUSE_ID_RE.fullmatch(clause):
        raise GateError("{}: {} {!r} is not a clause id".format(where, clause_key, clause))
    if not isinstance(raw.get("text-sha256"), str) or not SHA_RE.fullmatch(raw["text-sha256"]):
        raise GateError("{}: text-sha256 is not 64 lowercase hex".format(where))
    when = frozenset(_string_list(raw.get("when"), where + " when", PREDICATES))
    effect = frozenset(_string_list(raw.get(EFFECT_KEY[kind]), where + " " + EFFECT_KEY[kind], ATOMS))
    vocab, never, pairs = None, frozenset(), frozenset()
    if "params" in raw:
        params = raw["params"]
        if not isinstance(params, dict):
            raise GateError("{}: params is not a table".format(where))
        unknown = sorted(set(params) - {"exempt-vocab", "never-exempt", "exempt-on"})
        if unknown:
            raise GateError("{}: unknown params key(s) {}".format(where, ", ".join(unknown)))
        if "never-exempt" in params:
            never = frozenset(_string_list(params["never-exempt"], where + " never-exempt", WHERES))
        if "exempt-vocab" in params:
            vocab = frozenset(_string_list(params["exempt-vocab"], where + " exempt-vocab", LABELS))
            if "exempt-on" not in params:
                raise GateError("{}: exempt-vocab without exempt-on".format(where))
        if "exempt-on" in params:
            listed = params["exempt-on"]
            if not isinstance(listed, list) or not listed:
                raise GateError("{}: exempt-on is not a nonempty array".format(where))
            seen = []
            for pair in listed:
                if not isinstance(pair, dict) or set(pair) != {"label", "where"}:
                    raise GateError("{}: an exempt-on entry is not {{label, where}}".format(where))
                if vocab is None or not isinstance(pair["label"], str) or pair["label"] not in vocab:
                    raise GateError("{}: exempt-on label {!r} is outside exempt-vocab".format(where, pair["label"]))
                if pair["where"] not in WHERES:
                    raise GateError("{}: exempt-on where {!r} is unknown".format(where, pair["where"]))
                if (pair["label"], pair["where"]) in seen:
                    raise GateError("{}: duplicate exempt-on pair".format(where))
                seen.append((pair["label"], pair["where"]))
            pairs = frozenset(seen)
            if set(vocab) - set(label for label, _w in pairs):
                raise GateError("{}: an exempt-vocab label has no exempt-on surface".format(where))
    return Row(raw, clause, kind, when, effect, vocab, never, pairs)


def parse_table(data, label):
    doc = _toml(data, label)
    unknown = sorted(set(doc) - {"row"})
    if unknown:
        raise GateError("{}: unknown top-level key(s) {}".format(label, ", ".join(unknown)))
    raw_rows = doc.get("row", [])
    if not isinstance(raw_rows, list):
        raise GateError("{}: row is not an array of tables".format(label))
    rows = [parse_row(raw, "{} row {}".format(label, number)) for number, raw in enumerate(raw_rows, 1)]
    _limit_schema(rows, label)
    return rows


def _limit_schema(rows, label):
    suppressible = set()
    for row in rows:
        if row.kind == "exempt":
            suppressible |= row.effect
    for row in rows:
        if row.kind == "limit" and not row.effect <= suppressible:
            raise GateError("{}: a limit row for {} cancels an atom no exempt row suppresses".format(
                label, row.clause))


# ------------------------------------------------------------------------------------------------ cases

class Case:
    def __init__(self, rel, data, doc):
        self.rel, self.data = rel, data
        self.cid = doc["id"]
        self.kind = doc["kind"]
        self.clauses = tuple(doc.get("clauses", ()))
        self.removes = tuple(doc.get("removes", ()))
        self.controls = doc.get("controls")
        self.delta = tuple(doc.get("delta", ()))
        self.scenario = doc["scenario"]
        self.head = doc["expect"]["head"]
        self.base = doc["expect"].get("base")
        self.retro = doc.get("retro")


def _outcome_string(value, where):
    if value == "ACCEPT":
        return value
    if not isinstance(value, str) or not value.startswith("REJECT "):
        raise GateError("{}: {!r} is not ACCEPT or REJECT <atoms>".format(where, value))
    atoms = value[len("REJECT "):].split(" ")
    if (not all(ATOM_RE.fullmatch(atom) for atom in atoms)
            or atoms != sorted(set(atoms), key=lambda atom: atom.encode("utf-8"))):
        raise GateError("{}: {!r} does not list valid atoms, sorted bytewise and distinct".format(where, value))
    return value


def _records(value, where):
    if not isinstance(value, list):
        raise GateError("{}: not an array".format(where))
    out, ids = [], set()
    for record in value:
        if not isinstance(record, dict) or set(record) != {"id", "items"}:
            raise GateError("{}: a record is not {{id, items}}".format(where))
        if not isinstance(record["id"], str) or not record["id"] or record["id"] in ids:
            raise GateError("{}: record ids must be distinct nonempty strings".format(where))
        ids.add(record["id"])
        items = record["items"]
        if not isinstance(items, list) or not all(isinstance(i, str) and NAME_RE.fullmatch(i) for i in items):
            raise GateError("{}: record items must match [a-z0-9-]+".format(where))
        out.append((record["id"], tuple(items)))
    return tuple(out)


def parse_scenario(raw, where):
    if not isinstance(raw, dict):
        raise GateError("{}: scenario is not a table".format(where))
    unknown = sorted(set(raw) - set(SCENARIO_KEYS))
    if unknown:
        raise GateError("{}: unknown scenario key(s) {}".format(where, ", ".join(unknown)))
    op = raw.get("op", "none")
    if op not in OPS:
        raise GateError("{}: op {!r} is unknown".format(where, op))
    claim = raw.get("claim", "none")
    if claim not in CLAIMS:
        raise GateError("{}: claim {!r} is unknown".format(where, claim))
    before = _records(raw.get("before", []), where + " before")
    after = _records(raw.get("after", []), where + " after")
    evidence = frozenset(_string_list(raw.get("evidence", []), where + " evidence", EVIDENCE, True))
    item_evidence = raw.get("item-evidence", {})
    if not isinstance(item_evidence, dict):
        raise GateError("{}: item-evidence is not a table".format(where))
    known_items = set(item for _id, items in before for item in items)
    for item, value in item_evidence.items():
        if value != "own-evidence" or item not in known_items:
            raise GateError("{}: item-evidence {!r} must name a before item as own-evidence".format(where, item))
    instances_raw = raw.get("instances", [])
    if not isinstance(instances_raw, list):
        raise GateError("{}: instances is not an array".format(where))
    instances, seen = [], set()
    for instance in instances_raw:
        if not isinstance(instance, dict) or set(instance) - {"where", "form", "disposition"}:
            raise GateError("{}: an instance has unknown keys".format(where))
        if "form" not in instance:
            raise GateError("{}: an instance has no form (it never defaults)".format(where))
        if instance.get("where") not in WHERES:
            raise GateError("{}: instance where {!r} is unknown".format(where, instance.get("where")))
        if instance["form"] not in FORMS:
            raise GateError("{}: instance form {!r} is unknown".format(where, instance["form"]))
        if instance.get("disposition") not in DISPOSITIONS:
            raise GateError("{}: instance disposition {!r} is unknown".format(where, instance.get("disposition")))
        if instance["where"] in seen:
            raise GateError("{}: two instances share where {!r}".format(where, instance["where"]))
        seen.add(instance["where"])
        instances.append((instance["where"], instance["form"], instance["disposition"]))
    named = _named_set(raw.get("named-set"), where)
    probe = frozenset(_string_list(raw.get("probe", []), where + " probe", PROBE_PROPERTIES, True))
    return {"op": op, "claim": claim, "before": before, "after": after, "evidence": evidence,
            "item-evidence": dict(item_evidence), "instances": tuple(instances), "named-set": named,
            "probe": probe}


def _named_set(value, where):
    """The set a bounded claim lists: (members, the count the claim states or None), or None when absent."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"members", "count"} or "members" not in value:
        raise GateError("{}: named-set holds members and an optional count".format(where))
    members = _string_list(value["members"], where + " named-set members")
    if not all(NAME_RE.fullmatch(member) for member in members):
        raise GateError("{}: named-set members must match [a-z0-9-]+".format(where))
    count = value.get("count")
    if count is not None and (type(count) is not int or count < 1):
        raise GateError("{}: named-set count must be a positive integer".format(where))
    return tuple(members), count


def parse_case(rel, data):
    where = rel
    doc = _toml(data, rel)
    kind = doc.get("kind")
    if kind not in ("case", "control"):
        raise GateError("{}: kind {!r} is not case or control".format(where, kind))
    allowed = {"id", "summary", "kind", "scenario", "expect"}
    allowed |= {"clauses", "removes", "retro"} if kind == "case" else {"controls", "delta"}
    unknown = sorted(set(doc) - allowed)
    if unknown:
        raise GateError("{}: key(s) {} not allowed on a {}".format(where, ", ".join(unknown), kind))
    stem = rel[len(CASES_PREFIX):-len(".toml")]
    if doc.get("id") != stem or not NAME_RE.fullmatch(stem):
        raise GateError("{}: id must equal the file stem".format(where))
    summary = doc.get("summary")
    if not isinstance(summary, str) or not summary.strip() or "\n" in summary:
        raise GateError("{}: summary must be one nonempty line".format(where))
    expect = doc.get("expect")
    if not isinstance(expect, dict) or "head" not in expect:
        raise GateError("{}: [expect] head is required".format(where))
    unknown = sorted(set(expect) - ({"head", "base"} if kind == "case" else {"head"}))
    if unknown:
        raise GateError("{}: expect key(s) {} not allowed on a {}".format(where, ", ".join(unknown), kind))
    _outcome_string(expect["head"], where + " expect.head")
    if "base" in expect:
        _outcome_string(expect["base"], where + " expect.base")
    if kind == "case":
        clauses = _string_list(doc.get("clauses"), where + " clauses", allow_empty=True)
        removes = _string_list(doc.get("removes", []), where + " removes", allow_empty=True)
        if not clauses and not removes:
            raise GateError("{}: a case lists no clauses and removes nothing".format(where))
        for cid in clauses + removes:
            if not CLAUSE_ID_RE.fullmatch(cid):
                raise GateError("{}: {!r} is not a clause id".format(where, cid))
    else:
        if not isinstance(doc.get("controls"), str):
            raise GateError("{}: a control names the case it controls".format(where))
        _string_list(doc.get("delta"), where + " delta", SCENARIO_KEYS)
    doc["scenario"] = parse_scenario(doc.get("scenario", {}), where)
    if "retro" in doc:
        doc["retro"] = _parse_retro(doc["retro"], where)
    return Case(rel, data, doc)


def _parse_retro(retro, where):
    if not isinstance(retro, dict) or set(retro) != {"source", "old", "row"}:
        raise GateError("{}: [retro] holds exactly source, old and row".format(where))
    if not isinstance(retro["source"], str) or not COMMIT_RE.fullmatch(retro["source"]):
        raise GateError("{}: retro source is not a full commit id".format(where))
    _outcome_string(retro["old"], where + " retro.old")
    if not isinstance(retro["row"], list) or not retro["row"]:
        raise GateError("{}: retro rows are missing".format(where))
    rows, anchors = [], []
    for number, raw in enumerate(retro["row"], 1):
        label = "{} retro row {}".format(where, number)
        if not isinstance(raw, dict):
            raise GateError("{}: not a table".format(label))
        if "as-clause" in raw:
            if not isinstance(raw.get("path"), str) or not isinstance(raw.get("text"), str) or not raw["text"]:
                raise GateError("{}: a span anchor carries path and text".format(label))
            body = dict((k, v) for k, v in raw.items() if k not in ("path", "text"))
            rows.append(parse_row(body, label, clause_key="as-clause"))
            anchors.append(("span", raw["path"], raw["text"], raw.get("text-sha256")))
        else:
            rows.append(parse_row(raw, label))
            anchors.append(("clause", raw["clause-id"], None, raw.get("text-sha256")))
    return {"source": retro["source"], "old": retro["old"], "rows": rows, "anchors": anchors}


def parse_cases(snap):
    cases = {}
    for rel in snap.under(CASES_PREFIX):
        if not rel.endswith(".toml") or "/" in rel[len(CASES_PREFIX):]:
            raise GateError("{} at {}: only <id>.toml files belong in {}".format(rel, snap.label, CASES_DIR))
        case = parse_case(rel, snap.read(rel))
        if case.cid in cases:
            raise GateError("{}: duplicate case id {}".format(rel, case.cid))
        cases[case.cid] = case
    for case in cases.values():
        if case.kind != "control":
            continue
        target = cases.get(case.controls)
        if target is None or target.kind != "case":
            raise GateError("{}: controls {!r} names no case".format(case.rel, case.controls))
        differing = set(key for key in SCENARIO_KEYS if case.scenario[key] != target.scenario[key])
        if differing != set(case.delta):
            raise GateError("{}: the scenario differs from {} in {} but delta names {}".format(
                case.rel, target.cid, sorted(differing), sorted(case.delta)))
    return cases


# ---------------------------------------------------------------------------------------------- matcher

def facts(scenario):
    found = set()
    if scenario["op"] in RECORD_OPS:
        found.add("op:record-change")
    if any(len(items) >= 2 for _id, items in scenario["before"]):
        found.add("record:multi-item")
    if scenario["claim"] in ("completion", "bounded"):
        found.add("claim:completion")
    if scenario["claim"] == "bounded" and _closed(scenario["named-set"]):
        found.add("claim:bounded")
    if scenario["claim"] == "partial":
        found.add("claim:partial")
    if scenario["op"] == "wording-fix":
        found.add("fix:wording")
    return found


def _closed(named):
    """A named set is closed when the claim states its size and lists exactly that many members."""
    return named is not None and named[1] == len(named[0])


def _exemption_meets(eligible_rows, where, form, label):
    """Whether exempt:<label> disposes of an instance at where in form, under the triggered require rows
    that require instance:dispositioned or carry an exempt vocabulary. A row with no exempt vocabulary
    admits no label, so it keeps every instance strict. All four conditions must hold."""
    if not eligible_rows:
        return False
    vocab = frozenset.intersection(*[row.vocab if row.vocab is not None else frozenset()
                                     for row in eligible_rows])
    pairs = frozenset.intersection(*[row.pairs for row in eligible_rows])
    never = frozenset().union(*[row.never for row in eligible_rows])
    if label not in vocab:
        return False
    if (label, where) not in pairs:
        return False
    if where in never:
        return False
    if form != "quotation":
        return False
    return True


def outcome(rows, scenario):
    found = facts(scenario)
    triggered = [row for row in rows if row.when <= found]
    required = set()
    for row in triggered:
        if row.kind == "require":
            required |= row.effect
    suppressed, cancelled = set(), set()
    for row in triggered:
        if row.kind == "exempt":
            suppressed |= row.effect
        elif row.kind == "limit":
            cancelled |= row.effect
    required -= suppressed - cancelled
    eligible = [row for row in triggered if row.kind == "require"
                and (row.vocab is not None or "instance:dispositioned" in row.effect)]
    evidence = scenario["evidence"]
    unmet = []
    if "set:enumerated" in required and "index-enumeration" not in evidence:
        unmet.append("set:enumerated")
    if "count:reconciled" in required and not evidence & {"count-check", "index-enumeration"}:
        unmet.append("count:reconciled")
    if "item:accounted" in required:
        kept = set(item for _id, items in scenario["after"] for item in items)
        for item in sorted(set(item for _id, items in scenario["before"] for item in items)):
            if item not in kept and scenario["item-evidence"].get(item) != "own-evidence":
                unmet.append("item:accounted:" + item)
    if "instance:dispositioned" in required:
        if not scenario["probe"] >= frozenset(PROBE_PROPERTIES):
            unmet.append("instance:probed")
        for where, form, disposition in scenario["instances"]:
            met = disposition == "fixed" or (
                disposition.startswith("exempt:")
                and _exemption_meets(eligible, where, form, disposition[len("exempt:"):]))
            if not met:
                unmet.append("instance:dispositioned:" + where)
    if not unmet:
        return "ACCEPT"
    return "REJECT " + " ".join(sorted(unmet, key=lambda atom: atom.encode("utf-8")))


def decision(result):
    return "ACCEPT" if result == "ACCEPT" else "REJECT"


def without_clause(rows, cid):
    return [row for row in rows if row.clause != cid]


def reverted(head_rows, base_rows, cid):
    """HEAD's table with one clause's rows replaced by the base table's rows for it."""
    return without_clause(head_rows, cid) + [row for row in base_rows if row.clause == cid]


def _row_keys(rows, cid, without_sha=False):
    return sorted(row.key_without_sha if without_sha else row.key for row in rows if row.clause == cid)


# ---------------------------------------------------------------------------------------------- waivers

class Waiver:
    def __init__(self, raw, index):
        self.raw, self.index = raw, index
        self.wid, self.kind = raw["id"], raw["kind"]
        self.repeats = raw.get("repeats")
        if self.kind == RANGE_WAIVER_KIND:
            self.key = ("range", raw["path"], raw["side"], raw["start"], raw["end"], raw["bytes-sha256"],
                        raw["base-file-sha256"], raw["head-file-sha256"])
        else:
            self.key = ("clause", self.kind, raw["target"], raw["base-sha256"], raw["head-sha256"],
                        raw["base-anchor-sha256"], raw["head-anchor-sha256"])


def _digest_field(value, where, name, allow_absent=True):
    if not isinstance(value, str) or not (SHA_RE.fullmatch(value) or (allow_absent and value == ABSENT)):
        raise GateError("{}: {} is not 64 lowercase hex{}".format(where, name, " or absent" if allow_absent else ""))


def parse_waivers(data, label):
    doc = _toml(data, label)
    unknown = sorted(set(doc) - {"waiver"})
    if unknown:
        raise GateError("{}: unknown top-level key(s) {}".format(label, ", ".join(unknown)))
    rows = doc.get("waiver", [])
    if not isinstance(rows, list):
        raise GateError("{}: waiver is not an array of tables".format(label))
    out, ids = [], set()
    for index, raw in enumerate(rows):
        where = "{} row {}".format(label, index)
        if not isinstance(raw, dict):
            raise GateError("{}: not a table".format(where))
        wid = raw.get("id")
        if not isinstance(wid, str) or not NAME_RE.fullmatch(wid):
            raise GateError("{}: missing or malformed id".format(where))
        if wid in ids:
            raise GateError("{}: duplicate waiver id {}".format(where, wid))
        ids.add(wid)
        kind = raw.get("kind")
        if not isinstance(kind, str):
            raise GateError("{}: waiver kind {!r} is not a string".format(where, kind))
        if kind in RETIRED_WAIVER_KINDS:
            raise GateError("{}: kind {} is retired: {}".format(where, kind, RETIRED_WAIVER_KINDS[kind]))
        if kind not in WAIVER_KINDS:
            raise GateError("{}: kind {!r} is unknown".format(where, kind))
        if kind == RANGE_WAIVER_KIND:
            required = {"id", "kind", "path", "side", "start", "end", "bytes-sha256", "base-file-sha256",
                        "head-file-sha256", "category", "reason"}
            optional = {"repeats"}
        else:
            required = {"id", "kind", "target", "base-sha256", "head-sha256", "base-anchor-sha256",
                        "head-anchor-sha256", "reason"}
            optional = {"repeats", "successor"} if kind == "wording-only" else {"repeats"}
        if not required <= set(raw) or set(raw) - required - optional:
            raise GateError("{}: a {} waiver carries exactly {} (optional {})".format(
                where, kind, ", ".join(sorted(required)), ", ".join(sorted(optional))))
        if not isinstance(raw["reason"], str) or not raw["reason"].strip():
            raise GateError("{}: reason must be nonempty".format(where))
        if "repeats" in raw and (not isinstance(raw["repeats"], str) or not NAME_RE.fullmatch(raw["repeats"])):
            raise GateError("{}: repeats must name a waiver id".format(where))
        if kind == RANGE_WAIVER_KIND:
            if raw["side"] not in ("base", "head") or raw["category"] not in CATEGORIES:
                raise GateError("{}: side or category is unknown".format(where))
            if not (type(raw["start"]) is int and type(raw["end"]) is int and 0 <= raw["start"] < raw["end"]):
                raise GateError("{}: start and end must be integers with 0 <= start < end".format(where))
            if not isinstance(raw["path"], str) or not raw["path"].startswith(RULES_PREFIX):
                raise GateError("{}: path must name a rule file".format(where))
            _digest_field(raw["bytes-sha256"], where, "bytes-sha256", allow_absent=False)
            _digest_field(raw["base-file-sha256"], where, "base-file-sha256")
            _digest_field(raw["head-file-sha256"], where, "head-file-sha256")
        else:
            if not isinstance(raw["target"], str) or not CLAUSE_ID_RE.fullmatch(raw["target"]):
                raise GateError("{}: target is not a clause id".format(where))
            if "successor" in raw and (not isinstance(raw["successor"], str)
                                       or not CLAUSE_ID_RE.fullmatch(raw["successor"])):
                raise GateError("{}: successor is not a clause id".format(where))
            for name in ("base-sha256", "head-sha256", "base-anchor-sha256", "head-anchor-sha256"):
                _digest_field(raw[name], where, name)
        out.append(Waiver(raw, index))
    return out


def parse_budget(data, label):
    doc = _toml(data, label)
    unknown = sorted(set(doc) - set(BUDGET_KEYS.values()))
    if unknown:
        raise GateError("{}: unknown key(s) {}".format(label, ", ".join(unknown)))
    for key, value in doc.items():
        if type(value) is not int or value < 0:
            raise GateError("{}: {} must be a nonnegative integer".format(label, key))
    return dict((kind, doc.get(key, 0)) for kind, key in BUDGET_KEYS.items())


def _row_digest(row):
    """The digest that names one obligation row in coverage-exclusions.toml: its canonical JSON form."""
    return _text_sha(row.key)


def parse_exclusions(data, label):
    """(clause-id, row-sha256) -> reason, one per [[exclusion]] row; each carries exactly those three keys."""
    doc = _toml(data, label)
    unknown = sorted(set(doc) - {"exclusion"})
    if unknown:
        raise GateError("{}: unknown top-level key(s) {}".format(label, ", ".join(unknown)))
    rows = doc.get("exclusion", [])
    if not isinstance(rows, list):
        raise GateError("{}: exclusion is not an array of tables".format(label))
    out = {}
    for number, raw in enumerate(rows, 1):
        where = "{} exclusion {}".format(label, number)
        if not isinstance(raw, dict) or set(raw) != {"clause-id", "row-sha256", "reason"}:
            raise GateError("{}: an exclusion carries exactly clause-id, row-sha256 and reason".format(where))
        if not isinstance(raw["clause-id"], str) or not CLAUSE_ID_RE.fullmatch(raw["clause-id"]):
            raise GateError("{}: clause-id is not a clause id".format(where))
        _digest_field(raw["row-sha256"], where, "row-sha256", allow_absent=False)
        reason = raw["reason"]
        if not isinstance(reason, str) or not reason.strip():
            raise GateError("{}: an exclusion's reason must be nonempty".format(where))
        key = (raw["clause-id"], raw["row-sha256"])
        if key in out:
            raise GateError("{}: duplicate exclusion for {}".format(where, raw["clause-id"]))
        out[key] = reason
    return out


def _category_holds(data, start, end, category):
    """Whether bytes [start, end) of one rule file fall in a non-clause-text category: spaces and newlines
    (whitespace), the leading ----fenced block (frontmatter), or one ATX heading line, its line end included
    (heading)."""
    piece = data[start:end]
    if category == "whitespace":
        return all(byte in (0x20, 0x0A) for byte in piece)
    if category == "frontmatter":
        if not data.startswith(b"---\n"):
            return False
        at = 4
        while at < len(data):
            nxt = data.find(b"\n", at)
            line_end = len(data) if nxt < 0 else nxt
            if data[at:line_end] == b"---":
                block_end = line_end + 1 if nxt >= 0 else line_end
                return end <= block_end
            if nxt < 0:
                return False
            at = nxt + 1
        return False
    if category == "heading":
        line_start = data.rfind(b"\n", 0, start) + 1
        nxt = data.find(b"\n", line_start)
        line_end = len(data) if nxt < 0 else nxt + 1
        return ATX_HEADING_RE.match(data, line_start) is not None and end <= line_end
    return False


# ------------------------------------------------------------------------------------------------ report

class Report:
    def __init__(self):
        self.findings = []
        self.sections = {}

    def fail(self, text):
        self.findings.append(text)

    def note(self, section, text):
        self.sections.setdefault(section, []).append(text)


SECTION_ORDER = ("CHANGE", "BOUND", "REGISTERED", "ANCHOR MOVED", "WAIVED", "STALE", "ENCODING CHANGED",
                 "UNENCODED INVENTORY (not a waiver)", "RETRO (informational, never binding)", "UNCOVERED ROWS")


# ------------------------------------------------------------------------------------ the always-run legs

def check_schema(register, rows, cases, report):
    for row in rows:
        clause = register.get(row.clause)
        if clause is None:
            report.fail("obligation row for {}: no such clause in {}".format(row.clause, INVENTORY_REL))
        elif row.sha != clause.sha:
            report.fail("STALE obligation row for {}: text-sha256 does not match its canonical text".format(
                row.clause))
    for case in cases.values():
        for cid in case.clauses:
            if cid not in register:
                report.fail("case {} lists {}, which is not in {}".format(case.cid, cid, INVENTORY_REL))
        for cid in case.removes:
            if cid in register:
                report.fail("case {} removes {}, which is still in {}".format(case.cid, cid, INVENTORY_REL))


def check_premises(cases, report):
    """A scenario may not assert the fact a rule requires proving: a bounded claim lists its set, and a
    wording fix, or any listed instance, names the probe that found its instances. With the evidence
    present, the matcher decides whether it holds."""
    for case in cases.values():
        scenario = case.scenario
        if scenario["claim"] == "bounded" and scenario["named-set"] is None:
            report.fail("{} {}: claim = \"bounded\" with no named-set asserts the exemption bare; a bounded "
                        "claim counts only over the named, closed set it lists".format(case.kind, case.cid))
        if (scenario["op"] == "wording-fix" or scenario["instances"]) and not scenario["probe"]:
            report.fail("{} {}: its instances are asserted with no probe; they count only as the findings of a "
                        "probe with the properties its clause names".format(case.kind, case.cid))


def check_cases(rows, cases, report):
    for case in cases.values():
        got = outcome(rows, case.scenario)
        if got != case.head:
            report.fail("case {}: HEAD outcome is {!r}, expect.head is {!r}".format(case.cid, got, case.head))
    for case in cases.values():
        if case.kind == "control" and case.head == cases[case.controls].head:
            report.fail("control {}: its outcome does not differ from case {}".format(case.cid, case.controls))


def check_ablation(rows, cases, exclusions, report):
    listed = set()
    for case in cases.values():
        if case.kind != "case":
            continue
        full = outcome(rows, case.scenario)
        for cid in case.clauses:
            listed.add(cid)
            if outcome(without_clause(rows, cid), case.scenario) == full:
                report.fail("case {}: listed clause {} is not load bearing (removing its rows leaves the "
                            "outcome unchanged)".format(case.cid, cid))
    matched = set()
    for index, row in enumerate(rows):
        if row.clause not in listed:
            key = (row.clause, _row_digest(row))
            if key in exclusions:
                matched.add(key)
                report.note("UNCOVERED ROWS", "{} ({} row {}) excluded: {}".format(
                    row.clause, row.kind, index + 1, exclusions[key]))
            else:
                report.fail("obligation row {} for {} is uncovered: no case lists {}; add a case that does, or "
                            "an exclusion with its reason (row-sha256 {})".format(
                                index + 1, row.clause, row.clause, key[1]))
            continue
        others = rows[:index] + rows[index + 1:]
        if not any(outcome(others, case.scenario) != outcome(rows, case.scenario)
                   for case in cases.values() if case.kind == "case" and row.clause in case.clauses):
            report.fail("obligation row {} for {} is dead: no case listing {} depends on it".format(
                index + 1, row.clause, row.clause))
    for clause, digest in sorted(set(exclusions) - matched):
        report.fail("coverage exclusion for {} (row-sha256 {}) names no uncovered row".format(clause, digest))


def check_retro(rows, cases, history, report):
    for case in cases.values():
        if case.retro is None:
            continue
        retro = case.retro
        if history is None:
            raise GateError("case {}: retro needs git history and none is available".format(case.cid))
        if not history.has_commit(retro["source"]):
            raise GateError("case {}: retro source {} is not in the history".format(case.cid, retro["source"]))
        if not history.is_ancestor(retro["source"], history.head()):
            report.fail("case {}: retro source {} is not an ancestor of HEAD".format(case.cid, retro["source"]))
            continue
        pinned = history.snapshot(retro["source"])
        pinned_register = load_register(pinned)
        anchored = True
        for anchor in retro["anchors"]:
            if anchor[0] == "clause":
                clause = pinned_register.get(anchor[1])
                if clause is None or clause.sha != anchor[3]:
                    report.fail("case {}: retro anchor {} does not match {} at {}".format(
                        case.cid, anchor[1], INVENTORY_REL, retro["source"]))
                    anchored = False
            else:
                blob = pinned.read(anchor[1])
                needle = anchor[2].encode("utf-8")
                if blob is None or blob.count(needle) != 1 or _sha(needle) != anchor[3]:
                    report.fail("case {}: retro span anchor in {} does not occur exactly once at {} with "
                                "its text-sha256".format(case.cid, anchor[1], retro["source"]))
                    anchored = False
        if not anchored:
            continue
        old_rows = retro["rows"]
        got = outcome(old_rows, case.scenario)
        if got != retro["old"]:
            report.fail("case {}: retro outcome is {!r}, retro.old is {!r}".format(case.cid, got, retro["old"]))
            continue
        if retro["old"] == case.head:
            report.fail("case {}: retro.old equals expect.head; a retro records a difference".format(case.cid))
            continue
        head_out = outcome(rows, case.scenario)
        clauses = sorted(set(row.clause for row in rows) | set(row.clause for row in old_rows))
        causes = [cid for cid in clauses if _row_keys(rows, cid) != _row_keys(old_rows, cid)
                  and outcome(reverted(rows, old_rows, cid), case.scenario) != head_out]
        if not causes:
            report.fail("case {}: no clause whose encoding differs accounts for the retro difference".format(
                case.cid))
            continue
        report.note("RETRO (informational, never binding)", "{}: {} at {} -> {} at HEAD; caused by {}".format(
            case.cid, retro["old"], retro["source"][:12], head_out, " ".join(causes)))


# ----------------------------------------------------------------------------------------------- binding

def _subtract(low, high, spans):
    """The maximal runs of [low, high) that lie in no span."""
    pieces, at = [], low
    for start, end in sorted(spans):
        if end <= at or start >= high:
            continue
        if start > at:
            pieces.append((at, start))
        at = max(at, end)
    if at < high:
        pieces.append((at, high))
    return pieces


def _touches(ranges, points, start, end):
    return (any(low < end and high > start for low, high in ranges)
            or any(start < point < end for point in points))


def _maps(alignment, base_span, head_span):
    start, end = base_span
    for i1, i2, j1, _j2 in alignment:
        if i1 <= start and end <= i2 and head_span == (j1 + (start - i1), j1 + (end - i1)):
            return True
    return False


class Change:
    """The rule-byte change between two revisions: changed ranges, insertion points, spans and residue."""

    def __init__(self, base, head, base_register, head_register):
        self.base, self.head = base, head
        self.ranges = {"base": {}, "head": {}}
        self.points = {"base": {}, "head": {}}
        self.alignment = {}
        paths = sorted(set(base.under(RULES_PREFIX)) | set(head.under(RULES_PREFIX)))
        for path in paths:
            old, new = base.read(path), head.read(path)
            if old == new:
                self.alignment[path] = [(0, len(old), 0, len(new))]
                continue
            base_ranges, head_ranges, base_points, head_points, equal = [], [], [], [], []
            if old is None:
                head_ranges.append((0, len(new)))
            elif new is None:
                base_ranges.append((0, len(old)))
            else:
                matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
                for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                    if tag == "equal":
                        equal.append((i1, i2, j1, j2))
                    if tag in ("replace", "delete"):
                        base_ranges.append((i1, i2))
                    if tag in ("replace", "insert"):
                        head_ranges.append((j1, j2))
                    if tag == "insert":
                        base_points.append(i1)
                    if tag == "delete":
                        head_points.append(j1)
                self.alignment[path] = equal
            self.ranges["base"][path], self.ranges["head"][path] = base_ranges, head_ranges
            self.points["base"][path], self.points["head"][path] = base_points, head_points
        self.spans = {"base": {}, "head": {}}
        for side, snap, register in (("base", base, base_register), ("head", head, head_register)):
            for cid, clause in register.items():
                self.spans[side][cid] = (clause.path,) + locate(snap.read(clause.path), clause, snap.label)

    def touched(self, side, cid):
        path, start, end = self.spans[side][cid]
        return _touches(self.ranges[side].get(path, ()), self.points[side].get(path, ()), start, end)

    def wholly_changed(self, cid):
        path, start, end = self.spans["head"][cid]
        return not _subtract(start, end, self.ranges["head"].get(path, ()))

    def moved(self, base_clause, head_clause):
        """Whether an anchor moved: a changed source path, a span that is not the exact image of its base
        span under the alignment, or window line bounds that moved relative to the text."""
        cid = base_clause.cid
        if base_clause.path != head_clause.path:
            return True
        path, s, e = self.spans["base"][cid]
        _path, s2, e2 = self.spans["head"][cid]
        if not _maps(self.alignment.get(path, ()), (s, e), (s2, e2)):
            return True
        base_starts = _line_starts(self.base.read(path))
        head_starts = _line_starts(self.head.read(path))
        base_offsets = (base_clause.start - _line_of(base_starts, s), base_clause.end - _line_of(base_starts, e - 1))
        head_offsets = (head_clause.start - _line_of(head_starts, s2), head_clause.end - _line_of(head_starts, e2 - 1))
        return base_offsets != head_offsets

    def residue(self):
        """Every changed byte outside every clause span on its side, as (path, side, start, end) pieces."""
        pieces = []
        for side in ("base", "head"):
            for path, ranges in sorted(self.ranges[side].items()):
                spans = [(s, e) for p, s, e in self.spans[side].values() if p == path]
                for low, high in ranges:
                    for start, end in _subtract(low, high, spans):
                        pieces.append((path, side, start, end))
        return pieces


def _change_set(change, base_register, head_register):
    """C (clause id -> reasons), the anchor-moved set M, and the registered-only set N."""
    reasons, moved = {}, set()
    for cid in sorted(set(base_register) | set(head_register)):
        old, new = base_register.get(cid), head_register.get(cid)
        why = []
        if old is not None and new is None:
            why.append("removed")
        if old is not None and new is not None and old.sha != new.sha:
            why.append("text changed")
        if old is not None and change.touched("base", cid):
            why.append("touched at base")
        if new is not None and change.touched("head", cid):
            why.append("touched at head" if old is not None else "new over changed bytes")
        if old is not None and new is not None and old.sha == new.sha and change.moved(old, new):
            why.append("anchor moved")
            moved.add(cid)
        if why:
            reasons[cid] = why
    registered = sorted(cid for cid in set(head_register) - set(base_register) if cid not in reasons)
    return reasons, moved, registered


def _changed_cases(cases, base):
    return [case for case in cases.values() if base.read(case.rel) != case.data]


def _why_unflipped(base_out, head_out, revert_out, cid):
    """Why a listing case binds nothing: the same outcome on both sides, outcomes that differ only in their
    unmet atoms, or a decision flip that restoring the clause's base rows does not undo."""
    if base_out == head_out:
        return "its base and HEAD outcomes are both {!r}".format(head_out)
    if decision(base_out) == decision(head_out):
        return "unmet atoms differ only (base {!r}, HEAD {!r})".format(base_out, head_out)
    return "the decision flips, but restoring the base rows of {} leaves {!r}, so the flip is not on {}".format(
        cid, revert_out, cid)


def check_binding(head, base, head_register, rows, cases, waivers, report):
    base_register = load_register(base)
    change = Change(base, head, base_register, head_register)
    reasons, moved, registered = _change_set(change, base_register, head_register)
    residue = change.residue()
    for cid in sorted(reasons):
        report.note("CHANGE", "{}: {}".format(cid, ", ".join(reasons[cid])))
    for cid in sorted(moved):
        old, new = base_register[cid], head_register[cid]
        report.note("ANCHOR MOVED", "{}: {} {}-{} -> {} {}-{}".format(
            cid, old.path, old.start, old.end, new.path, new.start, new.end))
    for cid in registered:
        report.note("REGISTERED", cid)
        if any(row.clause == cid for row in rows) and not any(cid in case.clauses for case in cases.values()):
            report.fail("registered clause {} has obligation rows but no case lists it".format(cid))
    genesis = base.read(SCRIPT_REL) is None and base.read(TABLE_REL) is None
    if genesis:
        if reasons or residue:
            report.fail("rule edit in the harness genesis change: {} changed clause(s) and {} residue "
                        "piece(s); the genesis change alters no rule byte".format(len(reasons), len(residue)))
        for waiver in waivers:
            report.fail("waiver {} matches nothing in this change".format(waiver.wid))
        return {"genesis": True, "bound": 0, "waived": {}, "repeats": 0}
    if base.read(TABLE_REL) is None:
        raise GateError("the base has {} but no {}".format(SCRIPT_REL, TABLE_REL))
    base_rows = parse_table(base.read(TABLE_REL), "{} at {}".format(TABLE_REL, base.label))
    for cid in sorted(set(row.clause for row in rows) | set(row.clause for row in base_rows)):
        if cid in base_register and cid in head_register and base_register[cid].sha == head_register[cid].sha \
                and _row_keys(rows, cid) != _row_keys(base_rows, cid):
            report.note("ENCODING CHANGED", cid)
    for case in cases.values():
        for cid in case.removes:
            if cid not in base_register:
                report.fail("case {} removes {}, which is not in {} at the base".format(
                    case.cid, cid, INVENTORY_REL))
    changed = _changed_cases(cases, base)
    for case in changed:
        if case.kind == "case" and case.base is not None:
            got = outcome(base_rows, case.scenario)
            if got != case.base:
                report.fail("case {}: base outcome is {!r}, expect.base is {!r}".format(case.cid, got, case.base))

    def has_base_rows(cid):
        return any(row.clause == cid for row in base_rows)

    def bindable(cid):
        old, new = base_register.get(cid), head_register.get(cid)
        if old is not None and new is not None:
            return has_base_rows(cid)
        if old is not None:
            return has_base_rows(cid)
        return change.wholly_changed(cid)

    bound, unflipped = {}, {}
    for cid in sorted(reasons):
        if not bindable(cid):
            continue
        for case in changed:
            if case.kind != "case" or case.base is None or (cid not in case.clauses and cid not in case.removes):
                continue
            head_out, base_out = outcome(rows, case.scenario), outcome(base_rows, case.scenario)
            if head_out != case.head or base_out != case.base:
                continue
            revert_out = outcome(reverted(rows, base_rows, cid), case.scenario)
            if decision(base_out) != decision(head_out) and decision(revert_out) != decision(head_out):
                bound.setdefault(cid, []).append(case.cid)
                report.note("BOUND", "{} by case {} (unmet base: {}; head: {}; with {} reverted: {})".format(
                    cid, case.cid, base_out, head_out, cid, revert_out))
            else:
                unflipped.setdefault(cid, (case.cid, _why_unflipped(base_out, head_out, revert_out, cid)))
    waived, repeats = _check_waivers(change, reasons, moved, residue, base_register, head_register, rows,
                                     base_rows, bound, bindable, base, head, waivers, report)
    tiled = waived.pop("__tiled__")
    for cid in sorted(reasons):
        if cid in bound or cid in waived:
            continue
        if not bindable(cid):
            report.fail("no base encoding for {}: land its registration and encoding in an earlier change "
                        "that alters no rule byte, then edit".format(cid))
        elif cid in unflipped:
            report.fail("case {} does not flip the decision for {}; {}".format(unflipped[cid][0], cid,
                                                                                unflipped[cid][1]))
        else:
            report.fail("changed clause {} is not bound: add a new or changed case that lists it, whose base "
                        "and head decisions differ and flip back when its base rows are restored".format(cid))
    for path, side, start, end in residue:
        for low, high in _subtract(start, end, tiled.get((path, side), ())):
            if side == "head":
                report.fail("unregistered rule text at {} head [{},{}): register it as a clause".format(
                    path, low, high))
            else:
                report.fail("unregistered rule text removed at {} base [{},{}): register it in an earlier "
                            "change".format(path, low, high))
    counts = dict((kind, sum(1 for value in waived.values() if value == kind)) for kind in CLAUSE_WAIVER_KINDS)
    counts[RANGE_WAIVER_KIND] = sum(len(v) for v in tiled.values())
    return {"genesis": False, "bound": len(bound), "waived": counts, "repeats": repeats}


def _check_waivers(change, reasons, moved, residue, base_register, head_register, rows, base_rows, bound,
                   bindable, base, head, head_waivers, report):
    base_data = base.read(WAIVERS_REL)
    base_waivers = parse_waivers(base_data, "{} at {}".format(WAIVERS_REL, base.label)) if base_data is not None else []
    budget_data = base.read(BUDGET_REL)
    ceilings = parse_budget(budget_data, "{} at {}".format(BUDGET_REL, base.label)) if budget_data is not None \
        else dict((kind, 0) for kind in BUDGET_KEYS)
    waived = {"__tiled__": {}}
    for index, carried in enumerate(base_waivers):
        if index >= len(head_waivers) or head_waivers[index].raw != carried.raw:
            report.fail("waiver log is append-only: row {} ({}) changed or removed".format(index, carried.wid))
            return waived, 0
    live = head_waivers[len(base_waivers):]
    for carried in head_waivers[:len(base_waivers)]:
        report.note("STALE", "{} (carried; discharges nothing)".format(carried.wid))
    if live and base.read(BUDGET_REL) != head.read(BUDGET_REL):
        report.fail("budget change must land alone: {} changed together with {} live waiver(s)".format(
            BUDGET_REL, len(live)))
    repeats = 0
    live_keys = {}
    for waiver in live:
        if waiver.key in live_keys:
            report.fail("waivers {} and {} carry the same transition in one change".format(
                live_keys[waiver.key], waiver.wid))
        live_keys[waiver.key] = waiver.wid
        earlier = [w for w in head_waivers[:waiver.index] if w.key == waiver.key]
        named = next((w for w in head_waivers[:waiver.index] if w.wid == waiver.repeats), None)
        if earlier and waiver.repeats is None:
            report.fail("waiver {} repeats the transition of {}; a new waiver for it must say so".format(
                waiver.wid, earlier[-1].wid))
        elif waiver.repeats is not None and named is None:
            report.fail("waiver {}: repeats names {}, which is no earlier row".format(waiver.wid, waiver.repeats))
        elif waiver.repeats is not None and (not earlier or named is not earlier[-1]):
            report.fail("waiver {}: repeats names {}, not the latest earlier row with this transition".format(
                waiver.wid, waiver.repeats))
        elif waiver.repeats is not None:
            repeats += 1
    for kind in WAIVER_KINDS:
        count = sum(1 for waiver in live if waiver.kind == kind)
        if count > ceilings[kind]:
            report.fail("waiver ceiling exceeded: {} live {} waiver(s), base ceiling {}".format(
                count, kind, ceilings[kind]))
    for waiver in live:
        if waiver.kind == RANGE_WAIVER_KIND:
            _check_range_waiver(waiver, change, residue, base, head, waived["__tiled__"], report)
        else:
            _check_clause_waiver(waiver, change, reasons, moved, base_register, head_register, rows, base_rows,
                                 bound, bindable, waived, report)
    return waived, repeats


def _check_clause_waiver(waiver, change, reasons, moved, base_register, head_register, rows, base_rows, bound,
                         bindable, waived, report):
    cid, raw = waiver.raw["target"], waiver.raw
    old, new = base_register.get(cid), head_register.get(cid)
    if cid not in reasons:
        report.fail("waiver {} matches nothing in this change".format(waiver.wid))
        return
    expected = {"base-sha256": old.sha if old else ABSENT, "head-sha256": new.sha if new else ABSENT,
                "base-anchor-sha256": old.anchor if old else ABSENT,
                "head-anchor-sha256": new.anchor if new else ABSENT}
    wrong = [name for name, value in sorted(expected.items()) if raw[name] != value]
    if wrong:
        report.fail("waiver {}: digest mismatch for {} ({})".format(waiver.wid, cid, ", ".join(wrong)))
        return
    if cid in bound:
        report.fail("waiver {}: {} is bound by a case; a bound clause needs no waiver".format(waiver.wid, cid))
        return
    encoded = any(row.clause == cid for row in base_rows)
    text_changed = old is None or new is None or old.sha != new.sha
    if waiver.kind == "wording-only":
        if not encoded:
            report.fail("waiver {}: wording-only needs a base encoding for {}; use unencoded".format(waiver.wid, cid))
            return
        if not text_changed:
            report.fail("waiver {}: the text of {} is unchanged, so wording-only does not apply; an anchor "
                        "move with unchanged rows uses anchor-only".format(waiver.wid, cid))
            return
        if new is None:
            successor = raw.get("successor")
            want = sorted(json.dumps(dict((k, v) for k, v in row.raw.items()
                                          if k not in ("clause-id", "text-sha256")), sort_keys=True)
                          for row in base_rows if row.clause == cid)
            got = sorted(json.dumps(dict((k, v) for k, v in row.raw.items()
                                         if k not in ("clause-id", "text-sha256")), sort_keys=True)
                         for row in rows if row.clause == successor)
            if successor is None or want != got:
                report.fail("waiver {}: a removal waived as wording-only names a successor whose HEAD rows "
                            "equal the base rows of {}".format(waiver.wid, cid))
                return
        elif _row_keys(rows, cid, True) != _row_keys(base_rows, cid, True):
            report.fail("waiver {}: the encoding of {} changed, so wording-only does not apply".format(
                waiver.wid, cid))
            return
    elif waiver.kind == "unencoded":
        if encoded:
            report.fail("waiver {}: {} has a base encoding; unencoded does not apply".format(waiver.wid, cid))
            return
        if bindable(cid) and any(row.clause == cid for row in rows):
            report.fail("waiver {}: {} is bindable; bind it".format(waiver.wid, cid))
            return
    elif waiver.kind == "anchor-only":
        if text_changed:
            report.fail("waiver {}: the text of {} changed; use wording-only or bind it".format(waiver.wid, cid))
            return
        if cid not in moved:
            report.fail("waiver {}: the anchor of {} did not move".format(waiver.wid, cid))
            return
        if not encoded or _row_keys(rows, cid) != _row_keys(base_rows, cid):
            report.fail("waiver {}: anchor-only needs identical base and HEAD rows for {}".format(waiver.wid, cid))
            return
    waived[cid] = waiver.kind
    report.note("WAIVED", "{} by {} {}{}: {}".format(cid, waiver.kind, waiver.wid,
                                                       " (repeats {})".format(waiver.repeats) if waiver.repeats else "",
                                                       raw["reason"]))


def _check_range_waiver(waiver, change, residue, base, head, tiled, report):
    raw = waiver.raw
    path, side, start, end = raw["path"], raw["side"], raw["start"], raw["end"]
    data = (base if side == "base" else head).read(path)
    piece = next(((s, e) for p, sd, s, e in residue if p == path and sd == side and s <= start and end <= e), None)
    if piece is None:
        spans = [(s, e) for p, s, e in change.spans[side].values() if p == path]
        if data is not None and any(s < end and e > start for s, e in spans):
            report.fail("waiver {}: the tile [{},{}) covers clause text in {}".format(waiver.wid, start, end, path))
        else:
            report.fail("waiver {} matches nothing in this change".format(waiver.wid))
        return
    base_file, head_file = base.read(path), head.read(path)
    expected = {"base-file-sha256": _sha(base_file) if base_file is not None else ABSENT,
                "head-file-sha256": _sha(head_file) if head_file is not None else ABSENT,
                "bytes-sha256": _sha(data[start:end])}
    wrong = [name for name, value in sorted(expected.items()) if raw[name] != value]
    if wrong:
        report.fail("waiver {}: digest mismatch for {} [{},{}) ({})".format(waiver.wid, path, start, end,
                                                                            ", ".join(wrong)))
        return
    if not _category_holds(data, start, end, raw["category"]):
        report.fail("waiver {}: {} [{},{}) is not {} text".format(waiver.wid, path, start, end, raw["category"]))
        return
    have = tiled.setdefault((path, side), [])
    if any(s < end and e > start for s, e in have):
        report.fail("waiver {}: the tile [{},{}) overlaps another tile in {}".format(waiver.wid, start, end, path))
        return
    have.append((start, end))
    report.note("WAIVED", "{} {} [{},{}) by non-clause-text {} ({}){}: {}".format(
        path, side, start, end, waiver.wid, raw["category"],
        " (repeats {})".format(waiver.repeats) if waiver.repeats else "", raw["reason"]))


# ------------------------------------------------------------------------------------------------- run

def evaluate(head, base=None, history=None):
    """Run every leg over a HEAD snapshot (and, for binding, a base snapshot). Returns (exit code, text)."""
    report = Report()
    try:
        register = load_register(head)
        rows = parse_table(head.read(TABLE_REL), TABLE_REL)
        cases = parse_cases(head)
        waivers = parse_waivers(head.read(WAIVERS_REL), WAIVERS_REL)
        parse_budget(head.read(BUDGET_REL), BUDGET_REL)
        exclusions = parse_exclusions(head.read(EXCLUSIONS_REL), EXCLUSIONS_REL)
        check_schema(register, rows, cases, report)
        binding = None
        encoded = set(row.clause for row in rows)
        missing = sorted(cid for cid in register if cid not in encoded)
        if not report.findings:
            check_premises(cases, report)
            check_cases(rows, cases, report)
            check_ablation(rows, cases, exclusions, report)
            report.note("UNENCODED INVENTORY (not a waiver)", "{} clause(s): {}".format(
                len(missing), " ".join(missing)))
            check_retro(rows, cases, history, report)
            if base is not None:
                binding = check_binding(head, base, register, rows, cases, waivers, report)
    except GateError as exc:
        return 2, "error: rule-behaviour cannot evaluate ({}); fail-closed\n".format(exc)
    out = []
    for section in SECTION_ORDER:
        for line in report.sections.get(section, ()):
            out.append("{}: {}".format(section, line))
    if binding is None:
        state = "not applicable (no --base)" if base is None else "not run"
        out.append("binding {}".format(state))
    else:
        counts = binding["waived"]
        out.append("binding against {}{}: bound {}; waived {} (wording-only {}, unencoded {}, anchor-only {}, "
                   "non-clause-text {}; repeats {}); inventory {}".format(
                       base.label, " (genesis)" if binding["genesis"] else "", binding["bound"],
                       sum(counts.values()), counts.get("wording-only", 0), counts.get("unencoded", 0),
                       counts.get("anchor-only", 0), counts.get(RANGE_WAIVER_KIND, 0), binding["repeats"],
                       len(missing)))
    if report.findings:
        out.append("FAIL: rule-behaviour {} finding(s)".format(len(report.findings)))
        out.extend("  " + finding for finding in report.findings)
        return 1, "\n".join(out) + "\n"
    out.append("PASS: rule-behaviour checked {} case file(s) against {} obligation row(s)".format(
        len(cases), len(rows)))
    return 0, "\n".join(out) + "\n"


def run(root, base_ref=None):
    """The command-line gate over the working tree at root."""
    try:
        head = worktree_snapshot(root)
        history = GitHistory(root)
        base = None
        if base_ref is not None:
            oid = history.resolve(base_ref)
            history.require_full_history()
            base = history.snapshot(oid)
    except GateError as exc:
        print("error: rule-behaviour cannot evaluate ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    code, text = evaluate(head, base, history)
    (sys.stderr if code == 2 else sys.stdout).write(text)
    return code


def run_each_commit(root, base_ref):
    """The push leg: every commit on HEAD's first-parent chain after base_ref, oldest first, each read from
    history and judged by every leg against its own first parent, so a rule edit in any pushed commit needs
    its own case or waiver. The exit is the worst over the commits."""
    try:
        history = GitHistory(root)
        base = history.resolve(base_ref)
        history.require_full_history()
        transitions = history.first_parent_range(base, history.head())
    except GateError as exc:
        print("error: rule-behaviour cannot evaluate ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    worst, failed = 0, []
    for parent, commit in transitions:
        try:
            code, text = evaluate(history.snapshot(commit), history.snapshot(parent), history)
        except GateError as exc:
            code, text = 2, "error: rule-behaviour cannot evaluate ({}); fail-closed\n".format(exc)
        print("COMMIT {} against its first parent {}: exit {}".format(commit, parent, code))
        sys.stdout.write("".join("  " + line + "\n" for line in text.splitlines()))
        if code != 0:
            failed.append(commit)
        worst = max(worst, code)
    if failed:
        print("FAIL: rule-behaviour push range {}..{}: {} of {} commit(s) fail: {}".format(
            base[:12], transitions[-1][1][:12], len(failed), len(transitions), " ".join(failed)))
    else:
        print("PASS: rule-behaviour push range {}..{}: {} commit(s), each judged against its first parent".format(
            base[:12], transitions[-1][1][:12], len(transitions)))
    return worst


# SELF-TEST-BEGINS
# Everything below builds synthetic trees and checks the gate above against them. A mutant is the gate
# source above this marker with one check removed; the self-test loads each one as a scratch module and
# requires the vector it names to fail.

def _tk(key):
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else _tv(key)


def _tv(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    if isinstance(value, list):
        return "[" + ", ".join(_tv(item) for item in value) + "]"
    return "{ " + ", ".join("{} = {}".format(_tk(k), _tv(v)) for k, v in value.items()) + " }"


def _doc(top):
    """A TOML document: scalar and list keys first, then one [section] per table-valued key."""
    lines = ["{} = {}".format(_tk(k), _tv(v)) for k, v in top.items() if not isinstance(v, dict)]
    for key, value in top.items():
        if isinstance(value, dict):
            lines += ["", "[{}]".format(key)] + ["{} = {}".format(_tk(k), _tv(v)) for k, v in value.items()]
    return "\n".join(lines) + "\n"


def _array(name, rows):
    return "".join("[[{}]]\n".format(name) + "".join("{} = {}\n".format(_tk(k), _tv(v)) for k, v in row.items())
                   + "\n" for row in rows)


class _Tree:
    """A synthetic revision, read the way the gate reads a snapshot."""

    def __init__(self, files, label):
        self.files, self.label = files, label

    def read(self, rel):
        return self.files.get(rel)

    def under(self, prefix):
        return sorted(rel for rel in self.files if rel.startswith(prefix))


ALPHA = RULES_PREFIX + "fixalpha.md"
ALPHA_TEXT = ("---\ncorpus-id: fixalpha\n---\n\n# Alpha rule\n\nAlpha must hold. Beta must hold.\n\n"
              "Gamma must hold.\n")
X, Y, Z, W = "fixalpha.1", "fixalpha.2", "fixalpha.3", "fixalpha.4"
ZEROS = {"wording-only-per-change": 0, "unencoded-per-change": 0, "anchor-only-per-change": 0,
         "non-clause-text-per-change": 0}


class _Fx:
    """A synthetic repository: rule files, a register located from them, a table, cases and waivers."""

    def __init__(self):
        self.rules, self.clauses, self.rows, self.cases, self.waivers = {}, {}, [], {}, []
        self.budget, self.script, self.table, self.raw = dict(ZEROS), True, True, {}
        self.born, self.gone, self.exclusions, self.excluded = [], [], [], {}

    def copy(self):
        twin = _Fx()
        twin.rules, twin.clauses = dict(self.rules), json.loads(json.dumps(self.clauses))
        twin.rows, twin.cases = json.loads(json.dumps(self.rows)), json.loads(json.dumps(self.cases))
        twin.waivers = json.loads(json.dumps(self.waivers))
        twin.exclusions, twin.excluded = json.loads(json.dumps(self.exclusions)), dict(self.excluded)
        twin.budget = None if self.budget is None else dict(self.budget)
        twin.script, twin.table, twin.raw = self.script, self.table, dict(self.raw)
        twin.born, twin.gone = list(self.born), list(self.gone)
        return twin

    def edit(self, rel, old, new):
        assert self.rules[rel].count(old) == 1, (rel, old)
        self.rules[rel] = self.rules[rel].replace(old, new)
        return self

    def lines(self, cid):
        rel, text, start, end = self.clauses[cid]
        if start is not None:
            return start, end
        source = self.rules[rel]
        assert text in source, (cid, text)
        start = source[:source.index(text)].count("\n") + 1
        return start, start + text.count("\n")

    def span(self, cid):
        """[s, e) of the clause text in its file, located from its window's first line."""
        rel, text, _start, _end = self.clauses[cid]
        data = self.rules[rel].encode("utf-8")
        start, _end2 = self.lines(cid)
        offset = sum(len(line) + 1 for line in data.split(b"\n")[:start - 1])
        at = data.index(text.encode("utf-8"), offset)
        return at, at + len(text.encode("utf-8"))

    def sha(self, cid):
        return _sha(self.clauses[cid][1].encode("utf-8")) if cid in self.clauses else ABSENT

    def anchor(self, cid):
        if cid not in self.clauses:
            return ABSENT
        rel, text, _s, _e = self.clauses[cid]
        start, end = self.lines(cid)
        return _sha("{}\n{}\n{}\n{}".format(rel, start, end, text).encode("utf-8"))

    def file_sha(self, rel):
        return _sha(self.rules[rel].encode("utf-8")) if rel in self.rules else ABSENT

    def build(self, label="HEAD"):
        files = dict((rel, text.encode("utf-8")) for rel, text in self.rules.items())
        register = []
        for cid, (rel, text, _s, _e) in self.clauses.items():
            start, end = self.lines(cid)
            register.append({"clause-id": cid, "corpus-id": cid.split(".")[0], "source-path": rel,
                             "start-line": start, "end-line": end, "canonical-text": text,
                             "source-digest": self.file_sha(rel)})
        files[INVENTORY_REL] = _array("clause", register).encode("utf-8")
        exclusions = list(self.exclusions)
        if self.table:
            rows = []
            for row in self.rows:
                row = dict(row)
                cid = row.get("clause-id")
                if "text-sha256" not in row and cid in self.clauses:
                    row = dict([("clause-id", cid), ("text-sha256", self.sha(cid))]
                               + [(k, v) for k, v in row.items() if k != "clause-id"])
                rows.append(row)
                if cid in self.excluded:
                    # the row's digest, computed here independently of the gate: its canonical JSON form
                    exclusions.append({"clause-id": cid, "row-sha256": _sha(json.dumps(row, sort_keys=True)
                                                                            .encode("utf-8")),
                                       "reason": self.excluded[cid]})
            files[TABLE_REL] = _array("row", rows).encode("utf-8")
        for cid, case in self.cases.items():
            files[CASES_PREFIX + cid + ".toml"] = (case if isinstance(case, str) else _doc(case)).encode("utf-8")
        files[WAIVERS_REL] = ("# log\n" + _array("waiver", self.waivers)).encode("utf-8")
        files[EXCLUSIONS_REL] = ("# exclusions\n" + _array("exclusion", exclusions)).encode("utf-8")
        if self.budget is not None:
            files[BUDGET_REL] = "".join("{} = {}\n".format(k, v) for k, v in self.budget.items()).encode("utf-8")
        if self.script:
            files[SCRIPT_REL] = b"# the gate\n"
        files.update(self.raw)
        return _Tree(dict((k, v) for k, v in files.items() if v is not None), label)


def _req(cid, when, atoms, params=None):
    row = {"clause-id": cid, "kind": "require", "when": when, "require": atoms}
    if params is not None:
        row["params"] = params
    return row


def _case(cid, clauses, scenario, head, base=None, removes=None, **extra):
    doc = {"id": cid, "summary": "fixture case " + cid, "kind": "case", "clauses": clauses}
    if removes is not None:
        doc["removes"] = removes
    doc.update(extra)
    doc["scenario"] = scenario
    doc["expect"] = {"head": head} if base is None else {"head": head, "base": base}
    return doc


def _control(cid, controls, delta, scenario, head):
    return {"id": cid, "summary": "fixture control " + cid, "kind": "control", "controls": controls,
            "delta": delta, "scenario": scenario, "expect": {"head": head}}


def _alpha():
    """The common fixture: three clauses on fixalpha.md, X and Y encoded, Z not, one case each for X, Y."""
    fx = _Fx()
    fx.rules[ALPHA] = ALPHA_TEXT
    fx.clauses = {X: [ALPHA, "Alpha must hold.", None, None], Y: [ALPHA, "Beta must hold.", None, None],
                  Z: [ALPHA, "Gamma must hold.", None, None]}
    fx.rows = [_req(X, ["claim:completion"], ["count:reconciled"]), _req(Y, ["claim:partial"], ["set:enumerated"])]
    fx.cases = {"k-x": _case("k-x", [X], {"claim": "completion"}, "REJECT count:reconciled"),
                "k-y": _case("k-y", [Y], {"claim": "partial"}, "REJECT set:enumerated")}
    return fx


def _cw(wid, kind, cid, base, head, **extra):
    row = {"id": wid, "kind": kind, "target": cid, "base-sha256": base.sha(cid), "head-sha256": head.sha(cid),
           "base-anchor-sha256": base.anchor(cid), "head-anchor-sha256": head.anchor(cid)}
    row.update(extra)
    row.setdefault("reason", "reviewed: the change keeps the obligation")
    return row


def _rw(wid, rel, side, start, end, base, head, category, **extra):
    data = (base if side == "base" else head).rules[rel].encode("utf-8")
    row = {"id": wid, "kind": "non-clause-text", "path": rel, "side": side, "start": start, "end": end,
           "bytes-sha256": _sha(data[start:end]), "base-file-sha256": base.file_sha(rel),
           "head-file-sha256": head.file_sha(rel), "category": category}
    row.update(extra)
    row.setdefault("reason", "reviewed: layout only")
    return row


def _pieces(base, head, rel):
    """Independently of the gate: the changed bytes of one rule file outside every clause span, per side."""
    old = base.rules.get(rel, "").encode("utf-8")
    new = head.rules.get(rel, "").encode("utf-8")
    ranges = {"base": [], "head": []}
    if rel not in base.rules:
        ranges["head"].append((0, len(new)))
    elif rel not in head.rules:
        ranges["base"].append((0, len(old)))
    else:
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
            if tag in ("replace", "delete"):
                ranges["base"].append((i1, i2))
            if tag in ("replace", "insert"):
                ranges["head"].append((j1, j2))
    out = []
    for side, fx in (("base", base), ("head", head)):
        spans = [fx.span(cid) for cid, value in fx.clauses.items() if value[0] == rel]
        for low, high in ranges[side]:
            covered = set(i for s, e in spans for i in range(s, e))
            run = None
            for i in range(low, high + 1):
                inside = i < high and i not in covered
                if inside and run is None:
                    run = i
                elif not inside and run is not None:
                    out.append((side, run, i))
                    run = None
    return out


def _tile_all(base, head, rel, prefix="t"):
    """One whitespace tile per residue piece (each piece asserted to be spaces and newlines)."""
    tiles = []
    for number, (side, start, end) in enumerate(_pieces(base, head, rel), 1):
        data = (base if side == "base" else head).rules[rel].encode("utf-8")[start:end]
        assert set(data) <= {0x20, 0x0A}, (side, start, end, data)
        tiles.append(_rw("{}{}".format(prefix, number), rel, side, start, end, base, head, "whitespace"))
    return tiles


def _run(gate, head, base=None, history=None):
    try:
        return gate.evaluate(head.build("HEAD") if isinstance(head, _Fx) else head,
                             None if base is None else (base.build("BASE") if isinstance(base, _Fx) else base),
                             history)
    except Exception as exc:  # a crash is a vector failure, never a pass
        return "crashed ({}: {})".format(type(exc).__name__, exc), ""


def _expect(gate, head, base=None, code=0, needles=(), absent=(), history=None):
    got, text = _run(gate, head, base, history)
    ok = got == code and all(n in text for n in needles) and not any(n in text for n in absent)
    return ok, (got, text)


VECTORS = []


def _vector(name, git=False):
    def register(function):
        VECTORS.append((name, function, git))
        return function
    return register


# ------------------------------------------------------------------------------------------ schema

def _schema_head(change):
    fx = _alpha()
    change(fx)
    return fx


@_vector("schema/obligation-unknown-key-refused")
def _v(gate):
    fx = _schema_head(lambda fx: fx.rows[0].update(note="extra"))
    return _expect(gate, fx, code=2, needles=["unknown key(s) note"])


@_vector("schema/obligation-unknown-predicate-refused")
def _v(gate):
    fx = _schema_head(lambda fx: fx.rows[0].update(when=["claim:everything"]))
    return _expect(gate, fx, code=2, needles=["unknown value(s) claim:everything"])


@_vector("schema/obligation-unknown-atom-refused")
def _v(gate):
    fx = _schema_head(lambda fx: fx.rows[0].update(require=["set:counted"]))
    return _expect(gate, fx, code=2, needles=["unknown value(s) set:counted"])


@_vector("schema/limit-cancels-non-exempt-refused")
def _v(gate):
    fx = _schema_head(lambda fx: fx.rows.append({"clause-id": Z, "kind": "limit", "when": ["claim:bounded"],
                                                 "cancels": ["set:enumerated"]}))
    return _expect(gate, fx, code=2, needles=["cancels an atom no exempt row suppresses"])


@_vector("schema/case-unknown-fact-refused")
def _v(gate):
    fx = _schema_head(lambda fx: fx.cases["k-x"]["scenario"].update(claim="total"))
    return _expect(gate, fx, code=2, needles=["claim 'total' is unknown"])


@_vector("schema/instance-unknown-where-refused")
def _v(gate):
    fx = _schema_head(lambda fx: fx.cases["k-x"]["scenario"].update(
        instances=[{"where": "readme", "form": "operative", "disposition": "fixed"}]))
    return _expect(gate, fx, code=2, needles=["instance where 'readme' is unknown"])


@_vector("schema/duplicate-case-id-refused")
def _v(gate):
    fx = _alpha()
    fx.cases["k-x2"] = _case("k-x", [X], {"claim": "completion"}, "REJECT count:reconciled")
    return _expect(gate, fx, code=2, needles=["k-x2.toml: id must equal the file stem"])


@_vector("schema/stale-text-sha-fails")
def _v(gate):
    fx = _schema_head(lambda fx: fx.rows[0].update({"text-sha256": "0" * 64}))
    return _expect(gate, fx, code=1, needles=["STALE obligation row for fixalpha.1"])


@_vector("schema/clause-missing-from-register-fails")
def _v(gate):
    fx = _schema_head(lambda fx: fx.rows.append(_req("fixalpha.9", ["claim:partial"], ["count:reconciled"])
                                                | {"text-sha256": "1" * 64}))
    return _expect(gate, fx, code=1, needles=["obligation row for fixalpha.9: no such clause"])


@_vector("schema/malformed-toml-exit-2")
def _v(gate):
    fx = _alpha()
    fx.raw[TABLE_REL] = b"[[row]\n"
    return _expect(gate, fx, code=2, needles=["obligations.toml: malformed"])


@_vector("schema/control-delta-mismatch-refused")
def _v(gate):
    fx = _alpha()
    fx.cases["k-x-c"] = _control("k-x-c", "k-x", ["claim"], {"claim": "completion", "evidence": ["count-check"]},
                                 "ACCEPT")
    return _expect(gate, fx, code=2, needles=["but delta names ['claim']"])


@_vector("schema/prior-table-refused")
def _v(gate):
    fx = _schema_head(lambda fx: fx.cases["k-x"].update(prior={"outcome": "ACCEPT"}))
    return _expect(gate, fx, code=2, needles=["key(s) prior not allowed on a case"])


@_vector("schema/expect-base-on-control-refused")
def _v(gate):
    fx = _alpha()
    fx.cases["k-x-c"] = _control("k-x-c", "k-x", ["evidence"], {"claim": "completion", "evidence": ["count-check"]},
                                 "ACCEPT")
    fx.cases["k-x-c"]["expect"]["base"] = "ACCEPT"
    return _expect(gate, fx, code=2, needles=["expect key(s) base not allowed on a control"])


@_vector("schema/case-empty-clauses-and-empty-removes-refused")
def _v(gate):
    fx = _alpha()
    fx.cases["k-none"] = _case("k-none", [], {"claim": "none"}, "ACCEPT")
    return _expect(gate, fx, code=2, needles=["lists no clauses and removes nothing"])


@_vector("schema/case-empty-clauses-with-removes-accepted")
def _v(gate):
    fx = _alpha()
    fx.cases["k-rm"] = _case("k-rm", [], {"claim": "none"}, "ACCEPT", removes=["fixalpha.9"])
    return _expect(gate, fx, code=0, needles=["PASS: rule-behaviour checked 3 case file(s)"])


@_vector("schema/removes-id-in-head-register-fails")
def _v(gate):
    fx = _alpha()
    fx.cases["k-rm"] = _case("k-rm", [], {"claim": "none"}, "ACCEPT", removes=[Z])
    return _expect(gate, fx, code=1, needles=["case k-rm removes fixalpha.3, which is still in"])


@_vector("schema/removes-id-absent-at-base-fails")
def _v(gate):
    base = _alpha()
    head = base.copy()
    head.cases["k-rm"] = _case("k-rm", [], {"claim": "none"}, "ACCEPT", removes=["fixalpha.9"])
    return _expect(gate, head, base, code=1, needles=["case k-rm removes fixalpha.9, which is not in"])


@_vector("schema/removes-on-control-refused")
def _v(gate):
    fx = _alpha()
    fx.cases["k-x-c"] = _control("k-x-c", "k-x", ["evidence"], {"claim": "completion", "evidence": ["count-check"]},
                                 "ACCEPT")
    fx.cases["k-x-c"]["removes"] = ["fixalpha.9"]
    return _expect(gate, fx, code=2, needles=["key(s) removes not allowed on a control"])


def _wording_case(instances):
    return {"op": "wording-fix", "instances": instances}


@_vector("schema/instance-missing-form-refused")
def _v(gate):
    fx = _schema_head(lambda fx: fx.cases["k-x"].update(scenario=_wording_case(
        [{"where": "rule", "disposition": "fixed"}])))
    return _expect(gate, fx, code=2, needles=["an instance has no form"])


@_vector("schema/instance-unknown-form-refused")
def _v(gate):
    fx = _schema_head(lambda fx: fx.cases["k-x"].update(scenario=_wording_case(
        [{"where": "rule", "form": "paraphrase", "disposition": "fixed"}])))
    return _expect(gate, fx, code=2, needles=["instance form 'paraphrase' is unknown"])


def _params_head(params):
    return _schema_head(lambda fx: fx.rows.append(_req(Z, ["fix:wording"], ["instance:dispositioned"], params)))


@_vector("schema/exempt-on-label-outside-vocab-refused")
def _v(gate):
    fx = _params_head({"exempt-vocab": ["history"], "exempt-on": [{"label": "history", "where": "changelog-quote"},
                                                                  {"label": "warning", "where": "correction-note"}]})
    return _expect(gate, fx, code=2, needles=["exempt-on label 'warning' is outside exempt-vocab"])


@_vector("schema/exempt-vocab-label-without-surface-refused")
def _v(gate):
    fx = _params_head({"exempt-vocab": ["history", "warning"],
                       "exempt-on": [{"label": "history", "where": "changelog-quote"}]})
    return _expect(gate, fx, code=2, needles=["an exempt-vocab label has no exempt-on surface"])


@_vector("schema/exempt-on-unknown-where-refused")
def _v(gate):
    fx = _params_head({"exempt-vocab": ["history"], "exempt-on": [{"label": "history", "where": "readme"}]})
    return _expect(gate, fx, code=2, needles=["exempt-on where 'readme' is unknown"])


@_vector("schema/exempt-on-duplicate-pair-refused")
def _v(gate):
    fx = _params_head({"exempt-vocab": ["history"], "exempt-on": [{"label": "history", "where": "changelog-quote"},
                                                                  {"label": "history", "where": "changelog-quote"}]})
    return _expect(gate, fx, code=2, needles=["duplicate exempt-on pair"])


@_vector("schema/exempt-vocab-without-exempt-on-refused")
def _v(gate):
    fx = _params_head({"exempt-vocab": ["history"]})
    return _expect(gate, fx, code=2, needles=["exempt-vocab without exempt-on"])


# A value of the wrong TOML type is malformed input (exit 2), never a crash.
@_vector("schema/obligation-kind-not-a-string-exit-2")
def _v(gate):
    fx = _schema_head(lambda fx: fx.rows[0].update(kind=["require"]))
    return _expect(gate, fx, code=2, needles=["kind ['require'] is not require, exempt or limit"])


@_vector("schema/exempt-on-label-not-a-string-exit-2")
def _v(gate):
    fx = _params_head({"exempt-vocab": ["history"], "exempt-on": [{"label": ["history"], "where": "changelog-quote"}]})
    return _expect(gate, fx, code=2, needles=["exempt-on label ['history'] is outside exempt-vocab"])


@_vector("schema/named-set-unknown-key-refused")
def _v(gate):
    fx = _schema_head(lambda fx: fx.cases["k-x"]["scenario"].update({"named-set": dict(NAMED, closed=True)}))
    return _expect(gate, fx, code=2, needles=["named-set holds members and an optional count"])


@_vector("schema/named-set-member-malformed-refused")
def _v(gate):
    fx = _schema_head(lambda fx: fx.cases["k-x"]["scenario"].update({"named-set": {"members": ["Module A"]}}))
    return _expect(gate, fx, code=2, needles=["named-set members must match [a-z0-9-]+"])


@_vector("schema/named-set-count-not-positive-refused")
def _v(gate):
    fx = _schema_head(lambda fx: fx.cases["k-x"]["scenario"].update({"named-set": dict(NAMED, count=0)}))
    return _expect(gate, fx, code=2, needles=["named-set count must be a positive integer"])


@_vector("schema/probe-unknown-property-refused")
def _v(gate):
    fx = _schema_head(lambda fx: fx.cases["k-x"]["scenario"].update(probe=["matches-across-tabs"]))
    return _expect(gate, fx, code=2, needles=["probe: unknown value(s) matches-across-tabs"])


def _excluded_head(exclusion):
    return _schema_head(lambda fx: setattr(fx, "exclusions", [exclusion]))


EXCLUSION = {"clause-id": Z, "row-sha256": "0" * 64, "reason": "reviewed: not reached"}


@_vector("schema/exclusion-unknown-key-refused")
def _v(gate):
    fx = _excluded_head(dict(EXCLUSION, note="extra"))
    return _expect(gate, fx, code=2, needles=["an exclusion carries exactly clause-id, row-sha256 and reason"])


@_vector("schema/exclusion-malformed-clause-id-refused")
def _v(gate):
    fx = _excluded_head(dict(EXCLUSION, **{"clause-id": "Z"}))
    return _expect(gate, fx, code=2, needles=["clause-id is not a clause id"])


@_vector("schema/exclusion-malformed-digest-refused")
def _v(gate):
    fx = _excluded_head(dict(EXCLUSION, **{"row-sha256": "0" * 63}))
    return _expect(gate, fx, code=2, needles=["row-sha256 is not 64 lowercase hex"])


@_vector("schema/exclusion-without-reason-refused")
def _v(gate):
    fx = _excluded_head(dict(EXCLUSION, reason=" "))
    return _expect(gate, fx, code=2, needles=["an exclusion's reason must be nonempty"])


@_vector("schema/exclusion-duplicate-refused")
def _v(gate):
    fx = _schema_head(lambda fx: setattr(fx, "exclusions", [EXCLUSION, dict(EXCLUSION, reason="again")]))
    return _expect(gate, fx, code=2, needles=["duplicate exclusion for fixalpha.3"])


@_vector("schema/budget-negative-ceiling-exit-2")
def _v(gate):
    fx = _schema_head(lambda fx: fx.budget.update({"wording-only-per-change": -1}))
    return _expect(gate, fx, code=2, needles=["wording-only-per-change must be a nonnegative integer"])


def _write_tree(fx, root):
    """Write a fixture's files under root, as a working tree the gate reads from disk."""
    for rel, data in fx.build().files.items():
        (Path(root) / rel).parent.mkdir(parents=True, exist_ok=True)
        (Path(root) / rel).write_bytes(data)


def _run_root(gate, root, *args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = gate.run(root, *args)
    return code, out.getvalue() + err.getvalue()


@_vector("schema/worktree-symlink-input-exit-2")
def _v(gate):
    with tempfile.TemporaryDirectory(prefix="rule-behaviour-tree-") as directory:
        _write_tree(_alpha(), directory)
        table = Path(directory) / TABLE_REL
        table.rename(table.with_name("obligations-real.toml"))
        os.symlink("obligations-real.toml", table)
        got = _run_root(gate, directory)
    return got[0] == 2 and "obligations.toml: not a regular file" in got[1], got


@_vector("schema/worktree-symlink-entry-exit-2")
def _v(gate):
    with tempfile.TemporaryDirectory(prefix="rule-behaviour-tree-") as directory:
        _write_tree(_alpha(), directory)
        link = Path(directory) / CASES_PREFIX / "k-link.toml"
        os.symlink("k-x.toml", link)
        got = _run_root(gate, directory)
    return got[0] == 2 and "k-link.toml: not a regular file or directory" in got[1], got


# ----------------------------------------------------------------------------------------- premise

def _premise_head(row, scenario, head):
    """X and Y covered by their cases; one more row for Z and a case k-p listing Z over the scenario. The
    case expects what the scenario asserts, so only the evidence it carries decides whether that holds."""
    fx = _alpha()
    fx.rows.append(row)
    fx.cases["k-p"] = _case("k-p", [Z], scenario, head)
    return fx


BOUNDED_ROW = {"clause-id": Z, "kind": "exempt", "when": ["claim:bounded"], "suppresses": ["count:reconciled"]}


def _wf_row():
    return _req(Z, ["fix:wording"], ["instance:dispositioned"], EVG_PARAMS)


@_vector("premise/bounded-without-named-set-fails")
def _v(gate):
    fx = _premise_head(BOUNDED_ROW, {"claim": "bounded"}, "ACCEPT")
    return _expect(gate, fx, code=1, needles=["case k-p: claim = \"bounded\" with no named-set asserts the exemption "
                                              "bare"])


@_vector("premise/bounded-with-named-set-passes")
def _v(gate):
    fx = _premise_head(BOUNDED_ROW, BOUNDED, "ACCEPT")
    return _expect(gate, fx, code=0, absent=["asserts the exemption bare", "FAIL"])


@_vector("premise/wording-fix-without-probe-fails")
def _v(gate):
    fx = _premise_head(_wf_row(), {"op": "wording-fix", "instances": [
        {"where": "rule", "form": "operative", "disposition": "fixed"}]}, "ACCEPT")
    return _expect(gate, fx, code=1, needles=["case k-p: its instances are asserted with no probe"])


@_vector("premise/wording-fix-with-probe-passes")
def _v(gate):
    fx = _premise_head(_wf_row(), {"op": "wording-fix", "probe": PROBE, "instances": [
        {"where": "rule", "form": "operative", "disposition": "unfixed"}]}, "REJECT instance:dispositioned:rule")
    return _expect(gate, fx, code=0, absent=["asserted with no probe", "FAIL"])


@_vector("premise/listed-instances-without-probe-fail")
def _v(gate):
    fx = _alpha()
    fx.cases["k-x"]["scenario"]["instances"] = [{"where": "rule", "form": "operative", "disposition": "fixed"}]
    return _expect(gate, fx, code=1, needles=["case k-x: its instances are asserted with no probe"])


# ----------------------------------------------------------------------------------------- matcher

SHA_FILL = "a" * 64
NAMED = {"members": ["mod-a", "mod-b", "mod-c"], "count": 3}
PROBE = list(PROBE_PROPERTIES)
EVG_PARAMS = {"exempt-vocab": ["history", "warning"], "never-exempt": ["register-copy"],
              "exempt-on": [{"label": "history", "where": "changelog-quote"},
                            {"label": "history", "where": "mistakes-register"},
                            {"label": "warning", "where": "correction-note"}]}
SEED_ROWS = [
    _req("setcmp.1", ["claim:completion"], ["set:enumerated"]),
    _req("setcmp.3", ["claim:completion"], ["count:reconciled"]),
    _req("setcmp.9", ["op:record-change", "record:multi-item"], ["item:accounted"]),
    {"clause-id": "setcmp.11", "kind": "exempt", "when": ["claim:bounded"],
     "suppresses": ["set:enumerated", "count:reconciled", "item:accounted"]},
    {"clause-id": "setcmp.10", "kind": "limit", "when": ["claim:bounded", "op:record-change"],
     "cancels": ["item:accounted"]},
    _req("evgcmp.15", ["fix:wording"], ["instance:dispositioned"], EVG_PARAMS),
]


def _rows(gate, raw_rows):
    return gate.parse_table(_array("row", [dict(r, **{"text-sha256": SHA_FILL}) for r in raw_rows])
                            .encode("utf-8"), "fixture table")


def _scenario(gate, raw):
    return gate.parse_scenario(raw, "fixture scenario")


RETIRE = {"op": "retire", "before": [{"id": "R", "items": ["a", "b"]}], "after": [{"id": "R", "items": ["a", "c"]}],
          "evidence": ["id-check", "count-check"]}
MERGE = {"op": "merge", "claim": "bounded", "named-set": NAMED,
         "before": [{"id": "R1", "items": ["a", "b"]}, {"id": "R2", "items": ["c"]}],
         "after": [{"id": "R3", "items": ["a", "c"]}], "evidence": ["id-check"]}
WORDING = {"op": "wording-fix", "probe": PROBE, "instances": [
    {"where": "rule", "form": "operative", "disposition": "fixed"},
    {"where": "register-copy", "form": "operative", "disposition": "exempt:history"},
    {"where": "changelog-quote", "form": "quotation", "disposition": "exempt:history"}]}
BOUNDED = {"claim": "bounded", "named-set": NAMED}


def _outcome(gate, scenario, raw_rows=SEED_ROWS):
    try:
        return gate.outcome(_rows(gate, raw_rows), _scenario(gate, scenario))
    except Exception as exc:
        return "crashed ({})".format(type(exc).__name__)


def _matcher(name, scenario, want, raw_rows=SEED_ROWS):
    @_vector("matcher/" + name)
    def _v(gate):
        got = _outcome(gate, scenario, raw_rows)
        return got == want, got


_matcher("retire-in-place-head-rejects", RETIRE, "REJECT item:accounted:b")
_matcher("retire-own-evidence-accepts", dict(RETIRE, **{"item-evidence": {"b": "own-evidence"}}), "ACCEPT")
_matcher("merge-bounded-head-rejects", MERGE, "REJECT item:accounted:b")
_matcher("merge-own-evidence-accepts", dict(MERGE, **{"item-evidence": {"b": "own-evidence"}}), "ACCEPT")
_matcher("bounded-named-set-accepts", BOUNDED, "ACCEPT")
_matcher("completion-named-set-rejects", {"claim": "completion"}, "REJECT count:reconciled set:enumerated")
_matcher("wording-register-copy-head-rejects", WORDING, "REJECT instance:dispositioned:register-copy")
_matcher("wording-register-copy-fixed-accepts", {"op": "wording-fix", "probe": PROBE, "instances": [
    WORDING["instances"][0], {"where": "register-copy", "form": "operative", "disposition": "fixed"},
    WORDING["instances"][2]]}, "ACCEPT")
_matcher("bounded-derives-completion", BOUNDED, "REJECT set:enumerated",
         [_req("setcmp.1", ["claim:completion"], ["set:enumerated"])])
# A bounded claim is exempt only over a closed named set: with no set, an open one (no stated count) or a
# miscounted one, it is a completion claim and carries the whole burden.
_matcher("bounded-without-named-set-rejects", {"claim": "bounded"}, "REJECT count:reconciled set:enumerated")
_matcher("bounded-open-set-rejects", {"claim": "bounded", "named-set": {"members": NAMED["members"]}},
         "REJECT count:reconciled set:enumerated")
_matcher("bounded-miscounted-set-rejects", {"claim": "bounded", "named-set": dict(NAMED, count=4)},
         "REJECT count:reconciled set:enumerated")
# A wording fix's instances count only as the findings of a probe with every property its clause names.
_matcher("wording-fix-without-probe-rejects", {"op": "wording-fix", "instances": []}, "REJECT instance:probed")
_matcher("wording-fix-probe-missing-one-property-rejects", {"op": "wording-fix", "probe": PROBE[:-1], "instances": [
    {"where": "rule", "form": "operative", "disposition": "fixed"}]}, "REJECT instance:probed")
_matcher("wording-fix-no-instances-with-probe-accepts", {"op": "wording-fix", "probe": PROBE}, "ACCEPT")
# A require row with no exempt vocabulary stays strict when another triggered row carries one.
_matcher("strict-row-stays-strict-beside-vocab-row",
         {"op": "wording-fix", "probe": PROBE, "instances": [
             {"where": "changelog-quote", "form": "quotation", "disposition": "exempt:history"}]},
         "REJECT instance:dispositioned:changelog-quote",
         SEED_ROWS + [_req("evgcmp.16", ["fix:wording"], ["instance:dispositioned"])])
# A where the row names in never-exempt stays strict even when one of its exempt-on pairs names that where.
_matcher("never-exempt-overrides-exempt-on-pair-rejects", {"op": "wording-fix", "probe": PROBE, "instances": [
    {"where": "changelog-quote", "form": "quotation", "disposition": "exempt:history"}]},
    "REJECT instance:dispositioned:changelog-quote",
    [_req("evgcmp.15", ["fix:wording"], ["instance:dispositioned"],
          {"exempt-vocab": ["history"], "never-exempt": ["changelog-quote"],
           "exempt-on": [{"label": "history", "where": "changelog-quote"}]})])


def _one_instance(where, form, label):
    return {"op": "wording-fix", "probe": PROBE,
            "instances": [{"where": where, "form": form, "disposition": "exempt:" + label}]}


for _name, _where, _form, _label, _want in (
        ("rule-quotation-exempt-history-rejects", "rule", "quotation", "history", False),
        ("rule-operative-exempt-warning-rejects", "rule", "operative", "warning", False),
        ("register-copy-quotation-exempt-history-rejects", "register-copy", "quotation", "history", False),
        ("changelog-quote-operative-exempt-history-rejects", "changelog-quote", "operative", "history", False),
        ("correction-note-quotation-exempt-history-rejects", "correction-note", "quotation", "history", False),
        ("changelog-quote-quotation-exempt-warning-rejects", "changelog-quote", "quotation", "warning", False),
        ("mistakes-register-quotation-exempt-warning-rejects", "mistakes-register", "quotation", "warning", False),
        ("exempt-correction-label-at-head-rejects", "correction-note", "quotation", "correction", False),
        ("changelog-quote-quotation-exempt-history-accepts", "changelog-quote", "quotation", "history", True),
        ("correction-note-quotation-exempt-warning-accepts", "correction-note", "quotation", "warning", True)):
    _matcher(_name, _one_instance(_where, _form, _label),
             "ACCEPT" if _want else "REJECT instance:dispositioned:" + _where)


@_vector("matcher/row-order-independent")
def _v(gate):
    scenarios = [RETIRE, MERGE, WORDING, BOUNDED, {"claim": "completion"}, {"claim": "bounded"},
                 dict(MERGE, claim="completion"), dict(RETIRE, claim="bounded", **{"named-set": NAMED})]
    orders = [SEED_ROWS, list(reversed(SEED_ROWS)), SEED_ROWS[3:] + SEED_ROWS[:3], SEED_ROWS[1::2] + SEED_ROWS[::2]]
    table = [[_outcome(gate, scenario, order) for order in orders] for scenario in scenarios]
    return all(len(set(row)) == 1 and not row[0].startswith("crashed") for row in table), table


@_vector("matcher/exemption-eligibility-matrix")
def _v(gate):
    accepted, wrong = [], []
    for where in WHERES:
        for label in LABELS:
            for form in FORMS:
                got = _outcome(gate, _one_instance(where, form, label))
                if got == "ACCEPT":
                    accepted.append((label, where, form))
                elif got != "REJECT instance:dispositioned:" + where:
                    wrong.append((label, where, form, got))
    want = [("history", "changelog-quote", "quotation"), ("history", "mistakes-register", "quotation"),
            ("warning", "correction-note", "quotation")]
    return sorted(accepted) == sorted(want) and not wrong and len(WHERES) * len(LABELS) * len(FORMS) == 30, \
        (accepted, wrong)


@_vector("matcher/exempt-on-intersection-across-rows")
def _v(gate):
    second = _req("evgcmp.16", ["fix:wording"], ["instance:dispositioned"],
                  {"exempt-vocab": ["history"], "exempt-on": [{"label": "history", "where": "mistakes-register"}]})
    one = _one_instance("changelog-quote", "quotation", "history")
    both = _one_instance("mistakes-register", "quotation", "history")
    got = (_outcome(gate, one, SEED_ROWS + [second]), _outcome(gate, both, SEED_ROWS + [second]))
    return got == ("REJECT instance:dispositioned:changelog-quote", "ACCEPT"), got


@_vector("matcher/decision-of-outcome")
def _v(gate):
    got = [gate.decision(value) for value in ("ACCEPT", "REJECT set:enumerated",
                                              "REJECT count:reconciled item:accounted:b set:enumerated")]
    return got == ["ACCEPT", "REJECT", "REJECT"], got


# ---------------------------------------------------------------------------------------- ablation

@_vector("ablation/listed-clause-not-load-bearing-fails")
def _v(gate):
    fx = _alpha()
    fx.cases["k-z"] = _case("k-z", [Y], {"claim": "completion"}, "REJECT count:reconciled")
    return _expect(gate, fx, code=1, needles=["case k-z: listed clause fixalpha.2 is not load bearing"])


@_vector("ablation/dead-row-fails")
def _v(gate):
    fx = _alpha()
    fx.rows.append(_req(X, ["claim:completion", "claim:bounded"], ["count:reconciled"]))
    return _expect(gate, fx, code=1, needles=["obligation row 3 for fixalpha.1 is dead"])


def _uncovered(gate, exclude=True, reason="reviewed: no scenario in this table reaches it yet"):
    """X and Y covered by their cases; a third row for Z, which no case lists, with or without its exclusion."""
    fx = _alpha()
    fx.rows.append(_req(Z, ["claim:bounded"], ["set:enumerated"]))
    digest = gate._row_digest(gate.parse_table(fx.build().read(TABLE_REL), "fixture table")[2])
    if exclude:
        fx.exclusions = [{"clause-id": Z, "row-sha256": digest, "reason": reason}]
    return fx, digest


@_vector("ablation/every-listed-clause-load-bearing")
def _v(gate):
    fx, _digest = _uncovered(gate)
    return _expect(gate, fx, code=0, needles=["UNCOVERED ROWS: fixalpha.3 (require row 3) excluded: reviewed: no "
                                              "scenario"], absent=["not load bearing", "is dead"])


@_vector("ablation/uncovered-row-without-exclusion-fails")
def _v(gate):
    fx, digest = _uncovered(gate, exclude=False)
    return _expect(gate, fx, code=1, needles=["obligation row 3 for fixalpha.3 is uncovered: no case lists fixalpha.3",
                                              "(row-sha256 {})".format(digest)])


@_vector("ablation/exclusion-naming-no-uncovered-row-fails")
def _v(gate):
    fx = _alpha()
    digest = gate._row_digest(gate.parse_table(fx.build().read(TABLE_REL), "fixture table")[0])
    fx.exclusions = [{"clause-id": X, "row-sha256": digest, "reason": "reviewed: stale"}]
    return _expect(gate, fx, code=1, needles=["coverage exclusion for fixalpha.1 (row-sha256 {}) names no uncovered "
                                              "row".format(digest)])


@_vector("ablation/exclusion-for-changed-row-fails")
def _v(gate):
    fx, digest = _uncovered(gate)
    fx.rows[2] = _req(Z, ["claim:bounded"], ["count:reconciled"])
    return _expect(gate, fx, code=1, needles=["obligation row 3 for fixalpha.3 is uncovered",
                                              "coverage exclusion for fixalpha.3 (row-sha256 {})".format(digest)])


@_vector("ablation/empty-clauses-removal-case-vacuous")
def _v(gate):
    fx = _alpha()
    fx.cases["k-rm"] = _case("k-rm", [], {"claim": "completion"}, "REJECT count:reconciled", removes=["fixalpha.9"])
    return _expect(gate, fx, code=0, absent=["not load bearing"])


def _limit_fixture(exempt_atoms):
    fx = _Fx()
    fx.rules[ALPHA] = ALPHA_TEXT
    fx.clauses = {X: [ALPHA, "Alpha must hold.", None, None], Y: [ALPHA, "Beta must hold.", None, None],
                  Z: [ALPHA, "Gamma must hold.", None, None]}
    fx.rows = [_req(X, ["claim:completion"], ["set:enumerated", "count:reconciled"]),
               _req(X, ["op:record-change", "record:multi-item"], ["item:accounted"]),
               {"clause-id": Y, "kind": "exempt", "when": ["claim:bounded"], "suppresses": exempt_atoms},
               {"clause-id": Z, "kind": "limit", "when": ["claim:bounded", "op:record-change"],
                "cancels": ["item:accounted"]}]
    fx.cases = {"k-merge": _case("k-merge", [Z, Y], MERGE, "REJECT item:accounted:b")}
    fx.excluded = {X: "reviewed: the requirements this fixture's exemption and limit act on"}
    return fx


@_vector("ablation/atom-only-change-counts-for-coverage-not-binding")
def _v(gate):
    base = _limit_fixture(["set:enumerated", "item:accounted"])
    base.cases = {}
    head = _limit_fixture(["set:enumerated", "count:reconciled", "item:accounted"])
    head.edit(ALPHA, "Beta must hold.", "Beta must still hold.")
    head.clauses[Y][1] = "Beta must still hold."
    head.cases["k-merge"]["expect"]["base"] = "REJECT count:reconciled item:accounted:b"
    coverage = _expect(gate, head, code=0, absent=["not load bearing"])
    binding = _expect(gate, head, base, code=1, needles=[
        "case k-merge does not flip the decision for fixalpha.2; unmet atoms differ only"])
    return coverage[0] and binding[0], (coverage[1], binding[1])


# ------------------------------------------------------------------------------------------ change

def _insert_at(fx, cid, offset, text, new_clause_text=None):
    """Insert text into a clause's file at its span start plus offset (offset may be negative or past it)."""
    rel = fx.clauses[cid][0]
    start, _end = fx.span(cid)
    data = fx.rules[rel].encode("utf-8")
    at = start + offset
    fx.rules[rel] = (data[:at] + text.encode("utf-8") + data[at:]).decode("utf-8")
    if new_clause_text is not None:
        fx.clauses[cid][1] = new_clause_text
    return fx


def _replace_at(fx, cid, low, high, text, new_clause_text=None):
    """Replace bytes [span start + low, span start + high) of a clause's file."""
    rel = fx.clauses[cid][0]
    start, _end = fx.span(cid)
    data = fx.rules[rel].encode("utf-8")
    fx.rules[rel] = (data[:start + low] + text.encode("utf-8") + data[start + high:]).decode("utf-8")
    if new_clause_text is not None:
        fx.clauses[cid][1] = new_clause_text
    return fx


def _change_lines(text, cid):
    return [line for line in text.splitlines() if line.startswith("CHANGE: " + cid + ":")]


@_vector("change/same-line-insert-after-setcmp6-must-register")
def _v(gate):
    base = _alpha()
    head = base.copy().edit(ALPHA, "Alpha must hold. Beta", "Alpha must hold. Delta may vary. Beta")
    ok, detail = _expect(gate, head, base, code=1, needles=["unregistered rule text at {} head".format(ALPHA)])
    return ok and not _change_lines(detail[1], X) and not _change_lines(detail[1], Y), detail


@_vector("change/same-line-insert-registered-binds")
def _v(gate):
    base = _alpha()
    base.budget["non-clause-text-per-change"] = 1
    head = base.copy().edit(ALPHA, "Alpha must hold. Beta", "Alpha must hold. Delta may vary. Beta")
    head.clauses[W] = [ALPHA, "Delta may vary.", None, None]
    head.rows.append(_req(W, ["fix:wording"], ["set:enumerated"]))
    head.cases["k-w"] = _case("k-w", [W], WF, "REJECT set:enumerated", base="ACCEPT")
    head.waivers = _tile_all(base, head, ALPHA)
    return _expect(gate, head, base, code=0, needles=["BOUND: fixalpha.4 by case k-w", "non-clause-text 1"])


@_vector("change/span-interior-edit-marks-clause")
def _v(gate):
    base = _alpha()
    head = base.copy().edit(ALPHA, "Gamma must hold.", "Gamma must always hold.")
    head.clauses[Z][1] = "Gamma must always hold."
    ok, detail = _expect(gate, head, base, code=1, needles=["no base encoding for fixalpha.3"])
    lines = _change_lines(detail[1], Z)
    return ok and len(lines) == 1 and "touched at base" in lines[0], detail


def _boundary(gate, offset, insert=True, low=0, high=0, text="Q", clause=X, new_text=None):
    base = _alpha()
    head = base.copy()
    if insert:
        _insert_at(head, clause, offset, text, new_text)
    else:
        _replace_at(head, clause, low, high, text, new_text)
    return base, head, _run(gate, head, base)


@_vector("change/insert-at-span-start-goes-to-U")
def _v(gate):
    base, head, (code, text) = _boundary(gate, 0)
    start, _e = base.span(X)
    return (code == 1 and not _change_lines(text, X)
            and "unregistered rule text at {} head [{},{})".format(ALPHA, start, start + 1) in text), text


@_vector("change/insert-one-after-span-start-marks-clause")
def _v(gate):
    base, head, (code, text) = _boundary(gate, 1, new_text="AQlpha must hold.")
    lines = _change_lines(text, X)
    return code == 1 and len(lines) == 1 and "touched at base" in lines[0] and "unregistered" not in text, text


@_vector("change/insert-one-before-span-end-marks-clause")
def _v(gate):
    base, head, (code, text) = _boundary(gate, len("Alpha must hold"), new_text="Alpha must holdQ.")
    lines = _change_lines(text, X)
    return code == 1 and len(lines) == 1 and "touched at base" in lines[0] and "unregistered" not in text, text


@_vector("change/insert-at-span-end-goes-to-U")
def _v(gate):
    base, head, (code, text) = _boundary(gate, len("Alpha must hold."))
    _s, end = base.span(X)
    return (code == 1 and not _change_lines(text, X)
            and "unregistered rule text at {} head [{},{})".format(ALPHA, end, end + 1) in text), text


@_vector("change/replace-last-byte-before-span-goes-to-U")
def _v(gate):
    base, head, (code, text) = _boundary(gate, 0, insert=False, low=-1, high=0, text="_", clause=Y)
    start, _e = base.span(Y)
    return (code == 1 and not _change_lines(text, Y)
            and "removed at {} base [{},{})".format(ALPHA, start - 1, start) in text), text


@_vector("change/replace-first-span-byte-marks-clause")
def _v(gate):
    base, head, (code, text) = _boundary(gate, 0, insert=False, low=0, high=1, text="C", clause=Y,
                                         new_text="Ceta must hold.")
    lines = _change_lines(text, Y)
    return code == 1 and len(lines) == 1 and "touched at base" in lines[0] and "unregistered" not in text, text


@_vector("change/replace-last-span-byte-marks-clause")
def _v(gate):
    size = len("Beta must hold.")
    base, head, (code, text) = _boundary(gate, 0, insert=False, low=size - 1, high=size, text="!", clause=Y,
                                         new_text="Beta must hold!")
    lines = _change_lines(text, Y)
    return code == 1 and len(lines) == 1 and "touched at base" in lines[0] and "unregistered" not in text, text


@_vector("change/replace-first-byte-after-span-goes-to-U")
def _v(gate):
    size = len("Alpha must hold.")
    base, head, (code, text) = _boundary(gate, 0, insert=False, low=size, high=size + 1, text="_", clause=X)
    _s, end = base.span(X)
    return (code == 1 and not _change_lines(text, X)
            and "removed at {} base [{},{})".format(ALPHA, end, end + 1) in text), text


OMEGA = "\n\nOmega may vary.\n"


def _omega():
    fx = _alpha()
    fx.rules[ALPHA] = ALPHA_TEXT + OMEGA[1:]
    return fx


@_vector("change/unregistered-prose-removal-fails")
def _v(gate):
    base = _omega()
    head = _alpha()
    return _expect(gate, head, base, code=1, needles=["unregistered rule text removed at {} base".format(ALPHA)])


@_vector("change/new-rule-file-prose-must-register")
def _v(gate):
    base = _alpha()
    head = base.copy()
    head.rules[RULES_PREFIX + "fixbeta.md"] = "---\ncorpus-id: fixbeta\n---\n\nBeta may vary.\n"
    size = len(head.rules[RULES_PREFIX + "fixbeta.md"])
    return _expect(gate, head, base, code=1, needles=["unregistered rule text at {}fixbeta.md head [0,{})".format(
        RULES_PREFIX, size)])


@_vector("change/line-shift-only-not-a-change")
def _v(gate):
    base = _alpha()
    base.budget["non-clause-text-per-change"] = 1
    head = base.copy().edit(ALPHA, "# Alpha rule\n", "# Alpha rule\n\n")
    head.waivers = _tile_all(base, head, ALPHA)
    return _expect(gate, head, base, code=0, needles=["non-clause-text 1"], absent=["CHANGE:", "ANCHOR MOVED"])


@_vector("change/removed-clause-in-change-set")
def _v(gate):
    base = _alpha()
    head = base.copy().edit(ALPHA, "\n\nGamma must hold.", "")
    del head.clauses[Z]
    ok, detail = _expect(gate, head, base, code=1, needles=["no base encoding for fixalpha.3"])
    lines = _change_lines(detail[1], Z)
    return ok and len(lines) == 1 and "removed" in lines[0], detail


@_vector("change/registration-only-goes-to-N")
def _v(gate):
    base = _omega()
    head = base.copy()
    head.clauses[W] = [ALPHA, "Omega may vary.", None, None]
    return _expect(gate, head, base, code=0, needles=["REGISTERED: fixalpha.4"], absent=["CHANGE:"])


ONE_OP_BASE = "Alpha must hold. Omega may vary."
ONE_OP_HEAD = "Alpha must 1234567 vary."


def _one_op():
    fx = _alpha()
    fx.rules[ALPHA] = ALPHA_TEXT.replace("Alpha must hold. Beta must hold.", ONE_OP_BASE)
    del fx.clauses[Y]
    fx.rows = [fx.rows[0]]
    fx.cases = {"k-x": fx.cases["k-x"]}
    return fx


def _one_opcode(base, head):
    ops = difflib.SequenceMatcher(None, base.rules[ALPHA].encode(), head.rules[ALPHA].encode(),
                                  autojunk=False).get_opcodes()
    return [op for op in ops if op[0] != "equal"]


@_vector("change/one-replace-opcode-spans-clause-and-unregistered-prose-fails")
def _v(gate):
    base = _one_op()
    head = base.copy().edit(ALPHA, ONE_OP_BASE, ONE_OP_HEAD)
    head.clauses[X][1] = ONE_OP_HEAD
    ops = _one_opcode(base, head)
    start = base.rules[ALPHA].encode().index(b" Omega")
    ok, detail = _expect(gate, head, base, code=1, needles=[
        "unregistered rule text removed at {} base [{},{})".format(ALPHA, start, start + len(" Omega may"))])
    return ok and len(ops) == 1 and ops[0][0] == "replace", (ops, detail)


@_vector("change/one-replace-opcode-prose-registered-binds")
def _v(gate):
    base = _one_op()
    base.clauses[W] = [ALPHA, "Omega may vary.", None, None]
    base.rows = [_req(X, ["claim:completion"], ["set:enumerated"]), _req(W, ["claim:completion"], ["count:reconciled"])]
    base.cases = {"k-x": _case("k-x", [X, W], {"claim": "completion"}, "REJECT count:reconciled set:enumerated")}
    base.budget["non-clause-text-per-change"] = 1
    head = base.copy().edit(ALPHA, ONE_OP_BASE, ONE_OP_HEAD)
    head.clauses[X][1] = ONE_OP_HEAD
    del head.clauses[W]
    head.rows = [_req(X, ["claim:partial"], ["set:enumerated"])]
    head.cases = {"k-x": _case("k-x", [X], {"claim": "partial"}, "REJECT set:enumerated", base="ACCEPT"),
                  "k-w": _case("k-w", [], {"claim": "completion", "evidence": ["id-check"]}, "ACCEPT",
                               base="REJECT count:reconciled set:enumerated", removes=[W])}
    head.waivers = _tile_all(base, head, ALPHA)
    ok, detail = _expect(gate, head, base, code=0, needles=["BOUND: fixalpha.1 by case k-x",
                                                            "BOUND: fixalpha.4 by case k-w", "non-clause-text 1"])
    return ok and len(_one_opcode(base, head)) == 1, detail


def _two_clause_replace():
    base = _alpha()
    head = base.copy().edit(ALPHA, "hold. Beta", "12345678")
    head.clauses[X][1] = "Alpha must 1234"
    head.clauses[Y][1] = "5678 must hold."
    return base, head


@_vector("change/range-across-two-clauses-gap-residue")
def _v(gate):
    base, head = _two_clause_replace()
    _s, end = base.span(X)
    ok, detail = _expect(gate, head, base, code=1, needles=[
        "unregistered rule text removed at {} base [{},{})".format(ALPHA, end, end + 1)])
    return ok and detail[1].count("unregistered rule text") == 1, detail


@_vector("change/residue-whitespace-tile-passes")
def _v(gate):
    base, head = _two_clause_replace()
    base.budget.update({"non-clause-text-per-change": 1, "wording-only-per-change": 2})
    head.budget = dict(base.budget)
    head.waivers = _tile_all(base, head, ALPHA) + [_cw("w-x", "wording-only", X, base, head),
                                                   _cw("w-y", "wording-only", Y, base, head)]
    return _expect(gate, head, base, code=0, needles=["non-clause-text 1", "wording-only 2"])


def _residue_run(gate, clause, low, high, text, new_text):
    base, head, (code, out) = _boundary(gate, 0, insert=False, low=low, high=high, text=text, clause=clause,
                                        new_text=new_text)
    return base, code, out


@_vector("change/residue-range-starts-one-before-span-reports-one-byte")
def _v(gate):
    base, code, out = _residue_run(gate, Y, -1, 2, "12", "12ta must hold.")
    start, _e = base.span(Y)
    return code == 1 and out.count("unregistered rule text") == 1 and \
        "removed at {} base [{},{})".format(ALPHA, start - 1, start) in out, out


@_vector("change/residue-range-starts-at-span-start-reports-none")
def _v(gate):
    _base, code, out = _residue_run(gate, Y, 0, 2, "12", "12ta must hold.")
    return code == 1 and "unregistered rule text" not in out and _change_lines(out, Y), out


@_vector("change/residue-range-ends-at-span-end-reports-none")
def _v(gate):
    size = len("Alpha must hold.")
    _base, code, out = _residue_run(gate, X, size - 2, size, "12", "Alpha must hol12")
    return code == 1 and "unregistered rule text" not in out and _change_lines(out, X), out


@_vector("change/residue-range-ends-one-past-span-end-reports-one-byte")
def _v(gate):
    size = len("Alpha must hold.")
    base, code, out = _residue_run(gate, X, size - 2, size + 1, "12", "Alpha must hol12")
    _s, end = base.span(X)
    return code == 1 and out.count("unregistered rule text") == 1 and \
        "removed at {} base [{},{})".format(ALPHA, end, end + 1) in out, out


DUP = RULES_PREFIX + "fixdupl.md"
DUP_TEXT = "---\ncorpus-id: fixdupl\n---\n\nKeep the log.\n\n# Notes\n\nKeep the log.\n"
XD = "fixdupl.1"


def _dup(budget=None):
    fx = _Fx()
    fx.rules[DUP] = DUP_TEXT
    fx.clauses = {XD: [DUP, "Keep the log.", 5, 5]}
    fx.rows = [_req(XD, ["claim:completion"], ["count:reconciled"])]
    fx.cases = {"k-d": _case("k-d", [XD], {"claim": "completion"}, "REJECT count:reconciled")}
    fx.budget.update(budget or {})
    return fx


def _dup_moved(budget=None):
    base = _dup(budget)
    head = base.copy()
    head.clauses[XD] = [DUP, "Keep the log.", 9, 9]
    return base, head


def _clause_gate(fx, prev=None):
    """check_clauses.run over a fixture written to a private tempdir: (exit code, output)."""
    import check_clauses
    with tempfile.TemporaryDirectory(prefix="rule-behaviour-clauses-") as directory:
        root = Path(directory)
        tree = fx.build()
        for rel, data in tree.files.items():
            if rel.startswith(RULES_PREFIX) or rel == INVENTORY_REL:
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_bytes(data)
        born = "".join('[[born]]\nid = "{}"\nborn-release = "1.0.0"\n\n'.format(i) for i in fx.born)
        gone = "".join('[[tombstone]]\nid = "{}"\nretired-release = "1.1.0"\n\n'.format(i) for i in fx.gone)
        (root / ".aiqt/core/id-history.toml").write_text(born + gone, encoding="utf-8")
        kwargs = {}
        if prev is not None:
            (root / "prev.toml").write_bytes(prev.build().files[INVENTORY_REL])
            kwargs["prev_inventory_path"] = root / "prev.toml"
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = check_clauses.run(root, root / RULES_DIR, root / INVENTORY_REL,
                                     root / ".aiqt/core/id-history.toml", **kwargs)
        return code, out.getvalue() + err.getvalue()


# Two clauses that swap places, so every sentence lies inside a clause row on both sides (the clause gate's
# text-coverage leg) while each clause's own register row moves.
SWAP = RULES_PREFIX + "fixswap.md"
SWAP_TEXT = "---\ncorpus-id: fixswap\n---\n\nKeep the log.\n\nKeep the log.\n"
XS, XT = "fixswap.1", "fixswap.2"
PATH_A, PATH_B = RULES_PREFIX + "fixpatha.md", RULES_PREFIX + "fixpathb.md"
XA, XB = "fixpatha.1", "fixpathb.1"


def _encode_two(fx, first, second):
    fx.rows = [_req(first, ["claim:completion"], ["count:reconciled"]),
               _req(second, ["claim:partial"], ["set:enumerated"])]
    fx.cases = {"k-1": _case("k-1", [first], {"claim": "completion"}, "REJECT count:reconciled"),
                "k-2": _case("k-2", [second], {"claim": "partial"}, "REJECT set:enumerated")}


def _swap_windows(budget=None):
    """Two clauses on the two occurrences of one text in an unchanged file; HEAD swaps their windows."""
    base = _Fx()
    base.rules[SWAP] = SWAP_TEXT
    base.clauses = {XS: [SWAP, "Keep the log.", 5, 5], XT: [SWAP, "Keep the log.", 7, 7]}
    _encode_two(base, XS, XT)
    base.budget.update(budget or {})
    base.born, base.gone = ["fixswap", XS, XT], []
    head = base.copy()
    head.clauses[XS], head.clauses[XT] = [SWAP, "Keep the log.", 7, 7], [SWAP, "Keep the log.", 5, 5]
    return base, head


def _swap_paths():
    """Two unchanged files holding one text at the same lines, one clause on each; HEAD swaps their paths."""
    base = _Fx()
    for rel, cid in ((PATH_A, XA), (PATH_B, XB)):
        base.rules[rel] = "---\ncorpus-id: {}\n---\n\nKeep the log.\n".format(cid.split(".")[0])
    base.clauses = {XA: [PATH_A, "Keep the log.", 5, 5], XB: [PATH_B, "Keep the log.", 5, 5]}
    _encode_two(base, XA, XB)
    base.born, base.gone = ["fixpatha", XA, "fixpathb", XB], []
    head = base.copy()
    head.clauses[XA], head.clauses[XB] = [PATH_B, "Keep the log.", 5, 5], [PATH_A, "Keep the log.", 5, 5]
    return base, head


@_vector("change/register-only-window-to-other-occurrence-marks-clause")
def _v(gate):
    base, head = _swap_windows()
    pre = (_clause_gate(base), _clause_gate(head))
    refused = _expect(gate, head, base, code=1, needles=[
        "ANCHOR MOVED: {}: {} 5-5 -> {} 7-7".format(XS, SWAP, SWAP),
        "ANCHOR MOVED: {}: {} 7-7 -> {} 5-5".format(XT, SWAP, SWAP)])
    base2, head2 = _swap_windows({"anchor-only-per-change": 2})
    head2.waivers = [_cw("w-s", "anchor-only", XS, base2, head2), _cw("w-t", "anchor-only", XT, base2, head2)]
    waived = _expect(gate, head2, base2, code=0, needles=["WAIVED: {} by anchor-only w-s".format(XS),
                                                          "WAIVED: {} by anchor-only w-t".format(XT)])
    return pre[0][0] == 0 and pre[1][0] == 0 and refused[0] and waived[0], (pre, refused, waived)


@_vector("change/register-only-source-path-change-marks-clause")
def _v(gate):
    base, head = _swap_paths()
    pre = (_clause_gate(base), _clause_gate(head))
    ok, detail = _expect(gate, head, base, code=1, needles=[
        "ANCHOR MOVED: {}: {} 5-5 -> {} 5-5".format(XA, PATH_A, PATH_B),
        "ANCHOR MOVED: {}: {} 5-5 -> {} 5-5".format(XB, PATH_B, PATH_A)])
    # the HEAD register is one the clause gate refuses for its paths alone: every sentence is still covered
    return ok and pre[0][0] == 0 and pre[1][0] == 1 and "is not the scanned rule source" in pre[1][1] and \
        "lies inside no clause row" not in pre[1][1], (pre, detail)


@_vector("change/rule-file-rename-marks-every-clause")
def _v(gate):
    base = _alpha()
    head = base.copy()
    renamed = RULES_PREFIX + "fixalpha2.md"
    head.rules = {renamed: ALPHA_TEXT}
    for cid in (X, Y, Z):
        head.clauses[cid][0] = renamed
    code, text = _run(gate, head, base)
    marked = all(len(_change_lines(text, cid)) == 1 and "anchor moved" in _change_lines(text, cid)[0]
                 and "touched at base" in _change_lines(text, cid)[0] for cid in (X, Y, Z))
    return code == 1 and marked and "unregistered rule text at {} head [0,".format(renamed) in text and \
        "unregistered rule text removed at {} base [0,".format(ALPHA) in text, text


@_vector("change/anchor-insert-at-span-start-maps-not-moved")
def _v(gate):
    _base, _head, (code, text) = _boundary(gate, 0)
    return code == 1 and "ANCHOR MOVED" not in text and not _change_lines(text, X), text


@_vector("change/anchor-insert-one-after-span-start-touches")
def _v(gate):
    _base, _head, (code, text) = _boundary(gate, 1, new_text="AQlpha must hold.")
    lines = _change_lines(text, X)
    return code == 1 and lines and "touched" in lines[0] and "anchor moved" not in lines[0], text


@_vector("change/anchor-insert-at-span-end-maps-not-moved")
def _v(gate):
    _base, _head, (code, text) = _boundary(gate, len("Alpha must hold."))
    return code == 1 and "ANCHOR MOVED" not in text and not _change_lines(text, X), text


@_vector("change/anchor-insert-one-before-span-end-touches")
def _v(gate):
    _base, _head, (code, text) = _boundary(gate, len("Alpha must hold"), new_text="Alpha must holdQ.")
    lines = _change_lines(text, X)
    return code == 1 and lines and "touched" in lines[0] and "anchor moved" not in lines[0], text


@_vector("change/source-digest-only-change-not-anchor-move")
def _v(gate):
    base = _alpha()
    head = base.copy().edit(ALPHA, "Gamma must hold.", "Gamma must always hold.")
    head.clauses[Z][1] = "Gamma must always hold."
    code, text = _run(gate, head, base)
    return code == 1 and not _change_lines(text, X) and "ANCHOR MOVED" not in text and \
        base.file_sha(ALPHA) != head.file_sha(ALPHA), text


@_vector("change/anchor-move-with-text-change-in-C-once")
def _v(gate):
    base = _alpha()
    base.budget.update({"wording-only-per-change": 1, "non-clause-text-per-change": 1})
    head = base.copy().edit(ALPHA, "\n\nGamma must hold.", "\n\n\nGamma must surely hold.")
    head.clauses[Z][1] = "Gamma must surely hold."
    head.rows.append(_req(Z, ["fix:wording"], ["count:reconciled"]))
    base.rows.append(_req(Z, ["fix:wording"], ["count:reconciled"]))
    for fx in (base, head):
        fx.cases["k-z"] = _case("k-z", [Z], WF, "REJECT count:reconciled")
    head.budget = dict(base.budget)
    good = head.copy()
    good.waivers = _tile_all(base, head, ALPHA) + [_cw("w-z", "wording-only", Z, base, head)]
    bad = head.copy()
    bad.waivers = _tile_all(base, head, ALPHA) + [_cw("w-z", "wording-only", Z, base, head,
                                                      **{"head-anchor-sha256": base.anchor(Z)})]
    passed = _expect(gate, good, base, code=0, needles=["WAIVED: fixalpha.3 by wording-only w-z"],
                     absent=["ANCHOR MOVED"])
    refused = _expect(gate, bad, base, code=1, needles=["digest mismatch for fixalpha.3 (head-anchor-sha256)"])
    once = len(_change_lines(passed[1][1], Z)) == 1 and base.anchor(Z) != head.anchor(Z)
    return passed[0] and refused[0] and once, (passed, refused)


# ----------------------------------------------------------------------------------------- binding

def _text_edit(fx, cid, old, new):
    fx.edit(fx.clauses[cid][0], old, new)
    fx.clauses[cid][1] = fx.clauses[cid][1].replace(old, new)
    return fx


def _flip_pair():
    """X's text and encoding change so that a completion claim with a count check flips to REJECT. (A case
    whose HEAD outcome is ACCEPT through a met requirement cannot pass ablation, so the flip runs this way.)"""
    base = _alpha()
    head = _text_edit(base.copy(), X, "Alpha must", "Alpha must always")
    head.rows[0] = _req(X, ["claim:completion"], ["set:enumerated", "count:reconciled"])
    head.cases["k-x"]["expect"]["head"] = "REJECT count:reconciled set:enumerated"
    return base, head


FLIP = {"claim": "completion", "evidence": ["count-check"]}
WF = {"op": "wording-fix", "probe": PROBE}


@_vector("binding/changed-clause-no-case-fails")
def _v(gate):
    base = _alpha()
    head = _text_edit(base.copy(), X, "Alpha must", "Alpha must always")
    return _expect(gate, head, base, code=1, needles=["changed clause fixalpha.1 is not bound"])


@_vector("binding/unchanged-case-does-not-count")
def _v(gate):
    base, head = _flip_pair()
    for fx in (base, head):
        fx.cases["k-f"] = _case("k-f", [X], FLIP, "REJECT set:enumerated", base="ACCEPT")
    return _expect(gate, head, base, code=1, needles=["changed clause fixalpha.1 is not bound"])


@_vector("binding/nondiscriminating-case-fails")
def _v(gate):
    base, head = _flip_pair()
    head.cases["k-f"] = _case("k-f", [X], {"claim": "completion", "evidence": ["index-enumeration"]}, "ACCEPT",
                              base="ACCEPT")
    return _expect(gate, head, base, code=1, needles=[
        "case k-f does not flip the decision for fixalpha.1; its base and HEAD outcomes are both 'ACCEPT'"],
        absent=["unmet atoms differ only"])


@_vector("binding/discriminating-case-passes")
def _v(gate):
    base, head = _flip_pair()
    head.cases["k-f"] = _case("k-f", [X], FLIP, "REJECT set:enumerated", base="ACCEPT")
    return _expect(gate, head, base, code=0, needles=["BOUND: fixalpha.1 by case k-f", "bound 1;"])


def _decoy(list_y):
    base = _alpha()
    base.rows[1] = _req(Y, ["claim:partial"], ["set:enumerated"])
    head = base.copy()
    _text_edit(head, X, "Alpha must", "Alpha must always")
    _text_edit(head, Y, "Beta must", "Beta must always")
    head.rows[1] = _req(Y, ["claim:completion"], ["set:enumerated"])
    head.cases["k-x"]["expect"]["head"] = "REJECT count:reconciled set:enumerated"
    head.cases["k-y"] = _case("k-y", [Y], {"claim": "completion", "evidence": ["count-check"]},
                              "REJECT set:enumerated")
    head.cases["k-f"] = _case("k-f", [Y] if list_y else [X], FLIP, "REJECT set:enumerated", base="ACCEPT")
    return base, head


@_vector("binding/crossed-clause-decoy-fails")
def _v(gate):
    base, head = _decoy(False)
    return _expect(gate, head, base, code=1, needles=[
        "case k-f does not flip the decision for fixalpha.1; the decision flips, but restoring the base rows of "
        "fixalpha.1 leaves 'REJECT set:enumerated', so the flip is not on fixalpha.1",
        "changed clause fixalpha.2 is not bound"], absent=["unmet atoms differ only"])


@_vector("binding/crossed-clause-decoy-separate-case-passes")
def _v(gate):
    base, head = _decoy(True)
    base.budget["wording-only-per-change"] = 1
    head.budget = dict(base.budget)
    head.waivers = [_cw("w-x", "wording-only", X, base, head)]
    return _expect(gate, head, base, code=0, needles=["BOUND: fixalpha.2 by case k-f",
                                                      "WAIVED: fixalpha.1 by wording-only w-x"])


@_vector("binding/listed-wrong-clause-fails")
def _v(gate):
    base = _alpha()
    base.rows[1] = _req(Y, ["claim:partial"], ["set:enumerated"])
    head = base.copy()
    _text_edit(head, Y, "Beta must", "Beta must always")
    head.rows[1] = _req(Y, ["claim:completion"], ["set:enumerated"])
    head.cases["k-x"]["expect"]["head"] = "REJECT count:reconciled set:enumerated"
    head.cases["k-y"] = _case("k-y", [Y], {"claim": "completion", "evidence": ["count-check"]},
                              "REJECT set:enumerated")
    head.cases["k-f"] = _case("k-f", [X], FLIP, "REJECT set:enumerated", base="ACCEPT")
    return _expect(gate, head, base, code=1, needles=["changed clause fixalpha.2 is not bound"],
                   absent=["CHANGE: fixalpha.1"])


@_vector("binding/joint-cause-binds-both")
def _v(gate):
    def exempt(cid, when, atom):
        return {"clause-id": cid, "kind": "exempt", "when": [when], "suppresses": [atom]}
    base = _alpha()
    base.rows = [_req(Z, ["claim:completion"], ["set:enumerated", "count:reconciled"]),
                 exempt(X, "claim:partial", "set:enumerated"), exempt(Y, "claim:partial", "count:reconciled")]
    base.cases = {"k-z": _case("k-z", [Z], {"claim": "completion"}, "REJECT count:reconciled set:enumerated")}
    head = base.copy()
    _text_edit(head, X, "Alpha must", "Alpha must always")
    _text_edit(head, Y, "Beta must", "Beta must always")
    head.rows[1:] = [exempt(X, "claim:bounded", "set:enumerated"), exempt(Y, "claim:bounded", "count:reconciled")]
    head.cases["k-j"] = _case("k-j", [X, Y], BOUNDED, "ACCEPT", base="REJECT count:reconciled set:enumerated")
    return _expect(gate, head, base, code=0, needles=["BOUND: fixalpha.1 by case k-j", "BOUND: fixalpha.2 by case k-j"])


@_vector("binding/unencoded-at-base-edit-not-bindable")
def _v(gate):
    base = _alpha()
    head = _text_edit(base.copy(), Z, "Gamma must", "Gamma must always")
    head.rows.append(_req(Z, ["fix:wording"], ["count:reconciled"]))
    head.cases["k-zf"] = _case("k-zf", [Z], WF, "REJECT count:reconciled", base="ACCEPT")
    return _expect(gate, head, base, code=1, needles=["no base encoding for fixalpha.3"])


@_vector("binding/encoding-first-then-edit-binds")
def _v(gate):
    base = _alpha()
    first = base.copy()
    first.rows.append(_req(Z, ["fix:wording"], ["count:reconciled"]))
    first.cases["k-z"] = _case("k-z", [Z], WF, "REJECT count:reconciled")
    step1 = _expect(gate, first, base, code=0, needles=["ENCODING CHANGED: fixalpha.3"], absent=["CHANGE:"])
    second = _text_edit(first.copy(), Z, "Gamma must", "Gamma must always")
    second.rows[2] = _req(Z, ["fix:wording"], ["set:enumerated"])
    second.cases["k-z"]["expect"]["head"] = "REJECT set:enumerated"
    second.cases["k-zf"] = _case("k-zf", [Z], dict(WF, evidence=["count-check"]), "REJECT set:enumerated",
                                 base="ACCEPT")
    step2 = _expect(gate, second, first, code=0, needles=["BOUND: fixalpha.3 by case k-zf"])
    return step1[0] and step2[0], (step1, step2)


@_vector("binding/new-clause-over-existing-prose-not-bindable")
def _v(gate):
    base = _omega()
    head = base.copy().edit(ALPHA, "Omega may vary.", "Omega may shift.")
    head.clauses[W] = [ALPHA, "Omega may shift.", None, None]
    head.rows.append(_req(W, ["fix:wording"], ["set:enumerated"]))
    head.cases["k-w"] = _case("k-w", [W], WF, "REJECT set:enumerated", base="ACCEPT")
    return _expect(gate, head, base, code=1, needles=["no base encoding for fixalpha.4"])


def _new_clause(base, old, new, text):
    base.budget["non-clause-text-per-change"] = 2
    head = base.copy().edit(ALPHA, old, new)
    head.clauses[W] = [ALPHA, text, None, None]
    head.rows.append(_req(W, ["fix:wording"], ["set:enumerated"]))
    head.cases["k-w"] = _case("k-w", [W], WF, "REJECT set:enumerated", base="ACCEPT")
    head.waivers = _tile_all(base, head, ALPHA)
    return head


@_vector("binding/wholly-new-clause-binds-against-absent")
def _v(gate):
    base = _alpha()
    head = _new_clause(base, "Gamma must hold.\n", "Gamma must hold.\n\nDelta must hold.\n", "Delta must hold.")
    return _expect(gate, head, base, code=0, needles=["BOUND: fixalpha.4 by case k-w"])


def _omega_base(prose):
    fx = _alpha()
    fx.rules[ALPHA] = ALPHA_TEXT + "\n" + prose + "\n"
    return fx


@_vector("binding/new-span-first-byte-unchanged-not-bindable")
def _v(gate):
    base = _omega_base("Omega.")
    head = _new_clause(base, "Omega.", "Omega must hold.", "Omega must hold.")
    return _expect(gate, head, base, code=1, needles=["no base encoding for fixalpha.4"])


@_vector("binding/new-span-starts-at-changed-range-start-binds")
def _v(gate):
    base = _omega_base("Omega.")
    head = _new_clause(base, "\nOmega.", "\nTheta must hold. Omega.", "Theta must hold.")
    return _expect(gate, head, base, code=0, needles=["BOUND: fixalpha.4 by case k-w"])


@_vector("binding/new-span-ends-at-changed-range-end-binds")
def _v(gate):
    base = _omega_base("Omega.")
    head = _new_clause(base, "Omega.", "Omega. Theta must hold.", "Theta must hold.")
    return _expect(gate, head, base, code=0, needles=["BOUND: fixalpha.4 by case k-w"])


@_vector("binding/new-span-last-byte-unchanged-not-bindable")
def _v(gate):
    base = _omega_base("hold.")
    head = _new_clause(base, "\nhold.", "\nTheta must hold.", "Theta must hold.")
    return _expect(gate, head, base, code=1, needles=["no base encoding for fixalpha.4"])


def _z_removal(with_case):
    base = _alpha()
    base.rows.append(_req(Z, ["fix:wording"], ["set:enumerated"]))
    base.cases["k-z"] = _case("k-z", [Z], WF, "REJECT set:enumerated")
    base.budget["non-clause-text-per-change"] = 2
    head = base.copy().edit(ALPHA, "\n\nGamma must hold.", "")
    del head.clauses[Z]
    head.rows.pop()
    del head.cases["k-z"]
    if with_case:
        head.cases["k-rz"] = _case("k-rz", [], WF, "ACCEPT", base="REJECT set:enumerated", removes=[Z])
    head.waivers = _tile_all(base, head, ALPHA)
    return base, head


@_vector("binding/removed-clause-flip-case-passes")
def _v(gate):
    base, head = _z_removal(True)
    return _expect(gate, head, base, code=0, needles=["BOUND: fixalpha.3 by case k-rz"])


@_vector("binding/removed-clause-without-case-fails")
def _v(gate):
    base, head = _z_removal(False)
    return _expect(gate, head, base, code=1, needles=["changed clause fixalpha.3 is not bound"])


@_vector("binding/registration-only-needs-baseline-case")
def _v(gate):
    base = _omega()
    head = base.copy()
    head.clauses[W] = [ALPHA, "Omega may vary.", None, None]
    head.rows.append(_req(W, ["fix:wording"], ["set:enumerated"]))
    return _expect(gate, head, base, code=1, needles=["registered clause fixalpha.4 has obligation rows but no case"])


def _genesis_base():
    base = _alpha()
    base.script = base.table = False
    base.cases, base.budget = {}, None
    return base


@_vector("binding/genesis-empty-change-passes")
def _v(gate):
    return _expect(gate, _alpha(), _genesis_base(), code=0, needles=["(genesis): bound 0"])


@_vector("binding/genesis-with-rule-edit-fails")
def _v(gate):
    head = _text_edit(_alpha(), Z, "Gamma must", "Gamma must always")
    return _expect(gate, head, _genesis_base(), code=1, needles=["rule edit in the harness genesis change"])


@_vector("binding/base-gate-without-table-exit-2")
def _v(gate):
    base = _genesis_base()
    base.script = True
    return _expect(gate, _alpha(), base, code=2, needles=["but no .aiqt/core/behaviour/obligations.toml"])


def _atom_pair(head_atoms):
    base = _alpha()
    base.rows[0] = _req(X, ["claim:completion"], ["set:enumerated", "count:reconciled"])
    base.cases["k-x"]["expect"]["head"] = "REJECT count:reconciled set:enumerated"
    head = _text_edit(base.copy(), X, "Alpha must", "Alpha must always")
    head.rows[0] = _req(X, ["claim:completion"], head_atoms)
    return base, head


@_vector("binding/reject-to-reject-atom-change-does-not-bind")
def _v(gate):
    base, head = _atom_pair(["set:enumerated"])
    head.cases["k-x"]["expect"]["head"] = "REJECT set:enumerated"
    head.cases["k-a"] = _case("k-a", [X], {"claim": "completion"}, "REJECT set:enumerated",
                              base="REJECT count:reconciled set:enumerated")
    try:
        rows_b, rows_h = _rows(gate, base.rows), _rows(gate, head.rows)
        scenario = _scenario(gate, {"claim": "completion"})
        full_outcome_rule_binds = (gate.outcome(rows_b, scenario) != gate.outcome(rows_h, scenario)
                                   and gate.outcome(gate.reverted(rows_h, rows_b, X), scenario)
                                   != gate.outcome(rows_h, scenario))
    except Exception as exc:
        return False, exc
    ok, detail = _expect(gate, head, base, code=1, needles=[
        "case k-a does not flip the decision for fixalpha.1; unmet atoms differ only"])
    return ok and full_outcome_rule_binds, detail


@_vector("binding/decision-flip-binds")
def _v(gate):
    base, head = _flip_pair()
    head.cases["k-a"] = _case("k-a", [X], FLIP, "REJECT set:enumerated", base="ACCEPT")
    return _expect(gate, head, base, code=0, needles=["BOUND: fixalpha.1 by case k-a"])


@_vector("binding/accept-to-accept-does-not-bind")
def _v(gate):
    base, head = _flip_pair()
    head.cases["k-a"] = _case("k-a", [X], {"claim": "completion", "evidence": ["index-enumeration"]}, "ACCEPT",
                              base="ACCEPT")
    return _expect(gate, head, base, code=1, needles=["case k-a does not flip the decision for fixalpha.1"])


RETIRE_ONE = {"op": "retire", "claim": "completion", "before": [{"id": "R", "items": ["a", "b"]}],
              "after": [{"id": "R", "items": ["a"]}], "evidence": ["count-check"]}


def _revert_pair():
    base = _alpha()
    base.rows = [_req(X, ["op:record-change"], ["count:reconciled"]), _req(Y, ["claim:partial"], ["set:enumerated"])]
    base.cases = {"k-y": _case("k-y", [Y], {"claim": "partial"}, "REJECT set:enumerated")}
    head = base.copy()
    _text_edit(head, X, "Alpha must", "Alpha must always")
    _text_edit(head, Y, "Beta must", "Beta must always")
    head.rows = [_req(X, ["op:record-change"], ["item:accounted"]), _req(Y, ["claim:completion"], ["set:enumerated"])]
    head.cases = {"k-y": _case("k-y", [Y], {"claim": "completion"}, "REJECT set:enumerated")}
    return base, head


@_vector("binding/revert-flips-atoms-only-fails")
def _v(gate):
    base, head = _revert_pair()
    head.cases["k-r"] = _case("k-r", [X, Y], RETIRE_ONE, "REJECT item:accounted:b set:enumerated", base="ACCEPT")
    try:
        rows_b, rows_h = _rows(gate, base.rows), _rows(gate, head.rows)
        scenario = _scenario(gate, RETIRE_ONE)
        reverts = [gate.outcome(gate.reverted(rows_h, rows_b, cid), scenario) for cid in (X, Y)]
    except Exception as exc:
        return False, exc
    ok, detail = _expect(gate, head, base, code=1, needles=[
        "case k-r does not flip the decision for fixalpha.1", "case k-r does not flip the decision for fixalpha.2"])
    return ok and reverts == ["REJECT set:enumerated", "REJECT item:accounted:b"], (reverts, detail)


@_vector("binding/revert-flips-atoms-only-separate-cases-pass")
def _v(gate):
    base, head = _revert_pair()
    head.cases["k-rx"] = _case("k-rx", [X], dict(RETIRE_ONE, claim="none"), "REJECT item:accounted:b",
                               base="ACCEPT")
    head.cases["k-ry"] = _case("k-ry", [Y], {"claim": "completion", "evidence": ["count-check"]},
                               "REJECT set:enumerated", base="ACCEPT")
    return _expect(gate, head, base, code=0, needles=["BOUND: fixalpha.1 by case k-rx", "BOUND: fixalpha.2 by case k-ry"])


@_vector("binding/bound-report-prints-atom-diagnostics")
def _v(gate):
    base, head = _flip_pair()
    head.cases["k-a"] = _case("k-a", [X], FLIP, "REJECT set:enumerated", base="ACCEPT")
    return _expect(gate, head, base, code=0, needles=[
        "BOUND: fixalpha.1 by case k-a (unmet base: ACCEPT; head: REJECT set:enumerated; with fixalpha.1 "
        "reverted: ACCEPT)"])


KEEP = RULES_PREFIX + "fixkeep.md"
RMV = RULES_PREFIX + "fixrmv.md"
KEEP_TEXT = "---\n" + "corpus-id: fixkeep" + "\n---\n\nKeep the log.\n"
RMV_TEXT = "---\n" + "corpus-id: fixrmv" + "\n---\n\nAlpha must hold.\n"


def _removal(evidence=(), ceiling=3, tiles="exact"):
    """The sole-clause removal: fixrmv.md is deleted with its only clause, both ids tombstoned."""
    base = _Fx()
    base.rules = {KEEP: KEEP_TEXT, RMV: RMV_TEXT}
    base.clauses = {"fixkeep.1": [KEEP, "Keep the log.", 5, 5], "fixrmv.1": [RMV, "Alpha must hold.", 5, 5]}
    base.rows = [_req("fixrmv.1", ["claim:completion"], ["set:enumerated"]),
                 _req("fixkeep.1", ["claim:partial"], ["count:reconciled"])]
    base.budget["non-clause-text-per-change"] = ceiling
    base.excluded = {"fixkeep.1": "reviewed: the kept clause only carries the surviving rule file"}
    base.born, base.gone = ["fixkeep", "fixkeep.1", "fixrmv", "fixrmv.1"], []
    head = base.copy()
    del head.rules[RMV]
    del head.clauses["fixrmv.1"]
    head.rows = [head.rows[1]]
    head.born, head.gone = list(base.born), ["fixrmv", "fixrmv.1"]
    scenario = {"claim": "completion", "evidence": list(evidence)}
    accepted = "index-enumeration" in evidence
    head.cases["k-rmv"] = _case("k-rmv", [], scenario, "ACCEPT", base="ACCEPT" if accepted else "REJECT set:enumerated",
                                removes=["fixrmv.1"])
    fence = len("---\ncorpus-id: fixrmv\n---\n")
    start, end = base.span("fixrmv.1")
    layout = {"exact": [(0, fence, "frontmatter"), (fence, start, "whitespace"), (end, end + 1, "whitespace")],
              "no-final-lf": [(0, fence, "frontmatter"), (fence, start, "whitespace")],
              "one-before": [(0, fence - 1, "frontmatter"), (fence - 1, start, "whitespace"),
                             (end, end + 1, "whitespace")],
              "one-past": [(0, fence + 1, "frontmatter"), (end, end + 1, "whitespace")]}[tiles]
    head.waivers = [_rw("t{}".format(n), RMV, "base", s, e, base, head, cat) for n, (s, e, cat) in enumerate(layout, 1)]
    return base, head, fence, end


@_vector("binding/remove-sole-clause-with-rule-file-binds")
def _v(gate):
    base, head, _fence, _end = _removal()
    return _expect(gate, head, base, code=0, needles=["BOUND: fixrmv.1 by case k-rmv", "waived 3 (wording-only 0, "
                                                      "unencoded 0, anchor-only 0, non-clause-text 3; repeats 0)"])


@_vector("binding/remove-sole-clause-precondition-clause-gate")
def _v(gate):
    base, head, _fence, _end = _removal()
    first = _clause_gate(head, prev=base)
    old_shape = head.copy()
    old_shape.rules[RMV] = "---\n" + "corpus-id: fixrmv" + "\n---\n"
    old_shape.gone = ["fixrmv.1"]
    second = _clause_gate(old_shape, prev=base)
    return (first[0] == 0 and "cross-release disappearance leg ran" in first[1] and second[0] == 1
            and "rule corpus-id 'fixrmv' has no clause row in the inventory" in second[1]), (first, second)


@_vector("binding/remove-sole-clause-nondiscriminating-fails")
def _v(gate):
    base, head, _fence, _end = _removal(evidence=["index-enumeration"])
    return _expect(gate, head, base, code=1, needles=["case k-rmv does not flip the decision for fixrmv.1"])


@_vector("binding/remove-sole-clause-file-residue-over-ceiling-fails")
def _v(gate):
    base, head, _fence, _end = _removal(ceiling=2)
    return _expect(gate, head, base, code=1, needles=["waiver ceiling exceeded: 3 live non-clause-text"])


@_vector("binding/remove-sole-clause-file-untiled-lf-fails")
def _v(gate):
    base, head, _fence, end = _removal(tiles="no-final-lf")
    return _expect(gate, head, base, code=1, needles=["unregistered rule text removed at {} base [{},{})".format(
        RMV, end, end + 1)])


# ------------------------------------------------------------------------------------------ waiver

def _wording(kind="wording-only", budget=1, **extra):
    base = _alpha()
    base.budget[BUDGET_KEYS[kind]] = budget
    head = _text_edit(base.copy(), X, "Alpha must", "Alpha must always")
    head.waivers = [_cw("w-1", kind, X, base, head, **extra)]
    return base, head


@_vector("waiver/reported-separately-not-bound")
def _v(gate):
    base, head = _wording()
    return _expect(gate, head, base, code=0, needles=["WAIVED: fixalpha.1 by wording-only w-1", "bound 0; waived 1"],
                   absent=["BOUND:"])


@_vector("waiver/digest-mismatch-fails")
def _v(gate):
    base, head = _wording(**{"head-sha256": "0" * 64})
    return _expect(gate, head, base, code=1, needles=["waiver w-1: digest mismatch for fixalpha.1 (head-sha256)"])


@_vector("waiver/unknown-kind-refused")
def _v(gate):
    base, head = _wording()
    head.waivers[0]["kind"] = "trusted"
    return _expect(gate, head, base, code=2, needles=["kind 'trusted' is unknown"])


@_vector("waiver/untestable-kind-refused")
def _v(gate):
    base, head = _wording()
    head.waivers[0]["kind"] = "untestable"
    return _expect(gate, head, base, code=2, needles=["kind untestable is retired"])


@_vector("waiver/retired-kind-refused")
def _v(gate):
    base, head = _wording()
    head.waivers[0]["kind"] = "whole-edit"
    return _expect(gate, head, base, code=2, needles=["kind whole-edit is retired"])


@_vector("waiver/kind-not-a-string-exit-2")
def _v(gate):
    base, head = _wording()
    head.waivers[0]["kind"] = ["wording-only"]
    return _expect(gate, head, base, code=2, needles=["waiver kind ['wording-only'] is not a string"])


@_vector("waiver/wording-only-encoding-changed-fails")
def _v(gate):
    base, head = _wording()
    head.rows[0] = _req(X, ["claim:completion", "claim:bounded"], ["count:reconciled"])
    return _expect(gate, head, base, code=1, needles=["the encoding of fixalpha.1 changed"])


@_vector("waiver/wording-only-on-unencoded-clause-refused")
def _v(gate):
    base = _alpha()
    base.budget["wording-only-per-change"] = 1
    head = _text_edit(base.copy(), Z, "Gamma must", "Gamma must always")
    head.waivers = [_cw("w-1", "wording-only", Z, base, head)]
    return _expect(gate, head, base, code=1, needles=["wording-only needs a base encoding for fixalpha.3"])


@_vector("waiver/unencoded-on-encoded-clause-refused")
def _v(gate):
    base, head = _wording("unencoded")
    return _expect(gate, head, base, code=1, needles=["fixalpha.1 has a base encoding; unencoded does not apply"])


@_vector("waiver/unencoded-on-bindable-new-clause-refused")
def _v(gate):
    base = _alpha()
    base.budget["unencoded-per-change"] = 1
    head = _new_clause(base, "Gamma must hold.\n", "Gamma must hold.\n\nDelta must hold.\n", "Delta must hold.")
    del head.cases["k-w"]
    head.waivers.append(_cw("w-u", "unencoded", W, base, head))
    return _expect(gate, head, base, code=1, needles=["fixalpha.4 is bindable; bind it"])


def _log_pair(reason="reviewed: the change keeps the obligation"):
    """Change N appends a wording-only row; change N+1 carries it."""
    start, edited = _wording()
    carried = edited.copy()
    carried.waivers[0]["reason"] = reason
    return start, edited, carried


@_vector("waiver/carried-row-stale-discharges-nothing")
def _v(gate):
    base, head = _wording()
    base.waivers = list(head.waivers)
    return _expect(gate, head, base, code=1, needles=["STALE: w-1 (carried; discharges nothing)",
                                                      "changed clause fixalpha.1 is not bound"])


@_vector("waiver/live-row-matching-nothing-fails")
def _v(gate):
    base, head = _wording()
    head.waivers = [_cw("w-1", "wording-only", Y, base, head)]
    return _expect(gate, head, base, code=1, needles=["waiver w-1 matches nothing in this change"])


def _prose_insert(category):
    base = _alpha()
    base.budget["non-clause-text-per-change"] = 1
    head = base.copy().edit(ALPHA, "Gamma must hold.\n", "Gamma must hold.\n\nOmega may vary.\n")
    side, start, end = _pieces(base, head, ALPHA)[0]
    head.waivers = [_rw("t1", ALPHA, side, start, end, base, head, category)]
    return base, head, start, end


@_vector("waiver/non-clause-text-prose-range-refused")
def _v(gate):
    base, head, start, end = _prose_insert("whitespace")
    return _expect(gate, head, base, code=1, needles=["waiver t1: {} [{},{}) is not whitespace text".format(
        ALPHA, start, end)])


def _heading(new_heading, tiles):
    base = _alpha()
    base.budget["non-clause-text-per-change"] = len(tiles)
    head = base.copy().edit(ALPHA, "# Alpha rule", new_heading)
    pieces = _pieces(base, head, ALPHA)
    return base, head, pieces


@_vector("waiver/non-clause-text-heading-range-passes")
def _v(gate):
    base, head, pieces = _heading("# Alpha rules", [0])
    head.waivers = [_rw("t{}".format(n), ALPHA, side, s, e, base, head, "heading")
                    for n, (side, s, e) in enumerate(pieces, 1)]
    return _expect(gate, head, base, code=0, needles=["(heading)"])


def _heading_insert(line, tail="\n"):
    """A new line after the last clause, tiled as the blank line before it (whitespace), then from its first
    byte to the end of the change (heading)."""
    base = _alpha()
    base.budget["non-clause-text-per-change"] = 2
    head = base.copy().edit(ALPHA, "Gamma must hold.\n", "Gamma must hold.\n\n" + line + tail)
    (side, start, end), = _pieces(base, head, ALPHA)
    assert side == "head" and head.rules[ALPHA].encode("utf-8")[start:start + 1] == b"\n", (side, start, end)
    split = start + 1
    head.waivers = [_rw("t1", ALPHA, side, start, split, base, head, "whitespace"),
                    _rw("t2", ALPHA, side, split, end, base, head, "heading")]
    return base, head, split, end


def _not_heading(gate, line, tail="\n"):
    base, head, split, end = _heading_insert(line, tail)
    return _expect(gate, head, base, code=1, needles=["waiver t2: {} [{},{}) is not heading text".format(
        ALPHA, split, end)])


# A heading tile covers one ATX heading line only: '#' then a space or the line end, at most six '#', and
# nothing past the line's own newline.
@_vector("waiver/heading-tile-hash-without-space-refused")
def _v(gate):
    return _not_heading(gate, "#Bounded claims may skip the count.")


@_vector("waiver/heading-tile-seven-hashes-refused")
def _v(gate):
    return _not_heading(gate, "####### Omega")


@_vector("waiver/heading-tile-six-hashes-passes")
def _v(gate):
    base, head, _split, _end = _heading_insert("###### Omega")
    return _expect(gate, head, base, code=0, needles=["by non-clause-text t2 (heading)"])


@_vector("waiver/heading-tile-bare-hash-line-passes")
def _v(gate):
    base, head, _split, _end = _heading_insert("#")
    return _expect(gate, head, base, code=0, needles=["by non-clause-text t2 (heading)"])


@_vector("waiver/heading-tile-ends-at-line-end-passes")
def _v(gate):
    base, head, _split, _end = _heading_insert("# Omega")
    return _expect(gate, head, base, code=0, needles=["by non-clause-text t2 (heading)"])


@_vector("waiver/heading-tile-ends-one-past-line-end-refused")
def _v(gate):
    return _not_heading(gate, "# Omega", "\n\n")


@_vector("waiver/non-clause-text-whitespace-range-passes")
def _v(gate):
    base = _alpha()
    base.budget["non-clause-text-per-change"] = 1
    head = base.copy().edit(ALPHA, "Gamma must hold.\n", "Gamma must hold.\n\n")
    head.waivers = _tile_all(base, head, ALPHA)
    return _expect(gate, head, base, code=0, needles=["(whitespace)"])


def _wide_heading():
    base = _alpha()
    base.budget["non-clause-text-per-change"] = 3
    head = base.copy().edit(ALPHA, "# Alpha rule\n", "# Alpha rule 123456\n")
    (side, start, end), = _pieces(base, head, ALPHA)
    return base, head, side, start, end


@_vector("waiver/non-clause-text-tiles-exactly")
def _v(gate):
    base, head, side, start, end = _wide_heading()
    middle = start + 3
    head.waivers = [_rw("t1", ALPHA, side, start, middle, base, head, "heading"),
                    _rw("t2", ALPHA, side, middle, end, base, head, "heading")]
    return _expect(gate, head, base, code=0, needles=["non-clause-text 2"])


@_vector("waiver/non-clause-text-gap-fails")
def _v(gate):
    base, head, side, start, end = _wide_heading()
    head.waivers = [_rw("t1", ALPHA, side, start, start + 2, base, head, "heading"),
                    _rw("t2", ALPHA, side, start + 3, end, base, head, "heading")]
    return _expect(gate, head, base, code=1, needles=["unregistered rule text at {} head [{},{})".format(
        ALPHA, start + 2, start + 3)])


@_vector("waiver/non-clause-text-overlap-fails")
def _v(gate):
    base, head, side, start, end = _wide_heading()
    head.waivers = [_rw("t1", ALPHA, side, start, start + 4, base, head, "heading"),
                    _rw("t2", ALPHA, side, start + 3, end, base, head, "heading")]
    return _expect(gate, head, base, code=1, needles=["waiver t2: the tile [{},{}) overlaps another tile".format(
        start + 3, end)])


@_vector("waiver/range-bytes-digest-mismatch-fails")
def _v(gate):
    base, head, side, start, end = _wide_heading()
    head.waivers = [_rw("t1", ALPHA, side, start, end, base, head, "heading", **{"bytes-sha256": "0" * 64})]
    return _expect(gate, head, base, code=1, needles=["waiver t1: digest mismatch for {}".format(ALPHA)])


@_vector("waiver/frontmatter-tile-ends-one-before-block-end-passes")
def _v(gate):
    base, head, fence, _end = _removal(tiles="one-before")
    return _expect(gate, head, base, code=0, needles=["{} base [0,{}) by non-clause-text t1 (frontmatter)".format(
        RMV, fence - 1)])


@_vector("waiver/frontmatter-tile-ends-at-block-end-passes")
def _v(gate):
    base, head, fence, _end = _removal()
    return _expect(gate, head, base, code=0, needles=["{} base [0,{}) by non-clause-text t1 (frontmatter)".format(
        RMV, fence)])


@_vector("waiver/frontmatter-tile-ends-one-past-block-end-refused")
def _v(gate):
    base, head, fence, _end = _removal(tiles="one-past")
    return _expect(gate, head, base, code=1, needles=["waiver t1: {} [0,{}) is not frontmatter text".format(
        RMV, fence + 1)])


def _tiled_shift(base_ceiling=None, head_ceiling=None):
    base = _alpha()
    if base_ceiling is None:
        base.budget = None
    else:
        base.budget["non-clause-text-per-change"] = base_ceiling
    head = base.copy().edit(ALPHA, "Gamma must hold.\n", "Gamma must hold.\n\n")
    if head_ceiling is not None:
        head.budget = dict(ZEROS, **{"non-clause-text-per-change": head_ceiling})
    head.waivers = _tile_all(base, head, ALPHA)
    return base, head


@_vector("waiver/per-change-ceiling-from-base")
def _v(gate):
    allowed = _expect(gate, *reversed(_tiled_shift(1)), code=0)
    refused = _expect(gate, *reversed(_tiled_shift(0)), code=1, needles=[
        "waiver ceiling exceeded: 1 live non-clause-text waiver(s), base ceiling 0"])
    return allowed[0] and refused[0], (allowed, refused)


@_vector("waiver/ceiling-raised-in-same-change-not-used")
def _v(gate):
    base, head = _tiled_shift(0, 1)
    return _expect(gate, head, base, code=1, needles=["base ceiling 0"])


@_vector("waiver/budget-change-with-new-waiver-fails")
def _v(gate):
    base, head = _tiled_shift(1, 2)
    return _expect(gate, head, base, code=1, needles=["budget change must land alone"])


@_vector("waiver/no-base-budget-means-zero")
def _v(gate):
    base, head = _tiled_shift(None)
    head.budget = dict(ZEROS)
    return _expect(gate, head, base, code=1, needles=["base ceiling 0"])


@_vector("waiver/tile-covering-clause-bytes-refused")
def _v(gate):
    base, head = _wording()
    start, end = head.span(X)
    head.waivers.append(_rw("t1", ALPHA, "head", start, start + 3, base, head, "whitespace"))
    base.budget["non-clause-text-per-change"] = 1
    head.budget = dict(base.budget)
    return _expect(gate, head, base, code=1, needles=["waiver t1: the tile [{},{}) covers clause text".format(
        start, start + 3)])


def _carried(change):
    """Change N+1 over a base that already carries w-1; change edits HEAD's log."""
    base, head = _wording()
    base.waivers = json.loads(json.dumps(head.waivers))
    base.waivers.append(_cw("w-2", "wording-only", Y, base, head))
    head.waivers = json.loads(json.dumps(base.waivers))
    change(head)
    return base, head


@_vector("waiver/carried-row-reason-changed-fails")
def _v(gate):
    base, head = _carried(lambda head: head.waivers[0].update(reason="reviewed again"))
    live_under_identity_rule = head.waivers[0] not in base.waivers
    ok, detail = _expect(gate, head, base, code=1, needles=["waiver log is append-only: row 0 (w-1) changed or removed"])
    return ok and live_under_identity_rule, detail


@_vector("waiver/carried-row-digest-changed-fails")
def _v(gate):
    base, head = _carried(lambda head: head.waivers[1].update({"head-sha256": "0" * 64}))
    return _expect(gate, head, base, code=1, needles=["append-only: row 1 (w-2) changed or removed"])


@_vector("waiver/carried-row-deleted-fails")
def _v(gate):
    base, head = _carried(lambda head: head.waivers.pop())
    return _expect(gate, head, base, code=1, needles=["append-only: row 1 (w-2) changed or removed"])


@_vector("waiver/carried-rows-reordered-fails")
def _v(gate):
    base, head = _carried(lambda head: head.waivers.reverse())
    return _expect(gate, head, base, code=1, needles=["append-only: row 0 (w-1) changed or removed"])


@_vector("waiver/row-inserted-before-carried-rows-fails")
def _v(gate):
    base, head = _carried(lambda head: head.waivers.insert(0, dict(head.waivers[0], id="w-0")))
    return _expect(gate, head, base, code=1, needles=["append-only: row 0 (w-1) changed or removed"])


def _sequence():
    """T0 -> T1 waived by w-1 (change N), T1 -> T0 waived by w-2 (the revert), T0 -> T1 again (reapply)."""
    t0, t1 = _wording()
    t0.budget["wording-only-per-change"] = 1
    t1.budget = dict(t0.budget)
    w1 = t1.waivers[0]
    t2 = t0.copy()
    t2.waivers = [w1, _cw("w-2", "wording-only", X, t1, t0)]
    t3 = t1.copy()
    t3.waivers = [w1, t2.waivers[1]]
    return t0, t1, t2, t3


@_vector("waiver/appended-row-live-in-its-change-only")
def _v(gate):
    t0, t1, _t2, _t3 = _sequence()
    live = _expect(gate, t1, t0, code=0, needles=["WAIVED: fixalpha.1 by wording-only w-1"])
    rerun = _expect(gate, t1, t1, code=0, needles=["STALE: w-1"], absent=["WAIVED:"])
    recur_base = t0.copy()
    recur_base.waivers = list(t1.waivers)
    recurrence = _expect(gate, t1, recur_base, code=1, needles=["STALE: w-1", "changed clause fixalpha.1 is not bound"])
    return live[0] and rerun[0] and recurrence[0], (live, rerun, recurrence)


@_vector("waiver/revert-reapply-carried-row-does-not-discharge")
def _v(gate):
    t0, t1, t2, t3 = _sequence()
    steps = [_expect(gate, t1, t0, code=0), _expect(gate, t2, t1, code=0),
             _expect(gate, t3, t2, code=1, needles=["changed clause fixalpha.1 is not bound"])]
    return all(step[0] for step in steps), steps


def _reapply(**extra):
    t0, t1, t2, t3 = _sequence()
    t3.waivers.append(dict(_cw("w-3", "wording-only", X, t2, t3), **extra))
    return t2, t3


@_vector("waiver/repeat-key-without-repeats-fails")
def _v(gate):
    base, head = _reapply()
    return _expect(gate, head, base, code=1, needles=["waiver w-3 repeats the transition of w-1; a new waiver"])


@_vector("waiver/repeat-key-with-repeats-passes-and-reported")
def _v(gate):
    base, head = _reapply(repeats="w-1")
    return _expect(gate, head, base, code=0, needles=["by wording-only w-3 (repeats w-1)", "repeats 1)"])


@_vector("waiver/repeats-naming-wrong-row-fails")
def _v(gate):
    base, head = _reapply(repeats="w-2")
    return _expect(gate, head, base, code=1, needles=["waiver w-3: repeats names w-2, not the latest earlier row"])


@_vector("waiver/repeats-naming-missing-row-fails")
def _v(gate):
    base, head = _reapply(repeats="w-9")
    return _expect(gate, head, base, code=1, needles=["waiver w-3: repeats names w-9, which is no earlier row"])


@_vector("waiver/duplicate-key-within-one-change-fails")
def _v(gate):
    base, head = _wording(budget=2)
    head.waivers.append(dict(head.waivers[0], id="w-1b"))
    return _expect(gate, head, base, code=1, needles=["waivers w-1 and w-1b carry the same transition in one change"])


@_vector("waiver/duplicate-id-fails")
def _v(gate):
    base, head = _wording(budget=2)
    head.waivers.append(dict(head.waivers[0], reason="again"))
    return _expect(gate, head, base, code=2, needles=["duplicate waiver id w-1"])


@_vector("waiver/missing-id-refused")
def _v(gate):
    base, head = _wording()
    del head.waivers[0]["id"]
    return _expect(gate, head, base, code=2, needles=["missing or malformed id"])


def _anchor_only(budget=1, **extra):
    base, head = _dup_moved({"anchor-only-per-change": budget})
    head.waivers = [_cw("w-a", "anchor-only", XD, base, head, **extra)]
    return base, head


@_vector("waiver/anchor-only-passes-for-moved-anchor")
def _v(gate):
    base, head = _anchor_only()
    return _expect(gate, head, base, code=0, needles=["anchor-only 1"])


@_vector("waiver/anchor-only-anchor-digest-mismatch-fails")
def _v(gate):
    base, head = _anchor_only(**{"head-anchor-sha256": "0" * 64})
    return _expect(gate, head, base, code=1, needles=["digest mismatch for fixdupl.1 (head-anchor-sha256)"])


@_vector("waiver/anchor-only-on-text-change-refused")
def _v(gate):
    base, head = _wording("anchor-only")
    return _expect(gate, head, base, code=1, needles=["the text of fixalpha.1 changed; use wording-only or bind it"])


@_vector("waiver/anchor-only-with-changed-rows-refused")
def _v(gate):
    base, head = _anchor_only()
    head.rows[0] = _req(XD, ["claim:completion", "claim:bounded"], ["count:reconciled"])
    return _expect(gate, head, base, code=1, needles=["anchor-only needs identical base and HEAD rows for fixdupl.1"])


@_vector("waiver/anchor-only-ceiling-zero-fails")
def _v(gate):
    base, head = _anchor_only(budget=0)
    return _expect(gate, head, base, code=1, needles=["waiver ceiling exceeded: 1 live anchor-only waiver(s), base "
                                                      "ceiling 0"])


@_vector("waiver/wording-only-anchor-digest-mismatch-fails")
def _v(gate):
    base, head = _wording(**{"head-anchor-sha256": "0" * 64})
    return _expect(gate, head, base, code=1, needles=["digest mismatch for fixalpha.1 (head-anchor-sha256)"])


@_vector("waiver/wording-only-on-unchanged-text-anchor-move-refused")
def _v(gate):
    base, head = _dup_moved({"wording-only-per-change": 1, "anchor-only-per-change": 0})
    head.waivers = [_cw("w-a", "wording-only", XD, base, head)]
    return _expect(gate, head, base, code=1, needles=["the text of fixdupl.1 is unchanged, so wording-only does not "
                                                      "apply; an anchor move with unchanged rows uses anchor-only"])


# --------------------------------------------------------------------------------------- inventory

@_vector("inventory/reported-not-waiver")
def _v(gate):
    base = _alpha()
    return _expect(gate, base.copy(), base, code=0, needles=[
        "UNENCODED INVENTORY (not a waiver): 1 clause(s): fixalpha.3", "waived 0 (", "inventory 1"])


@_vector("inventory/discharges-nothing")
def _v(gate):
    base = _alpha()
    head = _text_edit(base.copy(), Z, "Gamma must", "Gamma must always")
    return _expect(gate, head, base, code=1, needles=["UNENCODED INVENTORY (not a waiver): 1 clause(s): fixalpha.3",
                                                      "no base encoding for fixalpha.3"])


# ------------------------------------------------------------------------- retro (git fixtures)

def _git_fixture(directory, revisions):
    """A throwaway repository with one commit per revision; returns (history, [commit ids])."""
    from _git_fixture_env import git_fixture_env
    root = Path(directory) / "repo"
    root.mkdir()
    env = git_fixture_env(GIT_AUTHOR_NAME="Rule Behaviour Self-Test", GIT_AUTHOR_EMAIL="selftest@example.invalid",
                          GIT_COMMITTER_NAME="Rule Behaviour Self-Test",
                          GIT_COMMITTER_EMAIL="selftest@example.invalid")
    subprocess.run(["git", "-C", str(root), "init", "-q"], env=env, capture_output=True, check=True,
                   timeout=CHILD_TIMEOUT)
    oids = []
    for tree in revisions:
        for rel in list(_walk_files(root, ".aiqt")) + list(_walk_files(root, "tools")):
            (root / rel).unlink()
        for rel, data in tree.files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(data)
        subprocess.run(["git", "-C", str(root), "add", "-A"], env=env, capture_output=True, check=True,
                       timeout=CHILD_TIMEOUT)
        subprocess.run(["git", "-C", str(root), "commit", "-q", "--allow-empty", "-m", "fixture revision"], env=env,
                       capture_output=True, check=True, timeout=CHILD_TIMEOUT)
        oids.append(subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], env=env, capture_output=True,
                                   check=True, timeout=CHILD_TIMEOUT).stdout.decode("ascii").strip())
    return root, oids


def _retro_fixture(directory, gate, anchor="clause", text_sha=None, span_text="Alpha must hold.", rows=None,
                   old="REJECT set:enumerated"):
    pinned = _alpha()
    pinned.rows[0] = _req(X, ["claim:completion"], ["set:enumerated"])
    pinned.cases["k-x"]["expect"]["head"] = "REJECT set:enumerated"
    head = _alpha()
    root, oids = _git_fixture(directory, [pinned.build(), head.build()])
    if anchor == "clause":
        row = dict(_req(X, ["claim:completion"], ["set:enumerated"]), **{"text-sha256": text_sha or pinned.sha(X)})
    else:
        row = dict(kind="require", when=["claim:completion"], require=["set:enumerated"], path=ALPHA, text=span_text,
                   **{"text-sha256": text_sha or _sha(span_text.encode("utf-8")), "as-clause": X})
    head.cases["k-x"]["retro"] = {"source": oids[0], "old": old, "row": rows if rows is not None else [row]}
    return root, head


@_vector("retro/pinned-text-verified", git=True)
def _v(gate):
    with tempfile.TemporaryDirectory(prefix="rule-behaviour-retro-") as directory:
        root, head = _retro_fixture(directory, gate)
        good = _run(gate, head, None, gate.GitHistory(root))
        root2, wrong = _retro_fixture(tempfile.mkdtemp(dir=directory), gate, text_sha="0" * 64)
        bad = _run(gate, wrong, None, gate.GitHistory(root2))
    return (good[0] == 0 and "RETRO (informational, never binding): k-x: REJECT set:enumerated at" in good[1]
            and "caused by fixalpha.1" in good[1] and bad[0] == 1 and "retro anchor fixalpha.1 does not match" in bad[1]), \
        (good, bad)


@_vector("retro/span-anchor-unique-in-pinned-blob", git=True)
def _v(gate):
    with tempfile.TemporaryDirectory(prefix="rule-behaviour-retro-") as directory:
        root, head = _retro_fixture(directory, gate, anchor="span")
        once = _run(gate, head, None, gate.GitHistory(root))
        root2, twice = _retro_fixture(tempfile.mkdtemp(dir=directory), gate, anchor="span", span_text="must hold.")
        many = _run(gate, twice, None, gate.GitHistory(root2))
    return (once[0] == 0 and many[0] == 1 and "does not occur exactly once" in many[1]), (once, many)


@_vector("retro/history-missing-exit-2", git=True)
def _v(gate):
    with tempfile.TemporaryDirectory(prefix="rule-behaviour-retro-") as directory:
        root, head = _retro_fixture(directory, gate)
        head.cases["k-x"]["retro"]["source"] = "1" * 40
        got = _run(gate, head, None, gate.GitHistory(root))
        missing = _run(gate, head, None, None)
    return got[0] == 2 and "is not in the history" in got[1] and missing[0] == 2, (got, missing)


@_vector("retro/never-read-by-binding", git=True)
def _v(gate):
    with tempfile.TemporaryDirectory(prefix="rule-behaviour-retro-") as directory:
        root, head = _retro_fixture(directory, gate)
        root2, other = _retro_fixture(tempfile.mkdtemp(dir=directory), gate, rows=[
            dict(_req(Y, ["claim:completion"], ["set:enumerated"]), **{"text-sha256": _alpha().sha(Y)})])
        base, edited = _flip_pair()
        results = []
        for repo, fx in ((root, head), (root2, other)):
            change = edited.copy()
            change.cases["k-x"] = dict(change.cases["k-x"], retro=fx.cases["k-x"]["retro"],
                                       expect=change.cases["k-x"]["expect"])
            change.cases["k-f"] = _case("k-f", [X], FLIP, "REJECT set:enumerated", base="ACCEPT")
            code, text = _run(gate, change, base, gate.GitHistory(repo))
            results.append((code, [line for line in text.splitlines() if line.startswith(("BOUND", "binding"))]))
    return results[0][0] == 0 and results[0] == results[1], results


@_vector("retro/no-attributed-cause-fails", git=True)
def _v(gate):
    with tempfile.TemporaryDirectory(prefix="rule-behaviour-retro-") as directory:
        pinned = _alpha()
        head = _alpha()
        head.rows = [_req(X, ["claim:completion"], ["set:enumerated"]), _req(Y, ["claim:completion"], ["set:enumerated"]),
                     _req(Z, ["claim:completion"], ["count:reconciled"])]
        head.cases = {"k-z": _case("k-z", [Z], {"claim": "completion"}, "REJECT count:reconciled set:enumerated")}
        root, oids = _git_fixture(directory, [pinned.build(), head.build()])
        head.cases["k-z"]["retro"] = {"source": oids[0], "old": "REJECT count:reconciled", "row": [
            dict(_req(Z, ["claim:completion"], ["count:reconciled"]), **{"text-sha256": pinned.sha(Z)})]}
        got = _run(gate, head, None, gate.GitHistory(root))
    return got[0] == 1 and "no clause whose encoding differs accounts for the retro difference" in got[1], got


@_vector("binding/base-unresolvable-exit-2", git=True)
def _v(gate):
    with tempfile.TemporaryDirectory(prefix="rule-behaviour-base-") as directory:
        root, _oids = _git_fixture(directory, [_alpha().build()])
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = gate.run(root, "refs/heads/no-such-branch")
    return code == 2 and "does not resolve to a commit" in err.getvalue(), (code, err.getvalue())


@_vector("binding/option-like-base-exit-2", git=True)
def _v(gate):
    with tempfile.TemporaryDirectory(prefix="rule-behaviour-base-") as directory:
        root, _oids = _git_fixture(directory, [_alpha().build()])
        got = _run_root(gate, root, "--all")
    return got[0] == 2 and "base '--all' is not a revision" in got[1], got


@_vector("binding/shallow-history-exit-2", git=True)
def _v(gate):
    with tempfile.TemporaryDirectory(prefix="rule-behaviour-base-") as directory:
        root, oids = _git_fixture(directory, [_alpha().build(), _alpha().build()])
        (root / ".git" / "shallow").write_text(oids[1] + "\n", encoding="ascii")
        single = _run_root(gate, root, "HEAD")
        push = _run_each(gate, root, oids[0])
    needle = "the history is shallow; the base leg needs full history"
    return single[0] == 2 and needle in single[1] and push[0] == 2 and needle in push[1], (single, push)


def _git_symlink_base(directory):
    """A repository whose first commit carries a symlink among the rule files and whose second removes it."""
    from _git_fixture_env import git_fixture_env
    root = Path(directory) / "repo"
    root.mkdir()
    env = git_fixture_env(GIT_AUTHOR_NAME="Rule Behaviour Self-Test", GIT_AUTHOR_EMAIL="selftest@example.invalid",
                          GIT_COMMITTER_NAME="Rule Behaviour Self-Test",
                          GIT_COMMITTER_EMAIL="selftest@example.invalid")
    subprocess.run(["git", "-C", str(root), "init", "-q"], env=env, capture_output=True, check=True,
                   timeout=CHILD_TIMEOUT)
    _write_tree(_alpha(), root)
    link = root / RULES_DIR / "fixlink.md"
    oids = []
    for step in ("link", "unlink"):
        if step == "link":
            os.symlink("fixalpha.md", link)
        else:
            link.unlink()
        subprocess.run(["git", "-C", str(root), "add", "-A"], env=env, capture_output=True, check=True,
                       timeout=CHILD_TIMEOUT)
        subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "fixture " + step], env=env,
                       capture_output=True, check=True, timeout=CHILD_TIMEOUT)
        oids.append(subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], env=env, capture_output=True,
                                   check=True, timeout=CHILD_TIMEOUT).stdout.decode("ascii").strip())
    return root, oids


@_vector("binding/base-tree-symlink-exit-2", git=True)
def _v(gate):
    with tempfile.TemporaryDirectory(prefix="rule-behaviour-base-") as directory:
        root, oids = _git_symlink_base(directory)
        got = _run_root(gate, root, oids[0])
    return got[0] == 2 and "{}/fixlink.md at {}: not a regular file".format(RULES_DIR, oids[0]) in got[1], got


# ------------------------------------------------------------------- the push leg (git fixtures)

def _run_each(gate, root, base):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = gate.run_each_commit(root, base)
    return code, out.getvalue() + err.getvalue()


def _readme(tree, text):
    tree.files["README.md"] = text.encode("utf-8")
    return tree


@_vector("binding/push-range-judges-every-commit", git=True)
def _v(gate):
    """The first pushed commit adds unregistered rule prose, the second changes only README.md: binding the
    last commit alone passes, and the push leg fails on the first."""
    with tempfile.TemporaryDirectory(prefix="rule-behaviour-push-") as directory:
        edited = _alpha().edit(ALPHA, "Gamma must hold.\n", "Gamma must hold.\n\nOmega may vary.\n")
        root, oids = _git_fixture(directory, [_alpha().build(), _readme(edited.build(), "one\n"),
                                              _readme(edited.build(), "two\n")])
        last_only = _run_root(gate, root, "HEAD^")
        push = _run_each(gate, root, oids[0])
    return (last_only[0] == 0 and push[0] == 1
            and "COMMIT {} against its first parent {}: exit 1".format(oids[1], oids[0]) in push[1]
            and "COMMIT {} against its first parent {}: exit 0".format(oids[2], oids[1]) in push[1]
            and "unregistered rule text at {} head".format(ALPHA) in push[1]
            and "1 of 2 commit(s) fail: {}".format(oids[1]) in push[1]), (last_only, push)


@_vector("binding/push-range-each-commit-against-its-parent", git=True)
def _v(gate):
    """The first pushed commit raises a ceiling alone and the second spends it: each passes against its own
    parent, while the second judged against the push's base would exceed the base ceiling."""
    with tempfile.TemporaryDirectory(prefix="rule-behaviour-push-") as directory:
        start = _alpha()
        raised = start.copy()
        raised.budget["wording-only-per-change"] = 1
        spent = _text_edit(raised.copy(), X, "Alpha must", "Alpha must always")
        spent.waivers = [_cw("w-1", "wording-only", X, raised, spent)]
        root, oids = _git_fixture(directory, [start.build(), raised.build(), spent.build()])
        push = _run_each(gate, root, oids[0])
    return push[0] == 0 and "PASS: rule-behaviour push range {}..{}: 2 commit(s)".format(
        oids[0][:12], oids[2][:12]) in push[1], push


def _side_commits(root, oid):
    """Two commits off HEAD's first-parent chain: a child of oid on no branch, and a parentless one."""
    from _git_fixture_env import git_fixture_env
    env = git_fixture_env(GIT_AUTHOR_NAME="Rule Behaviour Self-Test", GIT_AUTHOR_EMAIL="selftest@example.invalid",
                          GIT_COMMITTER_NAME="Rule Behaviour Self-Test",
                          GIT_COMMITTER_EMAIL="selftest@example.invalid")
    tree = subprocess.run(["git", "-C", str(root), "rev-parse", oid + "^{tree}"], env=env, capture_output=True,
                          check=True, timeout=CHILD_TIMEOUT).stdout.decode("ascii").strip()
    out = []
    for parents in (["-p", oid], []):
        out.append(subprocess.run(["git", "-C", str(root), "commit-tree", tree, *parents, "-m", "side"], env=env,
                                  capture_output=True, check=True, timeout=CHILD_TIMEOUT).stdout.decode("ascii")
                   .strip())
    return out


@_vector("binding/push-range-base-off-first-parent-chain-exit-2", git=True)
def _v(gate):
    with tempfile.TemporaryDirectory(prefix="rule-behaviour-push-") as directory:
        root, oids = _git_fixture(directory, [_alpha().build(), _readme(_alpha().build(), "one\n")])
        sibling, orphan = _side_commits(root, oids[0])
        got = [_run_each(gate, root, base) for base in (sibling, orphan)]
    return all(code == 2 and "is not on HEAD's first-parent chain" in text for code, text in got), got


@_vector("binding/push-range-empty-exit-2", git=True)
def _v(gate):
    with tempfile.TemporaryDirectory(prefix="rule-behaviour-push-") as directory:
        root, _oids = _git_fixture(directory, [_alpha().build()])
        got = _run_each(gate, root, "HEAD")
    return got[0] == 2 and "leaves no commit to judge" in got[1], got


class _FixedListing:
    """Stands in for the subprocess module inside one gate module: every run returns one fixed listing."""
    SubprocessError = subprocess.SubprocessError

    def __init__(self, stdout):
        # built before the swap, since the swap can rebind this module's own subprocess name
        self.result = subprocess.CompletedProcess(["git"], 0, stdout, b"")

    def run(self, _argv, **_kwargs):
        return self.result


# A listing whose second commit is not the first parent of the one before it is not a chain; git never
# prints one, so the listing is supplied directly.
@_vector("binding/push-range-broken-first-parent-chain-exit-2")
def _v(gate):
    head, parent, other, base = "1" * 40, "2" * 40, "3" * 40, "4" * 40
    saved = gate.subprocess
    gate.subprocess = _FixedListing("{} {}\n{} {}\n".format(head, parent, other, base).encode("ascii"))
    try:
        got = gate.GitHistory(ROOT).first_parent_range(base, head)
    except gate.GateError as exc:
        got = str(exc)
    except Exception as exc:  # a crash is a vector failure, never a pass
        got = "crashed ({}: {})".format(type(exc).__name__, exc)
    finally:
        gate.subprocess = saved
    return got == "cannot read the first-parent chain from {} to {}".format(base, head), got


# ------------------------------------------------------------- cases, and two more change shapes

@_vector("cases/expect-head-mismatch-fails")
def _v(gate):
    fx = _schema_head(lambda fx: fx.cases["k-x"]["expect"].update(head="ACCEPT"))
    return _expect(gate, fx, code=1, needles=["case k-x: HEAD outcome is 'REJECT count:reconciled', expect.head is "
                                              "'ACCEPT'"])


@_vector("cases/control-equal-outcome-fails")
def _v(gate):
    fx = _alpha()
    fx.cases["k-x-c"] = _control("k-x-c", "k-x", ["evidence"], {"claim": "completion", "evidence": ["id-check"]},
                                 "REJECT count:reconciled")
    return _expect(gate, fx, code=1, needles=["control k-x-c: its outcome does not differ from case k-x"])


@_vector("change/deletion-inside-new-span-marks-clause")
def _v(gate):
    base = _omega_base("Omega may well vary.")
    head = base.copy().edit(ALPHA, "Omega may well vary.", "Omega may vary.")
    head.clauses[W] = [ALPHA, "Omega may vary.", None, None]
    ok, detail = _expect(gate, head, base, code=1, needles=["no base encoding for fixalpha.4"])
    lines = _change_lines(detail[1], W)
    return ok and len(lines) == 1 and "new over changed bytes" in lines[0], detail


@_vector("change/window-bounds-move-marks-clause")
def _v(gate):
    base = _alpha()
    head = base.copy()
    start, end = base.lines(Z)
    head.clauses[Z] = [ALPHA, "Gamma must hold.", start - 1, end]
    return _expect(gate, head, base, code=1, needles=["ANCHOR MOVED: fixalpha.3: {} {}-{} -> {} {}-{}".format(
        ALPHA, start, end, ALPHA, start - 1, end), "no base encoding for fixalpha.3"])


# ---------------------------------------------------------------------------------------- mutants

# Each mutant removes or weakens one check in the gate source above SELF-TEST-BEGINS: (mutant id, the exact
# source text, its replacement, the vector that must then fail). Every vector but the listed twins must
# fail under at least one mutant, so no vector is left that removing a check would not expose.
_NOOP = "(lambda *args: None)("
MUTANTS = (
    ("row-unknown-key", '        raise GateError("{}: unknown key(s) {}".format(where, ", ".join(unknown)))',
     "        pass", "schema/obligation-unknown-key-refused"),
    ("vocabulary", '            raise GateError("{}: unknown value(s) {}".format(where, ", ".join(unknown)))',
     "            pass", "schema/obligation-unknown-predicate-refused"),
    ("limit-schema", '        if row.kind == "limit" and not row.effect <= suppressible:', "        if False:",
     "schema/limit-cancels-non-exempt-refused"),
    ("claim-vocabulary", "    if claim not in CLAIMS:", "    if False:", "schema/case-unknown-fact-refused"),
    ("where-vocabulary", '        if instance.get("where") not in WHERES:', "        if False:",
     "schema/instance-unknown-where-refused"),
    ("id-is-stem", '    if doc.get("id") != stem or not NAME_RE.fullmatch(stem):', "    if not NAME_RE.fullmatch(stem):",
     "schema/duplicate-case-id-refused"),
    ("stale-row", "        elif row.sha != clause.sha:", "        elif False:", "schema/stale-text-sha-fails"),
    ("unknown-clause", '            report.fail("obligation row for {}: no such clause in {}".format(row.clause, '
     'INVENTORY_REL))', "            continue", "schema/clause-missing-from-register-fails"),
    ("malformed-toml", '        raise GateError("{}: malformed ({})".format(label, type(exc).__name__))',
     "        return {}", "schema/malformed-toml-exit-2"),
    ("control-delta", "        if differing != set(case.delta):", "        if False:",
     "schema/control-delta-mismatch-refused"),
    ("case-keys", '        raise GateError("{}: key(s) {} not allowed on a {}".format(where, ", ".join(unknown), kind))',
     "        pass", "schema/prior-table-refused"),
    ("expect-keys", '        raise GateError("{}: expect key(s) {} not allowed on a {}".format(where, ", ".join(unknown), '
     'kind))', "        pass", "schema/expect-base-on-control-refused"),
    ("empty-case", "        if not clauses and not removes:", "        if False:",
     "schema/case-empty-clauses-and-empty-removes-refused"),
    ("empty-clauses", "        if not clauses and not removes:", "        if not clauses:",
     "schema/case-empty-clauses-with-removes-accepted"),
    ("removes-head", '                report.fail("case {} removes {}, which is still in {}".format(case.cid, cid, '
     'INVENTORY_REL))', "                pass", "schema/removes-id-in-head-register-fails"),
    ("removes-base", "            if cid not in base_register:", "            if False:",
     "schema/removes-id-absent-at-base-fails"),
    ("form-required", '        if "form" not in instance:', "        if False:", "schema/instance-missing-form-refused"),
    ("form-vocabulary", '        if instance["form"] not in FORMS:', "        if False:",
     "schema/instance-unknown-form-refused"),
    ("pair-label", '                if vocab is None or not isinstance(pair["label"], str) or pair["label"] not in vocab:',
     "                if False:",
     "schema/exempt-on-label-outside-vocab-refused"),
    ("vocab-surface", "            if set(vocab) - set(label for label, _w in pairs):", "            if False:",
     "schema/exempt-vocab-label-without-surface-refused"),
    ("pair-where", '                if pair["where"] not in WHERES:', "                if False:",
     "schema/exempt-on-unknown-where-refused"),
    ("pair-duplicate", '                if (pair["label"], pair["where"]) in seen:', "                if False:",
     "schema/exempt-on-duplicate-pair-refused"),
    ("vocab-needs-pairs", '            if "exempt-on" not in params:', "            if False:",
     "schema/exempt-vocab-without-exempt-on-refused"),
    ("exemption-surface", "    if (label, where) not in pairs:", "    if False:", "matcher/exemption-eligibility-matrix"),
    ("exemption-quotation", '    if form != "quotation":', "    if False:", "matcher/changelog-quote-operative-exempt-history-rejects"),
    ("exemption-always", "    if label not in vocab:\n        return False\n    if (label, where) not in pairs:\n"
     "        return False\n    if where in never:\n        return False\n    if form != \"quotation\":\n"
     "        return False\n", "", "matcher/wording-register-copy-head-rejects"),
    ("exemption-never", "        return False\n    return True\n\n\ndef outcome", "        return False\n    return False\n\n\n"
     "def outcome", "matcher/changelog-quote-quotation-exempt-history-accepts"),
    ("pairs-union", "    pairs = frozenset.intersection(*[row.pairs for row in eligible_rows])",
     "    pairs = frozenset().union(*[row.pairs for row in eligible_rows])", "matcher/exempt-on-intersection-across-rows"),
    ("bounded-derives", '    if scenario["claim"] in ("completion", "bounded"):', '    if scenario["claim"] == "completion":',
     "matcher/bounded-derives-completion"),
    ("limit-ignored", "    required -= suppressed - cancelled", "    required -= suppressed",
     "matcher/merge-bounded-head-rejects"),
    ("exempt-ignored", "    required -= suppressed - cancelled", "    required -= set()", "matcher/bounded-named-set-accepts"),
    ("own-evidence", '            if item not in kept and scenario["item-evidence"].get(item) != "own-evidence":',
     "            if item not in kept:", "matcher/retire-own-evidence-accepts"),
    ("kept-items", '            if item not in kept and scenario["item-evidence"].get(item) != "own-evidence":',
     '            if scenario["item-evidence"].get(item) != "own-evidence":', "matcher/retire-in-place-head-rejects"),
    ("multi-item", '    if any(len(items) >= 2 for _id, items in scenario["before"]):',
     '    if any(len(items) >= 3 for _id, items in scenario["before"]):', "matcher/retire-in-place-head-rejects"),
    ("set-evidence", '    if "set:enumerated" in required and "index-enumeration" not in evidence:',
     '    if "set:enumerated" in required and False:', "matcher/completion-named-set-rejects"),
    ("fixed-meets", '            met = disposition == "fixed" or (', "            met = (",
     "matcher/wording-register-copy-fixed-accepts"),
    ("order-dependent", "    triggered = [row for row in rows if row.when <= found]",
     "    triggered = [row for row in rows[:5] if row.when <= found]", "matcher/row-order-independent"),
    ("full-outcome-binding", '    return "ACCEPT" if result == "ACCEPT" else "REJECT"', "    return result",
     "binding/reject-to-reject-atom-change-does-not-bind"),
    ("listed-load-bearing", "            if outcome(without_clause(rows, cid), case.scenario) == full:", "            if False:",
     "ablation/listed-clause-not-load-bearing-fails"),
    ("dead-row", "        if not any(outcome(others, case.scenario) != outcome(rows, case.scenario)",
     "        if False and not any(outcome(others, case.scenario) != outcome(rows, case.scenario)",
     "ablation/dead-row-fails"),
    ("uncovered-report", '            report.note("UNCOVERED ROWS",', "            " + _NOOP + '"UNCOVERED ROWS",',
     "ablation/every-listed-clause-load-bearing"),
    ("cases-head", "        if got != case.head:", "        if False:", "cases/expect-head-mismatch-fails"),
    ("control-differs", '        if case.kind == "control" and case.head == cases[case.controls].head:', "        if False:",
     "cases/control-equal-outcome-fails"),
    ("residue-untouched-only", "                    for start, end in _subtract(low, high, spans):",
     "                    for start, end in ([] if any(s < high and e > low for s, e in spans) else [(low, high)]):",
     "change/one-replace-opcode-spans-clause-and-unregistered-prose-fails"),
    ("residue-off", "                        pieces.append((path, side, start, end))", "                        pass",
     "change/same-line-insert-after-setcmp6-must-register"),
    ("base-insert-points", "                        base_points.append(i1)", "                        pass",
     "change/insert-one-after-span-start-marks-clause"),
    ("head-delete-points", "                        head_points.append(j1)", "                        pass",
     "change/deletion-inside-new-span-marks-clause"),
    ("point-inclusive", "            or any(start < point < end for point in points))",
     "            or any(start <= point <= end for point in points))", "change/insert-at-span-start-goes-to-U"),
    ("range-inclusive", "    return (any(low < end and high > start for low, high in ranges)",
     "    return (any(low <= end and high >= start for low, high in ranges)",
     "change/replace-last-byte-before-span-goes-to-U"),
    ("touch-base", '        if old is not None and change.touched("base", cid):', "        if False:",
     "change/span-interior-edit-marks-clause"),
    ("added-file", "                head_ranges.append((0, len(new)))", "                pass",
     "change/new-rule-file-prose-must-register"),
    ("removed-file", "                base_ranges.append((0, len(old)))", "                pass",
     "binding/remove-sole-clause-file-untiled-lf-fails"),
    ("subtract-gap-inclusive", "        if start > at:", "        if start >= at:",
     "binding/new-span-starts-at-changed-range-start-binds"),
    ("subtract-tail-inclusive", "    if at < high:", "    if at <= high:", "change/residue-range-ends-at-span-end-reports-none"),
    ("subtract-left-edge", "            pieces.append((at, start))", "            pieces.append((at, start + 1))",
     "change/residue-range-starts-one-before-span-reports-one-byte"),
    ("subtract-right-edge", "        pieces.append((at, high))", "        pieces.append((at - 1, high))",
     "change/residue-range-ends-one-past-span-end-reports-one-byte"),
    ("maps-start-strict", "        if i1 <= start and end <= i2 and head_span ==", "        if i1 < start and end <= i2 and "
     "head_span ==", "change/anchor-insert-at-span-start-maps-not-moved"),
    ("maps-end-strict", "        if i1 <= start and end <= i2 and head_span ==", "        if i1 <= start and end < i2 and "
     "head_span ==", "change/anchor-insert-at-span-end-maps-not-moved"),
    ("maps-no-shift", "        if i1 <= start and end <= i2 and head_span == (j1 + (start - i1), j1 + (end - i1)):",
     "        if i1 <= start and end <= i2 and head_span == (start, end):", "change/line-shift-only-not-a-change"),
    ("anchor-moves-ignored", "        if old is not None and new is not None and old.sha == new.sha and change.moved(old, new):",
     "        if False:", "change/register-only-window-to-other-occurrence-marks-clause"),
    ("anchor-moves-on-text-change", "        if old is not None and new is not None and old.sha == new.sha and "
     "change.moved(old, new):", "        if old is not None and new is not None and change.moved(old, new):",
     "change/anchor-insert-one-after-span-start-touches"),
    ("anchor-path", "        if base_clause.path != head_clause.path:", "        if False:",
     "change/register-only-source-path-change-marks-clause"),
    ("anchor-window-lines", "        return base_offsets != head_offsets", "        return False",
     "change/window-bounds-move-marks-clause"),
    ("anchor-by-file-digest", "        if not _maps(self.alignment.get(path, ()), (s, e), (s2, e2)):",
     "        if self.base.read(path) != self.head.read(path):", "change/source-digest-only-change-not-anchor-move"),
    ("anchor-digest-fields", '        self.anchor = _sha("{}\\n{}\\n{}\\n{}".format(path, start, end, text).encode("utf-8"))',
     '        self.anchor = _sha("{}\\n{}".format(path, text).encode("utf-8"))',
     "change/anchor-move-with-text-change-in-C-once"),
    ("removed-reason", '            why.append("removed")', "            pass", "change/removed-clause-in-change-set"),
    ("registered-list", "    registered = sorted(cid for cid in set(head_register) - set(base_register) if cid not in reasons)",
     "    registered = []", "change/registration-only-goes-to-N"),
    ("registered-needs-case", "        if any(row.clause == cid for row in rows) and not any(cid in case.clauses for case in "
     "cases.values()):", "        if False:", "binding/registration-only-needs-baseline-case"),
    ("wholly-new", "        return change.wholly_changed(cid)", "        return True",
     "binding/new-span-first-byte-unchanged-not-bindable"),
    ("changed-cases", "    return [case for case in cases.values() if base.read(case.rel) != case.data]",
     "    return list(cases.values())", "binding/unchanged-case-does-not-count"),
    ("revert-not-required", "            if decision(base_out) != decision(head_out) and decision(revert_out) != "
     "decision(head_out):", "            if decision(base_out) != decision(head_out):", "binding/crossed-clause-decoy-fails"),
    ("revert-off", "    return without_clause(head_rows, cid) + [row for row in base_rows if row.clause == cid]",
     "    return list(head_rows)", "binding/joint-cause-binds-both"),
    ("listing-not-required", '            if case.kind != "case" or case.base is None or (cid not in case.clauses and cid not in '
     'case.removes):', '            if case.kind != "case" or case.base is None:', "binding/listed-wrong-clause-fails"),
    ("removes-not-binding", "(cid not in case.clauses and cid not in case.removes)", "(cid not in case.clauses)",
     "binding/removed-clause-flip-case-passes"),
    ("encoded-edit-bindable", "        if old is not None and new is not None:\n            return has_base_rows(cid)",
     "        if old is not None and new is not None:\n            return True",
     "binding/unencoded-at-base-edit-not-bindable"),
    ("removal-bindable", "        if old is not None:\n            return has_base_rows(cid)",
     "        if old is not None:\n            return True", "change/removed-clause-in-change-set"),
    ("unbound-skipped", "        if cid in bound or cid in waived:", "        if True:", "binding/changed-clause-no-case-fails"),
    ("bound-note", '"{} by case {} (unmet base: {}; head: {}; with {} reverted: {})"', '"{} by case {}"',
     "binding/bound-report-prints-atom-diagnostics"),
    ("encoding-changed-report", '            report.note("ENCODING CHANGED", cid)', "            pass",
     "binding/encoding-first-then-edit-binds"),
    ("genesis-edit", "        if reasons or residue:", "        if False:", "binding/genesis-with-rule-edit-fails"),
    ("genesis-detect", "    genesis = base.read(SCRIPT_REL) is None and base.read(TABLE_REL) is None",
     "    genesis = False", "binding/genesis-empty-change-passes"),
    ("base-without-table", '        raise GateError("the base has {} but no {}".format(SCRIPT_REL, TABLE_REL))',
     '        return {"genesis": True, "bound": 0, "waived": {}, "repeats": 0}', "binding/base-gate-without-table-exit-2"),
    ("resolve", '            raise GateError("base {!r} does not resolve to a commit".format(ref))',
     '            return "0" * 40', "binding/base-unresolvable-exit-2"),
    ("liveness-by-identity", "    for index, carried in enumerate(base_waivers):\n"
     "        if index >= len(head_waivers) or head_waivers[index].raw != carried.raw:\n"
     '            report.fail("waiver log is append-only: row {} ({}) changed or removed".format(index, carried.wid))\n'
     "            return waived, 0\n    live = head_waivers[len(base_waivers):]\n",
     "    live = [w for w in head_waivers if w.raw not in [c.raw for c in base_waivers]]\n",
     "waiver/carried-row-reason-changed-fails"),
    ("append-length", "        if index >= len(head_waivers) or head_waivers[index].raw != carried.raw:",
     "        if index < len(head_waivers) and head_waivers[index].raw != carried.raw:", "waiver/carried-row-deleted-fails"),
    ("live-all", "    live = head_waivers[len(base_waivers):]", "    live = list(head_waivers)",
     "waiver/appended-row-live-in-its-change-only"),
    ("stale-report", '        report.note("STALE", "{} (carried; discharges nothing)".format(carried.wid))',
     "        pass", "waiver/carried-row-stale-discharges-nothing"),
    ("budget-alone", "    if live and base.read(BUDGET_REL) != head.read(BUDGET_REL):", "    if False:",
     "waiver/budget-change-with-new-waiver-fails"),
    ("same-key", "        if waiver.key in live_keys:", "        if False:", "waiver/duplicate-key-within-one-change-fails"),
    ("repeat-required", "        if earlier and waiver.repeats is None:", "        if False:",
     "waiver/repeat-key-without-repeats-fails"),
    ("repeat-named", "        elif waiver.repeats is not None and named is None:", "        elif False:",
     "waiver/repeats-naming-missing-row-fails"),
    ("repeat-latest", "        elif waiver.repeats is not None and (not earlier or named is not earlier[-1]):",
     "        elif False:", "waiver/repeats-naming-wrong-row-fails"),
    ("repeat-count", "            repeats += 1", "            pass", "waiver/repeat-key-with-repeats-passes-and-reported"),
    ("ceiling", "        if count > ceilings[kind]:", "        if False:", "waiver/per-change-ceiling-from-base"),
    ("ceiling-from-head", "    budget_data = base.read(BUDGET_REL)", "    budget_data = head.read(BUDGET_REL)",
     "waiver/ceiling-raised-in-same-change-not-used"),
    ("no-budget-zero", "        else dict((kind, 0) for kind in BUDGET_KEYS)", "        else dict((kind, 9) for kind in BUDGET_KEYS)",
     "waiver/no-base-budget-means-zero"),
    ("clause-waiver-target", "    if cid not in reasons:", "    if False:", "waiver/live-row-matching-nothing-fails"),
    ("clause-waiver-digests", '        report.fail("waiver {}: digest mismatch for {} ({})".format(waiver.wid, cid, ", ".join('
     'wrong)))\n        return', "        pass", "waiver/digest-mismatch-fails"),
    ("wording-needs-encoding", '            report.fail("waiver {}: wording-only needs a base encoding for {}; use unencoded"'
     '.format(waiver.wid, cid))\n            return', "            pass", "waiver/wording-only-on-unencoded-clause-refused"),
    ("wording-needs-text-change", "        if not text_changed:", "        if False:",
     "waiver/wording-only-on-unchanged-text-anchor-move-refused"),
    ("wording-rows", "        elif _row_keys(rows, cid, True) != _row_keys(base_rows, cid, True):", "        elif False:",
     "waiver/wording-only-encoding-changed-fails"),
    ("unencoded-encoded", '    elif waiver.kind == "unencoded":\n        if encoded:',
     '    elif waiver.kind == "unencoded":\n        if False:', "waiver/unencoded-on-encoded-clause-refused"),
    ("unencoded-bindable", "        if bindable(cid) and any(row.clause == cid for row in rows):", "        if False:",
     "waiver/unencoded-on-bindable-new-clause-refused"),
    ("anchor-text", '    elif waiver.kind == "anchor-only":\n        if text_changed:',
     '    elif waiver.kind == "anchor-only":\n        if False:', "waiver/anchor-only-on-text-change-refused"),
    ("anchor-rows", "        if not encoded or _row_keys(rows, cid) != _row_keys(base_rows, cid):",
     "        if not encoded:", "waiver/anchor-only-with-changed-rows-refused"),
    ("waiver-counts", "    counts = dict((kind, sum(1 for value in waived.values() if value == kind)) for kind in "
     "CLAUSE_WAIVER_KINDS)", "    counts = dict((kind, 0) for kind in CLAUSE_WAIVER_KINDS)",
     "waiver/reported-separately-not-bound"),
    ("tile-over-clause", "        if data is not None and any(s < end and e > start for s, e in spans):", "        if False:",
     "waiver/tile-covering-clause-bytes-refused"),
    ("range-digests", '        report.fail("waiver {}: digest mismatch for {} [{},{}) ({})".format(',
     "        " + _NOOP + '"waiver {}: digest mismatch for {} [{},{}) ({})".format(', "waiver/range-bytes-digest-mismatch-fails"),
    ("category-check", '    if not _category_holds(data, start, end, raw["category"]):', "    if False:",
     "waiver/non-clause-text-prose-range-refused"),
    ("frontmatter-past-end", "                return end <= block_end", "                return end <= block_end + 1",
     "waiver/frontmatter-tile-ends-one-past-block-end-refused"),
    ("frontmatter-before-end", "                return end <= block_end", "                return end < block_end",
     "waiver/frontmatter-tile-ends-at-block-end-passes"),
    ("frontmatter-fence", '        if not data.startswith(b"---\\n"):', '        if not data.startswith(b"---\\n\\n"):',
     "waiver/frontmatter-tile-ends-one-before-block-end-passes"),
    ("heading-category", "        return ATX_HEADING_RE.match(data, line_start) is not None and end <= line_end",
     "        return False", "waiver/non-clause-text-heading-range-passes"),
    ("heading-any-hash", "        return ATX_HEADING_RE.match(data, line_start) is not None and end <= line_end",
     '        return data[line_start:line_start + 1] == b"#" and end <= line_end',
     "waiver/heading-tile-hash-without-space-refused"),
    ("heading-hash-count", 'ATX_HEADING_RE = re.compile(rb"#{1,6}(?:[ \\t]|\\n|\\Z)")',
     'ATX_HEADING_RE = re.compile(rb"#+(?:[ \\t]|\\n|\\Z)")', "waiver/heading-tile-seven-hashes-refused"),
    ("heading-five-hashes", 'ATX_HEADING_RE = re.compile(rb"#{1,6}(?:[ \\t]|\\n|\\Z)")',
     'ATX_HEADING_RE = re.compile(rb"#{1,5}(?:[ \\t]|\\n|\\Z)")', "waiver/heading-tile-six-hashes-passes"),
    ("heading-empty-line", 'ATX_HEADING_RE = re.compile(rb"#{1,6}(?:[ \\t]|\\n|\\Z)")',
     'ATX_HEADING_RE = re.compile(rb"#{1,6}(?:[ \\t]|\\Z)")', "waiver/heading-tile-bare-hash-line-passes"),
    ("heading-past-line-end", "is not None and end <= line_end", "is not None",
     "waiver/heading-tile-ends-one-past-line-end-refused"),
    ("heading-before-line-end", "is not None and end <= line_end", "is not None and end < line_end",
     "waiver/heading-tile-ends-at-line-end-passes"),
    ("whitespace-any", "        return all(byte in (0x20, 0x0A) for byte in piece)", "        return True",
     "waiver/non-clause-text-prose-range-refused"),
    ("whitespace-spaces", "        return all(byte in (0x20, 0x0A) for byte in piece)",
     "        return all(byte == 0x20 for byte in piece)", "waiver/non-clause-text-whitespace-range-passes"),
    ("tile-overlap", "    if any(s < end and e > start for s, e in have):", "    if False:", "waiver/non-clause-text-overlap-fails"),
    ("tile-coverage", "        for low, high in _subtract(start, end, tiled.get((path, side), ())):",
     "        for low, high in ([] if tiled.get((path, side)) else [(start, end)]):", "waiver/non-clause-text-gap-fails"),
    ("tile-split", "    have.append((start, end))", "    have[:] = [(start, end)]", "waiver/non-clause-text-tiles-exactly"),
    ("waiver-kind-type", '            raise GateError("{}: waiver kind {!r} is not a string".format(where, kind))',
     "            pass", "waiver/kind-not-a-string-exit-2"),
    ("row-kind-type", "    if not isinstance(kind, str) or kind not in EFFECT_KEY:", "    if kind not in EFFECT_KEY:",
     "schema/obligation-kind-not-a-string-exit-2"),
    ("pair-label-type", 'if vocab is None or not isinstance(pair["label"], str) or pair["label"] not in vocab:',
     'if vocab is None or pair["label"] not in vocab:', "schema/exempt-on-label-not-a-string-exit-2"),
    ("never-exempt", "    if where in never:", "    if False:", "matcher/never-exempt-overrides-exempt-on-pair-rejects"),
    ("range-chain-continuity", "            if not all(COMMIT_RE.fullmatch(field) for field in fields) or fields[0] != "
     "expected:", "            if not all(COMMIT_RE.fullmatch(field) for field in fields):",
     "binding/push-range-broken-first-parent-chain-exit-2"),
    ("retired-kinds", "        if kind in RETIRED_WAIVER_KINDS:", "        if False:", "waiver/untestable-kind-refused"),
    ("unknown-kinds", "        if kind not in WAIVER_KINDS:", "        if False:", "waiver/unknown-kind-refused"),
    ("duplicate-id", "        if wid in ids:", "        if False:", "waiver/duplicate-id-fails"),
    ("missing-id", "        if not isinstance(wid, str) or not NAME_RE.fullmatch(wid):", "        if False:",
     "waiver/missing-id-refused"),
    ("inventory-report", '            report.note("UNENCODED INVENTORY (not a waiver)", "{} clause(s): {}".format(',
     "            " + _NOOP + '"UNENCODED INVENTORY (not a waiver)", "{} clause(s): {}".format(',
     "inventory/reported-not-waiver"),
    ("retro-anchor", "                if clause is None or clause.sha != anchor[3]:", "                if False:",
     "retro/pinned-text-verified"),
    ("retro-span-once", "                if blob is None or blob.count(needle) != 1 or _sha(needle) != anchor[3]:",
     "                if blob is None or _sha(needle) != anchor[3]:", "retro/span-anchor-unique-in-pinned-blob"),
    ("retro-history", '        if not history.has_commit(retro["source"]):', "        if False:",
     "retro/history-missing-exit-2"),
    ("retro-cause", "        if not causes:", "        if False:", "retro/no-attributed-cause-fails"),
    ("retro-into-binding", '    base_rows = parse_table(base.read(TABLE_REL), "{} at {}".format(TABLE_REL, base.label))',
     '    base_rows = parse_table(base.read(TABLE_REL), "{} at {}".format(TABLE_REL, base.label)) + [row for case in '
     'cases.values() if case.retro for row in case.retro["rows"]]', "retro/never-read-by-binding"),
    ("premise-bounded", '        if scenario["claim"] == "bounded" and scenario["named-set"] is None:', "        if False:",
     "premise/bounded-without-named-set-fails"),
    ("premise-bounded-ignores-set", '        if scenario["claim"] == "bounded" and scenario["named-set"] is None:',
     '        if scenario["claim"] == "bounded":', "premise/bounded-with-named-set-passes"),
    ("premise-probe", '        if (scenario["op"] == "wording-fix" or scenario["instances"]) and not scenario["probe"]:',
     "        if False:", "premise/wording-fix-without-probe-fails"),
    ("premise-probe-ignores-probe", '        if (scenario["op"] == "wording-fix" or scenario["instances"]) and not '
     'scenario["probe"]:', '        if scenario["op"] == "wording-fix" or scenario["instances"]:',
     "premise/wording-fix-with-probe-passes"),
    ("premise-instances", '        if (scenario["op"] == "wording-fix" or scenario["instances"]) and not scenario["probe"]:',
     '        if scenario["op"] == "wording-fix" and not scenario["probe"]:', "premise/listed-instances-without-probe-fail"),
    ("bounded-bare", '    if scenario["claim"] == "bounded" and _closed(scenario["named-set"]):',
     '    if scenario["claim"] == "bounded":', "matcher/bounded-without-named-set-rejects"),
    ("closed-needs-count", "    return named is not None and named[1] == len(named[0])",
     "    return named is not None and named[1] in (None, len(named[0]))", "matcher/bounded-open-set-rejects"),
    ("closed-count-ignored", "    return named is not None and named[1] == len(named[0])", "    return named is not None",
     "matcher/bounded-miscounted-set-rejects"),
    ("probe-ignored", '        if not scenario["probe"] >= frozenset(PROBE_PROPERTIES):', "        if False:",
     "matcher/wording-fix-without-probe-rejects"),
    ("probe-any-property", '        if not scenario["probe"] >= frozenset(PROBE_PROPERTIES):', '        if not scenario["probe"]:',
     "matcher/wording-fix-probe-missing-one-property-rejects"),
    ("probe-never-met", '        if not scenario["probe"] >= frozenset(PROBE_PROPERTIES):', "        if True:",
     "matcher/wording-fix-no-instances-with-probe-accepts"),
    ("strict-rows-ignored", '                and (row.vocab is not None or "instance:dispositioned" in row.effect)]',
     "                and row.vocab is not None]", "matcher/strict-row-stays-strict-beside-vocab-row"),
    ("named-set-keys", '    if not isinstance(value, dict) or set(value) - {"members", "count"} or "members" not in value:',
     '    if not isinstance(value, dict) or "members" not in value:', "schema/named-set-unknown-key-refused"),
    ("named-set-members", "    if not all(NAME_RE.fullmatch(member) for member in members):", "    if False:",
     "schema/named-set-member-malformed-refused"),
    ("named-set-count", "    if count is not None and (type(count) is not int or count < 1):", "    if False:",
     "schema/named-set-count-not-positive-refused"),
    ("exclusion-keys", '        if not isinstance(raw, dict) or set(raw) != {"clause-id", "row-sha256", "reason"}:',
     "        if not isinstance(raw, dict):", "schema/exclusion-unknown-key-refused"),
    ("exclusion-clause-id", '        if not isinstance(raw["clause-id"], str) or not CLAUSE_ID_RE.fullmatch(raw["clause-id"]):',
     "        if False:", "schema/exclusion-malformed-clause-id-refused"),
    ("exclusion-digest", '        _digest_field(raw["row-sha256"], where, "row-sha256", allow_absent=False)', "        pass",
     "schema/exclusion-malformed-digest-refused"),
    ("exclusion-reason", "        if not isinstance(reason, str) or not reason.strip():", "        if False:",
     "schema/exclusion-without-reason-refused"),
    ("exclusion-duplicate", "        if key in out:", "        if False:", "schema/exclusion-duplicate-refused"),
    ("uncovered-passes", '                report.fail("obligation row {} for {} is uncovered: no case lists {}; add a case '
     'that does, or "', "                " + _NOOP + '"obligation row {} for {} is uncovered: no case lists {}; add a '
     'case that does, or "', "ablation/uncovered-row-without-exclusion-fails"),
    ("exclusion-ignored", "            if key in exclusions:", "            if False:",
     "ablation/every-listed-clause-load-bearing"),
    ("exclusion-stale-unchecked", "    for clause, digest in sorted(set(exclusions) - matched):",
     "    for clause, digest in []:", "ablation/exclusion-naming-no-uncovered-row-fails"),
    ("diagnostic-identical", "    if base_out == head_out:", "    if False:", "binding/nondiscriminating-case-fails"),
    ("diagnostic-revert", "    if decision(base_out) == decision(head_out):", "    if True:",
     "binding/crossed-clause-decoy-fails"),
    ("range-last-only", "    for parent, commit in transitions:", "    for parent, commit in transitions[-1:]:",
     "binding/push-range-judges-every-commit"),
    ("range-against-before", "            code, text = evaluate(history.snapshot(commit), history.snapshot(parent), history)",
     "            code, text = evaluate(history.snapshot(commit), history.snapshot(base), history)",
     "binding/push-range-each-commit-against-its-parent"),
    ("range-chain-end", "        if expected != base:", "        if False:",
     "binding/push-range-base-off-first-parent-chain-exit-2"),
    ("range-root", "            if len(fields) < 2:", "            if False:",
     "binding/push-range-base-off-first-parent-chain-exit-2"),
    ("range-empty", "        if not lines:", "        if False:", "binding/push-range-empty-exit-2"),
    ("range-shallow", "        base = history.resolve(base_ref)\n        history.require_full_history()",
     "        base = history.resolve(base_ref)", "binding/shallow-history-exit-2"),
    ("shallow", '        if answer == "true":', "        if False:", "binding/shallow-history-exit-2"),
    ("option-ref", '        if not isinstance(ref, str) or not ref or ref.startswith("-"):',
     "        if not isinstance(ref, str) or not ref:", "binding/option-like-base-exit-2"),
    ("tree-entry-mode", '            if kind != "blob" or mode not in ("100644", "100755"):', "            if False:",
     "binding/base-tree-symlink-exit-2"),
    ("worktree-regular", "    if not stat.S_ISREG(mode):", "    if False:", "schema/worktree-symlink-input-exit-2"),
    ("worktree-entry", '                raise GateError("{}: not a regular file or directory".format(rel))', "                pass",
     "schema/worktree-symlink-entry-exit-2"),
    ("budget-values", "        if type(value) is not int or value < 0:", "        if False:",
     "schema/budget-negative-ceiling-exit-2"),
)
# Twins no single-check removal can fail, each with the reason.
UNMUTATED = {
    # it checks check_clauses.py itself: the fixture is valid under the clause gate, and the old shape is not
    "binding/remove-sole-clause-precondition-clause-gate": "exercises check_clauses.py, not this gate",
}


def _load_mutant(source, directory, number):
    """Write one mutant module to the scratch directory and load it with importlib."""
    path = Path(directory) / "rule_behaviour_mutant_{}.py".format(number)
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("rule_behaviour_mutant_{}".format(number), path)
    module = importlib.util.module_from_spec(spec)
    saved = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = saved
    return module


def _run_vector(function, gate):
    try:
        ok, detail = function(gate)
    except Exception as exc:  # a vector that crashes under a gate fails; it never passes
        return False, "crashed ({}: {})".format(type(exc).__name__, exc)
    return bool(ok), detail


def _registered_check_ids():
    """This suite's registered check ids, through the execution gate's own schema validator."""
    try:
        data = tomllib.loads((ROOT / REGISTRY_REL).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError, MemoryError) as exc:
        return None, "{}: {}".format(REGISTRY_REL, type(exc).__name__)
    with contextlib.redirect_stderr(io.StringIO()):
        rows = check_selftest_execution._manifest_rows(data, REGISTRY_REL)
    row = next((row for row in rows or () if row["id"] == SUITE_ID), None)
    if row is None:
        return None, "{} registers no valid suite {}".format(REGISTRY_REL, SUITE_ID)
    return set(row["expected-check-ids"]), None


def self_test(report_path=None):
    results, executed = [], []
    if report_path is not None:
        # The execution report is finalized at interpreter exit, after this run's cleanup
        # (tools/_selftest_exit_report.py); nothing writes it in band.
        _selftest_exit_report.arm(report_path, SUITE_ID, executed)

    def check(check_id, ok, detail=""):
        results.append((check_id, bool(ok), detail))
        executed.append(check_id)

    from _git_fixture_env import fixture_git_lifecycle
    this = sys.modules[__name__]
    gate_source, marker, rest = Path(__file__).read_text(encoding="utf-8").partition("# SELF-TEST-BEGINS")
    exposed, uncaught = set(), []
    with fixture_git_lifecycle():
        functions = dict((name, function) for name, function, _git in VECTORS)
        ran = []
        for (name,) in (
                ("schema/obligation-unknown-key-refused",),
                ("schema/obligation-unknown-predicate-refused",),
                ("schema/obligation-unknown-atom-refused",),
                ("schema/limit-cancels-non-exempt-refused",),
                ("schema/case-unknown-fact-refused",),
                ("schema/instance-unknown-where-refused",),
                ("schema/duplicate-case-id-refused",),
                ("schema/stale-text-sha-fails",),
                ("schema/clause-missing-from-register-fails",),
                ("schema/malformed-toml-exit-2",),
                ("schema/control-delta-mismatch-refused",),
                ("schema/prior-table-refused",),
                ("schema/expect-base-on-control-refused",),
                ("schema/case-empty-clauses-and-empty-removes-refused",),
                ("schema/case-empty-clauses-with-removes-accepted",),
                ("schema/removes-id-in-head-register-fails",),
                ("schema/removes-id-absent-at-base-fails",),
                ("schema/removes-on-control-refused",),
                ("schema/instance-missing-form-refused",),
                ("schema/instance-unknown-form-refused",),
                ("schema/exempt-on-label-outside-vocab-refused",),
                ("schema/exempt-vocab-label-without-surface-refused",),
                ("schema/exempt-on-unknown-where-refused",),
                ("schema/exempt-on-duplicate-pair-refused",),
                ("schema/exempt-vocab-without-exempt-on-refused",),
                ("schema/obligation-kind-not-a-string-exit-2",),
                ("schema/exempt-on-label-not-a-string-exit-2",),
                ("schema/named-set-unknown-key-refused",),
                ("schema/named-set-member-malformed-refused",),
                ("schema/named-set-count-not-positive-refused",),
                ("schema/probe-unknown-property-refused",),
                ("schema/exclusion-unknown-key-refused",),
                ("schema/exclusion-malformed-clause-id-refused",),
                ("schema/exclusion-malformed-digest-refused",),
                ("schema/exclusion-without-reason-refused",),
                ("schema/exclusion-duplicate-refused",),
                ("schema/budget-negative-ceiling-exit-2",),
                ("schema/worktree-symlink-input-exit-2",),
                ("schema/worktree-symlink-entry-exit-2",),
                ("premise/bounded-without-named-set-fails",),
                ("premise/bounded-with-named-set-passes",),
                ("premise/wording-fix-without-probe-fails",),
                ("premise/wording-fix-with-probe-passes",),
                ("premise/listed-instances-without-probe-fail",),
                ("matcher/row-order-independent",),
                ("matcher/exemption-eligibility-matrix",),
                ("matcher/exempt-on-intersection-across-rows",),
                ("matcher/decision-of-outcome",),
                ("matcher/retire-in-place-head-rejects",),
                ("matcher/retire-own-evidence-accepts",),
                ("matcher/merge-bounded-head-rejects",),
                ("matcher/merge-own-evidence-accepts",),
                ("matcher/bounded-named-set-accepts",),
                ("matcher/completion-named-set-rejects",),
                ("matcher/wording-register-copy-head-rejects",),
                ("matcher/wording-register-copy-fixed-accepts",),
                ("matcher/bounded-derives-completion",),
                ("matcher/bounded-without-named-set-rejects",),
                ("matcher/bounded-open-set-rejects",),
                ("matcher/bounded-miscounted-set-rejects",),
                ("matcher/wording-fix-without-probe-rejects",),
                ("matcher/wording-fix-probe-missing-one-property-rejects",),
                ("matcher/wording-fix-no-instances-with-probe-accepts",),
                ("matcher/strict-row-stays-strict-beside-vocab-row",),
                ("matcher/never-exempt-overrides-exempt-on-pair-rejects",),
                ("matcher/rule-quotation-exempt-history-rejects",),
                ("matcher/rule-operative-exempt-warning-rejects",),
                ("matcher/register-copy-quotation-exempt-history-rejects",),
                ("matcher/changelog-quote-operative-exempt-history-rejects",),
                ("matcher/correction-note-quotation-exempt-history-rejects",),
                ("matcher/changelog-quote-quotation-exempt-warning-rejects",),
                ("matcher/mistakes-register-quotation-exempt-warning-rejects",),
                ("matcher/exempt-correction-label-at-head-rejects",),
                ("matcher/changelog-quote-quotation-exempt-history-accepts",),
                ("matcher/correction-note-quotation-exempt-warning-accepts",),
                ("ablation/listed-clause-not-load-bearing-fails",),
                ("ablation/dead-row-fails",),
                ("ablation/every-listed-clause-load-bearing",),
                ("ablation/uncovered-row-without-exclusion-fails",),
                ("ablation/exclusion-naming-no-uncovered-row-fails",),
                ("ablation/exclusion-for-changed-row-fails",),
                ("ablation/empty-clauses-removal-case-vacuous",),
                ("ablation/atom-only-change-counts-for-coverage-not-binding",),
                ("cases/expect-head-mismatch-fails",),
                ("cases/control-equal-outcome-fails",),
                ("change/same-line-insert-after-setcmp6-must-register",),
                ("change/same-line-insert-registered-binds",),
                ("change/span-interior-edit-marks-clause",),
                ("change/insert-at-span-start-goes-to-U",),
                ("change/insert-one-after-span-start-marks-clause",),
                ("change/insert-one-before-span-end-marks-clause",),
                ("change/insert-at-span-end-goes-to-U",),
                ("change/replace-last-byte-before-span-goes-to-U",),
                ("change/replace-first-span-byte-marks-clause",),
                ("change/replace-last-span-byte-marks-clause",),
                ("change/replace-first-byte-after-span-goes-to-U",),
                ("change/unregistered-prose-removal-fails",),
                ("change/new-rule-file-prose-must-register",),
                ("change/line-shift-only-not-a-change",),
                ("change/removed-clause-in-change-set",),
                ("change/registration-only-goes-to-N",),
                ("change/one-replace-opcode-spans-clause-and-unregistered-prose-fails",),
                ("change/one-replace-opcode-prose-registered-binds",),
                ("change/range-across-two-clauses-gap-residue",),
                ("change/residue-whitespace-tile-passes",),
                ("change/residue-range-starts-one-before-span-reports-one-byte",),
                ("change/residue-range-starts-at-span-start-reports-none",),
                ("change/residue-range-ends-at-span-end-reports-none",),
                ("change/residue-range-ends-one-past-span-end-reports-one-byte",),
                ("change/register-only-window-to-other-occurrence-marks-clause",),
                ("change/register-only-source-path-change-marks-clause",),
                ("change/rule-file-rename-marks-every-clause",),
                ("change/anchor-insert-at-span-start-maps-not-moved",),
                ("change/anchor-insert-one-after-span-start-touches",),
                ("change/anchor-insert-at-span-end-maps-not-moved",),
                ("change/anchor-insert-one-before-span-end-touches",),
                ("change/source-digest-only-change-not-anchor-move",),
                ("change/anchor-move-with-text-change-in-C-once",),
                ("change/deletion-inside-new-span-marks-clause",),
                ("change/window-bounds-move-marks-clause",),
                ("binding/changed-clause-no-case-fails",),
                ("binding/unchanged-case-does-not-count",),
                ("binding/nondiscriminating-case-fails",),
                ("binding/discriminating-case-passes",),
                ("binding/crossed-clause-decoy-fails",),
                ("binding/crossed-clause-decoy-separate-case-passes",),
                ("binding/listed-wrong-clause-fails",),
                ("binding/joint-cause-binds-both",),
                ("binding/unencoded-at-base-edit-not-bindable",),
                ("binding/encoding-first-then-edit-binds",),
                ("binding/new-clause-over-existing-prose-not-bindable",),
                ("binding/wholly-new-clause-binds-against-absent",),
                ("binding/new-span-first-byte-unchanged-not-bindable",),
                ("binding/new-span-starts-at-changed-range-start-binds",),
                ("binding/new-span-ends-at-changed-range-end-binds",),
                ("binding/new-span-last-byte-unchanged-not-bindable",),
                ("binding/removed-clause-flip-case-passes",),
                ("binding/removed-clause-without-case-fails",),
                ("binding/registration-only-needs-baseline-case",),
                ("binding/genesis-empty-change-passes",),
                ("binding/genesis-with-rule-edit-fails",),
                ("binding/base-gate-without-table-exit-2",),
                ("binding/reject-to-reject-atom-change-does-not-bind",),
                ("binding/decision-flip-binds",),
                ("binding/accept-to-accept-does-not-bind",),
                ("binding/revert-flips-atoms-only-fails",),
                ("binding/revert-flips-atoms-only-separate-cases-pass",),
                ("binding/bound-report-prints-atom-diagnostics",),
                ("binding/remove-sole-clause-with-rule-file-binds",),
                ("binding/remove-sole-clause-precondition-clause-gate",),
                ("binding/remove-sole-clause-nondiscriminating-fails",),
                ("binding/remove-sole-clause-file-residue-over-ceiling-fails",),
                ("binding/remove-sole-clause-file-untiled-lf-fails",),
                ("binding/base-unresolvable-exit-2",),
                ("binding/option-like-base-exit-2",),
                ("binding/shallow-history-exit-2",),
                ("binding/base-tree-symlink-exit-2",),
                ("binding/push-range-judges-every-commit",),
                ("binding/push-range-each-commit-against-its-parent",),
                ("binding/push-range-base-off-first-parent-chain-exit-2",),
                ("binding/push-range-empty-exit-2",),
                ("binding/push-range-broken-first-parent-chain-exit-2",),
                ("waiver/reported-separately-not-bound",),
                ("waiver/digest-mismatch-fails",),
                ("waiver/unknown-kind-refused",),
                ("waiver/untestable-kind-refused",),
                ("waiver/retired-kind-refused",),
                ("waiver/kind-not-a-string-exit-2",),
                ("waiver/wording-only-encoding-changed-fails",),
                ("waiver/wording-only-on-unencoded-clause-refused",),
                ("waiver/unencoded-on-encoded-clause-refused",),
                ("waiver/unencoded-on-bindable-new-clause-refused",),
                ("waiver/carried-row-stale-discharges-nothing",),
                ("waiver/live-row-matching-nothing-fails",),
                ("waiver/non-clause-text-prose-range-refused",),
                ("waiver/non-clause-text-heading-range-passes",),
                ("waiver/heading-tile-hash-without-space-refused",),
                ("waiver/heading-tile-seven-hashes-refused",),
                ("waiver/heading-tile-six-hashes-passes",),
                ("waiver/heading-tile-bare-hash-line-passes",),
                ("waiver/heading-tile-ends-at-line-end-passes",),
                ("waiver/heading-tile-ends-one-past-line-end-refused",),
                ("waiver/non-clause-text-whitespace-range-passes",),
                ("waiver/non-clause-text-tiles-exactly",),
                ("waiver/non-clause-text-gap-fails",),
                ("waiver/non-clause-text-overlap-fails",),
                ("waiver/range-bytes-digest-mismatch-fails",),
                ("waiver/frontmatter-tile-ends-one-before-block-end-passes",),
                ("waiver/frontmatter-tile-ends-at-block-end-passes",),
                ("waiver/frontmatter-tile-ends-one-past-block-end-refused",),
                ("waiver/per-change-ceiling-from-base",),
                ("waiver/ceiling-raised-in-same-change-not-used",),
                ("waiver/budget-change-with-new-waiver-fails",),
                ("waiver/no-base-budget-means-zero",),
                ("waiver/tile-covering-clause-bytes-refused",),
                ("waiver/carried-row-reason-changed-fails",),
                ("waiver/carried-row-digest-changed-fails",),
                ("waiver/carried-row-deleted-fails",),
                ("waiver/carried-rows-reordered-fails",),
                ("waiver/row-inserted-before-carried-rows-fails",),
                ("waiver/appended-row-live-in-its-change-only",),
                ("waiver/revert-reapply-carried-row-does-not-discharge",),
                ("waiver/repeat-key-without-repeats-fails",),
                ("waiver/repeat-key-with-repeats-passes-and-reported",),
                ("waiver/repeats-naming-wrong-row-fails",),
                ("waiver/repeats-naming-missing-row-fails",),
                ("waiver/duplicate-key-within-one-change-fails",),
                ("waiver/duplicate-id-fails",),
                ("waiver/missing-id-refused",),
                ("waiver/anchor-only-passes-for-moved-anchor",),
                ("waiver/anchor-only-anchor-digest-mismatch-fails",),
                ("waiver/anchor-only-on-text-change-refused",),
                ("waiver/anchor-only-with-changed-rows-refused",),
                ("waiver/anchor-only-ceiling-zero-fails",),
                ("waiver/wording-only-anchor-digest-mismatch-fails",),
                ("waiver/wording-only-on-unchanged-text-anchor-move-refused",),
                ("inventory/reported-not-waiver",),
                ("inventory/discharges-nothing",),
                ("retro/pinned-text-verified",),
                ("retro/span-anchor-unique-in-pinned-blob",),
                ("retro/history-missing-exit-2",),
                ("retro/never-read-by-binding",),
                ("retro/no-attributed-cause-fails",),
        ):
            ran.append(name)
            ok, detail = _run_vector(functions[name], this) if name in functions else (False, "no vector")
            check(name, ok, detail)
        named = dict((name, (function, git)) for name, function, git in VECTORS)
        with tempfile.TemporaryDirectory(prefix="rule-behaviour-mutants-") as directory:
            for number, (mutant, old, new, vector) in enumerate(MUTANTS, 1):
                if gate_source.count(old) != 1 or vector not in named:
                    uncaught.append((mutant, "anchor found {} times".format(gate_source.count(old)), vector))
                    continue
                module = _load_mutant(gate_source.replace(old, new) + marker + rest, directory, number)
                failed = set(name for name, function, git in VECTORS
                             if (not git or name == vector) and not _run_vector(function, module)[0])
                exposed |= failed
                if vector not in failed:
                    uncaught.append((mutant, vector))
    check("mutant/every-mutant-caught", MUTANTS and not uncaught, uncaught)
    never = sorted(set(named) - exposed - set(UNMUTATED))
    check("mutant/every-vector-discriminates", not never, never)

    # the live leg: the real tree passes, in process and through this file's own entry
    code, text = evaluate(worktree_snapshot(ROOT), None, GitHistory(ROOT))
    check("live/real-tree-passes", code == 0 and "PASS: rule-behaviour checked" in text, text[-2000:])
    try:
        launched = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__))], capture_output=True, text=True,
                                  timeout=CHILD_TIMEOUT, cwd=str(ROOT), env=dict(LC_ALL="C.UTF-8"))
        direct = (launched.returncode, launched.stdout.splitlines()[-1:] if launched.stdout else [], launched.stderr)
    except (OSError, subprocess.SubprocessError) as exc:
        direct = ("not launched", type(exc).__name__, "")
    check("live/direct-launch-passes", direct[0] == 0 and direct[1] and direct[1][0].startswith(
        "PASS: rule-behaviour checked") and direct[2] == "", direct)

    expected, problem = _registered_check_ids()
    harness = []
    if sorted(ran) != sorted(functions):
        harness.append(("vector-table/literal", dict(unlisted=sorted(set(functions) - set(ran)),
                                                    stray=sorted(set(ran) - set(functions)))))
    if problem is not None:
        harness.append(("execution-set/registry", problem))
    elif len(set(executed)) != len(executed) or set(executed) != expected:
        harness.append(("execution-set/reconciled", dict(missing=sorted(expected - set(executed)),
                                                         extra=sorted(set(executed) - expected))))
    failed = [(check_id, detail) for check_id, ok, detail in results if not ok] + harness
    if failed:
        print("SELF-TEST FAIL: rule-behaviour {} of {} check(s) failed".format(len(failed), len(results)))
        for check_id, detail in failed:
            print("  {}: {!r}".format(check_id, detail)[:4000])
        return 1
    print("SELF-TEST PASS: rule-behaviour {} checks ({} vectors over the schema, premise, matcher, ablation, change, "
          "binding, push, waiver, inventory and retro legs; {} mutants each caught, every vector but {} twin "
          "discriminating; the real tree passes in process and on direct launch)".format(
              len(results), len(VECTORS), len(MUTANTS), len(UNMUTATED)))
    return 0


def main(argv=None):
    """Run the selected mode and leave through _selftest_exit_report.exit_with, which the execution report
    requires; the entry line calls this only after the child-status guard."""
    _selftest_exit_report.exit_with(_dispatch(argv))


def _dispatch(argv):
    args = sys.argv[1:] if argv is None else argv
    if (len(args) in (2, 3) and args[-2] == "--execution-report" and os.path.isabs(args[-1])
            and args[:-2] in ([], ["--self-test"])):
        return self_test(args[-1])
    if args == ["--self-test"]:
        return self_test()
    if not args:
        return run(ROOT)
    if len(args) == 2 and args[0] == "--base":
        return run(ROOT, args[1])
    if len(args) == 3 and args[0] == "--base" and args[2] == "--each-commit":
        return run_each_commit(ROOT, args[1])
    print("usage: check_rule_behaviour.py [--base REF [--each-commit]] | [--self-test] [--execution-report "
          "ABS_PATH]", file=sys.stderr)
    return 2


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. The self-test reads child exit statuses
# (git, its own direct launch and check_clauses.py), so under an ignored SIGCHLD a failing child would read
# as a pass; the guard runs at the entry line, before main() dispatches any mode.
# The status the probe child exits with; any value other than 0 and the refusal exit would do.
_CHILD_STATUS_PROBE_CODE = 77


def _child_status_refusal():
    """None when this process can read a child's exit status, otherwise the reason it cannot."""
    import os
    import signal
    if not hasattr(signal, "SIGCHLD"):
        return None
    disposition = signal.getsignal(signal.SIGCHLD)
    if disposition is not signal.SIG_DFL:
        return ("SIGCHLD is ignored (SIG_IGN) in this process" if disposition is signal.SIG_IGN
                else "SIGCHLD is not at its default disposition in this process")
    if not hasattr(os, "fork"):
        return None
    try:
        pid = os.fork()
    except OSError as exc:
        return "the child-status probe could not fork ({})".format(type(exc).__name__)
    if pid == 0:
        os._exit(_CHILD_STATUS_PROBE_CODE)
    try:
        waited, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return "the probe child was reaped before its exit status could be read (ECHILD)"
    except OSError as exc:
        return "the probe child's exit status could not be read ({})".format(type(exc).__name__)
    if waited != pid or os.waitstatus_to_exitcode(status) != _CHILD_STATUS_PROBE_CODE:
        return "a probe child that exited {} was read back as wait status {}".format(
            _CHILD_STATUS_PROBE_CODE, status)
    return None


def _child_status_refusal_exit(name):
    """0 when a child's exit status is observable; otherwise write the named refusal and exit 2 at once
    (os._exit, so no exit-time stream flush can replace the status)."""
    import os
    reason = _child_status_refusal()
    if reason is None:
        return 0
    try:
        sys.stderr.write("error: {} refuses to run: {}, so a failed child would read as a success. Nothing "
                         "was run (cannot evaluate). Start it from a parent that leaves SIGCHLD at "
                         "SIG_DFL.\n".format(name, reason))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)


if __name__ == "__main__":
    sys.exit(_child_status_refusal_exit("check_rule_behaviour.py") or main())
