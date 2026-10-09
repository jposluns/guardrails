#!/usr/bin/env python3
"""PreToolUse Write|Edit|MultiEdit hook (char-policy-write): deny a write that adds a character the policy forbids.

WHAT IT DOES
    A repository can forbid some characters in some of its files with a character policy, the data file
    .aiqt/char-policy.json at its root, which the CI gate tools/check_no_dashes.py enforces after the fact.
    This hook reads the same file and applies the same scope before a file write lands, so a write the gate
    would fail is refused while the fix is still one edit. It names no character itself: the characters,
    their names, the advice and the scope all come from the policy file.

    Event: PreToolUse, matcher Write|Edit|MultiEdit. Output: nothing (allow), one line holding the standard
    PreToolUse deny object, or one line holding a systemMessage note (allow with a note). Exit status: 0,
    with the decision in the JSON, except the floor guard's exit 2 on an interpreter older than Python 3.14
    that can start the hook, armed or not, while its error line can be written to stderr and stderr flushed
    at shutdown (it currently exits 1 with stderr closed, and 120 when that write fails or succeeds and the
    shutdown flush fails; either allows the call); one that cannot start it exits with Python's own status
    first (DECISION). The verdict is deny, a note or silence: this hook never asks. Once armed (its root
    set), it allows every call it cannot evaluate with a note naming why, a malformed call among them, and
    it allows silently only a tool other than Write, Edit or MultiEdit and a well-formed call (see
    DECISION) that it evaluates and finds clean, whose target is outside the root or the policy's scope, or
    whose root holds no policy file.

CONFIGURATION
    AIQT_CHAR_POLICY_ROOT holds one absolute path, the repository root whose policy applies. There is no
    default and no older spelling. Unset or empty, the hook is not armed and does nothing, silently (the
    floor guard still runs first: DECISION). Set but relative, holding a control character, or naming a path
    that does not exist or is not a directory, it checks nothing and says so in a note on every call; so does
    an armed hook launched with any command-line argument other than --self-test alone. The policy file is
    <root>/.aiqt/char-policy.json; absent, the hook allows a well-formed call silently and notes a malformed
    one (the gate then applies its built-in default policy, which this hook does not copy).

POLICY FILE
    {"version": 1, "id": <name>, "chars": {<one code point>: <name>, ...}, "advice": <optional text>,
     "scope": [{"tree": <dir>, "suffixes": [".md", ...], "skip": [<name>, ...]} | {"file": <path>}, ...]}
    Paths are repo-relative POSIX paths ("." alone is the root as a tree). A tree entry scopes a file under
    the tree whose suffix (the text from the final dot of its name, case-sensitive) is listed, unless a
    directory or the file below the tree has a name in that entry's skip list; a file entry scopes that one
    file. Unknown keys and any other fault make the file malformed, and so does a character that
    str.splitlines() treats as a line boundary (U+2028 or U+2029; the rest are control characters), which the
    gate's line scan could not see, or a lone surrogate (U+D800 to U+DFFF) in any policy string. The region
    between the BEGIN COPY and END COPY markers below is generated from the gate's marked region by
    tools/gen_char_policy.py, whose --check fails on any byte difference: edit the gate and regenerate, never
    the copy. The region holds the validator and every module-level name it reads. H12 compares the bytes, the
    policy path, a sample of policies through both validators, and this hook's scope test with the gate's
    walk. The hook's self-test H12 walks both files' module-level statements, compound statements' bodies
    included, and fails when one outside the copied region binds a name the region binds or reads, or
    __builtins__, at module scope, or a def or class body outside it declares one global; or when one makes an
    attribute or item store (an assignment, an augmented or annotated assignment, a for, with or comprehension
    target) or deletion, at module scope, in a class body or in a def's or lambda's decorators or defaults,
    whose target chain starts from such a name, json, builtins, sys.modules, this module or an alias of one of
    them. Conservatively, whatever the stored value, it also fails on a store whose chain starts from anything
    but a name (a call such as __import__("json"), globals() or logging.getLogger("app"), a conditional
    expression) or has a link (an attribute or a constant string key) named sys, json, builtins or modules. An
    alias is a name bound by one of these forms: an import of json, builtins or sys, of a submodule of one or
    of a name from one (from sys import modules binds an alias of sys.modules); an import of this module
    (import __main__, or the gate's module check_no_dashes imported under any name); a from-import from this
    module, whose name is an alias of the name it imports; and a for, with, comprehension or match target
    whose source is such a name or alias, sys.modules (sys or an alias of sys, then .modules), or an item of
    sys.modules, which counts as this module. The source is the iterable, the context expression or the
    subject and, recursively, each element of a tuple, list or set display, the element of a comprehension and
    each positional argument of a call but a bare name of a builtin function or class that the file does not
    bind (map(list, _rows)), never the function called or a keyword argument. Scope follows Python's rules: a
    binding is module-level only at module scope or in a def or class body whose own scope declares the name
    global (an enclosing def's declaration does not count); any other binding is local to its def or class
    body, a comprehension target to its comprehension and a nonlocal name to the enclosing def, and none
    aliases the module-level name of the same spelling. A name in a source or a store's chain counts as the
    binding its scope reads: its own; for a name its scope does not bind, the nearest enclosing def's that
    binds it (class bodies skipped) or else the module's; and in a class body that binds the name, which can
    read it before binding it, conservatively both its own and the module's, never an enclosing def's. Every
    other aliasing form is out of scope; diff review is the control. Ordinary stores are not flagged:
    sys.path[0] = ..., an item of os.environ or of a module-level dict under a key not named sys, json,
    builtins or modules, a store to an attribute not so named of any other name (_Options.verbose = True), and
    a store whose chain has no link so named and starts from a target whose source is none of those (for _row
    in sorted(_rows, key=len): _row[0] = 2). That walk catches accidental drift between the two copies; it is
    not a defence against a deliberate edit that replaces behaviour through a path the walk does not model.
    Examples, not a complete list: an alias made by plain assignment or a walrus (m = sys.modules[__name__],
    then m.validate_policy = ...) or by a target over a call's result (for m in
    (importlib.import_module("__main__"),): ...); an item key that is not a constant string; a call such as
    setattr or exec; an in-place method call such as globals().update, sys.modules.update or
    json.__dict__.update; a store in a function or lambda body; reflective access through an object the walk
    cannot name; another module patching the file; and a module that shadows a standard library module, such
    as a json.py. The gate imports json before it puts tools/ on sys.path, so under python3 -I, as CI runs it,
    a tools/json.py is never imported (the gate's self-test pins this); run without -I, Python puts a script's
    own directory first on sys.path, and a module there named like a standard library module that is neither
    built in, frozen, nor already imported at startup (json is one; sys, os and time are not) shadows that
    module, for the gate and for this hook alike. The behaviour sample runs both validators in one process, so
    it shares one json module and cannot see a change made to json. Diff review is the control for a
    deliberate edit. The recorded hashes (.preview/SHA256SUMS for this hook, the release manifest
    .aiqt/manifest.toml for both files) let an installer or a release check detect a shipped copy that differs
    from the reviewed one; whoever makes an edit can record the new hashes in the same change.

DECISION
    - The floor guard runs first, armed or not, on every call the launch line hands to Python (the README
      launch line skips the hook, so the call goes ahead, when a standard stream is a directory). On an
      interpreter older than Python 3.14 that can start the hook, the guard at the top of this file reads
      no input, writes one line beginning `error: char-policy-write.py requires Python 3.14 or newer` to
      stderr and exits 2, which PreToolUse treats as a deny, so every such matching Write, Edit and
      MultiEdit call is denied until Python is upgraded or the hook's entry is removed. That holds only
      while the guard's write to stderr succeeds and the interpreter can then flush stderr at shutdown:
      with stderr closed the guard currently exits 1, and when that write fails (a full device or a broken
      pipe, say), or succeeds and the shutdown flush of stderr fails, it currently exits 120; either
      status allows the call unchecked. An older interpreter that cannot start the hook never reaches the
      guard and fails with Python's own error first: one that predates the -I option exits 2, which still
      denies every such matching call, and one that accepts -I but cannot compile this file (Python 3.4
      and 3.5 cannot: it uses f-strings, and 3.4 also rejects its starred items in list displays) exits 1,
      a non-blocking error, so every such matching call is allowed unchecked; .preview/README.md
      (Installing a hook, step 4) describes those cases.
    - AIQT_CHAR_POLICY_ROOT unset or empty: allow, silently (the hook is not armed). Set but relative,
      holding a control character, or naming a path that does not exist, cannot be examined or is not a
      directory: allow with a note naming the variable and the reason. Armed, but launched with a
      command-line argument other than --self-test alone, or given an argument list that is not a non-empty
      list of strings (main() called from other code; a real launch passes sys.argv, which always is one):
      allow with a note saying so, without reading the payload.
    - A tool_name string naming a tool other than Write, Edit or MultiEdit: allow, silently.
    - Fail-open, made visible: the preview channel allows what it cannot evaluate (the hook is opt-in and the
      CI gate is the backstop), but always with a note naming the reason, never silently and never with an ask.
      That covers a payload that is not read through its end of input within 2 seconds, is over 64 MiB, is not
      strict UTF-8 (UTF-16, UTF-32, an encoded surrogate, a byte order mark), is not one JSON value, holds NaN,
      Infinity or -Infinity, holds a key twice in one JSON object, or is not a JSON object; a missing
      tool_name, a tool_input that is not an object, a file_path that is not a non-empty string without control
      characters, a relative file_path with no absolute cwd, a field the tool needs (old_string, new_string,
      edits, content) of the wrong type, a path that cannot be resolved (a symbolic link loop, a component that
      is not a directory, a permission fault: every fault but a missing component) or encoded as a file name (a
      lone surrogate, say), and an internal error. An escaped lone surrogate (\\ud800) in a string is standard
      JSON and is read as sent and counted as itself: the validator refuses it as a policy character, so as
      sent it neither adds one nor hides one (see RESIDUAL COVERAGE for a harness that writes it as U+FFFD). A
      call is well-formed when none of these faults applies to it. The order: the file_path's form (and the cwd
      a relative one is joined onto), then the fields, then the path is resolved and compared with the root,
      and only then is the policy file read, so a malformed call gets a note wherever it points and whether or
      not the root holds a policy file.
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
    - Unverified harness behaviour: if the harness writes a string through a UTF-8 encoder that replaces a
      lone surrogate with U+FFFD, a call whose new text holds an escaped lone surrogate (\\ud800) puts U+FFFD
      in the file, and this hook, which counts the surrogate as itself, misses that increase for a policy
      that forbids U+FFFD. Whether the harness does this is not verified here; the gate scans the written
      file.
    - Path aliases: a hard link, a case-insensitive or Unicode-normalizing filesystem, or a symbolic link
      inside the root (the gate's walk does not follow linked directories; this hook resolves real paths)
      can make the hook's scope test differ from the gate's walk.
    - A malformed policy fails open here (with a note) and closed at the gate.
    - A reviewed edit to the policy file narrows the gate and the hook together; diff review is the only
      control over it.
    - The repository's other character checks with their own fixed sets and scopes are not read.
    - The file can change between this hook's decision and the write (a race).
    - A payload over 64 MiB, one that does not end within 2 seconds, one that is not strict UTF-8 JSON, one
      with a key twice in one JSON object, and one that is not a JSON object are allowed with a note.
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
# The policy validator. Every module-level name it reads is bound in this region or is a builtin. The hook's
# self-test H12 walks both files' module-level statements, compound statements' bodies included, and fails when one
# outside the copied region binds a name the region binds or reads, or __builtins__, at module scope, or a def or
# class body outside it declares one global; or when one makes an attribute or item store (an assignment, an
# augmented or annotated assignment, a for, with or comprehension target) or deletion, at module scope, in a class
# body or in a def's or lambda's decorators or defaults, whose target chain starts from such a name, json, builtins,
# sys.modules, this module or an alias of one of them. Conservatively, whatever the stored value, it also fails on a
# store whose chain starts from anything but a name (a call such as __import__("json"), globals() or
# logging.getLogger("app"), a conditional expression) or has a link (an attribute or a constant string key) named
# sys, json, builtins or modules. An alias is a name bound by one of these forms: an import of json, builtins or
# sys, of a submodule of one or of a name from one (from sys import modules binds an alias of sys.modules); an
# import of this module (import __main__, or the gate's module check_no_dashes imported under any name); a
# from-import from this module, whose name is an alias of the name it imports; and a for, with, comprehension or
# match target whose source is such a name or alias, sys.modules (sys or an alias of sys, then .modules), or an item
# of sys.modules, which counts as this module. The source is the iterable, the context expression or the subject
# and, recursively, each element of a tuple, list or set display, the element of a comprehension and each positional
# argument of a call but a bare name of a builtin function or class that the file does not bind (map(list, _rows)),
# never the function called or a keyword argument. Scope follows Python's rules: a binding is module-level only at
# module scope or in a def or class body whose own scope declares the name global (an enclosing def's declaration
# does not count); any other binding is local to its def or class body, a comprehension target to its comprehension
# and a nonlocal name to the enclosing def, and none aliases the module-level name of the same spelling. A name in a
# source or a store's chain counts as the binding its scope reads: its own; for a name its scope does not bind, the
# nearest enclosing def's that binds it (class bodies skipped) or else the module's; and in a class body that binds
# the name, which can read it before binding it, conservatively both its own and the module's, never an enclosing
# def's. Every other aliasing form is out of scope; diff review is the control. Ordinary stores are not flagged:
# sys.path[0] = ..., an item of os.environ or of a module-level dict under a key not named sys, json, builtins or
# modules, a store to an attribute not so named of any other name (_Options.verbose = True), and a store whose chain
# has no link so named and starts from a target whose source is none of those (for _row in sorted(_rows, key=len):
# _row[0] = 2). That walk catches accidental drift between the two copies; it is not a defence against a deliberate
# edit that replaces behaviour through a path the walk does not model. Examples, not a complete list: an alias made
# by plain assignment or a walrus (m = sys.modules[__name__], then m.validate_policy = ...) or by a target over a
# call's result (for m in (importlib.import_module("__main__"),): ...); an item key that is not a constant string; a
# call such as setattr or exec; an in-place method call such as globals().update, sys.modules.update or
# json.__dict__.update; a store in a function or lambda body; reflective access through an object the walk cannot
# name; another module patching the file; and a module that shadows a standard library module, such as a json.py.
# The recorded hashes (.preview/SHA256SUMS for the hook, .aiqt/manifest.toml for both files) let an installer or a
# release check detect a shipped copy that differs from the reviewed one.
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


def _no_payload_duplicates(pairs):
    """A JSON object of the hook payload as a dict; a key that appears twice in one object raises ValueError,
    so the call gets the cannot-evaluate note instead of being judged on one of the two values."""
    seen = set()
    for key, _ in pairs:
        if key in seen:
            raise ValueError(f"duplicate key {ascii(key)[:80]} in a JSON object of the hook payload")
        seen.add(key)
    return dict(pairs)


def _read_payload(fd=0, deadline=None):
    """The payload, parsed once the input has ended (EOF) and that end was read before the deadline (by default
    _READ_DEADLINE seconds, read at call time). Every byte read through the end counts toward one _MAX_INPUT
    budget. The clock is read again after each wait and after each read, before an end of input is accepted: a
    reading taken after a read returns is never earlier than the read, however late the OS runs this process,
    so an end of input that comes after the deadline is refused. Each wait is for the time left at most, but
    the OS can return from a wait late, so refusing can take longer than the deadline. The bytes must be strict
    UTF-8 (no UTF-16 or UTF-32, no encoded surrogate, and no byte order mark, which json.loads refuses in a
    str) holding one JSON value without NaN, Infinity or -Infinity and without a key that appears twice in one
    object. Raises ValueError for any of these faults and for no end read before the deadline."""
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
            return json.loads(bytes(data).decode("utf-8"), object_pairs_hook=_no_payload_duplicates,
                              parse_constant=_no_payload_constant)
        data += chunk
        if len(data) > _MAX_INPUT:
            raise ValueError("hook payload over the read bound")


def _emit_line(text):
    """Write one line to stdout; on any output failure point descriptor 1 at /dev/null so the interpreter's
    shutdown flush cannot fail, or end at once with status 0: past the floor guard, which an interpreter that
    cannot start the hook never reaches, the hook always exits 0."""
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
    """The hook, reached only past the floor guard: always 0. `--self-test` alone runs the self-test instead.
    A real launch passes sys.argv, a non-empty list of strings; any other argv (a call from other code, a
    tuple included) is treated like an unknown argument, and its payload is never read."""
    readable = isinstance(argv, list) and len(argv) > 0 and all(isinstance(a, str) for a in argv)
    if readable and list(argv[1:]) == ["--self-test"]:
        return _self_test()
    if not os.environ.get(ROOT_VAR):
        return 0  # unset or empty: the hook is not armed, so it reads nothing and says nothing
    if not readable:  # armed but given an argument list it cannot read: it checks nothing, and says so
        _emit_line(json.dumps(_unchecked("the hook was given an argument list that is not a non-empty list of "
                                         "strings")))
        return 0
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
    import builtins
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
        is not, though its name, decorators, defaults and bases are, and nor is a comprehension's target, which
        binds in the comprehension (a walrus in a comprehension, which binds outside it, is). A wildcard import
        adds "*"."""
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
        if isinstance(node, ast.comprehension):  # Python forbids a walrus in its target
            for child in [node.iter] + node.ifs:
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

    # The two conservative findings of stores(), whatever the stored value: a target chain that starts from
    # anything but a name, and a chain with a link named sys, json, builtins or modules. A third, this_module, is
    # what an alias of this module counts as: a name an import of __main__ (the module a script runs as) or of the
    # gate's own module name (check_no_dashes) binds, in either file, and sys.modules[...] as a target's source.
    from_expression = "<a store whose target chain starts from an expression>"
    through_watched = "<a store whose target chain has a link named sys, json, builtins or modules>"
    this_module = "<a store through an alias of this module>"
    watched = frozenset(("sys", "json", "builtins", "modules"))
    # The builtin functions and classes, none of which takes an attribute or item store: a call's positional
    # argument naming one that the file does not bind (map(list, _rows)) is not a source (see sources()).
    builtin_names = frozenset(name for name, value in vars(builtins).items() if isinstance(value, (type, type(len))))
    own_modules = frozenset(("__main__", GATE.rpartition("/")[2].rpartition(".")[0]))

    def stores(node, out, counts):
        """Add to out, for each attribute or item store or deletion node makes at module scope (an assignment,
        an augmented or annotated assignment, a del, or a for, with or comprehension target; a walrus or
        import-as target is always a plain name), whatever counts (built by rebound()) says the name its target
        chain starts from counts as where it runs: the name itself when it reads the module-level binding, and
        json, builtins, sys, sys.modules, this_module and the module-level names an alias's source names; "sys"
        becomes "sys.modules" when the chain's first link is .modules. It also adds, conservatively and whatever
        the value,
        from_expression for a chain that starts from anything but a name (a call such as __import__("json"),
        getattr(sys, "modules"), globals() or vars(json), a conditional expression, a list, a walrus), and
        through_watched for a chain with a link, the stored one included, that is an attribute or a constant
        string key named sys, json, builtins or modules (os.sys.modules[...], m.__dict__["modules"][...]). A
        def or lambda body, which runs only when called, is not walked, though its decorators and defaults
        are; a class body, which runs at once, is."""
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            inner = list(getattr(node, "decorator_list", [])) + node.args.defaults
            for child in inner + [d for d in node.args.kw_defaults if d is not None]:
                stores(child, out, counts)
            return
        if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, (ast.Store, ast.Del)):
            first, links = node, []
            while True:
                if isinstance(first, ast.Attribute):
                    links.append(first.attr)
                elif isinstance(first.slice, ast.Constant) and isinstance(first.slice.value, str):
                    links.append(first.slice.value)
                if not isinstance(first.value, (ast.Attribute, ast.Subscript)):
                    break
                first = first.value
            if watched.intersection(links):
                out.add(through_watched)
            if isinstance(first.value, ast.Name):
                for base in counts(first.value):
                    if base == "sys" and isinstance(first, ast.Attribute) and first.attr == "modules":
                        base = "sys.modules"
                    out.add(base)
            else:
                out.add(from_expression)
        for child in ast.iter_child_nodes(node):
            stores(child, out, counts)

    def resolver(tree):
        """(where, home, visible) for tree, with scope resolved by Python's rules. where maps id(node) to the scope
        node runs in, for every node of tree; a scope is a dict holding its number (the module's is 0), its kind
        (module, def, class or comprehension; a lambda is a def), its parent, and the names its own body binds,
        declares global and declares nonlocal. A def's or lambda's decorators, defaults, annotations and type
        parameters, a class's decorators, bases and keywords, and a comprehension's first iterable run in the
        enclosing scope; a walrus binds in the nearest enclosing scope that is not a comprehension. home(scope,
        name) is the scope a binding of name made in scope lands in: the module's at module scope or when the
        scope's own body declares name global (an enclosing def's declaration does not count), the enclosing def
        that binds it for a nonlocal name, and otherwise scope itself (a def or class body, or a comprehension for
        its target). visible(scope, name) lists the numbers of the scopes whose binding of name a read in scope
        can find: its own scope's when it binds name, and otherwise the nearest enclosing def's that binds it or
        the module's, class bodies skipped. A class body that binds name reads it from its own namespace and then
        the module's, never an enclosing def's; since it can read name before binding it, both are listed."""
        where, made = {}, []

        def scope_of(kind, parent):
            made.append({"number": len(made), "kind": kind, "parent": parent, "bound": set(), "global": set(),
                         "nonlocal": set()})
            return made[-1]

        def place(node, scope):
            where[id(node)] = scope
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                inner = scope_of("class" if isinstance(node, ast.ClassDef) else "def", scope)
                body = [node.body] if isinstance(node, ast.Lambda) else node.body
                if not isinstance(node, ast.Lambda):
                    scope["bound"].add(node.name)
                if not isinstance(node, ast.ClassDef):
                    args = node.args
                    inner["bound"].update(arg.arg for arg in args.posonlyargs + args.args + args.kwonlyargs
                                          + [args.vararg, args.kwarg] if arg is not None)
                for child in ast.iter_child_nodes(node):
                    place(child, inner if any(child is b for b in body) else scope)
                return
            if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                inner = scope_of("comprehension", scope)
                for number, generator in enumerate(node.generators):
                    where[id(generator)] = inner
                    place(generator.iter, inner if number else scope)
                    for child in [generator.target] + generator.ifs:
                        place(child, inner)
                for child in ast.iter_child_nodes(node):
                    if not isinstance(child, ast.comprehension):
                        place(child, inner)
                return
            if isinstance(node, ast.NamedExpr):
                outer = scope
                while outer["kind"] == "comprehension":
                    outer = outer["parent"]
                outer["bound"].add(node.target.id)
                where[id(node.target)] = scope
                place(node.value, scope)
                return
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                scope["global" if isinstance(node, ast.Global) else "nonlocal"].update(node.names)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                scope["bound"].add(node.id)
            elif isinstance(node, ast.alias) and node.name != "*":
                scope["bound"].add(node.asname or node.name.partition(".")[0])
            elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
                scope["bound"].add(node.name)
            elif isinstance(node, ast.MatchMapping) and node.rest:
                scope["bound"].add(node.rest)
            for child in ast.iter_child_nodes(node):
                place(child, scope)

        place(tree, scope_of("module", None))
        module = made[0]
        for scope in made:
            module["bound"] |= scope["bound"] & scope["global"]

        def home(scope, name):
            if scope["kind"] == "module" or name in scope["global"]:
                return module
            if name in scope["nonlocal"]:
                outer = scope["parent"]
                while outer["kind"] != "module":
                    if outer["kind"] == "def" and name in outer["bound"] and name not in outer["nonlocal"]:
                        return outer
                    outer = outer["parent"]
            return scope

        def visible(scope, name):
            if scope["kind"] == "module" or name in scope["global"]:
                return [0]
            if name in scope["nonlocal"]:
                return [home(scope, name)["number"]]
            if name in scope["bound"]:  # a class body reads a name it binds from itself, then the module (LOAD_NAME)
                return [scope["number"]] + ([0] if scope["kind"] == "class" else [])
            outer = scope["parent"]
            while outer["kind"] == "class":
                outer = outer["parent"]
            return visible(outer, name)

        return where, home, visible

    def sources(node, counts, builtin):
        """What a name that a for, with, comprehension or match target binds over the source node counts as: a
        name, whatever counts (built by rebound()) says it counts as where it runs; "sys.modules" for the
        attribute modules of something that counts as sys; this_module for an item of something that counts as
        sys.modules; and, recursively, whatever each element of a tuple, list or set display, the element of a
        comprehension, and each positional argument of a call counts as, but a bare name that builtin says is a
        builtin function or class the file does not bind (map(list, _rows)), and never the function called or a
        keyword argument (the walk does not trace a call's result). Anything else counts as nothing."""
        if isinstance(node, ast.Name):
            return counts(node)
        if isinstance(node, ast.Attribute):
            return ({"sys.modules"} if node.attr == "modules" and "sys" in sources(node.value, counts, builtin)
                    else set())
        if isinstance(node, ast.Subscript):
            return {this_module} if "sys.modules" in sources(node.value, counts, builtin) else set()
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            parts = node.elts
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            parts = [node.elt]
        elif isinstance(node, ast.Call):
            parts = [arg for arg in node.args if not (isinstance(arg, ast.Name) and builtin(arg))]
        else:
            return set()
        return set().union(*(sources(part, counts, builtin) for part in parts))

    def rebound(text):
        """The names the marked region binds or reads (every name in it, function bodies included), with
        __builtins__, that the rest of the module binds at module scope or that a def or class body outside the
        region declares global, plus "*" for a wildcard import outside it, plus the base of each attribute or item
        store outside the region, at module scope, whose base is one of those names, builtins, sys.modules or
        this_module or counts as one, plus the conservative findings of stores() (from_expression and
        through_watched). The alias map aliases says what a binding counts as; its key is (the number of the
        scope the binding lands in, by resolver()'s home, the name). Only these forms make an entry: an import of
        json, builtins or sys (that module, or sys.modules); an import of this module, a module named in
        own_modules (this_module); a from-import from this module (the module-level name it imports); and a for,
        with, comprehension or match target in a statement outside the region (whatever sources() says its
        iterable, context expression or subject counts as). A name read where it runs counts as each binding
        resolver()'s visible lists, the module-level one as the name itself too; each binding also counts as
        whatever the bindings it counts as count as, to a fixed point. Each module-level statement must lie
        wholly on one side."""
        first, last, _ = marked(text)
        inside, outside, stored, aliases, links, others = {"*", "__builtins__"}, set(), set(), {}, [], []
        tree = ast.parse(text)
        where, home, visible = resolver(tree)
        module = where[id(tree)]

        def counts(name):
            found = set()
            for number in visible(where[id(name)], name.id):
                found |= ({name.id} if number == 0 else set()) | aliases.get((number, name.id), set())
            return found

        def builtin(name):
            return (name.id in builtin_names and name.id not in module["bound"]
                    and visible(where[id(name)], name.id) == [0])

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module_name = (node.module or "") if isinstance(node, ast.ImportFrom) else None
                for alias in node.names:
                    whole = alias.name if module_name is None else f"{module_name}.{alias.name}"
                    bound = alias.asname or (alias.name if module_name is not None else whole.partition(".")[0])
                    key = (home(where[id(node)], bound)["number"], bound)
                    if whole.partition(".")[0] in ("json", "builtins", "sys"):
                        aliases.setdefault(key, set()).add(
                            "sys.modules" if whole == "sys.modules" else whole.partition(".")[0])
                    elif whole.rpartition(".")[2] in own_modules:
                        aliases.setdefault(key, set()).add(this_module)
                    elif module_name is not None and module_name.rpartition(".")[2] in own_modules:
                        imported = ast.Name(alias.name)
                        where[id(imported)] = module
                        links.append(({key}, imported))
        for stmt in tree.body:
            start = min([stmt.lineno] + [d.lineno for d in getattr(stmt, "decorator_list", [])])
            if first <= start and stmt.end_lineno <= last:
                bindings(stmt, inside)
                inside.update(n.id for n in ast.walk(stmt) if isinstance(n, ast.Name))
            elif stmt.end_lineno < first or start > last:
                others.append(stmt)
            else:
                raise AssertionError(f"the statement at line {start} straddles a copy marker")
        for stmt in others:
            for node in ast.walk(stmt):
                pairs = []
                if isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                    pairs.append((node.target, node.iter))
                elif isinstance(node, (ast.With, ast.AsyncWith)):
                    pairs += [(item.optional_vars, item.context_expr) for item in node.items if item.optional_vars]
                elif isinstance(node, ast.Match):
                    pairs += [(case.pattern, node.subject) for case in node.cases]
                for target, source in pairs:
                    bound = set()
                    bindings(target, bound)
                    links.append(({(home(where[id(node)], name)["number"], name) for name in bound}, source))
        changed = True
        while changed:
            changed = False
            for keys, source in links:
                reach = sources(source, counts, builtin)
                for key in keys:
                    if not reach <= aliases.setdefault(key, set()):
                        aliases[key] |= reach
                        changed = True
        for stmt in others:
            bindings(stmt, outside)
            outside.update(name for n in ast.walk(stmt) if isinstance(n, ast.Global) for name in n.names)
            stores(stmt, stored, counts)
        return (inside & outside) | (stored & (inside | {"builtins", "sys.modules", from_expression,
                                                          through_watched, this_module}))

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
    # from X import Y as Z); then QA round 4's shadowed aliases, where an import of another module under the
    # same name in a function body must not hide a module-level store; the last ones are QA round 6's stores
    # through a chain that does not start from a name or that passes through sys, json, builtins or modules,
    # each reproduction both reviewers listed, and one vector for each kind of store whose target can be such
    # a chain (an assignment, an augmented and an annotated assignment, a del, a for, with and comprehension
    # target); then QA round 7's stores through a name that an import of this module (import __main__, the gate
    # imported under any name), a from-import from it, or a for, with, comprehension or match target binds, each
    # reproduction the reviewer listed and one vector for each of those binding forms; the three stores that
    # only the sys, builtins and modules links catch, one for each; and the documented watched-key and
    # call-start cases; then QA round 8's: a for, with, match and comprehension target over sys.modules[...]
    # (this module), each way of naming sys.modules in a source (the name, sys imported under another name,
    # modules imported from sys), a comprehension as a source, a match on sys.modules, an async for and an
    # async with target (a vector each, in a def that declares the name global, with the source a name and a
    # call), and an import in a def that declares its name global and in a class body; then QA round 9's scope
    # cases: a class body in a def whose own scope declares the name global (an import and a for, the
    # reviewer's reproductions), a def-local or class-local alias as the source of a target declared global, a
    # store in a class body through a class-local for target and through a comprehension's own target, and a
    # call's positional argument named like a builtin that the file binds (at module scope, in a class body,
    # and in a def that declares it global), a store in a class body before the class binds the name, a
    # comprehension's first iterable read in the class body around it, a def's default read outside the def,
    # and a store in a comprehension through a name a walrus in it binds outside it; then QA round 10's: a class
    # body in a def, the class's own scope declaring a for target global, whose iterable names a name the class
    # binds after the loop and the def binds too, so the read finds the module's import, never the def's (the
    # reviewers' reproductions, and the class nested in another class), and a class that does not bind the name,
    # nested in one that does, whose read finds the def's import. A vector's first item is the one finding it
    # must give, or a tuple of every finding it must give.
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
        (("sys.modules", through_watched), "import json as _real_json\nclass _J:\n"
                                           "    def loads(self, *args, **kwargs):\n"
                                           "        data = _real_json.loads(*args, **kwargs)\n"
                                           '        data.pop("metadata", None)\n        return data\n'
                                           'sys.modules["json"] = _J()'),
        (("sys.modules", through_watched), 'import sys as _s\n_s.modules["json"] = None'),
        (("sys.modules", through_watched), 'from sys import modules as _mods\n_mods["json"] = None'),
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
        (from_expression, "(json if True else json).loads = json.loads"),
        (through_watched, 'import sys as _s\n_s.__dict__["modules"]["json"] = json'),
        (through_watched, 'os.sys.modules["json"] = None'),
        (from_expression, '__import__("json").loads = None'),
        (from_expression, 'import importlib\nimportlib.import_module("json").loads = None'),
        ((from_expression, through_watched), 'getattr(sys, "modules")["json"] = None'),
        (from_expression, "[json][0].loads = None"),
        (from_expression, "(json if True else None).loads = None"),
        (from_expression, "(_j := json).loads = None"),
        (through_watched, "import types as _ty\n_fake_json = _ty.ModuleType('json')\n"
                          "_fake_json.loads = lambda *args, **kwargs: {}\nos.sys.modules['json'] = _fake_json"),
        (from_expression, 'globals()["_TOP_KEYS"] = None'),
        (from_expression, 'vars(json)["loads"] = None'),
        ((from_expression, through_watched), '__import__("sys").modules["builtins"].sorted = None'),
        (through_watched, "_self = sys.modules[__name__]\n_self.json = None"),
        (from_expression, "(json if True else None).loads += None"),
        (from_expression, "(json if True else None).loads: object = None"),
        (from_expression, "del (json if True else None).loads"),
        (from_expression, "for (json if True else None).loads in ():\n    pass"),
        (from_expression, "with open(__file__) as (json if True else None).loads:\n    pass"),
        (from_expression, "[0 for (json if True else None).loads in ()]"),
        (this_module, 'import __main__ as _m\n_m._TOP_KEYS = _TOP_KEYS | {"metadata"}'),
        (this_module, "import __main__\n__main__.validate_policy = None"),
        ("validate_policy", "from __main__ import validate_policy as _vp\n_vp.__code__ = _shim.__code__"),
        ("validate_policy", "for _vp in (validate_policy,):\n    _vp.__code__ = _shim.__code__"),
        (this_module, "import check_no_dashes as _me\n_me.validate_policy = None"),
        (this_module, "from tools import check_no_dashes as _me\n_me.validate_policy = None"),
        ("validate_policy", "import contextlib\nwith contextlib.nullcontext(validate_policy) as _vp:\n"
                            "    _vp.__code__ = None"),
        ("validate_policy", "match validate_policy:\n    case _vp:\n        _vp.__code__ = None"),
        ("validate_policy", "[0 for _vp in (validate_policy,) for _vp.__code__ in [None]]"),
        ("validate_policy", "from __main__ import validate_policy as _a\nfor _b in (_a,):\n    _b.__code__ = None"),
        (this_module, "import __main__ as _m\nfor _x in (_m,):\n    _x.POLICY_CAP = 1"),
        ("json", "def _helper():\n    global _j\n    for _j in (json,):\n        pass\n_helper()\n_j.loads = None"),
        ("validate_policy", "def _bind():\n    global _b\n    for _b in (_a,):\n        pass\n"
                            "for _a in (validate_policy,):\n    _bind()\n_b.__code__ = None"),
        (through_watched, "os.sys.flags = None"),
        (through_watched, "import site\nsite.builtins.sorted = None"),
        (through_watched, "_s = os.sys\n_s.modules[__name__].validate_policy = None"),
        (through_watched, '_settings = {}\n_settings["json"] = 1'),
        (through_watched, 'os.environ["json"] = "1"'),
        (from_expression, 'import logging\nlogging.getLogger("app").level = 20'),
        (this_module, "for _me in (sys.modules[__name__],):\n    _me.validate_policy = None"),
        (this_module, "import contextlib\nwith contextlib.nullcontext(sys.modules[__name__]) as _me:\n"
                      "    _me.validate_policy = None"),
        (this_module, "match sys.modules[__name__]:\n    case _me:\n        _me.validate_policy = None"),
        (this_module, "[0 for _m in (sys.modules[__name__],) for _m.validate_policy in [None]]"),
        (this_module, "for _j in (sys.modules['json'],):\n    _j.loads = None"),
        (this_module, "for _m in [sys.modules[_k] for _k in (__name__,) if _k in sys.modules]:\n"
                      "    _m._TOP_KEYS = _m._TOP_KEYS | {'metadata'}"),
        (this_module, "from sys import modules as _mods\nfor _me in (_mods[__name__],):\n"
                      "    _me.validate_policy = None"),
        (this_module, "import sys as _s\nfor _me in (_s.modules[__name__],):\n    _me.validate_policy = None"),
        ("sys.modules", "match sys.modules:\n    case {'__main__': _m}:\n        _m.validate_policy = None"),
        ("sys.modules", "for _mods in (sys.modules,):\n    _mods[__name__].validate_policy = None"),
        ("validate_policy", "async def _bind():\n    global _vp\n"
                            "    async for _vp in validate_policy:\n        pass\n_vp.__code__ = None"),
        ("validate_policy", "async def _bind():\n    global _vp\n"
                            "    async with validate_policy as _vp:\n        pass\n_vp.__code__ = None"),
        ("validate_policy", "async def _h():\n    global _v\n"
                            "    async for _v in _agen(validate_policy):\n        pass\n_v.__code__ = None"),
        ("validate_policy", "async def _h():\n    global _v\n"
                            "    async with _cm(validate_policy) as _v:\n        pass\n_v.__code__ = None"),
        ("json", "def _helper():\n    global _j\n    import json as _j\n_j.loads = None"),
        ("json", "class _Patch:\n    import json as _j\n    _j.loads = None"),
        ("json", "def _setup():\n    class _Holder:\n        global _cfg\n        import json as _cfg\n"
                 "_cfg.loads = None"),
        ("json", "def _setup():\n    class _Holder:\n        global _cfg\n        for _cfg in (json,):\n"
                 "            pass\n_cfg.loads = None"),
        ("json", "def _h():\n    global _y\n    import json as _x\n    for _y in (_x,):\n        pass\n"
                 "_y.loads = None"),
        ("json", "class _P:\n    global _y\n    import json as _x\n    for _y in (_x,):\n        pass\n"
                 "_y.loads = None"),
        ("json", "class _P:\n    for _j in (json,):\n        _j.loads = None"),
        ("json", "class _P:\n    [0 for _j in (json,) for _j.loads in [None]]"),
        ("json", "import json as filter\nfor _x in map(filter, ()):\n    _x.loads = None"),
        ("json", "class _P:\n    import json as filter\n    for _x in map(filter, ()):\n        _x.loads = None"),
        ("json", "def _h():\n    global filter\n    import json as filter\nfor _x in map(filter, ()):\n"
                 "    _x.loads = None"),
        ("json", "class _Patch:\n    json.loads = None\n    json = None"),
        ("json", "class _P:\n    import json as _x\n    [0 for _j in (_x,) for _j.loads in [None]]"),
        ("json", "def _unrelated(json=[0 for json.loads in [None]]):\n    pass"),
        ("json", "import json as _j\n[(_j := _j) for _q in [0] for _j.loads in [None]]"),
        ("json", "import json as _cfg\ndef _outer():\n    _cfg = None\n    class _C:\n        global _escaped\n"
                 "        for _escaped in (_cfg,):\n            pass\n        _cfg = None\n_outer()\n"
                 "_escaped.loads = None"),
        ("json", "import json as _j\ndef _setup():\n    _j = None\n    class _H:\n        global _cfg\n"
                 "        for _cfg in (_j,):\n            pass\n        _j = 2\n_cfg.loads = None"),
        ("json", "import json as _j\ndef _setup():\n    _j = None\n    class _A:\n        class _B:\n"
                 "            global _cfg\n            for _cfg in (_j,):\n                pass\n            _j = 2\n"
                 "_cfg.loads = None"),
        ("json", "_j = dict()\ndef _setup():\n    import json as _j\n    class _A:\n        _j = None\n"
                 "        class _B:\n            global _cfg\n            for _cfg in (_j,):\n                pass\n"
                 "_cfg.loads = None"),
    )
    # Ordinary module-level code that H12 must not flag, in either file, before the region and after it: a
    # store to an item of sys.path or os.environ, to an item of a module-level dict, to a class attribute, and
    # through a for target whose iterable names no region name; then QA round 8's false positives, each one
    # both reviewers listed: a for or match target over a call to a builtin the region reads (list, dict,
    # sorted, len as a keyword argument), and a function-local loop target or import, with no global
    # declaration (a nested def's own declaration does not count), whose name module-level code then binds
    # and stores through; then QA round 9's: a class-local import and for target (the reviewer's
    # reproductions), a comprehension's target and a class body's loop target, each later bound and stored
    # through at module scope, a comprehension target named like a region name, a nonlocal name (an
    # assignment and an import, in a def and in a class body), a builtin passed as a positional argument
    # (map(list, ...), map(dict, ...), filter(len, ...)), a target over sys.path and over a slice of an alias
    # of sys.path, and a comprehension's later iterable in a class body, which skips the class's own names; then
    # QA round 10's: a class body in a def, the class's own scope declaring a for target global, whose iterable
    # names a name the class binds after the loop and the def imports, so the read finds the module's dict,
    # never the def's import (the reviewers' reproductions).
    ordinary = (
        "sys.path[0] = os.path.dirname(os.path.abspath(__file__))",
        'os.environ["AIQT_CHAR_POLICY_EXAMPLE"] = "1"',
        '_settings = {"mode": "strict", "level": 0}\n_settings["mode"] = "lenient"\n_settings["level"] += 1\n'
        'del _settings["mode"]',
        "class _Options:\n    pass\n_Options.verbose = True",
        "_rows = [[0], [1]]\nfor _row in _rows:\n    _row[0] = 2",
        "for _row in list([['a']]):\n    _row[0] = 'b'",
        "match dict(mode='strict'):\n    case _settings:\n        _settings['mode'] = 'lenient'",
        "def _helper():\n    for _options in (validate_policy,):\n        pass\nclass _options:\n    pass\n"
        "_options.verbose = True",
        "_OPTS = []\nfor _opt in sorted(_OPTS):\n    _opt.verbose = True",
        "_rows = [[0], [1]]\nfor _row in list(_rows):\n    _row[0] = 2",
        "_cfg = {'a': {}}\nfor _k, _v in dict(_cfg).items():\n    _v['b'] = 1",
        "_cfg = {'a': {}}\nmatch dict(_cfg):\n    case {'a': _v}:\n        _v['b'] = 1",
        "def _f():\n    for _opts in sorted([]):\n        pass\n_opts = type('O', (), {})\n_opts.verbose = True",
        "def _helper():\n    import json as _cfg\n_cfg = {}\n_cfg['mode'] = 1",
        "def _helper():\n    import __main__ as _o\nclass _o:\n    pass\n_o.verbose = True",
        "_items = [[0]]\nfor _item in sorted(_items, key=len):\n    _item[0] = 1",
        "def _outer():\n    def _inner():\n        global _cfg\n    import json as _cfg\n_cfg = {}\n_cfg['mode'] = 1",
        "class _Holder:\n    import json as _cfg\n_cfg = {}\n_cfg['mode'] = 1",
        "class _Holder:\n    for _cfg in (json,):\n        pass\n_cfg = {}\n_cfg['mode'] = 1",
        "[0 for _cfg in (json,)]\n_cfg = {}\n_cfg['mode'] = 1",
        "class _Holder:\n    for _m in (sys.modules[__name__],):\n        pass\n_m = type('O', (), {})\n"
        "_m.validate_policy = None",
        "[0 for _TOP_KEYS in ()]",
        "def _outer():\n    POLICY_CAP = 0\n    def _inner():\n        nonlocal POLICY_CAP\n        POLICY_CAP = 1",
        "def _outer():\n    _cfg = None\n    def _inner():\n        nonlocal _cfg\n        import json as _cfg\n"
        "_cfg = {}\n_cfg['mode'] = 1",
        "def _outer():\n    _cfg = None\n    class _H:\n        nonlocal _cfg\n        import json as _cfg\n"
        "_cfg = {}\n_cfg['mode'] = 1",
        "_rows = [[0]]\nfor _row in map(list, _rows):\n    _row[0] = 1",
        "_pairs = [[('a', 0)]]\nfor _row in map(dict, _pairs):\n    _row['a'] = 1",
        "_rows = [[0]]\nfor _row in filter(len, _rows):\n    _row[0] = 1",
        "for _p in (sys.path,):\n    _p[0] = 1",
        "from sys import path as _sp\nfor _first in (_sp[0:1],):\n    _first[0] = 1",
        "class _P:\n    import json as _x\n    [0 for _i in (0,) for _j in (_x,) for _j.y in [1]]",
        "_cfg = {}\ndef _outer():\n    import json as _cfg\n    class _C:\n        global _escaped\n"
        "        for _escaped in (_cfg,):\n            pass\n        _cfg = None\n_outer()\n_escaped['mode'] = 1",
        "_j = dict()\ndef _setup():\n    import json as _j\n    class _H:\n        global _cfg\n"
        "        for _cfg in (_j,):\n            pass\n        _j = 2\n_cfg = dict()\n_cfg['mode'] = 1",
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
            # scope, no module-level statement outside it stores to an attribute or item of one of those names, of
            # json, builtins, sys.modules or this module, or of an alias of one of them (see rebound()),
            # or (conservatively) through a target chain that starts from an expression or has a link named sys,
            # json, builtins or modules; each vector gives exactly its findings, and each ordinary statement none,
            # before either BEGIN marker and after either END marker. This catches accidental drift. It is not a
            # defence against a deliberate edit that replaces behaviour through a path this static walk does not
            # model (see the module docstring); diff review is the control for that.
            for path, text in texts.items():
                self.assertEqual(rebound(text), set(), (path, "a name of the copied region is bound outside it"))
                for marker in ("BEGIN", "END"):
                    for name, code in rebindings:
                        want = {name} if isinstance(name, str) else set(name)
                        self.assertEqual(rebound(beside(text, code, marker)), want, (path, marker, code))
                    for code in ordinary:
                        self.assertEqual(rebound(beside(text, code, marker)), set(), (path, marker, code))
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
            # process and through the hook as launched, while the same call as UTF-8 JSON is evaluated. QA round
            # 6: a payload with a key twice in one object was judged on the last value (allowed silently or
            # denied); it now gets the same note, whichever value comes last
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
                   "-Infinity": good[:-1] + b', "extra": -Infinity}', "trailing junk": good + b"x",
                   "duplicate key, the last value clean": good.replace(
                       b'"content": "safe"', b'"content": "\\u2014", "content": "safe"'),
                   "duplicate key, the last value a policy character": good.replace(
                       b'"content": "safe"', b'"content": "safe", "content": "\\u2014"'),
                   "duplicate top-level key": good[:-1] + b', "tool_name": "Write"}'}
            self.assertTrue(all(raw != good for raw in bad.values()))

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
                note = self.is_unchecked(json.loads(p.stdout), "cannot be read as JSON in UTF-8")
                if label.startswith("duplicate"):
                    self.assertIn("duplicate key", note, label)
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
            # an argument list that is not a non-empty list of strings: a note when armed, without reading the
            # payload; silence when not armed
            for argv in (None, [], (), ("char-policy-write.py",), "char-policy-write.py", ["char-policy-write.py", 1],
                         [b"char-policy-write.py"]):
                out = io.StringIO()
                with mock.patch.dict(os.environ, {ROOT_VAR: self.root}, clear=True), \
                        mock.patch.dict(globals(), {"_read_payload": boom}), redirect_stdout(out):
                    rc = main(argv)
                self.assertEqual(rc, 0, argv)
                self.is_unchecked(json.loads(out.getvalue()), "not a non-empty list of strings")
                for env in ({}, {ROOT_VAR: ""}):
                    out = io.StringIO()
                    with mock.patch.dict(os.environ, env, clear=True), redirect_stdout(out):
                        self.assertEqual((main(argv), out.getvalue()), (0, ""), (argv, env))
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
