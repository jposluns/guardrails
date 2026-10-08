#!/usr/bin/env python3
"""PreToolUse Write|Edit|MultiEdit hook (char-policy-write): deny a write that adds a character the policy forbids.

WHAT IT DOES
    A repository can forbid some characters in some of its files with a character policy, the data file
    .aiqt/char-policy.json at its root, which the CI gate tools/check_no_dashes.py enforces after the fact.
    This hook reads the same file and applies the same scope before a file write lands, so a write the gate
    would fail is refused while the fix is still one edit. It names no character itself: the characters,
    their names, the advice and the scope all come from the policy file.

    Event: PreToolUse, matcher Write|Edit|MultiEdit. Output: nothing (allow), one line holding the standard
    PreToolUse deny object, or one line holding a systemMessage note (allow with a note). Exit status: always
    0; the decision travels in the JSON. The verdict is deny, a note or silence: this hook never asks. Once
    armed, it never allows a call it could not evaluate silently: each such call gets a note naming why.

CONFIGURATION
    AIQT_CHAR_POLICY_ROOT holds one absolute path, the repository root whose policy applies. There is no
    default and no older spelling. Unset or empty, the hook is not armed and does nothing, silently. Set but
    relative or holding a control character, it checks nothing and says so in a note on every call. The
    policy file is
    <root>/.aiqt/char-policy.json; absent, the hook does nothing (the gate then applies its built-in default
    policy, which this hook does not copy).

POLICY FILE
    {"version": 1, "id": <name>, "chars": {<one code point>: <name>, ...}, "advice": <optional text>,
     "scope": [{"tree": <dir>, "suffixes": [".md", ...], "skip": [<name>, ...]} | {"file": <path>}, ...]}
    Paths are repo-relative POSIX paths ("." alone is the root as a tree). A tree entry scopes a file under
    the tree whose suffix (the text from the final dot of its name, case-sensitive) is listed, unless a
    directory or the file below the tree has a name in that entry's skip list; a file entry scopes that one
    file. Unknown keys and any other fault make the file malformed, and so does a character that
    str.splitlines() treats as a line boundary (U+2028 or U+2029; the rest are control characters), which
    the gate's line scan could not see. The region between the BEGIN COPY and END COPY markers below is a
    byte-for-byte copy of the gate's marked region; H12 compares the bytes, the policy cap and path, a sample
    of policies through both validators, and this hook's scope test with the gate's walk.

DECISION
    - AIQT_CHAR_POLICY_ROOT unset or empty: allow, silently (the hook is not armed). Set but relative or
      holding a control character: allow with a note naming the variable.
    - A tool_name string naming a tool other than Write, Edit or MultiEdit: allow, silently.
    - Fail-open, made visible: the preview channel allows what it cannot evaluate (the hook is opt-in and the
      CI gate is the backstop), but always with a note naming the reason, never silently and never with an
      ask. That covers a payload that cannot be read as JSON of at most 64 MiB or is not a JSON object, a
      missing tool_name, a tool_input that is not an object, a file_path that is not a non-empty string
      without control characters, a relative file_path with no absolute cwd, a path that cannot be resolved
      or encoded as a file name (a lone surrogate, say), a field the tool needs (old_string, new_string,
      edits, content) of the wrong type, and an internal error.
    - A relative file_path is joined onto the payload's cwd. The target's and the root's real paths are
      compared whole component by component; a target outside the root, or not in the policy's scope, is
      allowed silently.
    - A policy file that is malformed, unreadable, a symbolic link, not a regular file, or over 65536 bytes:
      allow with a note naming the gate, which exits 2 on the same file, so CI still fails closed. No policy
      file: allow, silently.
    - For each policy character c, a write ADDS c when:
        Edit       new_string holds more c than old_string (with replace_all both counts scale alike);
        MultiEdit  any one edit's new_string holds more c than its old_string;
        Write      content holds more c than the existing file. An absent file counts 0, and so does an
                   existing file that is not valid UTF-8 (the gate skips that file today, but would scan the
                   new UTF-8 content). The existing file is read only when it is a regular file of at most
                   4 MiB; otherwise the write is allowed with a note.
      Any added character: deny. The reason names each added character as U+XXXX with its policy name, the
      first position in the new text, and the policy's advice; every policy character in the path, the
      policy id or the advice is written as U+XXXX too. The fixed wording and the U+XXXX notation are ASCII,
      so the reason carries no policy character only when the policy forbids no ASCII character: a policy
      that forbids "U", "+", a hex digit or a letter of the wording sees it in the reason (H09 pins this).
    Existing occurrences never block an unrelated edit: only an increase is denied.

RESIDUAL COVERAGE
    - Writes this hook does not see: shell commands (redirections, here-documents, sed -i, tee, interpreter
      one-liners), generator scripts, NotebookEdit, MCP and other tools, other harnesses, and sessions where
      the hook is not installed. The CI gate is the only backstop for those.
    - It is opt-in: it does nothing until it is installed by hand with AIQT_CHAR_POLICY_ROOT set.
    - An HTML entity or other encoding of a character (a named or numeric character reference) is not
      decoded, the same gap the gate has.
    - A net-zero move of an existing character (removed in one place, added in another in the same Edit or
      Write) is allowed; the gate still flags the character.
    - MultiEdit over-fires: a call where a later edit removes what an earlier edit added is denied, because
      each edit is judged alone. Over the strings as sent, it never misses a net increase: a code point
      cannot be formed by joining strings that lack it, so the net change is the sum of each edit's own
      change times its match count.
    - Unverified harness behaviour: if Claude Code's Edit normalizes quotes (writing curly quotes where
      new_string has straight ones, to match the file's text), the file gains characters new_string does
      not hold, and this hook, which judges new_string as sent, misses that increase for a policy that
      forbids curly quotes. Whether and when the harness does this is not verified here; the gate scans the
      written file.
    - Path aliases: a hard link, a case-insensitive or Unicode-normalizing filesystem, or a symbolic link
      inside the root (the gate's walk does not follow linked directories; this hook resolves real paths)
      can make the hook's scope test differ from the gate's walk.
    - A malformed policy fails open here (with a note) and closed at the gate.
    - A reviewed edit to the policy file narrows the gate and the hook together; diff review is the only
      control over it.
    - The repository's other character checks with their own fixed sets and scopes are not read.
    - The file can change between this hook's decision and the write (a race).
    - A payload over 64 MiB, or one that is not a JSON object, is allowed with a note.
    - It does not read AIQT_HOOKS_WORKER: worker processes are checked like any other session.

SELF-TEST
    --self-test runs the vectors H1 to H19 below. The parity test H12 imports the sibling gate
    ../tools/check_no_dashes.py; when it is absent the test reports skipped, unless the environment sets
    AIQT_HOOKS_REQUIRE_SIBLINGS=1, when its absence fails the test.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: char-policy-write.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import json
import os
import stat
from pathlib import PurePosixPath

HOOK_ID = "char-policy-write"
ROOT_VAR = "AIQT_CHAR_POLICY_ROOT"
POLICY_PATH = ".aiqt/char-policy.json"
GATE = "tools/check_no_dashes.py"
POLICY_CAP = 65536  # bytes; the gate's cap
EXISTING_CAP = 4 * 1024 * 1024  # bytes of an existing file a Write is compared with
_MAX_INPUT = 64 * 1024 * 1024  # bytes of hook payload read
TOOLS = ("Write", "Edit", "MultiEdit")


# --- BEGIN COPY of the policy validator in tools/check_no_dashes.py (H12 checks the two agree) ---
_TOP_KEYS = frozenset(("version", "id", "chars", "advice", "scope"))
_REQUIRED_KEYS = frozenset(("version", "id", "chars", "scope"))
_TREE_KEYS = frozenset(("tree", "suffixes", "skip"))
# Every code point str.splitlines() treats as a line boundary, from the table in the Python documentation for
# str.splitlines: \n, \r, \v, \f, \x1c, \x1d, \x1e, \x85, U+2028 and U+2029 (\r\n is a pair of two of them). The
# gate scans line by line, so such a policy character would vanish before the check; the validator rejects it.
LINE_BOUNDARIES = frozenset("\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")


class PolicyError(ValueError):
    """The policy file is malformed or unsafe to read."""


def _plain(text):
    """A non-empty string holding no control character (C0, DEL or C1)."""
    return (isinstance(text, str) and text != ""
            and not any(ord(ch) < 32 or 127 <= ord(ch) <= 159 for ch in text))


def _rel_ok(value, tree):
    """A repo-relative POSIX path: no leading `/`, no backslash, no empty, `.` or `..` component, no control
    character. A tree may also be "." alone, the repository root."""
    if not _plain(value):
        return False
    if tree and value == ".":
        return True
    if value.startswith("/") or "\\" in value:
        return False
    return all(part not in ("", ".", "..") for part in value.split("/"))


def _suffix_ok(value):
    return _plain(value) and len(value) > 1 and value[0] == "." and "." not in value[1:] and "/" not in value


def _skip_ok(value):
    return _plain(value) and "/" not in value and value not in (".", "..")


def _name_list(value, what, check):
    if not isinstance(value, list):
        raise PolicyError(f"{what} must be a list")
    for item in value:
        if not check(item):
            raise PolicyError(f"{what} holds an invalid entry {item!r}")


def validate_policy(data):
    """Return data when it is a valid policy; otherwise raise PolicyError naming the first fault."""
    if not isinstance(data, dict):
        raise PolicyError("the policy must be a JSON object")
    unknown = sorted(set(data) - _TOP_KEYS)
    if unknown:
        raise PolicyError(f"unknown key(s) {unknown}")
    missing = sorted(_REQUIRED_KEYS - set(data))
    if missing:
        raise PolicyError(f"missing key(s) {missing}")
    if type(data["version"]) is not int or data["version"] != 1:
        raise PolicyError("version must be 1")
    if not _plain(data["id"]):
        raise PolicyError("id must be a non-empty string without control characters")
    chars = data["chars"]
    if not isinstance(chars, dict) or not chars:
        raise PolicyError("chars must be a non-empty object")
    for char, name in chars.items():
        if len(char) != 1 or not _plain(char):
            raise PolicyError(f"chars key {char!r} must be exactly one code point, not a control character")
        if char in LINE_BOUNDARIES:
            raise PolicyError(f"chars key U+{ord(char):04X} is a line boundary (str.splitlines splits on it), "
                              "which a line-by-line scan cannot see")
        if not _plain(name):
            raise PolicyError(f"chars name for U+{ord(char):04X} must be a non-empty string")
    if "advice" in data and not _plain(data["advice"]):
        raise PolicyError("advice must be a non-empty string without control characters")
    scope = data["scope"]
    if not isinstance(scope, list) or not scope:
        raise PolicyError("scope must be a non-empty list")
    for entry in scope:
        if not isinstance(entry, dict) or (("tree" in entry) == ("file" in entry)):
            raise PolicyError(f"scope entry {entry!r} must be an object with exactly one of tree or file")
        if "file" in entry:
            if set(entry) != {"file"} or not _rel_ok(entry["file"], False):
                raise PolicyError(f"scope entry {entry!r}: a file entry holds one repo-relative path only")
            continue
        if set(entry) - _TREE_KEYS or "suffixes" not in entry:
            raise PolicyError(f"scope entry {entry!r}: a tree entry holds tree, suffixes and optional skip")
        if not _rel_ok(entry["tree"], True):
            raise PolicyError(f"scope entry {entry!r}: tree must be a repo-relative path")
        _name_list(entry["suffixes"], "suffixes", _suffix_ok)
        if not entry["suffixes"]:
            raise PolicyError("suffixes must not be empty")
        if "skip" in entry:
            _name_list(entry["skip"], "skip", _skip_ok)
    return data


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise PolicyError(f"duplicate key {key!r}")
        out[key] = value
    return out


def _no_constant(name):
    raise PolicyError(f"non-standard JSON constant {name}")


def parse_policy(raw):
    """Policy bytes to a validated policy; PolicyError on any fault."""
    if len(raw) > POLICY_CAP:
        raise PolicyError(f"the policy file is over {POLICY_CAP} bytes")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
    except PolicyError:
        raise
    except ValueError as exc:  # UnicodeDecodeError and JSONDecodeError are ValueErrors
        raise PolicyError(f"the policy file is not valid UTF-8 JSON ({exc})") from None
    return validate_policy(data)
# --- END COPY ---


def _read_capped(path, cap):
    """The bytes of the regular file at path, at most cap + 1 of them. Raises FileNotFoundError when absent,
    PolicyError when it is not a regular file, and OSError when it cannot be opened or read."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise PolicyError("not a regular file")
        raw = b""
        while len(raw) <= cap:
            chunk = os.read(fd, cap + 1 - len(raw))
            if not chunk:
                break
            raw += chunk
    finally:
        os.close(fd)
    return raw


def _load_policy(root):
    """(policy, None) for a valid policy file, (None, None) when it is absent, (None, why) otherwise."""
    path = os.path.join(root, *POLICY_PATH.split("/"))
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None, "is not a regular file (a symbolic link or another kind)"
        raw = _read_capped(path, POLICY_CAP)
    except FileNotFoundError:
        return None, None
    except PolicyError:
        return None, "is not a regular file"
    except OSError as exc:
        return None, f"cannot be read ({exc.strerror or type(exc).__name__})"
    try:
        return parse_policy(raw), None
    except PolicyError as exc:
        return None, f"is malformed ({exc})"


def _in_scope(parts, policy):
    """True when the repo-relative path (a list of its components) is in the policy's scope, as the gate's
    walk would list it."""
    for entry in policy["scope"]:
        if "file" in entry:
            if parts == entry["file"].split("/"):
                return True
            continue
        tree = [] if entry["tree"] == "." else entry["tree"].split("/")
        if len(parts) <= len(tree) or parts[:len(tree)] != tree:
            continue
        skip = entry.get("skip", ())
        if any(part in skip for part in parts[len(tree):]):
            continue
        if PurePosixPath(parts[-1]).suffix in entry["suffixes"]:
            return True
    return False


def _escape(text, chars):
    """text with every policy character (every non-ASCII character when chars is None, as when the policy
    could not be read), and every control character, written as U+XXXX. The notation is ASCII, so a policy
    character among "U", "+" and the hex digits survives in it."""
    return "".join(f"U+{ord(ch):04X}" if (ord(ch) > 126 if chars is None else ch in chars)
                   or ord(ch) < 32 or 127 <= ord(ch) <= 159 else ch for ch in text)


def _added(chars, old, new):
    """The policy characters new holds more of than old, in policy order."""
    return [c for c in chars if new.count(c) > old.count(c)]


def _position(text, added):
    """(line, column), 1-based, of the first added character in text; lines end at a newline."""
    index = min(text.index(c) for c in added)
    return text.count("\n", 0, index) + 1, index - text.rfind("\n", 0, index)


def _note(message):
    return {"systemMessage": f"AIQT character policy hook ({HOOK_ID}): {message}"}


def _unchecked(why, tool="tool"):
    """The allow-with-a-note verdict for a call the hook could not evaluate (fail-open, made visible)."""
    return _note(_escape(f"this {tool} call was not checked against the character policy, because {why}. The CI "
                         f"gate {GATE} still scans the files the policy scopes.", None))


def _deny(policy, tool, rel, added, where):
    chars = policy["chars"]
    listed = " and ".join(f"U+{ord(c):04X} ({chars[c]})" for c in added)
    advice = policy.get("advice")
    reason = (f'AIQT character policy "{policy["id"]}" ({POLICY_PATH}): this {tool} would add {listed} to '
              f"{rel} (first at {where}). "
              + (f"{advice[0].upper()}{advice[1:].rstrip('.')}. " if advice else "")
              + "If this is quoted or third-party text, name the character in words, or narrow the policy "
              f"scope in a reviewed change. The CI gate {GATE} enforces the same policy.")
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": _escape(reason, chars)}}


def _decide(payload, env):
    """The verdict for one payload: None (allow silently), a note object, or a deny object."""
    root = env.get(ROOT_VAR)
    if not root:
        return None  # unset or empty: the hook is not armed
    if not isinstance(root, str) or not os.path.isabs(root) or not _plain(root):
        return _unchecked(f"{ROOT_VAR} is set but is not an absolute path without control characters")
    if not isinstance(payload, dict):
        return _unchecked("the hook payload is not a JSON object")
    if not isinstance(payload.get("tool_name"), str):
        return _unchecked("the hook payload has no tool_name string")
    if payload["tool_name"] not in TOOLS:
        return None
    tool, tool_input = payload["tool_name"], payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return _unchecked("its tool_input is not an object", tool)
    file_path = tool_input.get("file_path")
    if not _plain(file_path):
        return _unchecked("its file_path is not a non-empty string without control characters", tool)
    if not os.path.isabs(file_path):
        cwd = payload.get("cwd")
        if not _plain(cwd) or not os.path.isabs(cwd):
            return _unchecked("its file_path is relative and the payload has no absolute cwd", tool)
        file_path = os.path.join(cwd, file_path)
    try:
        root_real = os.path.realpath(root)
        target = os.path.realpath(file_path)
        outside = target == root_real or os.path.commonpath([target, root_real]) != root_real
    except (OSError, ValueError) as exc:  # a lone surrogate cannot be encoded as a file name: UnicodeEncodeError
        return _unchecked(f"its file_path cannot be resolved or encoded as a file name ({type(exc).__name__})",
                          tool)
    if outside:
        return None
    policy, why = _load_policy(root_real)
    if policy is None:
        if why is None:
            return None
        return _note(_escape(f"the policy file {POLICY_PATH} under {root_real} {why}, so this {tool} was not "
                             f"checked. The CI gate {GATE} exits 2 on the same file.", None))
    parts = os.path.relpath(target, root_real).split(os.sep)
    if not _in_scope(parts, policy):
        return None
    chars = list(policy["chars"])
    rel = "/".join(parts)
    if tool == "Edit":
        old, new = tool_input.get("old_string"), tool_input.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            return _unchecked("its old_string or new_string is not a string", tool)
        added = _added(chars, old, new)
        if added:
            line, column = _position(new, added)
            return _deny(policy, tool, rel, added, f"line {line}, column {column} of the new text")
        return None
    if tool == "MultiEdit":
        edits = tool_input.get("edits")
        if not isinstance(edits, list) or not edits:
            return _unchecked("its edits value is not a non-empty list", tool)
        pairs = []
        for edit in edits:
            if not isinstance(edit, dict):
                return _unchecked("one of its edits is not an object", tool)
            old, new = edit.get("old_string"), edit.get("new_string")
            if not isinstance(old, str) or not isinstance(new, str):
                return _unchecked("one of its edits has an old_string or new_string that is not a string", tool)
            pairs.append((old, new))
        for number, (old, new) in enumerate(pairs, 1):
            added = _added(chars, old, new)
            if added:
                line, column = _position(new, added)
                return _deny(policy, tool, rel, added,
                             f"line {line}, column {column} of edit {number}'s new text")
        return None
    content = tool_input.get("content")
    if not isinstance(content, str):
        return _unchecked("its content is not a string", tool)
    if not any(c in content for c in chars):
        return None
    try:
        raw = _read_capped(target, EXISTING_CAP)
    except FileNotFoundError:
        raw = b""
    except (PolicyError, OSError) as exc:
        kind = "is not a regular file" if isinstance(exc, PolicyError) else "cannot be read"
        return _note(_escape(f"the existing file {rel} {kind}, so whether this Write adds a character the "
                             f'"{policy["id"]}" policy forbids was not checked. The CI gate {GATE} still scans '
                             "the file.", chars))
    if len(raw) > EXISTING_CAP:
        return _note(_escape(f"the existing file {rel} is over {EXISTING_CAP} bytes, so whether this Write adds "
                             f'a character the "{policy["id"]}" policy forbids was not checked. The CI gate '
                             f"{GATE} still scans the file.", chars))
    try:
        old = raw.decode("utf-8")
    except UnicodeDecodeError:
        old = ""  # the gate skips a file that is not UTF-8, but would scan this new content
    added = _added(chars, old, content)
    if added:
        line, column = _position(content, added)
        return _deny(policy, tool, rel, added, f"line {line}, column {column} of the new text")
    return None


def _read_payload():
    data = bytearray()
    while len(data) <= _MAX_INPUT:
        chunk = os.read(0, 1 << 20)
        if not chunk:
            break
        data += chunk
    if len(data) > _MAX_INPUT:
        raise ValueError("hook payload over the read bound")
    return json.loads(bytes(data))


def _emit_line(text):
    """Write one line to stdout; on any output failure point descriptor 1 at /dev/null so the interpreter's
    shutdown flush cannot fail, or end at once with status 0: the hook always exits 0."""
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except Exception:
        try:
            fd = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(fd, 1)
            finally:
                os.close(fd)
        except Exception:
            os._exit(0)


def main(argv):
    """The hook: always 0. `--self-test` alone runs the self-test instead."""
    if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(a, str) for a in argv):
        return 0
    if len(argv) > 1:
        return _self_test() if list(argv[1:]) == ["--self-test"] else 0
    if not os.environ.get(ROOT_VAR):
        return 0  # unset or empty: the hook is not armed, so it reads nothing and says nothing
    # Fail-open, made visible: a payload it cannot read, or an internal error, allows the call with a note.
    try:
        payload = _read_payload()
    except Exception as exc:
        out = _unchecked(f"the hook payload cannot be read as JSON of at most {_MAX_INPUT} bytes "
                         f"({type(exc).__name__})")
    else:
        try:
            out = _decide(payload, os.environ)
        except Exception as exc:
            out = _unchecked(f"an internal error ({type(exc).__name__}) stopped the check")
    if out is not None:
        _emit_line(json.dumps(out))
    return 0


def _self_test():
    import ast
    import importlib.util
    import io
    import shutil
    import subprocess
    import tempfile
    import unittest
    from contextlib import redirect_stdout
    from unittest import mock

    here = os.path.abspath(__file__)
    em, en, quote, hyphen = "\u2014", "\u2013", "\u201c", "-"
    dashes = {"version": 1, "id": "no-dashes", "chars": {en: "en dash", em: "em dash"},
              "advice": "use hyphens, commas, colons, semicolons, or parentheses",
              "scope": [{"tree": ".", "suffixes": [".md"], "skip": [".git", "node_modules"]},
                        {"tree": "plugin", "suffixes": [".py"]}, {"file": "NOTICE"}]}
    quotes = {"version": 1, "id": "no-curly-quotes", "chars": {quote: "left double quotation mark"},
              "scope": [{"tree": ".", "suffixes": [".md"]}]}

    gate_path = os.path.join(os.path.dirname(os.path.dirname(here)), *GATE.split("/"))

    def sibling_gate(path=gate_path, env=None):
        """The sibling gate module, or None (skip) when it is absent and siblings are not required."""
        env = os.environ if env is None else env
        if not os.path.isfile(path):
            if env.get("AIQT_HOOKS_REQUIRE_SIBLINGS") == "1":
                raise AssertionError(f"sibling gate {GATE} is absent ({path}) and AIQT_HOOKS_REQUIRE_SIBLINGS=1 "
                                     "requires it")
            return None
        spec = importlib.util.spec_from_file_location("_char_policy_sibling_gate", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    class T(unittest.TestCase):
        def setUp(self):
            self.tmp = tempfile.mkdtemp(prefix="char-policy-write-selftest-")
            self.root = os.path.join(self.tmp, "repo")
            os.makedirs(os.path.join(self.root, ".aiqt"))
            self.set_policy(dashes)
            self.env = {ROOT_VAR: self.root}

        def tearDown(self):
            shutil.rmtree(self.tmp, ignore_errors=True)

        def set_policy(self, policy=None, raw=None):
            path = os.path.join(self.root, ".aiqt", "char-policy.json")
            if os.path.lexists(path):
                os.unlink(path)
            if policy is not None:
                raw = json.dumps(policy).encode("ascii")
            if raw is not None:
                with open(path, "wb") as handle:
                    handle.write(raw)

        def put(self, rel, data):
            path = os.path.join(self.root, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(data.encode("utf-8") if isinstance(data, str) else data)
            return path

        def at(self, rel):
            return os.path.join(self.root, *rel.split("/"))

        def edit(self, rel, old, new, **extra):
            payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                       "tool_input": {"file_path": self.at(rel), "old_string": old, "new_string": new}}
            payload.update(extra)
            return _decide(payload, self.env)

        def write(self, rel, content):
            return _decide({"tool_name": "Write", "tool_input": {"file_path": self.at(rel), "content": content}},
                           self.env)

        def multi(self, rel, *edits):
            return _decide({"tool_name": "MultiEdit", "tool_input": {
                "file_path": self.at(rel),
                "edits": [{"old_string": o, "new_string": n} for o, n in edits]}}, self.env)

        def is_deny(self, out):
            self.assertIsNotNone(out)
            h = out["hookSpecificOutput"]
            self.assertEqual((h["hookEventName"], h["permissionDecision"]), ("PreToolUse", "deny"))
            return h["permissionDecisionReason"]

        def is_note(self, out):
            self.assertIsNotNone(out)
            self.assertEqual(set(out), {"systemMessage"})
            return out["systemMessage"]

        def is_unchecked(self, out, needle):
            note = self.is_note(out)
            self.assertIn("was not checked", note)
            self.assertIn(needle, note)
            return note

        def test_h01_suffix(self):
            self.is_deny(self.edit("docs/a.md", "x", "x" + em))
            self.assertIsNone(self.edit("docs/a.txt", "x", "x" + em))

        def test_h02_net_count(self):
            self.assertIsNone(self.edit("docs/a.md", f"x{em}y", f"x{em}z"))
            self.is_deny(self.edit("docs/a.md", "x-y", f"x{em}z"))
            self.assertIsNone(self.edit("docs/a.md", f"x{em}y", "x-z"))  # a removal is never denied

        def test_h03_existing_debt(self):
            self.is_deny(self.write("docs/a.md", f"a{em}b\n"))
            self.put("docs/a.md", f"old {em}\n")
            self.assertIsNone(self.write("docs/a.md", f"a{em}b\n"))
            self.is_deny(self.write("docs/a.md", f"a{em}b{em}\n"))

        def test_h04_arming(self):
            payload = {"tool_name": "Edit", "tool_input": {"file_path": self.at("docs/a.md"), "old_string": "x",
                                                          "new_string": em}}
            # unset or empty: not armed, silent
            for env in ({}, {ROOT_VAR: ""}):
                self.assertIsNone(_decide(payload, env), env)
                self.assertIsNone(_decide("not a payload", env), env)
            # set but unusable: a note naming the variable, even for a root that resolves to the fixture from here
            for root in (os.path.relpath(self.root), self.root + "\n"):
                self.is_unchecked(_decide(payload, {ROOT_VAR: root}), ROOT_VAR)
            self.is_deny(_decide(payload, self.env))

        def test_h04_process(self):
            data = json.dumps({"tool_name": "Edit", "tool_input": {
                "file_path": self.at("docs/a.md"), "old_string": "x", "new_string": em}}).encode("ascii")
            for env, want in (({"LC_ALL": "C"}, b""), ({"LC_ALL": "C", ROOT_VAR: self.root}, b"deny")):
                p = subprocess.run([sys.executable, "-I", "-S", "-B", here], input=data, capture_output=True,
                                   env=env, timeout=60)
                self.assertEqual(p.returncode, 0)
                if want:
                    self.assertEqual(json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
                else:
                    self.assertEqual(p.stdout, b"")
            # armed: a payload it cannot evaluate is allowed with a note naming why; unarmed: silent
            for junk, needle in ((b"", "cannot be read as JSON"), (b"not json", "cannot be read as JSON"),
                                 (b"\xff", "cannot be read as JSON"), (b"[1]", "not a JSON object"),
                                 (b'{"tool_name": "Write"}', "tool_input is not an object")):
                p = subprocess.run([sys.executable, "-I", "-S", "-B", here], input=junk, capture_output=True,
                                   env={"LC_ALL": "C", ROOT_VAR: self.root}, timeout=60)
                self.assertEqual(p.returncode, 0, junk)
                self.is_unchecked(json.loads(p.stdout), needle)
                p = subprocess.run([sys.executable, "-I", "-S", "-B", here], input=junk, capture_output=True,
                                   env={"LC_ALL": "C"}, timeout=60)
                self.assertEqual((p.returncode, p.stdout), (0, b""), junk)

        def test_h05_policy(self):
            self.is_deny(self.edit("docs/a.md", "x", em))
            for raw in (json.dumps(dashes | {"version": 2}).encode("ascii"), b"{",
                        json.dumps(dashes).encode("ascii") + b" " * POLICY_CAP,
                        json.dumps({"version": 1, "id": "x", "chars": {em + em: "pair"},
                                    "scope": [{"file": "NOTICE"}]}).encode("ascii")):
                self.set_policy(raw=raw)
                note = self.is_note(self.edit("docs/a.md", "x", em))
                self.assertIn(GATE, note)
                self.assertNotIn(em, note)
            # a non-standard constant in an otherwise-valid policy, and the two line-boundary characters
            for raw, needle in ((json.dumps(dashes).replace('"version": 1', '"version": NaN').encode("ascii"),
                                 "non-standard JSON constant NaN"),
                                (json.dumps(dashes | {"chars": {"\u2028": "line separator"}}).encode("ascii"),
                                 "U+2028 is a line boundary"),
                                (json.dumps(dashes | {"chars": {"\u2029": "paragraph separator"}}).encode("ascii"),
                                 "U+2029 is a line boundary")):
                self.set_policy(raw=raw)
                note = self.is_note(self.edit("docs/a.md", "x", "\u2028\u2029" + em))
                self.assertIn(needle, note)
                self.assertIn("exits 2", note)
            self.set_policy(None)  # absent: silent
            self.assertIsNone(self.edit("docs/a.md", "x", em))
            elsewhere = self.put("elsewhere.json", json.dumps(dashes))
            os.symlink(elsewhere, os.path.join(self.root, ".aiqt", "char-policy.json"))
            self.assertIn("not a regular file", self.is_note(self.edit("docs/a.md", "x", em)))

        def test_h06_multiedit(self):
            self.is_deny(self.multi("docs/a.md", ("a", "b"), ("c", em)))
            self.assertIsNone(self.multi("docs/a.md", ("a", "b"), ("c", hyphen)))
            # the disclosed over-fire: a later edit removes what an earlier one added
            self.is_deny(self.multi("docs/a.md", ("a", em), (em, hyphen)))
            self.assertIn("edit 2's new text", self.is_deny(self.multi("docs/a.md", ("a", "b"), ("c", em))))

        def test_h07_skip(self):
            self.assertIsNone(self.edit("docs/node_modules/a.md", "x", em))
            self.is_deny(self.edit("docs/a.md", "x", em))
            self.assertIsNone(self.edit(".git/a.md", "x", em))

        def test_h08_matcher(self):
            self.assertIsNone(_decide({"tool_name": "Bash", "tool_input": {
                "command": f"printf '{em}' > {self.at('docs/a.md')}", "file_path": self.at("docs/a.md"),
                "content": em}}, self.env))
            self.is_deny(self.write("docs/a.md", em))

        def test_h09_message(self):
            reason = self.is_deny(self.edit("docs/a.md", "x", f"ab\ncd{em}"))
            self.assertIn("U+2014 (em dash)", reason)
            self.assertIn("line 2, column 3 of the new text", reason)
            self.assertIn("Use hyphens, commas, colons, semicolons, or parentheses.", reason)
            self.assertIn(GATE, reason)
            self.assertNotIn(em, reason)
            reason = self.is_deny(self.edit(f"docs/a{em}b.md", "x", f"{en}{em}"))
            self.assertIn("docs/aU+2014b.md", reason)
            self.assertIn("U+2013 (en dash) and U+2014 (em dash)", reason)
            self.assertFalse(any(c in reason for c in (em, en)))
            self.set_policy(dashes | {"id": f"no{em}dashes", "advice": f"use {en} never"})
            reason = self.is_deny(self.edit("docs/a.md", "x", em))
            self.assertFalse(any(c in reason for c in (em, en)), reason)
            # the disclosed limit: the U+XXXX notation is ASCII, so a policy forbidding "U" sees "U" in it
            self.set_policy(quotes | {"chars": {"U": "capital U"}})
            reason = self.is_deny(self.edit("docs/a.md", "x", "xU"))
            self.assertIn("U+0055+0055 (capital U+0055)", reason)  # each U of the notation escaped once, in turn

        def test_h10_adopter_set(self):
            self.set_policy(quotes)
            self.is_deny(self.edit("docs/a.md", "x", quote))
            self.assertIsNone(self.edit("docs/a.md", "x", em))

        def test_h11_containment(self):
            outside = os.path.join(self.tmp, "repo-other", "docs", "a.md")
            self.assertIsNone(_decide({"tool_name": "Edit", "tool_input": {
                "file_path": outside, "old_string": "x", "new_string": em}}, self.env))
            self.is_deny(self.edit("docs/a.md", "x", em))
            self.is_deny(self.edit("docs/../docs/a.md", "x", em))
            self.assertIsNone(self.edit("../repo-other/a.md", "x", em))

        def test_h12_gate_parity(self):
            gate = sibling_gate()
            if gate is None:
                self.skipTest(f"sibling gate {GATE} absent; set AIQT_HOOKS_REQUIRE_SIBLINGS=1 to require it")
            # The source: the marked validator region is byte-identical to the gate's, and nothing outside it
            # here redefines a name it defines.
            regions = {}
            for path in (here, gate.__file__):
                with open(path, "rb") as handle:
                    lines = handle.read().splitlines(keepends=True)
                begins = [i for i, line in enumerate(lines) if line.startswith(b"# --- BEGIN COPY")]
                ends = [i for i, line in enumerate(lines) if line.startswith(b"# --- END COPY")]
                self.assertEqual((len(begins), len(ends)), (1, 1), path)
                self.assertLess(begins[0], ends[0], path)
                regions[path] = (b"".join(lines[begins[0] + 1:ends[0]]), begins[0] + 1, ends[0],
                                 b"".join(lines).decode("utf-8"))
            self.assertEqual(regions[here][0], regions[gate.__file__][0], "the copied validator region drifted")
            _, first, last, source = regions[here]
            inside, outside = set(), set()
            for node in ast.parse(source).body:
                names = set()
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(node.name)
                elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                        names.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    names.update((a.asname or a.name).split(".")[0] for a in node.names)
                (inside if first < node.lineno <= last else outside).update(names)
            self.assertEqual(inside & outside, set(), "a name from the copied region is redefined outside it")
            self.assertEqual((POLICY_CAP, POLICY_PATH), (gate.POLICY_CAP, gate.POLICY_PATH))
            # The behaviour, on a sample: each fixture is otherwise valid, so a rejection is the named fault's.
            raws = [json.dumps(p).encode("ascii") for p in (
                dashes, quotes, gate.DEFAULT_POLICY, dashes | {"version": 2}, dashes | {"extra": 1},
                dashes | {"chars": {em + em: "x"}}, dashes | {"chars": {}}, dashes | {"id": ""},
                dashes | {"advice": ""}, dashes | {"scope": []}, dashes | {"scope": [{"file": "../x"}]},
                dashes | {"scope": [{"file": "a\\b"}]}, dashes | {"scope": [{"tree": "/x", "suffixes": [".md"]}]},
                dashes | {"scope": [{"tree": ".", "suffixes": ["md"]}]},
                dashes | {"scope": [{"tree": ".", "suffixes": [".a.b"]}]},
                dashes | {"scope": [{"tree": ".", "suffixes": [".md"], "skip": ["a/b"]}]},
                dashes | {"scope": [{"tree": ".", "file": "x", "suffixes": [".md"]}]},
                dashes | {"scope": [{"file": "x", "skip": []}]}, dashes | {"version": True},
                dashes | {"chars": {"\x9f": "c1"}}, dashes | {"chars": {"\x7f": "del"}}, dashes | {"id": "a\x80"},
                dashes | {"chars": {"\u2028": "line separator"}},
                dashes | {"chars": {"\u2029": "paragraph separator"}},
                dashes | {"scope": [{"file": "a/./b"}]}, dashes | {"scope": [{"file": "a//b"}]},
                dashes | {"scope": [{"tree": "a/", "suffixes": [".md"]}]},
                dashes | {"scope": [{"tree": ".", "suffixes": [".m/d"]}]},
                dashes | {"scope": [{"tree": ".", "suffixes": []}]},
                dashes | {"scope": [{"tree": ".", "suffixes": [".md"], "skip": ["."]}]},
                dashes | {"scope": [{"tree": ".", "suffixes": [".md"], "skip": [".."]}]},
                dashes | {"scope": [{"tree": ".", "suffixes": [".md"], "skip": "x"}]})]
            valid = json.dumps(dashes).encode("ascii")
            raws += [b"{", b"[]", b"\xff", valid[:-1] + b', "version": 1}',
                     valid.replace(b'"en dash"', b'"en dash", "\\u2013": "again"'),
                     valid.replace(b'"version": 1', b'"version": NaN'),
                     valid.replace(b'"version": 1', b'"version": Infinity'),
                     valid + b" " * (gate.POLICY_CAP - len(valid)),
                     valid + b" " * (gate.POLICY_CAP + 1 - len(valid))]
            for raw in raws:
                verdicts = []
                for parse, error in ((parse_policy, PolicyError), (gate.parse_policy, gate.PolicyError)):
                    try:
                        parse(raw)
                        verdicts.append(True)
                    except error:
                        verdicts.append(False)
                self.assertEqual(verdicts[0], verdicts[1], raw[:120])
            for raw in (valid, raws[0], raws[1], raws[-2]):  # the sample holds accepted policies too
                self.assertEqual(parse_policy(raw)["version"], 1)
            files = ["a.md", "a.mdc", "a.txt", "docs/a.md", "docs/node_modules/a.md", "node_modules.md",
                     "docs/__pycache__/a.md", ".git/a.md", "plugin/x.py", "plugin/x.json", "plugin/sub/y.toml",
                     "plugin/node_modules/z.py", ".aiqt/standards/s.toml", ".aiqt/standards/n/s.toml",
                     ".aiqt/core/hooks/h.py", "NOTICE", "NOTICE.md", "docs/NOTICE", ".aiqt/attribution.toml",
                     ".aiqt/char-policy.json", "tools/check.py", "site/a.html", "x.toml", "b/c.md", ".md"]
            for f in files:
                self.put(f, "x\n")
            for policy in (gate.DEFAULT_POLICY, dashes, quotes,
                           quotes | {"scope": [{"tree": "b", "suffixes": [".md"]}, {"tree": ".",
                                                                                  "suffixes": [".md"]}]},
                           quotes | {"scope": [{"tree": ".", "suffixes": [".md"], "skip": ["node_modules.md"]}]}):
                listed = {os.path.relpath(p, self.root).replace(os.sep, "/")
                          for p in gate.scope_paths(self.root, policy)}
                for f in files + ["docs/a.md"]:
                    self.assertEqual(_in_scope(f.split("/"), policy), f in listed, (policy["id"], f))

        def test_h13_encoding(self):
            self.put("docs/a.md", em.encode("utf-8") + b"\xff")
            self.is_deny(self.write("docs/a.md", f"{em}\n"))
            self.put("docs/a.md", em.encode("utf-8"))
            self.assertIsNone(self.write("docs/a.md", f"{em}\n"))

        def test_h14_malformed_payload(self):
            for tool_input in (None, "x", {}, {"file_path": 7}, {"file_path": ""},
                               {"file_path": self.at("docs/a\nb.md"), "content": em},
                               {"file_path": self.at("docs/a.md"), "content": 7},
                               {"file_path": self.at("docs/a.md"), "old_string": None, "new_string": em},
                               {"file_path": self.at("docs/a.md"), "edits": [{"old_string": "a"}]},
                               {"file_path": self.at("docs/a.md"), "edits": "x"},
                               {"file_path": self.at("docs/a.md"), "edits": []},
                               {"file_path": self.at("docs/a.md"), "edits": ["x"]}):
                for tool in TOOLS:  # armed: a note naming the fault, never a silent allow
                    self.is_unchecked(_decide({"tool_name": tool, "tool_input": tool_input}, self.env), tool)
            self.is_unchecked(_decide([], self.env), "not a JSON object")
            self.is_unchecked(_decide({"tool_input": {"file_path": self.at("docs/a.md")}}, self.env), "tool_name")
            self.assertIsNone(_decide({"tool_name": "Read", "tool_input": None}, self.env))
            # out of scope, a field the tool needs is never examined: silent
            self.assertIsNone(_decide({"tool_name": "Write", "tool_input": {"file_path": self.at("a.txt")}},
                                      self.env))

        def test_h15_existing_unreadable(self):
            os.makedirs(self.at("docs/dir.md"))
            self.assertIn("not a regular file", self.is_note(self.write("docs/dir.md", em)))
            self.put("docs/big.md", b"x" * (EXISTING_CAP + 1))
            self.assertIn("over", self.is_note(self.write("docs/big.md", em)))
            self.assertIsNone(self.write("docs/big.md", "no policy character"))
            if hasattr(os, "geteuid") and os.geteuid() != 0:
                path = self.put("docs/locked.md", "x")
                os.chmod(path, 0)
                self.assertIn("cannot be read", self.is_note(self.write("docs/locked.md", em)))

        def test_h16_paths(self):
            rel = {"tool_name": "Edit", "tool_input": {"file_path": "docs/a.md", "old_string": "x",
                                                      "new_string": em}}
            self.is_deny(_decide(rel | {"cwd": self.root}, self.env))
            self.is_unchecked(_decide(rel, self.env), "no absolute cwd")
            self.is_unchecked(_decide(rel | {"cwd": "relative"}, self.env), "no absolute cwd")
            self.is_deny(self.edit("NOTICE", "x", em))
            self.is_deny(self.edit("plugin/x.py", "x", em))
            self.assertIsNone(self.edit("docs/NOTICE", "x", em))
            self.assertIsNone(self.edit(".aiqt/char-policy.json", "x", em))  # the policy itself is never blocked
            self.assertIsNone(self.edit("", "x", em))

        def test_h17_unencodable_path(self):
            # a lone high surrogate cannot be encoded as a file name: a note, not a silent allow
            note = self.is_unchecked(self.write("docs/a\ud800.md", f"x{em}"), "cannot be resolved or encoded")
            self.assertIn("UnicodeEncodeError", note)
            self.assertNotIn("\ud800", note)
            # a low surrogate in the surrogateescape range is a byte of a file name: checked and denied
            self.is_deny(self.write("docs/a\udc80.md", f"x{em}"))

        def test_h18_visible_fail_open(self):
            payload = {"tool_name": "Edit", "tool_input": {"file_path": self.at("docs/a.md"), "old_string": "x",
                                                          "new_string": em}}

            def boom(*_):
                raise RuntimeError("internal")

            def run_main(env, decide=_decide):
                out = io.StringIO()
                with mock.patch.dict(os.environ, env, clear=True), \
                        mock.patch.dict(globals(), {"_read_payload": lambda: payload, "_decide": decide}), \
                        redirect_stdout(out):
                    rc = main(["char-policy-write.py"])
                return rc, out.getvalue()

            rc, out = run_main({ROOT_VAR: self.root}, boom)
            self.assertEqual(rc, 0)
            self.is_unchecked(json.loads(out), "an internal error (RuntimeError)")
            for env in ({}, {ROOT_VAR: ""}):  # not armed: nothing is read and nothing is said
                self.assertEqual(run_main(env, boom), (0, ""))
            rc, out = run_main({ROOT_VAR: self.root})
            self.is_deny(json.loads(out))
            # a payload over the read bound (lowered here to 64 bytes) is allowed with a note, not silently
            bounded = ("import importlib.util, sys\n"
                       "spec = importlib.util.spec_from_file_location('bounded_hook', sys.argv[1])\n"
                       "module = importlib.util.module_from_spec(spec)\n"
                       "spec.loader.exec_module(module)\n"
                       "module._MAX_INPUT = 64\n"
                       "sys.exit(module.main(['char-policy-write.py']))\n")
            p = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", bounded, here], capture_output=True,
                               input=json.dumps(payload).encode("ascii"), env={"LC_ALL": "C", ROOT_VAR: self.root},
                               timeout=60)
            self.assertEqual(p.returncode, 0)
            self.is_unchecked(json.loads(p.stdout), "at most 64 bytes (ValueError)")

        def test_h19_require_siblings(self):
            missing = os.path.join(self.tmp, "absent", "check_no_dashes.py")
            self.assertIsNone(sibling_gate(missing, {}))
            self.assertIsNone(sibling_gate(missing, {"AIQT_HOOKS_REQUIRE_SIBLINGS": "0"}))
            with self.assertRaises(AssertionError):
                sibling_gate(missing, {"AIQT_HOOKS_REQUIRE_SIBLINGS": "1"})

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(T)
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
