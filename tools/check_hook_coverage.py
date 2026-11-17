#!/usr/bin/env python3
"""Pre-launch hook-coverage gate: does the launch directory's project settings register every declared hook?

  check_hook_coverage.py --launch-dir ABS_DIR --require ABS_FILE
                                         exit 0 every required entry present, 1 a required entry missing
                                         (each one named) or disableAllHooks true in a settings file (each
                                         one named), 2 cannot evaluate (fail closed)
  check_hook_coverage.py --self-test     assert every row over tempdir fixtures, each mutation row red
                                         with its check reverted
  check_hook_coverage.py [--self-test] --execution-report ABS_PATH
  any other argument list is refused with exit 2

A session started from a directory whose project settings do not register the declared guards runs
without them, and a check registered in the scope that failed to load cannot report its own absence. This
gate therefore runs OUTSIDE the session: the orchestrator runs it before it starts one. Both paths are
given on the command line and must be absolute; nothing is discovered from the working directory, because
the failure being caught is a wrong working directory.

The declaration (--require) is a JSON object, a registry file such as the orchestration registry included,
whose "required_hooks" key holds a non-empty list of entries; the gate reads only that key and ignores every
other top-level key, and a file without it exits 2. Each entry is an object holding exactly the keys scope,
event, matcher and command, each a string; scope must be "project", event and command must be non-empty.
The whole file is still read under the rules below, so a duplicated key or a NaN constant anywhere in it
exits 2. The gate reads ONLY <launch>/.claude/settings.json and <launch>/.claude/settings.local.json. An
entry is present when either file has, under hooks.<event>, a group whose matcher equals the declared
matcher (a group with no matcher reads as the empty matcher) and that group holds a hook of type "command"
whose command equals the declared command, all compared as exact strings. An absent .claude directory or an
absent settings file contributes no entries; a settings file holding JSON null, or any other value that is
not an object, is malformed (exit 2), never read as absent.

Exit 0: every required entry is present and neither settings file sets disableAllHooks to true. Exit 1: at
least one is missing, each missing entry named on its own MISSING line, or a settings file sets
disableAllHooks to true, each such file named on its own DISABLED line. A declared command that contains
CLAUDE_PROJECT_DIR is named on its own WARN line, whatever the exit code; the warning never changes it.
Exit 2 (fail closed, never a pass):
a relative launch directory or declaration path; a launch directory or declaration reached through a
symbolic link anywhere on its path (each directory on the path is opened from the root one component at a
time without following a link, and the final file with O_NOFOLLOW, so pass paths with every link resolved;
a directory on the path that cannot be searched, or where the platform has no O_PATH cannot be read, also
exits 2); a launch directory that is absent or not a directory; a .claude that is a symbolic link or not a
directory; a settings file or declaration that is a symbolic link, not a regular file (a FIFO is opened
non-blocking and refused without a read), unreadable, larger than MAX_BYTES, not UTF-8 (decoded strictly),
not JSON (a duplicated object key or a NaN, Infinity or -Infinity constant included; a number literal too
large for a float, such as 1e999, is not refused and reads as infinity) or not a JSON object; a hooks
value, event list, group, group matcher, group hook list, hook, hook type, command hook's command or
disableAllHooks of the wrong JSON type; and a declaration with no required_hooks key, a required_hooks that
is not a list or is empty, an entry with an unknown or missing key, a value of the wrong type, an empty
event or command, or a scope other than "project".

RESIDUALS (what this gate does not establish):
- It predicts loading from files on disk; it never observes what the client actually loaded. Outside its
  view (inferred, not documented in this repository): command-line settings flags, managed policy, user
  scope settings, plugin enablement, workspace trust, client-version differences in how settings scopes
  are resolved or merged, settings keys other than hooks and disableAllHooks that change whether hooks
  run, and edits made between this check and the launch (a write can still change the settings after the
  gate passes).
- It reads the two project settings files as a union (an entry in either counts); that the client merges
  the two files' hooks that way is inferred, not documented here. That disableAllHooks set to true switches
  hooks off is also inferred, not documented here; the gate fails on it (exit 1, the safe direction).
- It protects only launches that call it: a session started by hand is still unnoticed, and no launcher
  this repository ships calls it yet, so it protects no launch until an orchestrator's own launch path runs
  it.
- Exact string matching fails a launch whose hook command or matcher is equivalent but spelt differently;
  that errs on the safe side (exit 1, never a false pass).
- It does not check that a registered command can run at the launch directory: a script missing under
  the project directory, a missing interpreter, a timeout or an error exit all still pass. It only prints a
  WARN line, with the exit code unchanged, when a declared command contains CLAUDE_PROJECT_DIR, a path
  it never resolves.
- A declared scope other than "project" exits 2, because user and managed settings sources are outside
  what this repository documents.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_hook_coverage.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import json
import os
import stat
import tempfile
from pathlib import Path

MAX_BYTES = 1048576
SETTINGS_DIR = ".claude"
SETTINGS_NAMES = ("settings.json", "settings.local.json")
DECL_KEY = "required_hooks"
DECL_ENTRY_KEYS = frozenset({"scope", "event", "matcher", "command"})
SUPPORTED_SCOPES = frozenset({"project"})
DISABLE_KEY = "disableAllHooks"
PROJECT_DIR_VAR = "CLAUDE_PROJECT_DIR"
# The exceptions a JSON parse can raise on bad input; the self-test reverts it to (ValueError,) to show the
# deep-nesting row turns red.
_JSON_ERRORS = (ValueError, RecursionError)
# The flags every settings and declaration open carries: never follow a final symbolic link, never block on
# a FIFO, never leak the descriptor. The self-test reverts _NOFOLLOW to show the symlink row turns red.
_NOFOLLOW = os.O_NOFOLLOW
_OPEN_FLAGS_BASE = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC
# The flags every directory open carries; the self-test reverts it (O_DIRECTORY dropped) to show the
# not-a-directory rows turn red.
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NONBLOCK | os.O_CLOEXEC
# The path walk opens each directory with O_PATH where the platform has it (Linux), so a directory the
# reader may search but not list (mode 0711, say) can still be walked; elsewhere it falls back to a read
# open, which refuses (exit 2) such a directory.
_O_PATH = getattr(os, "O_PATH", 0)
# What _load_json_file returns for an absent file, distinct from every parsed JSON value (JSON null
# included); the self-test reverts it to None to show the null-settings rows turn red.
_ABSENT = object()


class CannotEvaluate(Exception):
    """An input that cannot be judged: a bad path, an unreadable or malformed file, or a bad declaration."""


# ---------- the checks, each a named function so the self-test can revert it and watch its row turn red ----


def _check_absolute(path, what):
    if not os.path.isabs(path):
        raise CannotEvaluate("{} {!r} is not an absolute path".format(what, path))


def _check_size(data, where):
    if len(data) > MAX_BYTES:
        raise CannotEvaluate("{}: larger than {} bytes".format(where, MAX_BYTES))


def _decode(data, where):
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{}: not UTF-8 ({})".format(where, exc)) from None


def _expect(value, kind, where):
    """True when value has the JSON type kind; otherwise CannotEvaluate (a wrong type is never skipped)."""
    if isinstance(value, bool) or not isinstance(value, kind):
        raise CannotEvaluate("{}: expected {}, found {}".format(where, kind.__name__, type(value).__name__))
    return True


def _check_decl_nonempty(entries, where):
    if not entries:
        raise CannotEvaluate("{}: required_hooks is empty".format(where))


def _check_decl_keys(keys, allowed, where):
    unknown = sorted(set(keys) - allowed)
    missing = sorted(allowed - set(keys))
    if unknown or missing:
        raise CannotEvaluate("{}: unknown key(s) {} and missing key(s) {}".format(where, unknown, missing))


def _check_decl_doc(doc, where):
    _expect(doc, dict, where)


def _check_decl_entry(entry, where):
    _expect(entry, dict, where)


def _is_command_hook(hook):
    return hook.get("type") == "command"


def _project_dir_warnings(required):
    """A WARN line for each declared command that contains PROJECT_DIR_VAR (never a change to the exit)."""
    return ["WARN: declared command {!r} contains {}; this gate does not check that it can run at the launch "
            "directory".format(command, PROJECT_DIR_VAR) for _event, _matcher, command in required
            if PROJECT_DIR_VAR in command]


def _check_decl_scope(scope, where):
    if scope not in SUPPORTED_SCOPES:
        raise CannotEvaluate("{}: scope {!r} is not supported (only project settings are documented)"
                             .format(where, scope))


def _check_decl_has_key(doc, where):
    if DECL_KEY not in doc:
        raise CannotEvaluate("{}: no {} key".format(where, DECL_KEY))


def _check_decl_list(entries, where):
    _expect(entries, list, where + ": " + DECL_KEY)


def _check_decl_value_types(entry, where):
    for key in sorted(DECL_ENTRY_KEYS):
        if key in entry:
            _expect(entry[key], str, "{}.{}".format(where, key))


def _check_decl_nonempty_fields(entry, where):
    if not entry.get("event") or not entry.get("command"):
        raise CannotEvaluate("{}: event and command must be non-empty".format(where))


def _check_hook_type(value, where):
    _expect(value, str, where)


def _check_regular(mode, where):
    if not stat.S_ISREG(mode):
        raise CannotEvaluate("{}: not a regular file".format(where))


def _hooks_disabled(doc, where):
    """True when the settings object sets disableAllHooks to true; CannotEvaluate when it is not a boolean."""
    if DISABLE_KEY not in doc:
        return False
    value = doc[DISABLE_KEY]
    if not isinstance(value, bool):
        raise CannotEvaluate("{}: {}: expected bool, found {}".format(where, DISABLE_KEY, type(value).__name__))
    return value


def _same_event(declared, found):
    return declared == found


def _same_matcher(declared, found):
    return declared == found


def _same_command(declared, found):
    return declared == found


# ---------- reading ----------


def _json_pairs(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError("duplicated object key {!r}".format(key))
        seen[key] = value
    return seen


def _json_constant(name):
    raise ValueError("non-finite constant {}".format(name))


def _parse_json(text, where):
    try:
        return json.loads(text, object_pairs_hook=_json_pairs, parse_constant=_json_constant)
    except _JSON_ERRORS as exc:
        raise CannotEvaluate("{}: not valid JSON ({})".format(where, exc)) from None


def _open_file(name, dir_fd, where):
    """A descriptor for name (relative to dir_fd, or absolute with dir_fd None), or None when it is absent."""
    try:
        return os.open(name, _OPEN_FLAGS_BASE | _NOFOLLOW, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CannotEvaluate("{}: cannot open ({})".format(where, exc)) from None
    except ValueError as exc:
        raise CannotEvaluate("{}: cannot open ({})".format(where, exc)) from None


def _read_fd(fd, where):
    """The bytes of an open descriptor, at most MAX_BYTES + 1 of them; a non-regular file is refused unread."""
    try:
        _check_regular(os.fstat(fd).st_mode, where)
        chunks, total = [], 0
        while total <= MAX_BYTES:
            chunk = os.read(fd, MAX_BYTES + 1 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks)
    except OSError as exc:
        raise CannotEvaluate("{}: unreadable ({})".format(where, exc)) from None
    finally:
        os.close(fd)


def _load_json_file(name, dir_fd, where, absent_ok):
    """The parsed JSON value of name, or _ABSENT when the file is absent and absent_ok is set."""
    fd = _open_file(name, dir_fd, where)
    if fd is None:
        if absent_ok:
            return _ABSENT
        raise CannotEvaluate("{}: absent".format(where))
    data = _read_fd(fd, where)
    _check_size(data, where)
    return _parse_json(_decode(data, where), where)


def _open_dir_nofollow(path, what):
    """A descriptor for the directory path, opened one component at a time (from the root for an absolute
    path) with no symbolic link followed anywhere on it; CannotEvaluate when a component is absent, a
    symbolic link or not a directory."""
    fd = None
    try:
        fd = os.open("/" if path.startswith("/") else ".", _DIR_FLAGS | _O_PATH)
        for part in path.split("/"):
            if part in ("", "."):
                continue
            parent, fd = fd, None
            try:
                fd = os.open(part, _DIR_FLAGS | _O_PATH | _NOFOLLOW, dir_fd=parent)
            finally:
                os.close(parent)
        return fd
    except (OSError, ValueError) as exc:
        if fd is not None:
            os.close(fd)
        raise CannotEvaluate("{} {!r} is not a directory that can be opened without following a symbolic "
                             "link anywhere on its path ({})".format(what, path, exc)) from None


def load_declaration(path):
    """The declared entries as (event, matcher, command) tuples; CannotEvaluate on anything malformed."""
    where = "declaration {}".format(path)
    _check_absolute(path, "declaration")
    head, _sep, name = path.rpartition("/")
    if name in ("", ".", ".."):
        raise CannotEvaluate("{}: does not name a file".format(where))
    dfd = _open_dir_nofollow(head or "/", "declaration directory")
    try:
        doc = _load_json_file(name, dfd, where, absent_ok=False)
    finally:
        os.close(dfd)
    _check_decl_doc(doc, where)
    _check_decl_has_key(doc, where)
    entries = doc[DECL_KEY]
    _check_decl_list(entries, where)
    _check_decl_nonempty(entries, where)
    required = []
    for index, entry in enumerate(entries):
        at = "{}: {}[{}]".format(where, DECL_KEY, index)
        _check_decl_entry(entry, at)
        _check_decl_keys(entry.keys(), DECL_ENTRY_KEYS, at)
        _check_decl_value_types(entry, at)
        _check_decl_scope(entry.get("scope"), at)
        _check_decl_nonempty_fields(entry, at)
        required.append((entry.get("event"), entry.get("matcher", ""), entry.get("command")))
    return required


def _settings_entries(doc, where):
    """Every (event, matcher, command) a parsed settings object registers; CannotEvaluate on a wrong type."""
    found = []
    if not _expect(doc, dict, where):
        return found
    if "hooks" not in doc:
        return found
    hooks = doc["hooks"]
    if not _expect(hooks, dict, where + ": hooks"):
        return found
    for event, groups in hooks.items():
        at = "{}: hooks.{}".format(where, event)
        if not _expect(groups, list, at):
            continue
        for gindex, group in enumerate(groups):
            gat = "{}[{}]".format(at, gindex)
            if not _expect(group, dict, gat):
                continue
            matcher = group.get("matcher", "")
            if not _expect(matcher, str, gat + ".matcher"):
                continue
            inner = group.get("hooks", [])
            if not _expect(inner, list, gat + ".hooks"):
                continue
            for hindex, hook in enumerate(inner):
                hat = "{}.hooks[{}]".format(gat, hindex)
                if not _expect(hook, dict, hat):
                    continue
                _check_hook_type(hook.get("type"), hat + ".type")
                if not _is_command_hook(hook):
                    continue
                if not _expect(hook.get("command"), str, hat + ".command"):
                    continue
                found.append((event, matcher, hook["command"]))
    return found


def load_settings(launch_dir):
    """(every entry the launch directory's two project settings files register, as one list; the path of
    each of those files that sets disableAllHooks to true)."""
    _check_absolute(launch_dir, "launch directory")
    lfd = _open_dir_nofollow(launch_dir, "launch directory")
    try:
        try:
            cfd = os.open(SETTINGS_DIR, _DIR_FLAGS | _NOFOLLOW, dir_fd=lfd)
        except FileNotFoundError:
            return [], []
        except OSError as exc:
            raise CannotEvaluate("{}/{}: not a directory that can be opened without following a link ({})"
                                 .format(launch_dir, SETTINGS_DIR, exc)) from None
    finally:
        os.close(lfd)
    try:
        found, disabled = [], []
        for name in SETTINGS_NAMES:
            where = "{}/{}/{}".format(launch_dir.rstrip("/") or "/", SETTINGS_DIR, name)
            doc = _load_json_file(name, cfd, where, absent_ok=True)
            if doc is _ABSENT:
                continue
            found.extend(_settings_entries(doc, where))
            if _hooks_disabled(doc, where):
                disabled.append(where)
        return found, disabled
    finally:
        os.close(cfd)


def missing_entries(required, found):
    """The required entries no found entry matches, in declaration order."""
    missing = []
    for event, matcher, command in required:
        if not any(_same_event(event, f_event) and _same_matcher(matcher, f_matcher)
                   and _same_command(command, f_command) for f_event, f_matcher, f_command in found):
            missing.append((event, matcher, command))
    return missing


def evaluate(launch_dir, require):
    """(exit code, stdout lines, stderr lines) for one launch directory and one declaration."""
    try:
        required = load_declaration(require)
        found, disabled = load_settings(launch_dir)
    except CannotEvaluate as exc:
        return 2, [], ["CANNOT EVALUATE: " + str(exc)]
    missing = missing_entries(required, found)
    warnings = _project_dir_warnings(required)
    if missing or disabled:
        out = warnings + ["DISABLED: {} sets {} to true".format(where, DISABLE_KEY) for where in disabled]
        out.extend("MISSING: event={!r} matcher={!r} command={!r}".format(*entry) for entry in missing)
        if disabled:
            out.append("FAIL: {} project settings file{} at {} set{} {} to true".format(
                len(disabled), "" if len(disabled) == 1 else "s", launch_dir,
                "s" if len(disabled) == 1 else "", DISABLE_KEY))
        if missing:
            out.append("FAIL: {} of {} required hook entr{} missing from the project settings at {}"
                       .format(len(missing), len(required), "y" if len(required) == 1 else "ies", launch_dir))
        return 1, out, []
    return 0, warnings + ["PASS: all {} required hook entr{} present in the project settings at {}".format(
        len(required), "y" if len(required) == 1 else "ies", launch_dir)], []


def run_gate(launch_dir, require):
    code, out, err = evaluate(launch_dir, require)
    for line in err:
        print(line, file=sys.stderr)
    for line in out:
        print(line)
    return code


# ---------- self-test ----------

# The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id record the
# instrumented check() helper appends to; the execution gate reconciles the report against the registry.
SUITE_ID = "hook-coverage-selftest"
EXECUTED = []
FAILURES = []

GUARD_A = ("PreToolUse", "Bash", "python3 -I /opt/guard/a.py")
GUARD_B = ("PreToolUse", "Write|Edit", "python3 -I /opt/guard/b.py")


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


def _decl(entries, scope="project"):
    return {"required_hooks": [{"scope": scope, "event": e, "matcher": m, "command": c} for e, m, c in entries]}


def _settings(entries):
    hooks = {}
    for event, matcher, command in entries:
        hooks.setdefault(event, []).append(
            {"matcher": matcher, "hooks": [{"type": "command", "command": command, "timeout": 10}]})
    return {"hooks": hooks}


def _write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = body if isinstance(body, bytes) else (
        body.encode("utf-8") if isinstance(body, str) else json.dumps(body, indent=2).encode("utf-8"))
    path.write_bytes(data)


class _Case:
    """A tempdir holding a launch directory and a declaration file; settings bodies are written on request."""

    def __init__(self, decl=None):
        self.tmp = tempfile.TemporaryDirectory(prefix="hook-coverage-selftest-")
        # Every link above the temporary directory resolved, since the gate refuses a path through one.
        self.base = Path(os.path.realpath(self.tmp.name))
        self.launch = self.base / "launch"
        self.launch.mkdir()
        self.require = self.base / "require.json"
        _write(self.require, _decl([GUARD_A, GUARD_B]) if decl is None else decl)

    def settings(self, body, name="settings.json"):
        _write(self.launch / SETTINGS_DIR / name, body)
        return self

    def run(self, launch=None, require=None):
        return evaluate(str(self.launch) if launch is None else launch,
                        str(self.require) if require is None else require)

    def close(self):
        self.tmp.cleanup()


def _code(case, **kwargs):
    try:
        return case.run(**kwargs)[0]
    finally:
        case.close()


def _result(case, **kwargs):
    """(exit code, stdout lines, stderr lines) for case, closed afterwards."""
    try:
        return case.run(**kwargs)
    finally:
        case.close()


def _guarded(thunk):
    """thunk()'s result, or a description of the exception it raised (a reverted check may crash, and a
    crash is never the expected exit)."""
    try:
        return thunk()
    except Exception as exc:
        return "raised " + type(exc).__name__


def _swap(name, value):
    """The module global name's current value, after storing value in its place. Each revertible global is
    spelled as a literal key (tools/gen_renderers.py refuses a computed globals() key); any other name raises
    KeyError, as a lookup of an absent global does."""
    match name:
        case "_same_matcher":
            saved, globals()["_same_matcher"] = globals()["_same_matcher"], value
        case "_same_command":
            saved, globals()["_same_command"] = globals()["_same_command"], value
        case "_same_event":
            saved, globals()["_same_event"] = globals()["_same_event"], value
        case "SETTINGS_NAMES":
            saved, globals()["SETTINGS_NAMES"] = globals()["SETTINGS_NAMES"], value
        case "_decode":
            saved, globals()["_decode"] = globals()["_decode"], value
        case "_NOFOLLOW":
            saved, globals()["_NOFOLLOW"] = globals()["_NOFOLLOW"], value
        case "_DIR_FLAGS":
            saved, globals()["_DIR_FLAGS"] = globals()["_DIR_FLAGS"], value
        case "_check_regular":
            saved, globals()["_check_regular"] = globals()["_check_regular"], value
        case "MAX_BYTES":
            saved, globals()["MAX_BYTES"] = globals()["MAX_BYTES"], value
        case "_expect":
            saved, globals()["_expect"] = globals()["_expect"], value
        case "_check_hook_type":
            saved, globals()["_check_hook_type"] = globals()["_check_hook_type"], value
        case "_json_constant":
            saved, globals()["_json_constant"] = globals()["_json_constant"], value
        case "_ABSENT":
            saved, globals()["_ABSENT"] = globals()["_ABSENT"], value
        case "_hooks_disabled":
            saved, globals()["_hooks_disabled"] = globals()["_hooks_disabled"], value
        case "_check_absolute":
            saved, globals()["_check_absolute"] = globals()["_check_absolute"], value
        case "_open_dir_nofollow":
            saved, globals()["_open_dir_nofollow"] = globals()["_open_dir_nofollow"], value
        case "_check_decl_nonempty":
            saved, globals()["_check_decl_nonempty"] = globals()["_check_decl_nonempty"], value
        case "_check_decl_has_key":
            saved, globals()["_check_decl_has_key"] = globals()["_check_decl_has_key"], value
        case "_check_decl_list":
            saved, globals()["_check_decl_list"] = globals()["_check_decl_list"], value
        case "_check_decl_value_types":
            saved, globals()["_check_decl_value_types"] = globals()["_check_decl_value_types"], value
        case "_check_decl_nonempty_fields":
            saved, globals()["_check_decl_nonempty_fields"] = globals()["_check_decl_nonempty_fields"], value
        case "_check_decl_keys":
            saved, globals()["_check_decl_keys"] = globals()["_check_decl_keys"], value
        case "_check_decl_scope":
            saved, globals()["_check_decl_scope"] = globals()["_check_decl_scope"], value
        case "_check_decl_doc":
            saved, globals()["_check_decl_doc"] = globals()["_check_decl_doc"], value
        case "_check_decl_entry":
            saved, globals()["_check_decl_entry"] = globals()["_check_decl_entry"], value
        case "_is_command_hook":
            saved, globals()["_is_command_hook"] = globals()["_is_command_hook"], value
        case "_project_dir_warnings":
            saved, globals()["_project_dir_warnings"] = globals()["_project_dir_warnings"], value
        case "_JSON_ERRORS":
            saved, globals()["_JSON_ERRORS"] = globals()["_JSON_ERRORS"], value
        case "_check_gate_arity":
            saved, globals()["_check_gate_arity"] = globals()["_check_gate_arity"], value
        case _:
            raise KeyError(name)
    return saved


def _reverted(name, replacement, thunk):
    """thunk()'s result with the module global name temporarily replaced (a check reverted)."""
    saved = _swap(name, replacement)
    try:
        return thunk()
    finally:
        _swap(name, saved)


def _lenient_expect(value, kind, where):
    return not isinstance(value, bool) and isinstance(value, kind)


def _unknown_keys_only(keys, allowed, where):
    """_check_decl_keys with its missing-key half reverted: only an unknown key is refused."""
    unknown = sorted(set(keys) - allowed)
    if unknown:
        raise CannotEvaluate("{}: unknown key(s) {}".format(where, unknown))


def _run(make, revert=None):
    """(exit code, mutant exit code or None): make() builds a fresh case and returns its exit code; with
    revert=(global, replacement) the same row runs again with that check reverted, and a mutation row passes
    only when the mutant's code differs from the expected one (the row turns red without its check)."""
    got = make()
    mutant = None if revert is None else _reverted(revert[0], revert[1], make)
    return got, mutant


def _denied_open(target):
    """An os.open that refuses target with EACCES, as an unreadable file would."""
    real_open = os.open

    def _open(path, flags, mode=0o777, *, dir_fd=None):
        if path == target:
            raise PermissionError(13, "open refused by the self-test", path)
        return real_open(path, flags, mode, dir_fd=dir_fd)
    return real_open, _open


def _self_test():
    good = _settings([GUARD_A, GUARD_B])

    # Row 1. All required entries present.
    got, _m = _run(lambda: _code(_Case().settings(good)))
    check("pass/all-present", got == 0, got)

    # Row 2. No settings.json at the launch directory: exit 1, naming each missing entry.
    case = _Case()
    try:
        (case.launch / SETTINGS_DIR).mkdir()
        code, out, _err = case.run()
    finally:
        case.close()
    named = all(any(line.startswith("MISSING:") and repr(cmd) in line for line in out)
                for _e, _m2, cmd in (GUARD_A, GUARD_B))
    check("missing/no-settings-file", code == 1 and named and len(out) == 3
          and out[-1].startswith("FAIL: 2 of 2"), (code, out))
    got, _m = _run(lambda: _code(_Case()))
    check("missing/no-claude-dir-exit-1", got == 1, got)

    # Row 3. Settings present but no PreToolUse key.
    got, _m = _run(lambda: _code(_Case().settings({"hooks": {"PostToolUse": good["hooks"]["PreToolUse"]}})))
    check("missing/no-pretooluse-key", got == 1, got)
    got, _m = _run(lambda: _code(_Case().settings({"permissions": {}})))
    check("missing/no-hooks-key", got == 1, got)

    # Row 4. Matcher differs. Mutation row: a gate that ignores the matcher must fail this row.
    got, mutant = _run(
        lambda: _code(_Case().settings(_settings([("PreToolUse", "Bash|Write", GUARD_A[2]), GUARD_B]))),
        revert=("_same_matcher", lambda declared, found: True))
    check("missing/matcher-differs", got == 1, got)
    check("missing/matcher-differs/red-when-reverted", mutant != 1, mutant)
    # Row 5. Command differs by one character.
    got, mutant = _run(
        lambda: _code(_Case().settings(_settings([(GUARD_A[0], GUARD_A[1], GUARD_A[2][:-1] + "b"), GUARD_B]))),
        revert=("_same_command", lambda declared, found: True))
    check("missing/command-differs-one-char", got == 1, got)
    check("missing/command-differs-one-char/red-when-reverted", mutant != 1, mutant)
    # Row 6. Entry present only under the wrong event.
    got, mutant = _run(
        lambda: _code(_Case().settings(_settings([("PostToolUse", GUARD_A[1], GUARD_A[2]), GUARD_B]))),
        revert=("_same_event", lambda declared, found: True))
    check("missing/wrong-event", got == 1, got)
    check("missing/wrong-event/red-when-reverted", mutant != 1, mutant)
    # Row 6a. The exact required command carried by a hook whose type is not "command" (a prompt hook) is not
    # a registration: without the command-type filter it would count and the run would pass.
    got, mutant = _run(
        lambda: _code(_Case().settings({"hooks": {"PreToolUse": [
            {"matcher": GUARD_A[1], "hooks": [{"type": "prompt", "command": GUARD_A[2]}]},
            {"matcher": GUARD_B[1], "hooks": [{"type": "command", "command": GUARD_B[2]}]}]}})),
        revert=("_is_command_hook", lambda hook: True))
    check("missing/prompt-hook-not-a-command", got == 1, got)
    check("missing/prompt-hook-not-a-command/red-when-reverted", mutant != 1, mutant)

    # Row 7. Entry present only in settings.local.json.
    got, mutant = _run(
        lambda: _code(_Case().settings(_settings([GUARD_B])).settings(_settings([GUARD_A]),
                                                                       name="settings.local.json")),
        revert=("SETTINGS_NAMES", ("settings.json",)))
    check("pass/local-only", got == 0, got)
    check("pass/local-only/red-when-reverted", mutant != 0, mutant)

    # Row 8. Entry present twice: no false failure.
    got, _m = _run(lambda: _code(_Case().settings(_settings([GUARD_A, GUARD_A, GUARD_B]))))
    check("pass/present-twice", got == 0, got)
    got, _m = _run(lambda: _code(_Case().settings(good).settings(good, name="settings.local.json")))
    check("pass/present-in-both-files", got == 0, got)

    # Row 8a. A declared command containing CLAUDE_PROJECT_DIR is named on a WARN line and the exit code is
    # unchanged (the gate does not check that the command can run at the launch directory).
    project_dir_guard = ("PreToolUse", "Bash", 'python3 -I "$CLAUDE_PROJECT_DIR"/guard/a.py')

    def _project_dir_result():
        code, out, _err = _result(_Case(decl=_decl([project_dir_guard, GUARD_B])).settings(
            _settings([project_dir_guard, GUARD_B])))
        warned = [line for line in out if line.startswith("WARN: ")]
        return (code, len(warned) == 1 and repr(project_dir_guard[2]) in warned[0]
                and out[-1].startswith("PASS: "))
    got, mutant = _run(_project_dir_result, revert=("_project_dir_warnings", lambda required: []))
    check("warn/declared-command-names-project-dir", got == (0, True), got)
    check("warn/declared-command-names-project-dir/red-when-reverted", mutant != (0, True), mutant)

    # Row 9. Malformed JSON, non-UTF-8, symlinked file, FIFO (returns without hanging), oversized file.
    got, _m = _run(lambda: _code(_Case().settings('{"hooks": {')))
    check("refuse/malformed-json", got == 2, got)
    got, _m = _run(lambda: _code(_Case().settings('{"hooks": {}, "hooks": ' + json.dumps(good["hooks"]) + "}")))
    check("refuse/duplicate-key-json", got == 2, got)
    got, _m = _run(lambda: _code(_Case().settings(good).settings("[", name="settings.local.json")))
    check("refuse/malformed-local-file", got == 2, got)
    non_utf8 = json.dumps(good).encode("utf-8").replace(b'"timeout"', b'"t\xffimeout"', 1)
    got, mutant = _run(lambda: _code(_Case().settings(non_utf8)),
                       revert=("_decode", lambda data, where: data.decode("utf-8", errors="replace")))
    check("refuse/non-utf8", got == 2, got)
    check("refuse/non-utf8/red-when-reverted", mutant != 2, mutant)

    def _symlinked():
        case = _Case()
        _write(case.base / "real.json", good)
        (case.launch / SETTINGS_DIR).mkdir()
        os.symlink(case.base / "real.json", case.launch / SETTINGS_DIR / "settings.json")
        return _code(case)
    got, mutant = _run(_symlinked, revert=("_NOFOLLOW", 0))
    check("refuse/symlinked-file", got == 2, got)
    check("refuse/symlinked-file/red-when-reverted", mutant != 2, mutant)

    def _symlinked_dir():
        case = _Case()
        _write(case.base / "elsewhere" / "settings.json", good)
        os.symlink(case.base / "elsewhere", case.launch / SETTINGS_DIR)
        return _code(case)
    got, mutant = _run(_symlinked_dir, revert=("_NOFOLLOW", 0))
    check("refuse/symlinked-claude-dir", got == 2, got)
    check("refuse/symlinked-claude-dir/red-when-reverted", mutant != 2, mutant)

    def _fifo():
        case = _Case()
        (case.launch / SETTINGS_DIR).mkdir()
        os.mkfifo(case.launch / SETTINGS_DIR / "settings.json")
        return _code(case)
    got, _m = _run(_fifo)
    check("refuse/fifo-no-hang", got == 2, got)

    def _directory_in_place():
        case = _Case()
        (case.launch / SETTINGS_DIR / "settings.json").mkdir(parents=True)
        return _code(case)
    got, _m = _run(_directory_in_place)
    check("refuse/directory-in-place", got == 2, got)

    # Opened without O_DIRECTORY, a .claude that is a plain file would be refused one step later, at the
    # settings file, so the row asserts the refusal names .claude itself.
    def _claude_file():
        case = _Case()
        _write(case.launch / SETTINGS_DIR, "{}")
        code, _out, err = _result(case)
        return code, bool(err) and "/{}: not a directory".format(SETTINGS_DIR) in err[0]
    got, mutant = _run(_claude_file, revert=("_DIR_FLAGS", _DIR_FLAGS & ~os.O_DIRECTORY))
    check("refuse/claude-not-directory", got == (2, True), got)
    check("refuse/claude-not-directory/red-when-reverted", mutant != (2, True), mutant)

    # A FIFO holding valid settings and no writer: without the regular-file check its bytes would be read and
    # pass, so it is the row that shows that check.
    def _fifo_with_data():
        case = _Case()
        fifo = case.launch / SETTINGS_DIR / "settings.json"
        fifo.parent.mkdir()
        os.mkfifo(fifo)
        holder = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        try:
            writer = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
            try:
                os.write(writer, json.dumps(good).encode("utf-8"))
            finally:
                os.close(writer)
            return _code(case)
        finally:
            os.close(holder)
    got, mutant = _run(_fifo_with_data, revert=("_check_regular", lambda mode, where: None))
    check("refuse/fifo-with-data", got == 2, got)
    check("refuse/fifo-with-data/red-when-reverted", mutant != 2, mutant)

    padded = json.dumps(good) + " " * MAX_BYTES
    got, mutant = _run(lambda: _code(_Case().settings(padded)), revert=("MAX_BYTES", 4 * MAX_BYTES))
    check("refuse/oversized", got == 2, got)
    check("refuse/oversized/red-when-reverted", mutant != 2, mutant)
    # The size bound at its ends: one byte under, exactly at, and one byte over MAX_BYTES.
    base_len = len(json.dumps(good).encode("utf-8"))
    for name, extra, expected in (("bound/size-under", MAX_BYTES - base_len - 1, 0),
                                  ("bound/size-at", MAX_BYTES - base_len, 0),
                                  ("bound/size-over", MAX_BYTES - base_len + 1, 2)):
        got = _code(_Case().settings(json.dumps(good) + " " * extra))
        check(name, got == expected, (extra, got))

    real_read = os.read

    def _read_denied(fd, size):
        raise PermissionError(13, "read refused by the self-test")
    case = _Case().settings(good)
    os.read = _read_denied
    try:
        got = _code(case)
    finally:
        os.read = real_read
    check("refuse/unreadable-settings", got == 2, got)

    # Row 10. hooks or a group with the wrong type: exit 2, never a pass or a plain miss.
    for name, body in (
            ("refuse/hooks-not-object", {"hooks": []}),
            ("refuse/event-not-list", {"hooks": {"PreToolUse": {"matcher": "Bash"}}}),
            ("refuse/group-not-object", {"hooks": {"PreToolUse": good["hooks"]["PreToolUse"] + ["Bash"]}}),
            ("refuse/matcher-not-string", {"hooks": {"PreToolUse": good["hooks"]["PreToolUse"] + [
                {"matcher": ["Bash"], "hooks": []}]}}),
            ("refuse/group-hooks-not-list", {"hooks": {"PreToolUse": good["hooks"]["PreToolUse"] + [
                {"matcher": "Bash", "hooks": {"type": "command"}}]}}),
            ("refuse/command-not-string", {"hooks": {"PreToolUse": good["hooks"]["PreToolUse"] + [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": 7}]}]}}),
            ("refuse/settings-not-object", [good])):
        got = _code(_Case().settings(body))
        check(name, got == 2, got)
    # The mutation row for the type checks: a wrong-typed group and no required entry; with the type check
    # reverted (a wrong type skipped) it reads as a plain miss, exit 1, not 2.
    got, mutant = _run(lambda: _code(_Case().settings({"hooks": {"PreToolUse": ["Bash"]}})),
                       revert=("_expect", _lenient_expect))
    check("refuse/wrong-type-not-a-miss", got == 2, got)
    check("refuse/wrong-type-not-a-miss/red-when-reverted", mutant != 2, mutant)
    # A hook whose type is not a string, beside every required entry: without the type check it is skipped
    # as a non-command hook and the run passes.
    got, mutant = _run(lambda: _code(_Case().settings({"hooks": {"PreToolUse": good["hooks"]["PreToolUse"] + [
        {"matcher": "Bash", "hooks": [{"type": 7, "command": "x"}]}]}})),
        revert=("_check_hook_type", lambda value, where: None))
    check("refuse/hook-type-not-string", got == 2, got)
    check("refuse/hook-type-not-string/red-when-reverted", mutant != 2, mutant)
    # A NaN constant beside every required entry: without parse_constant it parses and the run passes.
    got, mutant = _run(lambda: _code(_Case().settings(json.dumps(good)[:-1] + ', "x": NaN}')),
                       revert=("_json_constant", float))
    check("refuse/nan-constant", got == 2, got)
    check("refuse/nan-constant/red-when-reverted", mutant != 2, mutant)
    # Deeply nested JSON exhausts the parser's recursion: refused (exit 2); without the RecursionError catch
    # the gate crashes instead.
    deep = "[" * 100000 + "]" * 100000
    got, mutant = _run(lambda: _guarded(lambda: _code(_Case().settings(deep))),
                       revert=("_JSON_ERRORS", (ValueError,)))
    check("refuse/deep-nesting", got == 2, got)
    check("refuse/deep-nesting/red-when-reverted", mutant != 2, mutant)

    # Row 10a. A settings file holding JSON null (or any other non-object) is malformed, never absent: alone
    # (not a plain miss) and beside a file with every required entry (not a pass).
    got, mutant = _run(lambda: _code(_Case().settings("null")), revert=("_ABSENT", None))
    check("refuse/settings-null", got == 2, got)
    check("refuse/settings-null/red-when-reverted", mutant != 2, mutant)
    got, mutant = _run(lambda: _code(_Case().settings(good).settings("null", name="settings.local.json")),
                       revert=("_ABSENT", None))
    check("refuse/settings-local-null-beside-good", got == 2, got)
    check("refuse/settings-local-null-beside-good/red-when-reverted", mutant != 2, mutant)
    got, _m = _run(lambda: _code(_Case().settings("7", name="settings.local.json")))
    check("refuse/settings-local-not-object", got == 2, got)

    # Row 10b. disableAllHooks true in either file is a named failure (exit 1) even with every entry present;
    # false passes, and a value that is not a boolean cannot be evaluated.
    def _disabled(name):
        case = _Case().settings(good)
        if name == "settings.json":
            return case.settings(dict(good, disableAllHooks=True))
        return case.settings({"disableAllHooks": True}, name=name)

    def _disabled_result(name):
        code, out, _err = _result(_disabled(name))
        ending = "/{} sets {} to true".format(name, DISABLE_KEY)
        named = any(line.startswith("DISABLED: ") and line.endswith(ending) for line in out)
        return code, named and out[-1].startswith("FAIL: 1 project settings file ")
    for name, row, red_row in (
            ("settings.json", "fail/disable-all-hooks/settings.json",
             "fail/disable-all-hooks/settings.json/red-when-reverted"),
            ("settings.local.json", "fail/disable-all-hooks/settings.local.json",
             "fail/disable-all-hooks/settings.local.json/red-when-reverted")):
        got, mutant = _run(lambda name=name: _disabled_result(name),
                           revert=("_hooks_disabled", lambda doc, where: False))
        check(row, got == (1, True), got)
        check(red_row, mutant != (1, True), mutant)
    got, _m = _run(lambda: _code(_Case().settings(dict(good, disableAllHooks=False))))
    check("pass/disable-all-hooks-false", got == 0, got)
    got, _m = _run(lambda: _code(_Case().settings(dict(good, disableAllHooks="true"))))
    check("refuse/disable-all-hooks-not-bool", got == 2, got)

    # Row 11. Relative launch directory, or one that is not a directory.
    def _relative():
        case = _Case().settings(good)
        return _code(case, launch=os.path.relpath(case.launch))
    got, mutant = _run(_relative, revert=("_check_absolute", lambda path, what: None))
    check("refuse/relative-launch-dir", got == 2, got)
    check("refuse/relative-launch-dir/red-when-reverted", mutant != 2, mutant)

    # The launch directory and .claude are opened with O_DIRECTORY: without it a plain file is opened and
    # refused one step later under another message, so these rows assert which path the refusal names.
    def _not_dir():
        case = _Case()
        _write(case.base / "plain-file", "x")
        code, _out, err = _result(case, launch=str(case.base / "plain-file"))
        return code, bool(err) and err[0].startswith("CANNOT EVALUATE: launch directory ")
    got, mutant = _run(_not_dir, revert=("_DIR_FLAGS", _DIR_FLAGS & ~os.O_DIRECTORY))
    check("refuse/launch-not-directory", got == (2, True), got)
    check("refuse/launch-not-directory/red-when-reverted", mutant != (2, True), mutant)

    # A launch directory or declaration reached through a symbolic link anywhere on its path: the link as
    # the launch directory itself, as a directory above it, and as a directory above the declaration.
    def _lenient_open_dir(path, what):
        return os.open(path, _DIR_FLAGS)

    def _launch_link(depth):
        def make():
            case = _Case().settings(good)
            real = case.launch if depth == 0 else case.base
            link = case.base / ("launch-link" if depth == 0 else "base-link")
            os.symlink(real, link)
            return _code(case, launch=str(link) if depth == 0 else str(link / "launch"))
        return make
    got, mutant = _run(_launch_link(0), revert=("_open_dir_nofollow", _lenient_open_dir))
    check("refuse/launch-dir-symlinked", got == 2, got)
    check("refuse/launch-dir-symlinked/red-when-reverted", mutant != 2, mutant)
    got, mutant = _run(_launch_link(1), revert=("_open_dir_nofollow", _lenient_open_dir))
    check("refuse/launch-dir-symlinked-ancestor", got == 2, got)
    check("refuse/launch-dir-symlinked-ancestor/red-when-reverted", mutant != 2, mutant)

    def _decl_link_ancestor():
        case = _Case().settings(good)
        os.symlink(case.base, case.base / "base-link")
        return _code(case, require=str(case.base / "base-link" / case.require.name))
    got, mutant = _run(_decl_link_ancestor, revert=("_open_dir_nofollow", _lenient_open_dir))
    check("refuse/decl-symlinked-ancestor", got == 2, got)
    check("refuse/decl-symlinked-ancestor/red-when-reverted", mutant != 2, mutant)
    got, _m = _run(lambda: _code(_Case(), launch="/nonexistent-hook-coverage-launch-dir"))
    check("refuse/launch-absent", got == 2, got)

    # Row 12. Empty declaration, unknown key, scope other than project.
    got, mutant = _run(lambda: _code(_Case(decl={"required_hooks": []}).settings(good)),
                       revert=("_check_decl_nonempty", lambda entries, where: None))
    check("refuse/decl-empty-list", got == 2, got)
    check("refuse/decl-empty-list/red-when-reverted", mutant != 2, mutant)
    got, _m = _run(lambda: _code(_Case(decl={}).settings(good)))
    check("refuse/decl-empty-object", got == 2, got)
    got, _m = _run(lambda: _code(_Case(decl="").settings(good)))
    check("refuse/decl-empty-file", got == 2, got)
    # A registry file: other top-level keys are ignored and only required_hooks is read; without
    # required_hooks it cannot be evaluated (with that check reverted, the missing key crashes the gate).
    registry = dict(_decl([GUARD_A]), version=1, record={"store": "notes"}, optional_hooks=[7])
    got, _m = _run(lambda: _code(_Case(decl=registry).settings(good)))
    check("pass/decl-registry-other-top-keys", got == 0, got)
    got, _m = _run(lambda: _code(_Case(decl=dict(registry, required_hooks=[
        {"scope": "project", "event": "PreToolUse", "matcher": "Bash", "command": "absent"}])).settings(good)))
    check("missing/decl-registry-other-top-keys", got == 1, got)
    no_key = {"version": 1, "record": {"store": "notes"}}
    got, mutant = _run(lambda: _guarded(lambda: _code(_Case(decl=no_key).settings(good))),
                       revert=("_check_decl_has_key", lambda doc, where: None))
    check("refuse/decl-no-required-hooks-key", got == 2, got)
    check("refuse/decl-no-required-hooks-key/red-when-reverted", mutant != 2, mutant)
    # required_hooks not a list: with the list check reverted a number crashes the gate rather than exit 2.
    got, mutant = _run(lambda: _guarded(lambda: _code(_Case(decl={"required_hooks": 5}).settings(good))),
                       revert=("_check_decl_list", lambda entries, where: None))
    check("refuse/decl-required-hooks-not-list", got == 2, got)
    check("refuse/decl-required-hooks-not-list/red-when-reverted", mutant != 2, mutant)
    # Entry values of the wrong type, and an empty event or command: with each check reverted the entry is
    # judged as a plain miss (exit 1), not refused.
    for name, red_name, key, value, check_name in (
            ("refuse/decl-matcher-not-string", "refuse/decl-matcher-not-string/red-when-reverted",
             "matcher", ["Bash"], "_check_decl_value_types"),
            ("refuse/decl-command-not-string", "refuse/decl-command-not-string/red-when-reverted",
             "command", 7, "_check_decl_value_types"),
            ("refuse/decl-empty-event", "refuse/decl-empty-event/red-when-reverted",
             "event", "", "_check_decl_nonempty_fields"),
            ("refuse/decl-empty-command", "refuse/decl-empty-command/red-when-reverted",
             "command", "", "_check_decl_nonempty_fields")):
        bad = _decl([GUARD_A])
        bad["required_hooks"][0][key] = value
        got, mutant = _run(lambda bad=bad: _guarded(lambda: _code(_Case(decl=bad).settings(good))),
                           revert=(check_name, lambda entry, where: None))
        check(name, got == 2, got)
        check(red_name, mutant != 2, mutant)
    unknown_entry = _decl([GUARD_A])
    unknown_entry["required_hooks"][0]["timeout"] = 10
    got, mutant = _run(lambda: _code(_Case(decl=unknown_entry).settings(good)),
                       revert=("_check_decl_keys", lambda keys, allowed, where: None))
    check("refuse/decl-unknown-entry-key", got == 2, got)
    check("refuse/decl-unknown-entry-key/red-when-reverted", mutant != 2, mutant)
    # An entry with no matcher key, against a group with no matcher: refused (exit 2); with only the
    # missing-key half of the key check reverted, both read as the empty matcher and the run passes.
    no_matcher = {"required_hooks": [{"scope": "project", "event": GUARD_A[0], "command": GUARD_A[2]}]}
    got, mutant = _run(
        lambda: _code(_Case(decl=no_matcher).settings(
            {"hooks": {GUARD_A[0]: [{"hooks": [{"type": "command", "command": GUARD_A[2]}]}]}})),
        revert=("_check_decl_keys", _unknown_keys_only))
    check("refuse/decl-missing-entry-key", got == 2, got)
    check("refuse/decl-missing-entry-key/red-when-reverted", mutant != 2, mutant)
    # A declaration document or entry that is not an object: refused (exit 2); with its object check
    # reverted the gate crashes instead.
    got, mutant = _run(lambda: _guarded(lambda: _code(_Case(decl=[DECL_KEY]).settings(good))),
                       revert=("_check_decl_doc", lambda doc, where: None))
    check("refuse/decl-not-object", got == 2, got)
    check("refuse/decl-not-object/red-when-reverted", mutant != 2, mutant)
    got, mutant = _run(lambda: _guarded(lambda: _code(_Case(decl={DECL_KEY: [7]}).settings(good))),
                       revert=("_check_decl_entry", lambda entry, where: None))
    check("refuse/decl-entry-not-object", got == 2, got)
    check("refuse/decl-entry-not-object/red-when-reverted", mutant != 2, mutant)
    for name, red_name, scope in (("refuse/decl-scope-user", "refuse/decl-scope-user/red-when-reverted", "user"),
                                  ("refuse/decl-scope-managed", "refuse/decl-scope-managed/red-when-reverted",
                                   "managed"),
                                  ("refuse/decl-scope-Project", "refuse/decl-scope-Project/red-when-reverted",
                                   "Project"),
                                  ("refuse/decl-scope-empty", "refuse/decl-scope-empty/red-when-reverted", "")):
        got, mutant = _run(lambda scope=scope: _code(_Case(decl=_decl([GUARD_A], scope=scope)).settings(good)),
                           revert=("_check_decl_scope", lambda scope, where: None))
        check(name, got == 2, got)
        check(red_name, mutant != 2, mutant)
    got, _m = _run(lambda: _code(_Case().settings(good), require="require.json"))
    check("refuse/decl-relative-path", got == 2, got)
    got, _m = _run(lambda: _code(_symlinked_decl(good)))
    check("refuse/decl-symlinked", got == 2, got)

    # Row 13. Unreadable declaration (its open refused with EACCES, as a mode-000 file would be for a
    # non-root reader).
    case = _Case().settings(good)
    real_open, denied = _denied_open(case.require.name)
    os.open = denied
    try:
        got = _code(case)
    finally:
        os.open = real_open
    check("refuse/decl-unreadable", got == 2, got)
    got, _m = _run(lambda: _code(_Case().settings(good), require="/nonexistent-hook-coverage-declaration.json"))
    check("refuse/decl-absent", got == 2, got)

    # The command line: the gate form, and every malformed argument list exits 2.
    case = _Case().settings(good)
    try:
        got = [_main_exit(["--launch-dir", str(case.launch), "--require", str(case.require)]),
               _main_exit(["--require", str(case.require), "--launch-dir", str(case.launch)])]
        # A trailing argument after a gate form whose paths exist: refused (exit 2); without the arity
        # check the extra argument is ignored and the run passes.
        extra = ["--launch-dir", str(case.launch), "--require", str(case.require), "--extra"]
        extra_got, extra_mutant = _run(lambda: _main_exit(extra), revert=("_check_gate_arity", lambda argv: True))
    finally:
        case.close()
    check("cli/gate-form-passes", got == [0, 0], got)
    check("cli/extra-argument-exit-2", extra_got == 2, extra_got)
    check("cli/extra-argument-exit-2/red-when-reverted", extra_mutant != 2, extra_mutant)
    got = [_main_exit(argv) for argv in ([], ["--bogus"], ["--self-test", "--bogus"],
                                         ["--launch-dir", "/x"], ["--launch-dir", "/x", "--launch-dir", "/y"],
                                         ["--launch-dir", "/x", "--require", "/y", "--extra"],
                                         ["--execution-report", "relative/report"])]
    check("cli/unrecognized-arguments-exit-2", got == [2] * 7, got)

    if FAILURES:
        print("SELF-TEST FAIL: check_hook_coverage {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_hook_coverage {} checks (every required entry present passes, in either "
          "settings file or twice, and a registry declaration's other top-level keys are ignored; an absent "
          "file, an absent event, a different matcher, command or event, and disableAllHooks true in either "
          "file are a named failure; malformed, duplicate-key, NaN, null, non-UTF-8, symlinked, FIFO, "
          "non-regular, oversized, unreadable and wrong-typed settings, a relative, symlinked or "
          "non-directory launch directory and an empty, keyless, unknown-entry-key, wrong-typed, "
          "non-project-scope, relative, symlinked, unreadable or absent declaration exit 2; each mutation row "
          "is red with its check reverted)".format(len(EXECUTED)))
    return 0


def _symlinked_decl(good):
    case = _Case().settings(good)
    real = case.base / "real-require.json"
    case.require.rename(real)
    os.symlink(real, case.require)
    return case


def _main_exit(argv):
    """main()'s exit code for the argument list argv, its output captured."""
    import contextlib
    import io
    saved = sys.argv
    sys.argv = [saved[0]] + list(argv)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main()
    finally:
        sys.argv = saved


def _check_gate_arity(argv):
    return len(argv) == 4


def _gate_args(argv):
    """(launch_dir, require) from exactly --launch-dir X and --require Y in either order, else None."""
    if not _check_gate_arity(argv):
        return None
    pairs = {argv[0]: argv[1], argv[2]: argv[3]}
    if set(pairs) != {"--launch-dir", "--require"}:
        return None
    return pairs["--launch-dir"], pairs["--require"]


def main():
    argv = sys.argv[1:]
    # The execution gate appends `--execution-report ABS_PATH` to the self-test launch; accepted only in that
    # trailing two-token form, with an absolute path, alone or after --self-test.
    report_path = None
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        report_path, argv = argv[-1], ["--self-test"]
    if argv == ["--self-test"]:
        if report_path is not None:
            # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
            sys.path.append(str(Path(__file__).resolve().parent))
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return _self_test()
    gate = _gate_args(argv)
    if gate is None:
        print("error: unrecognized arguments {!r}; expected --launch-dir ABS_DIR --require ABS_FILE or "
              "--self-test; fail closed".format(argv), file=sys.stderr)
        return 2
    return run_gate(*gate)


if __name__ == "__main__":
    _code = main()
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
