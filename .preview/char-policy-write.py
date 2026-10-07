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
    0; the decision travels in the JSON. The verdict is deny, a note or silence: this hook never asks.

CONFIGURATION
    AIQT_CHAR_POLICY_ROOT holds one absolute path, the repository root whose policy applies. There is no
    default and no older spelling: unset, empty or relative, the hook does nothing. The policy file is
    <root>/.aiqt/char-policy.json; absent, the hook does nothing (the gate then applies its built-in default
    policy, which this hook does not copy).

POLICY FILE
    {"version": 1, "id": <name>, "chars": {<one code point>: <name>, ...}, "advice": <optional text>,
     "scope": [{"tree": <dir>, "suffixes": [".md", ...], "skip": [<name>, ...]} | {"file": <path>}, ...]}
    Paths are repo-relative POSIX paths ("." alone is the root as a tree). A tree entry scopes a file under
    the tree whose suffix (the text from the final dot of its name, case-sensitive) is listed, unless a
    directory or the file below the tree has a name in that entry's skip list; a file entry scopes that one
    file. Unknown keys and any other fault make the file malformed. The loader and validator here are a copy
    of the gate's; the self-test checks they agree, and that this hook's scope test agrees with the gate's
    walk (H12).

DECISION
    - Not Write, Edit or MultiEdit, or the payload lacks tool_input or a string file_path, or the path holds a
      control character: allow, silently (the channel's fail-open contract).
    - A relative file_path is joined onto the payload's cwd (absent or relative cwd: allow). The target's and
      the root's real paths are compared whole component by component; a target outside the root, or not in
      the policy's scope, is allowed.
    - A policy file that is malformed, unreadable, a symbolic link, not a regular file, or over 65536 bytes:
      allow with a note naming the gate, which exits 2 on the same file, so CI still fails closed.
    - For each policy character c, a write ADDS c when:
        Edit       new_string holds more c than old_string (with replace_all both counts scale alike);
        MultiEdit  any one edit's new_string holds more c than its old_string;
        Write      content holds more c than the existing file. An absent file counts 0, and so does an
                   existing file that is not valid UTF-8 (the gate skips that file today, but would scan the
                   new UTF-8 content). The existing file is read only when it is a regular file of at most
                   4 MiB; otherwise the write is allowed with a note.
      Any added character: deny. The reason names each added character as U+XXXX with its policy name, the
      first position in the new text, and the policy's advice; every policy character in the reason (in the
      path, the policy id or the advice) is written as U+XXXX too, so the reason never carries one.
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
      each edit is judged alone. It never misses a net increase: a code point cannot be formed by joining
      strings that lack it, so the net change is the sum of each edit's own change times its match count.
    - Path aliases: a hard link, a case-insensitive or Unicode-normalizing filesystem, or a symbolic link
      inside the root (the gate's walk does not follow linked directories; this hook resolves real paths)
      can make the hook's scope test differ from the gate's walk.
    - A malformed policy fails open here and closed at the gate.
    - A reviewed edit to the policy file narrows the gate and the hook together; diff review is the only
      control over it.
    - The repository's other character checks with their own fixed sets and scopes are not read.
    - The file can change between this hook's decision and the write (a race).
    - A payload over 64 MiB, or one that is not a JSON object, is allowed silently.

SELF-TEST
    --self-test runs the vectors H1 to H16 below. The parity test H12 imports the sibling gate
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
    could not be read), and every control character, written as U+XXXX."""
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
    if not isinstance(root, str) or not root or not os.path.isabs(root) or not _plain(root):
        return None
    if not isinstance(payload, dict) or payload.get("tool_name") not in TOOLS:
        return None
    tool, tool_input = payload["tool_name"], payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    file_path = tool_input.get("file_path")
    if not _plain(file_path):
        return None
    if not os.path.isabs(file_path):
        cwd = payload.get("cwd")
        if not _plain(cwd) or not os.path.isabs(cwd):
            return None
        file_path = os.path.join(cwd, file_path)
    try:
        root_real = os.path.realpath(root)
        target = os.path.realpath(file_path)
        if target == root_real or os.path.commonpath([target, root_real]) != root_real:
            return None
    except (OSError, ValueError):
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
            return None
        added = _added(chars, old, new)
        if added:
            line, column = _position(new, added)
            return _deny(policy, tool, rel, added, f"line {line}, column {column} of the new text")
        return None
    if tool == "MultiEdit":
        edits = tool_input.get("edits")
        if not isinstance(edits, list) or not edits:
            return None
        pairs = []
        for edit in edits:
            if not isinstance(edit, dict):
                return None
            old, new = edit.get("old_string"), edit.get("new_string")
            if not isinstance(old, str) or not isinstance(new, str):
                return None
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
        return None
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
    try:
        out = _decide(_read_payload(), os.environ)
    except Exception:
        return 0  # malformed or unreadable input, or an internal error: fail open
    if out is not None:
        _emit_line(json.dumps(out))
    return 0


def _self_test():
    import importlib.util
    import shutil
    import subprocess
    import tempfile
    import unittest

    here = os.path.abspath(__file__)
    em, en, quote, hyphen = "\u2014", "\u2013", "\u201c", "-"
    dashes = {"version": 1, "id": "no-dashes", "chars": {en: "en dash", em: "em dash"},
              "advice": "use hyphens, commas, colons, semicolons, or parentheses",
              "scope": [{"tree": ".", "suffixes": [".md"], "skip": [".git", "node_modules"]},
                        {"tree": "plugin", "suffixes": [".py"]}, {"file": "NOTICE"}]}
    quotes = {"version": 1, "id": "no-curly-quotes", "chars": {quote: "left double quotation mark"},
              "scope": [{"tree": ".", "suffixes": [".md"]}]}

    def sibling_gate():
        """The sibling gate module, or None (skip) when it is absent and siblings are not required."""
        path = os.path.join(os.path.dirname(os.path.dirname(here)), *GATE.split("/"))
        if not os.path.isfile(path):
            if os.environ.get("AIQT_HOOKS_REQUIRE_SIBLINGS") == "1":
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
            # a relative root is off, even one that resolves to the fixture root from here
            for env in ({}, {ROOT_VAR: ""}, {ROOT_VAR: os.path.relpath(self.root)}):
                self.assertIsNone(_decide(payload, env), env)
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
            for junk in (b"", b"not json", b"[1]", b'{"tool_name": "Write"}'):
                p = subprocess.run([sys.executable, "-I", "-S", "-B", here], input=junk, capture_output=True,
                                   env={"LC_ALL": "C", ROOT_VAR: self.root}, timeout=60)
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
            raws = [json.dumps(p).encode("ascii") for p in (
                dashes, quotes, gate.DEFAULT_POLICY, dashes | {"version": 2}, dashes | {"extra": 1},
                dashes | {"chars": {em + em: "x"}}, dashes | {"chars": {}}, dashes | {"id": ""},
                dashes | {"advice": ""}, dashes | {"scope": []}, dashes | {"scope": [{"file": "../x"}]},
                dashes | {"scope": [{"file": "a\\b"}]}, dashes | {"scope": [{"tree": "/x", "suffixes": [".md"]}]},
                dashes | {"scope": [{"tree": ".", "suffixes": ["md"]}]},
                dashes | {"scope": [{"tree": ".", "suffixes": [".a.b"]}]},
                dashes | {"scope": [{"tree": ".", "suffixes": [".md"], "skip": ["a/b"]}]},
                dashes | {"scope": [{"tree": ".", "file": "x", "suffixes": [".md"]}]},
                dashes | {"scope": [{"file": "x", "skip": []}]}, dashes | {"version": True})]
            raws += [b"{", b"[]", b'{"version": 1, "version": 1}', b"\xff",
                     json.dumps(dashes).encode("ascii") + b" " * POLICY_CAP]
            for raw in raws:
                verdicts = []
                for parse, error in ((parse_policy, PolicyError), (gate.parse_policy, gate.PolicyError)):
                    try:
                        parse(raw)
                        verdicts.append(True)
                    except error:
                        verdicts.append(False)
                self.assertEqual(verdicts[0], verdicts[1], raw[:120])
            files = ["a.md", "a.mdc", "a.txt", "docs/a.md", "docs/node_modules/a.md", "node_modules.md",
                     "docs/__pycache__/a.md", ".git/a.md", "plugin/x.py", "plugin/x.json", "plugin/sub/y.toml",
                     "plugin/node_modules/z.py", ".aiqt/standards/s.toml", ".aiqt/standards/n/s.toml",
                     ".aiqt/core/hooks/h.py", "NOTICE", "NOTICE.md", "docs/NOTICE", ".aiqt/attribution.toml",
                     ".aiqt/char-policy.json", "tools/check.py", "site/a.html", "x.toml", "b/c.md", ".md"]
            for f in files:
                self.put(f, "x\n")
            for policy in (gate.DEFAULT_POLICY, dashes, quotes,
                           quotes | {"scope": [{"tree": "b", "suffixes": [".md"]}, {"tree": ".",
                                                                                  "suffixes": [".md"]}]}):
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
                               {"file_path": self.at("docs/a.md"), "edits": "x"}):
                for tool in TOOLS:
                    self.assertIsNone(_decide({"tool_name": tool, "tool_input": tool_input}, self.env),
                                      (tool, tool_input))
            self.assertIsNone(_decide([], self.env))

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
            self.assertIsNone(_decide(rel, self.env))
            self.assertIsNone(_decide(rel | {"cwd": "relative"}, self.env))
            self.is_deny(self.edit("NOTICE", "x", em))
            self.is_deny(self.edit("plugin/x.py", "x", em))
            self.assertIsNone(self.edit("docs/NOTICE", "x", em))
            self.assertIsNone(self.edit(".aiqt/char-policy.json", "x", em))  # the policy itself is never blocked
            self.assertIsNone(self.edit("", "x", em))

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(T)
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
