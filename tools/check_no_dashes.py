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

  check_no_dashes.py                     scan the repository under its policy
  check_no_dashes.py --self-test         fixture trees for the scan and the policy validator
  check_no_dashes.py --default-parity    this repository only: DEFAULT_POLICY equals .aiqt/char-policy.json
  check_no_dashes.py --staged-scan ROOT  the staged changes of the git working tree whose top level is ROOT

--staged-scan is the mode a commit-time runner calls. It reads the policy from the index (stage 0 of
.aiqt/char-policy.json; absent there, DEFAULT_POLICY), lists the staged changes against HEAD (against the empty
tree on an unborn branch) with renames detected, and reads the staged blob of each added, modified, type-changed
or renamed regular file whose new path the policy scopes (scoped_rel, the same matcher as the preview hook's
_in_scope, which agrees with scope_paths). It counts every occurrence of each policy character, per path and
per character and never netted across either, and reports only what a staged blob adds over its HEAD version
(for a rename, the old path's blob; for a new file or one that was not a regular file, nothing). A staged
deletion is never scanned; a staged symbolic link or submodule in scope is named on a NOTE line and not
scanned. Every git call is bound to ROOT (git -C ROOT, confirmed to be the working tree's top level) and runs
with every ambient GIT_-prefixed variable dropped, as tools/check_portability.py runs its own git reads, so a
GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE or GIT_CONFIG_* in the caller's environment cannot redirect it. Two
consequences are disclosed: under git commit -a or git commit PATH, whose pre-commit hook reads a temporary
index named only by GIT_INDEX_FILE, this mode reads the repository's own index instead; and the git binary is
resolved through PATH. Exit: 0 nothing added; 1 something added; 2 cannot evaluate (ROOT not the top level of
a git working tree, a git call that fails, an unmerged entry, an index policy that is malformed or not a
regular file, a scoped staged blob or its HEAD version that is not valid UTF-8, each named on stderr).

Exit: 0 clean; 1 a finding; 2 cannot evaluate (an unreadable tree, a scoped tree that is there but is not a
directory or a symlink to a directory inside the repository, a scoped file that is not valid UTF-8, a malformed
policy, a bad argument). Each scoped file that is not valid UTF-8 is named on its own line on stderr and the run
exits 2, never 0 and never 1, even when another file holds a finding, whose line is still printed on stdout. One
case prints less: when listing or reading the scoped files raises an OSError (a tree that cannot be walked or is
not a directory, a scoped file that cannot be read), the scan stops at once with one "cannot scan the tree" line
on stderr and exit 2, and it prints no finding and names no file that is not valid UTF-8. --default-parity exits
0 when the two are equal, and also, saying SKIP, when the file is absent; 1 when they differ; 2 when the file
cannot be used. An adopter's own policy differs from this repository's by design, so only this repository's
check roster runs that mode; the self-test never reads the repository's policy file.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_no_dashes.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import os
import stat
import subprocess
from pathlib import Path, PurePosixPath

# The standard library's json, imported before this folder goes on sys.path below, so the copied region's own
# `import json` finds it in sys.modules and a json.py in this folder cannot replace it (self-test G15). That holds
# under python3 -I, as CI runs this gate; without -I, Python puts a script's own folder first on sys.path at
# startup, so there a module in this folder named like a standard library module that is neither built in, frozen,
# nor already imported at startup (json is one; sys, os and time are not) shadows that module.
import json as _stdlib_json  # noqa: E402,F401

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)
from _standards import dir_or_absent, dir_present  # noqa: E402  fail-closed absence probes (raise on an unreadable parent)

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
    cannot be examined, or a tree that is there but cannot be read as a directory (fail-closed); an absent
    tree or file is skipped."""
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
            # dir_or_absent raises on an unreadable parent and walk_files on an unlistable subtree (exit 2); a
            # tree that is there but cannot be read as a directory (a regular file, or a symlink that is
            # dangling, whose target is not a directory or resolves outside root) is exit 2, never skipped. A
            # symlink to a directory inside root is walked. dir_present then raises on such a component above
            # it below root, or an unreadable one (exit 2).
            if tree != ".":
                _present, problem = dir_or_absent(base, root)
                if problem is not None:
                    raise NotADirectoryError(f"the scoped tree {tree} {problem}")
                if not dir_present(base, root=root):
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
    findings, undecodable = [], []
    try:
        for path in scope_paths(root, policy):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                undecodable.append(path.relative_to(root))
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
    # A scoped file that is not valid UTF-8 cannot be scanned, so the run cannot vouch for it: exit 2, which
    # wins over the findings above (exit 1), never a skip and a pass.
    for rel in undecodable:
        print(f"error: {rel} is not valid UTF-8, so it cannot be scanned (cannot evaluate); fail-closed",
              file=sys.stderr)
    if undecodable:
        return 2
    if findings:
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


# --staged-scan (see the module docstring). Every name below is outside the copied region and binds no name the
# region binds or reads.
STAGED_GIT_TIMEOUT = 120  # seconds, per git call
_STAGED_REGULAR_MODES = ("100644", "100755")


class StagedScanError(RuntimeError):
    """The staged changes cannot be read (cannot evaluate, exit 2)."""


def scoped_rel(parts, policy):
    """True when the repo-relative path, given as the list of its components, is one scope_paths would list
    for policy if it were present: a file entry naming exactly that path, or a path below a tree entry whose
    components below the tree hold no skip name and whose last component has a listed suffix. The same matcher
    as the preview hook's _in_scope."""
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


def _staged_shown(rel):
    """rel for one output line: each control character (C0, DEL, C1) and each lone surrogate (a path byte that
    is not UTF-8) written as U+XXXX, so no path can break the line format or the output encoding."""
    return "".join(f"U+{ord(ch):04X}" if ord(ch) < 32 or 127 <= ord(ch) <= 159 or 0xD800 <= ord(ch) <= 0xDFFF
                   else ch for ch in rel)


def _staged_git(root, *args, stdin=b""):
    """Run git bound to root (git -C root) with every ambient GIT_-prefixed variable dropped, the posture
    tools/check_portability.py takes for its own git reads, so GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE or a
    GIT_CONFIG_* variable cannot point the read at another repository, index or configuration. The three
    `-c` pairs in option position switch off automatic gc and maintenance (tools/_git_fixture_env.py
    _NO_AUTO_MAINTENANCE), so no git call here can leave a detached repack behind. Returns the completed process
    (bytes); raises StagedScanError when git cannot be launched or does not finish."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        return subprocess.run(["git", "-C", str(root), "-c", "gc.auto=0", "-c", "gc.autoDetach=false",
                               "-c", "maintenance.auto=false", *args], input=stdin, capture_output=True, env=env,
                              timeout=STAGED_GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise StagedScanError(f"git {args[0]} could not run ({exc})") from None


def _staged_git_ok(root, *args, stdin=b""):
    """_staged_git's stdout; StagedScanError, with git's own message, on a non-zero exit."""
    done = _staged_git(root, *args, stdin=stdin)
    if done.returncode != 0:
        detail = done.stderr.decode("utf-8", "replace").strip()[:300]
        raise StagedScanError(f"git {args[0]} exited {done.returncode} ({_staged_shown(detail)})")
    return done.stdout


def _staged_root(root):
    """Raise StagedScanError unless root is the top level of a git working tree."""
    top = _staged_git_ok(root, "rev-parse", "--show-toplevel").decode("utf-8", "surrogateescape").rstrip("\n")
    try:
        same = top != "" and os.path.samefile(top, root)
    except OSError as exc:
        raise StagedScanError(f"cannot compare {root} with the working tree's top level ({exc})") from None
    if not same:
        raise StagedScanError(f"{root} is not the top level of a git working tree (git names "
                              f"{_staged_shown(top)!r})")


def _staged_base(root):
    """The tree the staged changes are compared with: HEAD's, or the empty tree on an unborn branch (HEAD names
    a branch that does not exist yet), hashed without writing it. Any other failure is StagedScanError."""
    head = _staged_git(root, "rev-parse", "--verify", "--quiet", "HEAD^{tree}")
    if head.returncode == 0:
        return head.stdout.decode("ascii", "replace").strip()
    ref = _staged_git(root, "symbolic-ref", "--quiet", "HEAD")
    name = ref.stdout.decode("utf-8", "surrogateescape").strip()
    if ref.returncode != 0 or not name:
        raise StagedScanError("HEAD does not resolve to a tree and does not name a branch")
    listed = _staged_git_ok(root, "for-each-ref", "--format=%(refname)", name)
    if name in listed.decode("utf-8", "surrogateescape").splitlines():
        raise StagedScanError(f"HEAD names {_staged_shown(name)}, which exists but does not resolve to a tree")
    return _staged_git_ok(root, "hash-object", "-t", "tree", "--stdin").decode("ascii", "replace").strip()


def _staged_blobs(root, oids):
    """{object id: bytes} for each blob in oids, read by id through one git cat-file --batch; StagedScanError
    when one is missing, is not a blob, or the output is malformed."""
    unique = sorted(set(oids))
    if not unique:
        return {}
    out = _staged_git_ok(root, "cat-file", "--batch", stdin="".join(oid + "\n" for oid in unique).encode("ascii"))
    blobs, pos = {}, 0
    for oid in unique:
        end = out.find(b"\n", pos)
        header = out[pos:end].decode("ascii", "replace").split(" ") if end >= 0 else []
        if len(header) != 3 or header[0] != oid or header[1] != "blob" or not header[2].isdigit():
            raise StagedScanError(f"object {oid} is not a readable blob ({' '.join(header)[:120]!r})")
        start = end + 1
        size = int(header[2])
        if out[start + size:start + size + 1] != b"\n":
            raise StagedScanError(f"git cat-file output for {oid} is truncated")
        blobs[oid] = out[start:start + size]
        pos = start + size + 1
    if pos != len(out):
        raise StagedScanError("git cat-file printed more than the objects asked for")
    return blobs


def _staged_policy(root):
    """The policy in the index: stage 0 of POLICY_PATH parsed and validated, or DEFAULT_POLICY when the index
    does not hold it. PolicyError when it is not a regular file or is malformed; StagedScanError when it is
    unmerged or cannot be read."""
    listed = _staged_git_ok(root, "ls-files", "--stage", "-z", "--", ":(literal)" + POLICY_PATH)
    entries = []
    for record in listed.split(b"\0"):
        if not record:
            continue
        meta, _tab, path = record.partition(b"\t")
        fields = meta.decode("ascii", "replace").split(" ")
        if len(fields) != 3:
            raise StagedScanError(f"unexpected git ls-files output {record[:120]!r}")
        entries.append((fields, path.decode("utf-8", "surrogateescape")))
    if not entries:
        return DEFAULT_POLICY
    if any(path != POLICY_PATH for _fields, path in entries):
        raise PolicyError("the policy file in the index is not a regular file (a directory)")
    if len(entries) != 1 or entries[0][0][2] != "0":
        raise StagedScanError(f"{POLICY_PATH} is unmerged in the index")
    mode, oid, _stage = entries[0][0]
    if mode not in _STAGED_REGULAR_MODES:
        raise PolicyError("the policy file in the index is not a regular file (a symbolic link or another kind)")
    return parse_policy(_staged_blobs(root, [oid])[oid])


def _staged_changes(root, base):
    """The staged changes against base, in git's order, as (status letter, old mode, new mode, old object id,
    new object id, new path); a rename's old blob is its baseline. StagedScanError on an unmerged entry or
    output this parser does not know."""
    raw = _staged_git_ok(root, "diff-index", "--cached", "--raw", "-z", "-M", "--no-abbrev", base, "--")
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    changes, index = [], 0
    while index < len(fields):
        head = fields[index]
        meta = head[1:].decode("ascii", "replace").split(" ") if head.startswith(b":") else []
        if len(meta) != 5 or meta[4][:1] not in ("A", "C", "D", "M", "R", "T", "U"):
            raise StagedScanError(f"unexpected git diff-index output {head[:120]!r}")
        old_mode, new_mode, old_oid, new_oid, status = meta
        width = 2 if status[0] in ("R", "C") else 1
        if index + 1 + width > len(fields):
            raise StagedScanError("git diff-index output is truncated")
        path = fields[index + width].decode("utf-8", "surrogateescape")
        index += 1 + width
        if status[0] == "U":
            raise StagedScanError(f"{_staged_shown(path)} is unmerged in the index")
        changes.append((status[0], old_mode, new_mode, old_oid, new_oid, path))
    return changes


def staged_scan(root):
    """Scan the staged changes of the git working tree whose top level is root (see the module docstring);
    print the result and return the exit status."""
    root = Path(root)
    try:
        _staged_root(root)
        policy = _staged_policy(root)
    except PolicyError as exc:
        print(f"error: the character policy {POLICY_PATH} in the index cannot be used ({exc}); fail-closed",
              file=sys.stderr)
        return 2
    except StagedScanError as exc:
        print(f"error: cannot read the staged changes ({exc}); fail-closed", file=sys.stderr)
        return 2
    chars = list(policy["chars"].items())
    try:
        scoped, notes = [], []
        for status, old_mode, new_mode, old_oid, new_oid, path in _staged_changes(root, _staged_base(root)):
            if status == "D" or not scoped_rel(path.split("/"), policy):
                continue
            if new_mode not in _STAGED_REGULAR_MODES:
                notes.append(path)
                continue
            scoped.append((path, new_oid, old_oid if status != "A" and old_mode in _STAGED_REGULAR_MODES else None))
        blobs = _staged_blobs(root, [oid for _path, new_oid, old_oid in scoped for oid in (new_oid, old_oid)
                                     if oid is not None])
    except StagedScanError as exc:
        print(f"error: cannot read the staged changes ({exc}); fail-closed", file=sys.stderr)
        return 2
    findings, undecodable, total = [], [], 0
    for path, new_oid, old_oid in scoped:
        try:
            new = blobs[new_oid].decode("utf-8")
        except UnicodeDecodeError:
            undecodable.append(f"{_staged_shown(path)} is not valid UTF-8 in the index")
            continue
        try:
            old = "" if old_oid is None else blobs[old_oid].decode("utf-8")
        except UnicodeDecodeError:
            undecodable.append(f"{_staged_shown(path)} is not valid UTF-8 in HEAD (its baseline)")
            continue
        for char, name in chars:
            added = new.count(char) - old.count(char)
            if added > 0:
                total += added
                findings.append(f"{_staged_shown(path)}: {name} x{added} added (HEAD {old.count(char)}, "
                                f"staged {new.count(char)})")
    for path in notes:
        print(f"NOTE: {_staged_shown(path)} is staged as a symbolic link or a submodule, not a regular file; "
              "not scanned")
    if findings:
        print(f'FAIL: {total} character(s) forbidden by the "{policy["id"]}" character policy added by the staged '
              "changes")
        for finding in findings:
            print(f"  {finding}")
    for problem in undecodable:
        print(f"error: {problem}, so it cannot be scanned (cannot evaluate); fail-closed", file=sys.stderr)
    if undecodable:
        return 2
    if findings:
        return 1
    names = " or ".join(name for _, name in chars)
    print(f'PASS: the staged changes add no {names} to the files the "{policy["id"]}" character policy scopes')
    return 0


def _staged_vectors(failures, expect, tree, policy, scratch):
    """The --staged-scan self-test vectors (G19 to G32), each on throwaway git repositories. The whole block runs
    inside _git_fixture_env.fixture_git_lifecycle, so neither the fixture git calls nor the scan under test can
    read or change the caller's repository, index or git configuration, and the caller's environment is put
    back afterwards. Each vector that fails without the staged scan (or without the one behaviour it names) is
    paired with a twin that passes either way, so the pair proves the difference."""
    from _git_fixture_env import fixture_git_lifecycle, git_fixture_env

    em, en, quote = "—", "–", "“"
    identity = {"GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}

    def git(root, *args, stdin=b""):
        done = subprocess.run(["git", "-C", root, *args], input=stdin, capture_output=True,
                              env=git_fixture_env(**identity), timeout=120)
        if done.returncode != 0:
            raise RuntimeError(f"fixture git {args[0]} exited {done.returncode}: {done.stderr[:200]!r}")
        return done.stdout

    def write(root, rel, data):
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data.encode("utf-8") if isinstance(data, str) else data)

    def repo(head=None, staged=None, work=None):
        """A repository whose HEAD commit holds head (no commit at all when head is None), whose index then
        takes staged (a None value stages a deletion), and whose working tree then takes work, unstaged."""
        root = tree({})
        git(root, "init", "-q")
        if head is not None:
            for rel, data in head.items():
                write(root, rel, data)
            git(root, "add", "-A")
            git(root, "commit", "-q", "--allow-empty", "-m", "base")
        for rel, data in (staged or {}).items():
            if data is None:
                git(root, "rm", "-q", "--", rel)
            else:
                write(root, rel, data)
                git(root, "add", "--", rel)
        for rel, data in (work or {}).items():
            if data is None:
                os.remove(os.path.join(root, *rel.split("/")))
            else:
                write(root, rel, data)
        return root

    def staged(label, root, want, needle=None):
        expect(label, root, want, needle, check=staged_scan)

    pass_line = 'PASS: the staged changes add no en dash or em dash to the files the "no-dashes" character policy'
    filler = "".join(f"line {number} of a file long enough for rename detection\n" for number in range(30))
    with fixture_git_lifecycle():
        try:
            # G19 (T1) the index, not the working tree: a dash staged but taken out of the working tree is a
            # finding; a dash in the working tree only is not.
            staged("G19A dash staged, working tree clean", repo({"d/a.md": "x\n"}, {"d/a.md": f"x{em}\n"},
                                                                {"d/a.md": "x\n"}), 1, "d/a.md: em dash x1 added")
            staged("G19B dash in the working tree only", repo({"d/a.md": "x\n"}, work={"d/a.md": f"x{em}\n"}),
                   0, pass_line)
            # G20 (T3) the HEAD baseline, counted per occurrence: an edit beside an existing dash adds nothing;
            # a second dash on the same line adds one.
            staged("G20A edit beside a dash already in HEAD", repo({"d/a.md": f"a{em}b\n"},
                                                                   {"d/a.md": f"a{em}b\nmore\n"}), 0, pass_line)
            staged("G20B second dash on the same line", repo({"d/a.md": f"a{em}b\n"}, {"d/a.md": f"a{em}b{em}c\n"}),
                   1, "d/a.md: em dash x1 added (HEAD 1, staged 2)")
            # G21 (T4) per path and per character, never netted: a dash moved to another file, and an en dash
            # replaced by an em dash, are findings; a dash moved within its own file is not.
            staged("G21A dash moved to another file", repo({"d/a.md": f"{em}\n", "d/b.md": "x\n"},
                                                           {"d/a.md": "x\n", "d/b.md": f"{em}\n"}), 1,
                   "d/b.md: em dash x1 added (HEAD 0, staged 1)")
            staged("G21B en dash replaced by an em dash", repo({"d/a.md": f"{en}\n"}, {"d/a.md": f"{em}\n"}), 1,
                   "d/a.md: em dash x1 added")
            staged("G21C dash moved within its file", repo({"d/a.md": f"{em} x\n"}, {"d/a.md": f"x {em}\n"}), 0,
                   pass_line)
            # G22 (T5) a rename is compared with its old blob: a plain git mv adds nothing; a rename that adds a
            # dash adds one.
            moved = repo({"d/a.md": filler + f"{em}{em}\n"})
            git(moved, "mv", "d/a.md", "d/b.md")
            staged("G22A git mv of a file holding dashes", moved, 0, pass_line)
            moved = repo({"d/a.md": filler + f"{em}{em}\n"})
            git(moved, "mv", "d/a.md", "d/b.md")
            write(moved, "d/b.md", filler + f"{em}{em}{em}\n")
            git(moved, "add", "--", "d/b.md")
            staged("G22B rename that adds a dash", moved, 1, "d/b.md: em dash x1 added (HEAD 2, staged 3)")
            # G23 (T6) an unborn branch is compared with the empty tree.
            staged("G23A unborn branch, staged dash", repo(None, {"d/a.md": f"{em}\n"}), 1,
                   "d/a.md: em dash x1 added")
            staged("G23B unborn branch, clean", repo(None, {"d/a.md": "x\n"}), 0, pass_line)
            # G24 (T7) a staged deletion is never scanned.
            staged("G24A staged deletion of a file holding a dash", repo({"d/a.md": f"{em}\n"}, {"d/a.md": None}),
                   0, pass_line)
            # The added file shares no text with the deleted one, so git does not pair them as a rename.
            staged("G24B deletion beside an added dash", repo({"d/a.md": filler + f"{em}\n"},
                                                              {"d/a.md": None, "d/c.md": f"new {em}\n"}), 1,
                   "d/c.md: em dash x1 added")
            # G25 (T11) scope: a dash in an out-of-scope file is not a finding under the default policy.
            staged("G25A dash in out-of-scope tools/x.py", repo({"README.md": "x\n"}, {"tools/x.py": f"{em}\n"}),
                   0, pass_line)
            staged("G25B dash in in-scope plugin/x.py", repo({"README.md": "x\n"}, {"plugin/x.py": f"{em}\n"}), 1,
                   "plugin/x.py: em dash x1 added")
            # G26 (T9) the policy comes from the index: an untracked working-tree policy adding a quotation mark
            # is not used; a committed one is used even when the working-tree file is gone.
            quote_policy = json.dumps(policy()) + "\n"
            staged("G26A working-tree policy only", repo({"d/a.md": "x\n"}, {"d/a.md": f"{quote}\n"},
                                                         {".aiqt/char-policy.json": quote_policy}), 0)
            staged("G26B index policy, working-tree file removed",
                   repo({"d/a.md": "x\n", ".aiqt/char-policy.json": quote_policy}, {"d/a.md": f"{quote}\n"},
                        {".aiqt/char-policy.json": None}), 1, "d/a.md: left double quotation mark x1 added")
            # G27 fail-closed: a scoped staged blob that is not UTF-8, an unmerged entry, a root that is not the
            # top level, a directory outside any repository and a malformed index policy are each exit 2.
            staged("G27A staged blob not UTF-8", repo({"d/a.md": "x\n"}, {"d/a.md": b"x\xff\n"}), 2,
                   "d/a.md is not valid UTF-8 in the index")
            staged("G27B the same text as UTF-8", repo({"d/a.md": "x\n"}, {"d/a.md": b"xy\n"}), 0, pass_line)
            conflicted = repo({"README.md": "x\n"})
            blob = git(conflicted, "hash-object", "-w", "--stdin", stdin=b"x\n").decode("ascii").strip()
            git(conflicted, "update-index", "--index-info",
                stdin="".join(f"100644 {blob} {stage}\td/a.md\n" for stage in (1, 2, 3)).encode("ascii"))
            staged("G27C unmerged entry", conflicted, 2, "d/a.md is unmerged in the index")
            nested = repo({"d/a.md": "x\n"}, {"d/a.md": f"{em}\n"})
            staged("G27D root is a subdirectory", os.path.join(nested, "d"), 2, "is not the top level")
            staged("G27E root in no repository", tree({"d/a.md": f"{em}\n"}), 2, "cannot read the staged changes")
            staged("G27F malformed index policy", repo({"d/a.md": "x\n", ".aiqt/char-policy.json": "{\n"}), 2,
                   "in the index cannot be used")
            # G28 every git call drops the ambient GIT_ variables: with GIT_DIR, GIT_WORK_TREE and GIT_INDEX_FILE
            # naming a repository whose index holds a dash, the clean repository still scans clean; the dirty
            # one, scanned itself with no such variable, is the twin.
            dirty = repo({"d/a.md": "x\n"}, {"d/a.md": f"{em}\n"})
            clean = repo({"d/a.md": "x\n"})
            redirect = {"GIT_DIR": os.path.join(dirty, ".git"), "GIT_WORK_TREE": dirty,
                        "GIT_INDEX_FILE": os.path.join(dirty, ".git", "index")}
            os.environ.update(redirect)
            try:
                staged("G28A ambient GIT_DIR, GIT_WORK_TREE and GIT_INDEX_FILE name another repository", clean, 0,
                       pass_line)
            finally:
                for key in redirect:
                    os.environ.pop(key, None)
            staged("G28B that other repository scanned itself", dirty, 1, "d/a.md: em dash x1 added")
            # G29 (T8) paths with a space, a leading dash and a newline are read whole (git -z) and shown on one
            # line each, the newline as U+000A.
            odd = ("d/a b.md", "d/-x.md", "d/new\nline.md")
            staged("G29A odd paths holding a dash", repo({"README.md": "x\n"}, {rel: f"{em}\n" for rel in odd}), 1,
                   "d/newU+000Aline.md: em dash x1 added")
            for rel in ("d/a b.md", "d/-x.md"):
                staged(f"G29A {rel!r} named", repo({"README.md": "x\n"}, {rel: f"{em}\n"}), 1,
                       f"{rel}: em dash x1 added")
            staged("G29B the same paths clean", repo({"README.md": "x\n"}, {rel: "x\n" for rel in odd}), 0,
                   pass_line)
            # G30 (T13) every occurrence counts: two dashes on one line of a new file are two.
            staged("G30A two dashes on one line", repo({"README.md": "x\n"}, {"d/a.md": f"a{em}b{em}c\n"}), 1,
                   "d/a.md: em dash x2 added (HEAD 0, staged 2)")
            staged("G30B one dash", repo({"README.md": "x\n"}, {"d/a.md": f"a{em}b\n"}), 1,
                   "d/a.md: em dash x1 added (HEAD 0, staged 1)")
            # G31 the command line reaches the staged scan: --staged-scan ROOT exits 1 on a staged dash and 0 on a
            # clean index, run as CI runs the gate (python3 -I -B).
            for label, root, want in (("G31A command line, staged dash", dirty, 1),
                                      ("G31B command line, clean", clean, 0)):
                done = subprocess.run([sys.executable, "-I", "-B", os.path.abspath(__file__), "--staged-scan", root],
                                      capture_output=True, text=True, timeout=120)
                if done.returncode != want:
                    failures.append(f"{label}: want exit {want}, got {done.returncode}: "
                                    f"{(done.stdout + done.stderr).strip()[:200]!r}")
            # G32 a staged symbolic link in scope is named and not scanned, though its target text holds a dash;
            # a regular file of the same name holding a dash is the twin.
            linked = repo({"README.md": "x\n"})
            os.makedirs(os.path.join(linked, "d"))
            os.symlink(f"x{em}.txt", os.path.join(linked, "d", "l.md"))
            git(linked, "add", "--", "d/l.md")
            staged("G32A staged symbolic link", linked, 0, "NOTE: d/l.md is staged as a symbolic link")
            staged("G32B regular file of that name", repo({"README.md": "x\n"}, {"d/l.md": f"x{em}.txt"}), 1,
                   "d/l.md: em dash x1 added")
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            failures.append(f"G19 to G32 staged-scan fixtures could not be built ({exc})")


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

    def expect_streams(label, root, want, out_lines=(), err_lines=(), err_has=(), out_lacks=(), err_lacks=()):
        """Like expect(), but per stream: each of out_lines and err_lines must be a whole line of stdout or
        stderr, each of err_has a substring of stderr, and no out_lacks or err_lacks substring may appear."""
        rc, out, err = scan(root)
        out_split, err_split = out.splitlines(), err.splitlines()
        if (rc != want or any(line not in out_split for line in out_lines)
                or any(line not in err_split for line in err_lines) or any(text not in err for text in err_has)
                or any(text in out for text in out_lacks) or any(text in err for text in err_lacks)):
            failures.append(f"{label}: want exit {want}, got {rc}: stdout {out.strip()[:200]!r}, "
                            f"stderr {err.strip()[:200]!r}")

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
        # G8 encoding: a scoped file that is not UTF-8 cannot be scanned, so it is named and the run exits 2,
        # never skipped with exit 0; that exit 2 wins over a finding in another file (exit 1), whose line is still
        # printed. A file outside the scope is never read, so its bytes do not matter.
        def not_utf8_line(rel):
            return f"error: {rel} is not valid UTF-8, so it cannot be scanned (cannot evaluate); fail-closed"

        not_utf8 = "error: d/a.md is not valid UTF-8"
        expect("G8A UTF-8", tree({"d/a.md": em.encode("utf-8")}), 1)
        expect("G8B not UTF-8", tree({"d/a.md": em.encode("utf-8") + b"\xff"}), 2, not_utf8)
        expect("G8C not UTF-8 beside a finding", tree({"d/a.md": b"\xff", "d/b.md": em}), 2, not_utf8)
        expect("G8C finding still printed", tree({"d/a.md": b"\xff", "d/b.md": em}), 2, "d/b.md:1:1: em dash")
        expect("G8D not UTF-8 out of scope", tree({"d/a.txt": b"\xff", "d/b.md": "x\n"}), 0)
        # G8E to G8G pin the docstring's stream and naming claims line by line, since expect() searches stdout and
        # stderr together. G8E: each of two undecodable files gets its own stderr line, and neither is on stdout.
        expect_streams("G8E each undecodable file named on stderr",
                       tree({"d/a.md": b"\xff", "d/b.md": b"x\xfe\n", "d/c.md": "x\n"}), 2,
                       err_lines=[not_utf8_line("d/a.md"), not_utf8_line("d/b.md")], out_lacks=["not valid UTF-8"])
        # G8F: beside an undecodable file, a finding and its FAIL header are printed on stdout, and only there.
        expect_streams("G8F finding on stdout beside an undecodable file", tree({"d/a.md": b"\xff", "d/b.md": em}),
                       2, out_lines=['FAIL: 1 character(s) forbidden by the "no-dashes" character policy found',
                                     "  d/b.md:1:1: em dash"],
                       err_lines=[not_utf8_line("d/a.md")], out_lacks=["not valid UTF-8"], err_lacks=["em dash"])
        # G8G: an OSError while reading the scoped files stops the scan at once; the undecodable file read before
        # it is not named and the finding read before it is not printed (skipped when running as root).
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            stopped = tree({"d/a.md": b"\xff", "d/b.md": em, "d/c.md": "x\n"})
            os.chmod(os.path.join(stopped, "d", "c.md"), 0)
            expect_streams("G8G an unreadable scoped file stops the scan", stopped, 2,
                           err_lacks=["not valid UTF-8"], out_lacks=["em dash", "FAIL"],
                           err_has=["error: cannot scan the tree ("])
        # G8R revert pair: the same clean text, once as UTF-8 (exit 0 with PASS) and once with one invalid byte
        # added (exit 2, named, no PASS). Put back the old skip (print SKIP, continue) and the second exits 0 with
        # PASS, so this vector fails; it checks the scan's behaviour, not this file's source text.
        expect_streams("G8R clean UTF-8 twin", tree({"d/a.md": b"x\n"}), 0,
                       out_lines=['PASS: no en dash or em dash in the files the "no-dashes" character policy scopes'])
        expect_streams("G8R the same text with one invalid byte", tree({"d/a.md": b"x\xff\n"}), 2,
                       err_lines=[not_utf8_line("d/a.md")], out_lacks=["PASS"])
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
        # T13: --staged-scan takes exactly one ROOT; none, or a second argument, is the same usage error.
        for argv in (["check_no_dashes.py", "--bogus"], ["check_no_dashes.py", "--self-test", "x"],
                     ["check_no_dashes.py", "--default-parity", "x"], ["check_no_dashes.py", "--staged-scan"],
                     ["check_no_dashes.py", "--staged-scan", scratch, "x"]):
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
        # G16 a scoped tree that is there but is not a directory is exit 2, never skipped as absent.
        expect("G16 tree entry that is a regular file", tree({"t": em}, policy(
            chars={em: "em dash"}, scope=[{"tree": "t", "suffixes": [".md"]}])), 2,
            "the scoped tree t is not a directory")
        # G17 a scoped tree that is a symlink: to a directory inside the root it is walked (the em dash in it is
        # a finding); dangling, to a regular file inside the root, or to a directory outside the root (holding
        # the same file, so following it would find it too) it is exit 2, never skipped and never followed.
        outside = tempfile.mkdtemp(dir=scratch)
        with open(os.path.join(outside, "a.md"), "w", encoding="utf-8") as handle:
            handle.write(em)
        for shape, target, want, needle in (
                ("to a directory inside the root", os.path.join("shared", "d"), 1, None),
                ("dangling", "absent", 2, "the scoped tree t is a symbolic link whose target does not exist"),
                ("to a regular file", "f.md", 2,
                 "the scoped tree t is a symbolic link whose target is not a directory"),
                ("to a directory outside the root", outside, 2,
                 "the scoped tree t is a symbolic link whose target resolves outside the repository")):
            linked = tree({"shared/d/a.md": em, "f.md": "plain\n"}, policy(
                chars={em: "em dash"}, scope=[{"tree": "t", "suffixes": [".md"]}]))
            os.symlink(target, os.path.join(linked, "t"))
            expect(f"G17 tree entry that is a symlink {shape}", linked, want, needle)
        # G18 (plan T12) scoped_rel, the staged scan's matcher, lists exactly what scope_paths lists, on a tree
        # with tree and file entries, skip lists naming a directory and a file, a nested tree, overlapping
        # entries, a dot file named like a suffix and a double suffix. G18B pins named paths each way.
        matcher_policy = policy(chars={em: "em dash"}, scope=[
            {"tree": ".", "suffixes": [".md", ".mdc"], "skip": ["node_modules", "skip.md"]},
            {"tree": "plugin", "suffixes": [".py"]}, {"tree": "t/u", "suffixes": [".py"], "skip": ["__pycache__"]},
            {"tree": "t", "suffixes": [".md"]}, {"file": "NOTICE"}, {"file": "d/c.txt"}])
        matcher_files = ["a.md", "b.mdc", "c.txt", ".md", "x.md.txt", "skip.md", "d/skip.md", "d/c.txt",
                         "node_modules/n.md", "d/node_modules/n.md", "plugin/p.py", "plugin/p.txt", "plugin/q/r.py",
                         "t/u/v.py", "t/u/__pycache__/w.py", "t/u/x.md", "t/v.py", "NOTICE", "e/NOTICE"]
        matcher_root = tree({rel: "x\n" for rel in matcher_files})
        walked = {path.relative_to(matcher_root).as_posix() for path in scope_paths(matcher_root, matcher_policy)}
        matched = {rel for rel in matcher_files if scoped_rel(rel.split("/"), matcher_policy)}
        if walked != matched or not walked or walked == set(matcher_files):
            failures.append(f"G18A scoped_rel lists {sorted(matched)}, scope_paths lists {sorted(walked)}")
        default_root = tree({rel: "x\n" for rel in (
            "a.md", "node_modules/a.md", "x/__pycache__/a.md", ".aiqt/standards/a.toml", ".aiqt/a.toml",
            "plugin/a.py", "plugin/node_modules/a.py", "tools/a.py", ".aiqt/core/hooks/a.json", "NOTICE",
            ".aiqt/attribution.toml", "docs/NOTICE")})
        default_files = [path.relative_to(default_root).as_posix() for path in walk_files(default_root)]
        walked = {path.relative_to(default_root).as_posix() for path in scope_paths(default_root, DEFAULT_POLICY)}
        matched = {rel for rel in default_files if scoped_rel(rel.split("/"), DEFAULT_POLICY)}
        if walked != matched or not walked:
            failures.append(f"G18A default policy: scoped_rel {sorted(matched)}, scope_paths {sorted(walked)}")
        for rel, want in (("t/u", False), ("t/u/v.py", True), ("d/node_modules/n.md", False), ("d/c.txt", True),
                          ("c.txt", False), (".md", False), ("e/NOTICE", False), ("NOTICE", True),
                          ("t/u/x.md", True)):
            if scoped_rel(rel.split("/"), matcher_policy) is not want:
                failures.append(f"G18B scoped_rel({rel!r}) is not {want}")
        _staged_vectors(failures, expect, tree, policy, scratch)
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
    if len(argv) == 3 and argv[1] == "--staged-scan":
        return staged_scan(argv[2])
    if argv[1:]:
        print("usage: check_no_dashes.py [--self-test | --default-parity | --staged-scan ROOT]", file=sys.stderr)
        return 2
    return run(Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
