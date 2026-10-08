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
before the check. The region between the BEGIN COPY SOURCE and END COPY SOURCE markers is copied byte for byte
into the preview hook, whose self-test compares the two. The built-in default is the same policy as this
repository's file, and the self-test checks that too. This Python file is in no scope entry, so it may name the
characters in its own source without flagging itself; it writes them only as escapes.

  check_no_dashes.py              scan the repository under its policy
  check_no_dashes.py --self-test  fixture trees for the scan and the policy validator

Exit: 0 clean; 1 a finding; 2 cannot evaluate (an unreadable tree, a malformed policy, a bad argument). A file
that is not valid UTF-8 is reported as SKIP and not scanned.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: check_no_dashes.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import json
import os
import stat
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)
from _standards import dir_present  # noqa: E402  fail-closed absence probe (raises on an unreadable parent)

POLICY_PATH = ".aiqt/char-policy.json"
POLICY_CAP = 65536  # bytes; a larger policy file is malformed
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
# --- BEGIN COPY SOURCE: .preview/char-policy-write.py holds a byte-identical copy of this region (its H12) ---
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


def _self_test():
    """Fixture trees for each scan and validation clause; each pair differs in one feature."""
    import io
    import shutil
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

    def scan(root):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = run(root)
        return rc, out.getvalue(), err.getvalue()

    def expect(label, root, want, needle=None):
        rc, out, err = scan(root)
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
        # G0 the built-in default is this repository's policy file, value for value.
        shipped = os.path.join(Path(__file__).resolve().parents[1], *POLICY_PATH.split("/"))
        try:
            with open(shipped, "rb") as handle:
                if parse_policy(handle.read()) != DEFAULT_POLICY:
                    failures.append(f"G0 DEFAULT_POLICY differs from {POLICY_PATH}")
        except (OSError, PolicyError) as exc:
            failures.append(f"G0 {POLICY_PATH} cannot be compared with DEFAULT_POLICY ({exc})")
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
        # G7 policy validity: a present file is used, and a malformed one is exit 2, never the default.
        bad = {
            "two-code-point key": policy(chars={quote + quote: "pair"}),
            "version 2": policy(version=2),
            "version true": policy(version=True),
            "unknown key": policy(extra=1),
            "missing scope": {"version": 1, "id": "x", "chars": {quote: "q"}},
            "empty id": policy(id=""),
            "empty chars": policy(chars={}),
            "control character": policy(chars={"\n": "newline"}),
            "C1 character": policy(chars={"\x9f": "application program command"}),
            "C1 character in the id": policy(id="a\x80b"),
            "DEL character": policy(chars={"\x7f": "delete"}),
            "empty name": policy(chars={quote: ""}),
            "empty advice": policy(advice=""),
            "empty scope": policy(scope=[]),
            "tree and file": policy(scope=[{"tree": ".", "file": "NOTICE", "suffixes": [".md"]}]),
            "neither tree nor file": policy(scope=[{"suffixes": [".md"]}]),
            "dot-dot path": policy(scope=[{"file": "../NOTICE"}]),
            "absolute path": policy(scope=[{"tree": "/etc", "suffixes": [".md"]}]),
            "backslash path": policy(scope=[{"file": "a\\b"}]),
            "dot component": policy(scope=[{"file": "a/./b"}]),
            "empty component": policy(scope=[{"file": "a//b"}]),
            "trailing slash": policy(scope=[{"tree": "a/", "suffixes": [".md"]}]),
            "dot-led tree": policy(scope=[{"tree": "./a", "suffixes": [".md"]}]),
            "dot file entry": policy(scope=[{"file": "."}]),
            "suffix with a slash": policy(scope=[{"tree": ".", "suffixes": [".m/d"]}]),
            "dot skip": policy(scope=[{"tree": ".", "suffixes": [".md"], "skip": ["."]}]),
            "dot-dot skip": policy(scope=[{"tree": ".", "suffixes": [".md"], "skip": [".."]}]),
            "missing suffixes": policy(scope=[{"tree": "."}]),
            "suffix without a dot": policy(scope=[{"tree": ".", "suffixes": ["md"]}]),
            "empty suffixes": policy(scope=[{"tree": ".", "suffixes": []}]),
            "skip with a slash": policy(scope=[{"tree": ".", "suffixes": [".md"], "skip": ["a/b"]}]),
            "extra key on a file entry": policy(scope=[{"file": "NOTICE", "skip": []}]),
            "extra key on a tree entry": policy(scope=[{"tree": ".", "suffixes": [".md"], "file2": "x"}]),
        }
        for label, data in bad.items():
            expect(f"G7 {label}", tree({"d/a.md": quote}, data), 2)
        expect("G7 duplicate key", tree({"d/a.md": quote}, raw_policy=(
            b'{"version": 1, "version": 1, "id": "x", "chars": {"a": "a"}, "scope": [{"file": "N"}]}')), 2)
        expect("G7 not JSON", tree({"d/a.md": quote}, raw_policy=b"{"), 2)
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
                                         + b" " * POLICY_CAP), 2)
        linked = tree({"d/a.md": quote, "elsewhere.json": json.dumps(policy())})
        os.makedirs(os.path.join(linked, ".aiqt"))
        os.symlink(os.path.join(linked, "elsewhere.json"), os.path.join(linked, ".aiqt", "char-policy.json"))
        expect("G7 symbolic link", linked, 2)
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
        for argv in (["check_no_dashes.py", "--bogus"], ["check_no_dashes.py", "--self-test", "x"]):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = main(argv)
            if rc != 2 or "usage:" not in err.getvalue() or out.getvalue():
                failures.append(f"G13 {argv[1:]}: want exit 2 and usage, got {rc} {out.getvalue()[:120]!r}")
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
    if argv[1:]:
        print("usage: check_no_dashes.py [--self-test]", file=sys.stderr)
        return 2
    return run(Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
