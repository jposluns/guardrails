"""The must-deliver list .aiqt/core/must-deliver.toml: its one strict reader, shared by every tool that
reads it. Stdlib only, plus gen_rules.CID_RE.

Requires Python 3.14 or newer, the floor in .aiqt/core/python-floor.toml. No __main__ and no side effect at
import: tools/check_must_deliver.py (the prefix gate and its list ratchet), tools/gen_agents.py (the
AGENTS.md order: the apex, then the listed rules, then the rest, each in AIQT priority order),
tools/gen_first_pin_demo.py (the span algorithm, which composes in that same order) and
tools/conformance.py (C2, which judges an installed AGENTS.md against that order) import it. It imports
none of them, so no import cycle can form.

The declared file carries exactly format-version, warn-margin-bytes and [[rule]] rows of exactly id and
reason, and optionally [[removal]] rows of exactly id and reason. An absent, unreadable, symlinked,
non-UTF-8, malformed or EMPTY list is a MustDeliverError (a ValueError, so every caller maps it to exit 2).
listed_ids adds the corpus membership check: a listed id that is not a corpus-id in the loaded corpus is an
UnknownListedIdError (a MustDeliverError carrying the ids), which conformance tells apart by its class,
never by its message text. Removal ids are history and are not checked against the corpus.
"""
import os
import stat
import tomllib

from gen_rules import CID_RE

DECL_REL = ".aiqt/core/must-deliver.toml"
TOP_KEYS = frozenset({"format-version", "warn-margin-bytes", "rule"})
OPTIONAL_TOP_KEYS = frozenset({"removal"})
ROW_KEYS = frozenset({"id", "reason"})
REMOVAL_KEYS = frozenset({"id", "reason"})


class MustDeliverError(ValueError):
    """The must-deliver list cannot be read or is outside its exact schema; the caller maps it to exit 2."""


class UnknownListedIdError(MustDeliverError):
    """The list parses but names ids that are not corpus-ids in the loaded corpus (raised only by
    listed_ids' membership check). .ids holds those ids in list order."""

    def __init__(self, message, ids):
        super().__init__(message)
        self.ids = tuple(ids)


def _read_decl_bytes(path):
    """The one read of the declaration's bytes, a module attribute so a self-test can inject a
    PermissionError here on every runner, including one where a mode-000 file stays readable."""
    return path.read_bytes()


def read_declared(path):
    """The validated must-deliver list: ([rule ids], warn_margin_bytes, {removed id: reason}). Absent, a
    symlink or non-regular entry, unreadable, not UTF-8, not TOML, or outside the exact schema is a
    MustDeliverError (exit 2): the list is the gate's whole policy input, so nothing else may stand in for it."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise MustDeliverError("{} does not exist; the must-deliver policy cannot be read".format(DECL_REL))
    except OSError as exc:
        raise MustDeliverError("cannot stat {} ({})".format(DECL_REL, exc))
    if not stat.S_ISREG(st.st_mode):
        raise MustDeliverError("{} is not a regular file (a symlink, directory or special entry)".format(DECL_REL))
    try:
        raw = _read_decl_bytes(path)
    except OSError as exc:
        raise MustDeliverError("{} is unreadable ({})".format(DECL_REL, exc))
    return parse_declared(raw, DECL_REL)


def parse_declared(raw, label):
    """The one strict parser of the declaration's bytes, shared by the working file and the file at the
    comparison base (label names which in every message). Returns ([rule ids], warn_margin_bytes,
    {removed id: reason}); anything outside the exact schema is a MustDeliverError (exit 2)."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MustDeliverError("{} is not valid UTF-8 ({})".format(label, exc))
    try:
        data = tomllib.loads(text)
    # ValueError and RecursionError too: tomllib raises a BARE ValueError (not TOMLDecodeError) on an
    # integer literal past CPython's 4300-digit int-string limit, and a RecursionError (a RuntimeError)
    # on a deeply nested array or inline table; neither is a TOMLDecodeError.
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise MustDeliverError("{} does not parse ({})".format(label, exc))
    if not TOP_KEYS <= set(data) or not set(data) <= TOP_KEYS | OPTIONAL_TOP_KEYS:
        raise MustDeliverError("{} must carry exactly the keys {} (and optionally {}), found {}".format(
            label, sorted(TOP_KEYS), sorted(OPTIONAL_TOP_KEYS), sorted(data)))
    version = data["format-version"]
    # The type is checked before the value: TOML 1.0 parses to a float that compares equal to 1, and a
    # bool is an int subclass.
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise MustDeliverError("{}: format-version must be the integer 1, found {!r}".format(label, version))
    warn = data["warn-margin-bytes"]
    if isinstance(warn, bool) or not isinstance(warn, int) or warn < 1:
        raise MustDeliverError("{}: warn-margin-bytes must be a positive integer, found {!r}".format(label, warn))
    rows = data["rule"]
    if not isinstance(rows, list) or not rows:
        raise MustDeliverError("{}: rule must be a non-empty array of [[rule]] tables; an empty list is a "
                               "failure, never \"nothing to check\"".format(label))
    ids = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != ROW_KEYS:
            raise MustDeliverError("{}: each [[rule]] row must carry exactly the keys {}, found {!r}".format(
                label, sorted(ROW_KEYS), row))
        rid, reason = row["id"], row["reason"]
        if not isinstance(rid, str) or not CID_RE.fullmatch(rid):
            raise MustDeliverError("{}: id {!r} is not a well-formed corpus-id".format(label, rid))
        if not isinstance(reason, str) or not reason.strip():
            raise MustDeliverError("{}: rule {} must carry a non-empty reason string".format(label, rid))
        if rid in ids:
            raise MustDeliverError("{}: rule {} is listed more than once".format(label, rid))
        ids.append(rid)
    removals = {}
    if "removal" in data:
        removal_rows = data["removal"]
        if not isinstance(removal_rows, list) or not removal_rows:
            raise MustDeliverError("{}: removal must be a non-empty array of [[removal]] tables (omit the key "
                                   "when no removal is recorded)".format(label))
        for row in removal_rows:
            if not isinstance(row, dict) or set(row) != REMOVAL_KEYS:
                raise MustDeliverError("{}: each [[removal]] row must carry exactly the keys {}, found {!r}".format(
                    label, sorted(REMOVAL_KEYS), row))
            rid, reason = row["id"], row["reason"]
            if not isinstance(rid, str) or not CID_RE.fullmatch(rid):
                raise MustDeliverError("{}: removal id {!r} is not a well-formed corpus-id".format(label, rid))
            if not isinstance(reason, str) or not reason.strip():
                raise MustDeliverError("{}: removal {} must carry a non-empty reason string".format(label, rid))
            if rid in removals:
                raise MustDeliverError("{}: removal {} is recorded more than once".format(label, rid))
            if rid in ids:
                raise MustDeliverError("{}: removal {} is also listed in [[rule]]; a rule is either listed or "
                                       "removed".format(label, rid))
            removals[rid] = reason
    return ids, warn, removals


def listed_ids(decl_path, corpus_ids):
    """The listed ids of the declaration at decl_path (read_declared), each required to be a corpus-id in
    corpus_ids, the ids of the loaded corpus. A listed id outside it is an UnknownListedIdError naming every
    such id; removal ids are history and are not checked. Returns the listed ids in list order."""
    ids, _warn, _removals = read_declared(decl_path)
    known = set(corpus_ids)
    unknown = [rid for rid in ids if rid not in known]
    if unknown:
        raise UnknownListedIdError("{} lists {}, not a corpus-id in the rule corpus (renamed, removed, or never "
                                   "a corpus-id)".format(DECL_REL, ", ".join(unknown)), unknown)
    return ids


def fixture_text(ids):
    """A schema-valid declaration listing ids, for the self-test fixtures of the tools that read the list
    (one writer, so a later schema change moves every fixture list at once)."""
    lines = ["format-version = 1", "warn-margin-bytes = 512"]
    for rid in ids:
        lines += ["", "[[rule]]", 'id = "{}"'.format(rid), 'reason = "fixture rule"']
    return "\n".join(lines) + "\n"
