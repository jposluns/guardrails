#!/usr/bin/env python3
"""Leak-denylist gate: block internal host specifics and known internal codenames from public content.

Two layers (defense in depth):
  1. STRUCTURAL patterns (encoded here, generic, reveal no host specifics): absolute host paths under
     this host's roots, RFC1918 private IPs, GitHub token/PAT shapes, and worker-account id shapes.
  2. A HASHED codename denylist (tools/leak-hashes.txt): SHA-256 hashes only, never the words. Text is
     normalized (camelCase split; lowercased; every non-alphanumeric run, including `_`, `-`, and spaces,
     is a separator) and 1..maxn word n-grams are hashed and matched, so a codename written with a space,
     hyphen, underscore, or CamelCase all match the same hash.

HONEST SCOPE. This catches ACCIDENTAL reintroduction of internal terms and host specifics; it is not an
exfiltration control. The SHA-256 hashes are UNSALTED and low-entropy n-grams are dictionary-reversible,
so this is OBSCURITY, not secrecy (a salt cannot help: a public gate must hash arbitrary public text, so
any salt would also be public). Deliberate obfuscation (mid-word splits, HTML entities, zero-width chars)
can evade the hash layer; binary/archive contents (e.g. a .zip) are not unpacked; the structural path
patterns are scoped to this host's Linux layout. The dual-family verifiers and the private plaintext check
are the compensating layers. A line carrying a `leak-allow` marker is exempt from the STRUCTURAL layer.

SCANNED SET. Every file the walk yields (all of the working tree except the .git, node_modules and
__pycache__ subtrees, the denylist file included) is read as bytes and handled one of three ways:
  - it decodes as UTF-8: it is scanned, whatever its suffix (a .csv, .example or .sha256 included);
  - it carries a declared text suffix (TEXT_SUFFIXES) or no suffix but holds a non-UTF-8 byte: it is decoded
    losslessly (surrogateescape keeps every byte; none is dropped or replaced) and scanned;
  - it is not a regular file, or it does not decode and has any other suffix (a .zip, an image): it is named
    on a SKIP line and counted, never passed silently.
The PASS and FAIL lines state the scanned and skipped counts.
Exit 0 clean, 1 on any finding or a malformed hashes file, 2 on a read error (unreadable dir/file, fail-closed).

  check_leaks.py              scan the working tree
  check_leaks.py --self-test  build fixture trees and assert the scanned-set contract above
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_leaks.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import hashlib
import re
import stat
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)

SKIP_DIRS = {".git", "node_modules", "__pycache__"}
# Declared text suffixes: a file with one of these (or with no suffix) is decoded losslessly when it holds a
# stray non-UTF-8 byte. A file with any other suffix is still scanned when it decodes as UTF-8. The
# internal-names gate imports this set as its own scope filter.
TEXT_SUFFIXES = {".md", ".mdc", ".py", ".sh", ".yml", ".yaml", ".toml", ".json", ".txt",
                 ".html", ".css", ".js", ".cfg", ".ini", ".conf", ".svg"}

_OCT = r'(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])'
STRUCTURAL = [
    (re.compile(r'(?<![\w.])/(?:opt|var/lib|mnt|home)/[A-Za-z0-9_.][A-Za-z0-9_./-]*'), "absolute host path"),
    (re.compile(r'\b10\.' + _OCT + r'\.' + _OCT + r'\.' + _OCT + r'\b'), "private IP (10/8)"),
    (re.compile(r'\b172\.(?:1[6-9]|2[0-9]|3[01])\.' + _OCT + r'\.' + _OCT + r'\b'), "private IP (172.16/12)"),
    (re.compile(r'\b192\.168\.' + _OCT + r'\.' + _OCT + r'\b'), "private IP (192.168/16)"),
    (re.compile(r'\bgithub_pat_[A-Za-z0-9_]{20,}\b'), "GitHub PAT"),
    (re.compile(r'\bgh[pousr]_[A-Za-z0-9]{20,}\b'), "GitHub token"),
    (re.compile(r'\b(?:claude|codex)[-_](?:max|team|teams|worker|pro)[-_][a-z0-9][a-z0-9_-]*\b', re.I),
     "worker-account id shape"),
]

_CAMEL = re.compile(r'(?<=[a-z0-9])(?=[A-Z])')
_WORD = re.compile(r'[a-z0-9]+')      # '_' is a SEPARATOR, so under_score == with-hyphen == with space.
_HEX64 = re.compile(r'^[0-9a-f]{64}$')


def _tokens(text):
    return _WORD.findall(_CAMEL.sub(" ", text).lower())


def ngram_forms(text, maxn):
    toks = _tokens(text)
    grams = set()
    for n in range(1, maxn + 1):
        for i in range(len(toks) - n + 1):
            grams.add("-".join(toks[i:i + n]))
    return grams


def term_hash(term):   # the generator (store/gen-leak-hashes.py) imports this for the shared normalization.
    return hashlib.sha256("-".join(_tokens(term)).encode("utf-8")).hexdigest()


def load_denylist(root):
    """Return (hashes, maxn, bad_lines). bad_lines names any non-hash entry (integrity failure)."""
    f = root / "tools" / "leak-hashes.txt"
    hashes, maxn, bad, maxn_set = set(), 3, [], False
    try:
        content = f.read_text(encoding="utf-8")
    except FileNotFoundError:
        # An ABSENT denylist is intended (no extra terms -> empty set). An UNREADABLE denylist (or an
        # unreadable parent) raises a non-FileNotFound OSError that propagates to main's fail-closed
        # try (exit 2), so it can never read as an empty denylist and pass clean.
        content = None
    if content is not None:
        for number, line in enumerate(content.splitlines(), 1):
            s = line.strip()
            if not s or s.startswith("#"):
                parts = s.split()
                if len(parts) >= 2 and parts[0] == "#" and parts[1] == "maxn":
                    # exactly ONE "# maxn N", N a bounded positive integer; a duplicate, non-positive,
                    # oversized, or malformed directive fails closed (each would silently weaken or disable
                    # the codename layer). len<=3 is checked before int() so an overlong run cannot raise.
                    if maxn_set:
                        bad.append("leak-hashes.txt:{}: duplicate '# maxn' directive".format(number))
                    elif (len(parts) == 3 and parts[2].isascii() and parts[2].isdigit()
                          and 1 <= len(parts[2]) <= 3 and 1 <= int(parts[2]) <= 100):
                        maxn, maxn_set = int(parts[2]), True
                    else:
                        bad.append("leak-hashes.txt:{}: malformed '# maxn N' (need one integer 1..100)".format(number))
                continue
            if _HEX64.match(s):
                hashes.add(s)
            else:
                bad.append("leak-hashes.txt:{}: not a 64-hex hash (possible plaintext or trailing token)".format(number))
    return hashes, maxn, bad


def scan_text(text, hashes, maxn, honor_leak_allow=True):
    """Shared blob scanner: returns (line_or_None, label) findings (STRUCTURAL carries a 1-based line; a
    hash match carries None). honor_leak_allow (file gate True, message gate False) exempts a `leak-allow`
    line from STRUCTURAL. Keeps STRUCTURAL and the hash denylist applied in exactly one place."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if honor_leak_allow and "leak-allow" in line:
            continue
        found += [(number, label) for pattern, label in STRUCTURAL if pattern.search(line)]
    if hashes:
        for gram in ngram_forms(text, maxn):
            if hashlib.sha256(gram.encode("utf-8")).hexdigest() in hashes:
                found.append((None, "internal codename (hash match)"))
                break
    return found


def _display(rel):
    """A printable form of a relative path: a non-UTF-8 file name prints with backslash escapes instead of
    raising on output."""
    return str(rel).encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")


def read_scan_text(path):
    """Read one walked file for the scan. Returns (text, lossless, skip_reason): text is the content to
    scan, or None when the file is skipped; lossless is True when a declared-text file held a non-UTF-8 byte
    and was decoded with surrogateescape; skip_reason says why a file is not scanned. A read error raises
    OSError for the caller's fail-closed exit 2."""
    if not stat.S_ISREG(path.stat().st_mode):
        return None, False, "not a regular file"
    data = path.read_bytes()
    try:
        return data.decode("utf-8"), False, None
    except UnicodeDecodeError:
        pass
    if not path.suffix or path.suffix in TEXT_SUFFIXES:
        return data.decode("utf-8", errors="surrogateescape"), True, None
    return None, False, "not UTF-8 and not a declared text suffix"


def scan_tree(root):
    """Scan every file under root. Returns (findings, scanned, lossless, skipped): skipped lists each file
    not scanned, with its reason. Raises OSError (or UnicodeDecodeError on the denylist) on a read error."""
    # load_denylist reads a required file (the leak-hash denylist); run keeps this call inside its
    # fail-closed try so an unreadable denylist is a clean exit 2, not a traceback.
    hashes, maxn, findings = load_denylist(root)
    findings = list(findings)
    scanned, lossless, skipped = 0, 0, []
    for path in sorted(walk_files(root, SKIP_DIRS)):
        rel = _display(path.relative_to(root))
        text, decoded_lossless, reason = read_scan_text(path)
        if text is None:
            skipped.append("{} ({})".format(rel, reason))
            continue
        scanned += 1
        lossless += decoded_lossless
        for number, label in scan_text(text, hashes, maxn, honor_leak_allow=True):
            findings.append("{}:{}: {}".format(rel, number, label) if number is not None
                            else "{}: {}".format(rel, label))
    return findings, scanned, lossless, skipped


def run(root):
    try:
        findings, scanned, lossless, skipped = scan_tree(root)
    except (OSError, UnicodeDecodeError) as exc:
        # An unreadable/corrupt denylist OR an unreadable directory/file is a read failure on a required
        # input, not a clean skip: fail closed (exit 2) so a leak gate never reports clean without having
        # read its denylist and scanned every subtree. UnicodeDecodeError covers a non-UTF-8 denylist.
        print("error: cannot read a required input (denylist or tree) ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    for item in skipped:
        print("SKIP (not scanned): " + item)
    counts = ("{} file(s) scanned ({} non-UTF-8 text file(s) decoded losslessly), {} file(s) skipped (each "
              "named on a SKIP line)".format(scanned, lossless, len(skipped)))
    if findings:
        print("FAIL: {} possible leak(s) of internal host specifics or codenames; {}".format(len(findings), counts))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    print("PASS: no internal host specifics or known codenames in the scanned working tree; " + counts)
    return 0


def main():
    if "--self-test" in sys.argv[1:]:
        return self_test_main()
    return run(Path(__file__).resolve().parents[1])


# --- self-test ----------------------------------------------------------------------------------------
# Fixture trees in a tempdir, each driving the real run() so the scan is never mocked:
#   1. a clean tree passes (exit 0) and its PASS line states the scanned and skipped counts,
#   2. a private IP in a .csv (a suffix outside TEXT_SUFFIXES) is a finding (exit 1),
#   3. a host path in a .md holding a Latin-1 byte is a finding (exit 1), decoded losslessly,
#   4. a codename in a .txt holding a Latin-1 byte matches the hash layer (exit 1),
#   5. an undecodable .zip is named on a SKIP line and counted (exit 0),
#   6. a symlink to /dev/null (not a regular file) is named on a SKIP line and counted (exit 0),
#   7. an unreadable file fails closed (exit 2).
# Cases 2, 3, 5 and 6 each fail on the pre-fix scan, which skipped unlisted suffixes and non-UTF-8 files
# silently and read /dev/null as an empty clean file. The planted host path and private IP are assembled
# from pieces so this source stays clean under its own scan.
_HOST_PATH = "/" + "opt/" + "fixture-host/private/path"
_PRIVATE_IP = "10." + "20.30.40"


def self_test_main():
    import io
    import os
    import shutil
    import tempfile
    from contextlib import redirect_stderr, redirect_stdout

    def run_capture(root):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            rc = run(root)
        return rc, out.getvalue()

    def tree(name, files):
        base = tmp / name
        for rel, data in files.items():
            path = base / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return base

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-leaks-gate-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    clean_files = {"docs/a.md": b"# Plain\n\nNothing private here.\n", "site/data.csv": b"id,name\n1,alpha\n"}
    failures = []
    skipped_cases = []
    unreadable = None
    try:
        # 1. a clean tree passes and states its counts.
        rc, out = run_capture(tree("clean", clean_files))
        if rc != 0 or "2 file(s) scanned (0 non-UTF-8" not in out or "0 file(s) skipped" not in out:
            failures.append("a clean tree expected exit 0 with 2 files scanned and 0 skipped "
                            "(got {}: {!r})".format(rc, out))

        # 2. a private IP in a .csv is a finding.
        rc, out = run_capture(tree("csv", dict(clean_files, **{
            "site/downloads/plant.csv": ("id,host\n1," + _PRIVATE_IP + "\n").encode("ascii")})))
        if rc != 1 or "plant.csv:2: private IP (10/8)" not in out:
            failures.append("a private IP in a .csv expected exit 1 naming plant.csv:2 (got {}: {!r})"
                            .format(rc, out))

        # 3. a host path in a .md holding a Latin-1 byte is a finding, decoded losslessly.
        rc, out = run_capture(tree("latin1", dict(clean_files, **{
            "docs/latin1.md": b"# caf\xe9\n\nsee " + _HOST_PATH.encode("ascii") + b"\n"})))
        if (rc != 1 or "latin1.md:3: absolute host path" not in out
                or "1 non-UTF-8 text file(s) decoded losslessly" not in out):
            failures.append("a host path in a Latin-1 .md expected exit 1 naming latin1.md:3, decoded "
                            "losslessly (got {}: {!r})".format(rc, out))

        # 4. a codename in a .txt holding a Latin-1 byte matches the hash layer.
        rc, out = run_capture(tree("codename", dict(clean_files, **{
            "tools/leak-hashes.txt": (term_hash("fixture codename") + "\n").encode("ascii"),
            "notes/latin1.txt": b"caf\xe9 FixtureCodename\n"})))
        if rc != 1 or "notes/latin1.txt: internal codename (hash match)" not in out:
            failures.append("a codename in a Latin-1 .txt expected exit 1 by hash match (got {}: {!r})"
                            .format(rc, out))

        # 5. an undecodable .zip is named on a SKIP line and counted.
        rc, out = run_capture(tree("zip", dict(clean_files, **{
            "site/downloads/pack.zip": b"PK\x03\x04\xff\xfe\x00binary"})))
        if (rc != 0 or "SKIP (not scanned): site/downloads/pack.zip (not UTF-8" not in out
                or "1 file(s) skipped" not in out):
            failures.append("an undecodable .zip expected exit 0 with a SKIP line naming it and 1 file "
                            "skipped (got {}: {!r})".format(rc, out))

        # 6. a symlink to /dev/null is not a regular file: named on a SKIP line and counted.
        devnull = Path("/dev/null")
        if not devnull.exists() or devnull.is_file():
            skipped_cases.append("6 non-regular (/dev/null unavailable)")
        else:
            base = tree("nonreg", clean_files)
            os.symlink(str(devnull), str(base / "docs" / "devnull.md"))
            rc, out = run_capture(base)
            if (rc != 0 or "SKIP (not scanned): docs/devnull.md (not a regular file)" not in out
                    or "1 file(s) skipped" not in out):
                failures.append("a symlink to /dev/null expected exit 0 with a SKIP line naming it "
                                "(got {}: {!r})".format(rc, out))

        # 7. an unreadable file fails closed (exit 2). Skipped where the runner bypasses DAC (root).
        base = tree("unread", clean_files)
        unreadable = base / "docs" / "a.md"
        os.chmod(unreadable, 0)
        if os.access(unreadable, os.R_OK):
            skipped_cases.append("7 unreadable-file (chmod-0 still readable)")
        else:
            rc, out = run_capture(base)
            if rc != 2:
                failures.append("an unreadable file expected exit 2, fail-closed (got {}: {!r})".format(rc, out))
        os.chmod(unreadable, 0o644)
        unreadable = None
    finally:
        if unreadable is not None:
            os.chmod(unreadable, 0o644)  # restore even on an unexpected early exit
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    note = ("" if not skipped_cases else
            " NOTE: skipped {} case(s) the runner cannot exercise: {}".format(
                len(skipped_cases), ", ".join(skipped_cases)))
    print("SELF-TEST PASS: a clean tree passes and states its scanned and skipped counts; a private IP in a "
          ".csv, a host path in a Latin-1 .md and a codename in a Latin-1 .txt are each a finding (exit 1); "
          "an undecodable .zip and a symlink to /dev/null are each named on a SKIP line and counted; and an "
          "unreadable file fails closed (exit 2)" + note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
