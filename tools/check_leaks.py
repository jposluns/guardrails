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
Exit 0 clean, 1 on any finding or a malformed hashes file, 2 on a read error (unreadable dir/file, fail-closed).
"""
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)

SKIP_DIRS = {".git", "node_modules", "__pycache__"}
SKIP_NAMES = {"leak-hashes.txt"}
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
    hashes, maxn, bad = set(), 3, []
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
                if s.startswith("# maxn"):
                    parts = s.split()
                    if len(parts) >= 3 and parts[2].isdigit():
                        maxn = int(parts[2])
                continue
            token = s.split()[0]
            if _HEX64.match(token):
                hashes.add(token)
            else:
                bad.append("leak-hashes.txt:{}: not a 64-hex hash (possible plaintext)".format(number))
    return hashes, maxn, bad


def main():
    root = Path(__file__).resolve().parents[1]
    try:
        # load_denylist reads a required file (the leak-hash denylist); keep it inside the fail-closed
        # try so an unreadable denylist is a clean exit 2, not a traceback.
        hashes, maxn, findings = load_denylist(root)
        findings = list(findings)
        for path in sorted(walk_files(root, SKIP_DIRS)):
            if path.name in SKIP_NAMES:
                continue
            if path.suffix and path.suffix not in TEXT_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue  # binary / non-utf8: a text scanner skips it (gitleaks scans binaries)
            rel = path.relative_to(root)
            for number, line in enumerate(text.splitlines(), 1):
                if "leak-allow" in line:
                    continue
                for pattern, label in STRUCTURAL:
                    if pattern.search(line):
                        findings.append("{}:{}: {}".format(rel, number, label))
            if hashes:
                for gram in ngram_forms(text, maxn):
                    if hashlib.sha256(gram.encode("utf-8")).hexdigest() in hashes:
                        findings.append("{}: internal codename (hash match)".format(rel))
                        break
    except (OSError, UnicodeDecodeError) as exc:
        # An unreadable/corrupt denylist OR an unreadable directory/file is a read failure on a required
        # input, not a clean skip: fail closed (exit 2) so a leak gate never reports clean without having
        # read its denylist and scanned every subtree. UnicodeDecodeError covers a non-UTF-8 denylist.
        print("error: cannot read a required input (denylist or tree) ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    if findings:
        print("FAIL: {} possible leak(s) of internal host specifics or codenames".format(len(findings)))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    print("PASS: no internal host specifics or known codenames in tracked content")
    return 0


if __name__ == "__main__":
    sys.exit(main())
