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
    armed (its root set), it allows every call it cannot evaluate with a note naming why, a malformed call
    among them, and it allows silently only a tool other than Write, Edit or MultiEdit and a well-formed call
    (see DECISION) that it evaluates and finds clean, whose target is outside the root or the policy's scope,
    or whose root holds no policy file.

CONFIGURATION
    AIQT_CHAR_POLICY_ROOT holds one absolute path, the repository root whose policy applies. There is no
    default and no older spelling. Unset or empty, the hook is not armed and does nothing, silently. Set but
    relative, holding a control character, or naming a path that does not exist or is not a directory, it
    checks nothing and says so in a note on every call; so does an armed hook launched with any command-line
    argument other than --self-test alone. The policy file is <root>/.aiqt/char-policy.json; absent, the hook
    allows a well-formed call silently and notes a malformed one (the gate then applies its built-in default
    policy, which this hook does not copy).

POLICY FILE
    {"version": 1, "id": <name>, "chars": {<one code point>: <name>, ...}, "advice": <optional text>,
     "scope": [{"tree": <dir>, "suffixes": [".md", ...], "skip": [<name>, ...]} | {"file": <path>}, ...]}
    Paths are repo-relative POSIX paths ("." alone is the root as a tree). A tree entry scopes a file under
    the tree whose suffix (the text from the final dot of its name, case-sensitive) is listed, unless a
    directory or the file below the tree has a name in that entry's skip list; a file entry scopes that one
    file. Unknown keys and any other fault make the file malformed, and so does a character that
    str.splitlines() treats as a line boundary (U+2028 or U+2029; the rest are control characters), which
    the gate's line scan could not see, or a lone surrogate (U+D800 to U+DFFF) in any policy string. The
    region between the BEGIN COPY and END COPY markers below is generated from the gate's marked region by
    tools/gen_char_policy.py, whose --check fails on any byte difference: edit the gate and regenerate,
    never the copy. The region holds the validator and every module-level name it reads. H12 compares the
    bytes; walks both files' module scope (conditional and compound statements included, function and class
    bodies not) to check that no name the region binds or reads is bound outside it, and that no function
    declares one of them global; and compares the policy path, a sample of policies through both
    validators, and this hook's scope test with the gate's walk. The walk treats __builtins__ as a region
    name, and also fails on a module-level statement outside the region that stores to an attribute or item of
    a region name, json, builtins or sys.modules (a name that an import anywhere in the file binds to one of
    those modules counts as that module and as itself). H12 exists to catch accidental drift between the two
    copies. It is not a defence against a deliberate edit that replaces behaviour through a path the static
    walk does not model: a function body run later, an alias made by assignment, setattr, globals(), vars(),
    exec, an in-place call such as sys.modules.update(...) or json.__dict__.update(...), another module
    patching this one, or a module named json that shadows the standard library's. The gate imports json
    before it puts tools/ on sys.path, so under python3 -I, as CI runs it, a tools/json.py is never imported
    (the gate's self-test pins this); run without -I, Python puts a script's own directory first on sys.path,
    and a module there named like a standard library module shadows that module, for the gate and for this
    hook alike. The behaviour sample runs both validators in one process, so it shares one json module and
    cannot see a change made to json. Diff review is the control for a deliberate edit. The recorded hashes
    (.preview/SHA256SUMS for this hook, the release manifest .aiqt/manifest.toml for both files) let an
    installer or a release check detect a shipped copy that differs from the reviewed one; whoever makes an
    edit can record the new hashes in the same change.

DECISION
    - AIQT_CHAR_POLICY_ROOT unset or empty: allow, silently (the hook is not armed). Set but relative,
      holding a control character, or naming a path that does not exist, cannot be examined or is not a
      directory: allow with a note naming the variable and the reason. Armed, but launched with a
      command-line argument other than --self-test alone: allow with a note saying so, without reading the
      payload.
    - A tool_name string naming a tool other than Write, Edit or MultiEdit: allow, silently.
    - Fail-open, made visible: the preview channel allows what it cannot evaluate (the hook is opt-in and the
      CI gate is the backstop), but always with a note naming the reason, never silently and never with an
      ask. That covers a payload that is not read through its end of input within 2 seconds, is over 64 MiB,
      is not strict UTF-8 (UTF-16, UTF-32, an encoded surrogate, a byte order mark), is not one JSON value,
      holds NaN, Infinity or -Infinity, or is not a JSON object; a missing tool_name, a tool_input that is
      not an object, a file_path that is not a non-empty string without control characters, a relative
      file_path with no absolute cwd, a field the tool needs (old_string, new_string, edits, content) of the
      wrong type, a path that cannot be resolved (a symbolic link loop, a component that is not a directory, a
      permission fault: every fault but a missing component) or encoded as a file name (a lone surrogate,
      say), and an internal error. An escaped lone surrogate (\\ud800) in a string is standard JSON and is
      read as sent: it is not a policy character, so it neither adds one nor hides one. A call is well-formed
      when none of these faults applies to it. The order: the file_path's form (and the cwd a relative one is
      joined onto), then the fields, then the path is resolved and compared with the root, and only then is
      the policy file read, so a malformed call gets a note wherever it points and whether or not the root
      holds a policy file.
    - A relative file_path is joined onto the payload's cwd. The target's and the root's real paths are
      compared whole component by component; a well-formed call whose target is outside the root, or not in
      the policy's scope, is allowed silently.
    - A policy file that is malformed, unreadable, a symbolic link, not a regular file, or over 65536 bytes:
      allow with a note naming the gate, which exits 2 on the same file, so CI still fails closed. No policy
      file: allow a well-formed call, silently.
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
    - A payload over 64 MiB, one that does not end within 2 seconds, one that is not strict UTF-8 JSON, and one
      that is not a JSON object are allowed with a note.
    - It does not read AIQT_HOOKS_WORKER: worker processes are checked like any other session.
    - An armed hook whose root holds no policy file allows every well-formed call silently (a malformed one
      gets a note); the gate then applies its built-in default, which this hook does not copy.

SELF-TEST
    --self-test runs the vectors H1 to H23 below. The parity test H12 imports the sibling gate
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

import os
import select
import stat
import time
from pathlib import PurePosixPath

HOOK_ID = "char-policy-write"
ROOT_VAR = "AIQT_CHAR_POLICY_ROOT"
POLICY_PATH = ".aiqt/char-policy.json"
GATE = "tools/check_no_dashes.py"
EXISTING_CAP = 4 * 1024 * 1024  # bytes of an existing file a Write is compared with
_MAX_INPUT = 64 * 1024 * 1024  # bytes of hook payload read
_READ_DEADLINE = 2.0  # seconds for the whole payload to arrive, through its end of input
TOOLS = ("Write", "Edit", "MultiEdit")


# --- BEGIN COPY: generated from tools/check_no_dashes.py by tools/gen_char_policy.py; do not edit ---
# The policy validator. Every module-level name it reads is bound in this region or is a builtin. The hook's H12
# walks both files' module-level statements and fails when one outside this region binds a name the region
# binds or reads (or __builtins__), or stores to an attribute or item of such a name, json, builtins or
# sys.modules. That catches accidental drift between the two copies. A deliberate edit that replaces behaviour
# through a path the static walk does not model (a function body run later, an alias made by assignment,
# setattr, globals(), vars(), exec, an in-place call such as sys.modules.update or json.__dict__.update, another
# module patching this one, a module named json that shadows the standard library's) is not caught there. Diff
# review is the control for a deliberate edit; the recorded hashes (.preview/SHA256SUMS for the hook,
# .aiqt/manifest.toml for both files) let an installer or a release check detect a shipped copy that differs
# from the reviewed one.
import json  # noqa: E402

POLICY_CAP = 65536  # bytes; a larger policy file is malformed
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
    """A non-empty string holding no control character (C0, DEL or C1) and no lone surrogate (U+D800 to
    U+DFFF, which UTF-8 cannot encode, so the gate could not print it)."""
    return (isinstance(text, str) and text != ""
            and not any(ord(ch) < 32 or 127 <= ord(ch) <= 159 or 0xD800 <= ord(ch) <= 0xDFFF for ch in text))


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
        raise PolicyError("id must be a non-empty string without control characters or lone surrogates")
    chars = data["chars"]
    if not isinstance(chars, dict) or not chars:
        raise PolicyError("chars must be a non-empty object")
    for char, name in chars.items():
        if len(char) != 1 or not _plain(char):
            raise PolicyError(f"chars key {char!r} must be exactly one code point, not a control character or a "
                              "lone surrogate")
        if char in LINE_BOUNDARIES:
            raise PolicyError(f"chars key U+{ord(char):04X} is a line boundary (str.splitlines splits on it), "
                              "which a line-by-line scan cannot see")
        if not _plain(name):
            raise PolicyError(f"chars name for U+{ord(char):04X} must be a non-empty string without control "
                              "characters or lone surrogates")
    if "advice" in data and not _plain(data["advice"]):
        raise PolicyError("advice must be a non-empty string without control characters or lone surrogates")
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


def _clean(text):
    """A non-empty string holding no control character (C0, DEL or C1): the test for a root, path or cwd. Unlike
    the policy validator's _plain, it lets a lone surrogate through, because a low one can stand for a byte of
    a file name; one that cannot be encoded as a file name fails later, with a note."""
    return (isinstance(text, str) and text != ""
            and not any(ord(ch) < 32 or 127 <= ord(ch) <= 159 for ch in text))


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


def _fields(tool, tool_input):
    """(fields, None) when the fields the tool needs have the right type, else (None, why). For Write the fields
    are its content; for Edit and MultiEdit, a list of (old_string, new_string) pairs, one per edit."""
    if tool == "Write":
        content = tool_input.get("content")
        return (content, None) if isinstance(content, str) else (None, "its content is not a string")
    if tool == "Edit":
        old, new = tool_input.get("old_string"), tool_input.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            return None, "its old_string or new_string is not a string"
        return [(old, new)], None
    edits = tool_input.get("edits")
    if not isinstance(edits, list) or not edits:
        return None, "its edits value is not a non-empty list"
    pairs = []
    for edit in edits:
        if not isinstance(edit, dict):
            return None, "one of its edits is not an object"
        old, new = edit.get("old_string"), edit.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            return None, "one of its edits has an old_string or new_string that is not a string"
        pairs.append((old, new))
    return pairs, None


def _decide(payload, env):
    """The verdict for one payload: None (allow silently), a note object, or a deny object."""
    root = env.get(ROOT_VAR)
    if not root:
        return None  # unset or empty: the hook is not armed
    if not isinstance(root, str) or not os.path.isabs(root) or not _clean(root):
        return _unchecked(f"{ROOT_VAR} is set but is not an absolute path without control characters")
    try:
        root_mode = os.stat(root).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return _unchecked(f"{ROOT_VAR} names a path that does not exist")
    except (OSError, ValueError) as exc:  # ValueError: a lone surrogate cannot be encoded (UnicodeEncodeError)
        return _unchecked(f"{ROOT_VAR} names a path that cannot be examined ({type(exc).__name__})")
    if not stat.S_ISDIR(root_mode):
        return _unchecked(f"{ROOT_VAR} names a path that is not a directory")
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
    if not _clean(file_path):
        return _unchecked("its file_path is not a non-empty string without control characters", tool)
    if not os.path.isabs(file_path):
        cwd = payload.get("cwd")
        if not _clean(cwd) or not os.path.isabs(cwd):
            return _unchecked("its file_path is relative and the payload has no absolute cwd", tool)
        file_path = os.path.join(cwd, file_path)
    # The fields are checked before the path and the policy, so a malformed call gets a note wherever it points
    # and whether or not the root holds a policy file.
    fields, why = _fields(tool, tool_input)
    if why is not None:
        return _unchecked(why, tool)
    # ALLOW_MISSING: a missing component (the target, or a directory the write would create) resolves as
    # written; every other fault (a symbolic link loop, a component that is not a directory, a permission fault)
    # raises, so it gets a note before the out-of-scope and no-policy returns below.
    try:
        root_real = os.path.realpath(root, strict=os.path.ALLOW_MISSING)
        target = os.path.realpath(file_path, strict=os.path.ALLOW_MISSING)
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
    if tool != "Write":
        for number, (old, new) in enumerate(fields, 1):
            added = _added(chars, old, new)
            if added:
                line, column = _position(new, added)
                text = "the new text" if tool == "Edit" else f"edit {number}'s new text"
                return _deny(policy, tool, rel, added, f"line {line}, column {column} of {text}")
        return None
    content = fields
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


_LATE = "the hook payload did not end within the read deadline"


def _no_payload_constant(name):
    raise ValueError(f"non-standard JSON constant {name} in the hook payload")


def _read_payload(fd=0, deadline=None):
    """The payload, parsed once the input has ended (EOF) and that end was read before the deadline (by default
    _READ_DEADLINE seconds, read at call time). Every byte read through the end counts toward one _MAX_INPUT
    budget. The clock is read again after each wait and after each read, before an end of input is accepted: a
    reading taken after a read returns is never earlier than the read, however late the OS runs this process,
    so an end of input that comes after the deadline is refused. Each wait is for the time left at most, but
    the OS can return from a wait late, so refusing can take longer than the deadline. The bytes must be strict
    UTF-8 (no UTF-16 or UTF-32, no encoded surrogate, and no byte order mark, which json.loads refuses in a
    str) holding one JSON value without NaN, Infinity or -Infinity. Raises ValueError for any of these faults
    and for no end read before the deadline."""
    end = time.monotonic() + (_READ_DEADLINE if deadline is None else deadline)
    data = bytearray()
    while True:
        left = end - time.monotonic()
        if left <= 0:
            raise ValueError(_LATE)
        ready = select.select([fd], [], [], left)[0]
        if time.monotonic() >= end:  # after the wait: a wait the OS ended late is not followed by a read
            raise ValueError(_LATE)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 65536)
        except BlockingIOError:
            continue
        if time.monotonic() >= end:  # after the read, before its end of input is accepted
            raise ValueError(_LATE)
        if not chunk:
            return json.loads(bytes(data).decode("utf-8"), parse_constant=_no_payload_constant)
        data += chunk
        if len(data) > _MAX_INPUT:
            raise ValueError("hook payload over the read bound")


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
    if list(argv[1:]) == ["--self-test"]:
        return _self_test()
    if not os.environ.get(ROOT_VAR):
        return 0  # unset or empty: the hook is not armed, so it reads nothing and says nothing
    if len(argv) > 1:  # armed but launched with an argument it does not know: it checks nothing, and says so
        _emit_line(json.dumps(_unchecked(f"the hook was launched with {len(argv) - 1} unexpected command-line "
                                         "argument(s) (only --self-test is known)")))
        return 0
    # Fail-open, made visible: a payload it cannot read, or an internal error, allows the call with a note.
    try:
        payload = _read_payload()
    except Exception as exc:
        out = _unchecked(f"the hook payload cannot be read as JSON in UTF-8 of at most {_MAX_INPUT} bytes, "
                         f"ending within {_READ_DEADLINE:g} seconds ({type(exc).__name__}: {str(exc)[:200]})")
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
    import threading
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

    def marked(text):
        """(first, last, region): the 1-based first and last line between text's one BEGIN COPY line and its one
        END COPY line, and the text between them."""
        lines = text.split("\n")
        begins = [i for i, line in enumerate(lines) if line.startswith("# --- BEGIN COPY")]
        ends = [i for i, line in enumerate(lines) if line.startswith("# --- END COPY")]
        if len(begins) != 1 or len(ends) != 1 or begins[0] >= ends[0]:
            raise AssertionError(f"want one BEGIN COPY line before one END COPY line, found {begins} and {ends}")
        return begins[0] + 2, ends[0], "\n".join(lines[begins[0] + 1:ends[0]])

    def bindings(node, out):
        """Add to out each name node binds in the scope it runs in. Nested statements and expressions are walked
        (an if, try, for, while, with or match body included); a def, class or lambda body, a scope of its own,
        is not, though its name, decorators, defaults and bases are. A comprehension's own targets count too
        (conservative), and a wildcard import adds "*"."""
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            if not isinstance(node, ast.Lambda):
                out.add(node.name)
            inner = list(getattr(node, "decorator_list", []))
            if isinstance(node, ast.ClassDef):
                inner += node.bases + [keyword.value for keyword in node.keywords]
            else:
                inner += node.args.defaults + [d for d in node.args.kw_defaults if d is not None]
            for child in inner:
                bindings(child, out)
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            out.add(node.id)
        elif isinstance(node, ast.alias):
            out.add(node.asname or node.name.partition(".")[0])
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            out.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            out.add(node.rest)
        for child in ast.iter_child_nodes(node):
            bindings(child, out)

    def stores(node, out, aliases):
        """Add to out the bases of each attribute or item store or deletion node makes at module scope: the name
        its target chain starts from, and, when an import anywhere in the file (any scope) binds that name to
        json, builtins or sys, each such module too (conservative: an import in another scope can neither hide
        the name nor a module it may stand for), with "sys.modules" for a chain that starts there. A def or
        lambda body, which runs only when called, is not walked, though its decorators and defaults are; a
        class body, which runs at once, is."""
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            inner = list(getattr(node, "decorator_list", [])) + node.args.defaults
            for child in inner + [d for d in node.args.kw_defaults if d is not None]:
                stores(child, out, aliases)
            return
        if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, (ast.Store, ast.Del)):
            first = node
            while isinstance(first.value, (ast.Attribute, ast.Subscript)):
                first = first.value
            if isinstance(first.value, ast.Name):
                for base in {first.value.id} | aliases.get(first.value.id, set()):
                    if base == "sys" and isinstance(first, ast.Attribute) and first.attr == "modules":
                        base = "sys.modules"
                    out.add(base)
        for child in ast.iter_child_nodes(node):
            stores(child, out, aliases)

    def rebound(text):
        """The names the marked region binds or reads (every name in it, function bodies included), with
        __builtins__, that the rest of the module binds at module scope or that a function outside the region
        declares global, plus "*" for a wildcard import outside it, plus the base of each attribute or item
        store outside the region, at module scope, whose base is one of those names, builtins or sys.modules.
        The alias map records, for each name an import of json, builtins or sys binds anywhere in the file, every
        module it is bound to. Each module-level statement must lie wholly on one side."""
        first, last, _ = marked(text)
        inside, outside, stored, aliases = {"*", "__builtins__"}, set(), set(), {}
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = (node.module or "") if isinstance(node, ast.ImportFrom) else None
                for alias in node.names:
                    whole = alias.name if module is None else f"{module}.{alias.name}"
                    if whole.partition(".")[0] in ("json", "builtins", "sys"):
                        bound = alias.asname or (alias.name if module is not None else whole.partition(".")[0])
                        aliases.setdefault(bound, set()).add(
                            "sys.modules" if whole == "sys.modules" else whole.partition(".")[0])
        for stmt in tree.body:
            start = min([stmt.lineno] + [d.lineno for d in getattr(stmt, "decorator_list", [])])
            if first <= start and stmt.end_lineno <= last:
                bindings(stmt, inside)
                inside.update(n.id for n in ast.walk(stmt) if isinstance(n, ast.Name))
            elif stmt.end_lineno < first or start > last:
                bindings(stmt, outside)
                outside.update(name for n in ast.walk(stmt) if isinstance(n, (ast.Global, ast.Nonlocal))
                               for name in n.names)
                stores(stmt, stored, aliases)
            else:
                raise AssertionError(f"the statement at line {start} straddles a copy marker")
        return (inside & outside) | (stored & (inside | {"builtins", "sys.modules"}))

    def beside(text, code, marker):
        """text with code inserted on the line after its END COPY marker, or before its BEGIN COPY marker."""
        lines = text.split("\n")
        at = next(i for i, line in enumerate(lines) if line.startswith(f"# --- {marker} COPY"))
        at += marker == "END"
        return "\n".join(lines[:at] + [code] + lines[at:])

    # Rebindings outside the region that H12 must find, in either file, before the region and after it. The
    # first four are the mutants that survived QA round 2 (a plain and a conditional rebinding of _TOP_KEYS, a
    # plain and a conditional redefinition of _rel_ok); the next ones are the other ways a statement can bind
    # a name, one vector or more for each branch of bindings(); then the stores of QA round 3's mutants (A, B
    # and C) and their variants, one vector or more for each branch of stores() (a def's decorators, defaults
    # and keyword-only defaults, a lambda's defaults, a class body, a deletion, a chain through sys.modules)
    # and for each import form the alias map reads (import X, import X as Y, import X.Y as Z, from X import Y,
    # from X import Y as Z); the last ones are QA round 4's shadowed aliases, where an import of another
    # module under the same name in a function body must not hide a module-level store.
    rebindings = (
        ("_TOP_KEYS", '_TOP_KEYS = _TOP_KEYS | {"metadata"}'),
        ("_TOP_KEYS", 'if True:\n    _TOP_KEYS = _TOP_KEYS | {"metadata"}'),
        ("_rel_ok", "_region_rel_ok = _rel_ok\ndef _rel_ok(value, tree):\n"
                    '    return _region_rel_ok(value, tree) or (isinstance(value, str) and "/../" in value)'),
        ("_rel_ok", "if True:\n    _region_rel_ok = _rel_ok\n    def _rel_ok(value, tree):\n"
                    '        return _region_rel_ok(value, tree) or (isinstance(value, str) and "/../" in value)'),
        ("POLICY_CAP", "def widen():\n    global POLICY_CAP\n    POLICY_CAP = 1 << 30"),
        ("POLICY_CAP", "[POLICY_CAP := 1 << 30 for _ in ()]"),
        ("json", "import json"),
        ("json", "try:\n    pass\nexcept ImportError as json:\n    pass"),
        ("isinstance", "for isinstance in ():\n    pass"),
        ("PolicyError", "with open(__file__) as PolicyError:\n    pass"),
        ("LINE_BOUNDARIES", "match 1:\n    case LINE_BOUNDARIES:\n        pass"),
        ("_TREE_KEYS", "del _TREE_KEYS"),
        ("_name_list", "class _name_list:\n    pass"),
        ("validate_policy", "while False:\n    validate_policy = None"),
        ("parse_policy", "type parse_policy = int"),
        ("*", "from os.path import *"),
        ("_plain", "match []:\n    case [*_plain]:\n        pass"),
        ("_REQUIRED_KEYS", "match 0:\n    case {**_REQUIRED_KEYS}:\n        pass"),
        ("json", "import json.decoder"),
        ("_suffix_ok", "@(_suffix_ok := staticmethod)\ndef _unrelated():\n    pass"),
        ("_skip_ok", "def _unrelated(value=(_skip_ok := None)):\n    pass"),
        ("_no_duplicates", "def _unrelated(*, value=(_no_duplicates := None)):\n    pass"),
        ("_TREE_KEYS", "_unrelated = lambda value=(_TREE_KEYS := None): value"),
        ("_no_constant", "class _Unrelated((_no_constant := object)):\n    pass"),
        ("POLICY_CAP", "class _Unrelated(metaclass=(POLICY_CAP := type)):\n    pass"),
        ("__builtins__", "import builtins as _bi\n__builtins__ = dict(vars(_bi), sorted=lambda it: "
                         '[k for k in _bi.sorted(it) if k != "metadata"])'),
        ("__builtins__", '__builtins__["sorted"] = sorted'),
        ("builtins", "import builtins\nbuiltins.sorted = sorted"),
        ("builtins", "import builtins as _bi\n_bi.sorted = sorted"),
        ("validate_policy", "import types as _ty\n"
                            "_vp_copy = _ty.FunctionType(validate_policy.__code__, globals())\n"
                            "def _shim(data):\n"
                            "    return _vp_copy(dict((k, v) for k, v in data.items() if k != 'metadata'))\n"
                            "validate_policy.__code__ = _shim.__code__"),
        ("sys.modules", "import json as _real_json\nclass _J:\n    def loads(self, *args, **kwargs):\n"
                        "        data = _real_json.loads(*args, **kwargs)\n"
                        '        data.pop("metadata", None)\n        return data\n'
                        'sys.modules["json"] = _J()'),
        ("sys.modules", 'import sys as _s\n_s.modules["json"] = None'),
        ("sys.modules", 'from sys import modules as _mods\n_mods["json"] = None'),
        ("json", "import json as _j\n_j.loads = None"),
        ("json", "from json import decoder as _d\n_d.scanstring = None"),
        ("json", "class _Patch:\n    json.loads = None"),
        ("_TOP_KEYS", "if True:\n    _TOP_KEYS.__doc__ = None"),
        ("json", "del json.loads"),
        ("json", "def _unrelated(value=[0 for json.loads in [None]]):\n    pass"),
        ("builtins", "import builtins\n@[staticmethod for builtins.sorted in [None]][0]\n"
                     "def _unrelated():\n    pass"),
        ("json", "def _unrelated(*, value=[0 for json.loads in [None]]):\n    pass"),
        ("json", "_unrelated = lambda value=[0 for json.loads in [None]]: value"),
        ("json", "from json import decoder\ndecoder.scanstring = None"),
        ("json", "import json.decoder as _d\n_d.scanstring = None"),
        ("json", "import json as _backend\n"
                 '_backend.loads.__kwdefaults__["parse_float"] = lambda text: int(float(text))\n'
                 "def _helper():\n    import sys as _backend"),
        ("validate_policy", "def _helper():\n    import sys as validate_policy\n"
                            "import types as _ty\n"
                            "_vp_copy = _ty.FunctionType(validate_policy.__code__, globals())\n"
                            "def _shim(data):\n"
                            "    return _vp_copy(dict((k, v) for k, v in data.items() if k != 'metadata'))\n"
                            "validate_policy.__code__ = _shim.__code__"),
    )

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
            # set but unusable: a note naming the variable and the reason, even for a root that resolves to the
            # fixture from here, a mistyped root, and the policy file named as the root
            policy_file = os.path.join(self.root, ".aiqt", "char-policy.json")
            for root, why in ((os.path.relpath(self.root), "not an absolute path"),
                              (self.root + "\n", "not an absolute path"),
                              (os.path.join(self.root, "no-such-dir"), "does not exist"),
                              (self.root + "-typo", "does not exist"),
                              (os.path.join(policy_file, "x"), "does not exist"),
                              (policy_file, "is not a directory"),
                              (self.root + "\ud800", "cannot be examined (UnicodeEncodeError)")):
                self.assertIn(why, self.is_unchecked(_decide(payload, {ROOT_VAR: root}), ROOT_VAR), root)
            self.is_deny(_decide(payload, self.env))
            # armed and evaluable: a clean call, an out-of-scope call and another tool are allowed silently
            self.assertIsNone(self.edit("docs/a.md", "x", "clean"))
            self.assertIsNone(self.write("docs/a.md", "clean"))
            self.assertIsNone(self.edit("docs/a.txt", "x", em))
            self.assertIsNone(_decide({"tool_name": "Read", "tool_input": {"file_path": self.at("docs/a.md")}},
                                      self.env))

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
            # armed: a command-line argument other than --self-test alone checks nothing and says so, for a
            # payload it would deny and for one it cannot read; unarmed: silent
            for extra in (["--bogus"], ["--self-test", "x"], ["x", "--self-test"]):
                for junk in (data, b"not json"):
                    p = subprocess.run([sys.executable, "-I", "-S", "-B", here, *extra], input=junk,
                                       capture_output=True, env={"LC_ALL": "C", ROOT_VAR: self.root}, timeout=60)
                    self.assertEqual(p.returncode, 0, extra)
                    self.is_unchecked(json.loads(p.stdout), "unexpected command-line argument")
                p = subprocess.run([sys.executable, "-I", "-S", "-B", here, *extra], input=data,
                                   capture_output=True, env={"LC_ALL": "C"}, timeout=60)
                self.assertEqual((p.returncode, p.stdout), (0, b""), extra)

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
            # The source. tools/gen_char_policy.py writes the marked region from the gate's and fails its --check
            # on any byte difference; this compares the bytes again, for a tree where that check did not run.
            texts = {}
            for path in (here, gate.__file__):
                with open(path, "rb") as handle:
                    texts[path] = handle.read().decode("utf-8")
            self.assertEqual(marked(texts[here])[2], marked(texts[gate.__file__])[2],
                             "the validator region drifted; run tools/gen_char_policy.py")
            # Both files alike: no name the region binds or reads (or __builtins__) is bound outside it at module
            # scope, no module-level statement outside it stores to an attribute or item of one of those names,
            # of builtins or of sys.modules, and each vector is found before either BEGIN marker and after either
            # END marker. This catches accidental drift. It is not a defence against a deliberate edit that
            # replaces behaviour through a path this static walk does not model (see the module docstring); diff
            # review is the control for that.
            for path, text in texts.items():
                self.assertEqual(rebound(text), set(), (path, "a name of the copied region is bound outside it"))
                for name, code in rebindings:
                    for marker in ("BEGIN", "END"):
                        self.assertEqual(rebound(beside(text, code, marker)), {name}, (path, marker, code))
            self.assertEqual(POLICY_PATH, gate.POLICY_PATH)
            # The behaviour, on a sample: each rejected fixture is otherwise valid, so its rejection is the named
            # fault's, and both validators must give the stated verdict, not merely the same one.
            valid = json.dumps(dashes).encode("ascii")
            accepted = [valid, json.dumps(quotes).encode("ascii"), json.dumps(gate.DEFAULT_POLICY).encode("ascii"),
                        valid + b" " * (POLICY_CAP - len(valid)),
                        json.dumps(dashes | {"chars": {"\ud7ff": "before the surrogates",
                                                       "\ue000": "after them"}}).encode("ascii")]
            rejected = [json.dumps(p).encode("ascii") for p in (
                dashes | {"version": 2}, dashes | {"extra": 1},
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
                dashes | {"scope": [{"file": "."}]},
                dashes | {"scope": [{"tree": "a/", "suffixes": [".md"]}]},
                dashes | {"scope": [{"tree": ".", "suffixes": [".m/d"]}]},
                dashes | {"scope": [{"tree": ".", "suffixes": []}]},
                dashes | {"scope": [{"tree": ".", "suffixes": [".md"], "skip": ["."]}]},
                dashes | {"scope": [{"tree": ".", "suffixes": [".md"], "skip": [".."]}]},
                dashes | {"scope": [{"tree": ".", "suffixes": [".md"], "skip": "x"}]},
                dashes | {"chars": {"\ud800": "high surrogate"}}, dashes | {"chars": {"\udfff": "low surrogate"}},
                dashes | {"chars": {em: "em\udc80"}}, dashes | {"id": "a\ud800"}, dashes | {"advice": "\udfff"},
                dashes | {"scope": [{"file": "a\ud800"}]})]
            rejected += [b"{", b"[]", b"\xff", valid[:-1] + b', "version": 1}',
                         valid.replace(b'"en dash"', b'"en dash", "\\u2013": "again"'),
                         valid.replace(b'"version": 1', b'"version": NaN'),
                         valid.replace(b'"version": 1', b'"version": Infinity'),
                         valid + b" " * (POLICY_CAP + 1 - len(valid))]
            for raws, want in ((accepted, True), (rejected, False)):
                for raw in raws:
                    for parse, error, who in ((parse_policy, PolicyError, "hook"),
                                              (gate.parse_policy, gate.PolicyError, "gate")):
                        try:
                            parse(raw)
                            got = True
                        except error:
                            got = False
                        self.assertEqual(got, want, (who, raw[:120]))
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
            # the fields a tool needs are checked before the path and the policy: a malformed call gets a note
            # out of scope and outside the root too, and a well-formed one there is silent
            for path in (self.at("a.txt"), os.path.join(self.tmp, "elsewhere", "a.md")):
                self.is_unchecked(_decide({"tool_name": "Write", "tool_input": {"file_path": path}}, self.env),
                                  "its content is not a string")
                self.assertIsNone(_decide({"tool_name": "Write", "tool_input": {"file_path": path, "content": em}},
                                          self.env))

        def test_h21_no_policy_file(self):
            # QA round 3: under a root without a policy file, these three malformed calls were allowed silently;
            # each gets a note, in process and through the hook as launched, while the well-formed call is silent
            empty = os.path.join(self.tmp, "empty")
            os.makedirs(empty)
            self.set_policy(None)
            for tool, bad, good, needle in (
                    ("Write", {"content": 7}, {"content": em}, "its content is not a string"),
                    ("Edit", {"old_string": None, "new_string": "x"}, {"old_string": "x", "new_string": em},
                     "its old_string or new_string is not a string"),
                    ("MultiEdit", {"edits": []}, {"edits": [{"old_string": "x", "new_string": em}]},
                     "its edits value is not a non-empty list")):
                for root, target in ((empty, os.path.join(empty, "x.md")), (self.root, self.at("docs/a.md"))):
                    env = {ROOT_VAR: root}
                    payload = {"tool_name": tool, "tool_input": {"file_path": target} | bad}
                    self.is_unchecked(_decide(payload, env), needle)
                    self.assertIsNone(_decide({"tool_name": tool, "tool_input": {"file_path": target} | good}, env))
                p = subprocess.run([sys.executable, "-I", "-S", "-B", here], capture_output=True, timeout=60,
                                   input=json.dumps({"tool_name": tool, "tool_input": {
                                       "file_path": os.path.join(empty, "x.md")} | bad}).encode("ascii"),
                                   env={"LC_ALL": "C", ROOT_VAR: empty})
                self.assertEqual((p.returncode, p.stderr), (0, b""), tool)
                self.is_unchecked(json.loads(p.stdout), needle)

        def test_h22_payload_reader(self):
            # QA round 4: each payload in bad was allowed silently; each now gets the cannot-evaluate note, in
            # process and through the hook as launched, while the same call as UTF-8 JSON is evaluated
            def call(content):
                return {"tool_name": "Write", "tool_input": {"file_path": self.at("docs/qa-probe.md"),
                                                             "content": content}}

            good = json.dumps(call("safe")).encode("ascii")
            text = json.dumps(call("safe"))
            bad = {"UTF-16": text.encode("utf-16-le"), "UTF-16 with a byte order mark": text.encode("utf-16"),
                   "UTF-32": text.encode("utf-32-le"), "UTF-32 with a byte order mark": text.encode("utf-32"),
                   "UTF-8 with a byte order mark": b"\xef\xbb\xbf" + good,
                   "encoded surrogate bytes": good.replace(b'"safe"', b'"\xed\xa0\x80"'),
                   "NaN": good[:-1] + b', "extra": NaN}', "Infinity": good[:-1] + b', "extra": Infinity}',
                   "-Infinity": good[:-1] + b', "extra": -Infinity}', "trailing junk": good + b"x"}

            def piped(parts, deadline=2.0, within=None):
                """_read_payload(r, deadline) on a pipe that a thread fills with parts, (pause, bytes) each, with
                None as the bytes for the end of input; the parsed payload, or "refused" for a ValueError. When
                within is given, the read must also return within that many seconds (the pauses that remain end
                once it returns, so a reader that waits for the end of input fails here instead of hanging)."""
                r, w = os.pipe()
                open_w, done = [True], threading.Event()

                def feed():
                    for pause, chunk in parts:
                        done.wait(pause)
                        if chunk is None:
                            os.close(w)
                            open_w[0] = False
                            return
                        os.write(w, chunk)

                feeder = threading.Thread(target=feed)
                feeder.start()
                start = time.monotonic()
                try:
                    try:
                        got = _read_payload(r, deadline)
                    except ValueError:
                        got = "refused"
                    if within is not None:
                        self.assertLess(time.monotonic() - start, within)
                    return got
                finally:
                    done.set()
                    feeder.join()
                    if open_w[0]:
                        os.close(w)
                    os.close(r)

            self.assertEqual(piped([(0, good), (0, None)]), json.loads(good))
            for label, raw in bad.items():
                self.assertEqual(piped([(0, raw), (0, None)]), "refused", label)
            # the deadline: an open pipe after a complete payload, and an end of input after the deadline
            self.assertEqual(piped([(0, good), (10, None)], 0.3, within=5), "refused")
            self.assertEqual(piped([(0, good), (0.6, None)], 0.3), "refused")
            self.assertEqual(piped([(0, good), (0.05, None)]), json.loads(good))
            # the clock is read again after every wait and every read: with the payload and its end of input
            # already in the pipe, the reader takes 7 readings; whichever reading (from the second on) is the
            # first past the deadline, the payload is refused, and with none past it, it is read
            for late in range(2, 9):
                readings = [0]

                def monotonic():
                    readings[0] += 1
                    return 0.0 if readings[0] < late else 100.0

                r, w = os.pipe()
                os.write(w, good)
                os.close(w)
                try:
                    with mock.patch.dict(globals(), {"time": mock.Mock(monotonic=monotonic)}):
                        try:
                            got = _read_payload(r, 1.0)
                        except ValueError as exc:
                            got = str(exc)
                finally:
                    os.close(r)
                self.assertEqual(got, json.loads(good) if late == 8 else _LATE, late)
            # an escaped lone surrogate is standard JSON, read as sent: it is not a policy character (the
            # validator refuses one), so it neither adds one nor hides one; the rest of the text is judged
            self.assertIsNone(_decide(call("\ud800safe"), self.env))
            self.is_deny(_decide(call("\ud800" + em), self.env))
            launch = [sys.executable, "-I", "-S", "-B", here]
            env = {"LC_ALL": "C", ROOT_VAR: self.root}
            for label, raw in bad.items():
                p = subprocess.run(launch, input=raw, capture_output=True, env=env, timeout=60)
                self.assertEqual((p.returncode, p.stderr), (0, b""), label)
                self.is_unchecked(json.loads(p.stdout), "cannot be read as JSON in UTF-8")
            for raw, want in ((good, None), (good.replace(b'"safe"', b'"\\ud800safe"'), None),
                              (good.replace(b'"safe"', b'"\\ud800\\u2014"'), "deny")):
                p = subprocess.run(launch, input=raw, capture_output=True, env=env, timeout=60)
                self.assertEqual((p.returncode, p.stderr), (0, b""), raw)
                if want is None:
                    self.assertEqual(p.stdout, b"", raw)
                else:
                    self.is_deny(json.loads(p.stdout))
            # through the hook as launched: a complete payload on a pipe that stays open, and (with the deadline
            # lowered to 0.5 seconds) an end of input sent 1.5 seconds after the hook says it is about to read
            p = subprocess.Popen(launch, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env=env)
            try:
                p.stdin.write(good)
                p.stdin.flush()
                rc = p.wait(timeout=60)
                out, err = p.stdout.read(), p.stderr.read()
            finally:
                if p.poll() is None:
                    p.kill()
                    p.wait()
                for stream in (p.stdin, p.stdout, p.stderr):
                    stream.close()
            self.assertEqual((rc, err), (0, b""))
            self.is_unchecked(json.loads(out), "did not end within the read deadline")
            late = ("import importlib.util, sys\n"
                    "spec = importlib.util.spec_from_file_location('late_hook', sys.argv[1])\n"
                    "module = importlib.util.module_from_spec(spec)\n"
                    "spec.loader.exec_module(module)\n"
                    "module._READ_DEADLINE = 0.5\n"
                    "sys.stderr.write('ready\\n')\n"
                    "sys.stderr.flush()\n"
                    "sys.exit(module.main(['char-policy-write.py']))\n")
            p = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", late, here], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            try:
                p.stdin.write(good)
                p.stdin.flush()
                ready = p.stderr.readline()
                time.sleep(1.5)
                p.stdin.close()
                rc = p.wait(timeout=60)
                out, err = p.stdout.read(), p.stderr.read()
            finally:
                if p.poll() is None:
                    p.kill()
                    p.wait()
                for stream in (p.stdin, p.stdout, p.stderr):
                    stream.close()
            self.assertEqual((ready, rc, err), (b"ready\n", 0, b""))
            self.is_unchecked(json.loads(out), "ending within 0.5 seconds (ValueError: the hook payload did not "
                                               "end within the read deadline)")

        def test_h23_unresolvable_path(self):
            # QA round 4: a symbolic link loop resolved silently; now every resolution fault other than a missing
            # component gets a note, before the out-of-scope and no-policy returns: under a root without a policy
            # file, inside the root, outside it, and through a component that is not a directory
            empty, outside = os.path.join(self.tmp, "empty"), os.path.join(self.tmp, "outside")
            for folder in (empty, outside, self.root):
                os.makedirs(folder, exist_ok=True)
                os.symlink("loop", os.path.join(folder, "loop"))
            self.put("NOTICE", "x\n")
            for root, target, needle in ((empty, os.path.join(empty, "loop", "a.md"), "(OSError)"),
                                         (self.root, self.at("loop/a.md"), "(OSError)"),
                                         (self.root, self.at("loop"), "(OSError)"),
                                         (self.root, os.path.join(outside, "loop", "a.txt"), "(OSError)"),
                                         (self.root, self.at("NOTICE/a.md"), "(NotADirectoryError)")):
                for content in ("safe", em):
                    note = self.is_unchecked(_decide({"tool_name": "Write", "tool_input": {
                        "file_path": target, "content": content}}, {ROOT_VAR: root}), "cannot be resolved")
                    self.assertIn(needle, note)
            # a missing target, and a missing directory above it that the write would create, still resolve
            self.is_deny(self.write("docs/new/deeper/a.md", em))
            self.assertIsNone(self.write("docs/new/deeper/a.md", "safe"))
            p = subprocess.run([sys.executable, "-I", "-S", "-B", here], capture_output=True, timeout=60,
                               input=json.dumps({"tool_name": "Write", "tool_input": {
                                   "file_path": os.path.join(empty, "loop", "a.md"), "content": "safe"}}).encode(
                                   "ascii"), env={"LC_ALL": "C", ROOT_VAR: empty})
            self.assertEqual((p.returncode, p.stderr), (0, b""))
            self.is_unchecked(json.loads(p.stdout), "cannot be resolved or encoded as a file name (OSError)")

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
            self.is_unchecked(json.loads(p.stdout), "at most 64 bytes, ending within 2 seconds (ValueError: hook "
                                                    "payload over the read bound)")

        def test_h20_surrogates(self):
            # a lone surrogate in any policy string makes the policy malformed, as the gate exits 2 on it: a note
            for policy in (dashes | {"chars": {"\ud800": "high surrogate"}}, dashes | {"chars": {em: "em\udfff"}},
                           dashes | {"id": "a\udc80"}, dashes | {"advice": "\ud800"},
                           dashes | {"scope": [{"file": "a\ud800"}]}):
                self.set_policy(policy)
                note = self.is_note(self.write("docs/a.md", f"x{em}\ud800"))
                self.assertIn("is malformed", note)
                self.assertIn("exits 2", note)
                self.assertFalse(any(ord(c) > 126 for c in note), note)
            # the code points either side of the surrogate block are ordinary policy characters
            self.set_policy(dashes | {"chars": {"\ud7ff": "before the surrogates", "\ue000": "after them"}})
            self.assertIn("U+E000 (after them)", self.is_deny(self.edit("docs/a.md", "x", "\ue000")))
            self.assertIn("U+D7FF (before the surrogates)", self.is_deny(self.write("docs/a.md", "\ud7ff")))

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
