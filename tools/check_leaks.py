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

SCANNED SET. The walk yields every file in the working tree except the .git, node_modules and
__pycache__ subtrees (the denylist file included). Each yielded path is first CLASSIFIED by a stat probe
(a metadata check, not a content read), then its content is read as bytes and handled one of four ways:
  - it decodes as UTF-8: it is scanned, whatever its suffix (a .csv, .example or .sha256 included);
  - it starts with a UTF-16 byte-order mark and decodes as UTF-16: it is scanned and counted as a
    lossless decode (any other multibyte encoding without that mark falls to the byte decode below,
    where the patterns cannot match across its wide characters; a disclosed residual);
  - its suffix is on the named binary allow-list BINARY_SUFFIXES (matched case-insensitively; an
    archive): it is named on a SKIP line and counted, never passed silently;
  - anything else holding a non-UTF-8 byte, whatever its suffix or that suffix's case (a missing suffix
    included), is decoded losslessly (surrogateescape keeps every byte; none is dropped or replaced)
    and scanned.
SYMLINK RULES, stated exactly: a symlink to a regular file is followed (the stat probe reads the target)
and the target's content is scanned; a dangling or unresolvable file symlink is a read error (exit 2); a
non-regular target (a FIFO, a device) is named on a SKIP line and its content is never opened; a
SYMLINKED DIRECTORY is never followed and never silently dropped: it is named on a SKIP line and
counted, with the subtree behind it not scanned.
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
# Declared text suffixes. This gate no longer keys its decode on them (every non-UTF-8 file off the
# binary allow-list is decoded losslessly and scanned, whatever its suffix); the set stays because the
# internal-names gate imports it as its own scope filter.
TEXT_SUFFIXES = {".md", ".mdc", ".py", ".sh", ".yml", ".yaml", ".toml", ".json", ".txt",
                 ".html", ".css", ".js", ".cfg", ".ini", ".conf", ".svg"}
# Named binary allow-list, defined beside TEXT_SUFFIXES: the ONLY suffixes (matched case-insensitively)
# whose non-UTF-8 content may skip the scan, each named on a SKIP line. Exactly the shipped-archive
# suffix today; a new binary format must be added here deliberately or its bytes are scanned.
BINARY_SUFFIXES = {".zip"}

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


def _stat_mode(path):
    """The classification probe: stat() mode, FOLLOWING a file symlink (the file rule). A module-level
    seam so the self-test can report a non-regular mode deterministically (case 6) on every runner."""
    return path.stat().st_mode


def _read_bytes(path):
    """The content read. A module-level seam so the self-test can raise a read error deterministically
    (case 7) on a runner whose chmod 0 cannot bite (root or CAP_DAC_OVERRIDE)."""
    return path.read_bytes()


def read_scan_text(path):
    """Read one walked file for the scan. Returns (text, lossless, skip_reason): text is the content to
    scan, or None when the file is skipped; lossless is True when a non-UTF-8 file was decoded without
    dropping or replacing a byte; skip_reason says why a file is not scanned. The policy, in order: a
    non-regular file (the stat probe follows a file symlink, so a symlink to a regular file reads as its
    target) is a named SKIP whose content is never opened; UTF-8 content is scanned whatever the suffix;
    content behind a UTF-16 byte-order mark is decoded as UTF-16 and scanned; a remaining non-UTF-8 file
    whose suffix is on BINARY_SUFFIXES (case-insensitively) is a named SKIP; anything else is decoded
    losslessly with surrogateescape and scanned, whatever its suffix. A read error raises OSError for
    the caller's fail-closed exit 2 (a dangling symlink included)."""
    if not stat.S_ISREG(_stat_mode(path)):
        return None, False, "not a regular file"
    data = _read_bytes(path)
    try:
        return data.decode("utf-8"), False, None
    except UnicodeDecodeError:
        pass
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16"), True, None
        except UnicodeDecodeError:
            pass  # an odd-length or malformed stream: fall through to the lossless byte decode
    if path.suffix.lower() in BINARY_SUFFIXES:
        return None, False, "not UTF-8, on the binary allow-list (suffix {})".format(path.suffix.lower())
    return data.decode("utf-8", errors="surrogateescape"), True, None


def scan_tree(root):
    """Scan every file under root. Returns (findings, scanned, lossless, skipped): skipped lists each file
    (or symlinked directory) not scanned, with its reason. Raises OSError (or UnicodeDecodeError on the
    denylist) on a read error."""
    # load_denylist reads a required file (the leak-hash denylist); run keeps this call inside its
    # fail-closed try so an unreadable denylist is a clean exit 2, not a traceback.
    hashes, maxn, findings = load_denylist(root)
    findings = list(findings)
    scanned, lossless, skipped = 0, 0, []
    symlink_dirs = []
    for path in sorted(walk_files(root, SKIP_DIRS, symlink_dirs=symlink_dirs)):
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
    for link in symlink_dirs:
        skipped.append("{} (symlinked directory; not followed)".format(_display(link.relative_to(root))))
    skipped.sort()
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
# Fixture trees in a tempdir, each driving the real run() so the scan is never mocked. Every case runs
# on every runner: the two environment-dependent behaviours (a non-regular file, an unreadable file) are
# exercised through deterministic fault injection on the module seams _stat_mode/_read_bytes instead of
# a conditional skip, so a PASS is never printed while a guarded case did not run.
#   1. a clean tree passes (exit 0) and its PASS line states the scanned and skipped counts,
#   2. a private IP in a .csv is a finding (exit 1); 2b. a private IP in a Latin-1 .tsv (a suffix on no
#      list) is decoded losslessly and found,
#   3. a host path in a Latin-1 .md is a finding, decoded losslessly; 3b. the same in a Latin-1
#      README.MD (an upper-case suffix) is found, not skipped; 3c. a host path in a UTF-16 file with a
#      byte-order mark is decoded as UTF-16 and found,
#   4. a codename in a Latin-1 .txt matches the hash layer (exit 1); 4b. the denylist file itself is
#      scanned (a private IP in one of its comment lines is a finding); 4c. a suffixless Latin-1 file is
#      decoded losslessly and its host path found,
#   5. an undecodable .zip is named on a SKIP line and counted (exit 0); 5b. the allow-list match is
#      case-insensitive (an undecodable .ZIP is the same named SKIP),
#   6. a non-regular file (injected FIFO mode) is named on a SKIP line, its planted IP never read
#      (exit 0); 6b. a symlink to a regular file is followed and its target scanned (exit 1); 6c. a
#      symlinked directory is named on a SKIP line, never silently dropped (exit 0); 6d. a dangling
#      symlink fails closed (exit 2),
#   7. an unreadable file (injected PermissionError) fails closed (exit 2).
# Cases 1 through 6 each fail on the original pre-531 scan. Against the round-1 revision this replaces,
# 2b, 3b, 3c and 6c are new findings or named skips, 6 and 7 run where that revision skipped, and 4b,
# 4c, 5b, 6b and 6d pin behaviours a mutant could drop (the denylist walk, the suffixless decode, the
# case-insensitive allow-list, the stat-follows-a-file-symlink rule, the dangling-symlink error). The
# planted host path and private IP are assembled from pieces so this source stays clean under its own
# scan.
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
    try:
        # 1. a clean tree passes and states its counts.
        rc, out = run_capture(tree("clean", clean_files))
        if rc != 0 or "2 file(s) scanned (0 non-UTF-8" not in out or "0 file(s) skipped" not in out:
            failures.append("a clean tree expected exit 0 with 2 files scanned and 0 skipped "
                            "(got {}: {!r})".format(rc, out))

        # 2. a private IP in a UTF-8 .csv is a finding (scanned whatever the suffix).
        rc, out = run_capture(tree("csv", dict(clean_files, **{
            "site/downloads/plant.csv": ("id,host\n1," + _PRIVATE_IP + "\n").encode("ascii")})))
        if rc != 1 or "plant.csv:2: private IP (10/8)" not in out:
            failures.append("a private IP in a .csv expected exit 1 naming plant.csv:2 (got {}: {!r})"
                            .format(rc, out))

        # 2b. a private IP in a Latin-1 .tsv (a suffix on no list) is decoded losslessly and found.
        files = dict(clean_files)
        files["data/plant.tsv"] = b"id\thost\n1\t" + _PRIVATE_IP.encode("ascii") + b"\n\xe9\n"
        rc, out = run_capture(tree("tsv", files))
        if (rc != 1 or "plant.tsv:2: private IP (10/8)" not in out
                or "1 non-UTF-8 text file(s) decoded losslessly" not in out):
            failures.append("a private IP in a Latin-1 .tsv expected exit 1, decoded losslessly "
                            "(got {}: {!r})".format(rc, out))

        # 3. a host path in a .md holding a Latin-1 byte is a finding, decoded losslessly.
        rc, out = run_capture(tree("latin1", dict(clean_files, **{
            "docs/latin1.md": b"# caf\xe9\n\nsee " + _HOST_PATH.encode("ascii") + b"\n"})))
        if (rc != 1 or "latin1.md:3: absolute host path" not in out
                or "1 non-UTF-8 text file(s) decoded losslessly" not in out):
            failures.append("a host path in a Latin-1 .md expected exit 1 naming latin1.md:3, decoded "
                            "losslessly (got {}: {!r})".format(rc, out))

        # 3b. an upper-case suffix is no silent skip: a host path in a Latin-1 README.MD is found.
        files = dict(clean_files)
        files["docs/README.MD"] = b"caf\xe9 see " + _HOST_PATH.encode("ascii") + b"\n"
        rc, out = run_capture(tree("upper-md", files))
        if rc != 1 or "docs/README.MD:1: absolute host path" not in out:
            failures.append("a host path in a Latin-1 README.MD expected exit 1 "
                            "(got {}: {!r})".format(rc, out))

        # 3c. a UTF-16 file with a byte-order mark is decoded as UTF-16 and scanned: a host path in it
        #     is found, and the file counts as a lossless decode.
        files = dict(clean_files)
        files["docs/wide.md"] = ("# note\nsee " + _HOST_PATH + "\n").encode("utf-16")
        rc, out = run_capture(tree("utf16", files))
        if (rc != 1 or "docs/wide.md:2: absolute host path" not in out
                or "1 non-UTF-8 text file(s) decoded losslessly" not in out):
            failures.append("a host path in a UTF-16 .md with a BOM expected exit 1, decoded as UTF-16 "
                            "(got {}: {!r})".format(rc, out))

        # 4. a codename in a .txt holding a Latin-1 byte matches the hash layer.
        rc, out = run_capture(tree("codename", dict(clean_files, **{
            "tools/leak-hashes.txt": (term_hash("fixture codename") + "\n").encode("ascii"),
            "notes/latin1.txt": b"caf\xe9 FixtureCodename\n"})))
        if rc != 1 or "notes/latin1.txt: internal codename (hash match)" not in out:
            failures.append("a codename in a Latin-1 .txt expected exit 1 by hash match (got {}: {!r})"
                            .format(rc, out))

        # 4b. the denylist file itself is walked and scanned like any other file: a private IP in a
        #     comment line of tools/leak-hashes.txt is a finding.
        files = dict(clean_files)
        files["tools/leak-hashes.txt"] = ("# near " + _PRIVATE_IP + "\n").encode("ascii")
        rc, out = run_capture(tree("denylist-scanned", files))
        if rc != 1 or "tools/leak-hashes.txt:1: private IP (10/8)" not in out:
            failures.append("a private IP inside leak-hashes.txt expected exit 1 "
                            "(got {}: {!r})".format(rc, out))

        # 4c. a suffixless file holding a Latin-1 byte is decoded losslessly: a host path in it is found.
        files = dict(clean_files)
        files["LICENSE"] = b"caf\xe9 at " + _HOST_PATH.encode("ascii") + b"\n"
        rc, out = run_capture(tree("suffixless", files))
        if rc != 1 or "LICENSE:1: absolute host path" not in out:
            failures.append("a host path in a Latin-1 suffixless file expected exit 1 "
                            "(got {}: {!r})".format(rc, out))

        # 5. an undecodable .zip is named on a SKIP line and counted.
        rc, out = run_capture(tree("zip", dict(clean_files, **{
            "site/downloads/pack.zip": b"PK\x03\x04\xff\xfe\x00binary"})))
        if (rc != 0 or "SKIP (not scanned): site/downloads/pack.zip (not UTF-8" not in out
                or "1 file(s) skipped" not in out):
            failures.append("an undecodable .zip expected exit 0 with a SKIP line naming it and 1 file "
                            "skipped (got {}: {!r})".format(rc, out))

        # 5b. the binary allow-list match is case-insensitive: an undecodable .ZIP is the same named SKIP.
        files = dict(clean_files)
        files["site/downloads/pack2.ZIP"] = b"PK\x03\x04\xff\xfe\x00binary"
        rc, out = run_capture(tree("zip-upper", files))
        if (rc != 0 or "SKIP (not scanned): site/downloads/pack2.ZIP (not UTF-8, on the binary allow-list" not in out
                or "1 file(s) skipped" not in out):
            failures.append("an undecodable .ZIP expected exit 0 with a SKIP line naming it "
                            "(got {}: {!r})".format(rc, out))

        # 6. a non-regular file is named on a SKIP line and counted, its content never read.
        #    Deterministic: the stat seam reports a FIFO mode for one fixture file, so the case runs on
        #    every runner (no /dev/null dependency), and the file plants a private IP that must stay
        #    unseen because the skip happens before any content read.
        files = dict(clean_files)
        files["docs/fifo.md"] = ("fd " + _PRIVATE_IP + "\n").encode("ascii")
        base = tree("nonreg", files)
        module = sys.modules[__name__]
        saved_mode = module._stat_mode
        def _fifo_mode(path, _real=saved_mode):
            if path.name == "fifo.md":
                return stat.S_IFIFO | 0o644
            return _real(path)
        module._stat_mode = _fifo_mode
        try:
            rc, out = run_capture(base)
        finally:
            module._stat_mode = saved_mode
        if (rc != 0 or "SKIP (not scanned): docs/fifo.md (not a regular file)" not in out
                or "1 file(s) skipped" not in out):
            failures.append("a non-regular file expected exit 0 with a SKIP line naming it and its "
                            "planted content unread (got {}: {!r})".format(rc, out))

        # 6b. a symlink to a regular file is followed and its target scanned (the file rule).
        base = tree("symfile", dict(clean_files))
        (tmp / "target.txt").write_bytes(("ip " + _PRIVATE_IP + "\n").encode("ascii"))
        os.symlink(str(tmp / "target.txt"), str(base / "docs" / "linked.txt"))
        rc, out = run_capture(base)
        if rc != 1 or "docs/linked.txt:1: private IP (10/8)" not in out:
            failures.append("a symlink to a regular file expected exit 1 on the target content "
                            "(got {}: {!r})".format(rc, out))

        # 6c. a symlinked directory is named on a SKIP line and counted, never silently dropped.
        base = tree("symdir", dict(clean_files))
        (tmp / "behind").mkdir(exist_ok=True)
        os.symlink(str(tmp / "behind"), str(base / "docs" / "linkdir"))
        rc, out = run_capture(base)
        if (rc != 0 or "SKIP (not scanned): docs/linkdir (symlinked directory; not followed)" not in out
                or "1 file(s) skipped" not in out):
            failures.append("a symlinked directory expected exit 0 with a SKIP line naming it "
                            "(got {}: {!r})".format(rc, out))

        # 6d. a dangling file symlink is a read error: fail-closed (exit 2), never a clean skip.
        base = tree("dangle", dict(clean_files))
        os.symlink(str(base / "docs" / "gone.md"), str(base / "docs" / "dangling.md"))
        rc, out = run_capture(base)
        if rc != 2:
            failures.append("a dangling symlink expected exit 2, fail-closed (got {}: {!r})".format(rc, out))

        # 7. an unreadable file fails closed (exit 2). Deterministic: the read seam raises the
        #    PermissionError such a file raises, so the case runs on every runner (a chmod-0 probe would
        #    be skipped under a DAC-bypassing root and the guarded defect could ship meanwhile).
        base = tree("unread", dict(clean_files))
        saved_read = module._read_bytes
        def _deny_read(path, _real=saved_read):
            if path.name == "a.md":
                raise PermissionError(13, "injected permission denied", str(path))
            return _real(path)
        module._read_bytes = _deny_read
        try:
            rc, out = run_capture(base)
        finally:
            module._read_bytes = saved_read
        if rc != 2:
            failures.append("an unreadable file expected exit 2, fail-closed (got {}: {!r})".format(rc, out))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: a clean tree passes and states its scanned and skipped counts; a private IP in a "
          ".csv or a Latin-1 .tsv, a host path in a Latin-1 .md, a Latin-1 README.MD, a UTF-16 file with a "
          "byte-order mark, a suffixless Latin-1 file or a symlinked regular file's target, a codename in "
          "a Latin-1 .txt, and a private IP inside the denylist file are each a finding (exit 1); an "
          "undecodable .zip (in either suffix case) and an injected non-regular file are each named on a "
          "SKIP line and counted, the non-regular content never read; a symlinked directory is named on a "
          "SKIP line, never silently dropped; and a dangling symlink and an injected unreadable file each "
          "fail closed (exit 2)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
