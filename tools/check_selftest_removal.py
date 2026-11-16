#!/usr/bin/env python3
"""Self-test check removal gate (selftest-removal): a change that removes a registered self-test check id
lands only with that id's coverage accounted for in the same change.

A removed test case cannot fail, so a run of the suite after the removal is green whether or not the case
still mattered. This gate reads the expected-check-ids lists of tools/selftest_checks.toml at the change's
comparison base and at its head, and refuses (exit 1) a NET REMOVAL: a (suite id, check id) pair registered
at the base and not at the head, unless the accounting record .aiqt/check-removals.toml at the head names that
pair in one of the three states of the gate-discipline rule, in an entry this change wrote:

  schema = 1
  [[removal]]
  suite = "<suite id>"
  id = "<removed check id>"
  state = "covered"            covered-by-suite and covered-by-id name a check id registered at the head
  state = "unreachable"        capability-removed (the change that removes what the case depends on) and
                               observed (the attempt that confirms the path is gone), each non-blank
  state = "disclosed"          authorization (the maintainer's recorded authorization reference), in the
                               form D-<number> (<YYYY-MM-DD>): the decision number, 1 to 6 digits, one space,
                               and the decision's calendar date in parentheses, for example D-417 (2026-10-04)

SCOPE. The unit this gate reads is a REGISTERED CHECK ID: a (suite id, check id) pair in the expected-check-ids
list of a [[suite]] row of tools/selftest_checks.toml, with that row's runner and args. It covers the removal of
such a pair, by deleting the id, renaming it, or deleting or renaming its whole suite row. It does NOT cover a
self-test case that is not a registered check id: a test function, a subtest, an assertion or a fixture row
inside a runner that no registered id names, a test file that no suite row runs, or the code behind an id that
stays registered (tools/check_selftest_execution.py reconciles the ids a runner executes with the registry; this
gate reads the registry only, plus the runner text a move compares).

A MOVE is not a removal: a removed pair whose check id is newly registered at the head in another suite (a
pair the base did not hold) with the SAME LAUNCH and the SAME BODY, where the body is not GENERIC, is reported
as moved and needs no entry. The launch of a suite is its registered runner path and its args list (no args
key reads as an empty list): the old suite's launch at the base and the new suite's at the head must be
equal, so a move into a suite with another runner, or with the same runner and other args, is a removal (the
same check() statement in another program, such as the refuse/unrecognized-argument boilerplate each runner
holds, does not run the same case). The body of a check id is the AST dump, without positions, of the
innermost statement holding its direct check() call in that runner (with the loop row that names the id, for
an id a for loop binds), read at the base for the old suite and at the head for the new one, from the commit
trees. A body is generic when the same check id has the identical body in the runner of any other suite
whose runner path differs, among the suites registering that id at the base (runner read at the base) or at
the head (runner read at the head): such a body is boilerplate, not evidence that the case moved. A runner
that is absent, not a plain relative path, unreadable, not UTF-8 or unparsable, an args value that is not a
list of strings, and an id named by no call site or by more than one, give no launch or no body, so the pair
counts as removed. Each new registration absorbs one removed pair, so an id removed from two suites and added
to one is one move and one removal, and an id that the other suite already held at the base is not a move. A
move needs no entry because the moved id stays registered with the case it named, so
tools/check_selftest_execution.py still requires its new suite to run it. A removal that is not a move is
accounted for like any other (a covered entry naming the new pair is the usual one).

An entry accounts for a removal only when it is FRESH: the base record holds no entry for the pair, or holds
one whose fields differ from the head's after each value is whitespace-collapsed and case-folded, so a
whitespace or letter-case edit of an old entry is not a new accounting. A covered-by pair that is not
registered at the head (removed in this change too, or never registered) covers nothing, and a disclosed
entry whose authorization is not in the form above discloses nothing. Each of those is a finding (exit 1),
and so is a removed pair with no entry, or one whose entry is unchanged from the base.

The record is judged on the HEAD side: the head record must be well-formed (below), while the base record is
read only to tell a fresh entry from an old one. A base record that cannot be read (not UTF-8, not TOML, or
not the shape above) holds no entry, so every head entry reads as fresh and a change that repairs a
malformed record is not blocked by the record it repairs (an INFO line names this).

THE BASE is the one tools/check_release_cut.py derives (its context(), imported, not copied): in a
pull_request run (GITHUB_BASE_REF set) the merge HEAD's raw first parent, which must be the resolved
origin/GITHUB_BASE_REF tip; on a push, --protected origin/<branch> and --base <the event's before> (the
pushed range from before to HEAD); on a workflow_dispatch or schedule run, --protected alone (HEAD against
its first parent); locally, the unique merge base of HEAD and origin/HEAD's target. Every refusal of that
derivation (no origin/HEAD, a shallow or grafted history, more than one merge base, HEAD behind the target,
an unsupported event) is CANNOT_EVALUATE here too. The registry and the record are read from the base and
head commit trees through the same module's tree_blob (exact path components; a symlink, tree or gitlink
in their place is refused), never from the index or the working tree.

Exit 0: no net removal, or every removed pair moved or accounted for (each such pair is printed).
Exit 1: an unaccounted removed pair, a stale entry, a covered-by pair that does not survive, or a disclosed
entry whose authorization is not in the stated form.
Exit 2 (CANNOT_EVALUATE): the base cannot be derived or read, a git read fails, the registry is absent at
the head while the base holds it (absent at both ends, nothing is removed), either registry or the head
record is not UTF-8, not TOML, or not the shape above (an unknown key or state, a missing or blank field, a
repeated pair, a format-version other than 1). Never a pass.

  check_selftest_removal.py [--root DIR] [--protected REF] [--base OID]
  check_selftest_removal.py --self-test [--execution-report ABS_PATH]

RESIDUALS: it reads registered ids, not test code, so a case kept in the registry while its body is
weakened, a check whose assertion is emptied, a test function or subtest that no registered id names, and a
test file outside the registry are not seen; an id
renamed in the registry reads as a removal and an addition, so it needs an entry (covered by the new id);
the record proves that an accounting was written in a checkable form, never that it is true: the covered-by
pair is checked to be registered at the head, not to catch what the removed case did; the authorization's
form is checked, but the decision it names is not looked up; and the unreachable fields are free text;
freshness proves the entry was rewritten beyond whitespace and letter case, not that the rewrite is a new
accounting (a reworded old entry reads as fresh; a disclosed entry, whose one field has a fixed form, must
name another decision or date), and the form admits D-0 and a future date; a malformed base record makes
every head entry read as fresh; a move is judged by the id, its suite's launch (runner path and args) and its
check() statement only, so a moved case whose call is unchanged while the runner's helpers or fixtures it calls
changed reads as a move, and a boilerplate body is caught as generic only when another registered runner
holds it for the same id (a runner rewritten in place under the same path, keeping a boilerplate statement
that no other runner holds, reads as a move); the generic test fails toward a removal, so a suite rename
whose runner shares a boilerplate id with another runner needs an entry for that id; the comparison is of
the two endpoints, so a removal and re-addition inside the range is not seen; and, like its base
derivation, it trusts the git binary, the repository configuration and origin/HEAD.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_selftest_removal.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import contextlib
import datetime
import io
import os
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_release_cut as cut  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = "tools/selftest_checks.toml"
RECORD = ".aiqt/check-removals.toml"
STATES = {
    "covered": ("covered-by-suite", "covered-by-id"),
    "unreachable": ("capability-removed", "observed"),
    "disclosed": ("authorization",),
}
ENTRY_KEYS = frozenset(("suite", "id", "state")) | frozenset(key for keys in STATES.values() for key in keys)
# the stated form of a disclosed entry's authorization reference
AUTHORIZATION_FORM = "D-<number> (<YYYY-MM-DD>)"
AUTHORIZATION_DIGITS = 6
EXIT_OK, EXIT_REFUSE, EXIT_CANNOT_EVALUATE = 0, 1, 2


class CannotEvaluate(ValueError):
    """An input that cannot be judged: an underivable base, a failed git read, or a malformed file."""


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def parse_toml(raw, label):
    """The decoded TOML document of raw bytes, or CannotEvaluate (never a partial read)."""
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError, MemoryError, ValueError) as exc:
        raise CannotEvaluate("{}: not UTF-8 TOML ({})".format(label, type(exc).__name__)) from None


def load_registry(raw, label):
    """The set of (suite id, check id) pairs a registry's bytes hold, or CannotEvaluate."""
    if raw is None:
        raise CannotEvaluate("{}: {} is absent".format(label, REGISTRY))
    document = parse_toml(raw, label)
    if document.get("format-version") != 1 or isinstance(document.get("format-version"), bool):
        raise CannotEvaluate("{}: format-version must be the integer 1".format(label))
    suites = document.get("suite")
    if not isinstance(suites, list) or not suites:
        raise CannotEvaluate("{}: no [[suite]] rows".format(label))
    pairs = set()
    for row in suites:
        if not isinstance(row, dict) or not _text(row.get("id")) or not isinstance(
                row.get("expected-check-ids"), list):
            raise CannotEvaluate("{}: a [[suite]] row without an id and an expected-check-ids list".format(label))
        for check_id in row["expected-check-ids"]:
            if not _text(check_id):
                raise CannotEvaluate("{}: suite {!r} lists a check id that is not a string".format(label, row["id"]))
            pairs.add((row["id"], check_id))
    return pairs


def load_record(raw, label):
    """{(suite id, check id): entry} for an accounting record's bytes ({} when absent), or CannotEvaluate."""
    if raw is None:
        return {}
    document = parse_toml(raw, label)
    if set(document) - {"schema", "removal"} or document.get("schema") != 1 or isinstance(
            document.get("schema"), bool):
        raise CannotEvaluate("{}: the record holds schema = 1 and [[removal]] entries only".format(label))
    rows = document.get("removal", [])
    if not isinstance(rows, list):
        raise CannotEvaluate("{}: removal is not an array of tables".format(label))
    entries = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) - ENTRY_KEYS:
            raise CannotEvaluate("{}: a [[removal]] entry with an unknown key".format(label))
        state = row.get("state")
        if not _text(row.get("suite")) or not _text(row.get("id")) or state not in STATES:
            raise CannotEvaluate("{}: a [[removal]] entry needs suite, id and a state of {}".format(
                label, ", ".join(STATES)))
        if set(row) - {"suite", "id", "state"} != set(STATES[state]) or not all(
                _text(row[key]) for key in STATES[state]):
            raise CannotEvaluate("{}: a {} entry for {!r} carries exactly {}, each non-blank".format(
                label, state, row["id"], ", ".join(STATES[state])))
        key = (row["suite"], row["id"])
        if key in entries:
            raise CannotEvaluate("{}: {!r} in suite {!r} is named twice".format(label, row["id"], row["suite"]))
        entries[key] = row
    return entries


def base_entries(raw):
    """(entries, note) for the base record: its entries when it reads as a well-formed record, else ({}, the
    reason). The record is judged on the head side, so a malformed base record never blocks the change; it only
    leaves no entry to compare a head entry with."""
    try:
        return load_record(raw, "base"), None
    except CannotEvaluate as exc:
        return {}, str(exc)


def net_removed(base_pairs, head_pairs):
    """The pairs registered at the base and not at the head."""
    return base_pairs - head_pairs


def moved(removed, base_pairs, head_pairs, same_body):
    """{removed pair: the suite its check id moved to}: a removed pair whose check id is newly registered (a pair
    the base did not hold) in another suite at the head, with the same launch and the same body there, the body
    not generic (same_body(old suite, new suite, check id)), each new registration absorbing one removed pair."""
    added = {}
    for suite, check_id in sorted(head_pairs - base_pairs):
        added.setdefault(check_id, []).append(suite)
    result = {}
    for suite, check_id in sorted(removed):
        targets = added.get(check_id, [])
        target = next((other for other in targets if same_body(suite, other, check_id)), None)
        if target is not None:
            targets.remove(target)
            result[(suite, check_id)] = target
    return result


def launches(raw):
    """{suite id: (runner path, args tuple)} for a registry's bytes that load_registry accepted ({} when absent): the
    LAUNCH of a suite, its runner and the args it is run with (no args key reads as no args). A runner that is not
    a string, or args that are not a list of strings, leave the suite out, so it has no launch to compare."""
    if raw is None:
        return {}
    found = {}
    for row in parse_toml(raw, "registry")["suite"]:
        runner, args = row.get("runner"), row.get("args", [])
        if isinstance(runner, str) and isinstance(args, list) and all(isinstance(arg, str) for arg in args):
            found[row["id"]] = (runner, tuple(args))
    return found


def generic(body, elsewhere):
    """Whether a check id's body is GENERIC: the identical body is held for the same id by the runner of another
    suite (elsewhere: the bodies the id has in the runners, at another path, of the suites that register it at the
    base or the head). Boilerplate such as the refuse/unrecognized-argument row each runner holds is generic."""
    return body in elsewhere


def _relative_path(path):
    """Whether a runner path is a plain relative path of exact components (no empty, . or .. component)."""
    return bool(path) and "\\" not in path and not any(part in ("", ".", "..") for part in path.split("/"))


def _call_ids(arg, loops):
    """[(check id, loop row or None)] that a check() call's first argument names: a string literal, or a name bound
    by the innermost enclosing for over a literal tuple or list of rows holding a string literal at that name's
    position; [] for anything else."""
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return [(arg.value, None)]
    if not isinstance(arg, ast.Name):
        return []
    for loop in reversed(loops):
        if isinstance(loop.target, ast.Name) and loop.target.id == arg.id:
            return []
        names = [elt.id if isinstance(elt, ast.Name) else None
                 for elt in loop.target.elts] if isinstance(loop.target, ast.Tuple) else []
        if arg.id not in names:
            continue
        position = names.index(arg.id)
        if not isinstance(loop.iter, (ast.Tuple, ast.List)):
            return []
        found = []
        for row in loop.iter.elts:
            if (not isinstance(row, (ast.Tuple, ast.List)) or len(row.elts) != len(names)
                    or not isinstance(row.elts[position], ast.Constant)
                    or not isinstance(row.elts[position].value, str)):
                return []
            found.append((row.elts[position].value, row))
        return found
    return []


def check_bodies(raw):
    """{check id: body} for a runner's source bytes: the BODY of a check id is the AST dump (no positions) of the
    innermost statement holding its direct check() call, with the loop row that names the id for a loop-bound id.
    An id named by more than one call site maps to None (no single body); an absent, non-UTF-8 or unparsable
    source holds no body."""
    if raw is None:
        return {}
    try:
        tree = ast.parse(raw.decode("utf-8"))
    except (UnicodeDecodeError, SyntaxError, ValueError, RecursionError, MemoryError):
        return {}
    bodies = {}

    def visit(node, statement, loops):
        statement = node if isinstance(node, ast.stmt) else statement
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "check" and node.args:
            for check_id, row in _call_ids(node.args[0], loops):
                body = ast.dump(statement) + ("" if row is None else ast.dump(row))
                bodies[check_id] = None if check_id in bodies else body
        inner = loops + [node] if isinstance(node, ast.For) else loops
        for child in ast.iter_child_nodes(node):
            visit(child, statement, inner)

    try:
        visit(tree, None, [])
    except (RecursionError, MemoryError):
        return {}
    return bodies


def body_reader(base_registry, head_registry, base_read, head_read):
    """same_body(old suite, new suite, check id) for moved(): whether the old suite's launch at the base equals the
    new suite's launch at the head (the same runner path and args), the check id has one body in that runner at the
    base and the same body in it at the head, and that body is not generic (no runner at another path, of a suite
    that registers the id, holds it: such a runner is read at the base for a base suite and at the head for a head
    suite). A launch that differs or is missing, a runner that is not a plain relative path, absent, unreadable or
    unparsable, or an id with no single body there, is not the same body, so the pair counts as removed."""
    old_launches, new_launches, cache = launches(base_registry), launches(head_registry), {}
    sides = ((base_read, old_launches, set() if base_registry is None else load_registry(base_registry, "base")),
             (head_read, new_launches, set() if head_registry is None else load_registry(head_registry, "head")))

    def bodies(read, path):
        if read is None or not isinstance(path, str) or not _relative_path(path):
            return {}
        if (read, path) not in cache:
            try:
                cache[(read, path)] = check_bodies(read(path))
            except (cut.CannotEvaluate, OSError, ValueError):
                cache[(read, path)] = {}
        return cache[(read, path)]

    def elsewhere(check_id, runner):
        return [bodies(read, table[suite][0]).get(check_id) for read, table, pairs in sides
                for suite, other in sorted(pairs) if other == check_id and suite in table and table[suite][0] != runner]

    def same_body(old_suite, new_suite, check_id):
        launch = old_launches.get(old_suite)
        if launch is None or launch != new_launches.get(new_suite):
            return False
        old = bodies(base_read, launch[0]).get(check_id)
        return (old is not None and old == bodies(head_read, launch[0]).get(check_id)
                and not generic(old, elsewhere(check_id, launch[0])))

    return same_body


def survives(entry, head_pairs):
    """Whether a covered entry's covered-by pair is registered at the head."""
    return (entry["covered-by-suite"], entry["covered-by-id"]) in head_pairs


def authorized(value):
    """Whether a disclosed entry's authorization is in AUTHORIZATION_FORM: D-, 1 to AUTHORIZATION_DIGITS ASCII
    digits, one space, and a valid ISO calendar date in parentheses. Read by hand, with no pattern."""
    number, space, date = value.partition(" ")
    digits = number[2:]
    if number[:2] != "D-" or not 0 < len(digits) <= AUTHORIZATION_DIGITS or not all(
            "0" <= char <= "9" for char in digits) or space != " ":
        return False
    inner = date[1:-1]
    if date[:1] != "(" or date[-1:] != ")" or len(inner) != 10 or inner[4] != "-" or inner[7] != "-" or not all(
            "0" <= char <= "9" for char in inner[:4] + inner[5:7] + inner[8:]):
        return False
    try:
        datetime.date(int(inner[:4]), int(inner[5:7]), int(inner[8:]))
    except ValueError:
        return False
    return True


def canonical(entry):
    """An entry with each string value whitespace-collapsed and case-folded (None stays None), the form freshness
    compares, so an edit of whitespace or letter case is not a new accounting."""
    if entry is None:
        return None
    return {key: " ".join(value.split()).casefold() if isinstance(value, str) else value
            for key, value in entry.items()}


def fresh(entry, base_entry):
    """Whether this change wrote the entry: the base record holds no entry for the pair, or one that differs from
    it beyond whitespace and letter case."""
    return canonical(entry) != canonical(base_entry)


def judge(base_registry, head_registry, base_record, head_record, base_read=None, head_read=None):
    """(code, lines) for the four blobs (bytes, or None when absent) of a base and a head; CannotEvaluate when
    either registry or the head record cannot be read (the base record is read by base_entries). base_read and
    head_read map a repository path to its bytes at the base and the head (None when absent); they read the runners
    whose check() bodies a move compares, and without them no removal reads as a move."""
    base_pairs = set() if base_registry is None else load_registry(base_registry, "base")
    # absent at both ends, nothing was registered and nothing is removed; absent at the head only, the whole
    # registry went, which this gate cannot account for pair by pair
    head_pairs = set() if base_registry is None and head_registry is None else load_registry(head_registry, "head")
    head_entries = load_record(head_record, "head")
    old_entries, note = base_entries(base_record)
    findings, accounted = [], []
    if note is not None:
        accounted.append("the base {} is not readable ({}), so every head entry reads as written by this "
                         "change".format(RECORD, note))
    removed = net_removed(base_pairs, head_pairs)
    moves = moved(removed, base_pairs, head_pairs, body_reader(base_registry, head_registry, base_read, head_read))
    for suite, check_id in sorted(removed):
        where = "{!r} (suite {!r})".format(check_id, suite)
        entry = head_entries.get((suite, check_id))
        if (suite, check_id) in moves:
            accounted.append("{} moved to suite {!r}, not removed".format(where, moves[(suite, check_id)]))
        elif entry is None:
            findings.append("{} is removed from {} and {} names no accounting for it".format(where, REGISTRY, RECORD))
        elif not fresh(entry, old_entries.get((suite, check_id))):
            findings.append("{} is removed, but its {} entry is unchanged from the base: record the accounting with "
                            "this change".format(where, RECORD))
        elif entry["state"] == "covered" and not survives(entry, head_pairs):
            findings.append("{} is marked covered by {!r} (suite {!r}), which is not registered at the head".format(
                where, entry["covered-by-id"], entry["covered-by-suite"]))
        elif entry["state"] == "disclosed" and not authorized(entry["authorization"]):
            findings.append("{} is disclosed with authorization {!r}, which is not in the form {}".format(
                where, entry["authorization"], AUTHORIZATION_FORM))
        else:
            accounted.append("{} removed, accounted as {}".format(where, entry["state"]))
    if findings:
        return EXIT_REFUSE, ["FAIL: " + line for line in findings] + ["INFO: " + line for line in accounted]
    return EXIT_OK, ["INFO: " + line for line in accounted]


def blobs(root, tree):
    """(registry, record) bytes in a commit tree (None for an absent path or a None tree)."""
    if tree is None:
        return None, None
    return cut.tree_blob(root, tree, REGISTRY), cut.tree_blob(root, tree, RECORD)


def evaluate(root, protected=None, before=None):
    """(code, lines, binding) for the change at root, its base derived by check_release_cut.context()."""
    try:
        root, binding = cut.context(root, protected, before)
        base_registry, base_record = blobs(root, binding["base_tree"])
        head_registry, head_record = blobs(root, binding["head_tree"])
    except (cut.CannotEvaluate, OSError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("the base or the change cannot be read: {}".format(exc)) from None

    def reader(tree):
        return None if tree is None else lambda path: cut.tree_blob(root, tree, path)

    code, lines = judge(base_registry, head_registry, base_record, head_record, reader(binding["base_tree"]),
                        reader(binding["head_tree"]))
    return code, lines, binding


def run(root, protected=None, before=None):
    try:
        code, lines, binding = evaluate(root, protected, before)
    except CannotEvaluate as exc:
        print("CANNOT_EVALUATE: selftest-removal: {}".format(exc), file=sys.stderr)
        return EXIT_CANNOT_EVALUATE
    for line in lines:
        print(line)
    span = "{}..{}".format(binding["base"] or "(root)", binding["head"])
    if code == EXIT_REFUSE:
        print("REFUSE: selftest-removal: {} removes registered self-test check ids without accounting; add a "
              "[[removal]] entry for each to {} in this change (covered by a surviving id, unreachable, or "
              "disclosed with the maintainer's authorization reference), or restore the id".format(span, RECORD))
    else:
        print("PASS: selftest-removal: {}: no unaccounted removal of a registered self-test check id".format(span))
    return code


# SELF-TEST: production code ends here. Every case asserts through the instrumented check() choke point, and
# the executed id set is reconciled against tools/selftest_checks.toml (suite selftest-removal-selftest) by
# tools/check_selftest_execution.py. The revert/ cases patch one part of this module's globals (no code is
# generated or evaluated) and require their fixture to flip.

SUITE_ID = "selftest-removal-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


def _registry(*suites):
    """Registry bytes for (suite id, (check id, ...)) rows."""
    return "format-version = 1\n".encode() + b"".join(
        '[[suite]]\nid = "{}"\nrunner = "tools/x.py"\nexpected-check-ids = [{}]\n'.format(
            suite, ", ".join('"{}"'.format(i) for i in ids)).encode() for suite, ids in suites)


def _record(*entries):
    """Record bytes for entries given as dicts of string fields."""
    return b"schema = 1\n" + b"".join(
        b"[[removal]]\n" + "".join('{} = "{}"\n'.format(k, v) for k, v in entry.items()).encode()
        for entry in entries)


BASE = _registry(("s", ("a", "b", "c")), ("t", ("x",)))
COVERED = {"suite": "s", "id": "c", "state": "covered", "covered-by-suite": "s", "covered-by-id": "a"}
UNREACHABLE = {"suite": "s", "id": "c", "state": "unreachable", "capability-removed": "change 1",
               "observed": "the path refuses"}
DISCLOSED = {"suite": "s", "id": "c", "state": "disclosed", "authorization": "D-7 (2026-11-01)"}
WITHOUT_C = _registry(("s", ("a", "b")), ("t", ("x",)))
MOVED_C = _registry(("s", ("a", "b")), ("t", ("x", "c")))
# the runner every _registry suite names, read at the base and the head: one check() call site per id
X_RUNNER = b'check("a", 1)\ncheck("b", 1)\ncheck("c", main(["--bogus"]) == 2)\ncheck("x", 1)\n'
SAME = {"tools/x.py": X_RUNNER}.get
# the same calls with the body of c changed, reformatted only, named twice, or bound by a for loop's rows
OTHER_C = {"tools/x.py": X_RUNNER.replace(b'main(["--bogus"]) == 2', b"True")}.get
REFORMATTED_C = {"tools/x.py": X_RUNNER.replace(b'main(["--bogus"]) == 2', b'main( [ "--bogus" ] )==2')}.get
TWICE_C = {"tools/x.py": X_RUNNER + b'check("c", 1)\n'}.get
LOOP_C = b'for name, value in (("b", 1), ("c", 2)):\n    check(name, value == 2)\n'
# another program holding the very statement X_RUNNER holds for c (its main always returns 2), and one holding
# another body for c
Z_RUNNER = b'def main(argv):\n    return 2\ncheck("c", main(["--bogus"]) == 2)\n'
W_OTHER = b'check("c", 0)\n'
# the live twin: a boilerplate id whose check() statement two registered runners hold word for word
LIVE_ID, LIVE_SUITE, LIVE_TARGET = "refuse/unrecognized-argument", "headers-selftest", "tools/check_sitemap.py"


def _quote(value):
    return '"{}"'.format(value.replace("\\", "\\\\").replace('"', '\\"'))


def _launch_registry(*rows):
    """Registry bytes for (suite id, runner, (check id, ...)[, args]) rows; args None or absent writes no args key."""
    out = ["format-version = 1\n"]
    for suite, runner, ids, *args in rows:
        out.append("[[suite]]\nid = {}\nrunner = {}\n{}expected-check-ids = [{}]\n".format(
            _quote(suite), _quote(runner), "" if not args or args[0] is None else "args = [{}]\n".format(
                ", ".join(_quote(arg) for arg in args[0])), ", ".join(_quote(i) for i in ids)))
    return "".join(out).encode("utf-8")


def _live_twin():
    """(precondition, code) for the live twin: LIVE_ID removed from LIVE_SUITE in the live registry and registered
    in a new suite whose runner is LIVE_TARGET, both runners read from this checkout. The precondition is that the
    live suite registers the id and that both runners hold the same single body for it, so the twin is the generic
    case; when a later change breaks that, the row fails and must be re-pointed at another such pair."""
    live = (ROOT / REGISTRY).read_bytes()

    def read(path):
        target = ROOT / path
        return target.read_bytes() if target.is_file() else None

    rows = [(row["id"], row["runner"], [i for i in row["expected-check-ids"] if (row["id"], i) != (LIVE_SUITE, LIVE_ID)],
             row.get("args")) for row in tomllib.loads(live.decode("utf-8"))["suite"]]
    runner = launches(live).get(LIVE_SUITE, (None,))[0]
    body = check_bodies(read(runner) if isinstance(runner, str) else None).get(LIVE_ID)
    precondition = ((LIVE_SUITE, LIVE_ID) in load_registry(live, "live") and body is not None
                    and body == check_bodies(read(LIVE_TARGET)).get(LIVE_ID) and runner != LIVE_TARGET)
    head = _launch_registry(*rows, ("live-twin-selftest", LIVE_TARGET, (LIVE_ID,)))
    return precondition, _code(live, head, None, None, read, read)


def _generic_case(base_w):
    """The code for a move of c from s to t (one runner, tools/x.py) while suite v registers c in another runner:
    at the base, v's runner tools/w.py holds base_w (read at the base only); at the head, v runs tools/w2.py, whose
    body for c differs."""
    base = _launch_registry(("s", "tools/x.py", ("a", "b", "c")), ("t", "tools/x.py", ("x",)),
                            ("v", "tools/w.py", ("c",)))
    head = _launch_registry(("s", "tools/x.py", ("a", "b")), ("t", "tools/x.py", ("x", "c")),
                            ("v", "tools/w2.py", ("c",)))
    return _code(base, head, None, None, {"tools/x.py": X_RUNNER, "tools/w.py": base_w}.get,
                 {"tools/x.py": X_RUNNER, "tools/w2.py": W_OTHER}.get)


def _generic_head_case():
    """The code for the same move while a suite new at the head registers c in tools/w.py, which holds X_RUNNER's
    statement for c at the head."""
    base = _launch_registry(("s", "tools/x.py", ("a", "b", "c")), ("t", "tools/x.py", ("x",)))
    head = _launch_registry(("s", "tools/x.py", ("a", "b")), ("t", "tools/x.py", ("x", "c")),
                            ("v", "tools/w.py", ("c",)))
    files = {"tools/x.py": X_RUNNER, "tools/w.py": Z_RUNNER}
    return _code(base, head, None, None, files.get, files.get)


def _other_runner_case():
    """The code for c moved from suite s (tools/x.py) to a new suite u whose runner tools/z.py holds the same
    statement for c."""
    files = {"tools/x.py": X_RUNNER, "tools/z.py": Z_RUNNER}
    return _code(_launch_registry(("s", "tools/x.py", ("a", "b", "c")), ("t", "tools/x.py", ("x",))),
                 _launch_registry(("s", "tools/x.py", ("a", "b")), ("t", "tools/x.py", ("x",)),
                                  ("u", "tools/z.py", ("c",))), None, None, files.get, files.get)


def _not_plain_case(runner):
    """The code for c moved from s to t, both on the runner path given, with readers that return X_RUNNER for any
    path (as a filesystem read of ./tools/x.py or tools//x.py would)."""
    return _code(_launch_registry(("s", runner, ("a", "b", "c")), ("t", runner, ("x",))),
                 _launch_registry(("s", runner, ("a", "b")), ("t", runner, ("x", "c"))), None, None,
                 lambda path: X_RUNNER, lambda path: X_RUNNER)


NOT_PLAIN_RUNNERS = ("./tools/x.py", "tools/../tools/x.py", "/tools/x.py", "tools//x.py", "tools\\x.py", "")
# authorization references outside the stated form: free text, no date, a bad date, too many digits, lower case,
# two spaces, no parentheses, a non-ASCII digit
UNAUTHORIZED = ("decision 7", "x", "D-7", "D-7 (2026-13-01)", "D-1234567 (2026-11-01)", "d-7 (2026-11-01)",
                "D-7  (2026-11-01)", "D-7 2026-11-01", "D-٧ (2026-11-01)", "D- (2026-11-01)")


def _code(*blobs_):
    try:
        return judge(*blobs_)[0]
    except CannotEvaluate:
        return EXIT_CANNOT_EVALUATE


def _reason(*blobs_):
    """The CannotEvaluate reason judge gives for the blobs, the exception type name of a crash, or None."""
    try:
        judge(*blobs_)
    except CannotEvaluate as exc:
        return str(exc)
    except (AttributeError, TypeError, KeyError) as exc:
        return type(exc).__name__
    return None


@contextlib.contextmanager
def _patched(**replacements):
    names = globals()
    saved = {name: names[name] for name in replacements}
    names.update(replacements)
    try:
        yield
    finally:
        names.update(saved)


def _fixture_env():
    """The shared git fixture environment (tools/_git_fixture_env.py: no GIT_ variable, an empty scratch home,
    global and system config pinned to os.devnull), with a fixed identity and no CI or GitHub event variable,
    so the base derivation under test takes its local path."""
    from _git_fixture_env import git_fixture_env
    env = git_fixture_env(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
                          GIT_COMMITTER_EMAIL="t@example.invalid", GIT_TERMINAL_PROMPT="0")
    for key in [key for key in env if key.startswith("GITHUB_") or key == "CI"]:
        del env[key]
    return env


def _git(repo, _env, *args):
    """A fixture git call in the shared fixture environment with the fixed identity (the CI and GitHub event
    variables of _fixture_env matter only to the code under test, so _env is not read here)."""
    from _git_fixture_env import git_fixture_env
    env = git_fixture_env(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
                          GIT_COMMITTER_EMAIL="t@example.invalid", GIT_TERMINAL_PROMPT="0")
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=env, timeout=60)
    if result.returncode != 0:
        raise OSError("fixture git {} failed: {}".format(args[0], result.stderr.decode("utf-8", "replace")))
    return result.stdout.decode("utf-8").strip()


def _commit(repo, env, files, message):
    for rel, data in files.items():
        path = Path(repo) / rel
        if data is None:
            path.unlink()
            continue
        os.makedirs(path.parent, exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
    _git(repo, env, "add", "-A")
    _git(repo, env, "commit", "-q", "-m", message)
    return _git(repo, env, "rev-parse", "HEAD")


def _run_quiet(root, *args):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return run(root, *args)


def _repo_cases(tmp):
    """The end-to-end cases: a fixture repository whose origin/HEAD names its protected line, so the base is
    derived by check_release_cut.context() exactly as a local run derives it."""
    env = _fixture_env()
    repo = Path(tmp) / "repo"
    os.makedirs(repo)
    _git(repo, env, "init", "-q", "-b", "main")
    base = _commit(repo, env, {REGISTRY: BASE, "tools/x.py": X_RUNNER}, "base")
    _git(repo, env, "update-ref", "refs/remotes/origin/main", base)
    _git(repo, env, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _git(repo, env, "switch", "-q", "-c", "feature")
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        # the runner bodies a move compares are read from the base and head commit trees
        _commit(repo, env, {REGISTRY: MOVED_C}, "move c")
        check("repo/move-same-body-passes", _run_quiet(repo) == EXIT_OK)
        _commit(repo, env, {"tools/x.py": OTHER_C("tools/x.py")}, "move c and change its body")
        check("repo/move-other-body-refused", _run_quiet(repo) == EXIT_REFUSE)
        _commit(repo, env, {REGISTRY: WITHOUT_C, "tools/x.py": X_RUNNER}, "drop c")
        check("repo/unaccounted-removal-refused", _run_quiet(repo) == EXIT_REFUSE)
        _commit(repo, env, {RECORD: _record(COVERED)}, "account for c")
        check("repo/accounted-removal-passes", _run_quiet(repo) == EXIT_OK)
        _commit(repo, env, {REGISTRY: None}, "drop the registry")
        check("repo/registry-absent-at-head-cannot-evaluate", _run_quiet(repo) == EXIT_CANNOT_EVALUATE)
        _git(repo, env, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
        check("repo/base-underivable-cannot-evaluate", _run_quiet(repo) == EXIT_CANNOT_EVALUATE)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    plain = Path(tmp) / "plain"
    os.makedirs(plain)
    check("repo/not-a-repository-cannot-evaluate", _run_quiet(plain) == EXIT_CANNOT_EVALUATE)


def self_test():
    check("pass/no-removal", _code(BASE, BASE, None, None) == EXIT_OK)
    check("pass/addition-only", _code(BASE, _registry(("s", ("a", "b", "c", "d")), ("t", ("x",))), None, None)
          == EXIT_OK)
    check("pass/registry-absent-at-base", _code(None, BASE, None, None) == EXIT_OK)
    check("pass/registry-absent-at-both", _code(None, None, None, None) == EXIT_OK)
    check("refuse/unaccounted-removal", _code(BASE, WITHOUT_C, None, None) == EXIT_REFUSE)
    check("pass/moved-between-suites", judge(BASE, MOVED_C, None, None, SAME, SAME) == (
        EXIT_OK, ["INFO: 'c' (suite 's') moved to suite 't', not removed"]))
    check("refuse/move-target-held-at-base", _code(_registry(("s", ("a", "b", "c")), ("t", ("x", "c"))), MOVED_C,
                                                   None, None, SAME, SAME) == EXIT_REFUSE)
    check("refuse/move-absorbs-one-removal", _code(_registry(("s", ("a", "c")), ("t", ("x",)), ("u", ("c",))),
                                                   _registry(("s", ("a",)), ("t", ("x",)), ("v", ("c",))), None,
                                                   None, SAME, SAME) == EXIT_REFUSE)
    # a move counts only when the old and new suites have the same launch (runner path and args) and the id has
    # the same body, not generic, in that runner; a changed body, another runner, other args, a body another
    # registered runner also holds, and a runner path that is not plain are each a removal
    check("refuse/move-other-body", _code(BASE, MOVED_C, None, None, SAME, OTHER_C) == EXIT_REFUSE)
    check("refuse/move-other-runner", _other_runner_case() == EXIT_REFUSE)
    check("refuse/move-other-args", _code(_launch_registry(("s", "tools/x.py", ("c",), ("--one",))), _launch_registry(
        ("t", "tools/x.py", ("c",), ("--two",))), None, None, SAME, SAME) == EXIT_REFUSE)
    check("pass/move-same-launch", [_code(_launch_registry(("s", "tools/x.py", ("c",), ("--one",))), _launch_registry(
        ("t", "tools/x.py", ("c",), ("--one",))), None, None, SAME, SAME), _code(_launch_registry(
            ("s", "tools/x.py", ("c",))), _launch_registry(("t", "tools/x.py", ("c",))), None, None, SAME, SAME)]
          == [EXIT_OK] * 2)
    check("refuse/move-body-generic", [_generic_case(Z_RUNNER), _generic_head_case()] == [EXIT_REFUSE] * 2)
    check("pass/move-body-not-generic", _generic_case(W_OTHER) == EXIT_OK)
    check("refuse/move-runner-not-plain", [_not_plain_case(runner) for runner in NOT_PLAIN_RUNNERS]
          == [EXIT_REFUSE] * len(NOT_PLAIN_RUNNERS))
    # the live twin the review ran: refuse/unrecognized-argument moved from headers-selftest to a new suite on
    # tools/check_sitemap.py, whose statement for that id is word for word the same
    check("refuse/move-live-generic-twin", _live_twin() == (True, EXIT_REFUSE), _live_twin())
    check("pass/move-same-body-reformatted", _code(BASE, MOVED_C, None, None, SAME, REFORMATTED_C) == EXIT_OK)
    check("refuse/move-runner-absent", [_code(BASE, MOVED_C, None, None, SAME, {}.get), _code(
        BASE, MOVED_C, None, None, {}.get, SAME)] == [EXIT_REFUSE] * 2)
    check("refuse/move-without-runner-read", _code(BASE, MOVED_C, None, None) == EXIT_REFUSE)
    check("refuse/move-call-site-ambiguous", [_code(BASE, MOVED_C, None, None, TWICE_C, TWICE_C), _code(
        BASE, MOVED_C, None, None, SAME, {"tools/x.py": b"check(\n"}.get)] == [EXIT_REFUSE] * 2)
    check("pass/move-loop-row", _code(BASE, MOVED_C, None, None, {"tools/x.py": LOOP_C}.get,
                                      {"tools/x.py": LOOP_C}.get) == EXIT_OK)
    check("refuse/move-loop-row-changed", _code(BASE, MOVED_C, None, None, {"tools/x.py": LOOP_C}.get, {
        "tools/x.py": LOOP_C.replace(b'("c", 2)', b'("c", 3)')}.get) == EXIT_REFUSE)
    check("pass/authorization-form", [_code(BASE, WITHOUT_C, None, _record(dict(DISCLOSED, authorization=value)))
                                      for value in ("D-417 (2026-10-04)", "D-123456 (2024-02-29)")]
          == [EXIT_OK] * 2)
    check("refuse/authorization-not-in-form", [_code(BASE, WITHOUT_C, None, _record(dict(
        DISCLOSED, authorization=value))) for value in UNAUTHORIZED] == [EXIT_REFUSE] * len(UNAUTHORIZED))
    check("refuse/entry-whitespace-or-case-only-change", [_code(BASE, WITHOUT_C, _record(DISCLOSED), _record(dict(
        DISCLOSED, authorization=value))) for value in ("D-7 (2026-11-01) ", " D-7 (2026-11-01)")] + [_code(
            BASE, WITHOUT_C, _record(UNREACHABLE), _record(dict(UNREACHABLE, observed="The path  refuses")))]
          == [EXIT_REFUSE] * 3)
    check("pass/base-record-malformed-head-fixed", [_code(BASE, WITHOUT_C, base, _record(DISCLOSED)) for base in (
        b"schema = 3\n", b"schema = 1\n[[removal\n", b"\xff", b"schema = 1\nremoval = [1]\n")] == [EXIT_OK] * 4)
    check("cannot/record-unknown-key-named", "unknown key" in (_reason(BASE, WITHOUT_C, None, _record(dict(
        DISCLOSED, note="x"))) or ""))
    check("cannot/record-entry-not-table", _reason(BASE, WITHOUT_C, None, b"schema = 1\nremoval = [1]\n")
          == "head: a [[removal]] entry with an unknown key")
    check("pass/covered-by-survivor", _code(BASE, WITHOUT_C, None, _record(COVERED)) == EXIT_OK)
    check("refuse/covered-by-removed", _code(BASE, _registry(("s", ("b",)), ("t", ("x",))), None,
                                             _record(COVERED)) == EXIT_REFUSE)
    check("refuse/covered-by-never-registered", _code(BASE, WITHOUT_C, None, _record(dict(COVERED, **{
        "covered-by-id": "never-registered"}))) == EXIT_REFUSE)
    check("refuse/covered-by-itself", _code(BASE, WITHOUT_C, None, _record(dict(COVERED, **{
        "covered-by-id": "c"}))) == EXIT_REFUSE)
    check("pass/unreachable", _code(BASE, WITHOUT_C, None, _record(UNREACHABLE)) == EXIT_OK)
    check("pass/disclosed", _code(BASE, WITHOUT_C, None, _record(DISCLOSED)) == EXIT_OK)
    check("refuse/entry-other-suite", _code(BASE, WITHOUT_C, None, _record(dict(DISCLOSED, suite="t")))
          == EXIT_REFUSE)
    check("refuse/entry-unchanged-from-base", _code(BASE, WITHOUT_C, _record(DISCLOSED), _record(DISCLOSED))
          == EXIT_REFUSE)
    check("pass/entry-rewritten-in-change", _code(BASE, WITHOUT_C, _record(DISCLOSED), _record(dict(
        DISCLOSED, authorization="D-8 (2026-11-02)"))) == EXIT_OK)
    check("refuse/one-of-two-unaccounted", _code(BASE, _registry(("s", ("a",)), ("t", ("x",))), None,
                                                 _record(DISCLOSED)) == EXIT_REFUSE)
    for name, registry in (("cannot/registry-absent-at-head", None),
                           ("cannot/registry-not-toml", b"format-version = 1\n[[suite\n"),
                           ("cannot/registry-not-utf8", b"format-version = 1\n# \xff\n"),
                           ("cannot/registry-format-version", BASE.replace(b"format-version = 1", b"format-version = 2")),
                           ("cannot/registry-ids-not-list", b'format-version = 1\n[[suite]]\nid = "s"\n'
                                                            b'expected-check-ids = "a"\n'),
                           ("cannot/registry-id-not-string", b'format-version = 1\n[[suite]]\nid = "s"\n'
                                                             b'expected-check-ids = [1]\n'),
                           ("cannot/registry-nested-too-deep", b"format-version = 1\nx = " + b"[" * 100000 + b"]"
                            * 100000 + b"\n")):
        check(name, _code(BASE, registry, None, None) == EXIT_CANNOT_EVALUATE)
    check("cannot/base-registry-malformed", _code(b"[[suite\n", BASE, None, None) == EXIT_CANNOT_EVALUATE)
    for name, record in (("cannot/record-unknown-state", _record(dict(DISCLOSED, state="waived"))),
                         ("cannot/record-missing-field", _record({"suite": "s", "id": "c", "state": "disclosed"})),
                         ("cannot/record-blank-field", _record(dict(DISCLOSED, authorization="  "))),
                         ("cannot/record-unknown-key", _record(dict(DISCLOSED, note="x"))),
                         ("cannot/record-other-state-field", _record(dict(DISCLOSED, observed="x"))),
                         ("cannot/record-repeated-pair", _record(DISCLOSED, DISCLOSED)),
                         ("cannot/record-schema", _record(DISCLOSED).replace(b"schema = 1", b"schema = 2")),
                         ("cannot/record-not-toml", b"schema = 1\n[[removal\n")):
        check(name, _code(BASE, WITHOUT_C, None, record) == EXIT_CANNOT_EVALUATE)
    check("cannot/record-malformed-without-removal", _code(BASE, BASE, None, b"schema = 3\n")
          == EXIT_CANNOT_EVALUATE)
    # each part patched out flips its fixture
    with _patched(net_removed=lambda base_pairs, head_pairs: set()):
        check("revert/net-removal-red", _code(BASE, WITHOUT_C, None, None) == EXIT_OK)
    with _patched(survives=lambda entry, head_pairs: True):
        check("revert/survivor-red", _code(BASE, WITHOUT_C, None, _record(dict(COVERED, **{
            "covered-by-id": "never-registered"}))) == EXIT_OK)
    with _patched(fresh=lambda entry, base_entry: True):
        check("revert/fresh-entry-red", _code(BASE, WITHOUT_C, _record(DISCLOSED), _record(DISCLOSED)) == EXIT_OK)
    with _patched(STATES=dict(STATES, waived=())):
        check("revert/closed-states-red", _code(BASE, WITHOUT_C, None, _record(
            {"suite": "s", "id": "c", "state": "waived"})) == EXIT_OK)
    with _patched(load_registry=lambda raw, label: set()):
        check("revert/registry-fail-closed-red", _code(BASE, None, None, None) == EXIT_OK)
    with _patched(moved=lambda removed, base_pairs, head_pairs, same_body: {}):
        check("revert/move-red", _code(BASE, MOVED_C, None, None, SAME, SAME) == EXIT_REFUSE)
    with _patched(body_reader=lambda *readers: lambda old_suite, new_suite, check_id: True):
        check("revert/move-body-red", _code(BASE, MOVED_C, None, None, SAME, OTHER_C) == EXIT_OK)
    with _patched(launches=lambda raw: {} if raw is None else {
            suite: ("tools/x.py", ()) for suite, _ in load_registry(raw, "launch")}):
        check("revert/move-launch-red", _other_runner_case() == EXIT_OK)
    with _patched(generic=lambda body, elsewhere: False):
        check("revert/move-generic-red", [_generic_case(Z_RUNNER), _generic_head_case()] == [EXIT_OK] * 2)
    with _patched(_relative_path=lambda path: True):
        check("revert/relative-path-red", [_not_plain_case(runner) for runner in NOT_PLAIN_RUNNERS]
              == [EXIT_OK] * len(NOT_PLAIN_RUNNERS))
    # the base runner read at the head and the head runner at the base: tools/w.py, which holds c's statement at
    # the base only, is no longer read, so the generic body goes unseen
    with _patched(body_reader=lambda base_registry, head_registry, base_read, head_read, real=body_reader: real(
            base_registry, head_registry, head_read, base_read)):
        check("revert/move-reader-sides-red", _generic_case(Z_RUNNER) == EXIT_OK)
    with _patched(authorized=lambda value: True):
        check("revert/authorization-form-red", _code(BASE, WITHOUT_C, None, _record(dict(
            DISCLOSED, authorization="decision 7"))) == EXIT_OK)
    with _patched(canonical=lambda entry: entry):
        check("revert/canonical-fresh-red", _code(BASE, WITHOUT_C, _record(UNREACHABLE), _record(dict(
            UNREACHABLE, observed="The path  refuses"))) == EXIT_OK)
    with _patched(base_entries=lambda raw: (load_record(raw, "base"), None)):
        check("revert/base-record-head-side-red", _code(BASE, WITHOUT_C, b"schema = 3\n", _record(DISCLOSED))
              == EXIT_CANNOT_EVALUATE)
    with _patched(ENTRY_KEYS=ENTRY_KEYS | {"note"}):
        check("revert/unknown-key-red", "unknown key" not in (_reason(BASE, WITHOUT_C, None, _record(dict(
            DISCLOSED, note="x"))) or "unknown key"))
    tmp = tempfile.mkdtemp(prefix="aiqt-selftest-removal-")
    try:
        _repo_cases(tmp)
    except (OSError, subprocess.SubprocessError) as exc:
        FAILURES.append("repo fixture: {}".format(exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    got = [_main_exit(argv) for argv in (["--bogus"], ["--root"], ["--self-test", "--root", "x"],
                                         ["--execution-report", "relative/report"])]
    check("refuse/unrecognized-argument", got == [2, 2, 2, 2], got)
    if FAILURES:
        print("SELF-TEST FAIL: check_selftest_removal {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_selftest_removal {} checks (no removal, an addition, a move between suites with "
          "the same launch and the same check() body that no other runner holds, and a registry new at the base "
          "pass; a move whose body changed or cannot be read, whose runner or args differ, whose body is generic "
          "(the live refuse/unrecognized-argument twin included) or whose runner path is not plain, "
          "an unaccounted removal, a stale entry (whitespace or case edits "
          "included), an entry for another suite, a covered-by pair that does not survive and an authorization "
          "outside the stated form refuse; covered, unreachable and disclosed entries pass; a registry or head "
          "record that is absent at the head, not TOML, not UTF-8 or misshapen is cannot-evaluate, and a "
          "malformed base record does not block; the base is derived by check_release_cut.context() in a fixture "
          "repository, and an underivable base is cannot-evaluate; each part patched out flips its "
          "fixture)".format(len(EXECUTED)))
    return 0


def _main_exit(argv):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return main(argv)


def main(argv):
    if argv[-2:-1] == ["--execution-report"] and os.path.isabs(argv[-1]) and argv[:-2] in ([], ["--self-test"]):
        sys.path.append(str(Path(__file__).resolve().parent))
        import _selftest_exit_report
        _selftest_exit_report.arm(argv[-1], SUITE_ID, EXECUTED)
        return self_test()
    if argv == ["--self-test"]:
        return self_test()
    options, args = {}, list(argv)
    while args:
        arg = args.pop(0)
        if arg in ("--root", "--protected", "--base") and args and arg not in options:
            options[arg] = args.pop(0)
        else:
            print("error: unrecognized arguments {!r}; fail closed".format(argv), file=sys.stderr)
            return EXIT_CANNOT_EVALUATE
    return run(options.get("--root", os.getcwd()), options.get("--protected"), options.get("--base"))


if __name__ == "__main__":
    _code_ = main(sys.argv[1:])
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code_)
