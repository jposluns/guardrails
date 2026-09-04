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
     A second, NARROW attribution exemption (GD-56) permits the operator NAME in one exact public
     attribution line, and only in the two published chat artefacts (the generated SKILL.md and the
     aiqt-instructions.txt fallback): mask_attribution_line() is LINE-ANCHORED and clears only a standalone
     line whose full content equals that exact string, and only when the artefact carries exactly one such
     line, so a wrapped or near-variant line, a duplicate copy, the email, or the name anywhere else in the
     surface still trips C1 (a wrong count is itself a placement finding). An @-prefixed operator personal
     handle (find_handle) is also a C1 finding, while the bare owner segment inside a legitimate source URL
     (github.com/<owner>/...) is not, so the attribution URL and the shipped mappings stay clean.
  C2 repo-operational vocabulary: the terms are public (they appear in the repo CLAUDE.md), so obscurity is
     the wrong tool; a small in-gate plaintext list is matched with the SAME n-gram normalization the leak
     gate uses (imported _tokens/ngram_forms), so spaced/hyphenated/camelCase variants all match one term.
     The relative PATHNAME is scanned too, so a file named session-handoff.md is a finding on its name alone.
  C3 unscannable content: check_leaks silently skips binaries and non-UTF-8; in the shippable surface that
     is a fail-open, so here every file outside the text-suffix set (or failing UTF-8 decode) must be on the
     binary allow-list or it is a finding, an allow-listed binary is OPENED so an unreadable one fails
     closed, and a symlink or unsupported entry type under a shipped root is itself a finding.
  C4 tracked-surface existence and enumerability: check_leaks has no concept of a required surface; here
     every required root must be tracked-non-empty and enumerable from git (a failed enumeration, an absent
     or empty root, or an untracked required file is fail-closed, never "nothing to scan"), and enumeration
     applies NO skip rules over the tracked set, so no shipped file is silently unscanned; untracked and
     ignored working-tree paths are outside the surface because they do not ship.
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
  check_portability.py --self-test   deterministic self-test: the finding-1..5 regressions (including
                                      value-span masking, the whole-document cross-line identity catch, the
                                      malformed-attribution fail-closed, the injected-lister enumeration,
                                      symlink/type and unreadable-binary cases, and the GD-130 tracked/untracked
                                      stray-artifact pair) run in memory with no
                                      filesystem dependency and ALWAYS run; a real-surface end-to-end layer
                                      runs too where a writable tempdir exists and is reported PARTIAL where not

Exit convention (matches the repo's gates):
  0  clean
  1  a real finding (operator identity, operational vocabulary, an exemption marker, or an unscannable file)
  2  an unreadable identity source, an absent or unwalkable required root, a failed or unconfirmable
     tracked-file enumeration, a required file that is not git-tracked, an unreadable allow-listed binary,
     or a read error (fail-closed). A text file that does not decode as UTF-8 is the ONE read outcome that is
     a C3 finding (exit 1), not a fail-closed exit 2, since an unscannable shipped file is itself the thing
     this gate is asserting against.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: check_portability.py requires Python 3.11+ (tomllib).")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_leaks import _tokens, ngram_forms  # noqa: E402  reuse the leak gate's n-gram normalization


class GateError(Exception):
    """An input the gate cannot read, resolve, or enumerate. Caught at run() and reported as exit 2
    (fail-closed): an unreadable identity source, an absent/unwalkable root, an unconfirmable tracked
    surface, or an unreadable allow-listed binary is never treated as clean."""


# The shippable surface. Every root is REQUIRED: an absent, unwalkable, empty, or untracked root in the
# authoring repo can never read as "nothing to check" (a stranger's install is validated separately, by
# conformance.py).
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
    "aiqt-barebones.md",                 # a shipped starter file, in scope (GD-46)
]

# The single source of the operator identity (the same file the hooks generator renders from). Read at
# runtime so no personal data is hardcoded in this gate; an absent or unparseable source is fail-closed.
IDENTITY_MANIFEST = ".aiqt/core/hooks/manifest.toml"
PLUGIN_JSON = "plugin/aiqt-guardrails-hooks/.claude-plugin/plugin.json"

# The operator identity is legitimate ONLY in the specific attribution VALUES of these two files: the
# manifest [plugin] author-name/author-email values, and the plugin.json TOP-LEVEL author.name/author.email
# values the hooks generator renders from them. mask_identity_attribution() blanks ONLY those exact value
# spans; everywhere ELSE in these files (a comment, a description, an extra or nested-author field, or a
# value split across lines) and everywhere else in the surface, the identity is a finding. An adopter keeps
# the original attribution (CC BY-SA), so these values ship the identity by design.

# The only shippable file that is not scannable text. It is byte-reconciled by gen_skill.py --check from
# sources this gate DOES scan, so its content portability follows transitively; it is still OPENED here so
# an unreadable copy fails closed rather than passing silently.
BINARY_ALLOW = {"site/downloads/aiqt-skill.zip", "site/downloads/aiqt-skill-1.0.4.zip"}

# GD-56 attribution exemption (NARROW and REVIEWED; NOT a general operator-identity allowance). The
# maintainer deliberately attributes both the project and himself, by name, on the two PUBLISHED chat
# artefacts, under the CC BY-SA the pack ships. That one exact line therefore carries the operator NAME by
# design. attribution_line() rebuilds it from the loaded operator name plus the pack's public source URL
# (so no personal data is hardcoded here, the same reason load_identity reads the name at runtime), and
# mask_attribution_line() blanks ONLY a STANDALONE LINE whose full stripped content equals that exact
# string, and ONLY in ATTRIBUTION_EXEMPT_FILES, and ONLY when the artefact carries EXACTLY ONE such line,
# before the C1 identity scan. It is line-anchored, not a substring replace: the exact string embedded in a
# longer line (a prefix/suffix wrap), a near-variant (a trailing period), a duplicate copy, or a missing
# line all leave the identity to trip C1 and, for a wrong count, raise an explicit placement finding. The
# email is never part of the line and is never exempt anywhere; the identity in any OTHER wording, field,
# pathname, or file in the surface still trips C1. The source URL is the pack's public origin, the same one
# the reference registry and shipped mappings use.
ATTRIBUTION_SOURCE_URL = "https://github.com/jposluns/guardrails"
ATTRIBUTION_EXEMPT_FILES = {
    "site/downloads/aiqt/SKILL.md",           # the generated skill body the download zip carries
    "site/downloads/aiqt-instructions.txt",   # the same body wrapped for platforms with no Skills feature
}

# The generated SKILL.md carries, directly under its `# AIQT™` H1, a visible metadata header block whose
# `Author: <name>` line is a SECOND legitimate occurrence of the operator name (distinct from the GD-56
# footer attribution line). It is exempted with the SAME narrow, line-anchored, single-placement discipline
# as the footer line, and ONLY in SKILL.md (this rel), never in the instructions fallback or anywhere else.
SKILL_MD_REL = "site/downloads/aiqt/SKILL.md"


def attribution_line(name):
    """The single public attribution string GD-56 permits, built from the loaded operator NAME and the
    pack's public source URL. Exactly this string, on its own line, and only in ATTRIBUTION_EXEMPT_FILES,
    is masked before the C1 scan; nothing else is exempted."""
    return "AIQT Guardrails by {}, {}, CC BY-SA 4.0".format(name, ATTRIBUTION_SOURCE_URL)


def author_header_line(name):
    """The single SKILL.md header `Author: <name>` line that legitimately carries the operator name in the
    visible metadata block. The shipped line ends with a two-space CommonMark hard break (markdownlint MD009
    br_spaces=2, matching the cleanlanguage skill; byte-canon permits exactly this via its path-scoped
    [[hardbreak]] allowance for SKILL.md). The C1 mask (mask_attribution_line) compares a line's STRIPPED
    content, so this exempt string carries NO trailing whitespace and the two-space break is transparent to
    the match. Exactly this string, as a STANDALONE stripped line and only in SKILL_MD_REL, is masked before
    the C1 scan; nothing else is exempted. A break-style change that merges this into a neighbouring line
    (dropping the standalone break) makes the stripped body no longer equal it, so the name trips C1
    (fail-safe), never a silent pass."""
    return "Author: {}".format(name)


def mask_attribution_line(text, line):
    """LINE-ANCHORED, exact-match mask. Return (masked_text, count): blank with equal-length spaces (line
    and column offsets preserved) every STANDALONE line whose full stripped content equals the attribution
    string, and report how many such lines were found. A line that merely CONTAINS the string (a
    prefix/suffix wrap) or a near-variant (a trailing period, extra tokens) does NOT match, so its operator
    identity survives to trip C1. The caller trusts the masked text only when count == 1 (the expected
    single placement); a count of 0 or 2+ is left un-blanked so any identity still trips, plus a placement
    finding. Only ever applied to ATTRIBUTION_EXEMPT_FILES."""
    out, count = [], 0
    for physical in text.splitlines(keepends=True):
        body = physical.rstrip("\n")
        newline = physical[len(body):]
        if body.strip() == line:
            count += 1
            out.append((" " * len(body)) + newline)
        else:
            out.append(physical)
    return "".join(out), count


# Narrow, syntax-aware personal-handle check (round-1 QA finding 2). The name/email deny forms do not catch
# an @-prefixed social handle (for example @jposluns). The handle is the OWNER path segment of the public
# source URL (github.com/<owner>/...); it is flagged ONLY in its @-prefixed mention form, never as the bare
# owner token, because legitimate source URLs (the attribution line, references.toml, the shipped mappings)
# all carry the bare owner as github.com/<owner>/... and MUST stay clean. The @ must not sit directly after
# an alphanumeric or an email local-part character, so an address like x@jposluns.example is not mistaken
# for a handle mention, and a trailing alphanumeric/underscore is excluded so @ownerdev does not match.
def personal_handle(source_url):
    """The operator's personal handle, taken as the OWNER path segment of the pack's source URL
    (https://<host>/<owner>/<repo>...). Returns '' when no owner segment can be parsed."""
    body = source_url.split("://", 1)[-1]
    parts = [seg for seg in body.split("/") if seg]
    return parts[1] if len(parts) >= 2 else ""


PERSONAL_HANDLE = personal_handle(ATTRIBUTION_SOURCE_URL)
_HANDLE_RE = (re.compile(r'(?<![A-Za-z0-9._%+-])@' + re.escape(PERSONAL_HANDLE) + r'(?![A-Za-z0-9_])',
                         re.IGNORECASE) if PERSONAL_HANDLE else None)


def find_handle(text):
    """True when text carries an @-prefixed operator personal-handle mention (for example @jposluns), not a
    bare owner segment inside a URL and not an email local@handle form."""
    return bool(_HANDLE_RE and _HANDLE_RE.search(text))

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


def mask_identity_attribution(rel, text):
    """Return text with ONLY the operator-identity attribution VALUE spans blanked out: the manifest
    [plugin] author-name/author-email values, or plugin.json's TOP-LEVEL author.name/author.email values.
    Those are the two places the identity ships by design (CC BY-SA attribution). Every OTHER occurrence in
    these files (a comment, a description, an extra field, a NESTED non-top-level author, or a value split
    across lines) survives the mask and faces the whole-document C1 scan. Each masked span is replaced by
    equal-length spaces so line and column offsets elsewhere are preserved for the finding message.
    Malformed TOML/JSON in an attribution file is fail-closed (GateError -> exit 2): a deny input that
    cannot be parsed must never scan as clean. A non-attribution file is returned unchanged (no masking)."""
    if rel == IDENTITY_MANIFEST:
        return _mask_manifest_identity(text)
    if rel == PLUGIN_JSON:
        return _mask_plugin_json_identity(text)
    return text


def _blank_first(line, value):
    """Replace the FIRST occurrence of value in line with equal-length spaces and return the result. The
    value sits in the value position (right after the key's = or :), so the first occurrence is the field
    value; a later occurrence on the same line (a trailing comment, an extra field) is left to be scanned."""
    idx = line.find(value)
    if idx < 0:
        return line
    return line[:idx] + (" " * len(value)) + line[idx + len(value):]


def _mask_manifest_identity(text):
    """Blank the [plugin] author-name/author-email VALUE spans in the manifest TOML, and only those: a
    [[hook]] table or any other section is not an attribution location. Malformed TOML is fail-closed."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise GateError("attribution source {} does not parse ({})".format(IDENTITY_MANIFEST, exc))
    plugin = data.get("plugin") if isinstance(data, dict) else None
    values = {}
    if isinstance(plugin, dict):
        for key in ("author-name", "author-email"):
            value = plugin.get(key)
            if isinstance(value, str) and value.strip():
                values[key] = value
    if not values:
        return text
    out, section = [], None
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        header = re.match(r'\[+\s*([^\]]+?)\s*\]+', stripped)
        if header:
            section = header.group(1)
            out.append(line)
            continue
        masked = line
        if section == "plugin":
            key_match = re.match(r'\s*(author-(?:name|email))\s*=', line)
            if key_match and key_match.group(1) in values:
                masked = _blank_first(line, values[key_match.group(1)])
        out.append(masked)
    return "".join(out)


def _mask_plugin_json_identity(text):
    """Blank the TOP-LEVEL author.name/author.email VALUE spans in plugin.json, and only those: a nested
    (non-top-level) author is NOT exempt, so its identity survives the mask and is flagged. The value spans
    are located on the top-level author object's name/email lines (the generator's multiline shape, one key
    per line); an unrecognized shape masks nothing, which errs safe (the identity is then FLAGGED, never
    passed). Malformed JSON is fail-closed."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GateError("attribution source {} does not parse ({})".format(PLUGIN_JSON, exc))
    author = data.get("author") if isinstance(data, dict) else None
    values = {}
    if isinstance(author, dict):
        for key in ("name", "email"):
            value = author.get(key)
            if isinstance(value, str) and value.strip():
                values[key] = value
    if not values:
        return text
    out, depth, in_top_author, author_open_depth = [], 0, False, 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        masked = line
        if in_top_author:
            key_match = re.match(r'"(name|email)"\s*:', stripped)
            if key_match and key_match.group(1) in values:
                masked = _blank_first(line, values[key_match.group(1)])
        elif depth == 1 and re.match(r'"author"\s*:', stripped):
            in_top_author, author_open_depth = True, depth
        out.append(masked)
        depth += line.count("{") - line.count("}")
        if in_top_author and depth <= author_open_depth:
            in_top_author = False
    return "".join(out)


def scan_text(rel, text, ident_forms, ident_maxn, term_grams, maxn, attribution=None, header_line=None):
    """Scan one file's text for C1 (operator identity), C2 (operational vocabulary), and C5 (exemption
    marker). For the two attribution files the operator-identity VALUE spans are masked first (that identity
    ships by design); the identity is then matched over the WHOLE remaining document, so a same-line comment,
    an extra or nested-author field, or a value split across lines still trips C1. A non-attribution file is
    scanned whole with no masking. When attribution is given (the exact GD-56 line) and rel is one of the
    ATTRIBUTION_EXEMPT_FILES, a STANDALONE line equal to that exact string is blanked ONLY when the artefact
    carries EXACTLY ONE such line, so the operator name in that one public attribution line is not a finding
    while the same name in any other wording, a wrapped or near-variant line, or a duplicate copy still is
    (a wrong count also raises a placement finding). An @-prefixed operator personal handle is flagged too.
    A per-line pass names the exact line for a match sitting on one line; a whole-document pass then adds any
    form that only appears split across lines. C2 and C5 apply to the whole document. Masking a malformed
    attribution file is fail-closed (GateError -> exit 2). Returns a list of finding strings."""
    findings = []
    masked = mask_identity_attribution(rel, text)
    if attribution and rel in ATTRIBUTION_EXEMPT_FILES:
        blanked, count = mask_attribution_line(masked, attribution)
        if count == 1:
            masked = blanked  # the single expected attribution line clears
        else:
            # 0 or 2+: do NOT trust the mask, so any identity in a missing/duplicated/malformed attribution
            # still trips C1 below; and the wrong placement is itself a finding.
            findings.append("{}: exempt attribution artefact must carry exactly one standalone GD-56 "
                            "attribution line, found {} (portability C1)".format(rel, count))
    if header_line and rel == SKILL_MD_REL:
        # The SKILL.md header `Author: <name>` line, masked with the SAME line-anchored, single-placement
        # discipline as the attribution line above and ONLY in SKILL.md: on exactly one standalone match
        # the operator name in the header block clears; a missing, wrapped, near-variant, or duplicated
        # line leaves the identity to trip C1 and raises a placement finding.
        h_blanked, h_count = mask_attribution_line(masked, header_line)
        if h_count == 1:
            masked = h_blanked
        else:
            findings.append("{}: exempt chat artefact must carry exactly one standalone GD-56 author-header "
                            "line, found {} (portability C1)".format(rel, h_count))
    if find_handle(masked):
        findings.append("{}: operator personal handle (@{}) in shipped content (portability C1)".format(
            rel, PERSONAL_HANDLE))
    seen = set()
    for number, line in enumerate(masked.splitlines(), 1):
        for hit in find_identity(line, ident_forms, ident_maxn):
            findings.append("{}:{}: operator identity ({}) in shipped content (portability C1)".format(
                rel, number, hit))
            seen.add(hit)
    for hit in find_identity(masked, ident_forms, ident_maxn):
        if hit not in seen:
            findings.append("{}: operator identity ({}) split across lines in shipped content "
                            "(portability C1)".format(rel, hit))
    for term in find_operational_terms(text, term_grams, maxn):
        findings.append("{}: repo-operational term {!r} (portability C2)".format(rel, term))
    for number, line in enumerate(text.splitlines(), 1):
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


def _classify_mode(mode):
    """Classify one git index mode: tracked symlinks and gitlinks are C3 findings, regular executable and
    non-executable files are scanned, and every unknown mode is malformed enumeration input."""
    if mode == 0o120000:
        return "reject-symlink"
    if mode == 0o160000:
        return "reject-gitlink"
    if mode in (0o100644, 0o100755):
        return "file"
    return "reject-unknown"


def _disk_kind(root, rel):
    """Classify the working-tree object for a tracked path, SAFE against a symlinked ancestor.

    A symlink at rel's final component OR at any intermediate ancestor beneath root classifies as
    "symlink" and is never followed: a name-based read through a symlinked ancestor could scan external
    content (SECI-symlink-resolution), so it is rejected here rather than followed, matching the old
    walk's per-entry symlink detection and preserving the anti-skip guarantee. Residual (disclosed): like
    the old walk, the ancestor lstat check and the later content read are not one atomic operation, so a
    mid-scan ancestor swap is not defended; this gate is a best-effort portability check, not a race-safe
    security boundary."""
    base = root
    for comp in rel.split("/")[:-1]:
        base = base / comp
        if base.is_symlink():
            return "symlink"
    path = root / rel
    if path.is_symlink():
        return "symlink"
    if path.is_file():
        return "file"
    if path.exists():
        return "other"
    return "missing"


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


def _safe_resolve(path):
    """Resolve a path fail-closed: canonicalize it, converting any resolution failure into a GateError.
    Path.resolve() raises RuntimeError on a symlink loop under Python 3.11/3.12 (changed to return-the-path
    in 3.13) and can raise OSError; either becomes the gate's fail-closed exit 2, never an uncaught crash."""
    try:
        return path.resolve()
    except (OSError, RuntimeError) as exc:
        raise GateError("cannot resolve path {} ({})".format(path, exc))


def _list_tracked(root, runner=subprocess.run):
    """Enumerate the git-tracked shippable surface as ``[(mode, rel), ...]``. Every degraded outcome is
    fail-closed (GateError); runner is injectable for the self-test."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        top = runner(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                     capture_output=True, env=env)
    except OSError as exc:
        raise GateError("cannot confirm the git working-tree root ({})".format(exc))
    try:
        top_returncode, top_stdout = top.returncode, top.stdout
    except AttributeError as exc:
        raise GateError("git working-tree confirmation returned a malformed result ({})".format(exc))
    if top_returncode != 0:
        raise GateError("git could not confirm the working-tree root (exit {})".format(top_returncode))
    try:
        top_text = top_stdout.decode("utf-8").strip()
    except (AttributeError, UnicodeDecodeError) as exc:
        raise GateError("git working-tree root is not valid UTF-8 ({})".format(exc))
    if not top_text or _safe_resolve(Path(top_text)) != _safe_resolve(root):
        raise GateError("git working-tree root {!r} does not match required root {!r}".format(
            top_text, str(_safe_resolve(root))))

    try:
        result = runner(["git", "-C", str(root), "ls-files", "-s", "-z", "--",
                         *REQUIRED_DIR_ROOTS, *REQUIRED_FILE_ROOTS], capture_output=True, env=env)
    except OSError as exc:
        raise GateError("cannot enumerate the git-tracked surface ({})".format(exc))
    try:
        returncode, payload = result.returncode, result.stdout
    except AttributeError as exc:
        raise GateError("git tracked-file enumeration returned a malformed result ({})".format(exc))
    if returncode != 0:
        raise GateError("git tracked-file enumeration failed (exit {})".format(returncode))
    if not isinstance(payload, bytes):
        raise GateError("git tracked-file enumeration returned non-byte output")
    if payload and not payload.endswith(b"\0"):
        raise GateError("git tracked-file enumeration returned a malformed non-NUL-terminated record")

    entries, seen = [], set()
    records = payload[:-1].split(b"\0") if payload else []
    for raw in records:
        try:
            record = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GateError("git tracked-file enumeration contains a non-UTF-8 pathname ({})".format(exc))
        header, separator, rel = record.partition("\t")
        fields = header.split(" ")
        if (not separator or not rel or len(fields) != 3 or any(not field for field in fields)
                or not re.fullmatch(r"[0-7]{6}", fields[0])
                or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", fields[1])
                or not re.fullmatch(r"[0-9]+", fields[2])):
            raise GateError("git tracked-file enumeration contains a malformed record")
        mode, stage = int(fields[0], 8), int(fields[2])
        if stage != 0:
            raise GateError("git tracked-file enumeration contains unmerged path {!r} (stage {})".format(
                rel, stage))
        if rel in seen:
            raise GateError("git tracked-file enumeration contains duplicate path {!r}".format(rel))
        seen.add(rel)
        entries.append((mode, rel))
    return entries


def _surface_from_tracked(root, entries, disk_kind):
    """Turn tracked index entries into working-tree files and C3 findings, while proving coverage of every
    required root. The injected disk_kind keeps the coverage and type regressions hermetic."""
    files, findings = [], []
    dir_counts = {rel: 0 for rel in REQUIRED_DIR_ROOTS}
    tracked = set()
    try:
        iterator = iter(entries)
    except TypeError as exc:
        raise GateError("git tracked-file enumeration is not iterable ({})".format(exc))
    for entry in iterator:
        try:
            mode, rel = entry
        except (TypeError, ValueError):
            raise GateError("git tracked-file enumeration contains a malformed entry")
        if not isinstance(mode, int) or not isinstance(rel, str) or not rel:
            raise GateError("git tracked-file enumeration contains a malformed entry")
        if rel in tracked:
            raise GateError("git tracked-file enumeration contains duplicate path {!r}".format(rel))
        tracked.add(rel)

        owner = next((dr for dr in REQUIRED_DIR_ROOTS if rel.startswith(dr + "/")), None)
        if owner is not None:
            dir_counts[owner] += 1
        elif rel not in REQUIRED_FILE_ROOTS:
            raise GateError("git tracked-file enumeration returned out-of-surface path {!r}".format(rel))

        kind = _classify_mode(mode)
        if kind == "reject-symlink":
            findings.append("{}: symlink in shipped content is not portable (portability C3)".format(rel))
        elif kind == "reject-gitlink":
            findings.append("{}: submodule (gitlink) in shipped content is not portable "
                            "(portability C3)".format(rel))
        elif kind == "reject-unknown":
            raise GateError("git tracked-file enumeration contains unknown mode {:06o} for {!r}".format(
                mode, rel))
        else:
            path = root / rel
            on_disk = disk_kind(root, rel)
            if on_disk == "file":
                files.append(path)
            elif on_disk == "symlink":
                findings.append("{}: symlink in shipped content is not portable "
                                "(portability C3)".format(rel))
            elif on_disk == "other":
                findings.append("{}: unsupported file type in shipped content is not portable "
                                "(portability C3)".format(rel))
            elif on_disk == "missing":
                raise GateError("git-tracked surface file {} is absent from the working tree".format(rel))
            else:
                raise GateError("cannot classify git-tracked surface file {} in the working tree".format(rel))

    for rel, count in dir_counts.items():
        if count == 0:
            raise GateError("required surface root {} has no git-tracked (shippable) files".format(rel))
    for rel in REQUIRED_FILE_ROOTS:
        if rel not in tracked:
            raise GateError("required surface file {} is not git-tracked (not shipped)".format(rel))
    if IDENTITY_MANIFEST not in tracked:
        raise GateError("identity source {} is not git-tracked (not shipped)".format(IDENTITY_MANIFEST))
    return files, findings


def gather_surface(root, lister=_list_tracked, disk_kind=_disk_kind):
    """Return (files, findings) for the git-tracked shippable surface, reading each tracked regular file
    from the working tree. Required directory roots must exist as real directories and contain tracked
    entries; required file roots and the identity manifest must be tracked. Enumeration, coverage, or
    working-tree read/type uncertainty fails closed. Tracked symlinks/gitlinks and working-tree symlinks or
    unsupported objects are C3 findings. By design this gate asserts nothing about untracked or ignored
    working-tree paths because they do not ship; it scans working-tree copies, not committed blobs."""
    for rel in REQUIRED_DIR_ROOTS:
        d = root / rel
        if d.is_symlink() or not d.is_dir():
            raise GateError("required surface root {} is absent, a symlink, or not a directory".format(rel))
    entries = lister(root)
    return _surface_from_tracked(root, entries, disk_kind)


def scan_file(root, path, ident_forms, ident_maxn, term_grams, maxn, opener=open, attribution=None,
              header_line=None):
    """Scan one surface file: its relative PATHNAME always (C1/C2), and its CONTENT unless it is an
    allow-listed binary. An allow-listed binary is OPENED and read (opener, builtin open by default;
    injectable for the self-test) so an unreadable one fails closed (GateError -> exit 2), never a silent
    clean pass. A non-text suffix or a UTF-8 decode failure off the allow-list is a C3 finding (exit 1),
    never fail-closed exit 2: an unscannable shipped file is exactly what this gate asserts against. The
    attribution line (GD-56) is threaded to the content scan, where it is honoured only for the two
    exempt artefacts and never for a pathname. Returns a list of finding strings."""
    rel = path.relative_to(root).as_posix()
    findings = scan_pathname(rel, ident_forms, ident_maxn, term_grams, maxn)
    if rel in BINARY_ALLOW:
        try:
            with opener(path, "rb") as handle:
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
    findings.extend(scan_text(rel, text, ident_forms, ident_maxn, term_grams, maxn, attribution=attribution,
                              header_line=header_line))
    return findings


def run(root, lister=_list_tracked):
    """Scan the shippable surface under root. Returns the exit code 0/1/2."""
    try:
        root = _safe_resolve(root)  # canonicalize the anchor (like main()): a symlinked ROOT is not
                                    # followed mid-scan; every component below it is then checked by _disk_kind.
        name, email = load_identity(root)
        ident_forms = identity_deny_forms(name, email)
        ident_maxn = max(len(_tokens(name)), len(_tokens(email)))
        term_grams = {normalize_term(t) for t in OPERATIONAL_TERMS}
        maxn = max(len(_tokens(t)) for t in OPERATIONAL_TERMS)
        attribution = attribution_line(name)  # GD-56 exempt line, from the same runtime identity source
        author_header = author_header_line(name)  # GD-56 SKILL.md header line, same runtime identity source
        files, findings = gather_surface(root, lister=lister)
        for path in sorted(files):
            findings.extend(scan_file(root, path, ident_forms, ident_maxn, term_grams, maxn,
                                      attribution=attribution, header_line=author_header))
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        # A filesystem classification or read failure on a required input is not a clean skip: fail closed
        # so an unreadable surface can never read as portable.
        print("error: cannot read a required surface input ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    print("scanned surface roots: {} ({} files)".format(
        ", ".join(REQUIRED_DIR_ROOTS + REQUIRED_FILE_ROOTS), len(files)))
    if findings:
        print("FAIL: {} portability finding(s) in the shippable surface".format(len(findings)))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    print("PASS: the shippable surface is portable (operator identity only in its attribution values, no "
          "repo-operational vocabulary in content or pathnames, no exemption markers, no unscannable file "
          "classes, symlinks, or unsupported entry types off the allow-list)")
    return 0


# --- self-test --------------------------------------------------------------------------------------
# The finding-1..5 regressions are pure in-memory cases (value-span identity masking, the whole-document
# cross-line identity catch, the malformed-attribution fail-closed, case-folded and operator-scoped
# matching, pathname scanning, and, through an INJECTED lister/reader, the enumeration, symlink/type
# classification, and unreadable-binary/git-enumeration fail-closed paths). They ALWAYS run: no
# writable tempdir, no chmod, no symlink support, no wall clock, no randomness. A real-surface end-to-end
# layer runs additionally where a writable tempdir exists, and every case it cannot run is tracked so the
# result is reported PARTIAL (never a full PASS) whenever ANY case skips.

_CLEAN_MD = "# Heading\n\nPortable governance content with no operator identity and no operating vocabulary.\n"


class _FakeProc:
    """A minimal subprocess.CompletedProcess stand-in for injected git-enumerator self-tests."""

    def __init__(self, returncode=0, stdout=b""):
        self.returncode = returncode
        self.stdout = stdout


def _fake_runner(*results):
    """Return a runner that yields fake process results, or raises a queued OSError."""
    queued = iter(results)

    def runner(*_args, **_kwargs):
        result = next(queued)
        if isinstance(result, OSError):
            raise result
        return result

    return runner


def _build_surface(base, name, email):
    """Create a minimal clean-but-complete shippable surface and return (base, tracked entries)."""
    entries = []
    for rel in REQUIRED_DIR_ROOTS:
        d = base / rel
        d.mkdir(parents=True, exist_ok=True)
        placeholder = d / "placeholder.md"
        placeholder.write_text(_CLEAN_MD, encoding="utf-8")
        entries.append((0o100644, placeholder.relative_to(base).as_posix()))
    manifest = base / IDENTITY_MANIFEST
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        '[plugin]\nname = "aiqt-guardrails-hooks"\nversion = "0.1.0"\n'
        'author-name = "{}"\nauthor-email = "{}"\n'.format(name, email), encoding="utf-8")
    entries.append((0o100644, IDENTITY_MANIFEST))
    plugin_json = base / PLUGIN_JSON
    plugin_json.parent.mkdir(parents=True, exist_ok=True)
    plugin_json.write_text(
        '{{\n  "name": "aiqt-guardrails-hooks",\n  "author": {{\n    "name": "{}",\n'
        '    "email": "{}"\n  }}\n}}\n'.format(name, email), encoding="utf-8")
    entries.append((0o100644, PLUGIN_JSON))
    for rel in REQUIRED_FILE_ROOTS:
        f = base / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(_CLEAN_MD, encoding="utf-8")
        entries.append((0o100644, rel))
    for rel in sorted(BINARY_ALLOW):
        (base / rel).write_bytes(b"PK\x03\x04 synthetic zip bytes, not text")
        entries.append((0o100644, rel))
    return base, entries


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

    # Finding 1: VALUE-SPAN masked identity. In the manifest, only the [plugin] author-name/author-email
    # VALUE spans are masked; the SAME identity in a description field on another line survives and fails.
    manifest_text = (
        '[plugin]\nname = "aiqt-guardrails-hooks"\n'
        'author-name = "{0}"\nauthor-email = "{1}"\n'
        'description = "governance authored by {0}"\n'.format(name, email))
    mf = scan_text(IDENTITY_MANIFEST, manifest_text, ident_forms, ident_maxn, term_grams, maxn)
    mf_c1 = [f for f in mf if "portability C1" in f]
    if any(":3:" in f or ":4:" in f for f in mf_c1):
        failures.append("finding 1: identity flagged in its own [plugin] attribution value (should mask)")
    if not any(":5:" in f for f in mf_c1):
        failures.append("finding 1: identity in a non-attribution manifest field was not flagged")

    # Finding 1 (plugin.json): only the TOP-LEVEL author object's name/email value spans are masked; a
    # description carrying the identity fails, and the top-level package "name" is not the identity.
    pj_text = (
        '{{\n  "name": "aiqt-guardrails-hooks",\n  "author": {{\n'
        '    "name": "{0}",\n    "email": "{1}"\n  }},\n'
        '  "description": "by {0}"\n}}\n'.format(name, email))
    pj = scan_text(PLUGIN_JSON, pj_text, ident_forms, ident_maxn, term_grams, maxn)
    pj_c1 = [f for f in pj if "portability C1" in f]
    if any(":4:" in f or ":5:" in f for f in pj_c1):
        failures.append("finding 1: identity flagged in the plugin.json author object (should mask)")
    if not any(":7:" in f for f in pj_c1):
        failures.append("finding 1: identity in a non-attribution plugin.json field was not flagged")

    # Value-span C1a: an identity split across lines (name across two lines, email across two lines) is
    # caught by the WHOLE-DOCUMENT scan even though no single line carries the whole identity, and the two
    # legitimate attribution values above are still masked (not re-flagged).
    split_manifest = (
        '[plugin]\nname = "aiqt-guardrails-hooks"\n'
        'author-name = "{0}"\nauthor-email = "{1}"\n'
        '# Jeff\n# Posluns wrote this; reach jeff@\n# posluns.ca\n'.format(name, email))
    sm_c1 = [f for f in scan_text(IDENTITY_MANIFEST, split_manifest, ident_forms, ident_maxn,
                                  term_grams, maxn) if "portability C1" in f]
    if not any("split across lines" in f for f in sm_c1):
        failures.append("value-span: an identity split across lines was not caught by the whole-document scan")
    if any(":3:" in f or ":4:" in f for f in sm_c1):
        failures.append("value-span: an attribution value span was re-flagged (masking too narrow)")

    # Value-span C1b: a NESTED (non-top-level) plugin.json author is NOT exempt; its identity survives the
    # mask (which only blanks the TOP-LEVEL author values) and is flagged.
    nested_json = (
        '{{\n  "name": "aiqt-guardrails-hooks",\n  "author": {{\n'
        '    "name": "{0}",\n    "email": "{1}"\n  }},\n'
        '  "meta": {{\n    "author": {{\n      "name": "{0}"\n    }}\n  }}\n}}\n'.format(name, email))
    nj_c1 = [f for f in scan_text(PLUGIN_JSON, nested_json, ident_forms, ident_maxn, term_grams, maxn)
             if "portability C1" in f]
    if not any(":9:" in f for f in nj_c1):
        failures.append("value-span: identity in a NESTED (non-top-level) author was not flagged")
    if any(":4:" in f or ":5:" in f for f in nj_c1):
        failures.append("value-span: the top-level author value span was re-flagged (masking too narrow)")

    # Value-span C1c: malformed TOML/JSON in an attribution file is fail-closed (GateError -> exit 2),
    # never a silent clean pass on an unparseable deny input.
    try:
        scan_text(IDENTITY_MANIFEST, '[plugin\nauthor-name = "x"\n', ident_forms, ident_maxn,
                  term_grams, maxn)
        failures.append("value-span: malformed manifest TOML did not fail closed")
    except GateError:
        pass
    try:
        scan_text(PLUGIN_JSON, '{ "author": { "name": ', ident_forms, ident_maxn, term_grams, maxn)
        failures.append("value-span: malformed plugin.json did not fail closed")
    except GateError:
        pass

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

    # GD-56 attribution exemption (NARROW): the ONE exact attribution line is masked in the two exempt
    # artefacts, but the exemption is value-span-exact and never a blanket site/downloads operator-identity
    # allowance. (a) the exact line in an exempt artefact is NOT flagged; (b) a DIFFERENT operator wording
    # in the SAME artefact still trips C1; (c) the exact line in a NON-exempt file still trips C1; (d) the
    # operator email in an exempt artefact still trips C1 (the email is never part of the line, never
    # exempt). A regression that widened the mask to any operator identity in site/downloads fails (b)-(d).
    attr = attribution_line(name)
    exempt_rel = "site/downloads/aiqt-instructions.txt"
    if [f for f in scan_text(exempt_rel, "header\n\n" + attr + "\ncontent\n", ident_forms, ident_maxn,
                             term_grams, maxn, attribution=attr) if "portability C1" in f]:
        failures.append("GD-56: the exact attribution line was flagged in an exempt artefact")
    if not [f for f in scan_text(exempt_rel, "governance authored personally by {}\n".format(name),
                                 ident_forms, ident_maxn, term_grams, maxn, attribution=attr)
            if "portability C1" in f]:
        failures.append("GD-56: a DIFFERENT operator string in an exempt artefact was not flagged "
                        "(exemption too broad)")
    if not [f for f in scan_text(".claude/rules/x.md", attr + "\n", ident_forms, ident_maxn,
                                 term_grams, maxn, attribution=attr) if "portability C1" in f]:
        failures.append("GD-56: the attribution line was exempted in a NON-exempt file "
                        "(exemption not scoped to the two artefacts)")
    if not [f for f in scan_text(exempt_rel, "contact {}\n".format(email), ident_forms, ident_maxn,
                                 term_grams, maxn, attribution=attr) if "portability C1" in f]:
        failures.append("GD-56: the operator email was exempted in an exempt artefact (email never exempt)")

    # GD-56 LINE-ANCHORING (round-1 QA finding 1): the exemption clears ONLY a standalone line exactly equal
    # to the attribution string, exactly once. A trailing-period near-variant, a prefix/suffix wrap, and two
    # duplicate copies each leave the identity to trip C1 (the old substring replace masked all of them);
    # and a placement finding fires when the count is not exactly one.
    def _c1(txt):
        return [f for f in scan_text(exempt_rel, txt, ident_forms, ident_maxn, term_grams, maxn,
                                     attribution=attr) if "portability C1" in f]
    if not _c1(attr + ".\n"):
        failures.append("GD-56 anchor: a trailing-period attribution variant was not flagged")
    if not _c1("see " + attr + " here\n"):
        failures.append("GD-56 anchor: the attribution string wrapped in a longer line was not flagged")
    if not _c1(attr + "\n" + attr + "\n"):
        failures.append("GD-56 anchor: two duplicate attribution copies were not flagged")
    if not any("exactly one standalone" in f for f in _c1(attr + "\n" + attr + "\n")):
        failures.append("GD-56 anchor: a duplicate attribution did not raise the placement finding")
    if not any("exactly one standalone" in f for f in _c1("no attribution here\n")):
        failures.append("GD-56 anchor: a missing attribution line did not raise the placement finding")
    if _c1("header\n\n  " + attr + "  \ncontent\n"):
        failures.append("GD-56 anchor: an indented standalone attribution line was not cleared")

    # GD-56 SKILL.md author-header exemption: the visible metadata block under the SKILL.md `# AIQT™` H1
    # carries the operator name a SECOND time on an `Author: <name>` line (distinct from the footer
    # attribution line), masked with the SAME line-anchored, single-placement discipline and ONLY in
    # SKILL.md. (a) exactly one standalone Author line masks clean; (b) a duplicated Author line leaves the
    # identity to trip C1 and raises the placement finding; (c) a missing Author line (when a header_line is
    # expected) raises the placement finding; (d) the same header line in the instructions fallback (not
    # SKILL_MD_REL) is NOT exempted, so its operator name still trips C1.
    hdr = author_header_line(name)

    def _skill_c1(txt):
        return [f for f in scan_text(SKILL_MD_REL, txt, ident_forms, ident_maxn, term_grams, maxn,
                                     attribution=attr, header_line=hdr) if "portability C1" in f]
    if _skill_c1("# AIQT\n\n" + hdr + "\n" + attr + "\ncontent\n"):
        failures.append("GD-56 author-header: a single standalone Author line in SKILL.md was not masked clean")
    if not any("author-header" in f for f in _skill_c1(hdr + "\n" + hdr + "\n" + attr + "\n")):
        failures.append("GD-56 author-header: a duplicated Author line did not raise the placement finding")
    if not any("author-header" in f for f in _skill_c1(attr + "\n")):
        failures.append("GD-56 author-header: a missing Author line did not raise the placement finding")
    if not [f for f in scan_text(exempt_rel, hdr + "\n", ident_forms, ident_maxn, term_grams, maxn,
                                 attribution=attr, header_line=hdr) if "portability C1" in f]:
        failures.append("GD-56 author-header: the header line was exempted outside SKILL.md (scope too broad)")

    # Personal-handle check (round-1 QA finding 2): an @-prefixed operator handle in shipped content is
    # flagged, while the bare owner segment inside a legitimate source URL (the attribution URL and a normal
    # github.com/<owner>/... URL) and an email local@handle form are NOT (they MUST stay clean).
    def _handle_c1(rel_, txt):
        return [f for f in scan_text(rel_, txt, ident_forms, ident_maxn, term_grams, maxn)
                if "personal handle" in f]
    if not _handle_c1(".claude/rules/x.md", "ping @{} about it\n".format(PERSONAL_HANDLE)):
        failures.append("handle: an @-prefixed operator handle was not flagged")
    if _handle_c1(".claude/rules/x.md", "see {}\n".format(ATTRIBUTION_SOURCE_URL)):
        failures.append("handle: the bare owner in the attribution source URL was wrongly flagged")
    if _handle_c1(".claude/rules/x.md", "https://github.com/{}/other/blob/main/a.md\n".format(PERSONAL_HANDLE)):
        failures.append("handle: the bare owner in a normal github URL was wrongly flagged")
    if _handle_c1(".claude/rules/x.md", "mail someone@{}.example today\n".format(PERSONAL_HANDLE)):
        failures.append("handle: an email local@handle form was wrongly flagged as a handle mention")

    # Finding 3: a pathname operational term fails even with clean bytes; a clean pathname passes; and git
    # modes classify regular files, symlinks, gitlinks, and unknown input distinctly.
    if not scan_pathname(".claude/rules/session-handoff.md", ident_forms, ident_maxn, term_grams, maxn):
        failures.append("finding 3: an operational term in a pathname was not flagged")
    if scan_pathname(".claude/rules/portable-note.md", ident_forms, ident_maxn, term_grams, maxn):
        failures.append("finding 3: a clean pathname was flagged")
    if _classify_mode(0o120000) != "reject-symlink":
        failures.append("finding 3: a tracked symlink mode was not rejected")
    if _classify_mode(0o160000) != "reject-gitlink":
        failures.append("finding 3: a tracked gitlink mode was not rejected")
    if _classify_mode(0o100644) != "file" or _classify_mode(0o100755) != "file":
        failures.append("finding 3: a tracked regular-file mode was not collected")
    if _classify_mode(0o040000) != "reject-unknown":
        failures.append("finding 3: an unknown tracked mode was not rejected")

    # Finding 3 (injected lister): tracked content under __pycache__ is still collected (NO skip rules),
    # while a tracked symlink and gitlink are C3 findings. Placeholders cover every other required root.
    iw_root = Path("/surface")
    iw_entries = [
        (0o100644, ".aiqt/core/good.md"),
        (0o100644, ".aiqt/core/__pycache__/note.md"),
        (0o120000, ".aiqt/core/link.md"),
        (0o160000, ".aiqt/core/sub"),
        (0o100644, IDENTITY_MANIFEST),
    ]
    iw_entries.extend((0o100644, rel + "/placeholder.md") for rel in REQUIRED_DIR_ROOTS[1:])
    iw_entries.extend((0o100644, rel) for rel in REQUIRED_FILE_ROOTS)
    iw_files, iw_findings = _surface_from_tracked(iw_root, iw_entries, lambda _root, _rel: "file")
    iw_rels = {path.relative_to(iw_root).as_posix() for path in iw_files}
    if not {".aiqt/core/good.md", ".aiqt/core/__pycache__/note.md"}.issubset(iw_rels):
        failures.append("finding 3: the injected lister did not collect tracked __pycache__ content")
    if not any("symlink" in finding for finding in iw_findings):
        failures.append("finding 3: the injected lister did not reject a tracked symlink")
    if not any("gitlink" in finding for finding in iw_findings):
        failures.append("finding 3: the injected lister did not reject a tracked gitlink")

    def _expect_gate_error(label, thunk, contains=None):
        try:
            thunk()
        except GateError as exc:
            if contains and contains not in str(exc):
                failures.append("{} raised GateError without naming {!r}".format(label, contains))
        except Exception as exc:  # a degraded guard input must become GateError, not escape another way
            failures.append("{} raised {} instead of GateError".format(label, type(exc).__name__))
        else:
            failures.append("{} did not fail closed".format(label))

    # Finding 4a (injected git lister): binary absence, either nonzero call, target mismatch, malformed or
    # non-UTF-8 output, nonzero conflict stage, and duplicate records all become GateError.
    top_ok = _FakeProc(stdout=b"/surface\n")
    oid = b"0" * 40
    good_record = b"100644 " + oid + b" 0\t.aiqt/core/good.md\0"
    parsed = _list_tracked(iw_root, runner=_fake_runner(top_ok, _FakeProc(stdout=good_record)))
    if parsed != [(0o100644, ".aiqt/core/good.md")]:
        failures.append("finding 4: the injected git lister did not parse a valid record")
    _expect_gate_error("finding 4: absent git binary",
                       lambda: _list_tracked(iw_root, runner=_fake_runner(FileNotFoundError("git"))))
    _expect_gate_error("finding 4: nonzero git root confirmation",
                       lambda: _list_tracked(iw_root, runner=_fake_runner(_FakeProc(returncode=1))))
    _expect_gate_error("finding 4: nonzero git enumeration",
                       lambda: _list_tracked(iw_root, runner=_fake_runner(
                           top_ok, _FakeProc(returncode=1))))
    _expect_gate_error("finding 4: git toplevel mismatch",
                       lambda: _list_tracked(iw_root, runner=_fake_runner(
                           _FakeProc(stdout=b"/elsewhere\n"))))
    _expect_gate_error("finding 4: malformed git record",
                       lambda: _list_tracked(iw_root, runner=_fake_runner(
                           top_ok, _FakeProc(stdout=b"malformed\0"))))
    _expect_gate_error("finding 4: non-UTF-8 git pathname",
                       lambda: _list_tracked(iw_root, runner=_fake_runner(
                           top_ok, _FakeProc(stdout=b"100644 " + oid + b" 0\tbad-\xff\0"))))
    _expect_gate_error("finding 4: unmerged git path",
                       lambda: _list_tracked(iw_root, runner=_fake_runner(
                           top_ok, _FakeProc(stdout=b"100644 " + oid + b" 2\tconflict.md\0"))))
    _expect_gate_error("finding 4: duplicate git path",
                       lambda: _list_tracked(iw_root, runner=_fake_runner(
                           top_ok, _FakeProc(stdout=good_record + good_record))))

    # Coverage and working-tree kind checks remain fail-closed or C3 exactly as the tracked-surface roster
    # specifies, including the identity manifest as a shipped deny input.
    empty_dir = REQUIRED_DIR_ROOTS[1]
    _expect_gate_error("finding 4: empty tracked directory root",
                       lambda: _surface_from_tracked(
                           iw_root, [(mode, rel) for mode, rel in iw_entries
                                     if not rel.startswith(empty_dir + "/")], lambda _root, _rel: "file"),
                       empty_dir)
    untracked_file = REQUIRED_FILE_ROOTS[1]
    _expect_gate_error("finding 4: untracked required file",
                       lambda: _surface_from_tracked(
                           iw_root, [(mode, rel) for mode, rel in iw_entries if rel != untracked_file],
                           lambda _root, _rel: "file"), untracked_file)
    _expect_gate_error("finding 4: untracked identity manifest",
                       lambda: _surface_from_tracked(
                           iw_root, [(mode, rel) for mode, rel in iw_entries
                                     if rel != IDENTITY_MANIFEST], lambda _root, _rel: "file"),
                       IDENTITY_MANIFEST)
    _expect_gate_error("finding 4: unknown git mode",
                       lambda: _surface_from_tracked(
                           iw_root, iw_entries + [(0o040000, ".aiqt/core/unknown")],
                           lambda _root, _rel: "file"))
    missing_rel = ".aiqt/core/good.md"
    _expect_gate_error("finding 4: missing tracked working-tree file",
                       lambda: _surface_from_tracked(
                           iw_root, iw_entries,
                           lambda _root, rel: "missing" if rel == missing_rel else "file"),
                       missing_rel)
    kind_entries = iw_entries + [
        (0o100644, ".aiqt/core/disk-link.md"),
        (0o100644, ".aiqt/core/pipe"),
    ]
    _, kind_findings = _surface_from_tracked(
        iw_root, kind_entries,
        lambda _root, rel: {".aiqt/core/disk-link.md": "symlink",
                      ".aiqt/core/pipe": "other"}.get(rel, "file"))
    if not any("disk-link.md: symlink" in finding for finding in kind_findings):
        failures.append("finding 3: a working-tree symlink at a tracked regular path was not rejected")
    if not any("pipe: unsupported file type" in finding for finding in kind_findings):
        failures.append("finding 3: an unsupported working-tree type at a tracked path was not rejected")


    # Finding 4b (injected reader): an unreadable allow-listed binary fails closed (GateError -> exit 2),
    # never a silent clean pass. The injected opener raises OSError, with no filesystem path or chmod.
    def _raising_opener(*_args, **_kwargs):
        raise OSError("injected unreadable binary")

    try:
        scan_file(iw_root, iw_root / "site/downloads/aiqt-skill.zip", ident_forms, ident_maxn,
                  term_grams, maxn, opener=_raising_opener)
        failures.append("finding 4: an unreadable allow-listed binary did not fail closed")
    except GateError:
        pass

    # Finding 5 companion: a shipped leak-allow exemption marker (C5) is flagged.
    if not scan_text(".claude/rules/m.md", "this line carries a leak-allow marker", ident_forms,
                     ident_maxn, term_grams, maxn):
        failures.append("a shipped leak-allow marker (C5) was not flagged")

    # --- real-surface end-to-end layer (runs where a writable tempdir exists) ----------------------
    # Cases (a)-(n) use an injected tracked-entry roster over real files. Case (o) uses a real git index
    # when git is available; its absence is tracked as PARTIAL, never reported as a full PASS.
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stderr, redirect_stdout

    # GD-130: _safe_resolve fails CLOSED (GateError) on a resolve() that raises RuntimeError (Python
    # 3.11/3.12 symlink-loop behavior) or OSError, never an uncaught crash. Discriminating: without the
    # (OSError, RuntimeError) catch, the exception escapes and the second `except` records the failure.
    class _RaisingResolve:
        def __init__(self, exc):
            self._exc = exc

        def resolve(self, *_a, **_k):
            raise self._exc

        def __str__(self):
            return "<raising-resolve>"

    for _exc in (RuntimeError("Too many levels of symbolic links"), OSError("ELOOP")):
        try:
            _safe_resolve(_RaisingResolve(_exc))
            failures.append("GD-130: _safe_resolve did not fail closed on {}".format(type(_exc).__name__))
        except GateError:
            pass
        except (OSError, RuntimeError):
            failures.append("GD-130: _safe_resolve let a {} escape instead of GateError".format(
                type(_exc).__name__))

    # GD-130: main() routes its __file__ resolve through _safe_resolve and returns fail-closed exit 2.
    # Discriminating: monkeypatch the module _safe_resolve to raise GateError, invoke main() on a
    # non-self-test argv, and confirm it returns 2 WITHOUT running the gate. Reverting main() to a bare
    # resolve would skip _safe_resolve, so the injected failure would not fire and main() would not return 2.
    _mod = sys.modules[__name__]
    _orig_safe_resolve, _orig_argv = _mod._safe_resolve, sys.argv
    _file_str = str(Path(__file__))

    def _selective_raise(path):
        # raise ONLY for main()'s own __file__ resolve; other paths (run()'s root) resolve normally, so this
        # discriminates main()'s guard specifically (run() also routes through _safe_resolve).
        if str(path) == _file_str:
            raise GateError("injected __file__ resolve failure")
        return _orig_safe_resolve(path)

    _main_err = io.StringIO()
    try:
        _mod._safe_resolve = _selective_raise
        sys.argv = ["check_portability.py"]
        with redirect_stdout(io.StringIO()), redirect_stderr(_main_err):
            _main_code = main()
        if _main_code != 2:
            failures.append("GD-130: main() did not fail closed (exit 2) when its __file__ resolve raised; "
                            "is main() routed through _safe_resolve?")
        if "; fail-closed" not in _main_err.getvalue():
            failures.append("GD-130: main()'s fail-closed exit-2 message did not carry the '; fail-closed' marker")
    finally:
        _mod._safe_resolve, sys.argv = _orig_safe_resolve, _orig_argv

    # GD-130 round-8: run() routes its ROOT resolve through _safe_resolve and fails closed (exit 2) with
    # the "; fail-closed" marker. Discriminating: record whether the injected resolver fired for run()'s
    # root; reverting run() to a bare root.resolve() would skip _safe_resolve, so the injected raise would
    # never fire on the root and _root_seen would stay False. This also asserts the fail-closed marker.
    _root_arg = Path(tempfile.gettempdir()) / "gd130-run-resolve-probe"
    _root_seen = {"hit": False}

    def _selective_raise_root(path):
        if str(path) == str(_root_arg):
            _root_seen["hit"] = True
            raise GateError("injected root resolve failure")
        return _orig_safe_resolve(path)

    _run_err = io.StringIO()
    try:
        _mod._safe_resolve = _selective_raise_root
        with redirect_stdout(io.StringIO()), redirect_stderr(_run_err):
            _run_code = run(_root_arg, lister=lambda _root: [])
        if not _root_seen["hit"]:
            failures.append("GD-130: run() did not route its root through _safe_resolve (injected resolver "
                            "never fired on the root); is run()'s root resolve routed through _safe_resolve?")
        if _run_code != 2:
            failures.append("GD-130: run() did not fail closed (exit 2) when its root resolve raised GateError")
        if "; fail-closed" not in _run_err.getvalue():
            failures.append("GD-130: run()'s fail-closed exit-2 message did not carry the '; fail-closed' marker")
    finally:
        _mod._safe_resolve = _orig_safe_resolve

    # GD-130 round-8b: run()'s OSError fail-close (a read failure on a required surface input) returns
    # exit 2 with the "; fail-closed" marker. Discriminating: force a required-surface read to raise
    # OSError after the anchor resolves; removing run()'s `except OSError` catch lets it escape (the
    # self-test errors instead of passing), and dropping the marker fails the assertion.
    _orig_load_identity = _mod.load_identity

    def _raise_oserror(*_a, **_k):
        raise OSError("EIO: simulated required-surface read failure")

    _osr_arg = Path(tempfile.gettempdir()) / "gd130-run-oserror-probe"
    _os_err = io.StringIO()
    try:
        _mod.load_identity = _raise_oserror
        with redirect_stdout(io.StringIO()), redirect_stderr(_os_err):
            _os_code = run(_osr_arg, lister=lambda _root: [])
        if _os_code != 2:
            failures.append("GD-130: run() did not fail closed (exit 2) when a required-surface read raised "
                            "OSError")
        if "; fail-closed" not in _os_err.getvalue():
            failures.append("GD-130: run()'s OSError fail-closed exit-2 message did not carry the "
                            "'; fail-closed' marker")
    finally:
        _mod.load_identity = _orig_load_identity

    # GD-130 round-8: the CLI-misuse (unknown-argument) exit-2 path also carries the "; fail-closed" marker,
    # so every exit-2 outcome is unambiguously a non-pass, never mistaken for a clean result. Discriminating:
    # dropping the marker from _parse_args's usage line leaves this assertion unsatisfied.
    _usage_err = io.StringIO()
    _orig_argv2 = sys.argv
    try:
        sys.argv = ["check_portability.py", "--bogus-unknown-arg"]
        with redirect_stdout(io.StringIO()), redirect_stderr(_usage_err):
            _usage_code = main()
        if _usage_code != 2:
            failures.append("GD-130: main() did not return exit 2 on an unknown CLI argument")
        if "; fail-closed" not in _usage_err.getvalue():
            failures.append("GD-130: the unknown-argument usage/exit-2 path did not carry the "
                            "'; fail-closed' marker")
    finally:
        sys.argv = _orig_argv2

    skipped = []

    def _run_quiet(sroot, entries=None):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            if entries is None:
                return run(sroot)
            return run(sroot, lister=lambda _root: entries)

    e2e_name, e2e_email = "Test Operator", "operator@example.invalid"
    try:
        base_tmp = Path(tempfile.mkdtemp(prefix="aiqt-portability-selftest-"))
    except OSError:
        base_tmp = None

    if base_tmp is None:
        skipped.append("all end-to-end surface cases (no writable temp directory)")
    else:
        def _fresh(tag):
            return _build_surface(base_tmp / tag, e2e_name, e2e_email)

        try:
            # GD-130: the REAL _disk_kind rejects a symlinked ANCESTOR (never follows it) and a terminal
            # symlink; run() canonicalizes a symlinked ROOT anchor. ALL symlink/fs setup is inside the guard
            # so a symlink-less host reports PARTIAL (never a raise); the root-boundary check DISCRIMINATES
            # (it fails if root.resolve() is absent, because the injected lister would then receive the
            # symlink rather than the canonical root).
            _dk = base_tmp / "disk-kind"
            try:
                (_dk / "ext").mkdir(parents=True); (_dk / "ext" / "f.md").write_text("decoy")
                (_dk / "sub" / "real").mkdir(parents=True); (_dk / "sub" / "real" / "g.md").write_text("ok")
                os.symlink(_dk / "ext", _dk / "sub" / "mid")
                os.symlink(_dk / "ext" / "f.md", _dk / "sub" / "real" / "term.md")
                _rsroot, _rentries = _fresh("rootlink-real")
                _rlink = base_tmp / "rootlink-link"
                os.symlink(_rsroot, _rlink)
            except OSError:
                skipped.append("_disk_kind ancestor-symlink and root-boundary real-fs tests "
                               "(no symlink support)")
            else:
                if _disk_kind(_dk, "sub/mid/f.md") != "symlink":
                    failures.append("GD-130: _disk_kind followed an intermediate symlinked ancestor")
                if _disk_kind(_dk, "sub/real/g.md") != "file":
                    failures.append("GD-130: _disk_kind mis-classified a real file under real directories")
                if _disk_kind(_dk, "sub/real/term.md") != "symlink":
                    failures.append("GD-130: _disk_kind did not reject a terminal symlink")
                _got_root = []
                def _cap_lister(_r):
                    _got_root.append(_r)
                    return _rentries
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    run(_rlink, lister=_cap_lister)
                if not _got_root or _got_root[0].is_symlink():
                    failures.append("GD-130: run() did not canonicalize a symlinked root before the lister")

            # (a) a clean surface, identity present ONLY in the two attribution fields, passes.
            sroot, entries = _fresh("clean")
            if _run_quiet(sroot, entries) != 0:
                failures.append("e2e: a clean synthetic surface (identity in its attribution fields) "
                                "expected exit 0")

            # (b) the operator email planted in a rule fails (email scoped to the operator address).
            sroot, entries = _fresh("planted-email")
            planted = ".claude/rules/leak.md"
            (sroot / planted).write_text(
                "# Rule\n\nquestions to {}\n".format(e2e_email), encoding="utf-8")
            entries.append((0o100644, planted))
            if _run_quiet(sroot, entries) != 1:
                failures.append("e2e: the operator email planted in a rule expected exit 1")

            # (c) the operator name planted (lowercase) outside the attribution fields fails.
            sroot, entries = _fresh("planted-name")
            (sroot / "AGENTS.md").write_text(
                "# Adapter\n\nauthored by {}\n".format(e2e_name.lower()), encoding="utf-8")
            if _run_quiet(sroot, entries) != 1:
                failures.append("e2e: the lowercase operator name outside its attribution expected exit 1")

            # (d) a camel-cased operational term fails.
            sroot, entries = _fresh("camel-term")
            (sroot / ".aiqt/core/placeholder.md").write_text(
                "# Note\n\nrun the SessionHandoff at close\n", encoding="utf-8")
            if _run_quiet(sroot, entries) != 1:
                failures.append("e2e: a camel-cased operational term expected exit 1")

            # (e) a pathname operational term fails on the NAME alone, with clean bytes.
            sroot, entries = _fresh("pathname-term")
            planted = ".claude/rules/session-handoff.md"
            (sroot / planted).write_text(_CLEAN_MD, encoding="utf-8")
            entries.append((0o100644, planted))
            if _run_quiet(sroot, entries) != 1:
                failures.append("e2e: a file NAMED session-handoff.md expected exit 1")

            # (f) an unknown non-text file fails while the allow-listed zips pass.
            sroot, entries = _fresh("unknown-binary")
            planted = "site/downloads/logo.png"
            (sroot / planted).write_bytes(b"\x89PNG\r\n\x1a\n not text")
            entries.append((0o100644, planted))
            if _run_quiet(sroot, entries) != 1:
                failures.append("e2e: an unknown .png in the surface expected exit 1")

            # (g) a planted leak-allow exemption marker fails.
            sroot, entries = _fresh("planted-marker")
            planted = ".claude/rules/marked.md"
            (sroot / planted).write_text(
                "# Rule\n\nthis line carries a leak-allow marker\n", encoding="utf-8")
            entries.append((0o100644, planted))
            if _run_quiet(sroot, entries) != 1:
                failures.append("e2e: a shipped leak-allow marker expected exit 1")

            # (h) tracked content under __pycache__ is enumerated despite its directory name and fails.
            sroot, entries = _fresh("no-skip")
            planted = ".aiqt/core/__pycache__/note.md"
            (sroot / planted).parent.mkdir(parents=True, exist_ok=True)
            (sroot / planted).write_text("# x\n\nrun the SessionHandoff\n", encoding="utf-8")
            entries.append((0o100644, planted))
            if _run_quiet(sroot, entries) != 1:
                failures.append("e2e: tracked content under __pycache__ was not enumerated")

            # (i) a missing required root is fail-closed exit 2.
            sroot, entries = _fresh("missing-root")
            shutil.rmtree(sroot / ".cursor/rules/aiqt-guardrails")
            if _run_quiet(sroot, entries) != 2:
                failures.append("e2e: a missing required root expected fail-closed exit 2")

            # (j) an absent identity manifest is fail-closed exit 2.
            sroot, entries = _fresh("no-manifest")
            (sroot / IDENTITY_MANIFEST).unlink()
            if _run_quiet(sroot, entries) != 2:
                failures.append("e2e: an absent identity manifest expected fail-closed exit 2")

            # (k) GD-130: an untracked stray bytecode artefact is outside the shipped surface and passes.
            sroot, entries = _fresh("untracked-pyc")
            stray = ".aiqt/core/hooks/scripts/__pycache__/x.cpython-312.pyc"
            (sroot / stray).parent.mkdir(parents=True, exist_ok=True)
            (sroot / stray).write_bytes(b"stray bytecode")
            if _run_quiet(sroot, entries) != 0:
                failures.append("GD-130: an untracked stray .pyc expected exit 0")

            # (l) Paired guard: the same bytecode artefact in the tracked roster is a C3 finding.
            entries.append((0o100644, stray))
            if _run_quiet(sroot, entries) != 1:
                failures.append("GD-130: the tracked stray .pyc expected exit 1")

            # (m) A required file present on disk but absent from the tracked roster fails closed.
            sroot, entries = _fresh("untracked-required-file")
            entries = [(mode, rel) for mode, rel in entries if rel != "GEMINI.md"]
            if _run_quiet(sroot, entries) != 2:
                failures.append("GD-130: an untracked required GEMINI.md expected fail-closed exit 2")

            # (n) A required dir root present on disk but empty in the tracked roster fails closed.
            sroot, entries = _fresh("empty-tracked-root")
            entries = [(mode, rel) for mode, rel in entries
                       if not rel.startswith("site/downloads/")]
            if _run_quiet(sroot, entries) != 2:
                failures.append("GD-130: a tracked-empty site/downloads expected fail-closed exit 2")

            # (o) Canonical real-git witness: untracked stray passes, then the same path tracked fails C3.
            if shutil.which("git") is None:
                skipped.append("real-git tracked/untracked GD-130 pair (git is unavailable)")
            else:
                sroot, _entries = _fresh("real-git")
                git_env = {key: value for key, value in os.environ.items()
                           if not key.startswith("GIT_")}

                def _git(*args):
                    try:
                        return subprocess.run(["git", "-C", str(sroot), *args], capture_output=True,
                                              env=git_env).returncode
                    except OSError:
                        return None

                if _git("init", "-q") != 0:
                    failures.append("GD-130 real-git: git init failed")
                elif _git("add", "-A") != 0:
                    failures.append("GD-130 real-git: initial git add failed")
                else:
                    stray = ".aiqt/core/hooks/scripts/__pycache__/x.cpython-312.pyc"
                    (sroot / stray).parent.mkdir(parents=True, exist_ok=True)
                    (sroot / stray).write_bytes(b"stray bytecode")
                    if _run_quiet(sroot) != 0:
                        failures.append("GD-130 real-git: untracked stray .pyc expected exit 0")
                    if _git("add", "-f", "--", stray) != 0:
                        failures.append("GD-130 real-git: git add of stray .pyc failed")
                    elif _run_quiet(sroot) != 1:
                        failures.append("GD-130 real-git: tracked stray .pyc expected exit 1")
        finally:
            shutil.rmtree(base_tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    core = ("the finding-1..5 regressions ran in memory and hold (value-span identity masking, the "
            "whole-document cross-line catch, nested-author non-exemption, malformed-attribution "
            "fail-closed, case-folded and operator-scoped matching, pathname scanning, and the injected "
            "lister/reader enumeration, symlink/gitlink/type, git-enumeration and unreadable-binary "
            "fail-closed paths, plus the exemption marker)")
    if skipped:
        print("SELF-TEST PASS (PARTIAL): {}; the following were SKIPPED, so those invariants are UNVERIFIED "
              "this run: {}".format(core, "; ".join(skipped)))
    else:
        print("SELF-TEST PASS: {}; the end-to-end surface cases hold (clean pass, planted identity/email/"
              "vocabulary/pathname/marker/unknown-file findings, tracked no-skip enumeration, GD-130 "
              "tracked/untracked stray-artifact pairs, and missing/untracked roots fail-closed)".format(core))
    return 0


def _parse_args(argv):
    self_test = False
    i = 0
    while i < len(argv):
        if argv[i] == "--self-test":
            self_test = True
            i += 1
        else:
            print("usage: check_portability.py [--self-test]; fail-closed", file=sys.stderr)
            return None
    return (self_test,)


def main():
    parsed = _parse_args(sys.argv[1:])
    if parsed is None:
        return 2
    (self_test,) = parsed
    if self_test:
        return self_test_main()
    try:
        root = _safe_resolve(Path(__file__)).parents[1]  # last resolve site; guarded for completeness
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    return run(root)


if __name__ == "__main__":
    sys.exit(main())
