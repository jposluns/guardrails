#!/usr/bin/env python3
"""Push gate record: refuse a branch push whose commit has no complete, passing local gate record.

The gate-discipline rule trusts a verdict only from the gate's own unmasked exit status or from a
structured terminal result bound to the exact revision under gate, and treats a pending, missing or
malformed result as unverified. This tool makes that checkable at push time for the local runner
(tools/run_all_checks.sh), in four modes:

  check_push_gate_record.py --supervise
      Export HEAD's commit into a fresh temporary directory, run tools/run_all_checks.sh there under
      supervision and publish a record bound to HEAD's full object id at
      <git-common-dir>/aiqt-gate-records/<oid>.jsonl. Exits with the runner's own exit status (a
      signal n reads as 128+n); 2 when the tree is dirty, HEAD or the git directory cannot be read or
      the commit cannot be exported (no record is written then).

EXPORT. The runner never runs in the caller's work tree: the supervisor makes a private clone of the
repository (git clone --shared --no-checkout, objects borrowed through alternates, no hooks template)
in a fresh temporary directory, detaches its HEAD at the commit, builds a fresh index from the
commit's tree (read-tree) and writes every file from that index (checkout-index), so the files the
runner reads are the commit's by construction, whatever the caller's index flags (assume-unchanged,
skip-worktree), ignored files or uncommitted edits hide. head_at_end and clean_at_end are read in
that export. The export is removed when the run ends. Cost: one full checkout per run (disk and
time proportional to the tree), and the runner sees no untracked or ignored local file (a gate that
needs one fails), no local branch (the export's branches are the caller's as remote-tracking refs
under origin/) and none of the caller's per-repository configuration.
  check_push_gate_record.py --pre-push --records DIR REMOTE URL
      Read git's pre-push lines on stdin and refuse (exit 1) every branch update whose pushed commit has
      no acceptable record in DIR; exit 2 on a malformed stdin line or an unreadable DIR; exit 0 when
      every branch update has an acceptable record (an empty stdin, a deletion and a destination
      outside refs/heads/ are not checked).
  check_push_gate_record.py --print-pre-push-hook
      Print a POSIX sh shim for an adopter to install as .git/hooks/pre-push; it execs the check, so
      git sees the check's own exit status.
  check_push_gate_record.py --self-test [--red-on-revert] [--execution-report ABS_PATH]
      Synthetic fixtures; --red-on-revert also switches each acceptance rule off and requires its
      fixture to flip.

RECORD FORMAT. UTF-8 JSON Lines, every line ending in LF, at most MAX_RECORD_BYTES. Each line is one
JSON object; an unknown key, a duplicate key, a missing key and a boolean where an integer belongs are
refused. The first line is
  {"type":"start","v":1,"run_id":<hex>,"commit":<oid>,"object_format":"sha1"|"sha256",
   "clean_at_start":true,"started_utc":<YYYY-MM-DDTHH:MM:SSZ>}
then zero or more {"type":"gate","name":<str>,"exit":<int>} lines, and the last line is
  {"type":"terminal","v":1,"run_id":<same>,"commit":<oid>,"status":<int>,"gates":<count>,
   "head_at_end":<oid>,"clean_at_end":<bool>,"ended_utc":<YYYY-MM-DDTHH:MM:SSZ>}
with nothing after it.

ACCEPTANCE (each a named entry in RULES, so --red-on-revert can switch it off). A record is accepted only
when: it is present; it is opened without following a symbolic link and is a regular file within the
size limit; its size, modification time, change time, inode and device are unchanged between the open
and the end of the read; it is valid UTF-8, every line valid JSON with no duplicate or unknown key and
ends in LF; the first line is start and the last is terminal, with nothing after the first terminal;
v is 1 on both; run_id matches; start.commit, terminal.commit and head_at_end all equal the pushed oid;
clean_at_start and clean_at_end are true; gates is at least 1 and equals the number of gate lines; the
status is the integer 0; and no gate line has a nonzero exit while the status is 0.

DECIDED WHERE THE PLAN LEFT IT OPEN (fail closed): a records directory that is itself a symbolic link
is refused (cannot evaluate, exit 2); a stdin that is not UTF-8, lacks its final LF or exceeds
MAX_STDIN_BYTES is malformed (exit 2); a carriage return anywhere in a record line refuses the record;
a supervised run whose end-of-run HEAD or tree status cannot be read publishes no record; the
supervisor's git reads drop every inherited GIT_* variable, keep the caller's HOME and on-disk config
(the trust surface a real checkout needs), disable optional locks, replace objects and terminal
prompts, and carry the automatic-maintenance pins in option position; --supervise refuses (exit 2,
no record) when SIGCHLD is ignored in its process, because the runner's exit status could then read
as 0. That is a disposition read only, not the canonical fork-and-wait guard copy, so
tools/check_child_status_coverage.py lists this entry point as a direct-launch residual.

RESIDUALS (what this check does not catch):
  - Only its self-test runs in this repository (CI and the local runner); the push check itself is an
    adopter-installed git pre-push hook, so --no-verify, an uninstalled or non-executable hook, a
    core.hooksPath override, and pushes outside git's hook path (an API, a web editor, another client)
    are not checked.
  - The record is written by the same actor that pushes and is not an attestation: it catches a push
    made before its run finished or after it failed, not a forged or hand-written record.
  - Rerun semantics: a supervised run removes any earlier record for its commit before it starts and
    publishes atomically on exit, so a pending rerun refuses the push; of two concurrent runs on one
    commit the last rename wins. A supervisor killed mid-run leaves no record for the commit (and may
    leave its private temporary file in the records directory and its export directory, named
    aiqt-push-gate-export-*, in the temporary directory).
  - The record binds the commit's exported files, not the process that ran: the runner can still
    read outside the export (an absolute path, the network, the caller's home and global git
    configuration, the borrowed object store), a change to the export during the run is caught
    only when it is still visible to git status at the end, and the caller's own work tree is
    checked only by a git status read at the start (its index flags can hide an edit there,
    which the export makes harmless to the record).
  - Only refs/heads/ destinations are checked; a tag push or any other destination is not.
  - No remote CI result is checked; the event-trigger paragraph of the rule still governs that in prose.
  - Per-gate lines are parsed from the runner's printed output (a gate that prints a look-alike line
    can add one); the verdict is the supervised process's exit status, and the parsed lines can only
    add refusals (gates-nonzero, no-contradiction), never a pass.
  - Parent directories of the records directory are followed as the path resolves; only its final
    component and the record itself are opened without following a link.
  - The runner inherits the caller's environment except every GIT_* variable (so an inherited
    GIT_DIR or GIT_WORK_TREE cannot point it back at the caller's tree); the supervisor does not
    otherwise harden it.
  - Only the pushed tip commit of each branch update is checked; the intermediate commits of a
    multi-commit push need no record.
  - A pending rerun refuses the push only until some run publishes: an earlier concurrent run on the
    same commit that publishes after the rerun removed the old record makes the push pass on it.

Exit codes: 0 pass, 1 refusal or self-test failure, 2 cannot evaluate (usage, malformed input, an
unreadable git state or a self-test harness error). Stdlib only.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_push_gate_record.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import contextlib
import datetime
import errno
import io
import json
import os
import secrets
import shutil
import signal
import stat
import subprocess
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _selftest_exit_report  # noqa: E402

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_CANNOT_EVALUATE = 2

RUNNER_REL = "tools/run_all_checks.sh"
TOOL_REL = "tools/check_push_gate_record.py"
RECORDS_DIR_NAME = "aiqt-gate-records"
RECORD_SUFFIX = ".jsonl"
MAX_RECORD_BYTES = 1024 * 1024
MAX_STDIN_BYTES = 1024 * 1024
GIT_TIMEOUT = 60
GIT_EXPORT_TIMEOUT = 600
EXPORT_PREFIX = "aiqt-push-gate-export-"
HEX = frozenset("0123456789abcdef")
OID_LENGTHS = {"sha1": 40, "sha256": 64}
UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
# The automatic-maintenance pins, in option position on every git launch: a status or rev-parse never
# needs a gc, so none may start one in the caller's repository.
GIT_PINS = ("-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false")

START_KEYS = frozenset(("type", "v", "run_id", "commit", "object_format", "clean_at_start", "started_utc"))
GATE_KEYS = frozenset(("type", "name", "exit"))
TERMINAL_KEYS = frozenset(("type", "v", "run_id", "commit", "status", "gates", "head_at_end",
                           "clean_at_end", "ended_utc"))
LINE_KEYS = {"start": START_KEYS, "gate": GATE_KEYS, "terminal": TERMINAL_KEYS}

# Every acceptance rule by name. True is enforced; the self-test's --red-on-revert switches one off at a
# time (_reverted) and requires its fixture to flip. Production code never changes this table.
RULES = {
    "stdin-well-formed": True,
    "delete-exempt": True,
    "branch-scope": True,
    "record-present": True,
    "no-symlink": True,
    "regular-file": True,
    "size-limit": True,
    "stable-read": True,
    "utf8": True,
    "json-lines": True,
    "no-duplicate-keys": True,
    "no-unknown-keys": True,
    "final-lf": True,
    "nothing-after-terminal": True,
    "version": True,
    "first-is-start": True,
    "last-is-terminal": True,
    "run-id-match": True,
    "start-commit-bind": True,
    "terminal-commit-bind": True,
    "head-at-end-bind": True,
    "clean-at-start": True,
    "clean-at-end": True,
    "status-zero": True,
    "int-not-bool": True,
    "gates-nonzero": True,
    "gate-count": True,
    "no-contradiction": True,
    "supervise-clean-start": True,
    "supervise-sigchld": True,
}

# Self-test seam: called between the end of the record read and the second fstat (None in production).
_AFTER_READ = None


class CannotEvaluate(RuntimeError):
    """A required input or git state could not be read; no verdict."""


class Refusal(Exception):
    """A record refused by the named rule (or by a structural reason that is not a switchable rule)."""

    def __init__(self, rule, detail):
        super().__init__(rule, detail)
        self.rule = rule
        self.detail = detail


class _DuplicateKey(Exception):
    pass


def _on(rule):
    return RULES[rule]


def _is_hex(text, lengths=(40, 64)):
    return isinstance(text, str) and len(text) in lengths and all(c in HEX for c in text)


def _is_zero_oid(text):
    return bool(text) and all(c == "0" for c in text)


def _utc_ok(value):
    if not isinstance(value, str) or len(value) != 20:
        return False
    try:
        datetime.datetime.strptime(value, UTC_FORMAT)
    except ValueError:
        return False
    return True


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime(UTC_FORMAT)


# ---------------------------------------------------------------------------------------------------
# Reading and judging one record
# ---------------------------------------------------------------------------------------------------

class Verdict:
    def __init__(self, ok, rule=None, detail="", gate_lines=(), terminal=None):
        self.ok = ok
        self.rule = rule
        self.detail = detail
        self.gate_lines = list(gate_lines)
        self.terminal = terminal


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out and _on("no-duplicate-keys"):
            raise _DuplicateKey(key)
        out[key] = value
    return out


def _bad_constant(name):
    raise ValueError("non-finite number {}".format(name))


def _read_bytes(dir_fd, name):
    """The record's bytes, or Refusal. Returns None when the record is absent and record-present is off."""
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC
    if _on("no-symlink"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(name, flags, dir_fd=dir_fd)
    except FileNotFoundError:
        if _on("record-present"):
            raise Refusal("record-present", "no gate record exists for this commit")
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise Refusal("no-symlink", "the record is a symbolic link")
        raise Refusal("unreadable", "the record cannot be opened: {}".format(exc.strerror or exc))
    try:
        first = os.fstat(fd)
        if _on("regular-file") and not stat.S_ISREG(first.st_mode):
            raise Refusal("regular-file", "the record is not a regular file")
        if _on("size-limit") and first.st_size > MAX_RECORD_BYTES:
            raise Refusal("size-limit", "the record exceeds {} bytes".format(MAX_RECORD_BYTES))
        chunks, total = [], 0
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError as exc:
                raise Refusal("unreadable", "the record cannot be read: {}".format(exc.strerror or exc))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if _on("size-limit") and total > MAX_RECORD_BYTES:
                raise Refusal("size-limit", "the record exceeds {} bytes".format(MAX_RECORD_BYTES))
        data = b"".join(chunks)
        if _AFTER_READ is not None:
            _AFTER_READ()
        second = os.fstat(fd)
        if _on("stable-read"):
            before = (first.st_size, first.st_mtime_ns, first.st_ctime_ns, first.st_ino, first.st_dev)
            after = (second.st_size, second.st_mtime_ns, second.st_ctime_ns, second.st_ino, second.st_dev)
            if before != after or len(data) != second.st_size:
                raise Refusal("stable-read", "the record changed while it was read")
        return data
    finally:
        os.close(fd)


def _int_field(obj, key, where):
    value = obj[key]
    if isinstance(value, bool):
        if _on("int-not-bool"):
            raise Refusal("int-not-bool", "{}: {} is a boolean, not an integer".format(where, key))
        return int(value)
    if type(value) is not int:
        raise Refusal("schema", "{}: {} is not an integer".format(where, key))
    return value


def _bool_field(obj, key, where):
    if type(obj[key]) is not bool:
        raise Refusal("schema", "{}: {} is not a boolean".format(where, key))
    return obj[key]


def _validate_line(number, obj):
    where = "line {}".format(number)
    kind = obj.get("type")
    if not isinstance(kind, str) or kind not in LINE_KEYS:
        raise Refusal("schema", "{}: unknown line type {!r}".format(where, kind))
    keys = LINE_KEYS[kind]
    extra = sorted(set(obj) - keys)
    if extra and _on("no-unknown-keys"):
        raise Refusal("no-unknown-keys", "{}: unknown key(s) {}".format(where, ", ".join(extra)))
    missing = sorted(keys - set(obj))
    if missing:
        raise Refusal("schema", "{}: missing key(s) {}".format(where, ", ".join(missing)))
    if kind == "gate":
        if not isinstance(obj["name"], str) or not obj["name"]:
            raise Refusal("schema", "{}: name is not a nonempty string".format(where))
        _int_field(obj, "exit", where)
        return
    _int_field(obj, "v", where)
    run_id = obj["run_id"]
    if not (isinstance(run_id, str) and 16 <= len(run_id) <= 64 and all(c in HEX for c in run_id)):
        raise Refusal("schema", "{}: run_id is not 16 to 64 lowercase hex digits".format(where))
    if not _is_hex(obj["commit"]):
        raise Refusal("schema", "{}: commit is not a full lowercase object id".format(where))
    if kind == "start":
        fmt = obj["object_format"]
        if not isinstance(fmt, str) or fmt not in OID_LENGTHS or len(obj["commit"]) != OID_LENGTHS[fmt]:
            raise Refusal("schema", "{}: object_format does not match the commit id".format(where))
        _bool_field(obj, "clean_at_start", where)
        if not _utc_ok(obj["started_utc"]):
            raise Refusal("schema", "{}: started_utc is not {}".format(where, UTC_FORMAT))
        return
    _int_field(obj, "status", where)
    _int_field(obj, "gates", where)
    if not _is_hex(obj["head_at_end"]):
        raise Refusal("schema", "{}: head_at_end is not a full lowercase object id".format(where))
    _bool_field(obj, "clean_at_end", where)
    if not _utc_ok(obj["ended_utc"]):
        raise Refusal("schema", "{}: ended_utc is not {}".format(where, UTC_FORMAT))


def _parse(data, objs):
    """Append the record's (line number, object) pairs to objs and return it, or Refusal; the lines
    parsed before a refusal stay in objs for the diagnostics."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        if _on("utf8"):
            raise Refusal("utf8", "the record is not valid UTF-8 (byte {})".format(exc.start))
        text = data.decode("utf-8", "replace")
    if text and not text.endswith("\n"):
        if _on("final-lf"):
            raise Refusal("final-lf", "the record's last line does not end in LF")
        text += "\n"
    lines = text.split("\n")[:-1] if text else []
    for number, line in enumerate(lines, 1):
        try:
            if "\r" in line:
                raise ValueError("carriage return in the line")
            obj = json.loads(line, object_pairs_hook=_pairs, parse_constant=_bad_constant)
            if not isinstance(obj, dict):
                raise ValueError("not a JSON object")
        except _DuplicateKey as exc:
            raise Refusal("no-duplicate-keys", "line {}: duplicate key {!r}".format(number, exc.args[0]))
        except (ValueError, RecursionError) as exc:
            if _on("json-lines"):
                raise Refusal("json-lines", "line {}: not a valid JSON object ({})".format(number, exc))
            continue
        objs.append((number, obj))
    return objs


def _judge(objs, oid):
    """Return (gate lines, terminal) for an acceptable record, else raise Refusal."""
    for number, obj in objs:
        _validate_line(number, obj)
    types = [obj["type"] for _, obj in objs]
    if not types:
        raise Refusal("first-is-start" if _on("first-is-start") else "one-start", "the record is empty")
    if _on("first-is-start") and types[0] != "start":
        raise Refusal("first-is-start", "the first line is not a start line")
    terms = [i for i, kind in enumerate(types) if kind == "terminal"]
    starts = [i for i, kind in enumerate(types) if kind == "start"]
    if _on("nothing-after-terminal") and terms and terms[0] != len(types) - 1:
        raise Refusal("nothing-after-terminal", "a line follows the terminal line")
    if _on("last-is-terminal") and types[-1] != "terminal":
        raise Refusal("last-is-terminal", "the record does not end with a terminal line (run unfinished)")
    if not terms:
        raise Refusal("no-terminal", "the record holds no terminal line (run unfinished)")
    if len(starts) != 1 or starts[0] > terms[0]:
        raise Refusal("one-start", "the record does not hold exactly one start line before its terminal")
    start = objs[starts[0]][1]
    term = objs[terms[0]][1]
    gate_lines = [obj for _, obj in objs[starts[0] + 1:terms[0]] if obj["type"] == "gate"]
    if _on("version") and (start["v"] != 1 or term["v"] != 1):
        raise Refusal("version", "record version is not 1")
    if _on("run-id-match") and start["run_id"] != term["run_id"]:
        raise Refusal("run-id-match", "start and terminal run_id differ")
    if _on("start-commit-bind") and start["commit"] != oid:
        raise Refusal("start-commit-bind", "the start line names another commit")
    if _on("terminal-commit-bind") and term["commit"] != oid:
        raise Refusal("terminal-commit-bind", "the terminal line names another commit")
    if _on("head-at-end-bind") and term["head_at_end"] != oid:
        raise Refusal("head-at-end-bind", "HEAD moved during the run")
    if _on("clean-at-start") and start["clean_at_start"] is not True:
        raise Refusal("clean-at-start", "the tree was not clean when the run started")
    if _on("clean-at-end") and term["clean_at_end"] is not True:
        raise Refusal("clean-at-end", "the tree was not clean when the run ended")
    if _on("gates-nonzero") and term["gates"] < 1:
        raise Refusal("gates-nonzero", "the run recorded no gate")
    if _on("gate-count") and term["gates"] != len(gate_lines):
        raise Refusal("gate-count", "terminal gates {} but {} gate line(s)".format(term["gates"], len(gate_lines)))
    if _on("status-zero") and term["status"] != 0:
        raise Refusal("status-zero", "the run exited {}".format(term["status"]))
    if _on("no-contradiction") and term["status"] == 0 and any(g["exit"] != 0 for g in gate_lines):
        raise Refusal("no-contradiction", "status 0 but a gate line has a nonzero exit")
    return gate_lines, term


def evaluate(dir_fd, oid):
    """Judge the record for oid in the open records directory (dir_fd None: the directory is absent)."""
    objs = []
    try:
        if dir_fd is None:
            if _on("record-present"):
                raise Refusal("record-present", "no gate record exists for this commit (no records directory)")
            return Verdict(True)
        data = _read_bytes(dir_fd, oid + RECORD_SUFFIX)
        if data is None:
            return Verdict(True)
        _parse(data, objs)
        gate_lines, term = _judge(objs, oid)
        return Verdict(True, gate_lines=gate_lines, terminal=term)
    except Refusal as exc:
        gates = [obj for _, obj in objs if obj.get("type") == "gate"]
        terms = [obj for _, obj in objs if obj.get("type") == "terminal"]
        return Verdict(False, exc.rule, exc.detail, gates, terms[0] if terms else None)


def _describe(verdict):
    if verdict.gate_lines:
        gates = ", ".join("{} exit {}".format(g.get("name"), g.get("exit")) for g in verdict.gate_lines)
    else:
        gates = "none"
    if verdict.terminal is None:
        terminal = "terminal line missing"
    else:
        t = verdict.terminal
        terminal = "terminal status {}, gates {}, head_at_end {}, clean_at_end {}".format(
            t.get("status"), t.get("gates"), t.get("head_at_end"), t.get("clean_at_end"))
    return "  gate lines read: {}\n  {}".format(gates, terminal)


# ---------------------------------------------------------------------------------------------------
# The pre-push check
# ---------------------------------------------------------------------------------------------------

def _open_records_dir(path):
    try:
        return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CannotEvaluate("records directory {} cannot be opened: {}".format(path, exc.strerror or exc))


def check_push(stdin_bytes, records_dir, out):
    """Judge git's pre-push lines; write findings to the text stream out; return the exit code."""
    if len(stdin_bytes) > MAX_STDIN_BYTES:
        print("CANNOT_EVALUATE: pre-push input exceeds {} bytes".format(MAX_STDIN_BYTES), file=out)
        return EXIT_CANNOT_EVALUATE
    try:
        text = stdin_bytes.decode("utf-8")
    except UnicodeDecodeError:
        if _on("stdin-well-formed"):
            print("CANNOT_EVALUATE: pre-push input is not UTF-8", file=out)
            return EXIT_CANNOT_EVALUATE
        text = stdin_bytes.decode("utf-8", "replace")
    if not text:
        print("OK: nothing to push", file=out)
        return EXIT_OK
    if not text.endswith("\n"):
        if _on("stdin-well-formed"):
            print("CANNOT_EVALUATE: pre-push input does not end in LF", file=out)
            return EXIT_CANNOT_EVALUATE
        text += "\n"
    updates = []
    for number, line in enumerate(text.split("\n")[:-1], 1):
        fields = line.split(" ")
        good = (len(fields) == 4 and all(fields) and _is_hex(fields[1]) and _is_hex(fields[3])
                and len(fields[1]) == len(fields[3]))
        if not good:
            if _on("stdin-well-formed"):
                print("CANNOT_EVALUATE: malformed pre-push line {}: expected "
                      "'<local ref> <local oid> <remote ref> <remote oid>'".format(number), file=out)
                return EXIT_CANNOT_EVALUATE
            continue
        updates.append(fields)
    try:
        dir_fd = _open_records_dir(records_dir)
    except CannotEvaluate as exc:
        print("CANNOT_EVALUATE: {}".format(exc), file=out)
        return EXIT_CANNOT_EVALUATE
    refused = 0
    try:
        for local_ref, local_oid, remote_ref, _remote_oid in updates:
            if _is_zero_oid(local_oid) and _on("delete-exempt"):
                print("NOTE: {} is a deletion; not checked".format(remote_ref), file=out)
                continue
            if not remote_ref.startswith("refs/heads/") and _on("branch-scope"):
                print("NOTE: {} is not a branch (refs/heads/); not checked".format(remote_ref), file=out)
                continue
            verdict = evaluate(dir_fd, local_oid)
            if verdict.ok:
                print("OK: {} {} has a complete passing gate record".format(remote_ref, local_oid), file=out)
                continue
            refused += 1
            print("REFUSED: {} ({} from {}): {}: {}".format(remote_ref, local_oid, local_ref, verdict.rule,
                                                          verdict.detail), file=out)
            print(_describe(verdict), file=out)
    finally:
        if dir_fd is not None:
            os.close(dir_fd)
    if refused:
        print("FAIL: {} branch update(s) refused; run python3 -I -B {} --supervise on a clean tree at the "
              "pushed commit and push after it passes".format(refused, TOOL_REL), file=out)
        return EXIT_REFUSED
    return EXIT_OK


# ---------------------------------------------------------------------------------------------------
# The supervisor
# ---------------------------------------------------------------------------------------------------

def _git_env():
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    return env


def _git_query(root, args, env=None):
    """(exit status, stdout bytes) of one git call in root; CannotEvaluate when git cannot be launched.
    env None is _git_env(); the self-test passes a hermetic environment."""
    try:
        done = subprocess.run(["git", *GIT_PINS, "-C", str(root), *args], stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=GIT_EXPORT_TIMEOUT if args[:1] == ("clone",)
                              else GIT_TIMEOUT, env=_git_env() if env is None else env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("git cannot be run: {}".format(exc))
    return done.returncode, done.stdout


def _one_line(git, root, args, what):
    code, out = git(root, args)
    if code != 0 or not out.endswith(b"\n") or out.count(b"\n") != 1:
        raise CannotEvaluate("{} cannot be read (git exit {})".format(what, code))
    try:
        return out[:-1].decode("utf-8")
    except UnicodeDecodeError:
        raise CannotEvaluate("{} is not UTF-8".format(what))


def _head(git, root):
    head = _one_line(git, root, ("rev-parse", "--verify", "--end-of-options", "HEAD^{commit}"), "HEAD")
    fmt = _one_line(git, root, ("rev-parse", "--show-object-format"), "the object format")
    if fmt not in OID_LENGTHS or not _is_hex(head, (OID_LENGTHS[fmt],)):
        raise CannotEvaluate("HEAD {!r} is not a full {} object id".format(head, fmt))
    return head, fmt


def _clean(git, root):
    code, out = git(root, ("status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none"))
    if code != 0:
        raise CannotEvaluate("the work tree status cannot be read (git exit {})".format(code))
    return out == b""


def _export(git, root, oid, dest):
    """Write the commit oid's tree into dest (a path that does not exist yet) as a private clone of
    root's repository with HEAD detached at oid, a fresh index from oid's tree and every file
    written from that index; CannotEvaluate when a step fails or the export's HEAD is not oid."""
    steps = (
        (root, ("clone", "--quiet", "--shared", "--no-checkout", "--template=", "--", str(root), str(dest))),
        (dest, ("update-ref", "--no-deref", "HEAD", oid)),
        (dest, ("read-tree", "--end-of-options", oid)),
        (dest, ("checkout-index", "--all", "--force", "--index")),
    )
    for where, args in steps:
        code, _out = git(where, args)
        if code != 0:
            raise CannotEvaluate("the commit cannot be exported (git {} exit {})".format(args[0], code))
    exported, _fmt = _head(git, dest)
    if exported != oid:
        raise CannotEvaluate("the export's HEAD {} is not the commit {}".format(exported, oid))


def _runner_env():
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


class _RunnerChild:
    """The real runner: bash tools/run_all_checks.sh in root (the export), stdout and stderr merged,
    read to EOF, with every inherited GIT_* variable dropped."""

    def __init__(self, root):
        runner = root / RUNNER_REL
        try:
            info = os.lstat(runner)
        except OSError as exc:
            raise CannotEvaluate("{} cannot be read: {}".format(RUNNER_REL, exc.strerror or exc))
        if not stat.S_ISREG(info.st_mode):
            raise CannotEvaluate("{} is not a regular file".format(RUNNER_REL))
        try:
            self.proc = subprocess.Popen(["bash", RUNNER_REL], cwd=str(root), stdin=subprocess.DEVNULL,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         env=_runner_env())
        except OSError as exc:
            raise CannotEvaluate("the runner cannot be launched: {}".format(exc))

    def lines(self):
        yield from iter(self.proc.stdout.readline, b"")

    def wait(self):
        self.proc.stdout.close()
        code = self.proc.wait()
        return 128 - code if code < 0 else code

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()


def _start_runner(root):
    return _RunnerChild(root)


def _gate_events(raw, state):
    """Display-only gate lines from one runner output line: '--- name ---' opens a gate, and
    'GATE FAILED: name (exit N)' closes it with N; an open gate closed by the next opener or by EOF
    reads exit 0."""
    events = []
    line = raw.decode("utf-8", "replace").rstrip("\n")
    if line.startswith("--- ") and line.endswith(" ---") and len(line) > 8:
        if state.get("open") is not None:
            events.append((state["open"], 0))
        state["open"] = line[4:-4]
    elif line.startswith("GATE FAILED: ") and line.endswith(")") and " (exit " in line:
        name, _, tail = line[len("GATE FAILED: "):].rpartition(" (exit ")
        try:
            code = int(tail[:-1])
        except ValueError:
            code = None
        if name and code is not None:
            events.append((name, code))
            if state.get("open") == name:
                state["open"] = None
    return events


def _dump(obj):
    return json.dumps(obj, ensure_ascii=True, separators=(",", ":"), sort_keys=False) + "\n"


def _sync_dir(path):
    dir_fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def supervise(cwd, out, err, git=_git_query, start_runner=_start_runner, replace=os.replace,
              sync_dir=_sync_dir):
    """Export the commit, run the runner there under supervision and publish its record; return the
    exit code (see the module docstring). out receives the runner's bytes; err is a text stream for
    the supervisor's own lines."""
    if _on("supervise-sigchld") and signal.getsignal(signal.SIGCHLD) == signal.SIG_IGN:
        # With SIGCHLD ignored the kernel reaps the runner and its exit status cannot be read, so a failed
        # run would read as a pass. Refuse by name instead of publishing such a status.
        print("CANNOT_EVALUATE: SIGCHLD is ignored in this process, so the runner's exit status cannot be "
              "read; no record written", file=err)
        return EXIT_CANNOT_EVALUATE
    try:
        root = Path(_one_line(git, cwd, ("rev-parse", "--show-toplevel"), "the work tree root"))
        head, fmt = _head(git, root)
        clean_start = _clean(git, root)
        common = _one_line(git, root, ("rev-parse", "--path-format=absolute", "--git-common-dir"),
                           "the git common directory")
    except CannotEvaluate as exc:
        print("CANNOT_EVALUATE: {}; no record written".format(exc), file=err)
        return EXIT_CANNOT_EVALUATE
    if not clean_start and _on("supervise-clean-start"):
        print("CANNOT_EVALUATE: the work tree is not clean (commit or remove every change, untracked files "
              "included); no record written", file=err)
        return EXIT_CANNOT_EVALUATE
    records = Path(common) / RECORDS_DIR_NAME
    final = records / (head + RECORD_SUFFIX)
    try:
        os.makedirs(records, mode=0o700, exist_ok=True)
        info = os.lstat(records)
        if not stat.S_ISDIR(info.st_mode):
            raise OSError(errno.ENOTDIR, "not a directory (or a symbolic link)")
        # Rerun rule: an earlier record for this commit is removed before the run starts, so the push is
        # refused until this run publishes.
        try:
            os.unlink(final)
        except FileNotFoundError:
            pass
        fd, tmp = tempfile.mkstemp(prefix=".run-", suffix=".tmp", dir=str(records))
    except OSError as exc:
        print("CANNOT_EVALUATE: the records directory {} cannot be prepared: {}; no record written".format(
            records, exc.strerror or exc), file=err)
        return EXIT_CANNOT_EVALUATE
    run_id = secrets.token_hex(16)
    child = None
    published = False
    export_parent = None
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            try:
                export_parent = tempfile.mkdtemp(prefix=EXPORT_PREFIX)
                tree = Path(export_parent) / "tree"
                _export(git, root, head, tree)
                clean_start = clean_start and _clean(git, tree)
            except (OSError, CannotEvaluate) as exc:
                print("CANNOT_EVALUATE: {}; no record written".format(exc), file=err)
                return EXIT_CANNOT_EVALUATE
            handle.write(_dump({"type": "start", "v": 1, "run_id": run_id, "commit": head, "object_format": fmt,
                                "clean_at_start": clean_start, "started_utc": _utc_now()}))
            handle.flush()
            try:
                child = start_runner(tree)
            except CannotEvaluate as exc:
                print("CANNOT_EVALUATE: {}; no record written".format(exc), file=err)
                return EXIT_CANNOT_EVALUATE
            state, gates = {"open": None}, 0
            for raw in child.lines():
                out.write(raw)
                out.flush()
                for name, code in _gate_events(raw, state):
                    handle.write(_dump({"type": "gate", "name": name, "exit": code}))
                    gates += 1
            if state["open"] is not None:
                handle.write(_dump({"type": "gate", "name": state["open"], "exit": 0}))
                gates += 1
            status = child.wait()
            child = None
            try:
                head_end, _fmt = _head(git, tree)
                clean_end = _clean(git, tree)
            except CannotEvaluate as exc:
                print("ERROR: {} after the run; no record published (the push stays refused)".format(exc),
                      file=err)
                return status
            handle.write(_dump({"type": "terminal", "v": 1, "run_id": run_id, "commit": head, "status": status,
                                "gates": gates, "head_at_end": head_end, "clean_at_end": clean_end,
                                "ended_utc": _utc_now()}))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            replace(tmp, final)
        except OSError as exc:
            print("ERROR: the record could not be published: {} (the push stays refused)".format(
                exc.strerror or exc), file=err)
            return status
        published = True
        try:
            sync_dir(records)
        except OSError as exc:
            # The rename has happened, so the record is in place and the pre-push check reads it; only
            # its durability across a crash is unconfirmed.
            print("WARNING: the record {} is published but the records directory could not be synced: {} "
                  "(a crash may still lose it)".format(final, exc.strerror or exc), file=err)
        print("RECORD: {} status {} ({} gate line(s))".format(final, status, gates), file=err)
        return status
    finally:
        if child is not None:
            with contextlib.suppress(Exception):
                child.kill()
        if not published:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
        if export_parent is not None:
            shutil.rmtree(export_parent, ignore_errors=True)


# ---------------------------------------------------------------------------------------------------
# The hook shim
# ---------------------------------------------------------------------------------------------------

HOOK_SHIM = (
    "#!/bin/sh\n"
    "# pre-push: refuse a branch push whose commit has no complete, passing gate record.\n"
    "# Printed by python3 -I -B " + TOOL_REL + " --print-pre-push-hook; run the gates with --supervise.\n"
    "d=$(git rev-parse --path-format=absolute --git-common-dir) || exit 2\n"
    "exec python3 -I -B " + TOOL_REL + " --pre-push --records \"$d/" + RECORDS_DIR_NAME + "\" \"$1\" \"$2\"\n"
)


USAGE = ("usage: check_push_gate_record.py --supervise\n"
         "       check_push_gate_record.py --pre-push --records DIR REMOTE URL   (git's ref lines on stdin)\n"
         "       check_push_gate_record.py --print-pre-push-hook\n"
         "       check_push_gate_record.py --self-test [--red-on-revert] [--execution-report ABS_PATH]")


def main(argv):
    args = list(argv)
    report = None
    if len(args) >= 2 and args[-2] == "--execution-report":
        if not os.path.isabs(args[-1]):
            print("CANNOT_EVALUATE: --execution-report needs an absolute path\n" + USAGE, file=sys.stderr)
            return EXIT_CANNOT_EVALUATE
        report = args[-1]
        args = args[:-2]
        if not args:
            args = ["--self-test", "--red-on-revert"]
    if args == ["--self-test"]:
        return self_test(False, report)
    if args == ["--self-test", "--red-on-revert"]:
        return self_test(True, report)
    if report is not None:
        print("CANNOT_EVALUATE: --execution-report goes only with --self-test\n" + USAGE, file=sys.stderr)
        return EXIT_CANNOT_EVALUATE
    if args == ["--supervise"]:
        try:
            return supervise(Path.cwd(), sys.stdout.buffer, sys.stderr)
        except KeyboardInterrupt:
            print("INTERRUPTED: no record published", file=sys.stderr)
            return 130
    if args == ["--print-pre-push-hook"]:
        sys.stdout.write(HOOK_SHIM)
        return EXIT_OK
    if len(args) == 5 and args[0] == "--pre-push" and args[1] == "--records" and args[2]:
        return check_push(sys.stdin.buffer.read(MAX_STDIN_BYTES + 1), args[2], sys.stderr)
    print("CANNOT_EVALUATE: wrong arguments\n" + USAGE, file=sys.stderr)
    return EXIT_CANNOT_EVALUATE


# SELF-TEST: production code ends here. Every fixture lives in a temporary directory. Most supervisor
# rows inject a fake git and a fake runner through supervise()'s parameters (never through the command
# line); the child/ rows run the real runner child, and the realgit/ rows and the hook shim row run
# real git in repositories made with git init, under a hermetic git environment (_hermetic_git_env),
# so the git-fixture-env config lanes' poisoned caller configuration never reaches them. Each case asserts through the check() choke point; the executed id set is reconciled
# against tools/selftest_checks.toml (suite push-gate-record-selftest) by tools/check_selftest_execution.py.
# A revert/ case switches one RULES entry off (_reverted) and requires the same fixture to flip: to
# acceptance where the rule is the record's only defect, otherwise to a refusal by a later reason, which
# shows the original row would fail with its rule reverted.

SUITE_ID = "push-gate-record-selftest"
FAILURES = []
EXECUTED = []
_EXECUTED_SET = set()

OID = "3f" * 20
OTHER = "4e" * 20
OID256 = "5d" * 32
RID = "a1" * 16
RID2 = "b2" * 16
TS = "2026-01-02T03:04:05Z"


def check(name, got, want):
    if name in _EXECUTED_SET:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    _EXECUTED_SET.add(name)
    EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


@contextlib.contextmanager
def _reverted(rule):
    saved = RULES[rule]
    RULES[rule] = False
    try:
        yield
    finally:
        RULES[rule] = saved


def _lines(oid=OID, fmt="sha1", gates=(("a", 0), ("b", 0)), start=None, terminal=None, count=None,
           status=0):
    head = {"type": "start", "v": 1, "run_id": RID, "commit": oid, "object_format": fmt,
            "clean_at_start": True, "started_utc": TS}
    head.update(start or {})
    body = [{"type": "gate", "name": name, "exit": code} for name, code in gates]
    tail = {"type": "terminal", "v": 1, "run_id": RID, "commit": oid, "status": status,
            "gates": len(gates) if count is None else count, "head_at_end": oid, "clean_at_end": True,
            "ended_utc": TS}
    tail.update(terminal or {})
    return [head] + body + [tail]


def _encode(objs):
    return "".join(_dump(obj) for obj in objs).encode("utf-8")


class _Cases:
    """A fresh records directory per case under one temporary root."""

    def __init__(self, base):
        self.base = Path(base)
        self.count = 0

    def fresh(self):
        self.count += 1
        path = self.base / "case-{}".format(self.count)
        path.mkdir()
        return path

    def judge(self, data=None, oid=OID, setup=None):
        """(0, None) when accepted, else (1, rule). data: bytes written as the record; setup(records)
        builds the record itself instead."""
        records = self.fresh()
        if setup is not None:
            setup(records)
        elif data is not None:
            (records / (oid + RECORD_SUFFIX)).write_bytes(data)
        dir_fd = _open_records_dir(str(records))
        try:
            verdict = evaluate(dir_fd, oid)
        finally:
            if dir_fd is not None:
                os.close(dir_fd)
        return (0, None) if verdict.ok else (1, verdict.rule)

    def push(self, stdin_text, records=None):
        records = self.fresh() if records is None else records
        buffer = io.StringIO()
        code = check_push(stdin_text.encode("utf-8"), str(records), buffer)
        return code, buffer.getvalue()


def _branch_line(oid=OID, ref="refs/heads/main"):
    return "refs/heads/main {} {} {}\n".format(oid, ref, "0" * len(oid))


class _FakeGit:
    """Answers the supervisor's git calls from fixed values. HEAD answers (start in the work tree, then
    in the export after it is made, then in the export at the end) and status answers (the work tree,
    the export at the start, the export at the end) are consumed in order, the last one repeating; a
    HEAD answer of None is a failed read. The export steps succeed and make the export directory."""

    def __init__(self, root, common, heads=(OID,), fmt="sha1", statuses=(b"",)):
        self.root, self.common, self.fmt = root, common, fmt
        self.heads, self.statuses = list(heads), list(statuses)
        self.calls = []

    def __call__(self, where, args):
        self.calls.append((Path(where), tuple(args)))
        if args == ("rev-parse", "--show-toplevel"):
            return 0, (str(self.root) + "\n").encode()
        if args[:2] == ("rev-parse", "--verify"):
            head = self.heads.pop(0) if len(self.heads) > 1 else self.heads[0]
            return (128, b"") if head is None else (0, (head + "\n").encode())
        if args == ("rev-parse", "--show-object-format"):
            return 0, (self.fmt + "\n").encode()
        if args[:2] == ("rev-parse", "--path-format=absolute"):
            return 0, (str(self.common) + "\n").encode()
        if args[0] == "status":
            return 0, self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if args[0] == "clone":
            Path(args[-1]).mkdir()
            return 0, b""
        if args[0] in ("update-ref", "read-tree", "checkout-index"):
            return 0, b""
        return 128, b""


class _FakeChild:
    def __init__(self, lines, status, during=None, raise_at=None):
        self._lines, self.status, self.during, self.raise_at = list(lines), status, during, raise_at
        self.killed = False

    def lines(self):
        for index, line in enumerate(self._lines):
            if self.during is not None:
                self.during()
            if self.raise_at is not None and index == self.raise_at:
                raise KeyboardInterrupt
            yield line

    def wait(self):
        return self.status

    def kill(self):
        self.killed = True


PASS_OUTPUT = (b"--- a ---\n", b"ok\n", b"\n", b"--- b ---\n", b"\n", b"RESULT: PASS\n")


def _supervise_case(base, name, lines=PASS_OUTPUT, status=0, statuses=(b"",), during=None,
                    raise_at=None, replace=os.replace, prior=None, heads=(OID,), sync_dir=_sync_dir,
                    records_link=False, messages=None):
    root = Path(base) / name
    common = root / ".git"
    common.mkdir(parents=True)
    records = common / RECORDS_DIR_NAME
    final = records / (OID + RECORD_SUFFIX)
    if records_link:
        elsewhere = root / "elsewhere"
        elsewhere.mkdir()
        os.symlink(elsewhere, records)
    if prior is not None:
        records.mkdir()
        final.write_bytes(prior)
    child = _FakeChild(lines, status, during=during and (lambda: during(records, final)), raise_at=raise_at)
    spawned = []

    def fake_runner(where):
        spawned.append(where)
        return child

    out, err = io.BytesIO(), io.StringIO()
    code = supervise(root, out, err, git=_FakeGit(root, common, heads=heads, statuses=statuses),
                     start_runner=fake_runner, replace=replace, sync_dir=sync_dir)
    if messages is not None:
        messages.append(err.getvalue())
    return code, records, final, spawned, out.getvalue()


def _hermetic_git_env(base):
    """The environment of every real git call the self-test makes: built from nothing but PATH, with
    HOME and XDG_CONFIG_HOME at an empty private directory, system and global configuration off and
    no inherited GIT_* variable, so a poisoned caller configuration (the git-fixture-env config
    lanes) never reaches a fixture."""
    home = Path(base) / "git-home"
    home.mkdir(exist_ok=True)
    return {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / "xdg"), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C",
            "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}


def _git_fixture(env, repo, *args):
    """One setup git call in a real fixture repository; raises on failure."""
    return subprocess.run(["git", *GIT_PINS, "-C", str(repo), *args], env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, timeout=GIT_TIMEOUT, check=True).stdout


def _real_repo(base, name, files, env):
    """A real repository made with git init, holding files {relative path: text} in one commit."""
    repo = Path(base) / name
    repo.mkdir()
    _git_fixture(env, repo, "init", "--quiet", "--template=")
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git_fixture(env, repo, "add", "--all")
    _git_fixture(env, repo, "commit", "--quiet", "--no-verify", "-m", "fixture")
    return repo, _git_fixture(env, repo, "rev-parse", "HEAD").decode().strip()


def _real_supervise(repo, env):
    """supervise() with real git (hermetic) and the real runner child; (exit, stdout bytes, err text)."""
    out, err = io.BytesIO(), io.StringIO()
    code = supervise(repo, out, err, git=lambda where, args: _git_query(where, args, env=env))
    return code, out.getvalue(), err.getvalue()


def _real_records(repo):
    return repo / ".git" / RECORDS_DIR_NAME


def _stray(records):
    return sorted(p.name for p in records.iterdir() if p.name.endswith(".tmp")) if records.is_dir() else []


def _judge_path(records, oid=OID):
    dir_fd = _open_records_dir(str(records))
    try:
        verdict = evaluate(dir_fd, oid)
    finally:
        if dir_fd is not None:
            os.close(dir_fd)
    return (0, None) if verdict.ok else (1, verdict.rule)


def _run_hook_shim(base, env):
    """Run the printed shim with real git (hermetic env) and a stub python3 first on PATH, inside a
    real repository made with git init and then outside any repository; return (exit, argv seen,
    expected records directory, exit when git fails)."""
    stubs = Path(base) / "stubs"
    stubs.mkdir()
    shim = Path(base) / "pre-push"
    shim.write_text(HOOK_SHIM, encoding="utf-8")
    seen = Path(base) / "argv.txt"
    py_stub = stubs / "python3"
    py_stub.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > '{}'\nexit 7\n".format(seen), encoding="utf-8")
    os.chmod(py_stub, 0o755)
    real_git = shutil.which("git", path=env["PATH"])
    if real_git is None:
        raise OSError(errno.ENOENT, "git not found on PATH")
    repo = Path(base) / "repo"
    repo.mkdir()
    _git_fixture(env, repo, "init", "--quiet", "--template=")
    shim_env = dict(env, PATH=str(stubs) + os.pathsep + str(Path(real_git).parent),
                    GIT_CEILING_DIRECTORIES=str(base))
    first = subprocess.run(["/bin/sh", str(shim), "origin", "https://example.invalid/repo"], cwd=str(repo),
                           env=shim_env, stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
    argv = seen.read_text(encoding="utf-8").splitlines() if seen.exists() else None
    outside = Path(base) / "outside"
    outside.mkdir()
    second = subprocess.run(["/bin/sh", str(shim), "origin", "https://example.invalid/repo"],
                            cwd=str(outside), env=shim_env, stdin=subprocess.DEVNULL, capture_output=True,
                            timeout=60)
    return first.returncode, argv, os.path.realpath(repo / ".git" / RECORDS_DIR_NAME), second.returncode


def self_test(red_on_revert, report_path=None):
    if report_path is not None:
        # The execution report is finalized at interpreter exit (tools/_selftest_exit_report.py).
        _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
    try:
        holder = tempfile.TemporaryDirectory(prefix="push-gate-record-selftest-")
    except OSError as exc:
        print("SELF-TEST CANNOT EVALUATE: no temporary directory: {}".format(exc), file=sys.stderr)
        return EXIT_CANNOT_EVALUATE
    with holder as base:
        cases = _Cases(base)
        good = _encode(_lines())

        # ---------- the baseline ----------
        records = cases.fresh()
        (records / (OID + RECORD_SUFFIX)).write_bytes(good)
        code, text = cases.push(_branch_line(), records=records)
        check("pre-push/well-formed-pass-allows", (code, "REFUSED" in text), (0, False))

        # ---------- stdin and scope ----------
        malformed = "refs/heads/main {} refs/heads/main\n".format(OID)
        check("stdin/malformed-line-2", cases.push(malformed)[0], 2)
        check("stdin/empty-allows", cases.push("")[0], 0)
        deletion = "(delete) {} refs/heads/old {}\n".format("0" * 40, OTHER)
        check("scope/delete-skipped", cases.push(deletion)[0], 0)
        tag = "refs/tags/v1 {} refs/tags/v1 {}\n".format(OID, "0" * 40)
        code, text = cases.push(tag)
        check("scope/non-branch-ref-noted", (code, "NOTE: refs/tags/v1" in text), (0, True))
        # Each of these would be accepted (a run of deletions, or a deletion) were its limit dropped.
        many = deletion * (MAX_STDIN_BYTES // len(deletion) + 1)
        check("stdin/over-size-cannot-evaluate",
              check_push(many.encode("utf-8"), str(cases.fresh()), io.StringIO()), 2)
        check("stdin/no-final-lf-cannot-evaluate", cases.push(_branch_line()[:-1])[0], 2)
        not_utf8 = "(delete) {} refs/heads/old {}\n".format("0" * 40, OTHER).encode().replace(b"old", b"\xff")
        check("stdin/not-utf8-cannot-evaluate", check_push(not_utf8, str(cases.fresh()), io.StringIO()), 2)
        if red_on_revert:
            with _reverted("stdin-well-formed"):
                check("revert/stdin-well-formed-goes-red", cases.push(malformed)[0], 0)
            with _reverted("delete-exempt"):
                check("revert/delete-exempt-goes-red", cases.push(deletion)[0], 1)
            with _reverted("branch-scope"):
                check("revert/branch-scope-goes-red", cases.push(tag)[0], 1)

        # ---------- lookup and read ----------
        check("lookup/missing-record-refuses", cases.judge(), (1, "record-present"))

        def symlinked(records):
            target = records / "elsewhere.jsonl"
            target.write_bytes(good)
            os.symlink(target, records / (OID + RECORD_SUFFIX))

        def directory(records):
            (records / (OID + RECORD_SUFFIX)).mkdir()

        def fifo(records):
            os.mkfifo(records / (OID + RECORD_SUFFIX))

        oversize = _encode(_lines(gates=[("g{:06d}".format(i), 0) for i in range(30000)]))

        def touched(records):
            path = records / (OID + RECORD_SUFFIX)
            path.write_bytes(good)

            def bump():
                info = os.stat(path)
                os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 7 * 10 ** 9))

            global _AFTER_READ
            _AFTER_READ = bump

        def changed():
            global _AFTER_READ
            try:
                return cases.judge(setup=touched)
            finally:
                _AFTER_READ = None

        bad_utf8 = good.replace(b'"name":"a"', b'"name":"\xff"')
        bad_json =_encode(_lines()[:1]) + b'{"type":"gate","name":"x"\n' + _encode(_lines()[1:])
        duplicate = good.replace(b'"name":"a"', b'"name":"z","name":"a"')
        unknown = good.replace(b'"name":"a"', b'"name":"a","note":"x"')
        no_lf = good[:-1]
        trailing = good + _encode(_lines()[-1:])
        check("read/symlink-refuses", cases.judge(setup=symlinked), (1, "no-symlink"))
        check("read/directory-refuses", cases.judge(setup=directory), (1, "regular-file"))
        check("read/fifo-refuses", cases.judge(setup=fifo), (1, "regular-file"))
        check("read/over-size-refuses", cases.judge(oversize), (1, "size-limit"))
        check("read/changed-during-read-refuses", changed(), (1, "stable-read"))
        check("read/bad-utf8-refuses", cases.judge(bad_utf8), (1, "utf8"))
        check("read/bad-json-refuses", cases.judge(bad_json), (1, "json-lines"))
        check("read/duplicate-key-refuses", cases.judge(duplicate), (1, "no-duplicate-keys"))
        check("read/unknown-key-refuses", cases.judge(unknown), (1, "no-unknown-keys"))
        check("read/no-final-lf-refuses", cases.judge(no_lf), (1, "final-lf"))
        check("read/trailing-after-terminal-refuses", cases.judge(trailing), (1, "nothing-after-terminal"))

        def unreadable(records):
            path = records / (OID + RECORD_SUFFIX)
            path.write_bytes(good)
            os.chmod(path, 0)

        check("read/unreadable-refuses", cases.judge(setup=unreadable), (1, "unreadable"))
        carriage = good.replace(b'"name":"a"', b'"name":\r"a"')
        check("read/carriage-return-refuses", cases.judge(carriage), (1, "json-lines"))
        linked_target = cases.fresh()
        (linked_target / (OID + RECORD_SUFFIX)).write_bytes(good)
        linked_dir = Path(base) / "records-link"
        os.symlink(linked_target, linked_dir)
        check("lookup/records-dir-symlink-cannot-evaluate",
              cases.push(_branch_line(), records=linked_dir)[0], 2)
        closed_dir = cases.fresh()
        (closed_dir / (OID + RECORD_SUFFIX)).write_bytes(good)
        os.chmod(closed_dir, 0)
        try:
            closed = cases.push(_branch_line(), records=closed_dir)[0]
        finally:
            os.chmod(closed_dir, 0o700)
        check("lookup/unreadable-records-dir-cannot-evaluate", closed, 2)
        if red_on_revert:
            with _reverted("record-present"):
                check("revert/record-present-goes-red", cases.judge(), (0, None))
            with _reverted("no-symlink"):
                check("revert/no-symlink-goes-red", cases.judge(setup=symlinked), (0, None))
            with _reverted("regular-file"):
                # A directory or an empty FIFO holds no record, so with the rule off a later reason
                # refuses them (the read fails, or the record is empty): the row's own rule no longer fires.
                check("revert/regular-file-goes-red",
                      (cases.judge(setup=directory), cases.judge(setup=fifo)),
                      ((1, "unreadable"), (1, "first-is-start")))
            with _reverted("size-limit"):
                check("revert/size-limit-goes-red", cases.judge(oversize), (0, None))
            with _reverted("stable-read"):
                check("revert/stable-read-goes-red", changed(), (0, None))
            with _reverted("utf8"):
                check("revert/utf8-goes-red", cases.judge(bad_utf8), (0, None))
            with _reverted("json-lines"):
                check("revert/json-lines-goes-red", cases.judge(bad_json), (0, None))
            with _reverted("no-duplicate-keys"):
                check("revert/no-duplicate-keys-goes-red", cases.judge(duplicate), (0, None))
            with _reverted("no-unknown-keys"):
                check("revert/no-unknown-keys-goes-red", cases.judge(unknown), (0, None))
            with _reverted("final-lf"):
                check("revert/final-lf-goes-red", cases.judge(no_lf), (0, None))
            with _reverted("nothing-after-terminal"):
                check("revert/nothing-after-terminal-goes-red", cases.judge(trailing), (0, None))

        # ---------- record shape ----------
        version = _encode(_lines(terminal={"v": 2}))
        objs = _lines()
        first_not_start = _encode([objs[1]] + [objs[0]] + objs[2:3] + [dict(objs[-1], gates=1)])
        seed = _encode(_lines()[:-1])
        run_id = _encode(_lines(terminal={"run_id": RID2}))
        check("shape/version-mismatch-refuses", cases.judge(version), (1, "version"))
        check("shape/first-not-start-refuses", cases.judge(first_not_start), (1, "first-is-start"))
        check("shape/last-not-terminal-refuses", cases.judge(seed), (1, "last-is-terminal"))
        check("shape/run-id-mismatch-refuses", cases.judge(run_id), (1, "run-id-match"))
        check("shape/two-starts-refuses", cases.judge(_encode(objs[:1] + objs)), (1, "one-start"))
        format_types = []
        for bad_format in ([], {}):
            records = cases.fresh()
            (records / (OID + RECORD_SUFFIX)).write_bytes(_encode(_lines(start={"object_format": bad_format})))
            try:
                code, text = cases.push(_branch_line(), records=records)
                format_types.append((code, "REFUSED: refs/heads/main" in text, ": schema: " in text))
            except Exception as exc:  # the defect this row pins: a crash instead of a refusal
                format_types.append(type(exc).__name__)
        check("shape/object-format-type-refuses", format_types, [(1, True, True), (1, True, True)])
        if red_on_revert:
            with _reverted("version"):
                check("revert/version-goes-red", cases.judge(version), (0, None))
            with _reverted("first-is-start"):
                check("revert/first-is-start-goes-red", cases.judge(first_not_start), (0, None))
            with _reverted("last-is-terminal"):
                # The seed incident has no terminal at all, so it stays refused by the structural
                # no-terminal reason; the row's own rule no longer fires.
                check("revert/last-is-terminal-goes-red", cases.judge(seed), (1, "no-terminal"))
            with _reverted("run-id-match"):
                check("revert/run-id-match-goes-red", cases.judge(run_id), (0, None))

        # ---------- binding ----------
        start_other = _encode(_lines(start={"commit": OTHER}))
        terminal_other = _encode(_lines(terminal={"commit": OTHER}))
        head_moved = _encode(_lines(terminal={"head_at_end": OTHER}))
        check("bind/start-commit-mismatch-refuses", cases.judge(start_other), (1, "start-commit-bind"))
        check("bind/terminal-commit-mismatch-refuses", cases.judge(terminal_other), (1, "terminal-commit-bind"))
        check("bind/head-moved-refuses", cases.judge(head_moved), (1, "head-at-end-bind"))
        check("bind/sha256-object-format-accepted",
              (cases.judge(_encode(_lines(oid=OID256, fmt="sha256")), oid=OID256),
               cases.judge(_encode(_lines(oid=OID256, fmt="sha1")), oid=OID256)),
              ((0, None), (1, "schema")))
        if red_on_revert:
            with _reverted("start-commit-bind"):
                check("revert/start-commit-bind-goes-red", cases.judge(start_other), (0, None))
            with _reverted("terminal-commit-bind"):
                check("revert/terminal-commit-bind-goes-red", cases.judge(terminal_other), (0, None))
            with _reverted("head-at-end-bind"):
                check("revert/head-at-end-bind-goes-red", cases.judge(head_moved), (0, None))

        # ---------- clean tree ----------
        dirty_start = _encode(_lines(start={"clean_at_start": False}))
        dirty_end = _encode(_lines(terminal={"clean_at_end": False}))
        check("clean/dirty-at-start-refuses", cases.judge(dirty_start), (1, "clean-at-start"))
        check("clean/dirty-at-end-refuses", cases.judge(dirty_end), (1, "clean-at-end"))
        if red_on_revert:
            with _reverted("clean-at-start"):
                check("revert/clean-at-start-goes-red", cases.judge(dirty_start), (0, None))
            with _reverted("clean-at-end"):
                check("revert/clean-at-end-goes-red", cases.judge(dirty_end), (0, None))

        # ---------- status ----------
        nonzero = _encode(_lines(gates=(("a", 0), ("b", 1)), status=1))
        as_bool = _encode(_lines(terminal={"status": False}))
        zero_gates = _encode(_lines(gates=()))
        count_off = _encode(_lines(count=1))
        contradiction = _encode(_lines(gates=(("a", 0), ("b", 1)), status=0))
        check("status/nonzero-refuses", cases.judge(nonzero), (1, "status-zero"))
        check("status/bool-refuses", cases.judge(as_bool), (1, "int-not-bool"))
        check("status/zero-gates-refuses", cases.judge(zero_gates), (1, "gates-nonzero"))
        check("status/gate-count-mismatch-refuses", cases.judge(count_off), (1, "gate-count"))
        check("status/contradiction-refuses", cases.judge(contradiction), (1, "no-contradiction"))
        if red_on_revert:
            with _reverted("status-zero"):
                check("revert/status-zero-goes-red", cases.judge(nonzero), (0, None))
            with _reverted("int-not-bool"):
                check("revert/int-not-bool-goes-red", cases.judge(as_bool), (0, None))
            with _reverted("gates-nonzero"):
                check("revert/gates-nonzero-goes-red", cases.judge(zero_gates), (0, None))
            with _reverted("gate-count"):
                check("revert/gate-count-goes-red", cases.judge(count_off), (0, None))
            with _reverted("no-contradiction"):
                check("revert/no-contradiction-goes-red", cases.judge(contradiction), (0, None))

        # ---------- multiple refs and the report ----------
        records = cases.fresh()
        (records / (OID + RECORD_SUFFIX)).write_bytes(good)
        two = _branch_line(OID, "refs/heads/good") + _branch_line(OTHER, "refs/heads/bad")
        code, text = cases.push(two, records=records)
        check("multi/one-bad-of-two-refuses-naming-it",
              (code, "REFUSED: refs/heads/bad" in text, "REFUSED: refs/heads/good" in text,
               "OK: refs/heads/good" in text),
              (1, True, False, True))
        records = cases.fresh()
        (records / (OID + RECORD_SUFFIX)).write_bytes(nonzero)
        (records / (OTHER + RECORD_SUFFIX)).write_bytes(_encode(_lines(oid=OTHER)[:-1]))
        code, text = cases.push(_branch_line(OID, "refs/heads/x") + _branch_line(OTHER, "refs/heads/y"),
                                records=records)
        check("report/lists-every-gate-exit-read",
              (code, "gate lines read: a exit 0, b exit 1" in text, "terminal status 1, gates 2" in text,
               "terminal line missing" in text),
              (1, True, True, True))
        records = cases.fresh()
        (records / (OID + RECORD_SUFFIX)).write_bytes(_encode(_lines()[:2]) + b'{"type":\n')
        code, text = cases.push(_branch_line(), records=records)
        check("report/gate-lines-kept-after-malformed-line",
              (code, ": json-lines: line 3" in text, "gate lines read: a exit 0" in text), (1, True, True))

        # ---------- the supervisor ----------
        code, records, final, spawned, shown = _supervise_case(
            base, "exit", lines=(b"--- a ---\n", b"GATE FAILED: a (exit 3)\n", b"\n"), status=3)
        status = json.loads(final.read_bytes().splitlines()[-1])["status"] if final.exists() else None
        check("supervise/exit-propagated", (code, status, _judge_path(records), b"GATE FAILED" in shown),
              (3, 3, (1, "status-zero"), True))

        seen = []

        def watch(records, final):
            seen.append((final.exists(), len(_stray(records))))

        code, records, final, spawned, shown = _supervise_case(base, "running", during=watch)
        check("supervise/no-final-name-while-running",
              (code, set(seen), final.exists(), _stray(records)), (0, {(False, 1)}, True, []))

        try:
            _supervise_case(base, "killed", raise_at=1)
            interrupted = False
        except KeyboardInterrupt:
            interrupted = True
        records = Path(base) / "killed" / ".git" / RECORDS_DIR_NAME
        check("supervise/killed-runner-leaves-no-record",
              (interrupted, (records / (OID + RECORD_SUFFIX)).exists(), _stray(records)), (True, False, []))

        during_rerun = []

        def rerun_watch(records, final):
            during_rerun.append(final.exists())

        code, records, final, spawned, shown = _supervise_case(base, "rerun", prior=good, during=rerun_watch)
        new_run = json.loads(final.read_bytes().splitlines()[0])["run_id"] if final.exists() else None
        check("supervise/rerun-removes-prior-record",
              (code, set(during_rerun), new_run is not None and new_run != RID, _judge_path(records)),
              (0, {False}, True, (0, None)))

        code, records, final, spawned, shown = _supervise_case(base, "dirty", statuses=(b"?? x\0", b""))
        check("supervise/dirty-tree-refuses-start", (code, spawned, final.exists()), (2, [], False))
        if red_on_revert:
            with _reverted("supervise-clean-start"):
                code, records, final, spawned, shown = _supervise_case(base, "dirty-reverted",
                                                                       statuses=(b"?? x\0", b""))
                check("revert/supervise-clean-start-goes-red",
                      (code, len(spawned), _judge_path(records)), (0, 1, (1, "clean-at-start")))

        def ignored_sigchld(name):
            saved = signal.signal(signal.SIGCHLD, signal.SIG_IGN)
            try:
                code, records, final, spawned, shown = _supervise_case(base, name)
            finally:
                signal.signal(signal.SIGCHLD, saved)
            return code, len(spawned), final.exists()

        check("supervise/ignored-sigchld-refuses", ignored_sigchld("sigchld"), (2, 0, False))
        if red_on_revert:
            with _reverted("supervise-sigchld"):
                check("revert/supervise-sigchld-goes-red", ignored_sigchld("sigchld-reverted"), (0, 1, True))

        renames = []

        def recording_replace(src, dst):
            renames.append((Path(src).parent, Path(dst)))
            os.replace(src, dst)

        code, records, final, spawned, shown = _supervise_case(base, "publish", replace=recording_replace)
        check("supervise/publish-is-rename",
              (code, renames == [(records, final)], _judge_path(records)), (0, True, (0, None)))
        ran_in = spawned[0] if spawned else None
        check("supervise/runner-runs-in-removed-export",
              (ran_in is not None and ran_in != Path(base) / "publish" and ran_in.name == "tree"
               and ran_in.parent.name.startswith(EXPORT_PREFIX), ran_in is not None and ran_in.exists()),
              (True, False))

        code, records, final, spawned, shown = _supervise_case(base, "head-moved", heads=(OID, OID, OTHER))
        check("supervise/head-at-end-recorded", (code, _judge_path(records)), (0, (1, "head-at-end-bind")))
        code, records, final, spawned, shown = _supervise_case(base, "dirty-end", statuses=(b"", b"", b"?? y\0"))
        check("supervise/clean-at-end-recorded", (code, _judge_path(records)), (0, (1, "clean-at-end")))
        code, records, final, spawned, shown = _supervise_case(base, "end-unread", heads=(OID, OID, None))
        check("supervise/end-read-failure-publishes-nothing", (code, final.exists(), _stray(records)),
              (0, False, []))
        code, records, final, spawned, shown = _supervise_case(base, "records-link", records_link=True)
        check("supervise/records-dir-symlink-refuses",
              (code, spawned, sorted(os.listdir(Path(base) / "records-link" / "elsewhere"))), (2, [], []))

        def failing_replace(src, dst):
            raise OSError(errno.EIO, "injected rename failure")

        messages = []
        code, records, final, spawned, shown = _supervise_case(base, "rename-fails", replace=failing_replace,
                                                               messages=messages)
        check("supervise/rename-failure-publishes-nothing",
              (code, final.exists(), _stray(records), "could not be published" in messages[0]),
              (0, False, [], True))

        def failing_sync(path):
            raise OSError(errno.EIO, "injected directory sync failure")

        messages = []
        code, records, final, spawned, shown = _supervise_case(base, "sync-fails", sync_dir=failing_sync,
                                                               messages=messages)
        check("supervise/sync-failure-after-rename-says-published",
              (code, _judge_path(records), "is published" in messages[0], "stays refused" in messages[0]),
              (0, (0, None), True, False))

        saved_git_dir = os.environ.get("GIT_DIR")
        os.environ["GIT_DIR"] = str(Path(base) / "inherited-git-dir")
        try:
            git_env, runner_env = _git_env(), _runner_env()
        finally:
            if saved_git_dir is None:
                del os.environ["GIT_DIR"]
            else:
                os.environ["GIT_DIR"] = saved_git_dir
        check("supervise/inherited-git-vars-dropped",
              ("GIT_DIR" in git_env, "GIT_DIR" in runner_env, git_env.get("GIT_OPTIONAL_LOCKS")),
              (False, False, "0"))

        # ---------- the real runner child ----------
        def real_child(name, script, link=False):
            where = Path(base) / name
            (where / "tools").mkdir(parents=True)
            target = where / "tools" / ("elsewhere.sh" if link else "run_all_checks.sh")
            target.write_text(script, encoding="utf-8")
            if link:
                os.symlink(target, where / RUNNER_REL)
            try:
                child = _RunnerChild(where)
            except CannotEvaluate:
                return "cannot evaluate"
            shown = b"".join(child.lines())
            return child.wait(), shown

        try:
            child_rows = (real_child("child-exit", "printf 'x\\n'\nexit 3\n"),
                          real_child("child-signal", "printf 'x\\n'\nkill -KILL $$\n"),
                          real_child("child-link", "exit 0\n", link=True))
        except (OSError, subprocess.SubprocessError) as exc:
            child_rows = ("child run failed: {}".format(exc),)
        check("child/real-runner-status-signal-and-link", child_rows,
              ((3, b"x\n"), (128 + signal.SIGKILL, b"x\n"), "cannot evaluate"))

        # ---------- real git (repositories made with git init, hermetic git environment) ----------
        genv = _hermetic_git_env(base)
        gated = "printf -- '--- a ---\\n'\nif [ -f local.flag ]; then exit 0; fi\nexit 7\n"
        files = {RUNNER_REL: gated, ".gitignore": "local.flag\n"}

        def hidden(name, how):
            repo, oid = _real_repo(base, name, files, genv)
            if how == "ignored":
                (repo / "local.flag").write_text("x\n", encoding="utf-8")
            else:
                _git_fixture(genv, repo, "update-index", how, RUNNER_REL)
                (repo / RUNNER_REL).write_text("printf -- '--- a ---\\n'\nexit 0\n", encoding="utf-8")
            hidden_status = _git_fixture(genv, repo, "status", "--porcelain", "--untracked-files=all")
            code, shown, said = _real_supervise(repo, genv)
            return hidden_status, code, _judge_path(_real_records(repo), oid)

        try:
            hidden_rows = (hidden("real-assume", "--assume-unchanged"), hidden("real-skip", "--skip-worktree"),
                           hidden("real-ignored", "ignored"))
        except (OSError, subprocess.SubprocessError) as exc:
            hidden_rows = ("real git fixture failed: {}".format(exc),)
        check("realgit/hidden-local-change-not-recorded-as-pass", hidden_rows,
              ((b"", 7, (1, "status-zero")),) * 3)

        try:
            repo, oid = _real_repo(base, "real-pass", {RUNNER_REL: "printf -- '--- a ---\\n'\npwd\nexit 0\n"},
                                   genv)
            code, shown, said = _real_supervise(repo, genv)
            ran = Path(shown.decode("utf-8", "replace").splitlines()[-1])
            buffer = io.StringIO()
            pushed = check_push(_branch_line(oid).encode("utf-8"), str(_real_records(repo)), buffer)
            real_pass = (code, pushed, ran != repo and ran.name == "tree", ran.exists())
        except (OSError, subprocess.SubprocessError, IndexError) as exc:
            real_pass = ("real git fixture failed: {}".format(exc),)
        check("realgit/pass-recorded-and-push-allowed", real_pass, (0, 0, True, False))

        # ---------- the hook shim ----------
        hook_base = Path(base) / "hook"
        hook_base.mkdir()
        try:
            code, argv, want_dir, failed = _run_hook_shim(hook_base, genv)
            shim = (code, argv, failed)
        except (OSError, subprocess.SubprocessError) as exc:
            shim, want_dir = ("shim run failed: {}".format(exc),), None
        check("hook/shim-execs-and-propagates", shim,
              (7, ["-I", "-B", TOOL_REL, "--pre-push", "--records", want_dir,
                   "origin", "https://example.invalid/repo"], 2))

    if FAILURES:
        print("SELF-TEST FAIL ({} of {} checks):".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  " + failure)
        return EXIT_REFUSED
    print("SELF-TEST PASS: {} checks{}".format(len(EXECUTED), " (red-on-revert)" if red_on_revert else ""))
    return EXIT_OK


if __name__ == "__main__":
    _selftest_exit_report.exit_with(main(sys.argv[1:]))
