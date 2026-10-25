#!/usr/bin/env python3
"""Control-retirement gate (control-retirement): a control removed between the comparison base and the head
carries a recorded disposition.

Removing or replacing a control is a set operation, so a removal is a reduction until its coverage is
shown (rule gate-discipline, its second paragraph). This gate holds the part of that accounting the
repository can read mechanically: three control id sets, each compared between the base commit and the
head working tree.

  gate:<id>                  every [[gate]] id in .aiqt/core/gates/manifest.toml
  hook:<id>                  every [[hook]] id in .aiqt/core/hooks/manifest.toml
  check:<suite>/<check id>   every expected check id of every [[suite]] in tools/selftest_checks.toml

A control present at the base and absent at the head needs exactly one [[retirement]] row in
tools/control_retirements.toml, in one of the rule's three states:

  covered      successor names a control live at the head, and evidence names the case exercised against
               it and observed to be caught; a rename is one covered row naming the new id
  unreachable  evidence names the change that removes the capability and the observed attempt
  disclosed    authorization names the maintainer's recorded authorization; evidence is optional

The record is append-only: a row present at the base is present at the head with the same keys and values,
unless its control is live at the head again, which every row forbids. Rows are compared as parsed TOML, so
key order, row order and string quoting are not compared. Every row field holds a string; a row carrying
any other type, at either end, cannot be evaluated (exit 2). A row may name only a control absent at the
head, and a row new at the head only a control live at the base (one the change removes); a row the base
already holds that names a control absent at both ends is history and stays. A base row naming a control
live at the base cannot stand in for a removal in this range: when the head removes that control, the stale
row is a finding and the removal needs its own row. While the control stays live at the head, deleting the
stale row is the repair, which the append-only check allows for a control live at the head.

Base binding. On a pull request (GITHUB_EVENT_NAME=pull_request or GITHUB_BASE_REF set) the base is the
unique merge base of HEAD and refs/remotes/origin/<GITHUB_BASE_REF>, and --base is refused. On a GitHub
Actions push the base is the event payload's before commit (GITHUB_EVENT_PATH), and --base is refused; any
other Actions event is refused. Locally the base is the unique merge base of HEAD and refs/heads/main, or
the commit --base names. A shallow repository, an unresolvable or ambiguous base, a missing or malformed
input at either end, or a missing head record cannot be evaluated (exit 2), never a pass. The base record
may be absent (an empty record).

Git configuration. Every git call the gate makes, and every fixture git call of its self-test, runs under
the hermetic git environment of tools/_git_fixture_env.py (git_fixture_env): every GIT_-prefixed variable
is dropped, HOME and XDG_CONFIG_HOME point at an empty scratch home (a temporary directory created for the
run and removed at exit), the global and system config files are pinned to os.devnull with
GIT_CONFIG_NOSYSTEM=1, and gc.auto, gc.autoDetach and maintenance.auto are pinned off at command scope
(GIT_CONFIG_COUNT), which no read the gate makes depends on. So no global, XDG or system configuration, a
malformed file included, reaches a read; the repository's own config, refs and objects are still read. A
scratch home that cannot be created cannot be evaluated (exit 2). The self-test's poison controls are the
one deliberate exception: each runs git with one malformed caller configuration surface, to show that the
surface breaks a read that honours it.

Loaders. At the head, the registry rows are validated by the execution gate's own schema validator
(check_selftest_execution._manifest_rows), and gate and hook ids are held to the hooks generator's
control-id grammar (gen_hooks.ID_RE). The base is read by id only: the id of each [[gate]] and [[hook]]
row, and the id and expected-check-ids of each [[suite]] row, every other key ignored (a key the head
schema does not know among them). A base id is a non-empty string with no control or format character (a
suite id also without '/'), which every id the head grammars admit meets, and a row may name any id in that
grammar. So a change that migrates the registry schema or narrows an id grammar can still be judged against
its base. Renaming a key this reader uses (id or expected-check-ids) needs the reader to accept both names
for that change, or the base cannot be evaluated (exit 2). The full gate and hook loaders
(gen_enforceability.load_gates_manifest, gen_hooks.load_manifest) are not reused: they read a filesystem
path and the gate loader requires each script on disk, so neither can read a base snapshot. Their own
gates validate the head.

Residue. It sees only the three id sets: a control whose id survives while its assertion weakens, a
diagnostic no registered check pins, a reason whose wording changes while its meaning is lost, a hook in
the preview channel (.aiqt/core/hooks/preview.toml), every self-test suite that is not registered
(the main hook suite among them) and every gate step that is not in the gates manifest are outside it. It
compares two endpoints, so an id removed and restored inside the range is unseen. A row's evidence and
authorization are checked for presence and form only; whether the evidence is true, and whether the
authorization was given, is review's to establish. Locally the base follows the local main ref, which can
be stale.
Grafts and replace objects are not inspected beyond GIT_NO_REPLACE_OBJECTS. A safe.directory entry in the
caller's global or system configuration is not read either, so a checkout owned by another account fails
git's ownership check, which the gate reports as cannot-evaluate (exit 2), never a pass; runs by the
checkout owner, and CI, whose runner owns its checkout, are unaffected.

  check_control_retirement.py               check the repository
  check_control_retirement.py --base REV    check against REV (local runs only)
  check_control_retirement.py --self-test   assert the gate's vectors, and that each is discriminating
  check_control_retirement.py [--self-test] --execution-report ABS_PATH
                                            the self-test, also writing its execution report, finalized
                                            at interpreter exit (tools/_selftest_exit_report.py)
Exit: 0 every removed control has a valid disposition; 1 findings; 2 cannot evaluate.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_control_retirement.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import contextlib
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _git_fixture_env  # noqa: E402  (its scratch home, which one self-test vector makes unavailable)
import _selftest_exit_report  # noqa: E402  (the execution report, finalized at interpreter exit)
import check_selftest_execution  # noqa: E402  (the authoritative registry schema)
from _git_fixture_env import fixture_git_lifecycle, git_fixture_env  # noqa: E402  (the hermetic git environment)
from gen_hooks import ID_RE  # noqa: E402  (the control-id grammar of gate and hook ids)

ROOT = Path(__file__).resolve().parents[1]
SUITE_ID = "control-retirement-selftest"
GATES_REL = ".aiqt/core/gates/manifest.toml"
HOOKS_REL = ".aiqt/core/hooks/manifest.toml"
REGISTRY_REL = "tools/selftest_checks.toml"
LEDGER_REL = "tools/control_retirements.toml"
LOCAL_TARGET = "refs/heads/main"
TIMEOUT = 60
OID_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
BRANCH_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._/-]*")
LEDGER_TOP_KEYS = {"format-version", "retirement"}
ROW_KEYS = {"control", "state", "successor", "evidence", "authorization"}
# Per state: (the fields a row must carry non-empty, the further fields it may carry).
STATE_FIELDS = {
    "covered": ({"successor", "evidence"}, set()),
    "unreachable": ({"evidence"}, set()),
    "disclosed": ({"authorization"}, {"evidence"}),
}


class CannotEvaluate(Exception):
    """An input the gate cannot resolve, read or parse; the run exits 2."""


class OwnershipRefused(CannotEvaluate):
    """git refused to read the checkout because another account owns it; the run exits 2 with a note
    naming who can evaluate (the refusal class, not a message scan, selects the note, so a caller-supplied
    revision name such as safe.directory cannot draw the note onto an unrelated refusal)."""


def _git_env():
    """The hermetic git environment (git_fixture_env) with replace objects, optional locks and prompts off: no
    GIT_-prefixed variable redirects a read, and no global, XDG or system configuration reaches it. A scratch
    home that cannot be created is CannotEvaluate (exit 2), not an uncaught error (exit 1, the findings exit)."""
    try:
        return git_fixture_env(GIT_NO_REPLACE_OBJECTS="1", GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0",
                               LC_ALL="C")
    except OSError as exc:
        raise CannotEvaluate("hermetic git environment: scratch home not created ({})".format(exc))


def _finish(result, label):
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace")
        detail = "{} exited {}: {}".format(label, result.returncode, stderr.strip())
        # The ownership refusal is recognized only on a line git itself begins, never inside an echoed
        # argument, so a revision name carrying the phrase stays an ordinary refusal.
        if any(line.startswith("fatal: detected dubious ownership in repository at")
               for line in stderr.splitlines()):
            raise OwnershipRefused(detail)
        raise CannotEvaluate(detail)
    return result.stdout


def _rev_parse(root, env, *args):
    try:
        result = subprocess.run(["git", "-C", str(root), "rev-parse", *args],
                                capture_output=True, env=env, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("git rev-parse: {}".format(exc))
    return _finish(result, "git rev-parse")


def _merge_base(root, env, *args):
    try:
        result = subprocess.run(["git", "-C", str(root), "merge-base", *args],
                                capture_output=True, env=env, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("git merge-base: {}".format(exc))
    return _finish(result, "git merge-base")


def _ls_tree(root, env, *args):
    try:
        result = subprocess.run(["git", "-C", str(root), "ls-tree", *args],
                                capture_output=True, env=env, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("git ls-tree: {}".format(exc))
    return _finish(result, "git ls-tree")


def _cat_file(root, env, *args):
    try:
        result = subprocess.run(["git", "-C", str(root), "cat-file", *args],
                                capture_output=True, env=env, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("git cat-file: {}".format(exc))
    return _finish(result, "git cat-file")


def _commit(root, env, revision):
    """The commit oid revision names, or CannotEvaluate."""
    if not revision or revision.startswith("-"):
        raise CannotEvaluate("revision {!r} is not a usable commit name".format(revision))
    try:
        raw = _rev_parse(root, env, "--verify", "--quiet", "--end-of-options", revision + "^{commit}")
    except CannotEvaluate as exc:
        # The refusal keeps its class, so an ownership refusal stays recognizable through the wrap.
        raise type(exc)("{} does not resolve to a commit ({})".format(revision, exc))
    value = raw.decode("ascii", "replace").strip()
    if not OID_RE.fullmatch(value) or raw.count(b"\n") != 1:
        raise CannotEvaluate("{} does not resolve to one commit".format(revision))
    return value


def _merge_base_of(root, env, target):
    """The unique merge base of HEAD and target, or CannotEvaluate."""
    head, tip = _commit(root, env, "HEAD"), _commit(root, env, target)
    bases = _merge_base(root, env, "--all", head, tip).decode("ascii", "replace").split()
    if len(bases) != 1 or not OID_RE.fullmatch(bases[0]):
        raise CannotEvaluate("expected exactly one merge base of HEAD and {} (found {})".format(
            target, len(bases)))
    return bases[0]


def _push_before(environ):
    """The before commit of the GitHub Actions push event payload, or CannotEvaluate."""
    path = environ.get("GITHUB_EVENT_PATH", "")
    if not path:
        raise CannotEvaluate("push event without GITHUB_EVENT_PATH")
    try:
        with open(path, "rb") as handle:
            payload = json.loads(handle.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise CannotEvaluate("push event payload unreadable ({})".format(type(exc).__name__))
    before = payload.get("before") if isinstance(payload, dict) else None
    if not isinstance(before, str) or not OID_RE.fullmatch(before) or not before.strip("0"):
        raise CannotEvaluate("push event payload carries no usable before commit")
    return before


def resolve_base(root, base_arg, environ):
    """(how the base was bound, the base commit oid); every unresolvable binding raises CannotEvaluate."""
    env = _git_env()
    if _rev_parse(root, env, "--is-shallow-repository") != b"false\n":
        raise CannotEvaluate("shallow or unrecognizable repository; the base history cannot be trusted")
    event = environ.get("GITHUB_EVENT_NAME", "")
    base_ref = environ.get("GITHUB_BASE_REF", "")
    if event == "pull_request" or base_ref:
        if base_arg is not None:
            raise CannotEvaluate("a pull request derives its base from GITHUB_BASE_REF; --base is refused")
        if not BRANCH_RE.fullmatch(base_ref) or ".." in base_ref or base_ref.endswith((".lock", "/")):
            raise CannotEvaluate("pull request without a usable GITHUB_BASE_REF ({!r})".format(base_ref))
        target = "refs/remotes/origin/" + base_ref
        return "merge base of HEAD and " + target, _merge_base_of(root, env, target)
    if environ.get("GITHUB_ACTIONS") == "true":
        if event != "push":
            raise CannotEvaluate("unsupported GitHub Actions event {!r}".format(event))
        if base_arg is not None:
            raise CannotEvaluate("a push derives its base from the event payload; --base is refused")
        return "push before commit", _commit(root, env, _push_before(environ))
    if base_arg is not None:
        return "--base " + base_arg, _commit(root, env, base_arg)
    return "merge base of HEAD and " + LOCAL_TARGET, _merge_base_of(root, env, LOCAL_TARGET)


def _base_reader(root, env, base):
    """read(rel) for the base commit: the blob bytes, or None when the path is absent there."""
    def read(rel):
        listing = _ls_tree(root, env, "-z", base, "--", rel)
        if not listing:
            return None
        entry = listing.split(b"\0")[0]
        meta, _tab, path = entry.partition(b"\t")
        fields = meta.split(b" ")
        if (path != rel.encode("utf-8") or len(fields) != 3 or fields[1] != b"blob"
                or fields[0] not in (b"100644", b"100755")):
            raise CannotEvaluate("base {}: not a regular file in the base tree".format(rel))
        return _cat_file(root, env, "blob", fields[2].decode("ascii"))
    return read


def _head_reader(root):
    """read(rel) for the head working tree: the file bytes, or None when the path is absent."""
    def read(rel):
        path = Path(root) / rel
        try:
            mode = os.lstat(path).st_mode
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise CannotEvaluate("head {}: unreadable ({})".format(rel, type(exc).__name__))
        if not stat.S_ISREG(mode):
            raise CannotEvaluate("head {}: not a regular file".format(rel))
        try:
            return path.read_bytes()
        except OSError as exc:
            raise CannotEvaluate("head {}: unreadable ({})".format(rel, type(exc).__name__))
    return read


def _parse_toml(data, label):
    try:
        return tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("{}: malformed ({})".format(label, type(exc).__name__))


def _required(read, where, rel):
    data = read(rel)
    if data is None:
        raise CannotEvaluate("{} {}: missing".format(where, rel))
    return _parse_toml(data, "{} {}".format(where, rel))


def _id_text(value):
    """True for a non-empty string with no control or format character: the registry's own check-id grammar,
    and the grammar a base id and a row's control id are read by, which every id the head grammars admit
    meets."""
    return isinstance(value, str) and bool(value) and not check_selftest_execution._has_ctrl(value)


def _head_id_ok(cid):
    return bool(ID_RE.fullmatch(cid))


def control_ids(data, table, kind, label, id_ok):
    """The control keys kind:<id> of every [[table]] row, each id held to id_ok; a row without such an id cannot
    be evaluated."""
    rows = data.get(table)
    if not isinstance(rows, list) or not rows:
        raise CannotEvaluate("{}: [[{}]] must be a non-empty array of tables".format(label, table))
    keys = set()
    for number, row in enumerate(rows, 1):
        cid = row.get("id") if isinstance(row, dict) else None
        if not isinstance(cid, str) or not id_ok(cid):
            raise CannotEvaluate("{}: [[{}]] #{} has no usable id".format(label, table, number))
        key = "{}:{}".format(kind, cid)
        if key in keys:
            raise CannotEvaluate("{}: duplicate {} id {!r}".format(label, table, cid))
        keys.add(key)
    return keys


def check_keys(data, label):
    """The control keys check:<suite>/<check id> of the head registry, after the execution gate's schema."""
    violation = io.StringIO()
    with contextlib.redirect_stderr(violation):
        rows = check_selftest_execution._manifest_rows(data, label)
    if rows is None:
        raise CannotEvaluate("{}: schema-invalid ({})".format(label, " ".join(violation.getvalue().split())))
    return {"check:{}/{}".format(row["id"], cid) for row in rows for cid in row["expected-check-ids"]}


def base_check_keys(data, label):
    """The control keys check:<suite>/<check id> of the base registry, read by id only: the id and
    expected-check-ids of each [[suite]] row, every other key ignored, so a change that migrates the registry
    schema can still be judged against its base. A row without a suite id (no '/') or a non-empty list of
    check ids, or a key listed twice, cannot be evaluated."""
    suites = data.get("suite")
    if not isinstance(suites, list) or not suites:
        raise CannotEvaluate("{}: [[suite]] must be a non-empty array of tables".format(label))
    found = set()
    for number, row in enumerate(suites, 1):
        sid = row.get("id") if isinstance(row, dict) else None
        if not _id_text(sid) or "/" in sid:
            raise CannotEvaluate("{}: [[suite]] #{} has no usable suite id".format(label, number))
        cids = row.get("expected-check-ids")
        if not isinstance(cids, list) or not cids or not all(_id_text(cid) for cid in cids):
            raise CannotEvaluate("{}: [[suite]] #{} ({}) has no non-empty list of usable check ids".format(
                label, number, sid))
        for cid in cids:
            control = "check:{}/{}".format(sid, cid)
            if control in found:
                raise CannotEvaluate("{}: {} is listed twice".format(label, control))
            found.add(control)
    return found


def live_controls(read, where):
    """Every control key live in one snapshot: the head held to the head's own registry schema and id grammar,
    the base read by id only."""
    head = where == "head"
    id_ok = _head_id_ok if head else _id_text
    registry = check_keys if head else base_check_keys
    live = set()
    live |= control_ids(_required(read, where, GATES_REL), "gate", "gate", where + " " + GATES_REL, id_ok)
    live |= control_ids(_required(read, where, HOOKS_REL), "hook", "hook", where + " " + HOOKS_REL, id_ok)
    live |= registry(_required(read, where, REGISTRY_REL), where + " " + REGISTRY_REL)
    return live


def load_ledger(data, label):
    """The [[retirement]] rows of one record; an absent record (data None) is empty. Every field of every
    row must hold a string: row values are hashed and compared downstream, so a TOML array, table or other
    non-string value (unhashable, or silently unequal to any string) cannot be evaluated (exit 2), never an
    uncaught TypeError."""
    if data is None:
        return []
    parsed = _parse_toml(data, label)
    if not {"format-version"} <= set(parsed) <= LEDGER_TOP_KEYS:
        raise CannotEvaluate("{}: top-level keys must be format-version and optionally [[retirement]]"
                             .format(label))
    if type(parsed["format-version"]) is not int or parsed["format-version"] != 1:
        raise CannotEvaluate("{}: format-version must be exactly the integer 1".format(label))
    rows = parsed.get("retirement", [])
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise CannotEvaluate("{}: [[retirement]] must be an array of tables".format(label))
    for number, row in enumerate(rows, 1):
        for key in sorted(row):
            if not isinstance(row[key], str):
                raise CannotEvaluate("{}: row {} field {} must be a string, not {}".format(
                    label, number, key, type(row[key]).__name__))
    return rows


def removed_controls(base_live, head_live):
    return base_live - head_live


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and not check_selftest_execution._has_ctrl(value)


def control_key_ok(key):
    """True for gate:<id>, hook:<id> or check:<suite>/<check id> in the grammar a base id is read by
    (_id_text), so every id the base or head registries admit, a whitespace-padded check id among them, can
    be named."""
    kind, sep, rest = key.partition(":")
    if kind in ("gate", "hook"):
        return bool(sep) and _id_text(rest)
    if kind == "check":
        suite, slash, cid = rest.partition("/")
        return bool(sep) and _id_text(suite) and bool(slash) and _id_text(cid)
    return False


def row_findings(number, row, head_live):
    """The findings of one head [[retirement]] row."""
    where = "{} row {}".format(LEDGER_REL, number)
    control = row.get("control")
    if not isinstance(control, str) or not control_key_ok(control):
        return [where + ": control must be gate:<id>, hook:<id> or check:<suite>/<check id>"]
    where = "{} ({})".format(where, control)
    findings = []
    unknown = sorted(set(row) - ROW_KEYS)
    if unknown:
        findings.append("{}: unknown field(s) {}".format(where, ", ".join(unknown)))
    if control in head_live:
        findings.append("{}: names a control still live at head; a disposition is for a removed control"
                        .format(where))
    state = row.get("state")
    if state not in STATE_FIELDS:
        findings.append("{}: state must be covered, unreachable or disclosed".format(where))
        return findings
    required, optional = STATE_FIELDS[state]
    for key in sorted(required):
        if not _text(row.get(key)):
            findings.append("{}: a row in state {} needs a non-empty {}".format(where, state, key))
    # An optional field escapes both the required loop above and the foreign-field loop below, so its
    # form is held here whenever it is present: the record promises presence AND form for every field.
    for key in sorted(set(row) & optional):
        if not _text(row.get(key)):
            findings.append("{}: optional {} on a {} row must be non-empty text without control "
                            "characters".format(where, key, state))
    for key in sorted((set(row) & ROW_KEYS) - {"control", "state"} - required - optional):
        findings.append("{}: {} does not belong on a {} row".format(where, key, state))
    # The row's own control is absent at head (else the live-row finding above), so a successor live at head
    # is never the control itself.
    successor = row.get("successor")
    if state == "covered" and _text(successor):
        if successor not in head_live:
            findings.append("{}: successor {} is not a control live at head".format(where, successor))
    return findings


def evaluate(base_read, head_read):
    """(count of removed controls, findings); raises CannotEvaluate naming the input it could not use."""
    base_live = live_controls(base_read, "base")
    head_live = live_controls(head_read, "head")
    base_rows = load_ledger(base_read(LEDGER_REL), "base " + LEDGER_REL)
    head_data = head_read(LEDGER_REL)
    if head_data is None:
        raise CannotEvaluate("head {}: missing".format(LEDGER_REL))
    head_rows = load_ledger(head_data, "head " + LEDGER_REL)
    removed = removed_controls(base_live, head_live)
    base_named = set(row["control"] for row in base_rows if isinstance(row.get("control"), str))
    findings, rows_by_control = [], {}
    for number, row in enumerate(head_rows, 1):
        findings.extend(row_findings(number, row, head_live))
        control = row.get("control")
        if isinstance(control, str):
            if control in rows_by_control:
                findings.append("{} row {} ({}): duplicates row {}".format(
                    LEDGER_REL, number, control, rows_by_control[control]))
            rows_by_control.setdefault(control, number)
            # A row new at head records a removal in this range, so it names a control live at the base. A row
            # whose control the base record already names is history, or an edit the append-only check reports;
            # a malformed control or one live at head is reported by row_findings.
            if (control_key_ok(control) and control not in head_live and control not in base_live
                    and control not in base_named):
                findings.append("{} row {} ({}): names no control live at base; a new row records a control "
                                "the change removes".format(LEDGER_REL, number, control))
    for control in sorted(removed):
        if control not in rows_by_control:
            findings.append("{}: present at base, absent at head, and no row in {} records its disposition"
                            .format(control, LEDGER_REL))
    for number, row in enumerate(base_rows, 1):
        # A disposition lands in the change that removes its control, so a base row whose control is
        # still live at the base was recorded ahead of any removal and cannot stand in for a later one.
        # The finding is bound to the removal (the control absent at the head): while the control stays
        # live, deleting the stale row is the repair, and the row itself is reported by the head-row
        # still-live check for as long as it is kept.
        base_control = row.get("control")
        if isinstance(base_control, str) and base_control in base_live and base_control not in head_live:
            findings.append("{} base row {} ({}): names a control live at the base; a disposition is "
                            "recorded in the change that removes its control".format(
                                LEDGER_REL, number, base_control))
        if row not in head_rows and row.get("control") not in head_live:
            findings.append("{} base row {} ({}): edited or removed; recorded dispositions are append-only"
                            .format(LEDGER_REL, number, row.get("control")))
    return len(removed), findings


def run(root, base_arg=None, environ=None):
    environ = os.environ if environ is None else environ
    try:
        how, base = resolve_base(root, base_arg, environ)
        count, findings = evaluate(_base_reader(root, _git_env(), base), _head_reader(root))
    except CannotEvaluate as exc:
        print("error: cannot evaluate control retirement ({}); fail-closed".format(exc), file=sys.stderr)
        if isinstance(exc, OwnershipRefused):
            # git's advice above suggests a global safe.directory entry, but this gate reads git with the
            # global and system configuration pinned to os.devnull, so that advice cannot take effect.
            # The refusal class, set where git's own ownership line is read, selects this note; a message
            # that merely mentions ownership or safe.directory does not.
            print("note: only the checkout owner or CI can evaluate this checkout; this gate reads git "
                  "with no global or system configuration, so a safe.directory entry cannot reach it.",
                  file=sys.stderr)
        return 2
    if findings:
        print("FAIL: control-retirement: {} finding(s) against base {} ({})".format(len(findings), base, how))
        for finding in findings:
            print("  " + finding)
        return 1
    print("PASS: control-retirement: {} control(s) removed against base {} ({}), each with a recorded "
          "disposition".format(count, base, how))
    return 0


# ---------------------------------------------------------------------------------------------- self-test

BASE_GATES = ("ci-parity", "entry-guard")
BASE_HOOKS = ("gate-weakening-guard", "stop-guard")
BASE_SUITES = (("orch-behaviour-selftest", ("trunc/head-denies", "trunc/head-allows")),
               ("entry-guard-selftest", ("live/real-tree-passes", "live/direct-launch-passes")))
DROPPED = "check:orch-behaviour-selftest/trunc/head-denies"
SUCCESSOR = "check:orch-behaviour-selftest/trunc/head-refuses"
EVIDENCE = "case trunc/head-refuses exercised against the successor and observed to deny"
LEDGER_HEADER = "format-version = 1\n"


def _gates_text(ids):
    return "".join('[[gate]]\nid = "{}"\nscript = "tools/x.py"\n\n'.format(cid) for cid in ids)


def _hooks_text(ids):
    return '[plugin]\nname = "fixture"\n\n' + "".join(
        '[[hook]]\nid = "{}"\nhandler = "h_{}"\n\n'.format(cid, number) for number, cid in enumerate(ids, 1))


def _registry_text(suites):
    rows = "".join('\n[[suite]]\nid = "{}"\nrunner = "tools/{}.py"\nexpected-check-ids = [{}]\n'.format(
        sid, sid.replace("-", "_"), ", ".join(json.dumps(cid) for cid in ids)) for sid, ids in suites)
    return "format-version = 1\n" + rows


def _ledger_text(rows):
    return LEDGER_HEADER + "".join("\n[[retirement]]\n" + "".join(
        "{} = {}\n".format(key, json.dumps(value)) for key, value in row.items()) for row in rows)


def _extra_key_registry(suites):
    """_registry_text with a top-level key and a [[suite]] key the head schema does not know."""
    return 'schema-note = "fixture"\n' + _registry_text(suites).replace("\nrunner = ", '\nowner = "fixture"\nrunner = ')


def _no_scratch_home():
    raise FileNotFoundError(2, "No usable temporary directory found")


def _without(suites, suite_id, cid=None, add=None):
    """suites with one check id (or, when cid is None, the whole suite) removed, and an id optionally added."""
    kept = []
    for sid, ids in suites:
        if sid == suite_id and cid is None:
            continue
        if sid == suite_id:
            ids = tuple(i for i in ids if i != cid) + ((add,) if add else ())
        kept.append((sid, ids))
    return tuple(kept)


def _fixture_commit(root):
    # This broad staging is confined to a newly created fixture repository.
    subprocess.run(["git", "-C", str(root), "add", "--all", "--", "."],
                   check=True, capture_output=True, timeout=TIMEOUT, env=git_fixture_env())
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Selftest", "-c", "user.email=selftest@example.invalid",
                    "-c", "commit.gpgsign=false", "commit", "-q", "-m", "fixture"],
                   check=True, capture_output=True, timeout=TIMEOUT, env=git_fixture_env())


def _write(root, files):
    for rel, text in files.items():
        path = Path(root) / rel
        if text is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _snapshot(gates=BASE_GATES, hooks=BASE_HOOKS, suites=BASE_SUITES, ledger=()):
    return {GATES_REL: _gates_text(gates), HOOKS_REL: _hooks_text(hooks), REGISTRY_REL: _registry_text(suites),
            LEDGER_REL: None if ledger is None else _ledger_text(ledger)}


def _repo(base_dir, name, base, head, branch="main"):
    """A fixture repository: base committed on branch, head written over the working tree."""
    root = Path(base_dir) / name
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", branch, str(root)], check=True, capture_output=True,
                   timeout=TIMEOUT, env=git_fixture_env())
    _write(root, base)
    _fixture_commit(root)
    _write(root, head)
    return root


def _covered(control=DROPPED, successor=SUCCESSOR):
    return dict(control=control, state="covered", successor=successor, evidence=EVIDENCE)


def _fixtures(base_dir):
    """[(vector, root, environ, base_arg, expected exit, expected output fragments, patch or None)]."""
    vectors = []
    environ = {key: value for key, value in os.environ.items() if not key.startswith("GITHUB_")}

    def add(vector, base, head, code, texts, env=None, base_arg=None, branch="main", prepare=None, patch=None):
        # A preparer adjusts the fixture and the run environment; it may return a replacement root. A patch
        # (owner, attribute, replacement) is applied around the gate's run only.
        root = _repo(base_dir, vector.replace("/", "-"), base, head, branch)
        run_env = dict(environ, **(env or {}))
        if prepare is not None:
            root = prepare(root, run_env) or root
        vectors.append((vector, root, run_env, base_arg, code, texts, patch))

    base = _snapshot()
    dropped = _without(BASE_SUITES, "orch-behaviour-selftest", "trunc/head-denies")
    renamed = _without(BASE_SUITES, "orch-behaviour-selftest", "trunc/head-denies", "trunc/head-refuses")
    add("same/base-equals-head", base, _snapshot(), 0, ("0 control(s) removed",))
    add("drop/check-no-row", base, _snapshot(suites=dropped), 1, (DROPPED + ": present at base",))
    add("drop/check-covered", base, _snapshot(suites=renamed, ledger=[_covered()]), 0, ("1 control(s) removed",))
    add("covered/successor-absent", base, _snapshot(suites=dropped, ledger=[_covered()]), 1,
        ("successor " + SUCCESSOR + " is not a control live at head",))
    add("covered/successor-is-self", base, _snapshot(suites=dropped, ledger=[_covered(successor=DROPPED)]), 1,
        ("successor " + DROPPED + " is not a control live at head",))
    add("covered/no-evidence", base, _snapshot(suites=renamed, ledger=[
        dict(control=DROPPED, state="covered", successor=SUCCESSOR)]), 1, ("a row in state covered needs a non-empty evidence",))
    add("disclosed/no-authorization", base, _snapshot(suites=dropped, ledger=[
        dict(control=DROPPED, state="disclosed", evidence="no successor pins this diagnostic")]), 1,
        ("a row in state disclosed needs a non-empty authorization",))
    add("disclosed/blank-authorization", base, _snapshot(suites=dropped, ledger=[
        dict(control=DROPPED, state="disclosed", authorization="  ")]), 1,
        ("a row in state disclosed needs a non-empty authorization",))
    add("disclosed/authorized", base, _snapshot(suites=dropped, ledger=[
        dict(control=DROPPED, state="disclosed", authorization="maintainer authorization recorded in the change")]),
        0, ("1 control(s) removed",))
    # The optional evidence on a disclosed row: valid when present and well-formed (and valid when
    # omitted, the disclosed/authorized vector above), a finding when empty or carrying a control
    # character, cannot-evaluate when not a string at all.
    add("disclosed/evidence-valid", base, _snapshot(suites=dropped, ledger=[
        dict(control=DROPPED, state="disclosed", authorization="maintainer authorization recorded in the change",
             evidence="the removed check was exercised and the refusal observed")]), 0, ("1 control(s) removed",))
    add("disclosed/evidence-empty", base, _snapshot(suites=dropped, ledger=[
        dict(control=DROPPED, state="disclosed", authorization="recorded in the change", evidence="")]), 1,
        ("optional evidence on a disclosed row must be non-empty",))
    add("disclosed/evidence-control-char", base, _snapshot(suites=dropped, ledger=[
        dict(control=DROPPED, state="disclosed", authorization="recorded in the change", evidence="two\nlines")]),
        1, ("optional evidence on a disclosed row must be non-empty",))
    add("disclosed/evidence-int", base, _snapshot(suites=dropped, ledger=[
        dict(control=DROPPED, state="disclosed", authorization="recorded in the change", evidence=17)]), 2,
        ("row 1 field evidence must be a string, not int",))
    add("disclosed/evidence-list", base, _snapshot(suites=dropped, ledger=[
        dict(control=DROPPED, state="disclosed", authorization="recorded in the change", evidence=[])]), 2,
        ("row 1 field evidence must be a string, not list",))
    add("unreachable/no-evidence", base, _snapshot(suites=dropped, ledger=[
        dict(control=DROPPED, state="unreachable")]), 1, ("a row in state unreachable needs a non-empty evidence",))
    add("unreachable/with-evidence", base, _snapshot(suites=dropped, ledger=[
        dict(control=DROPPED, state="unreachable", evidence="the change removes the path; an attempt was refused")]),
        0, ("1 control(s) removed",))
    add("drop/hook-no-row", base, _snapshot(hooks=("gate-weakening-guard",)), 1,
        ("hook:stop-guard: present at base",))
    add("drop/gate-no-row", base, _snapshot(gates=("ci-parity",)), 1, ("gate:entry-guard: present at base",))
    add("drop/suite-no-row", base, _snapshot(suites=_without(BASE_SUITES, "entry-guard-selftest")), 1,
        ("check:entry-guard-selftest/live/real-tree-passes: present at base",
         "check:entry-guard-selftest/live/direct-launch-passes: present at base", "2 finding(s)"))
    add("rename/no-row", base, _snapshot(suites=renamed), 1, (DROPPED + ": present at base",))
    # The registry admits a whitespace-padded check id, so a row can name one.
    padded = BASE_SUITES[:1] + (("entry-guard-selftest", BASE_SUITES[1][1] + (" live/padded ",)),)
    add("rename/padded-check-id", _snapshot(suites=padded), _snapshot(ledger=[
        dict(control="check:entry-guard-selftest/ live/padded ", state="disclosed",
             authorization="recorded in the change")]), 0, ("1 control(s) removed",))
    add("row/names-live", base, _snapshot(ledger=[
        dict(control="gate:ci-parity", state="disclosed", authorization="recorded")]), 1,
        ("(gate:ci-parity): names a control still live at head",))
    add("row/duplicate", base, _snapshot(suites=renamed, ledger=[_covered(), _covered()]), 1,
        ("row 2 (" + DROPPED + "): duplicates row 1",))
    add("row/unknown-state", base, _snapshot(suites=dropped, ledger=[dict(control=DROPPED, state="retired")]), 1,
        ("state must be covered, unreachable or disclosed",))
    # A non-string row field (TOML array or inline table) cannot be hashed or compared, so it is refused
    # (exit 2) with a named diagnostic, never an uncaught TypeError; head and base rows both.
    add("row/state-array", base, _snapshot(suites=dropped, ledger=[
        dict(control=DROPPED, state=[], authorization="recorded")]), 2,
        ("head tools/control_retirements.toml: row 1 field state must be a string, not list",))
    add("row/state-table", base, dict(_snapshot(suites=dropped), **{
        LEDGER_REL: LEDGER_HEADER + '\n[[retirement]]\ncontrol = "' + DROPPED
        + '"\nstate = { kind = "disclosed" }\nauthorization = "recorded"\n'}), 2,
        ("head tools/control_retirements.toml: row 1 field state must be a string, not dict",))
    add("row/control-array", base, _snapshot(suites=dropped, ledger=[
        dict(control=[DROPPED], state="disclosed", authorization="recorded")]), 2,
        ("head tools/control_retirements.toml: row 1 field control must be a string, not list",))
    add("row/foreign-field", base, _snapshot(suites=renamed, ledger=[dict(_covered(), authorization="x")]), 1,
        ("authorization does not belong on a covered row",))
    add("row/unknown-field", base, _snapshot(suites=renamed, ledger=[dict(_covered(), reviewer="x")]), 1,
        ("unknown field(s) reviewer",))
    add("row/bad-control", base, _snapshot(suites=dropped, ledger=[
        dict(control="orch-behaviour-selftest/trunc/head-denies", state="disclosed", authorization="x")]), 1,
        ("row 1: control must be gate:<id>, hook:<id> or check:<suite>/<check id>", DROPPED + ": present at base"))
    # The same malformed control with nothing removed: the grammar finding is the only thing standing
    # between the junk row and a pass, so reverting the grammar check accepts this fixture.
    add("row/bad-control-only", base, _snapshot(ledger=[
        dict(control="orch-behaviour-selftest/trunc/head-denies", state="disclosed", authorization="x")]), 1,
        ("row 1: control must be gate:<id>, hook:<id> or check:<suite>/<check id>", "1 finding(s)"))
    add("row/names-nothing-removed", base, _snapshot(ledger=[
        dict(control="gate:never-existed", state="disclosed", authorization="recorded in the change")]), 1,
        ("(gate:never-existed): names no control live at base", "1 finding(s)"))
    history = dict(control="gate:retired-gate", state="disclosed", authorization="recorded earlier")
    add("ledger/history-kept", _snapshot(ledger=[history]), _snapshot(ledger=[history]), 0,
        ("0 control(s) removed",))
    add("ledger/append-edit", _snapshot(ledger=[history]), _snapshot(ledger=[dict(history, authorization="other")]),
        1, ("base row 1 (gate:retired-gate): edited or removed",))
    add("ledger/append-delete", _snapshot(ledger=[history]), _snapshot(), 1,
        ("base row 1 (gate:retired-gate): edited or removed",))
    add("ledger/readopted-row-deleted", _snapshot(gates=BASE_GATES, ledger=[history]),
        _snapshot(gates=BASE_GATES + ("retired-gate",)), 0, ("0 control(s) removed",))
    # A base row recorded while its control was still live at the base cannot stand in for a later
    # removal: the disposition for this range was never recorded, so reusing the stale row is a finding.
    add("ledger/base-row-live-at-base", _snapshot(ledger=[_covered()]),
        _snapshot(suites=renamed, ledger=[_covered()]), 1,
        ("base row 1 (" + DROPPED + "): names a control live at the base", "1 finding(s)"))
    # The repair for such a stale row: delete it while its control stays live. Nothing is removed in the
    # range and the append-only check allows deleting a row whose control is live at the head, so the
    # repair passes; binding the stale-row finding to the removal is what keeps this change acceptable.
    add("ledger/base-row-live-repair", _snapshot(ledger=[_covered()]), _snapshot(ledger=()), 0,
        ("0 control(s) removed",))
    # Keeping the stale row while its control lives stays refused through the head-row still-live check
    # alone: the stale-row finding is bound to the removal, so this is exactly one finding.
    stale = dict(control=DROPPED, state="disclosed", authorization="recorded ahead of any removal")
    add("ledger/base-row-live-kept", _snapshot(ledger=[stale]), _snapshot(ledger=[stale]), 1,
        ("names a control still live at head", "1 finding(s)"))
    # Non-string fields in base rows are refused the same way as in head rows (see row/state-array).
    add("ledger/base-control-array", _snapshot(ledger=[
        dict(control=["gate:x"], state="disclosed", authorization="recorded")]), _snapshot(), 2,
        ("base tools/control_retirements.toml: row 1 field control must be a string, not list",))
    add("ledger/base-state-table", dict(_snapshot(), **{
        LEDGER_REL: LEDGER_HEADER + '\n[[retirement]]\ncontrol = "gate:x"\n'
        'state = { kind = "disclosed" }\nauthorization = "recorded"\n'}), _snapshot(), 2,
        ("base tools/control_retirements.toml: row 1 field state must be a string, not dict",))
    # A base row with a non-string field, deleted at the head while its control is live there again (the
    # deletion the append-only check allows): the type refusal is all that stands between this record and a
    # pass, so this is the fixture a reverted type check accepts.
    add("ledger/base-evidence-list-row-deleted", _snapshot(ledger=[dict(history, evidence=["x"])]),
        _snapshot(gates=BASE_GATES + ("retired-gate",)), 2,
        ("base tools/control_retirements.toml: row 1 field evidence must be a string, not list",))
    two = _without(_without(BASE_SUITES, "orch-behaviour-selftest", "trunc/head-denies", "trunc/head-refuses"),
                   "entry-guard-selftest", "live/direct-launch-passes")
    second = dict(control="check:entry-guard-selftest/live/direct-launch-passes", state="unreachable",
                  evidence="the launch leg is removed with its runner; a launch attempt was refused")
    add("boundary/exact-rows", base, _snapshot(suites=two, ledger=[_covered(), second]), 0,
        ("2 control(s) removed",))
    add("boundary/one-short", base, _snapshot(suites=two, ledger=[_covered()]), 1,
        ("check:entry-guard-selftest/live/direct-launch-passes: present at base", "1 finding(s)"))
    add("boundary/extra-live-row", base, _snapshot(suites=two, ledger=[_covered(), second, dict(
        control="hook:stop-guard", state="disclosed", authorization="recorded")]), 1,
        ("(hook:stop-guard): names a control still live at head", "1 finding(s)"))
    # base binding and inputs that cannot be evaluated
    add("base/missing-main", base, _snapshot(suites=dropped), 2, ("refs/heads/main",), branch="trunk")
    add("base/unparseable-registry", dict(base, **{REGISTRY_REL: "[[suite]\n"}), _snapshot(), 2,
        ("base tools/selftest_checks.toml: malformed",))
    add("base/schema-invalid-registry", dict(base, **{REGISTRY_REL: "format-version = 2\n"}), _snapshot(), 2,
        ("base tools/selftest_checks.toml: [[suite]] must be a non-empty array of tables",))
    # The base is read by id only: keys the head schema does not know, and an id outside the head grammar,
    # still yield the base ids, so the removal is judged; the head stays held to its own schema.
    add("base/registry-extra-key", dict(base, **{REGISTRY_REL: _extra_key_registry(BASE_SUITES)}),
        _snapshot(suites=dropped), 1, (DROPPED + ": present at base", "1 finding(s)"))
    add("base/id-outside-head-grammar", _snapshot(gates=BASE_GATES + ("Legacy_Gate",)), _snapshot(ledger=[
        dict(control="gate:Legacy_Gate", state="disclosed", authorization="recorded in the change")]), 0,
        ("1 control(s) removed",))
    add("head/schema-invalid-registry", base, dict(_snapshot(), **{REGISTRY_REL: _extra_key_registry(BASE_SUITES)}),
        2, ("head tools/selftest_checks.toml: schema-invalid",))
    # The same schema-invalid head registry with a disclosed row for every base check id: read as an empty
    # check set, it would show every check removed and recorded, so this is the fixture a reverted schema
    # refusal accepts.
    every_check = ["check:{}/{}".format(sid, cid) for sid, ids in BASE_SUITES for cid in ids]
    add("head/schema-invalid-registry-all-recorded", base, dict(_snapshot(ledger=[
        dict(control=control, state="disclosed", authorization="recorded in the change") for control in every_check]),
        **{REGISTRY_REL: _extra_key_registry(BASE_SUITES)}), 2, ("head tools/selftest_checks.toml: schema-invalid",))
    add("base/gate-without-id", dict(base, **{GATES_REL: '[[gate]]\nscript = "tools/x.py"\n'}), _snapshot(), 2,
        ("base .aiqt/core/gates/manifest.toml: [[gate]] #1 has no usable id",))
    # The same id-less row at both ends: without the id check both snapshots collapse to the same unusable
    # key and nothing is removed, so this is the fixture a reverted id check accepts.
    add("base/gate-without-id-both-ends", dict(base, **{GATES_REL: '[[gate]]\nscript = "tools/x.py"\n'}),
        dict(_snapshot(), **{GATES_REL: '[[gate]]\nscript = "tools/x.py"\n'}), 2,
        ("base .aiqt/core/gates/manifest.toml: [[gate]] #1 has no usable id",))
    add("base/hooks-missing", dict(base, **{HOOKS_REL: None}), _snapshot(), 2,
        ("base .aiqt/core/hooks/manifest.toml: missing",))
    add("head/ledger-missing", base, _snapshot(ledger=None), 2, ("head tools/control_retirements.toml: missing",))
    add("head/ledger-malformed", base, dict(_snapshot(), **{LEDGER_REL: "format-version = 1\n[[retirement]\n"}), 2,
        ("head tools/control_retirements.toml: malformed",))
    add("head/ledger-format-version", base, dict(_snapshot(), **{LEDGER_REL: "format-version = 2\n"}), 2,
        ("format-version must be exactly the integer 1",))
    # The duplicated id sits beside the full base set, so the set a reverted duplicate check would
    # deduplicate to equals the base set exactly: the revert turns this refusal into an acceptance.
    add("head/duplicate-hook-id", base, dict(_snapshot(), **{
        HOOKS_REL: _hooks_text(("gate-weakening-guard", "stop-guard", "stop-guard"))}),
        2, ("duplicate hook id 'stop-guard'",))
    add("local/base-arg", base, _snapshot(suites=dropped), 1, (DROPPED + ": present at base", "--base HEAD"),
        base_arg="HEAD", branch="trunk")
    add("local/base-arg-option", base, _snapshot(), 2, ("is not a usable commit name",), base_arg="--all")

    def dash_tag(root, run_env):
        # A tag named with a leading '-' resolves after --end-of-options, so without the leading-dash refusal
        # --base -probe would bind the base to that tag and pass.
        subprocess.run(["git", "-C", str(root), "update-ref", "refs/tags/-probe", "HEAD"],
                       check=True, capture_output=True, timeout=TIMEOUT, env=git_fixture_env())

    add("local/base-arg-dash-tag", base, _snapshot(), 2, ("revision '-probe' is not a usable commit name",),
        base_arg="-probe", prepare=dash_tag)

    def committed_drop(root, run_env):
        # The removal is already committed on a feature branch; only refs/heads/main still holds the base.
        # Binding the local base to HEAD instead of main would judge the head against itself and pass.
        subprocess.run(["git", "-C", str(root), "switch", "-q", "-c", "feature"],
                       check=True, capture_output=True, timeout=TIMEOUT, env=git_fixture_env())
        _write(root, _snapshot(suites=dropped))
        _fixture_commit(root)

    add("local/committed-drop-no-row", base, {}, 1,
        (DROPPED + ": present at base", "merge base of HEAD and " + LOCAL_TARGET), prepare=committed_drop)
    add("env/scratch-home-unavailable", base, _snapshot(), 2, ("scratch home not created",),
        patch=(_git_fixture_env, "_scratch_home", _no_scratch_home))

    def shallow(root, run_env):
        # Two commits, then a depth-1 clone over file://, which git marks shallow; the clone replaces the root.
        _write(root, {"later.txt": "later\n"})
        _fixture_commit(root)
        clone = root.with_name(root.name + "-clone")
        subprocess.run(["git", "clone", "-q", "--depth", "1", "file://" + str(root), str(clone)],
                       check=True, capture_output=True, timeout=TIMEOUT, env=git_fixture_env())
        return clone

    add("base/shallow", base, {}, 2, ("shallow",), prepare=shallow)

    def origin_main(root, run_env):
        # The pull request target is the base commit under origin/main; no local main remains.
        subprocess.run(["git", "-C", str(root), "update-ref", "refs/remotes/origin/main", "HEAD"],
                       check=True, capture_output=True, timeout=TIMEOUT, env=git_fixture_env())
        subprocess.run(["git", "-C", str(root), "branch", "-q", "-m", "main", "feature"],
                       check=True, capture_output=True, timeout=TIMEOUT, env=git_fixture_env())

    pr = dict(GITHUB_ACTIONS="true", GITHUB_EVENT_NAME="pull_request", GITHUB_BASE_REF="main")
    add("ci/pr-drop-no-row", base, _snapshot(suites=dropped), 1,
        (DROPPED + ": present at base", "merge base of HEAD and refs/remotes/origin/main"), env=pr,
        prepare=origin_main)
    add("ci/pr-base-arg-refused", base, _snapshot(), 2, ("--base is refused",), env=pr, base_arg="HEAD",
        prepare=origin_main)
    add("ci/pr-no-base-ref", base, _snapshot(), 2, ("without a usable GITHUB_BASE_REF",),
        env=dict(pr, GITHUB_BASE_REF=""))
    add("ci/pr-base-ref-unresolved", base, _snapshot(), 2, ("refs/remotes/origin/main",), env=pr)
    # Revision syntax in GITHUB_BASE_REF: main~0 is not a branch name but still resolves under git's
    # revision grammar, so without the branch-name check the gate would bind the base to it and pass.
    add("ci/pr-base-ref-revision-syntax", base, _snapshot(), 2, ("without a usable GITHUB_BASE_REF",),
        env=dict(pr, GITHUB_BASE_REF="main~0"), prepare=origin_main)

    def payload(before):
        def prepare(root, run_env):
            oid = _commit(root, _git_env(), "HEAD") if before is None else before
            path = Path(base_dir) / (root.name + "-event.json")
            path.write_text(json.dumps({"before": oid}), encoding="utf-8")
            run_env["GITHUB_EVENT_PATH"] = str(path)
        return prepare

    push = dict(GITHUB_ACTIONS="true", GITHUB_EVENT_NAME="push")
    add("ci/push-drop-no-row", base, _snapshot(suites=dropped), 1, (DROPPED + ": present at base",
                                                                    "push before commit"),
        env=push, prepare=payload(None))
    add("ci/push-zero-before", base, _snapshot(), 2, ("no usable before commit",), env=push,
        prepare=payload("0" * 40))

    def zero_named_ref(root, run_env):
        # An all-zero before of the other hash width is not a full object id in this repository, so git looks
        # it up as a ref name: with a branch of that name, a reverted zero check would bind the base to it and
        # pass.
        width = 64 if len(_commit(root, _git_env(), "HEAD")) == 40 else 40
        subprocess.run(["git", "-C", str(root), "update-ref", "refs/heads/" + "0" * width, "HEAD"],
                       check=True, capture_output=True, timeout=TIMEOUT, env=git_fixture_env())
        payload("0" * width)(root, run_env)

    add("ci/push-zero-before-named-ref", base, _snapshot(), 2, ("no usable before commit",), env=push,
        prepare=zero_named_ref)
    add("ci/push-no-payload", base, _snapshot(), 2, ("without GITHUB_EVENT_PATH",), env=push)
    add("ci/push-base-arg-refused", base, _snapshot(), 2, ("--base is refused",), env=push, base_arg="HEAD",
        prepare=payload(None))
    add("ci/unsupported-event", base, _snapshot(), 2, ("unsupported GitHub Actions event 'workflow_dispatch'",),
        env=dict(push, GITHUB_EVENT_NAME="workflow_dispatch"))
    # The same unsupported event carrying a usable push payload: without the event check the gate would
    # read the payload as a push base and pass, so this is the fixture a reverted event check accepts.
    add("ci/dispatch-with-payload", base, _snapshot(), 2,
        ("unsupported GitHub Actions event 'workflow_dispatch'",),
        env=dict(push, GITHUB_EVENT_NAME="workflow_dispatch"), prepare=payload(None))
    return vectors


def _gate(module, vector):
    """Run one vector against module (this gate or a mutant), its patch applied around the run: (exit code or
    "crashed (...)", output)."""
    _name, root, environ, base_arg, _code, _texts, patch = vector
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            with contextlib.nullcontext() if patch is None else _patched(*patch):
                code = module.run(root, base_arg, environ)
    except Exception as exc:
        code = "crashed ({})".format(type(exc).__name__)
    return code, out.getvalue()


def _run_vectors(module, vectors):
    """{vector: (ok, detail)} for every vector against module."""
    results = {}
    for vector in vectors:
        code, output = _gate(module, vector)
        results[vector[0]] = (code == vector[4] and all(text in output for text in vector[5]), (code, output))
    return results


@contextlib.contextmanager
def _patched(owner, name, replacement):
    original = getattr(owner, name)
    setattr(owner, name, replacement)
    try:
        yield
    finally:
        setattr(owner, name, original)


# Each mutant reverts one check (or one fail-closed path) in the gate half of this file's source, and the
# self-test loads it as a scratch module. The mutants are separated by what a revert is shown to do, not by
# where its line sits: a revert under which some input the gate refuses is accepted is an enforcement
# revert, wherever it lives.
#
# ENFORCEMENT_MUTANTS (mutant id, the exact source text, its replacement, an invalid vector): a mutant
# reverting an acceptance-guarding check counts as caught ONLY when its named invalid fixture becomes
# ACCEPTED (exit 0) under it, so the self-test goes red because of that acceptance. A mutant that merely
# crashes, or merely changes a diagnostic while the fixture stays rejected, earns no credit here.
#
# DIAGNOSTIC_MUTANTS (mutant id, the exact source text, its replacement, a vector, the exact exit under the
# mutant, output fragments under the mutant): a revert that changes only a refusal message, an exception
# handler or a crash class, or that narrows what the gate accepts (a stricter grammar or schema, or the
# stale-row repair re-tightened), so it refuses a valid fixture but accepts no invalid one; the comment
# above each row states which. Each is pinned to its exact outcome under the mutant, never loosely counted
# as caught. The class boundary is checked only against the vectors: under every diagnostic mutant, no
# vector the gate refuses may be accepted, so a revert whose accepting input is among the vectors cannot
# sit in this class. An accepting input outside the vectors is not searched for; the reason stated on each
# row is a review argument, not a proof, and a revert found to accept such an input moves to the
# enforcement class with that input added as its vector.
ENFORCEMENT_MUTANTS = (
    ("removal-leg", "        if control not in rows_by_control:\n", "        if False:\n", "drop/check-no-row"),
    ("gate-set", '    live |= control_ids(_required(read, where, GATES_REL), "gate", "gate", where + " " + GATES_REL,'
     " id_ok)\n", "", "drop/gate-no-row"),
    ("hook-set", '    live |= control_ids(_required(read, where, HOOKS_REL), "hook", "hook", where + " " + HOOKS_REL,'
     " id_ok)\n", "", "drop/hook-no-row"),
    ("check-set", '    live |= registry(_required(read, where, REGISTRY_REL), where + " " + REGISTRY_REL)\n', "",
     "drop/suite-no-row"),
    ("successor-live", "        if successor not in head_live:\n", "        if False:\n", "covered/successor-absent"),
    ("required-field", "        if not _text(row.get(key)):\n            findings.append(\"{}: a row in state",
     "        if row.get(key) is None:\n            findings.append(\"{}: a row in state",
     "disclosed/blank-authorization"),
    ("required-present", "        if not _text(row.get(key)):\n            findings.append(\"{}: a row in state",
     "        if False:\n            findings.append(\"{}: a row in state",
     "disclosed/no-authorization"),
    ("optional-field", "    for key in sorted(set(row) & optional):\n", "    for key in ():\n",
     "disclosed/evidence-empty"),
    ("live-row", "    if control in head_live:\n", "    if False:\n", "row/names-live"),
    ("duplicate-row", "            if control in rows_by_control:\n", "            if False:\n", "row/duplicate"),
    ("state-vocabulary", "    if state not in STATE_FIELDS:\n        findings.append(\"{}: state must be covered, "
     "unreachable or disclosed\".format(where))\n        return findings\n",
     "    if state not in STATE_FIELDS:\n        return findings\n", "row/unknown-state"),
    ("foreign-field", '    for key in sorted((set(row) & ROW_KEYS) - {"control", "state"} - required - optional):\n',
     "    for key in ():\n", "row/foreign-field"),
    ("unknown-field", "    if unknown:\n", "    if False:\n", "row/unknown-field"),
    ("control-grammar", "    if not isinstance(control, str) or not control_key_ok(control):\n",
     "    if not isinstance(control, str):\n", "row/bad-control-only"),
    ("new-row-removal", "            if (control_key_ok(control) and control not in head_live and control not in base_live\n",
     "            if (False\n", "row/names-nothing-removed"),
    ("append-only", "        if row not in head_rows and row.get(\"control\") not in head_live:\n",
     "        if False:\n", "ledger/append-edit"),
    ("base-row-live",
     "        if isinstance(base_control, str) and base_control in base_live and base_control not in head_live:\n",
     "        if False:\n", "ledger/base-row-live-at-base"),
    ("base-registry-rows",
     "        raise CannotEvaluate(\"{}: [[suite]] must be a non-empty array of tables\".format(label))\n",
     "        return set()\n", "base/schema-invalid-registry"),
    ("head-ledger-required", "    if head_data is None:\n        raise", "    if head_data is None and False:\n        raise",
     "head/ledger-missing"),
    ("ledger-version", "    if type(parsed[\"format-version\"]) is not int or parsed[\"format-version\"] != 1:\n",
     "    if False:\n", "head/ledger-format-version"),
    ("duplicate-id", "        if key in keys:\n", "        if False:\n", "head/duplicate-hook-id"),
    ("shallow-refused", '    if _rev_parse(root, env, "--is-shallow-repository") != b"false\\n":\n',
     "    if False:\n", "base/shallow"),
    ("pr-base-arg", "            raise CannotEvaluate(\"a pull request derives its base from GITHUB_BASE_REF; --base is refused\")\n",
     "            pass\n", "ci/pr-base-arg-refused"),
    ("pr-binding", 'target = "refs/remotes/origin/" + base_ref', 'target = "HEAD"', "ci/pr-base-ref-unresolved"),
    ("push-base-arg", "            raise CannotEvaluate(\"a push derives its base from the event payload; --base is refused\")\n",
     "            pass\n", "ci/push-base-arg-refused"),
    ("local-target", 'LOCAL_TARGET = "refs/heads/main"', 'LOCAL_TARGET = "HEAD"', "local/committed-drop-no-row"),
    ("control-id", "        if not isinstance(cid, str) or not id_ok(cid):\n", "        if False:\n",
     "base/gate-without-id-both-ends"),
    ("actions-event", '        if event != "push":\n', "        if False:\n", "ci/dispatch-with-payload"),
    ("pr-base-ref", '        if not BRANCH_RE.fullmatch(base_ref) or ".." in base_ref or base_ref.endswith((".lock", "/")):\n',
     "        if False:\n", "ci/pr-base-ref-revision-syntax"),
    ("row-field-type", "            if not isinstance(row[key], str):\n", "            if False:\n",
     "ledger/base-evidence-list-row-deleted"),
    ("registry-schema", "    if rows is None:\n        raise CannotEvaluate(\"{}: schema-invalid",
     "    if rows is None:\n        return set()\n        raise CannotEvaluate(\"{}: schema-invalid",
     "head/schema-invalid-registry-all-recorded"),
    ("option-revision", 'if not revision or revision.startswith("-"):', "if not revision:", "local/base-arg-dash-tag"),
    ("push-before-zero", 'or not before.strip("0"):', ":", "ci/push-zero-before-named-ref"),
)
DIAGNOSTIC_MUTANTS = (
    # Narrows the gate and hook id grammar a row's control is read by: a row the narrowed grammar rejects
    # draws the control-grammar finding, which stands in for the new-row finding it skips.
    ("row-id-grammar", "        return bool(sep) and _id_text(rest)\n", "        return bool(sep) and _head_id_ok(rest)\n",
     "base/id-outside-head-grammar", 1,
     ("row 1: control must be gate:<id>, hook:<id> or check:<suite>/<check id>",)),
    # Narrows the check id grammar a row's control is read by, the same way.
    ("padded-id", "        return bool(sep) and _id_text(suite) and bool(slash) and _id_text(cid)\n",
     "        return bool(sep) and _id_text(suite) and bool(slash) and _id_text(cid) and cid == cid.strip()\n",
     "rename/padded-check-id", 1, ("control must be gate:<id>, hook:<id> or check:<suite>/<check id>",)),
    # Changes an exception handler: a malformed TOML input crashes the run (exit 1 from the interpreter).
    ("toml-parse", "    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:\n"
     "        raise CannotEvaluate(\"{}: malformed ({})\".format(label, type(exc).__name__))\n",
     "    except ZeroDivisionError as exc:\n        raise CannotEvaluate(str(exc))\n", "base/unparseable-registry",
     "crashed (TOMLDecodeError)", ()),
    # Narrows the base registry reader to the head schema, which refuses every registry the by-id reader
    # refuses and otherwise yields the same check keys.
    ("base-registry-by-id", "    registry = check_keys if head else base_check_keys\n", "    registry = check_keys\n",
     "base/registry-extra-key", 2, ("base tools/selftest_checks.toml: schema-invalid",)),
    # Narrows the base gate and hook id grammar: a base id it rejects is refused, never skipped.
    ("base-id-grammar", "    id_ok = _head_id_ok if head else _id_text\n", "    id_ok = _head_id_ok\n",
     "base/id-outside-head-grammar", 2, ("base .aiqt/core/gates/manifest.toml: [[gate]] #3 has no usable id",)),
    # Changes an exception handler: an unavailable scratch home crashes the run.
    ("scratch-home-refused", "    except OSError as exc:\n        raise CannotEvaluate(\"hermetic git environment",
     "    except ZeroDivisionError as exc:\n        raise CannotEvaluate(\"hermetic git environment",
     "env/scratch-home-unavailable", "crashed (FileNotFoundError)", ()),
    # Changes a refusal message: a missing input read as empty TOML is still refused by its reader, since
    # every reader requires a non-empty array of rows.
    ("input-required", "    if data is None:\n        raise CannotEvaluate(\"{} {}: missing\".format(where, rel))\n",
     "    if data is None:\n        data = b\"\"\n", "base/hooks-missing", 2,
     ("base .aiqt/core/hooks/manifest.toml: [[hook]] must be a non-empty array of tables",)),
    # Changes a refusal message: an empty event path still fails to open, as an unreadable payload.
    ("push-payload", '    if not path:\n        raise CannotEvaluate("push event without GITHUB_EVENT_PATH")\n', "",
     "ci/push-no-payload", 2, ("push event payload unreadable",)),
    # Re-tightens the stale-row repair: it only adds a finding (the repair is refused again), never removes one.
    ("base-row-repair",
     "        if isinstance(base_control, str) and base_control in base_live and base_control not in head_live:\n",
     "        if isinstance(base_control, str) and base_control in base_live:\n",
     "ledger/base-row-live-repair", 1, ("names a control live at the base",)),
)


def _load_mutant(source, directory, number):
    """Write one mutant module to the scratch directory and load it with importlib."""
    path = Path(directory) / "control_retirement_mutant_{}.py".format(number)
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("control_retirement_mutant_{}".format(number), path)
    module = importlib.util.module_from_spec(spec)
    saved = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = saved
    return module


MALFORMED_CONFIG = "[core\n\tbroken\n"
# The vectors run again under a poisoned caller git configuration: a pass, a finding, the push binding, a
# shallow repository and an unresolvable base.
POISONED_VECTORS = ("same/base-equals-head", "drop/check-no-row", "ci/push-drop-no-row", "base/shallow",
                    "ci/pr-base-ref-unresolved")
# The caller configuration surfaces, each with the variable that selects it.
POISON_SURFACES = (("home", "HOME"), ("system", "GIT_CONFIG_SYSTEM"), ("xdg", "XDG_CONFIG_HOME"))


def _poisoned_config(base_dir, git):
    """(environment changes, environment changes per surface, malformed file per surface, exposure log) for a
    caller git configuration that breaks a read honouring it: HOME/.gitconfig, XDG_CONFIG_HOME/git/config and
    the GIT_CONFIG_SYSTEM file are each malformed. The first changes poison all three surfaces; the changes for
    one surface poison only it, with HOME and XDG_CONFIG_HOME otherwise at an empty directory and the system
    file otherwise os.devnull. An observer git placed first on PATH records launch for every launch, and home,
    xdg or system when the launch would read that surface (the system file whenever neither
    GIT_CONFIG_NOSYSTEM=1 nor GIT_CONFIG_SYSTEM=/dev/null pins it off), then runs git, the executable the
    lifecycle's PATH wrapper runs, so no pin of that wrapper stands in for the gate's own."""
    poison = Path(base_dir) / "caller-config"
    home, xdg, clean = poison / "home", poison / "xdg", poison / "clean"
    files = dict(home=home / ".gitconfig", xdg=xdg / "git" / "config", system=poison / "system-gitconfig")
    (xdg / "git").mkdir(parents=True)
    home.mkdir()
    clean.mkdir()
    for path in files.values():
        path.write_text(MALFORMED_CONFIG, encoding="utf-8")
    observer_dir = poison / "bin"
    observer_dir.mkdir()
    log = poison / "exposure"
    log.write_bytes(b"")
    observer = observer_dir / "git"
    observer.write_text(
        "#!/bin/sh\n"
        'printf "launch\\n" >> {log}\n'
        '[ "${{HOME:-}}" != {home} ] || printf "home\\n" >> {log}\n'
        '[ "${{XDG_CONFIG_HOME:-}}" != {xdg} ] || printf "xdg\\n" >> {log}\n'
        'if [ "${{GIT_CONFIG_NOSYSTEM:-}}" != 1 ] && [ "${{GIT_CONFIG_SYSTEM:-}}" != {null} ]; then '
        'printf "system\\n" >> {log}; fi\n'
        'exec {git} "$@"\n'.format(home=shlex.quote(str(home)), xdg=shlex.quote(str(xdg)),
                                   null=shlex.quote(os.devnull), log=shlex.quote(str(log)),
                                   git=shlex.quote(git)), encoding="utf-8")
    observer.chmod(0o700)
    changes = dict(HOME=str(home), XDG_CONFIG_HOME=str(xdg), GIT_CONFIG_SYSTEM=str(files["system"]),
                   PATH=str(observer_dir) + os.pathsep + os.environ.get("PATH", os.defpath))
    unpoisoned = dict(changes, HOME=str(clean), XDG_CONFIG_HOME=str(clean), GIT_CONFIG_SYSTEM=os.devnull)
    surfaces = dict((surface, dict(unpoisoned, **{key: changes[key]})) for surface, key in POISON_SURFACES)
    return changes, surfaces, files, log


def _poisoned(environ, changes):
    """environ with the poisoned configuration applied and the global and system pins removed."""
    env = {key: value for key, value in environ.items() if key not in ("GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL")}
    env.update(changes)
    return env


def _poisoned_gate(module, vector, changes):
    """_gate with the poisoned configuration in the run environment and in the process environment, so a git
    environment derived from either carries it; the process environment is restored afterwards."""
    name, root, environ, base_arg, code, texts, patch = vector
    saved = dict(os.environ)
    try:
        poisoned = _poisoned(saved, changes)
        os.environ.clear()
        os.environ.update(poisoned)
        return _gate(module, (name, root, _poisoned(environ, changes), base_arg, code, texts, patch))
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _registered_check_ids():
    """This suite's registered check ids, read through the execution gate's schema validator: (set of ids,
    None), or (None, reason)."""
    try:
        data = tomllib.loads((ROOT / REGISTRY_REL).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        return None, "{}: {}".format(REGISTRY_REL, exc)
    with contextlib.redirect_stderr(io.StringIO()):
        rows = check_selftest_execution._manifest_rows(data, REGISTRY_REL)
    row = next((row for row in rows or () if row["id"] == SUITE_ID), None)
    if row is None:
        return None, "{} registers no valid suite {}".format(REGISTRY_REL, SUITE_ID)
    return set(row["expected-check-ids"]), None


def self_test(report_path=None):
    """Fixture git calls run under the hermetic fixture environment, gate reads included."""
    executed = []
    if report_path is not None:
        # The execution report is finalized at interpreter exit, after this run's cleanup
        # (tools/_selftest_exit_report.py); nothing writes it in band. It is armed before the fixture
        # environment, whose scratch home registers its own exit handler, so the finalizer runs last.
        _selftest_exit_report.arm(report_path, SUITE_ID, executed)
    # The git the lifecycle's PATH wrapper runs, resolved the same way before the wrapper is first on PATH; the
    # poison observer runs it directly. Without git the lifecycle refuses before this is used.
    real_git = shutil.which("git")
    with fixture_git_lifecycle():
        return _self_test_isolated(executed, os.path.abspath(real_git))


def _self_test_isolated(executed, real_git):
    results = []

    def check(check_id, ok, detail=""):
        results.append((check_id, bool(ok), detail))
        executed.append(check_id)

    this = sys.modules[__name__]
    with tempfile.TemporaryDirectory(prefix="control-retirement-selftest-") as base_dir:
        try:
            vectors = _fixtures(base_dir)
            harness_problem = None
        except (OSError, subprocess.SubprocessError, CannotEvaluate) as exc:
            vectors, harness_problem = [], "fixture build failed: {}".format(exc)
        outcome = _run_vectors(this, vectors)
        missing = (False, harness_problem or "vector not built")
        checked = []
        # 1. every vector holds against this gate
        for check_id, vector in (
                ("vector/same-base-equals-head", "same/base-equals-head"),
                ("vector/drop-check-no-row", "drop/check-no-row"),
                ("vector/drop-check-covered", "drop/check-covered"),
                ("vector/covered-successor-absent", "covered/successor-absent"),
                ("vector/covered-successor-is-self", "covered/successor-is-self"),
                ("vector/covered-no-evidence", "covered/no-evidence"),
                ("vector/disclosed-no-authorization", "disclosed/no-authorization"),
                ("vector/disclosed-blank-authorization", "disclosed/blank-authorization"),
                ("vector/disclosed-authorized", "disclosed/authorized"),
                ("vector/disclosed-evidence-valid", "disclosed/evidence-valid"),
                ("vector/disclosed-evidence-empty", "disclosed/evidence-empty"),
                ("vector/disclosed-evidence-control-char", "disclosed/evidence-control-char"),
                ("vector/disclosed-evidence-int", "disclosed/evidence-int"),
                ("vector/disclosed-evidence-list", "disclosed/evidence-list"),
                ("vector/unreachable-no-evidence", "unreachable/no-evidence"),
                ("vector/unreachable-with-evidence", "unreachable/with-evidence"),
                ("vector/drop-hook-no-row", "drop/hook-no-row"),
                ("vector/drop-gate-no-row", "drop/gate-no-row"),
                ("vector/drop-suite-no-row", "drop/suite-no-row"),
                ("vector/rename-no-row", "rename/no-row"),
                ("vector/rename-padded-check-id", "rename/padded-check-id"),
                ("vector/row-names-live", "row/names-live"),
                ("vector/row-duplicate", "row/duplicate"),
                ("vector/row-unknown-state", "row/unknown-state"),
                ("vector/row-state-array", "row/state-array"),
                ("vector/row-state-table", "row/state-table"),
                ("vector/row-control-array", "row/control-array"),
                ("vector/row-foreign-field", "row/foreign-field"),
                ("vector/row-unknown-field", "row/unknown-field"),
                ("vector/row-bad-control", "row/bad-control"),
                ("vector/row-bad-control-only", "row/bad-control-only"),
                ("vector/row-names-nothing-removed", "row/names-nothing-removed"),
                ("vector/ledger-history-kept", "ledger/history-kept"),
                ("vector/ledger-append-edit", "ledger/append-edit"),
                ("vector/ledger-append-delete", "ledger/append-delete"),
                ("vector/ledger-readopted-row-deleted", "ledger/readopted-row-deleted"),
                ("vector/ledger-base-row-live-at-base", "ledger/base-row-live-at-base"),
                ("vector/ledger-base-row-live-repair", "ledger/base-row-live-repair"),
                ("vector/ledger-base-row-live-kept", "ledger/base-row-live-kept"),
                ("vector/ledger-base-control-array", "ledger/base-control-array"),
                ("vector/ledger-base-state-table", "ledger/base-state-table"),
                ("vector/ledger-base-evidence-list-row-deleted", "ledger/base-evidence-list-row-deleted"),
                ("vector/boundary-exact-rows", "boundary/exact-rows"),
                ("vector/boundary-one-short", "boundary/one-short"),
                ("vector/boundary-extra-live-row", "boundary/extra-live-row"),
                ("vector/base-missing-main", "base/missing-main"),
                ("vector/base-unparseable-registry", "base/unparseable-registry"),
                ("vector/base-schema-invalid-registry", "base/schema-invalid-registry"),
                ("vector/base-registry-extra-key", "base/registry-extra-key"),
                ("vector/base-id-outside-head-grammar", "base/id-outside-head-grammar"),
                ("vector/head-schema-invalid-registry", "head/schema-invalid-registry"),
                ("vector/head-schema-invalid-registry-all-recorded", "head/schema-invalid-registry-all-recorded"),
                ("vector/base-gate-without-id", "base/gate-without-id"),
                ("vector/base-gate-without-id-both-ends", "base/gate-without-id-both-ends"),
                ("vector/base-hooks-missing", "base/hooks-missing"),
                ("vector/base-shallow", "base/shallow"),
                ("vector/head-ledger-missing", "head/ledger-missing"),
                ("vector/head-ledger-malformed", "head/ledger-malformed"),
                ("vector/head-ledger-format-version", "head/ledger-format-version"),
                ("vector/head-duplicate-hook-id", "head/duplicate-hook-id"),
                ("vector/local-base-arg", "local/base-arg"),
                ("vector/local-base-arg-option", "local/base-arg-option"),
                ("vector/local-base-arg-dash-tag", "local/base-arg-dash-tag"),
                ("vector/local-committed-drop-no-row", "local/committed-drop-no-row"),
                ("vector/env-scratch-home-unavailable", "env/scratch-home-unavailable"),
                ("vector/ci-pr-drop-no-row", "ci/pr-drop-no-row"),
                ("vector/ci-pr-base-arg-refused", "ci/pr-base-arg-refused"),
                ("vector/ci-pr-no-base-ref", "ci/pr-no-base-ref"),
                ("vector/ci-pr-base-ref-unresolved", "ci/pr-base-ref-unresolved"),
                ("vector/ci-pr-base-ref-revision-syntax", "ci/pr-base-ref-revision-syntax"),
                ("vector/ci-push-drop-no-row", "ci/push-drop-no-row"),
                ("vector/ci-push-zero-before", "ci/push-zero-before"),
                ("vector/ci-push-zero-before-named-ref", "ci/push-zero-before-named-ref"),
                ("vector/ci-push-no-payload", "ci/push-no-payload"),
                ("vector/ci-push-base-arg-refused", "ci/push-base-arg-refused"),
                ("vector/ci-unsupported-event", "ci/unsupported-event"),
                ("vector/ci-dispatch-with-payload", "ci/dispatch-with-payload")):
            checked.append(vector)
            check(check_id, *outcome.get(vector, missing))
        by_name = dict((vector[0], vector) for vector in vectors)
        check("vector/every-built-vector-checked", harness_problem is None and sorted(checked) == sorted(by_name)
              and len(by_name) == len(vectors), (harness_problem, sorted(set(by_name) ^ set(checked))))

        # 2. red on revert, by attribute: with the removal set patched to empty, the dropped-check vector turns
        # green (exit 0), so the vector that holds it fails
        reverted = (None, "vector not built")
        if "drop/check-no-row" in by_name:
            with _patched(this, "removed_controls", lambda base_live, head_live: set()):
                reverted = _gate(this, by_name["drop/check-no-row"])
        check("revert/empty-removal-set-turns-drop-green", reverted[0] == 0 and "PASS" in reverted[1], reverted)

        # 3. honest mutants, separated by class (see the tables). An enforcement mutant is caught only when
        # its named invalid fixture becomes ACCEPTED (exit 0) under it; a crash or a changed diagnostic
        # earns no credit. A diagnostic or exception-handling mutant is pinned to its exact outcome under
        # the mutant, and under it no vector the gate refuses may be accepted (the class boundary, checked
        # against every vector). The discrimination check keeps its weaker reading on purpose: every vector
        # expecting a nonzero exit deviates from its expectation under at least one mutant of either class.
        gate_source, marker, rest = Path(__file__).read_text(encoding="utf-8").partition("\nBASE_GATES = ")
        exposed, unaccepted, mismatched, accepting = set(), [], [], []
        mutant_dir = Path(base_dir) / "mutants"
        mutant_dir.mkdir()

        def mutant_results(old, new, number):
            if not marker or gate_source.count(old) != 1:
                return None, "anchor found {} times".format(gate_source.count(old))
            module = _load_mutant(gate_source.replace(old, new) + marker + rest, mutant_dir, number)
            outcomes = _run_vectors(module, vectors)
            exposed.update(name for name, (ok, _detail) in outcomes.items() if not ok)
            return outcomes, None

        for number, (mutant, old, new, vector) in enumerate(ENFORCEMENT_MUTANTS, 1):
            outcomes, problem = mutant_results(old, new, number)
            if problem is not None or vector not in by_name or by_name[vector][4] == 0:
                unaccepted.append((mutant, vector, problem or "no vector expecting a nonzero exit"))
                continue
            code = outcomes[vector][1][0]
            if code != 0:
                unaccepted.append((mutant, vector, "not accepted under the mutant", code))
        check("mutant/every-mutant-caught", vectors and not unaccepted, unaccepted)
        for number, (mutant, old, new, vector, code, texts) in enumerate(
                DIAGNOSTIC_MUTANTS, len(ENFORCEMENT_MUTANTS) + 1):
            outcomes, problem = mutant_results(old, new, number)
            if problem is not None:
                mismatched.append((mutant, vector, problem))
                accepting.append((mutant, problem))
                continue
            accepted = sorted(name for name, (_ok, (got, _output)) in outcomes.items()
                              if got == 0 and by_name[name][4] != 0)
            if accepted:
                accepting.append((mutant, accepted))
            if vector not in by_name:
                mismatched.append((mutant, vector, "vector not built"))
                continue
            got_code, got_output = outcomes[vector][1]
            if got_code != code or not all(text in got_output for text in texts):
                mismatched.append((mutant, vector, got_code, got_output))
        check("mutant/every-diagnostic-mutant-exact-outcome", vectors and not mismatched, mismatched)
        check("mutant/no-diagnostic-mutant-accepts", vectors and not accepting, accepting)
        never = sorted(vector[0] for vector in vectors if vector[4] != 0 and vector[0] not in exposed)
        check("mutant/every-refusing-vector-discriminates", vectors and not never, never)

        # 3b. the ownership refusal names who can act: git's safe.directory advice suggests a global config
        # entry the gate pins to os.devnull, so the refusal adds that only the checkout owner or CI can
        # evaluate this checkout; every other refusal stays without the note. The refusal class is set only
        # on a line git itself begins, so an echoed argument carrying the phrase, the strongest spoof a
        # caller-supplied revision can produce, stays an ordinary refusal.
        ownership_stderr = (b"fatal: detected dubious ownership in repository at '/x'\n"
                            b"To add an exception for this directory, call:\n\n"
                            b"\tgit config --global --add safe.directory /x\n")
        spoof_stderr = (b"fatal: ambiguous argument 'fatal: detected dubious ownership in repository at': "
                        b"unknown revision or path not in the working tree.\n")

        def _classify(stderr):
            try:
                _finish(subprocess.CompletedProcess([], 128, b"", stderr), "git rev-parse")
            except CannotEvaluate as exc:
                return type(exc).__name__
            return "no exception"

        check("diagnostic/ownership-classified",
              (_classify(ownership_stderr), _classify(spoof_stderr)) == ("OwnershipRefused", "CannotEvaluate"),
              (_classify(ownership_stderr), _classify(spoof_stderr)))

        advice = ("git rev-parse exited 128: fatal: detected dubious ownership in repository at '/x'\n"
                  "To add an exception for this directory, call:\n\n"
                  "\tgit config --global --add safe.directory /x")

        def _refuse(exc_class, message):
            def raiser(root, base_arg, environ):
                raise exc_class(message)
            return raiser

        noted, plain = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(noted), _patched(this, "resolve_base", _refuse(OwnershipRefused, advice)):
            noted_code = this.run(ROOT)
        with contextlib.redirect_stderr(plain), _patched(
                this, "resolve_base", _refuse(CannotEvaluate, "shallow repository")):
            plain_code = this.run(ROOT)
        # An ordinary refusal whose message mentions safe.directory, here an unresolvable revision of that
        # name: exit 2 without the note, because the refusal class, not the message text, selects the note.
        environ = {key: value for key, value in os.environ.items() if not key.startswith("GITHUB_")}
        try:
            named = _repo(base_dir, "diag-safe-directory-revision", _snapshot(), _snapshot())
            named_code, named_out = _gate(this, ("diag/safe-directory-revision", named, environ,
                                                 "safe.directory", 2, (), None))
        except (OSError, subprocess.SubprocessError) as exc:
            named_code, named_out = "fixture build failed: {}".format(exc), ""
        note = ("only the checkout owner or CI can evaluate this checkout; this gate reads git "
                "with no global or system configuration, so a safe.directory entry cannot reach it.")
        check("diagnostic/ownership-note",
              (noted_code, note in noted.getvalue(), plain_code, note in plain.getvalue(), named_code,
               "safe.directory does not resolve to a commit" in named_out,
               "only the checkout owner" in named_out) == (2, True, 2, False, 2, True, False),
              (noted_code, noted.getvalue(), plain_code, plain.getvalue(), named_code, named_out))

        # 4. caller git configuration: with a malformed HOME, XDG and system config in the run and process
        # environments, every git read the gate makes is pinned off them, so each vector gives the same exit and
        # output as without the poison, and the observer records launches for each vector but none reading those
        # surfaces. Controls first prove that each surface alone breaks a git read that honours it (git names
        # that surface's file), and that the observer records that launch and exactly that surface.
        changes, surfaces, files, log = _poisoned_config(base_dir, real_git)
        controls = {}
        control_root = by_name["same/base-equals-head"][1] if "same/base-equals-head" in by_name else None
        for surface, _key in POISON_SURFACES:
            log.write_bytes(b"")
            if control_root is None:
                controls[surface] = (False, "vector not built")
                continue
            try:
                probe = subprocess.run(["git", "-C", str(control_root), "rev-parse", "HEAD"], capture_output=True,
                                       timeout=TIMEOUT, env=_poisoned(os.environ, surfaces[surface]))
                controls[surface] = (probe.returncode != 0 and b"bad config" in probe.stderr
                                     and str(files[surface]).encode("utf-8") in probe.stderr,
                                     sorted(set(log.read_text(encoding="utf-8").split())))
            except (OSError, subprocess.SubprocessError) as exc:
                controls[surface] = (False, "control launch failed: {}".format(exc))
        check("config/poison-control-breaks-git", all(
            controls[surface] == (True, sorted(("launch", surface))) for surface, _key in POISON_SURFACES), controls)
        changed, unobserved, exposure = [], [], set()
        for name in POISONED_VECTORS:
            if name not in by_name:
                changed.append((name, "vector not built"))
                unobserved.append(name)
                continue
            clean = _gate(this, by_name[name])
            log.write_bytes(b"")
            poisoned = _poisoned_gate(this, by_name[name], changes)
            observed = log.read_text(encoding="utf-8").split()
            if "launch" not in observed:
                unobserved.append(name)
            exposure |= set(observed) - {"launch"}
            if poisoned != clean:
                changed.append((name, clean, poisoned))
        check("config/poisoned-same-result", vectors and not changed, changed)
        check("config/poisoned-no-exposure", vectors and not unobserved and not exposure,
              dict(unobserved=unobserved, exposure=sorted(exposure)))

    # the in-run self-guard: the executed set is exactly this suite's registered set
    expected, problem = _registered_check_ids()
    harness = []
    if problem is not None:
        harness.append(("execution-set/registry", problem))
    elif len(set(executed)) != len(executed) or set(executed) != expected:
        harness.append(("execution-set/reconciled", dict(missing=sorted(expected - set(executed)),
                                                         extra=sorted(set(executed) - expected))))

    failed = [(check_id, detail) for check_id, ok, detail in results if not ok] + harness
    if failed:
        print("SELF-TEST FAIL: control-retirement {} of {} check(s) failed".format(len(failed), len(results)))
        for check_id, detail in failed:
            print("  {}: {!r}".format(check_id, detail))
        return 1
    print("SELF-TEST PASS: control-retirement {} checks ({} vectors: removed gate, hook and check ids without a "
          "row are findings; covered, unreachable and disclosed rows are held to their fields, optional fields "
          "included; every row field is a string or the record cannot be evaluated; a rename is one row; a new "
          "row names a control live at the base; a stale base row cannot stand in for a removal, and deleting "
          "it while its control lives is the repair; the record is "
          "append-only; the base is read by id only; unresolvable bases, malformed inputs and an unavailable "
          "scratch home fail closed; an ownership refusal says only the checkout owner or CI can evaluate; {} "
          "enforcement mutants each caught by an accepted invalid fixture, {} diagnostic and refusal mutants "
          "each pinned to an exact outcome and accepting no refusing vector, every refusing vector deviating "
          "under at least one mutant, a crash counted as a deviation; a poisoned caller HOME, XDG "
          "and system git configuration, each shown to break a read that honours it, changes no result)".format(
              len(results), len(vectors), len(ENFORCEMENT_MUTANTS), len(DIAGNOSTIC_MUTANTS)))
    return 0


def main(argv=None):
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
    print("usage: check_control_retirement.py [--base REV | --self-test [--execution-report ABS_PATH]]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    _selftest_exit_report.exit_with(main())
