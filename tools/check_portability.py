#!/usr/bin/env python3
"""Portability gate for the shippable pack surface. Offline, stdlib only, fail-closed.

check_leaks.py answers a CONFIDENTIALITY question repo-wide: does any tracked content expose private host
specifics or private codenames? This gate (GA-3) answers a FITNESS question over the shippable surface only:
can this exact content land on a stranger's machine, in a stranger's project, unchanged? Content can fail
portability while being perfectly public: the maintainer's email in a rule body, or a reference to this
repo's operational vocabulary, leaks nothing but ships governance that is not the adopter's.

What portability adds beyond the leak gate (nothing here duplicates it):
  C1 operator identity outside its attribution: the identity is public attribution, not a secret, so the
     hashed leak denylist must never deny it; here it is denied everywhere in the surface EXCEPT the two
     attribution FIELDS that legitimately carry it (the manifest [plugin] author-name/author-email, and the
     generated plugin.json author.name/author.email). The rest of those two files, and their PATHNAMES, are
     scanned normally: the identity in a description, a comment, or a filename is a finding. Both the name
     and the email are matched through the leak gate's n-gram normalization, so a case-folded, spaced, or
     hyphenated spelling all match, and email matching is scoped to the OPERATOR address, not any address.
  C2 repo-operational vocabulary: the terms are public (they appear in the repo CLAUDE.md), so obscurity is
     the wrong tool; a small in-gate plaintext list is matched with the SAME n-gram normalization the leak
     gate uses (imported _tokens/ngram_forms), so spaced/hyphenated/camelCase variants all match one term.
     The relative PATHNAME is scanned too, so a file named session-handoff.md is a finding on its name alone.
  C3 unscannable content: check_leaks silently skips binaries and non-UTF-8; in the shippable surface that
     is a fail-open, so here every file outside the text-suffix set (or failing UTF-8 decode) must be on the
     binary allow-list or it is a finding, an allow-listed binary is OPENED so an unreadable one fails
     closed, and a symlink or unsupported entry type under a shipped root is itself a finding.
  C4 surface existence and walkability: check_leaks has no concept of a required surface; here every scan
     root must exist and walk cleanly (an absent or unlistable root is fail-closed, never "nothing to scan"),
     and enumeration under a shipped root applies NO skip rules, so nothing under it is silently unscanned.
  C5 no shipped exemption marker: a `leak-allow` marker inside shipped content would ship the exemption and
     weaken the leak gate's structural layer wherever the content lands, so its presence is a finding.

What portability deliberately does NOT do (overlap stated honestly, the way check_leaks states its scope):
no host-path patterns (check_leaks covers its four structural roots repo-wide, a superset of this surface,
and a broader pattern would false-positive on the hook script's legitimate /usr/bin/git, /tmp/x, /dev/tty,
and shebang lines); no hashed-codename re-scan; no secret or IP scanning (check_secrets, gitleaks, and
check_leaks own those repo-wide).

Placement discipline (stated in both gate headers): a PRIVATE term goes in the check_leaks hashed denylist;
a PUBLIC-but-operational term goes HERE; never both. Exemptions live ONLY in this gate source (no inline
marker of any kind), so every exemption is a reviewed code change and never travels with shipped content.

  check_portability.py               scan the shippable surface (default: the repo root above tools/)
  check_portability.py --self-test   deterministic self-test: the finding-1..5 regressions run in memory with
                                      no filesystem dependency and ALWAYS run; a real-surface end-to-end layer
                                      runs too where a writable tempdir exists and is reported PARTIAL where not

Exit convention (matches the repo's gates):
  0  clean
  1  a real finding (operator identity, operational vocabulary, an exemption marker, or an unscannable file)
  2  an unreadable identity source, an absent or unwalkable required root, an unreadable allow-listed binary,
     or a read error (fail-closed). A text file that does not decode as UTF-8 is the ONE read outcome that is
     a C3 finding (exit 1), not a fail-closed exit 2, since an unscannable shipped file is itself the thing
     this gate is asserting against.
"""
import os
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: check_portability.py requires Python 3.11+ (tomllib).")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_leaks import _tokens, ngram_forms  # noqa: E402  reuse the leak gate's n-gram normalization


class GateError(Exception):
    """An input the gate cannot read, resolve, or walk. Caught at run() and reported as exit 2
    (fail-closed): an unreadable identity source, an absent/unwalkable root, or an unreadable
    allow-listed binary is never treated as clean."""


# The shippable surface. Every root is REQUIRED: an absent or unwalkable root in the authoring repo can
# never read as "nothing to check" (a stranger's install is validated separately, by conformance.py).
REQUIRED_DIR_ROOTS = [
    ".aiqt/core",                        # rules, hooks and scripts, chat-skill source, conformance checklist
    ".claude/rules",                     # the generated read tree
    ".cursor/rules/aiqt-guardrails",     # the generated Cursor tree
    "plugin/aiqt-guardrails-hooks",      # the shipped plugin surface
    "site/downloads",                    # the published artefacts (skill dir, instructions, zip, mappings)
]
REQUIRED_FILE_ROOTS = [
    "AGENTS.md",                         # generated adapter
    "GEMINI.md",                         # generated adapter
    ".github/copilot-instructions.md",   # generated adapter
    "barebones-claude.md",               # a shipped starter file, in scope (GD-46)
]

# The single source of the operator identity (the same file the hooks generator renders from). Read at
# runtime so no personal data is hardcoded in this gate; an absent or unparseable source is fail-closed.
IDENTITY_MANIFEST = ".aiqt/core/hooks/manifest.toml"
PLUGIN_JSON = "plugin/aiqt-guardrails-hooks/.claude-plugin/plugin.json"

# The operator identity is legitimate ONLY in the specific attribution FIELDS of these two files: the
# manifest [plugin] author-name/author-email, and the plugin.json author.name/author.email the hooks
# generator renders from them. Everywhere ELSE in these files (a comment, a description) and everywhere else
# in the surface, the identity is a finding; attribution_lines() resolves the exact allowed source lines.
# An adopter keeps the original attribution (CC BY-SA), so these fields ship the identity by design.
IDENTITY_ALLOW = {IDENTITY_MANIFEST, PLUGIN_JSON}

# The only shippable file that is not scannable text. It is byte-reconciled by gen_skill.py --check from
# sources this gate DOES scan, so its content portability follows transitively; it is still OPENED here so
# an unreadable copy fails closed rather than passing silently.
BINARY_ALLOW = {"site/downloads/aiqt-skill.zip"}

# Repo-operational vocabulary. Each term is public but names this repo's own operating machinery, so a
# stranger's install must not carry it. Normalized through the leak gate's _tokens, so a spaced, hyphenated,
# underscored, or camelCase spelling all match one term (for example CLAUDE.local.md matches claude-local).
OPERATIONAL_TERMS = [
    "claude-local",       # the private machine-local memory file (CLAUDE.local.md)
    "session-handoff",    # the orchestrator handoff record
    "session-state",      # the concurrency-lease state record
    "open-findings",      # the operational defect register
    "design-of-record",   # the private design-of-record
]

# Text suffixes scanned as content. Mirrors check_leaks.TEXT_SUFFIXES, plus .csv (the shippable mappings
# artefact, which check_leaks treats as non-text but which is portable text this gate does scan).
TEXT_SUFFIXES = {".md", ".mdc", ".py", ".sh", ".yml", ".yaml", ".toml", ".json", ".txt",
                 ".html", ".css", ".js", ".cfg", ".ini", ".conf", ".svg", ".csv"}


# --- pure logic (always run in --self-test) ---------------------------------------------------------

def normalize_term(term):
    """The canonical hyphen-joined token form of a term, using the leak gate's normalization, so every
    spelling variant of the same term collapses to one gram (session handoff -> session-handoff)."""
    return "-".join(_tokens(term))


def identity_deny_forms(name, email):
    """The case-folded, normalization-collapsed deny forms of the operator identity, derived from BOTH the
    loaded name and the loaded email through the leak gate's _tokens (so 'Jeff Posluns', 'jeff  posluns',
    and 'jeff-posluns' collapse to one form, and 'jeff@posluns.ca' to another). Matched as n-grams, so only
    the full identity trips, never a lone common first name, and the email form is the OPERATOR address."""
    forms = set()
    for value in (name, email):
        gram = normalize_term(value)
        if gram:
            forms.add(gram)
    return forms


def find_operational_terms(text, term_grams, maxn):
    """Return the sorted operational grams present in text (matched as 1..maxn token n-grams, so a term
    matches regardless of the spacing, hyphenation, or casing it is written with)."""
    grams = ngram_forms(text, maxn)
    return sorted(t for t in term_grams if t in grams)


def find_identity(text, ident_forms, ident_maxn):
    """Return the sorted operator-identity deny forms present in text (name and/or email), matched as
    1..ident_maxn token n-grams, so a case-folded or reformatted spelling still matches."""
    grams = ngram_forms(text, ident_maxn)
    return sorted(f for f in ident_forms if f in grams)


def attribution_lines(rel, text):
    """The set of 1-based line numbers of rel on which the operator identity ships by design: the [plugin]
    author-name/author-email fields of the manifest, and the author.name/author.email fields of plugin.json.
    Every OTHER line of these files, and every line of every other file, is scanned for the identity
    normally. A file that is not an attribution file returns the empty set."""
    if rel == IDENTITY_MANIFEST:
        return _toml_plugin_author_lines(text)
    if rel == PLUGIN_JSON:
        return _json_author_lines(text)
    return set()


def _toml_plugin_author_lines(text):
    """Line numbers of the author-name/author-email keys inside the manifest [plugin] table (and only
    there): a [[hook]] table or any other section is not an attribution location."""
    allowed, section = set(), None
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        header = re.match(r'\[+\s*([^\]]+?)\s*\]+', stripped)
        if header:
            section = header.group(1)
            continue
        if section == "plugin" and re.match(r'author-(?:name|email)\s*=', stripped):
            allowed.add(number)
    return allowed


def _json_author_lines(text):
    """Line numbers of the name/email keys inside the plugin.json top-level author object, assuming the
    generator's multiline object shape (one key per line). An unrecognized shape yields no allowed lines,
    which errs safe: the identity in the attribution field is then FLAGGED (the gate fails, never passes)."""
    allowed, in_author, depth = set(), False, 0
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not in_author:
            if re.match(r'"author"\s*:', stripped):
                in_author = True
                depth = line.count("{") - line.count("}")
            continue
        if re.match(r'"(?:name|email)"\s*:', stripped):
            allowed.add(number)
        depth += line.count("{") - line.count("}")
        if depth <= 0:
            in_author = False
    return allowed


def scan_text(rel, text, ident_forms, ident_maxn, term_grams, maxn, allowed_lines=frozenset()):
    """Scan one file's text for C1 (operator identity), C2 (operational vocabulary), and C5 (exemption
    marker). allowed_lines are the attribution field lines of an allow-listed file, where the identity
    ships by design; a C1 identity match on any OTHER line is a finding, and C2/C5 apply everywhere.
    Returns a list of finding strings."""
    findings = []
    lines = text.splitlines()
    for number, line in enumerate(lines, 1):
        if number in allowed_lines:
            continue
        hits = find_identity(line, ident_forms, ident_maxn)
        if hits:
            findings.append("{}:{}: operator identity ({}) in shipped content (portability C1)".format(
                rel, number, ", ".join(hits)))
    for term in find_operational_terms(text, term_grams, maxn):
        findings.append("{}: repo-operational term {!r} (portability C2)".format(rel, term))
    for number, line in enumerate(lines, 1):
        if "leak-allow" in line:
            findings.append("{}:{}: shipped leak-allow exemption marker (portability C5)".format(rel, number))
    return findings


def scan_pathname(rel, ident_forms, ident_maxn, term_grams, maxn):
    """Scan a file's relative PATHNAME for C1 (operator identity) and C2 (operational vocabulary): a file
    NAMED session-handoff.md, or one whose path carries the operator identity, is non-portable even when its
    bytes are clean. A pathname is never an attribution field, so the identity is never allowed here."""
    findings = []
    hits = find_identity(rel, ident_forms, ident_maxn)
    if hits:
        findings.append("{}: operator identity ({}) in shipped pathname (portability C1)".format(
            rel, ", ".join(hits)))
    for term in find_operational_terms(rel, term_grams, maxn):
        findings.append("{}: repo-operational term {!r} in shipped pathname (portability C2)".format(rel, term))
    return findings


def _classify_entry(entry):
    """Classify one directory entry by TYPE, following no symlink: 'reject-symlink' for a symlink,
    'dir' for a real subdirectory (descended, with NO skip rules), 'file' for a regular file, and
    'reject-type' for anything else (fifo, socket, device). Takes anything exposing is_symlink /
    is_dir(follow_symlinks=False) / is_file(follow_symlinks=False), so an injected entry can test it."""
    if entry.is_symlink():
        return "reject-symlink"
    if entry.is_dir(follow_symlinks=False):
        return "dir"
    if entry.is_file(follow_symlinks=False):
        return "file"
    return "reject-type"


# --- identity source, surface gathering, per-file scan ----------------------------------------------

def load_identity(root):
    """Read the operator name and email from the manifest [plugin] block. An absent, unreadable, or
    unparseable source, or a missing name/email, is fail-closed (GateError -> exit 2): a deny input that
    cannot be read must never scan as an empty deny."""
    path = root / IDENTITY_MANIFEST
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise GateError("cannot read the identity source {} ({})".format(IDENTITY_MANIFEST, exc))
    except tomllib.TOMLDecodeError as exc:
        raise GateError("identity source {} does not parse ({})".format(IDENTITY_MANIFEST, exc))
    plugin = data.get("plugin")
    if not isinstance(plugin, dict):
        raise GateError("identity source {} has no [plugin] table".format(IDENTITY_MANIFEST))
    name = plugin.get("author-name")
    email = plugin.get("author-email")
    if not isinstance(name, str) or not name.strip():
        raise GateError("identity source {} has no author-name".format(IDENTITY_MANIFEST))
    if not isinstance(email, str) or not email.strip():
        raise GateError("identity source {} has no author-email".format(IDENTITY_MANIFEST))
    return name, email


def _walk_dir_root(base, root, files, findings):
    """Recursively account for EVERY entry under base (a required dir root), following no symlink and
    applying NO skip rules, so nothing under a shipped root (not even node_modules/__pycache__/.git) is
    silently unscanned. A regular file is collected to scan; a symlink or unsupported entry type is a C3
    finding; a real subdirectory is descended. os.scandir raises OSError on an unlistable directory, which
    the caller converts to fail-closed exit 2."""
    with os.scandir(base) as it:
        entries = sorted(it, key=lambda e: e.name)
    for entry in entries:
        path = Path(entry.path)
        rel = path.relative_to(root).as_posix()
        kind = _classify_entry(entry)
        if kind == "dir":
            _walk_dir_root(path, root, files, findings)
        elif kind == "file":
            files.append(path)
        elif kind == "reject-symlink":
            findings.append("{}: symlink in shipped content is not portable (portability C3)".format(rel))
        else:
            findings.append("{}: unsupported file type in shipped content is not portable "
                            "(portability C3)".format(rel))


def gather_surface(root):
    """Return (files, findings): every regular file in the shippable surface to scan, plus any C3 finding
    for a symlink or unsupported entry type under a required root. Each required dir root must exist as a
    real directory and list cleanly, and each required file root must exist (a symlink there is a C3
    finding); anything absent or unlistable is fail-closed (GateError, or an OSError from os.scandir on an
    unlistable subtree, both caught at run() as exit 2)."""
    files, findings = [], []
    for rel in REQUIRED_DIR_ROOTS:
        d = root / rel
        if d.is_symlink() or not d.is_dir():
            raise GateError("required surface root {} is absent, a symlink, or not a directory".format(rel))
        _walk_dir_root(d, root, files, findings)
    for rel in REQUIRED_FILE_ROOTS:
        f = root / rel
        if f.is_symlink():
            findings.append("{}: symlink in shipped content is not portable (portability C3)".format(rel))
            continue
        if not f.is_file():
            raise GateError("required surface file {} is absent".format(rel))
        files.append(f)
    return files, findings


def scan_file(root, path, ident_forms, ident_maxn, term_grams, maxn):
    """Scan one surface file: its relative PATHNAME always (C1/C2), and its CONTENT unless it is an
    allow-listed binary. An allow-listed binary is OPENED and read so an unreadable one fails closed
    (GateError -> exit 2), never a silent clean pass. A non-text suffix or a UTF-8 decode failure off the
    allow-list is a C3 finding (exit 1), never fail-closed exit 2: an unscannable shipped file is exactly
    what this gate asserts against. Returns a list of finding strings."""
    rel = path.relative_to(root).as_posix()
    findings = scan_pathname(rel, ident_forms, ident_maxn, term_grams, maxn)
    if rel in BINARY_ALLOW:
        try:
            with open(path, "rb") as handle:
                handle.read()
        except OSError as exc:
            raise GateError("cannot read allow-listed binary {} ({})".format(rel, exc))
        return findings
    if path.suffix not in TEXT_SUFFIXES:
        findings.append("{}: non-portable file class (suffix {!r} is not scannable text and is not on the "
                        "binary allow-list) (portability C3)".format(rel, path.suffix))
        return findings
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        findings.append("{}: shipped text file is not valid UTF-8 and is not on the binary allow-list "
                        "(portability C3)".format(rel))
        return findings
    findings.extend(scan_text(rel, text, ident_forms, ident_maxn, term_grams, maxn,
                              allowed_lines=attribution_lines(rel, text)))
    return findings


def run(root):
    """Scan the shippable surface under root. Returns the exit code 0/1/2."""
    try:
        name, email = load_identity(root)
        ident_forms = identity_deny_forms(name, email)
        ident_maxn = max(len(_tokens(name)), len(_tokens(email)))
        term_grams = {normalize_term(t) for t in OPERATIONAL_TERMS}
        maxn = max(len(_tokens(t)) for t in OPERATIONAL_TERMS)
        files, findings = gather_surface(root)
        for path in sorted(files):
            findings.extend(scan_file(root, path, ident_forms, ident_maxn, term_grams, maxn))
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        # An unlistable directory or an unreadable file on a required root is a read failure on a required
        # input, not a clean skip: fail closed so an unreadable surface can never read as portable.
        print("error: cannot read a required surface input ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    print("scanned surface roots: {} ({} files)".format(
        ", ".join(REQUIRED_DIR_ROOTS + REQUIRED_FILE_ROOTS), len(files)))
    if findings:
        print("FAIL: {} portability finding(s) in the shippable surface".format(len(findings)))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    print("PASS: the shippable surface is portable (operator identity only in its attribution fields, no "
          "repo-operational vocabulary in content or pathnames, no exemption markers, no unscannable file "
          "classes, symlinks, or unsupported entry types off the allow-list)")
    return 0


# --- self-test --------------------------------------------------------------------------------------
# The finding-1..5 regressions are pure in-memory cases (field-scoped identity keying, case-folded and
# operator-scoped matching, pathname scanning, symlink/type classification, and the unreadable-binary
# fail-closed path against a NON-existent allow-listed path). They ALWAYS run: no writable tempdir, no
# permissions, no wall clock, no randomness. A real-surface end-to-end layer runs additionally where a
# writable tempdir exists and is reported PARTIAL (never a full PASS) where it cannot.

_CLEAN_MD = "# Heading\n\nPortable governance content with no operator identity and no operating vocabulary.\n"


class _FakeEntry:
    """A stand-in directory entry for classifying an entry TYPE without a real filesystem entry, so the
    symlink/type rejection logic (finding 3) runs deterministically in memory."""

    def __init__(self, symlink=False, isdir=False, isfile=False):
        self._symlink, self._dir, self._file = symlink, isdir, isfile

    def is_symlink(self):
        return self._symlink

    def is_dir(self, follow_symlinks=True):
        return self._dir

    def is_file(self, follow_symlinks=True):
        return self._file


def _build_surface(base, name, email):
    """Create a minimal clean-but-complete shippable surface under base and return it."""
    for rel in REQUIRED_DIR_ROOTS:
        d = base / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / "placeholder.md").write_text(_CLEAN_MD, encoding="utf-8")
    manifest = base / IDENTITY_MANIFEST
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        '[plugin]\nname = "aiqt-guardrails-hooks"\nversion = "0.1.0"\n'
        'author-name = "{}"\nauthor-email = "{}"\n'.format(name, email), encoding="utf-8")
    plugin_json = base / PLUGIN_JSON
    plugin_json.parent.mkdir(parents=True, exist_ok=True)
    plugin_json.write_text(
        '{{\n  "name": "aiqt-guardrails-hooks",\n  "author": {{\n    "name": "{}",\n'
        '    "email": "{}"\n  }}\n}}\n'.format(name, email), encoding="utf-8")
    for rel in REQUIRED_FILE_ROOTS:
        f = base / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(_CLEAN_MD, encoding="utf-8")
    (base / "site/downloads/aiqt-skill.zip").write_bytes(b"PK\x03\x04 synthetic zip bytes, not text")
    return base


def self_test_main():
    name, email = "Jeff Posluns", "jeff@posluns.ca"
    ident_forms = identity_deny_forms(name, email)
    ident_maxn = max(len(_tokens(name)), len(_tokens(email)))
    term_grams = {normalize_term(t) for t in OPERATIONAL_TERMS}
    maxn = max(len(_tokens(t)) for t in OPERATIONAL_TERMS)
    failures = []

    # --- always-run deterministic regressions (no filesystem, no tempdir) --------------------------

    # Term normalization: every spelling variant of a term collapses to one gram, and the camelCase form
    # is matched inside text.
    for variant in ["session-handoff", "session handoff", "session_handoff", "SessionHandoff"]:
        if normalize_term(variant) != "session-handoff":
            failures.append("normalize_term({!r}) != 'session-handoff'".format(variant))
    if "claude-local" not in find_operational_terms("see CLAUDE.local.md today", {"claude-local"}, 3):
        failures.append("find_operational_terms missed CLAUDE.local.md -> claude-local")
    if find_operational_terms("wholly portable prose", term_grams, maxn):
        failures.append("find_operational_terms found a term in clean prose")

    # Finding 1: FIELD-scoped identity. In the manifest, the identity is allowed ONLY on its [plugin]
    # author-name/author-email lines; the SAME identity in a description field on another line fails.
    manifest_text = (
        '[plugin]\nname = "aiqt-guardrails-hooks"\n'
        'author-name = "{0}"\nauthor-email = "{1}"\n'
        'description = "governance authored by {0}"\n'.format(name, email))
    mf = scan_text(IDENTITY_MANIFEST, manifest_text, ident_forms, ident_maxn, term_grams, maxn,
                   attribution_lines(IDENTITY_MANIFEST, manifest_text))
    mf_c1 = [f for f in mf if "portability C1" in f]
    if any(":3:" in f or ":4:" in f for f in mf_c1):
        failures.append("finding 1: identity flagged in its own [plugin] attribution field (should allow)")
    if not any(":5:" in f for f in mf_c1):
        failures.append("finding 1: identity in a non-attribution manifest field was not flagged")

    # Finding 1 (plugin.json): the identity is allowed only inside the author object; a description carrying
    # it fails, and the top-level package "name" is not the identity so it never trips.
    pj_text = (
        '{{\n  "name": "aiqt-guardrails-hooks",\n  "author": {{\n'
        '    "name": "{0}",\n    "email": "{1}"\n  }},\n'
        '  "description": "by {0}"\n}}\n'.format(name, email))
    pj = scan_text(PLUGIN_JSON, pj_text, ident_forms, ident_maxn, term_grams, maxn,
                   attribution_lines(PLUGIN_JSON, pj_text))
    pj_c1 = [f for f in pj if "portability C1" in f]
    if any(":4:" in f or ":5:" in f for f in pj_c1):
        failures.append("finding 1: identity flagged in the plugin.json author object (should allow)")
    if not any(":7:" in f for f in pj_c1):
        failures.append("finding 1: identity in a non-attribution plugin.json field was not flagged")

    # Finding 2: a LOWERCASE operator name in ordinary content is flagged (case-folded matching), the
    # operator email is flagged, and a non-operator example address is NOT (email scoped to the operator).
    if not scan_text(".claude/rules/x.md", "authored by jeff posluns", ident_forms, ident_maxn,
                     term_grams, maxn):
        failures.append("finding 2: a lowercase operator name was not flagged")
    if not scan_text(".claude/rules/x.md", "contact {}".format(email), ident_forms, ident_maxn,
                     term_grams, maxn):
        failures.append("finding 2: the operator email was not flagged outside its attribution")
    if scan_text(".claude/rules/x.md", "reach user@example.com anytime", ident_forms, ident_maxn,
                 term_grams, maxn):
        failures.append("finding 2: a non-operator email was flagged (email scope too broad)")

    # Finding 3: a pathname operational term fails even with clean bytes; a clean pathname passes; and the
    # symlink/type classification rejects a symlink and an unsupported type while descending a dir.
    if not scan_pathname(".claude/rules/session-handoff.md", ident_forms, ident_maxn, term_grams, maxn):
        failures.append("finding 3: an operational term in a pathname was not flagged")
    if scan_pathname(".claude/rules/portable-note.md", ident_forms, ident_maxn, term_grams, maxn):
        failures.append("finding 3: a clean pathname was flagged")
    if _classify_entry(_FakeEntry(symlink=True)) != "reject-symlink":
        failures.append("finding 3: a symlink entry was not rejected")
    if _classify_entry(_FakeEntry(isdir=True)) != "dir":
        failures.append("finding 3: a real directory entry was not descended")
    if _classify_entry(_FakeEntry(isfile=True)) != "file":
        failures.append("finding 3: a regular file entry was not collected")
    if _classify_entry(_FakeEntry()) != "reject-type":
        failures.append("finding 3: an unsupported entry type was not rejected")

    # Finding 4: an unreadable allow-listed binary fails closed (GateError -> exit 2), never a silent clean
    # pass. A path ON the binary allow-list that does not exist makes open() raise, deterministically.
    missing_root = Path("/nonexistent-aiqt-portability-selftest")
    missing_bin = missing_root / "site/downloads/aiqt-skill.zip"
    try:
        scan_file(missing_root, missing_bin, ident_forms, ident_maxn, term_grams, maxn)
        failures.append("finding 4: an unreadable allow-listed binary did not fail closed")
    except GateError:
        pass

    # Finding 5 companion: a shipped leak-allow exemption marker (C5) is flagged.
    if not scan_text(".claude/rules/m.md", "this line carries a leak-allow marker", ident_forms,
                     ident_maxn, term_grams, maxn):
        failures.append("a shipped leak-allow marker (C5) was not flagged")

    # --- real-surface end-to-end layer (runs where a writable tempdir exists) ----------------------
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stderr, redirect_stdout

    def _run_quiet(sroot):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return run(sroot)

    e2e_name, e2e_email = "Test Operator", "operator@example.invalid"
    tmp_ran = False
    unreadable_dir_ran = False
    try:
        base_tmp = Path(tempfile.mkdtemp(prefix="aiqt-portability-selftest-"))
    except OSError:
        base_tmp = None

    if base_tmp is not None:
        tmp_ran = True

        def _fresh(tag):
            return _build_surface(base_tmp / tag, e2e_name, e2e_email)

        try:
            # (a) a clean surface, identity present ONLY in the two attribution fields, passes.
            if _run_quiet(_fresh("clean")) != 0:
                failures.append("e2e: a clean synthetic surface (identity in its attribution fields) "
                                "expected exit 0")

            # (b) the operator email planted in a rule fails (email scoped to the operator address).
            s = _fresh("planted-email")
            (s / ".claude/rules/leak.md").write_text(
                "# Rule\n\nquestions to {}\n".format(e2e_email), encoding="utf-8")
            if _run_quiet(s) != 1:
                failures.append("e2e: the operator email planted in a rule expected exit 1")

            # (c) the operator name planted (lowercase) outside the attribution fields fails.
            s = _fresh("planted-name")
            (s / "AGENTS.md").write_text(
                "# Adapter\n\nauthored by {}\n".format(e2e_name.lower()), encoding="utf-8")
            if _run_quiet(s) != 1:
                failures.append("e2e: the lowercase operator name outside its attribution expected exit 1")

            # (d) a camel-cased operational term fails.
            s = _fresh("camel-term")
            (s / ".aiqt/core/placeholder.md").write_text(
                "# Note\n\nrun the SessionHandoff at close\n", encoding="utf-8")
            if _run_quiet(s) != 1:
                failures.append("e2e: a camel-cased operational term expected exit 1")

            # (e) a pathname operational term fails on the NAME alone, with clean bytes.
            s = _fresh("pathname-term")
            (s / ".claude/rules/session-handoff.md").write_text(_CLEAN_MD, encoding="utf-8")
            if _run_quiet(s) != 1:
                failures.append("e2e: a file NAMED session-handoff.md expected exit 1")

            # (f) an unknown non-text file fails while the allow-listed zip (present in every surface) passes.
            s = _fresh("unknown-binary")
            (s / "site/downloads/logo.png").write_bytes(b"\x89PNG\r\n\x1a\n not text")
            if _run_quiet(s) != 1:
                failures.append("e2e: an unknown .png in the surface expected exit 1")

            # (g) a planted leak-allow exemption marker fails.
            s = _fresh("planted-marker")
            (s / ".claude/rules/marked.md").write_text(
                "# Rule\n\nthis line carries a leak-allow marker\n", encoding="utf-8")
            if _run_quiet(s) != 1:
                failures.append("e2e: a shipped leak-allow marker expected exit 1")

            # (h) NO skip rules: a clean-named file with an operational term inside a __pycache__ directory
            # under a shipped root is now enumerated and fails (the old walk pruned it).
            s = _fresh("no-skip")
            pycache = s / ".aiqt/core/__pycache__"
            pycache.mkdir(parents=True, exist_ok=True)
            (pycache / "note.md").write_text("# x\n\nrun the SessionHandoff\n", encoding="utf-8")
            if _run_quiet(s) != 1:
                failures.append("e2e: content under a __pycache__ dir was not enumerated (skip rule leaked)")

            # (i) a symlink under a shipped root is a C3 finding (exit 1), never a silently skipped subtree.
            s = _fresh("symlink")
            try:
                os.symlink(s / ".aiqt/core/placeholder.md", s / ".claude/rules/link.md")
                symlink_made = True
            except OSError:
                symlink_made = False
            if symlink_made:
                if _run_quiet(s) != 1:
                    failures.append("e2e: a symlink under a shipped root expected exit 1")
            else:
                print("SELF-TEST NOTE: symlinks unsupported here; the e2e symlink case was SKIPPED "
                      "(the deterministic classify-symlink regression above still ran)", file=sys.stderr)

            # (j) an unreadable allow-listed binary is fail-closed exit 2.
            s = _fresh("unreadable-binary")
            zip_path = s / "site/downloads/aiqt-skill.zip"
            os.chmod(zip_path, 0)
            try:
                with open(zip_path, "rb"):
                    zip_readable = True
            except PermissionError:
                zip_readable = False
            if zip_readable:
                print("SELF-TEST NOTE: an unreadable file could not be produced (running as root?); the "
                      "e2e unreadable-binary case was SKIPPED (the deterministic fail-closed regression "
                      "above still ran)", file=sys.stderr)
            else:
                if _run_quiet(s) != 2:
                    failures.append("e2e: an unreadable allow-listed binary expected fail-closed exit 2")
            os.chmod(zip_path, 0o644)

            # (k) a missing required root is fail-closed exit 2.
            s = _fresh("missing-root")
            shutil.rmtree(s / ".cursor/rules/aiqt-guardrails")
            if _run_quiet(s) != 2:
                failures.append("e2e: a missing required root expected fail-closed exit 2")

            # (l) an absent identity manifest is fail-closed exit 2.
            s = _fresh("no-manifest")
            (s / IDENTITY_MANIFEST).unlink()
            if _run_quiet(s) != 2:
                failures.append("e2e: an absent identity manifest expected fail-closed exit 2")

            # (m) an unreadable directory under a required root is fail-closed exit 2. Skipped with a note
            # where the environment cannot produce one (for example running as root, which reads regardless).
            s = _fresh("unreadable-dir")
            locked = s / ".aiqt/core/locked"
            locked.mkdir()
            (locked / "f.md").write_text(_CLEAN_MD, encoding="utf-8")
            os.chmod(locked, 0)
            try:
                os.listdir(locked)
                readable = True
            except PermissionError:
                readable = False
            if readable:
                print("SELF-TEST NOTE: an unreadable directory could not be produced (running as root?); "
                      "the e2e unreadable-dir case was SKIPPED", file=sys.stderr)
            else:
                unreadable_dir_ran = True
                if _run_quiet(s) != 2:
                    failures.append("e2e: an unreadable directory expected fail-closed exit 2")
            os.chmod(locked, 0o755)  # restore so the tree removes cleanly
        finally:
            shutil.rmtree(base_tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    core = ("the finding-1..5 regressions ran in memory and hold (field-scoped identity, case-folded and "
            "operator-scoped matching, pathname scanning, symlink/type classification, unreadable-binary "
            "fail-closed, and the exemption marker)")
    if tmp_ran:
        e2e = ("the end-to-end surface cases hold (clean pass, planted identity/email/vocabulary/pathname/"
               "marker/unknown-file findings, no-skip enumeration, missing-root and absent-manifest "
               "fail-closed); the unreadable-dir exit-2 case " + ("ran" if unreadable_dir_ran else "was SKIPPED"))
        print("SELF-TEST PASS: {}; {}".format(core, e2e))
    else:
        print("SELF-TEST PASS (PARTIAL): {}; the end-to-end surface cases were SKIPPED (no writable temp "
              "directory), so those integration invariants are UNVERIFIED this run".format(core))
    return 0


def _parse_args(argv):
    self_test = False
    i = 0
    while i < len(argv):
        if argv[i] == "--self-test":
            self_test = True
            i += 1
        else:
            print("usage: check_portability.py [--self-test]", file=sys.stderr)
            return None
    return (self_test,)


def main():
    parsed = _parse_args(sys.argv[1:])
    if parsed is None:
        return 2
    (self_test,) = parsed
    if self_test:
        return self_test_main()
    root = Path(__file__).resolve().parents[1]
    return run(root)


if __name__ == "__main__":
    sys.exit(main())
