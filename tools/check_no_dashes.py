#!/usr/bin/env python3
"""Fail on the characters a repository's character policy forbids, in the files that policy scopes.

The policy is data, .aiqt/char-policy.json at the repository root: the characters (each one code point, with
a name), optional advice, and the scope, an ordered list of entries. A `tree` entry scans the files under a
repo-relative directory whose suffix is listed, pruning any directory or file whose name is in that entry's
own `skip` list; a `file` entry scans one repo-relative file when it is present. The preview hook
.preview/char-policy-write.py reads the same file and applies the same scope before a write lands.

When the file is absent, DEFAULT_POLICY below applies: this project's writing style, which forbids en and em
dashes (hyphens, commas, colons, semicolons and parentheses are the sanctioned substitutes) in Markdown, the
standards crosswalk manifests, the shipped hook files, and the NOTICE pair. Deleting the file therefore never
weakens the gate here, and an adopter who ships no policy file gets exactly that scan. A policy file that is
present but malformed, unreadable, a symbolic link, or over POLICY_CAP bytes is a cannot-evaluate (exit 2),
never a fallback to the default. A policy character that str.splitlines() treats as a line boundary (U+2028 and
U+2029; the others are control characters) makes the policy malformed, because the line scan would drop it
before the check, and so does a lone surrogate (U+D800 to U+DFFF) in any policy string, which UTF-8 cannot
encode. The region between the BEGIN COPY SOURCE and END COPY SOURCE markers is the policy validator with every
module-level name it reads; tools/gen_char_policy.py writes it byte for byte into the preview hook, and its
--check fails on any byte difference. The built-in default is the same policy as this repository's file;
--default-parity checks that, for this repository's CI only. This Python file is in no scope entry, so it may
name the characters in its own source without flagging itself; it writes them only as escapes.

  check_no_dashes.py                   scan the repository under its policy
  check_no_dashes.py --self-test       fixture trees for the scan and the policy validator
  check_no_dashes.py --default-parity  this repository only: DEFAULT_POLICY equals .aiqt/char-policy.json

Exit: 0 clean; 1 a finding; 2 cannot evaluate (an unreadable tree, a malformed policy, a bad argument). A file
that is not valid UTF-8 is reported as SKIP and not scanned. --default-parity exits 0 when the two are equal,
and also, saying SKIP, when the file is absent; 1 when they differ; 2 when the file cannot be used. An adopter's
own policy differs from this repository's by design, so only this repository's check roster runs that mode;
the self-test never reads the repository's policy file.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: check_no_dashes.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import os
import stat
from pathlib import Path

# The standard library's json, imported before this folder goes on sys.path below, so the copied region's own
# `import json` finds it in sys.modules and a json.py in this folder cannot replace it (self-test G15). That holds
# under python3 -I, as CI runs this gate; without -I, Python puts a script's own folder first on sys.path at
# startup, so there a module in this folder named like a standard library module that is neither built in, frozen,
# nor already imported at startup (json is one; sys, os and time are not) shadows that module.
import json as _stdlib_json  # noqa: E402,F401

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)
from _standards import dir_present  # noqa: E402  fail-closed absence probe (raises on an unreadable parent)

POLICY_PATH = ".aiqt/char-policy.json"
_SKIP = [".git", "node_modules", "__pycache__"]
# The policy that applies when no policy file is present: this project's own, the same as its data file.
DEFAULT_POLICY = {
    "version": 1,
    "id": "no-dashes",
    "chars": {"\u2013": "en dash", "\u2014": "em dash"},
    "advice": "use hyphens, commas, colons, semicolons, or parentheses",
    "scope": [
        {"tree": ".", "suffixes": [".md", ".mdc"], "skip": _SKIP},
        {"tree": ".aiqt/standards", "suffixes": [".toml"]},
        {"tree": "plugin", "suffixes": [".py", ".json", ".toml"], "skip": _SKIP},
        {"tree": ".aiqt/core/hooks", "suffixes": [".py", ".json", ".toml"], "skip": _SKIP},
        {"file": "NOTICE"},
        {"file": ".aiqt/attribution.toml"},
    ],
}
# --- BEGIN COPY SOURCE: tools/gen_char_policy.py writes this region into .preview/char-policy-write.py ---
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
# source or a store's chain counts as the binding its scope reads, its own or else the nearest enclosing def's or
# the module's; in a class body, which can read a name before binding it, conservatively both. Every other aliasing
# form is out of scope; diff review is the control. Ordinary stores are not flagged: sys.path[0] = ..., an item of
# os.environ or of a module-level dict under a key not named sys, json, builtins or modules, a store to an attribute
# not so named of any other name (_Options.verbose = True), and a store whose chain has no link so named and starts
# from a target whose source is none of those (for _row in sorted(_rows, key=len): _row[0] = 2). That walk catches
# accidental drift between the two copies; it is not a defence against a deliberate edit that replaces behaviour
# through a path the walk does not model. Examples, not a complete list: an alias made by plain assignment or a
# walrus (m = sys.modules[__name__], then m.validate_policy = ...) or by a target over a call's result (for m in
# (importlib.import_module("__main__"),): ...); an item key that is not a constant string; a call such as setattr or
# exec; an in-place method call such as globals().update, sys.modules.update or json.__dict__.update; a store in a
# function or lambda body; reflective access through an object the walk cannot name; another module patching the
# file; and a module that shadows a standard library module, such as a json.py. The recorded hashes
# (.preview/SHA256SUMS for the hook, .aiqt/manifest.toml for both files) let an installer or a release check detect
# a shipped copy that differs from the reviewed one.
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
# --- END COPY SOURCE ---


def load_policy(root):
    """The policy for root: DEFAULT_POLICY when the policy file is absent, else the validated file. Raises
    PolicyError for a malformed or unsafe file (a symbolic link, not a regular file, over the cap) and OSError
    for one that cannot be read."""
    path = os.path.join(root, *POLICY_PATH.split("/"))
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return DEFAULT_POLICY
    if not stat.S_ISREG(st.st_mode):
        raise PolicyError("the policy file is not a regular file (a symbolic link or another kind)")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise PolicyError("the policy file is not a regular file")
        raw = b""
        while len(raw) <= POLICY_CAP:
            chunk = os.read(fd, POLICY_CAP + 1 - len(raw))
            if not chunk:
                break
            raw += chunk
    finally:
        os.close(fd)
    return parse_policy(raw)


def scope_paths(root, policy):
    """The files the policy scopes under root, in scope-entry order, each entry's files sorted, a path listed
    by an earlier entry kept there only. Raises OSError on a tree that cannot be walked or a file entry that
    cannot be examined (fail-closed); an absent tree or file is skipped."""
    root = Path(root)
    paths, seen = [], set()
    for entry in policy["scope"]:
        if "file" in entry:
            candidate = root.joinpath(*entry["file"].split("/"))
            # os.stat raises EACCES on an unreadable file (unlike exists()/is_file(), which swallow it), so a
            # present-but-unreadable file surfaces as exit 2; absent is skipped.
            try:
                os.stat(candidate)
            except FileNotFoundError:
                continue
            found = [candidate]
        else:
            tree = entry["tree"]
            base = root if tree == "." else root.joinpath(*tree.split("/"))
            # dir_present raises on an unreadable parent and walk_files on an unlistable subtree (exit 2).
            if tree != "." and not dir_present(base):
                continue
            found = sorted(walk_files(base, frozenset(entry.get("skip", ())), suffixes=set(entry["suffixes"])))
        for path in found:
            if path not in seen:
                seen.add(path)
                paths.append(path)
    return paths


def run(root):
    """Scan root under its policy; print the result and return the exit status."""
    root = Path(root)
    try:
        policy = load_policy(root)
    except (PolicyError, OSError) as exc:
        print(f"error: the character policy {POLICY_PATH} cannot be used ({exc}); fail-closed", file=sys.stderr)
        return 2
    chars = list(policy["chars"].items())
    findings = []
    try:
        for path in scope_paths(root, policy):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                print(f"SKIP (not utf-8): {path.relative_to(root)}")
                continue
            for number, line in enumerate(lines, 1):
                for char, name in chars:
                    if char in line:
                        column = line.index(char) + 1
                        findings.append(f"{path.relative_to(root)}:{number}:{column}: {name}")
    except OSError as exc:
        print(f"error: cannot scan the tree ({exc}); fail-closed", file=sys.stderr)
        return 2
    if findings:
        print(f'FAIL: {len(findings)} character(s) forbidden by the "{policy["id"]}" character policy found')
        for finding in findings:
            print(f"  {finding}")
        return 1
    names = " or ".join(name for _, name in chars)
    print(f'PASS: no {names} in the files the "{policy["id"]}" character policy scopes')
    return 0


def default_parity(root):
    """This repository's own check, run by its CI roster and not by the self-test: DEFAULT_POLICY equals the
    policy file under root, value for value. Prints the result and returns the exit status: 0 equal, or SKIP
    when the file is absent; 1 when they differ; 2 when the file cannot be used."""
    try:
        policy = load_policy(Path(root))
    except (PolicyError, OSError) as exc:
        print(f"error: the character policy {POLICY_PATH} cannot be used ({exc}); fail-closed", file=sys.stderr)
        return 2
    if policy is DEFAULT_POLICY:
        print(f"SKIP: {POLICY_PATH} is absent, so the built-in default applies and there is nothing to compare")
        return 0
    if policy != DEFAULT_POLICY:
        print(f"FAIL: DEFAULT_POLICY in tools/check_no_dashes.py differs from {POLICY_PATH}; make them the same "
              "policy")
        return 1
    print(f"PASS: DEFAULT_POLICY equals {POLICY_PATH}")
    return 0


def _self_test():
    """Fixture trees for each scan and validation clause; each pair differs in one feature."""
    import io
    import shutil
    import subprocess
    import tempfile
    from contextlib import redirect_stderr, redirect_stdout

    em, en, quote = "\u2014", "\u2013", "\u201c"
    failures = []
    scratch = tempfile.mkdtemp(prefix="check_no_dashes_selftest_")

    def tree(files, policy=None, raw_policy=None):
        root = tempfile.mkdtemp(dir=scratch)
        for rel, data in files.items():
            path = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(data.encode("utf-8") if isinstance(data, str) else data)
        if policy is not None:
            raw_policy = json.dumps(policy).encode("ascii")
        if raw_policy is not None:
            os.makedirs(os.path.join(root, ".aiqt"), exist_ok=True)
            with open(os.path.join(root, ".aiqt", "char-policy.json"), "wb") as handle:
                handle.write(raw_policy)
        return root

    def scan(root, check=run):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                rc = check(root)
            except Exception as exc:  # a traceback is never an exit status: record it as one more failure
                rc = f"an exception ({type(exc).__name__}: {exc!r})"
        return rc, out.getvalue(), err.getvalue()

    def expect(label, root, want, needle=None, check=run):
        rc, out, err = scan(root, check)
        if rc != want or (needle is not None and needle not in out + err):
            failures.append(f"{label}: want exit {want}" + (f" and {needle!r}" if needle else "")
                            + f", got {rc}: {out.strip()[:200]!r}")

    def policy(**changes):
        data = {"version": 1, "id": "fixture", "chars": {quote: "left double quotation mark"},
                "scope": [{"tree": ".", "suffixes": [".md"]}]}
        data.update(changes)
        return data

    try:
        try:
            validate_policy(DEFAULT_POLICY)
        except PolicyError as exc:
            failures.append(f"DEFAULT_POLICY is invalid: {exc}")
        # G0 _plain refuses exactly the C0, DEL, C1 and lone-surrogate code points, and accepts every other one.
        refused = [c for c in range(0x110000) if not _plain(chr(c))]
        if refused != [*range(32), *range(127, 160), *range(0xD800, 0xE000)]:
            failures.append(f"G0 _plain refuses {len(refused)} code points, not exactly C0, DEL, C1 and the "
                            "surrogates")
        # G0 LINE_BOUNDARIES is exactly the set str.splitlines() splits on, and each one is rejected.
        splitting = {chr(c) for c in range(0x110000) if len(f"a{chr(c)}b".splitlines()) == 2}
        if splitting != LINE_BOUNDARIES:
            failures.append(f"G0 LINE_BOUNDARIES {sorted(map(ord, LINE_BOUNDARIES))} differs from what "
                            f"splitlines splits on {sorted(map(ord, splitting))}")
        for boundary in sorted(LINE_BOUNDARIES):
            try:
                validate_policy(policy(chars={boundary: "line boundary"}))
                failures.append(f"G0 line boundary U+{ord(boundary):04X} accepted as a policy character")
            except PolicyError:
                pass
        # G1 character: no policy file, so the default applies.
        expect("G1A em dash in Markdown", tree({"d/a.md": f"x{em}y\n"}), 1, "d/a.md:1:2: em dash")
        expect("G1B hyphen in Markdown", tree({"d/a.md": "x-y\n"}), 0)
        expect("G1C en dash in Markdown", tree({"d/a.md": f"x{en}y\n"}), 1, "d/a.md:1:2: en dash")
        # G1D line numbers are str.splitlines() lines: a form feed or U+2028 in the file ends a line.
        expect("G1D line numbering", tree({"d/a.md": f"x\x0cy\u2028z{em}\n"}), 1, "d/a.md:3:2: em dash")
        # G2 adopter set: the policy's characters replace the default ones.
        expect("G2A adopter character", tree({"d/a.md": f"{quote}x\n"}, policy()), 1,
               "d/a.md:1:1: left double quotation mark")
        expect("G2B default character under an adopter set", tree({"d/a.md": f"x{em}\n"}, policy()), 0)
        # G3 suffix.
        expect("G3A listed suffix", tree({"d/a.md": em}), 1)
        expect("G3B unlisted suffix", tree({"d/a.txt": em}), 0)
        # G4 per-entry skip, for a directory and for a file name.
        expect("G4A skipped directory", tree({"t/node_modules/a.md": quote}, policy(
            scope=[{"tree": "t", "suffixes": [".md"], "skip": ["node_modules"]}])), 0)
        expect("G4B no skip list", tree({"t/node_modules/a.md": quote}, policy(
            scope=[{"tree": "t", "suffixes": [".md"]}])), 1)
        expect("G4C skipped file name", tree({"t/node_modules.md": quote}, policy(
            scope=[{"tree": "t", "suffixes": [".md"], "skip": ["node_modules.md"]}])), 0)
        # G5 location: a tree entry covers its own tree only.
        expect("G5A plugin source", tree({"plugin/x.py": em}), 1)
        expect("G5B tools source", tree({"tools/x.py": em}), 0)
        expect("G5C standards manifest", tree({".aiqt/standards/x.toml": em}), 1)
        expect("G5D root toml", tree({"x.toml": em}), 0)
        # G6 file entry.
        expect("G6A NOTICE present", tree({"NOTICE": em}), 1, "NOTICE:1:1: em dash")
        expect("G6B NOTICE absent", tree({"README.md": "x\n"}), 0)
        # G7 policy validity: a present file is used, and a malformed one is exit 2, never the default. Each
        # fixture is otherwise valid; validate_policy itself must raise PolicyError naming the fault, and the
        # scan must exit 2 with that diagnostic, so a fault that a later step happens to reject (a "." file
        # entry would make the scan read a directory) cannot pass for a validation failure.
        one_code_point, file_entry = "must be exactly one code point", "a file entry holds one repo-relative path"
        tree_path, tree_keys = "tree must be a repo-relative path", "a tree entry holds tree, suffixes"
        name_fault, bad_suffix, bad_skip = ("chars name for U+201C must be a non-empty string",
                                            "suffixes holds an invalid entry", "skip holds an invalid entry")
        bad = {
            "two-code-point key": (policy(chars={quote + quote: "pair"}), one_code_point),
            "version 2": (policy(version=2), "version must be 1"),
            "version true": (policy(version=True), "version must be 1"),
            "unknown key": (policy(extra=1), "unknown key(s) ['extra']"),
            "missing scope": ({"version": 1, "id": "x", "chars": {quote: "q"}}, "missing key(s) ['scope']"),
            "empty id": (policy(id=""), "id must be a non-empty string"),
            "empty chars": (policy(chars={}), "chars must be a non-empty object"),
            "control character": (policy(chars={"\n": "newline"}), one_code_point),
            "C1 character": (policy(chars={"\x9f": "application program command"}), one_code_point),
            "C1 character in the id": (policy(id="a\x80b"), "id must be a non-empty string"),
            "DEL character": (policy(chars={"\x7f": "delete"}), one_code_point),
            "empty name": (policy(chars={quote: ""}), name_fault),
            "empty advice": (policy(advice=""), "advice must be a non-empty string"),
            "empty scope": (policy(scope=[]), "scope must be a non-empty list"),
            "tree and file": (policy(scope=[{"tree": ".", "file": "NOTICE", "suffixes": [".md"]}]),
                              "exactly one of tree or file"),
            "neither tree nor file": (policy(scope=[{"suffixes": [".md"]}]), "exactly one of tree or file"),
            "dot-dot path": (policy(scope=[{"file": "../NOTICE"}]), file_entry),
            "absolute path": (policy(scope=[{"tree": "/etc", "suffixes": [".md"]}]), tree_path),
            "backslash path": (policy(scope=[{"file": "a\\b"}]), file_entry),
            "dot component": (policy(scope=[{"file": "a/./b"}]), file_entry),
            "empty component": (policy(scope=[{"file": "a//b"}]), file_entry),
            "trailing slash": (policy(scope=[{"tree": "a/", "suffixes": [".md"]}]), tree_path),
            "dot-led tree": (policy(scope=[{"tree": "./a", "suffixes": [".md"]}]), tree_path),
            "dot file entry": (policy(scope=[{"file": "."}]), file_entry),
            "suffix with a slash": (policy(scope=[{"tree": ".", "suffixes": [".m/d"]}]), bad_suffix),
            "dot skip": (policy(scope=[{"tree": ".", "suffixes": [".md"], "skip": ["."]}]), bad_skip),
            "dot-dot skip": (policy(scope=[{"tree": ".", "suffixes": [".md"], "skip": [".."]}]), bad_skip),
            "missing suffixes": (policy(scope=[{"tree": "."}]), tree_keys),
            "suffix without a dot": (policy(scope=[{"tree": ".", "suffixes": ["md"]}]), bad_suffix),
            "empty suffixes": (policy(scope=[{"tree": ".", "suffixes": []}]), "suffixes must not be empty"),
            "skip with a slash": (policy(scope=[{"tree": ".", "suffixes": [".md"], "skip": ["a/b"]}]), bad_skip),
            "extra key on a file entry": (policy(scope=[{"file": "NOTICE", "skip": []}]), file_entry),
            "extra key on a tree entry": (policy(scope=[{"tree": ".", "suffixes": [".md"], "file2": "x"}]),
                                          tree_keys),
            # A lone surrogate cannot be encoded as UTF-8, so the gate could not print it: malformed, exit 2.
            "lone surrogate in a name": (policy(chars={quote: "a\ud800"}), name_fault),
            "lone surrogate in the id": (policy(id="a\udfff"), "id must be a non-empty string"),
            "lone surrogate in the advice": (policy(advice="\udc80"), "advice must be a non-empty string"),
            "lone high surrogate as a character": (policy(chars={"\ud800": "high"}), one_code_point),
            "lone low surrogate as a character": (policy(chars={"\udfff": "low"}), one_code_point),
            "lone surrogate in a path": (policy(scope=[{"file": "a\ud800"}]), file_entry),
        }
        for label, (data, needle) in bad.items():
            try:
                validate_policy(data)
                failures.append(f"G7 {label}: validate_policy accepted it")
            except PolicyError as exc:
                if needle not in str(exc):
                    failures.append(f"G7 {label}: want PolicyError with {needle!r}, got {exc}")
            expect(f"G7 {label}", tree({"d/a.md": quote}, data), 2, needle)
        expect("G7 duplicate key", tree({"d/a.md": quote}, raw_policy=(
            b'{"version": 1, "version": 1, "id": "x", "chars": {"a": "a"}, "scope": [{"file": "N"}]}')), 2,
            "duplicate key 'version'")
        expect("G7 not JSON", tree({"d/a.md": quote}, raw_policy=b"{"), 2, "is not valid UTF-8 JSON")
        # A non-standard constant in an otherwise-valid policy: the needle tells the constant check from the
        # version check that would also reject the parsed float.
        for constant in ("NaN", "Infinity", "-Infinity"):
            expect(f"G7 {constant} constant", tree({"d/a.md": quote}, raw_policy=json.dumps(policy()).replace(
                '"version": 1', f'"version": {constant}').encode("ascii")), 2,
                f"non-standard JSON constant {constant}")
        # U+2028 and U+2029: str.splitlines() would drop them, so the policy is malformed (exit 2).
        for boundary in ("\u2028", "\u2029"):
            expect(f"G7 line boundary U+{ord(boundary):04X}", tree({"d/a.md": f"x{boundary}y\n"}, policy(
                chars={boundary: "separator"})), 2, f"U+{ord(boundary):04X} is a line boundary")
        expect("G7 over the cap", tree({"d/a.md": quote}, raw_policy=json.dumps(policy()).encode("ascii")
                                         + b" " * POLICY_CAP), 2, f"is over {POLICY_CAP} bytes")
        linked = tree({"d/a.md": quote, "elsewhere.json": json.dumps(policy())})
        os.makedirs(os.path.join(linked, ".aiqt"))
        os.symlink(os.path.join(linked, "elsewhere.json"), os.path.join(linked, ".aiqt", "char-policy.json"))
        expect("G7 symbolic link", linked, 2, "a symbolic link or another kind")
        expect("G7 valid file is used", tree({"d/a.md": quote}, policy()), 1)
        expect("G7 valid file with advice and skip", tree({"d/a.md": quote}, policy(
            advice="spell it out", scope=[{"tree": ".", "suffixes": [".md"], "skip": ["x"]}])), 1)
        # G8 encoding: a file that is not UTF-8 is reported and skipped.
        expect("G8A UTF-8", tree({"d/a.md": em.encode("utf-8")}), 1)
        expect("G8B not UTF-8", tree({"d/a.md": em.encode("utf-8") + b"\xff"}), 0, "SKIP (not utf-8): d/a.md")
        # G9 order and duplicates: overlapping entries report a file once, in its first entry's place.
        overlap = policy(scope=[{"tree": "b", "suffixes": [".md"]}, {"tree": ".", "suffixes": [".md"]}])
        rc, out, _ = scan(tree({"a.md": quote, "b/c.md": quote}, overlap))
        if (rc, out.splitlines()[1:]) != (1, ["  b/c.md:1:1: left double quotation mark",
                                              "  a.md:1:1: left double quotation mark"]):
            failures.append(f"G9 order or duplicates: {rc} {out!r}")
        # G10 a scoped tree the walk cannot list fails closed (skipped when running as root, which reads it).
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            locked = tree({"d/a.md": "x\n"})
            os.chmod(os.path.join(locked, "d"), 0)
            try:
                expect("G10 unlistable tree", locked, 2)
            finally:
                os.chmod(os.path.join(locked, "d"), 0o700)
            # G11 a present policy file that cannot be read is exit 2, never the default.
            unreadable = tree({"d/a.md": "x\n"}, policy())
            os.chmod(os.path.join(unreadable, ".aiqt", "char-policy.json"), 0)
            expect("G11 unreadable policy file", unreadable, 2, "cannot be used")
            # G12 a file entry whose parent cannot be searched is exit 2, not skipped as absent.
            hidden = tree({"d/NOTICE": em}, policy(chars={em: "em dash"}, scope=[{"file": "d/NOTICE"}]))
            os.chmod(os.path.join(hidden, "d"), 0o600)
            try:
                expect("G12 unsearchable file entry", hidden, 2, "cannot scan the tree")
            finally:
                os.chmod(os.path.join(hidden, "d"), 0o700)
        # G13 arguments: an unknown argument is exit 2 with the usage line, never a scan.
        for argv in (["check_no_dashes.py", "--bogus"], ["check_no_dashes.py", "--self-test", "x"],
                     ["check_no_dashes.py", "--default-parity", "x"]):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = main(argv)
            if rc != 2 or "usage:" not in err.getvalue() or out.getvalue():
                failures.append(f"G13 {argv[1:]}: want exit 2 and usage, got {rc} {out.getvalue()[:120]!r}")
        # G14 --default-parity, on fixture trees only (never this repository's own file, which an adopter's
        # tree does not hold): equal is 0, absent is 0 with SKIP, different is 1, and malformed is 2.
        expect("G14A default parity, equal", tree({}, DEFAULT_POLICY), 0, "PASS: DEFAULT_POLICY equals",
               check=default_parity)
        expect("G14B default parity, absent", tree({}), 0, "SKIP:", check=default_parity)
        expect("G14C default parity, different advice", tree({}, DEFAULT_POLICY | {"advice": "x"}), 1,
               "FAIL: DEFAULT_POLICY", check=default_parity)
        expect("G14D default parity, another policy", tree({}, policy()), 1, "FAIL: DEFAULT_POLICY",
               check=default_parity)
        expect("G14E default parity, malformed", tree({}, raw_policy=b"{"), 2, "cannot be used",
               check=default_parity)
        # G15 a json.py beside the gate does not replace the standard library's json for it: a copy of the gate,
        # run as CI runs it (python3 -I), with a json.py beside it whose loads returns a valid policy whatever it
        # reads, still rejects a policy with an unknown key (exit 2). Without the import of json before the
        # sys.path insert, the shadow is imported and the copy exits 0.
        shadowed = tree({"d/a.md": quote}, raw_policy=json.dumps(policy(metadata=1)).encode("ascii"))
        copy = os.path.join(shadowed, "tools")
        os.makedirs(copy)
        here = Path(__file__).resolve().parent
        for name in ("check_no_dashes.py", "_walk.py", "_standards.py"):
            shutil.copyfile(here / name, os.path.join(copy, name))
        with open(os.path.join(copy, "json.py"), "w", encoding="ascii") as handle:
            handle.write("def loads(*args, **kwargs):\n"
                         "    return {'version': 1, 'id': 'shadow', 'chars': {'x': 'x'}, 'scope': [{'file': 'N'}]}\n")
        done = subprocess.run([sys.executable, "-I", "-B", os.path.join(copy, "check_no_dashes.py")],
                              capture_output=True, text=True, timeout=120)
        if done.returncode != 2 or "unknown key(s) ['metadata']" not in done.stderr:
            failures.append(f"G15 shadowing json.py: want exit 2 and the unknown key, got {done.returncode}: "
                            f"{(done.stdout + done.stderr).strip()[:200]!r}")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    for failure in failures:
        print(f"FAIL: {failure}")
    if failures:
        print(f"SELF-TEST FAILED: {len(failures)} vector(s)")
        return 1
    print("SELF-TEST PASS: check_no_dashes.py scan and policy vectors")
    return 0


def main(argv):
    if argv[1:] == ["--self-test"]:
        return _self_test()
    if argv[1:] == ["--default-parity"]:
        return default_parity(Path(__file__).resolve().parents[1])
    if argv[1:]:
        print("usage: check_no_dashes.py [--self-test | --default-parity]", file=sys.stderr)
        return 2
    return run(Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
