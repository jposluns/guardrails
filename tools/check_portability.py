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
  C3 unscannable content: check_leaks scans any file class it can decode (a ZIP archive member by
     member) and exits 2 naming any input it cannot read in full (a non-regular file, a symlinked
     directory, or an archive or archive member it cannot unpack within its bounds), but it accepts every
     decodable class; in the shippable surface an undeclared class is itself unfit, so here every file
     outside the text-suffix set (or failing UTF-8 decode) must be on the binary allow-list or it is a
     finding, an allow-listed binary is OPENED so an unreadable one fails closed, and a symlink or
     unsupported entry type under a shipped root is itself a finding.
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
                                      runs too where a writable tempdir exists; a case it cannot run (no
                                      tempdir, no symlink support, no git) is a NAMED NOT RUN failure

Exit convention (matches the repo's gates):
  0  clean
  1  a real finding (operator identity, operational vocabulary, an exemption marker, or an unscannable file)
  2  an unreadable identity source, an absent or unwalkable required root, a failed or unconfirmable
     tracked-file enumeration, a required file that is not git-tracked, an unreadable allow-listed binary,
     or a read error (fail-closed). A text file that does not decode as UTF-8 is the ONE read outcome that is
     a C3 finding (exit 1), not a fail-closed exit 2, since an unscannable shipped file is itself the thing
     this gate is asserting against.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_portability.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    import os
    try:
        sys.stderr.write(
            "error: check_portability.py cannot import tomllib, part of the Python standard library; "
            "this installation is incomplete. Nothing was run (cannot evaluate).\n")
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

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
    "opf/spec",                          # the OPF specification pair, relocated from .aiqt/core/opf/ (OPF-SELF-CONTAIN)
    "opf/enforcement",                   # the enforcement pack (CI recipe + workflow template, U16): the ownership
                                         # roster records opf/enforcement/** as ADOPTER-INSTALLED content, so it is
                                         # part of the portable surface this gate polices, unlike opf/tools below.
    # NOTE: opf/tools is NOT a portability root, mirroring tools/. Both are the pack's AUTHOR-SIDE tooling
    # (generators, gates, vendored provenance), not the adopter-installed portable surface this gate policies;
    # they legitimately carry operational domain vocabulary and vendored .sha256/.typed binaries. The spec
    # pair WAS portability-scanned under .aiqt/core/opf/, so opf/spec is added to preserve that coverage.
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
# the original attribution (Apache License 2.0), so these values ship the identity by design.

# The only shippable file that is not scannable text. It is byte-reconciled by gen_skill.py --check from
# sources this gate DOES scan, so its content portability follows transitively; it is still OPENED here so
# an unreadable copy fails closed rather than passing silently.
BINARY_ALLOW = {"site/downloads/aiqt-skill.zip", "site/downloads/aiqt-skill-1.0.6.zip"}

# GD-56 attribution exemption (NARROW and REVIEWED; NOT a general operator-identity allowance). The
# maintainer deliberately attributes both the project and himself, by name, on the two PUBLISHED chat
# artefacts, under the Apache License 2.0 the pack ships. That one exact line therefore carries the operator NAME by
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
    return "AIQT Guardrails by {}, {}, Apache License 2.0".format(name, ATTRIBUTION_SOURCE_URL)


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
# artefact, outside the leak gate's declared text suffixes; check_leaks scans it when it decodes as UTF-8).
TEXT_SUFFIXES = {".md", ".mdc", ".py", ".sh", ".yml", ".yaml", ".toml", ".json", ".txt",
                 ".html", ".css", ".js", ".cfg", ".ini", ".conf", ".svg", ".csv"}

# Shipped text files whose NAME is fixed by an outside convention and so carries no text suffix: git runs a
# hook only under its exact name, so the enforcement pack's pre-commit hook is named pre-commit. Each is an
# exact path, scanned as text like any TEXT_SUFFIXES file.
TEXT_PATHS = frozenset(("opf/enforcement/precommit/pre-commit",))


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
    Those are the two places the identity ships by design (Apache License 2.0 attribution). Every OTHER occurrence in
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
    # ValueError and RecursionError too: tomllib raises a BARE ValueError (not TOMLDecodeError) on an
    # integer literal past CPython's 4300-digit int-string limit, and a RecursionError (a RuntimeError)
    # on a deeply nested array or inline table (F-TOML-BARE-VALUEERROR-CLASS).
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
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
    # ValueError and RecursionError too: tomllib raises a BARE ValueError (not TOMLDecodeError) on an
    # integer literal past CPython's 4300-digit int-string limit, and a RecursionError (a RuntimeError)
    # on a deeply nested array or inline table (F-TOML-BARE-VALUEERROR-CLASS).
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
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
    if path.suffix not in TEXT_SUFFIXES and rel not in TEXT_PATHS:
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
# layer runs additionally where a writable tempdir exists; every case it cannot run (no tempdir, no
# symlink support, no git) is a NAMED NOT RUN failure (nonzero exit), never a skip beside a PASS.

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


def _not_run(failures, case, cause):
    """Record a self-test case that could not run as a NAMED failure (a counted cannot-evaluate that
    fails the run), never a printed note beside a PASS: coverage that did not run is not coverage."""
    failures.append("NOT RUN: {}: {}; coverage that did not run FAILS the self-test, never a silent "
                    "PASS".format(case, cause))


_E2E_SURFACE_CASES = "every end-to-end surface case over a real synthetic shippable tree"
_DISK_KIND_CASES = "the _disk_kind ancestor-symlink and root-boundary real-filesystem cases"
_REAL_GIT_CASE = "the real-git tracked/untracked GD-130 pair"
_EVERY_CASE = "every self-test case"
_SUPPRESS_VECTORS_ENV = "AIQT_SELFTEST_SUPPRESS_VECTORS"
# Every fresh Python VECTOR-ENTRY child (the self-test children the bounded launcher starts)
# carries this depth marker: a nested entry that reaches the vectors with it PRESENT, with
# any value, the empty string included, refuses to run them (a child never launches
# self-test vector children of its own; the launcher-timeout child's plain descendant and
# the full-run children's git launches are deliberate and are not vector children), so a
# reverted no-vectors seam or marked-child gate ends at depth 1 with a named failure, never
# an unbounded chain; an inherited value, even an empty one, trips the same refusal (fail
# closed: presence, never truthiness, is the trigger), and the refusal text names the
# inheritance as a possible cause.
_CHILD_DEPTH_ENV = "AIQT_SELFTEST_CHILD_DEPTH"
_CHILD_TIMEOUT = 120  # seconds: the wait bound the launcher applies to every vector-entry child
_CHILD_DRAIN_TIMEOUT = 10  # seconds: each bounded post-deadline step (drain, exit wait, reap)

# Reentrancy latch for the depth-refusal DRIVE in _not_run_vectors: a nested vectors call
# that reaches the drive with the refusal reverted must not run the three-value loop again
# (three more drives per level: an exponential chain no recursion limit ends), so the loop
# runs only at the outermost level and a nested drive falls through to its first spied
# launch attempt, which the outer drive reports as the named failure, bounded in process.
_DEPTH_DRIVE_ACTIVE = False


class _ChildOutcome:
    """What _run_bounded_child observed. returncode is None when the leader could not be
    reaped within the reap bound, and ALSO whenever anchor_lost is True: a status this
    launcher did not collect itself is never reported (CPython's Popen.wait turns the ECHILD
    of an already reaped child into 0; this launcher reaps with its own waitid instead, so a
    lost anchor can never read as an exit of 0); stdout and stderr are BYTES (text-mode
    newline translation would split a CR-separated prefix into a line of its own and defeat
    an exact line match); timed_out means the wait bound elapsed first, so the group kill
    was attempted (sent only when the anchor check before it found the leader UNREAPED);
    ready is False when a requested readiness handshake never produced a complete line;
    survivors means the group still had a member when the bounded post-reap OBSERVATION
    ended (nothing is ever signalled after the reap); anchor_lost means an anchor check or
    the reap found the leader ALREADY REAPED by another path (SIGCHLD ignored in a way the
    launch check cannot see, or an outside reap), or the cleanup's anchor check could not
    be made, so every destructive group signal still to come was withheld. Ordinary
    vector-entry callers treat timed_out,
    a None returncode, ready False and survivors True each as a failure, so a lost anchor
    reaches every one of them as a failure through its None returncode; the launcher's own
    NEGATIVE vectors are the deliberate exceptions (launcher-timeout and
    launcher-readiness-window REQUIRE timed_out True, launcher-group-replacement REQUIRES
    survivors True, and the launcher-anchor vectors REQUIRE anchor_lost True)."""

    def __init__(self, returncode, stdout, stderr, timed_out, survivors, ready=True,
                 anchor_lost=False):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out
        self.survivors = survivors
        self.ready = ready
        self.anchor_lost = anchor_lost


def _child_depth_marker_present():
    """True when _CHILD_DEPTH_ENV is set AT ALL, the empty string included: presence is what
    the bounded launcher guarantees and what an inheriting caller may hand down, so the
    refusal keyed on this helper triggers on membership, never on truthiness (an inherited
    empty value refuses the same way: fail closed)."""
    return _CHILD_DEPTH_ENV in os.environ


def _ready_line_complete(ready_path):
    """True once the readiness file exists and holds a complete (LF-terminated) line."""
    try:
        with open(ready_path, "rb") as fh:
            return fh.read().endswith(b"\n")
    except OSError:
        return False


def _await_readiness(proc, ready_path):
    """Wait, bounded by _CHILD_TIMEOUT, for the child's readiness handshake: a complete
    LF-terminated line at ready_path, which the child writes BEFORE entering the phase the
    caller's timeout window is meant to cover. Returns False when the bound elapses or the
    leader exits without completing it; the leader is NOT reaped here either way."""
    import time
    deadline = time.monotonic() + _CHILD_TIMEOUT
    while True:
        if _ready_line_complete(ready_path):
            return True
        exited = os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
        if exited is not None or time.monotonic() >= deadline:
            # one last look: the write may have landed between the read and the exit
            return _ready_line_complete(ready_path)
        time.sleep(0.05)


def _drain_pipes_unreaped(proc, deadline, out, err):
    """Read the child's pipes toward EOF until DEADLINE WITHOUT reaping the leader (no
    communicate, no wait): the unreaped leader keeps anchoring the group id so a later group
    signal can never land on a reused id. Each pipe is closed exactly once, at its own EOF;
    a pipe still open when the deadline passes is left for the launcher's own close. Returns
    True when every pipe reached EOF within the bound."""
    import selectors
    import time
    sel = selectors.DefaultSelector()
    try:
        for pipe, buf in ((proc.stdout, out), (proc.stderr, err)):
            if pipe is not None and not pipe.closed:
                os.set_blocking(pipe.fileno(), False)
                sel.register(pipe, selectors.EVENT_READ, buf)
        while sel.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            for key, _events in sel.select(min(remaining, 0.5)):
                try:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                except BlockingIOError:
                    continue
                if chunk:
                    key.data.extend(chunk)
                else:
                    sel.unregister(key.fileobj)
                    key.fileobj.close()
        return True
    finally:
        sel.close()


def _await_exit_unreaped(pid, deadline):
    """Wait, bounded by DEADLINE, for PID to exit WITHOUT reaping it (WNOWAIT): the exited
    leader stays an unreaped zombie that keeps anchoring its process-group id until the
    launcher has sent the final group signal. Returns True once the exit was observed."""
    import time
    while True:
        if os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT | os.WNOHANG) is not None:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _leader_unreaped(proc):
    """The ANCHOR CHECK made before EVERY destructive group signal: True while the leader is
    still this process's own UNREAPED child (live, or a zombie), OBSERVED with WNOWAIT and
    never reaped here; False when the anchor is LOST (Popen already holds a returncode, or
    the observation raises ChildProcessError because another path reaped the leader). Any
    other failure propagates, so an anchor that cannot be verified is never treated as
    held. It relies on two PRECONDITIONS: SIGCHLD is not ignored (_run_bounded_child
    refuses to launch under SIG_IGN; ignored anyway, the kernel reaps the leader itself and
    this check reports the anchor lost), and this process is the leader's ONLY reaper.
    What it cannot exclude: an outside reap landing
    AFTER this observation and BEFORE the signal it guards (no unsynchronized observation
    can); the launcher's reap then finds the leader gone and reports the anchor lost, but
    that signal has already been sent."""
    if proc.returncode is not None:
        return False
    try:
        os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
    except ChildProcessError:
        return False
    return True


def _reap_leader(pid, deadline):
    """The launcher's ONE reap, bounded by DEADLINE: waitid WITHOUT WNOWAIT, so the status
    comes from this very call. Returns (returncode, lost): the collected returncode (the
    exit status, or minus the signal number for a killed leader) with lost False; (None,
    True) when the leader is no longer this process's child (ChildProcessError: another path
    reaped it, so no status is collected and none is made up); (None, False) when the bound
    elapsed first."""
    import time
    while True:
        try:
            info = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG)
        except ChildProcessError:
            return None, True
        if info is not None:
            if info.si_code == os.CLD_EXITED:
                return info.si_status, False
            return -info.si_status, False  # CLD_KILLED or CLD_DUMPED: the signal number
        if time.monotonic() >= deadline:
            return None, False
        time.sleep(0.05)


class _SigchldIgnoredError(RuntimeError):
    """Raised by _run_bounded_child BEFORE anything is launched when SIGCHLD is ignored
    (SIG_IGN) in this process. The kernel would then reap each leader itself at its exit,
    so no anchor check could hold the leader's process-group id and the launcher's own
    reap could collect no status. A parent's ignore survives exec, so a self-test started
    by such a parent inherits it and is refused under this name, not by a bare
    ChildProcessError from a later wait."""


def _run_bounded_child(args, env, timeout=_CHILD_TIMEOUT, ready_path=None):
    """The one bounded launcher every fresh Python VECTOR-ENTRY self-test child goes
    through. That scope is deliberately narrower than every child process of this tool: the
    parent self-test's direct git launches (the real-git GD-130 fixture's git calls, run
    without this launcher) and the launcher-timeout child's own plain descendant are
    OUTSIDE this launcher and its bounds.
    args is the argv tail after the fixed [sys.executable, "-I", "-B"] head, so every
    vector-entry child runs this interpreter. Bounds: (1) the child's environment carries
    _CHILD_DEPTH_ENV, whose presence-refusal at the top of _not_run_vectors stops any child
    of this tool from launching vector children of its own, so a reverted guard ends at
    depth 1; (2) the child leads its OWN session and so its own process group, and EVERY
    destructive group signal, the first timeout kill and the final one alike, is preceded
    by the ANCHOR CHECK (_leader_unreaped: the leader OBSERVED with WNOWAIT, never reaped
    first; the pipes are drained and the exit observed the same way) and is sent only when
    that check finds the leader still unreaped (live, or a zombie); a leader another path
    already reaped means the anchor is LOST, and an observation failing any other way leaves
    it UNVERIFIABLE (that failure also raises: during the lifecycle it is the primary
    exception, in the cleanup a kept stage failure); either way the signal is withheld,
    anchor_lost is True and the outcome's returncode is None. After the single reap at the
    end the group is only ever OBSERVED (signal 0), never signalled destructively, and a
    member seen then surfaces only as survivors True; (3) every wait, drain and probe
    carries its own bound, so a pipe held open outside the killed group only delays the
    bounded drain and is reported through timed_out (returncode None is reserved for a
    leader that could not be reaped within the reap bound, or a lost anchor), never a stall
    of the whole self-test. When ready_path is given, the launcher first waits, bounded by
    _CHILD_TIMEOUT, for the child to publish a complete LF-terminated line there, and the
    TIMEOUT window starts only at that readiness; a handshake that never completes comes
    back as ready False and the group is killed the same anchored way. What the group kill
    bounds is the group's MEMBERS: group membership, not ancestry, is the boundary, so a
    descendant that moved itself into a new session, or into another process group with
    setpgid, is outside the group and outside this bound.
    Exception safety: the output buffers and every flag the cleanup reads exist BEFORE the
    launch (an allocation failure there launches nothing), and the try/finally starts the
    moment Popen returns. The finally runs each cleanup stage as its OWN guarded step, in
    order: the stdout close, the stderr close, the anchor check, the final destructive group
    signal and the one bounded reap; a stage that raises (any exception class) never skips
    a later stage. An exception from the lifecycle itself (the PRIMARY) is re-raised
    unchanged; the first cleanup-stage failure is kept and raised only when nothing else
    raised. Attempting a cleanup step cannot guarantee it succeeds: a close, signal or reap
    that fails is reported (raised, or set aside for the primary), never retried, so a
    failed signal can leave the group running and a failed reap leaves returncode None.
    PRECONDITIONS, relied on by every anchor check: SIGCHLD NOT IGNORED (ignored, the kernel
    reaps the leader itself and the check reports the anchor lost) and this process as the
    leader's ONLY reaper. The first is CHECKED once, before anything is created or
    launched: when signal.getsignal reports SIGCHLD as SIG_IGN (a parent's ignore survives
    exec), the launcher raises _SigchldIgnoredError and launches nothing. That check sees
    only what Python recorded: a disposition changed after it, or set outside Python (a
    C-level ignore or SA_NOCLDWAIT, which getsignal does not report), is not refused, and a
    leader the kernel reaps under it surfaces as a ChildProcessError from a wait or as a
    lost anchor. The second precondition is stated, not checked. The RESIDUAL they leave open:
    an outside reap landing after an anchor check and before the signal it guards is not
    excluded (no unsynchronized observation can exclude it); the reap that follows then
    finds the leader gone, so the outcome still reports anchor_lost True and returncode
    None, but that one signal was already sent."""
    import signal
    import time
    # the SIGCHLD precondition, checked before anything exists: ignored, the kernel would
    # reap each leader itself, so the launch is refused by name and nothing is launched
    if signal.getsignal(signal.SIGCHLD) == signal.SIG_IGN:
        raise _SigchldIgnoredError(
            "refused before launch: SIGCHLD is ignored (SIG_IGN) in this process, possibly "
            "inherited across exec from a parent that ignores it; the kernel would reap each "
            "launched leader itself, so no anchor check could hold its process-group id and "
            "no exit status could be collected (run with SIGCHLD at SIG_DFL)")
    child_env = dict(env)
    child_env[_CHILD_DEPTH_ENV] = "1"
    # everything the cleanup reads exists BEFORE the launch: an allocation failure here
    # launches nothing, so the protected region can start the moment Popen returns
    out = bytearray()
    err = bytearray()
    ready = True
    timed_out = False
    anchor_lost = False
    returncode = None
    primary_raised = False
    cleanup_error = None
    proc = subprocess.Popen([sys.executable, "-I", "-B", *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=child_env, start_new_session=True)
    try:
        if ready_path is not None:
            ready = _await_readiness(proc, ready_path)
        # the execution window starts only HERE, at readiness (or at launch when no
        # handshake was requested): the deadline is taken AFTER the readiness wait returns
        deadline = time.monotonic() + timeout
        timed_out = not _drain_pipes_unreaped(proc, deadline, out, err)
        if ready and not timed_out:
            # the pipes are at EOF; wait, bounded by the same deadline, for the leader's
            # exit, observed with WNOWAIT and never reaped here
            timed_out = not _await_exit_unreaped(proc.pid, deadline)
        if not ready or timed_out:
            # the FIRST destructive signal: the whole group, sent BEFORE the bounded
            # post-timeout drain below and only when the anchor check just before it finds
            # the leader still unreaped; a lost anchor withholds it, and the exit wait too
            # (that leader is no longer this process's child)
            if _leader_unreaped(proc):
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                anchor_lost = True
            drain_deadline = time.monotonic() + _CHILD_DRAIN_TIMEOUT
            _drain_pipes_unreaped(proc, drain_deadline, out, err)
            if not anchor_lost:
                _await_exit_unreaped(proc.pid, drain_deadline)
    except BaseException:
        primary_raised = True  # the finally below still cleans up; the raise is UNCHANGED
        raise
    finally:
        # each cleanup stage is its OWN guarded step: a stage that raises (any exception
        # class) never skips a later one, and the first stage failure is kept until every
        # stage has run
        for pipe in (proc.stdout, proc.stderr):
            try:
                if pipe is not None and not pipe.closed:
                    pipe.close()  # EOF never came in bound, or the lifecycle above raised
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        anchored = False
        if not anchor_lost:
            try:
                anchored = _leader_unreaped(proc)  # the anchor check before the final signal
            except BaseException as exc:  # UNVERIFIABLE: never treated as held
                if cleanup_error is None:
                    cleanup_error = exc
        if anchored:
            try:
                # the FINAL destructive signal, sent while the unreaped (zombie, or
                # unkillable and live) leader still anchors the group id; harmless when
                # the group is already empty apart from that leader
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
        else:
            anchor_lost = True  # lost or unverifiable: the final signal is withheld
        try:
            reaped, lost = _reap_leader(proc.pid, time.monotonic() + _CHILD_DRAIN_TIMEOUT)
            if lost:
                anchor_lost = True
            if proc.returncode is None and (reaped is not None or lost):
                # Popen's own bookkeeping, so it never waits on this pid again (its later
                # cleanup could otherwise reap a reused pid): after a lost anchor it is
                # closed the way CPython closes it on ECHILD, while the OUTCOME below
                # carries None, never that 0
                proc.returncode = reaped if reaped is not None else 0
            returncode = None if anchor_lost else reaped
        except BaseException as exc:
            if cleanup_error is None:
                cleanup_error = exc
        if cleanup_error is not None and not primary_raised:
            raise cleanup_error  # a cleanup failure surfaces only when nothing else raised
    # bounded post-reap OBSERVATION: after the reap the group id may be reused by an
    # unrelated group, so the probe uses signal 0 only and nothing is ever signalled
    # destructively here; a member still seen at the end surfaces as survivors True
    survivors = True
    for _ in range(100):
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            survivors = False
            break
        time.sleep(0.1)
    return _ChildOutcome(returncode, bytes(out), bytes(err), timed_out, survivors, ready,
                         anchor_lost)


def _exact_line_count(stdout_bytes, line):
    """Count LINE occurring as an exact LF-delimited line of STDOUT_BYTES. The split is on
    LF over captured BYTES: text-mode newline translation would turn a CR-separated prefix
    into a line of its own and let it count as exact, so a prefixed, suffixed, CR-prefixed
    or CR-suffixed spelling all count 0 here."""
    return stdout_bytes.split(b"\n").count(line.encode("utf-8"))


class _SetupAllocationError(OSError):
    """An OSError raised by the fixture setup's temp-directory ALLOCATION, re-raised (chained) at
    the allocation call itself by _mark_setup_allocations. The public entry's handler catches
    ONLY this type, so the catch is bound to the allocation: an OSError from the lifecycle
    wrapper's write_text or chmod, or from anywhere else in the setup, propagates unchanged."""


def _mark_setup_allocations():
    """Type the setup's temp-directory allocations at the call: until the returned restore
    callable runs, an OSError raised by tempfile.mkdtemp is re-raised as _SetupAllocationError.
    The entry scrub's scratch HOME and the lifecycle's wrapper directory both allocate through
    tempfile.mkdtemp (TemporaryDirectory calls it) and nothing else in that setup allocates a
    temp directory, so this binds the public entry's handler to exactly the allocation calls.
    The restore is idempotent and runs before the isolated cases, whose own allocations stay
    plain OSError (guarded in-run, or propagating)."""
    real_mkdtemp = tempfile.mkdtemp

    def marked_mkdtemp(*args, **kwargs):
        try:
            return real_mkdtemp(*args, **kwargs)
        except OSError as exc:
            raise _SetupAllocationError(str(exc)) from exc

    def restore():
        if tempfile.mkdtemp is marked_mkdtemp:
            tempfile.mkdtemp = real_mkdtemp

    tempfile.mkdtemp = marked_mkdtemp
    return restore


_LIFECYCLE_ALLOC_PREFIX = "aiqt-fixture-git-bin-"
_NO_VECTORS_PASS_NOTE = "SELF-TEST PASS (vectors not run in this nested call)"


def _mkdtemp_prefix(args, kwargs):
    if kwargs.get("prefix"):
        return str(kwargs["prefix"])
    return str(args[1]) if len(args) > 1 and args[1] else ""


def _lifecycle_alloc_failure(got):
    """The lifecycle entry vector's exact named failure text for a given outcome; the
    unmarked-drifted-lifecycle vector asserts this exact text (with got "exit 0") on its
    child's stdout."""
    want = "NOT RUN: {}: fixture setup failed: no writable temp directory".format(_EVERY_CASE)
    return ("(entry vector lifecycle-allocation) an OSError scoped to the lifecycle's "
            "wrapper-directory allocation must be the whole-run NOT RUN failure {!r} (exit 1), "
            "never a traceback or a smaller scope (got {})".format(want, got))


def _entry_vector_lifecycle(failures):
    """Drives the REAL lifecycle-allocation nested public entry; the injection's match is read
    from _LIFECYCLE_ALLOC_PREFIX at call time, so the unmarked-drifted-lifecycle child can
    drift it and drive this same function."""
    import io
    from contextlib import redirect_stderr, redirect_stdout
    # Entry positive: an OSError scoped to the lifecycle's wrapper-directory allocation is the
    # named whole-run NOT RUN failure (the typed marker binds the handler at the allocation).
    # An OSError that ESCAPES the nested entry instead (the allocation marking removed) is this
    # vector's own NAMED failure, never an abort of the whole self-test.
    real_entry_mkdtemp = tempfile.mkdtemp

    def _lifecycle_alloc_unavailable(*args, **kwargs):
        if _LIFECYCLE_ALLOC_PREFIX in _mkdtemp_prefix(args, kwargs):
            raise OSError("injected: unavailable")
        return real_entry_mkdtemp(*args, **kwargs)

    tempfile.mkdtemp = _lifecycle_alloc_unavailable
    out = io.StringIO()
    rc, escaped = None, None
    try:
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            rc = _nested_entry_no_vectors()
    except OSError as exc:
        escaped = exc
    finally:
        tempfile.mkdtemp = real_entry_mkdtemp
    want = "NOT RUN: {}: fixture setup failed: no writable temp directory".format(_EVERY_CASE)
    if rc != 1 or want not in out.getvalue():
        got = ("an escaped {}: {}".format(type(escaped).__name__, escaped)
               if escaped is not None else "exit {}".format(rc))
        failures.append(_lifecycle_alloc_failure(got))


def _entry_vector_wrapper(failures):
    import io
    import pathlib
    from contextlib import redirect_stderr, redirect_stdout
    # Entry negatives: a PermissionError injected at the lifecycle wrapper's write_text or at
    # its chmod is NOT an allocation failure and must PROPAGATE (red when the entry catches
    # plain OSError instead of the typed allocation error).
    for attr in ("write_text", "chmod"):
        real_method = getattr(pathlib.Path, attr)
        denial = "injected: wrapper {} denied".format(attr)

        def _denying(self, *args, _real=real_method, _denial=denial, **kwargs):
            if self.name == "git" and _LIFECYCLE_ALLOC_PREFIX in str(self):
                raise PermissionError(_denial)
            return _real(self, *args, **kwargs)

        setattr(pathlib.Path, attr, _denying)
        propagated = False
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                _nested_entry_no_vectors()
        except PermissionError as exc:
            propagated = denial in str(exc)
        finally:
            setattr(pathlib.Path, attr, real_method)
        if not propagated:
            failures.append("(entry vector wrapper-{0}) an injected PermissionError at the "
                            "lifecycle wrapper's {0} must PROPAGATE out of the public entry, "
                            "never be reported as the setup NOT RUN".format(attr))


def _entry_vector_post_entry(failures):
    import io
    from contextlib import redirect_stderr, redirect_stdout
    # Entry negatives: anything raised AFTER the setup entered must propagate (the entered
    # guards), and a pre-entry RuntimeError WITHOUT the lifecycle's git message must propagate
    # (the message filter).
    _mod = sys.modules[__name__]
    real_isolated = _mod._self_test_main_isolated

    def _post_entry_oserror():
        raise _SetupAllocationError("injected: post-entry failure")

    def _post_entry_runtime():
        raise RuntimeError("self-test requires git (injected: post-entry failure)")

    for label, raiser in (("post-entry typed OSError", _post_entry_oserror),
                          ("post-entry git-message RuntimeError", _post_entry_runtime)):
        _mod._self_test_main_isolated = lambda *_args, _raiser=raiser, **_kwargs: _raiser()
        propagated = False
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                _nested_entry_no_vectors()
        except (OSError, RuntimeError) as exc:
            propagated = "injected: post-entry failure" in str(exc)
        finally:
            _mod._self_test_main_isolated = real_isolated
        if not propagated:
            failures.append("(entry vector {}) an exception raised AFTER the setup entered must "
                            "PROPAGATE out of the public entry, never be reported as the setup "
                            "NOT RUN".format(label))


def _entry_vector_pre_entry(failures):
    import io
    import shutil
    from contextlib import redirect_stderr, redirect_stdout
    real_which = shutil.which

    def _raising_which(_name):
        raise RuntimeError("injected: unrelated pre-entry failure")

    shutil.which = _raising_which
    propagated = False
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            _nested_entry_no_vectors()
    except RuntimeError as exc:
        propagated = "injected: unrelated pre-entry failure" in str(exc)
    finally:
        shutil.which = real_which
    if not propagated:
        failures.append("(entry vector pre-entry unrelated RuntimeError) a setup RuntimeError "
                        "WITHOUT the lifecycle's git message must PROPAGATE out of the public "
                        "entry, never be reported as the git-absent NOT RUN")


def _entry_vector_pass_text(failures):
    import io
    from contextlib import redirect_stderr, redirect_stdout
    # No-vectors PASS text: a clean nested no-vectors entry must complete with exit 0 and
    # print the DISTINCT no-vectors PASS line, never the full-run "SELF-TEST PASS:" text,
    # so the full PASS line is only ever printed by a run whose vectors ran.
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        rc = _nested_entry_no_vectors()
    text = out.getvalue()
    if rc != 0 or _NO_VECTORS_PASS_NOTE + ":" not in text or "SELF-TEST PASS:" in text:
        failures.append("(vector no-vectors-pass-text) a clean nested no-vectors entry "
                        "must exit 0 and print the distinct no-vectors PASS line {!r}, "
                        "never the full-run SELF-TEST PASS line (got exit {})".format(
                            _NO_VECTORS_PASS_NOTE + ":", rc))


def _not_run_vectors(failures):
    """Red-on-revert vectors for the skips that printed SELF-TEST PASS (PARTIAL) and exited 0,
    plus the narrowness of the public entry's setup handlers.

    Scoped layer: a nested self-test run with an unavailable temp directory (mkdtemp raises
    OSError, blanket or scoped to the bigint fixture allocation so the run continues PAST it),
    one with no symlink support (symlink creation raises OSError), and one with git absent (the
    lookup returns None), must each exit nonzero AND name the unrun cases on a NOT RUN line.
    These enter through _self_test_main_isolated, AFTER self_test_main's entry scrub and
    lifecycle setup already succeeded.

    Entry-narrowness layer (in-process nested public entries; git resolves through THIS run's
    lifecycle wrapper on any host that reached the vectors, and the scrub's scratch HOME is
    already cached, so only the lifecycle's wrapper directory still allocates at the nested
    entry): an OSError scoped to that allocation must be the entry's whole-run NOT RUN failure,
    while an injected PermissionError at the lifecycle wrapper's write_text or at its chmod, an
    unrelated pre-entry RuntimeError, and an exception raised AFTER entry (OSError-typed or
    carrying the git message) must each PROPAGATE out of self_test_main, never be reported as
    the setup NOT RUN. The seam vectors drive each of these call sites, and the
    no-vectors-pass-text drive, against a recorder to require the explicit no-vectors seam,
    so every in-process nested public entry goes through _nested_entry_no_vectors.

    Fresh-process public-entry layer: the in-process injections cannot reach a FRESH process's
    setup (a new scrub allocation, a PATH with no git resolvable), so each child below runs this
    tool as __main__ --self-test with the failure injected BEFORE the scrub or the lifecycle can
    run, and must exit 1 with the entry's whole-run NOT RUN line on stdout, never a bare
    traceback. EVERY fresh Python vector-entry child runs through _run_bounded_child, the
    one bounded launcher (its own process group, the _CHILD_DEPTH_ENV marker whose
    presence-refusal at the top of this body bounds any child at depth 1, bytes capture,
    and a bound on every wait, drain and probe); the parent self-test's direct git launches
    and the launcher-timeout child's own plain descendant are outside that launcher. Every
    PUBLIC-ENTRY child environment except the unmarked-drifted-lifecycle child's
    additionally carries _SUPPRESS_VECTORS_ENV (the launcher-group, launcher-timeout and
    launcher-cleanup probe children carry only the depth marker, and the
    launcher-group-replacement, launcher-readiness-window, launcher-anchor and
    launcher-sigchld-refusal vectors launch nothing: modelled doubles, or a spied Popen),
    so a marked child records the named suppression
    NOT RUN and exits
    1 without printing any PASS text; the unmarked child drives only the explicit no-vectors
    seam, whose nested entry prints the distinct no-vectors note, never the full PASS line.
    The spy child proves ONLY that a marker-carrying child reaches its own named
    SELF-TEST FAIL without launching any subprocess (subprocess.run and subprocess.Popen are
    both replaced); its injections leave the end-to-end,
    disk-kind and real-git groups NOT RUN inside it, so it does not prove those groups
    complete."""
    import io
    import pathlib
    import shutil
    from contextlib import redirect_stderr, redirect_stdout
    from _git_fixture_env import fixture_git_lifecycle

    if _child_depth_marker_present():
        # This entry already runs INSIDE a child the bounded launcher started (every
        # vector-entry child carries the marker), or the variable was inherited from the
        # caller's environment, possibly empty: PRESENCE, never truthiness, triggers this
        # refusal. Reaching the vectors under the marker means a no-vectors seam or the
        # marked-child gate did not hold, or the inherited variable is the cause: refuse
        # with ONE named failure instead of launching children (a child never launches
        # vector children of its own), so any such chain ends at depth 1.
        _not_run(failures, "the red-on-revert NOT RUN and entry vectors",
                 "refused at child depth 1 ({} is present, with any value, even empty, "
                 "either set by the bounded launcher or inherited from the caller's "
                 "environment): a nested entry reached the vectors, so a no-vectors seam "
                 "or the marked-child gate did not hold, or the variable was "
                 "inherited".format(_CHILD_DEPTH_ENV))
        return

    # Launcher depth-refusal vector (in-process): with _CHILD_DEPTH_ENV present (the empty
    # string, "0" and "1" alike: presence, never truthiness, triggers the refusal) and both
    # subprocess.run and subprocess.Popen replaced by spies, a nested _not_run_vectors call
    # must record exactly ONE failure, the named depth refusal, and launch NOTHING: red when
    # the depth refusal is removed, and red for the empty value when the refusal reverts to
    # truthiness (the spies turn any launch attempt, or anything else the unrefused vectors
    # do, into this vector's named failure, bounded in process; the _DEPTH_DRIVE_ACTIVE
    # latch keeps a reverted refusal LINEAR: a nested drive skips this loop, so each value
    # ends at its nested call's first spied launch, never an exponential chain).
    def _spy_launch(*_args, **_kwargs):
        raise RuntimeError("spy: a depth-marked nested vectors call tried to launch")

    prior_depth = os.environ.get(_CHILD_DEPTH_ENV)
    real_spied_run, real_spied_popen = subprocess.run, subprocess.Popen
    global _DEPTH_DRIVE_ACTIVE
    prior_drive = _DEPTH_DRIVE_ACTIVE
    _DEPTH_DRIVE_ACTIVE = True
    for depth_value in (() if prior_drive else ("", "0", "1")):
        os.environ[_CHILD_DEPTH_ENV] = depth_value
        subprocess.run, subprocess.Popen = _spy_launch, _spy_launch
        depth_failures = []
        depth_got = None
        try:
            _not_run_vectors(depth_failures)
        except Exception as exc:  # the spies raise on any launch attempt
            depth_got = "an escaped {}: {}".format(type(exc).__name__, exc)
        finally:
            subprocess.run, subprocess.Popen = real_spied_run, real_spied_popen
            if prior_depth is None:
                os.environ.pop(_CHILD_DEPTH_ENV, None)
            else:
                os.environ[_CHILD_DEPTH_ENV] = prior_depth
        if depth_got is None and (len(depth_failures) != 1
                                  or "refused at child depth 1" not in depth_failures[0]):
            depth_got = "{} failure(s): {!r}".format(len(depth_failures), depth_failures[:2])
        if depth_got is not None:
            failures.append("(vector child-depth-refusal value {!r}) a nested vectors call "
                            "with {} present, whatever its value, must record exactly one "
                            "named depth refusal and launch nothing (got {})".format(
                                depth_value, _CHILD_DEPTH_ENV, depth_got))
    _DEPTH_DRIVE_ACTIVE = prior_drive
    # The ABSENT side of the same boundary: this very call reached the vectors only under
    # an absent marker, and the predicate must agree now that the loop restored absence, so
    # a predicate that refused on absence too could never turn the full run green.
    if _child_depth_marker_present():
        failures.append("(vector child-depth-absent) with {} absent the depth predicate "
                        "must be False so a full run reaches these vectors (got "
                        "True)".format(_CHILD_DEPTH_ENV))

    # Launcher group-and-marker vector (fresh process): a trivial child run through the ONE
    # bounded launcher reports whether it leads its own process group and the depth-marker
    # value it inherited: red when start_new_session is dropped (the child then joins THIS
    # group and reports False) or when the launcher stops setting _CHILD_DEPTH_ENV.
    group_probe = ("import os, sys\n"
                   "print(os.getpgid(0) == os.getpid(), os.environ.get(sys.argv[1]))\n")
    outcome = _run_bounded_child(["-c", group_probe, _CHILD_DEPTH_ENV], os.environ)
    if (outcome.returncode != 0 or outcome.stdout != b"True 1\n"
            or outcome.timed_out or outcome.survivors):
        failures.append("(vector launcher-group) a child run through the bounded launcher "
                        "must lead its OWN process group and carry {}=1 (want stdout "
                        "b'True 1\\n'; got exit {}, stdout {!r}, timed out {}, survivors "
                        "{})".format(_CHILD_DEPTH_ENV, outcome.returncode, outcome.stdout[:80],
                                     outcome.timed_out, outcome.survivors))

    # Launcher group-replacement vector (in-process, modelled doubles): once the leader has
    # been REAPED the kernel may hand its process-group id to an UNRELATED new group, so a
    # post-reap probe that still "sees a member" may be seeing that replacement. The doubles
    # model exactly that schedule: the leader exits at once, readiness completes at the
    # first waitid observation, and every group probe afterwards keeps reporting a member
    # (the replacement). The waitid double HONOURS its flags (WNOWAIT observes without
    # reaping; a call WITHOUT WNOWAIT is a reap and is recorded exactly like a wait or a
    # communicate) and validates the target and the required flags, so the launcher must
    # perform EXACTLY ONE reap (its own final waitid without WNOWAIT), send its destructive
    # group signal only while the unreaped leader anchors the id, and NEVER signal
    # destructively after that reap (the replacement sighting may surface only as survivors
    # True): red when a post-reap cleanup kill is reintroduced, red when the final kill
    # moves after the reap, red when the reap reverts to Popen.wait, and red when os.WNOWAIT
    # is removed from _await_readiness, _await_exit_unreaped or the anchor check (the
    # un-flagged waitid then counts as a SECOND reap and the final kill lands post-reap).
    import signal
    import time
    gmod = globals()
    killpg_events = []
    reap_events = []
    model_errors = []
    ready_seen = {"complete": False}

    class _ModelSiginfo:
        """A modelled waitid result: only the two fields the launcher reads."""

        def __init__(self, si_code, si_status):
            self.si_code = si_code
            self.si_status = si_status

    class _ReplacedGroupProc:
        """A Popen double whose leader is reapable at once and whose numeric group id is
        then reused: the patched killpg below always sees a member. No pipes (stdout and
        stderr None): the drain has nothing to read and the schedule stays modelled."""
        pid = 4194305  # modelled only: killpg and waitid are both patched below
        stdout = None
        stderr = None

        def __init__(self, *_args, **_kwargs):
            self.returncode = None

        def communicate(self, timeout=None):  # a reap, however the launcher drains
            self.returncode = 0
            reap_events.append("communicate")
            return b"", b""

        def wait(self, timeout=None):  # a reap through Popen
            self.returncode = 0
            reap_events.append("wait")
            return 0

    def _replaced_killpg(pgid, sig):
        if pgid != _ReplacedGroupProc.pid:
            model_errors.append("killpg target {!r} (want {!r})".format(
                pgid, _ReplacedGroupProc.pid))
        killpg_events.append((sig, bool(reap_events)))

    def _replaced_waitid(idtype, wpid, options):
        # the flags are honoured and validated: WNOWAIT observes without reaping; its
        # ABSENCE is a reap and is recorded; the first call also completes the modelled
        # readiness handshake, so _await_readiness exercises its own waitid here
        if idtype != os.P_PID or wpid != _ReplacedGroupProc.pid:
            model_errors.append("waitid target ({!r}, {!r}) (want (P_PID, {!r}))".format(
                idtype, wpid, _ReplacedGroupProc.pid))
        if not options & os.WEXITED:
            model_errors.append("waitid without WEXITED (options {!r})".format(options))
        if not options & os.WNOWAIT:
            reap_events.append("waitid")  # a reap: the flags say so
        ready_seen["complete"] = True
        return _ModelSiginfo(os.CLD_EXITED, 0)  # the modelled leader is already waitable

    def _replaced_ready_line(_path):
        # the modelled readiness file: complete only after the first waitid observation
        return ready_seen["complete"]

    def _no_sleep(*_args, **_kwargs):
        return None

    real_rg_popen, real_rg_killpg = subprocess.Popen, os.killpg
    real_rg_waitid, real_rg_sleep = os.waitid, time.sleep
    real_rg_ready_line = _ready_line_complete
    subprocess.Popen = _ReplacedGroupProc
    os.killpg = _replaced_killpg
    os.waitid = _replaced_waitid
    time.sleep = _no_sleep
    gmod["_ready_line_complete"] = _replaced_ready_line
    try:
        outcome = _run_bounded_child(["-c", "pass"], os.environ, timeout=5,
                                     ready_path="modelled: never opened")
    finally:
        subprocess.Popen, os.killpg = real_rg_popen, real_rg_killpg
        os.waitid, time.sleep = real_rg_waitid, real_rg_sleep
        gmod["_ready_line_complete"] = real_rg_ready_line
    post_reap_kills = [sig for sig, after_reap in killpg_events if sig != 0 and after_reap]
    anchored_kill = any(sig != 0 and not after_reap for sig, after_reap in killpg_events)
    if (post_reap_kills or not anchored_kill or model_errors or reap_events != ["waitid"]
            or outcome.returncode != 0 or not outcome.ready or not outcome.survivors):
        failures.append("(vector launcher-group-replacement) under a modelled reaped-leader"
                        "/replaced-group schedule the launcher must perform EXACTLY ONE "
                        "reap (its own final waitid), send its destructive group signal "
                        "while the unreaped leader anchors the id and never after the "
                        "reap, and aim every kill and waitid at the leader with the "
                        "required flags, where the replacement surfaces only as survivors "
                        "True (got post-reap kills {!r}, anchored kill {}, reaps {!r}, "
                        "model errors {!r}, exit {!r}, ready {}, survivors {})".format(
                            post_reap_kills, anchored_kill, reap_events, model_errors,
                            outcome.returncode, outcome.ready, outcome.survivors))

    # Launcher readiness-window vector (in-process, modelled doubles, virtual clock): the
    # execution window must start AT readiness, never at launch, last exactly the requested
    # timeout, and on timeout the FIRST destructive group kill must come BEFORE the bounded
    # post-timeout drain. The virtual clock advances only inside time.sleep, so the
    # schedule is deterministic: readiness completes at about 100.0 on that clock, far past
    # a 5-second window measured from launch, and the modelled leader exits only once the
    # group kill lands. The vector records the ACTUAL readiness completion (the first
    # complete-line observation) and the deadline the launcher hands its first drain, and
    # requires that deadline to be readiness plus the requested timeout and the first kill
    # to land at or after it and before it plus one polling increment of the model (the
    # 0.05-second poll of _await_exit_unreaped, the loop that observes the deadline here).
    # Red when the readiness gate is routed around (ready = True in its place), when the
    # deadline is taken before the readiness wait, for a zero-length or an overlong window,
    # and when the first timeout kill ALONE is removed (the first recorded kill then follows
    # the post-timeout drain). The window predicate itself is checked at each end, just
    # inside, exactly at and just outside, so neither bound can drift unseen.
    window_events = []
    window_clock = {"now": 0.0}
    window_killed = {"done": False}
    window_ready = {"at": None}
    window_ready_from = 100.0
    window_timeout = 5
    window_poll = 0.05

    def _window_bounds_ok(ready_at, drain_deadline, kill_at):
        # the requested window, from the ACTUAL readiness: the drain's deadline is
        # readiness plus the timeout, and the first kill lands at or after that deadline
        # and strictly before it plus one polling increment
        if ready_at is None or drain_deadline is None or kill_at is None:
            return False
        want = ready_at + window_timeout
        return abs(drain_deadline - want) <= 1e-9 and want <= kill_at < want + window_poll

    window_bound_misses = []
    for bound_label, bound_deadline, bound_kill, bound_want in (
            ("deadline just below", 104.999999, 105.0, False),
            ("deadline exactly at", 105.0, 105.0, True),
            ("deadline just above", 105.000001, 105.0, False),
            ("kill just below the deadline", 105.0, 104.999999, False),
            ("kill exactly at the deadline", 105.0, 105.0, True),
            ("kill just above the deadline", 105.0, 105.000001, True),
            ("kill just below one increment", 105.0, 105.049999, True),
            ("kill exactly one increment", 105.0, 105.0 + window_poll, False),
            ("kill just above one increment", 105.0, 105.050001, False)):
        if _window_bounds_ok(100.0, bound_deadline, bound_kill) is not bound_want:
            window_bound_misses.append(bound_label)
    if window_bound_misses:
        failures.append("(vector launcher-readiness-window-bounds) the window predicate "
                        "must accept a kill from readiness plus the timeout up to, not "
                        "including, one polling increment later, and only the deadline "
                        "readiness plus the timeout (wrong at: {})".format(
                            ", ".join(window_bound_misses)))

    def _window_monotonic():
        return window_clock["now"]

    def _window_sleep(duration):
        window_clock["now"] += duration

    class _WindowProc:
        """A Popen double that never exits on its own: unwaitable until the group kill
        lands, an unreaped zombie afterwards. No pipes: each drain returns at once and
        still marks its place in the recorded event order."""
        pid = 4194306  # modelled only: killpg and waitid are both patched below
        stdout = None
        stderr = None

        def __init__(self, *_args, **_kwargs):
            self.returncode = None

        def communicate(self, timeout=None):
            self.returncode = -9
            return b"", b""

        def wait(self, timeout=None):
            self.returncode = -9
            return -9

    def _window_killpg(_pgid, sig):
        if sig == 0:
            if window_killed["done"]:
                raise ProcessLookupError  # the killed, reaped group is empty
            return None
        window_events.append(("kill", window_clock["now"], None))
        window_killed["done"] = True

    def _window_waitid(_idtype, _wpid, _options):
        if window_killed["done"]:
            return _ModelSiginfo(os.CLD_KILLED, signal.SIGKILL)
        return None

    def _window_ready_line(_path):
        complete = window_clock["now"] >= window_ready_from
        if complete and window_ready["at"] is None:
            window_ready["at"] = window_clock["now"]  # the ACTUAL readiness completion
        return complete

    real_w_drain = _drain_pipes_unreaped

    def _window_drain(proc, deadline, out, err):
        window_events.append(("drain", window_clock["now"], deadline))
        return real_w_drain(proc, deadline, out, err)

    real_w_popen, real_w_killpg = subprocess.Popen, os.killpg
    real_w_waitid, real_w_sleep = os.waitid, time.sleep
    real_w_monotonic, real_w_ready_line = time.monotonic, _ready_line_complete
    subprocess.Popen = _WindowProc
    os.killpg = _window_killpg
    os.waitid = _window_waitid
    time.sleep = _window_sleep
    time.monotonic = _window_monotonic
    gmod["_ready_line_complete"] = _window_ready_line
    gmod["_drain_pipes_unreaped"] = _window_drain
    try:
        outcome = _run_bounded_child(["-c", "pass"], os.environ, timeout=window_timeout,
                                     ready_path="modelled: never opened")
    finally:
        subprocess.Popen, os.killpg = real_w_popen, real_w_killpg
        os.waitid, time.sleep = real_w_waitid, real_w_sleep
        time.monotonic = real_w_monotonic
        gmod["_ready_line_complete"] = real_w_ready_line
        gmod["_drain_pipes_unreaped"] = real_w_drain
    kill_idx = [i for i, event in enumerate(window_events) if event[0] == "kill"]
    drain_idx = [i for i, event in enumerate(window_events) if event[0] == "drain"]
    first_kill_at = window_events[kill_idx[0]][1] if kill_idx else None
    first_drain_deadline = window_events[drain_idx[0]][2] if drain_idx else None
    if (not outcome.ready or not outcome.timed_out or outcome.returncode != -9
            or outcome.survivors or len(drain_idx) < 2 or not kill_idx
            or not _window_bounds_ok(window_ready["at"], first_drain_deadline, first_kill_at)
            or kill_idx[0] > drain_idx[1]):
        failures.append("(vector launcher-readiness-window) on the modelled virtual-clock "
                        "schedule the {}-second window must start AT the actual readiness "
                        "(recorded at {!r}): the first drain's deadline must be readiness "
                        "plus the timeout, the first destructive group kill must land at "
                        "or after it and before it plus {} and precede the post-timeout "
                        "drain, with the leader then reaped (got ready {}, timed out {}, "
                        "returncode {!r}, survivors {}, first drain deadline {!r}, first "
                        "kill at {!r}, events {!r})".format(
                            window_timeout, window_ready["at"], window_poll, outcome.ready,
                            outcome.timed_out, outcome.returncode, outcome.survivors,
                            first_drain_deadline, first_kill_at, window_events))

    # Launcher anchor vectors (in-process, modelled doubles): EVERY destructive group
    # signal waits on the anchor check, and a lost anchor never reads as an exit of 0. The
    # Popen double's wait models CPython's Popen.wait on a leader another path already
    # reaped (its ECHILD reads as 0), so a launcher that reaps through Popen.wait reports
    # 0 here. first-kill: the leader was ALREADY REAPED before the first timeout kill
    # (every waitid raises ChildProcessError, as it does once SIGCHLD is ignored and the
    # kernel reaped the leader, and the modelled drain reports a pipe still held at the
    # deadline): no destructive group signal may be sent at all. final-signal: the leader
    # is reaped by another path after its exit was observed and before the final anchor
    # check: the final signal must be withheld. outside-reap: the outside reap lands
    # BETWEEN the final anchor observation and the final signal (modelled: inside the
    # killpg double, before the signal is recorded); no unsynchronized observation can
    # exclude that window and this vector does not claim it closed, so the signal is still
    # sent (the disclosed residual, required here only to prove the schedule was reached),
    # and what it pins is that the reap which follows reports the loss. Each must come back
    # with anchor_lost True and returncode None, without raising. Red when the anchor check
    # before the first kill is removed, red when the final anchor check is replaced by
    # anchored = True, and red when the reap reverts to Popen.wait.
    anchor_state = {}
    anchor_kills = []
    anchor_reaps = []

    class _AnchorProc:
        """A Popen double with no pipes whose wait and communicate model CPython's ECHILD
        handling on an already reaped leader (both read as an exit of 0)."""
        pid = 4194307  # modelled only: killpg and waitid are both patched below
        stdout = None
        stderr = None

        def __init__(self, *_args, **_kwargs):
            self.returncode = None

        def communicate(self, timeout=None):
            anchor_reaps.append("communicate")
            self.returncode = 0
            return b"", b""

        def wait(self, timeout=None):
            anchor_reaps.append("wait")
            self.returncode = 0
            return 0

    def _anchor_waitid(_idtype, _wpid, _options):
        reap_after = anchor_state["reap_after"]
        if reap_after is not None and anchor_state["observations"] >= reap_after:
            anchor_state["reaped"] = True  # another path reaps the leader now
        if anchor_state["reaped"]:
            raise ChildProcessError("modelled: the leader was reaped by another path")
        anchor_state["observations"] += 1
        return _ModelSiginfo(os.CLD_EXITED, 0)

    def _anchor_killpg(_pgid, sig):
        if sig == 0:
            raise ProcessLookupError  # the post-reap probe: the modelled group is empty
        if anchor_state["reap_at_signal"]:
            anchor_state["reaped"] = True  # the outside reap lands just before the signal
        anchor_kills.append((sig, anchor_state["observations"]))

    def _anchor_drain_held(_proc, _deadline, _out, _err):
        return False  # modelled: a pipe is still held open when the deadline passes

    real_a_drain = _drain_pipes_unreaped
    for anchor_label, reap_after, reap_at_signal, anchor_drain, want_timed_out, want_kills in (
            ("first-kill", 0, False, _anchor_drain_held, True, 0),
            ("final-signal", 1, False, real_a_drain, False, 0),
            ("outside-reap", None, True, real_a_drain, False, 1)):
        anchor_state.update(reap_after=reap_after, reap_at_signal=reap_at_signal,
                            reaped=False, observations=0)
        del anchor_kills[:]
        del anchor_reaps[:]
        anchor_got = None
        real_a_popen, real_a_killpg = subprocess.Popen, os.killpg
        real_a_waitid, real_a_sleep = os.waitid, time.sleep
        subprocess.Popen = _AnchorProc
        os.killpg = _anchor_killpg
        os.waitid = _anchor_waitid
        time.sleep = _no_sleep
        gmod["_drain_pipes_unreaped"] = anchor_drain
        try:
            outcome = _run_bounded_child(["-c", "pass"], os.environ, timeout=5)
        except Exception as exc:  # the vector reports it below as its own named failure
            anchor_got = "an escaped {}: {}".format(type(exc).__name__, exc)
        finally:
            subprocess.Popen, os.killpg = real_a_popen, real_a_killpg
            os.waitid, time.sleep = real_a_waitid, real_a_sleep
            gmod["_drain_pipes_unreaped"] = real_a_drain
        if anchor_got is None and (
                len(anchor_kills) != want_kills or anchor_reaps or outcome.returncode is not None
                or not outcome.anchor_lost or outcome.timed_out is not want_timed_out):
            anchor_got = ("destructive kills {!r}, Popen reaps {!r}, returncode {!r}, "
                          "anchor lost {}, timed out {}".format(
                              anchor_kills, anchor_reaps, outcome.returncode,
                              outcome.anchor_lost, outcome.timed_out))
        if anchor_got is not None:
            failures.append("(vector launcher-anchor-{}) a leader another path reaped must "
                            "withhold every destructive group signal an anchor check can "
                            "still guard ({} expected on this schedule) and come back with "
                            "anchor_lost True and returncode None, never a collected 0, "
                            "without raising (got {})".format(anchor_label, want_kills,
                                                             anchor_got))

    # Launcher SIGCHLD-refusal vectors (in-process, a spied Popen): with SIGCHLD ignored the
    # kernel reaps every leader itself, so no anchor check could hold its group id; the
    # launcher must refuse with the named _SigchldIgnoredError BEFORE anything is launched.
    # subprocess.Popen is replaced by a spy that records every launch attempt and raises
    # its own error, so nothing real starts under any disposition here. ignored (SIG_IGN)
    # must be refused with no launch attempt; default (SIG_DFL) and python-handler (a
    # no-op handler installed through signal.signal, under which the kernel does not reap)
    # must reach exactly one launch attempt, the spy's own error propagating unchanged. The
    # caller's disposition is restored after each, whatever happened, and the vector
    # requires it restored. A handler installed outside Python (getsignal None) cannot be
    # put back through signal.signal, so these vectors then leave it alone and record a
    # NAMED NOT RUN. Red when the refusal is removed or moves after the launch, and red for
    # python-handler when the refusal widens to every disposition other than SIG_DFL.
    sigchld_prior = signal.getsignal(signal.SIGCHLD)
    if sigchld_prior is None:
        _not_run(failures, "the launcher-sigchld-refusal vectors",
                 "SIGCHLD has a handler installed outside Python, which signal.signal "
                 "cannot restore")
    else:
        sigchld_launches = []
        sigchld_spy_error = RuntimeError("spy: the launcher attempted a launch")

        def _sigchld_spy_popen(*_args, **_kwargs):
            sigchld_launches.append(signal.getsignal(signal.SIGCHLD))
            raise sigchld_spy_error

        def _sigchld_noop_handler(_signum, _frame):
            return None

        for sc_label, sc_disposition, sc_refused in (
                ("ignored", signal.SIG_IGN, True),
                ("default", signal.SIG_DFL, False),
                ("python-handler", _sigchld_noop_handler, False)):
            del sigchld_launches[:]
            sc_got = None
            real_sc_popen = subprocess.Popen
            subprocess.Popen = _sigchld_spy_popen
            try:
                signal.signal(signal.SIGCHLD, sc_disposition)
                _run_bounded_child(["-c", "pass"], os.environ, timeout=5)
                sc_got = "no exception"
            except _SigchldIgnoredError as exc:
                if not sc_refused:
                    sc_got = "refused: {}".format(exc)
            except Exception as exc:  # reported below as this vector's own named failure
                if sc_refused or exc is not sigchld_spy_error:
                    sc_got = "an escaped {}: {}".format(type(exc).__name__, exc)
            finally:
                subprocess.Popen = real_sc_popen
                signal.signal(signal.SIGCHLD, sigchld_prior)
            want_launches = 0 if sc_refused else 1
            if sc_got is None and len(sigchld_launches) != want_launches:
                sc_got = "{} launch attempt(s), want {}".format(len(sigchld_launches),
                                                              want_launches)
            if sc_got is None and signal.getsignal(signal.SIGCHLD) != sigchld_prior:
                sc_got = "the caller's SIGCHLD disposition was not restored"
            if sc_got is not None:
                failures.append("(vector launcher-sigchld-refusal {}) the launcher must "
                                "refuse with _SigchldIgnoredError and launch nothing exactly "
                                "when SIGCHLD is SIG_IGN, and reach its launch otherwise, "
                                "with the caller's disposition restored (got {})".format(
                                    sc_label, sc_got))

    # Launcher exception-cleanup vectors (fresh processes, injected failures): whatever
    # raises after Popen returns, the launcher's try/finally must run EVERY cleanup stage:
    # close both owned pipes with EXACTLY ONE close call each, attempt the final group
    # signal only after the anchor check, perform the one bounded reap, re-raise a PRIMARY
    # (lifecycle) exception unchanged, and raise a cleanup-stage failure only when nothing
    # else raised. Each vector launches a REAL child (except allocation, whose failure
    # must come BEFORE any launch) and asserts the expected exception OBJECT itself
    # propagated, the per-pipe close count, a returncode collected, the leader reaped and
    # the group empty. readiness, selector, pipe-read, pipe-close (the drain's EOF close)
    # and exit-observation inject the primary at one lifecycle step. anchor-check (a
    # cleanup waitid OSError: the anchor is unverifiable, so the final signal must be
    # withheld), final-signal (a PermissionError from the final killpg) and close-runtime
    # (a RuntimeError from the FINAL cleanup's stdout close while stderr is still open)
    # each fail one cleanup stage with a readiness failure pending, which must propagate
    # unchanged while every later stage still runs. close-alone fails the final cleanup's
    # stdout close with NOTHING else raised (the drain is modelled as finishing without
    # reaching EOF, over a real child that exits on its own): that close error must be
    # raised, but only after stderr is closed and the leader reaped. anchor-echild is a
    # REAL outside reap (the exit observation is replaced by a bounded waitpid that collects
    # the status 3) before the cleanup: no destructive signal, no exception, and the
    # outcome must carry anchor_lost True and returncode None, never the 0 CPython's
    # Popen.wait makes of ECHILD. Its window is _CHILD_TIMEOUT, the bound every vector-entry
    # child gets, not 5 seconds: the drain and the outside reap both run against the
    # launcher's one window, so a child slow to start under load would otherwise run it
    # out, and the first timeout kill that follows (recorded, never sent) would turn the
    # vector red although the launcher behaved. These vectors, like the anchor check they
    # exercise, rely on SIGCHLD not being ignored (the launcher's precondition; under
    # SIG_IGN it refuses by name, as the vectors above require). A child a red vector
    # leaves unreaped is killed and reaped by its pid afterwards. Red
    # when the cleanup try/finally is removed, when the buffers are allocated after the
    # launch, when a cleanup stage is no longer isolated (or catches only OSError), when a
    # final close is duplicated, when the kept cleanup failure is dropped or re-raised at
    # once, and when the reap reverts to Popen.wait.
    import selectors
    real_cl_selector = selectors.DefaultSelector
    real_cl_read = os.read
    real_cl_waitid = os.waitid
    real_cl_killpg = os.killpg
    real_cl_await_readiness = gmod["_await_readiness"]
    real_cl_await_exit = gmod["_await_exit_unreaped"]
    real_cl_drain = gmod["_drain_pipes_unreaped"]
    cl_hang_args = ["-c", "import time; time.sleep(60)"]

    def _cleanup_raiser(injected):
        def _raise(*_args, **_kwargs):
            raise injected
        return _raise

    def _drive_cleanup_vector(child_args, ready_path, injected, install, restore,
                              close_raises=None, launches=True, extra=None, window=5):
        """Launch a REAL child with INSTALL's injected failure in place and a WINDOW-second
        timeout, and return what the launcher got wrong (None when every guarantee held):
        INJECTED itself re-raised
        unchanged (None: a normal return is required), both pipes closed by EXACTLY ONE
        close call each, a returncode collected, the leader reaped and the group empty,
        plus EXTRA's own check of what SEEN recorded. With LAUNCHES False the failure must
        come before any launch. CLOSE_RAISES, when given, is raised by stdout's FIRST close
        call, after the raw pipe is closed. A child this vector launched that is still this
        process's UNREAPED child afterwards (a regressed launcher) is killed and reaped BY
        ITS PID here (unreaped, so the pid cannot have been reused): a red vector leaks no
        sleeping child."""
        seen = {"closes": {}}
        real_cl_popen = subprocess.Popen

        class _CountingPipe:
            """Delegates to the real pipe and counts EVERY close call; with RAISES set, the
            first close call closes the raw pipe, then raises RAISES."""

            def __init__(self, raw, name, raises):
                self.raw = raw
                self._name = name
                self._raises = raises
                seen["closes"][name] = 0

            @property
            def closed(self):
                return self.raw.closed

            def fileno(self):
                return self.raw.fileno()

            def close(self):
                seen["closes"][self._name] += 1
                self.raw.close()
                if self._raises is not None and seen["closes"][self._name] == 1:
                    raise self._raises

        class _RecordingPopen(real_cl_popen):
            def __init__(self, *p_args, **p_kwargs):
                super().__init__(*p_args, **p_kwargs)
                self.stdout = _CountingPipe(self.stdout, "stdout", close_raises)
                self.stderr = _CountingPipe(self.stderr, "stderr", None)
                seen["proc"] = self

        subprocess.Popen = _RecordingPopen
        install(seen)
        got = None
        try:
            seen["outcome"] = _run_bounded_child(child_args, os.environ, timeout=window,
                                                 ready_path=ready_path)
            if injected is not None:
                got = "no exception propagated"
        except BaseException as exc:
            if injected is None or exc is not injected:
                got = "a different exception {}: {}".format(type(exc).__name__, exc)
        finally:
            restore()
            subprocess.Popen = real_cl_popen
        misses = [] if got is None else [got]
        proc = seen.get("proc")
        if proc is None:
            if launches:
                misses.append("no child was launched")
            return "; ".join(misses) if misses else None
        if not launches:
            misses.append("a child was launched before the failing allocation")
        for pipe_name in ("stdout", "stderr"):
            if seen["closes"][pipe_name] != 1:
                misses.append("{} closed by {} close call(s), not exactly one".format(
                    pipe_name, seen["closes"][pipe_name]))
        if proc.returncode is None:
            misses.append("no returncode collected")
        for pipe_name, pipe in (("stdout", proc.stdout), ("stderr", proc.stderr)):
            if not pipe.closed:
                misses.append(pipe_name + " left open")
        leader_unreaped = False
        try:
            os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
            leader_unreaped = True
            misses.append("leader still waitable: never reaped")
        except ChildProcessError:
            pass
        if leader_unreaped:
            # a regressed launcher left its child unreaped: kill it by its pid (still this
            # process's unreaped child, so the pid is not reused) and reap it
            try:
                os.kill(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=_CHILD_DRAIN_TIMEOUT)
            except subprocess.TimeoutExpired:
                misses.append("the leaked child could not be reaped")
        for pipe in (proc.stdout, proc.stderr):
            if not pipe.closed:
                pipe.raw.close()  # no descriptor outlives a red vector
        group_gone = False
        for _ in range(100):
            try:
                os.killpg(proc.pid, 0)
            except ProcessLookupError:
                group_gone = True
                break
            time.sleep(0.1)
        if not group_gone:
            misses.append("the group still has a member")
        if extra is not None:
            extra_miss = extra(seen)
            if extra_miss is not None:
                misses.append(extra_miss)
        return "; ".join(misses) if misses else None

    inj_ready = RuntimeError("injected: readiness failure")
    inj_selector = RuntimeError("injected: selector-creation failure")
    inj_read = OSError("injected: pipe-read failure")
    inj_close = OSError("injected: pipe-close failure")
    inj_exit = RuntimeError("injected: exit-observation failure")
    inj_alloc = MemoryError("injected: buffer-allocation failure")
    inj_anchor = OSError("injected: cleanup anchor-check failure")
    inj_signal = PermissionError("injected: final-signal failure")
    inj_close_rt = RuntimeError("injected: final-cleanup close failure")
    inj_close_alone = OSError("injected: final-cleanup close failure, nothing else raised")
    cl_kills = []

    def _install_ready(_seen):
        gmod["_await_readiness"] = _cleanup_raiser(inj_ready)

    def _restore_all():
        # every seam any cleanup vector patches, restored whichever one it installed
        gmod["_await_readiness"] = real_cl_await_readiness
        gmod["_await_exit_unreaped"] = real_cl_await_exit
        gmod["_drain_pipes_unreaped"] = real_cl_drain
        gmod.pop("bytearray", None)
        selectors.DefaultSelector = real_cl_selector
        os.read = real_cl_read
        os.waitid = real_cl_waitid
        os.killpg = real_cl_killpg

    def _install_selector(_seen):
        selectors.DefaultSelector = _cleanup_raiser(inj_selector)

    def _install_read(seen):
        def _read_child_pipe(fd, length):
            proc = seen.get("proc")
            open_fds = []
            for pipe in (proc.stdout, proc.stderr) if proc is not None else ():
                if pipe is not None and not pipe.closed:
                    open_fds.append(pipe.fileno())
            if fd in open_fds:
                raise inj_read
            return real_cl_read(fd, length)
        os.read = _read_child_pipe

    def _install_exit(_seen):
        gmod["_await_exit_unreaped"] = _cleanup_raiser(inj_exit)

    def _install_nothing(_seen):
        return None

    def _install_alloc(_seen):
        gmod["bytearray"] = _cleanup_raiser(inj_alloc)  # shadows the builtin in this module

    def _recording_killpg(pgid, sig):
        if sig != 0:
            cl_kills.append(sig)
        return real_cl_killpg(pgid, sig)

    def _install_anchor(seen):
        _install_ready(seen)
        del cl_kills[:]

        def _anchor_failing_waitid(idtype, wpid, options):
            if options & os.WNOWAIT:
                raise inj_anchor  # the cleanup's anchor observation cannot be made
            return real_cl_waitid(idtype, wpid, options)
        os.waitid = _anchor_failing_waitid
        os.killpg = _recording_killpg

    def _no_destructive_signal(_seen):
        if cl_kills:
            return "a destructive group signal {!r} was sent on an unverifiable anchor".format(
                cl_kills)
        return None

    def _install_signal(seen):
        _install_ready(seen)

        def _signal_failing_killpg(pgid, sig):
            if sig != 0:
                raise inj_signal
            return real_cl_killpg(pgid, sig)
        os.killpg = _signal_failing_killpg

    def _install_drain_short(_seen):
        def _drain_without_eof(_proc, _deadline, _out, _err):
            return True  # modelled: the drain finished with both pipes still open
        gmod["_drain_pipes_unreaped"] = _drain_without_eof

    def _install_echild(_seen):
        del cl_kills[:]

        def _outside_reap(pid, deadline):
            # the OUTSIDE reap: collect the leader's status before the launcher can
            while time.monotonic() < deadline:
                if os.waitpid(pid, os.WNOHANG)[0] == pid:
                    return True
                time.sleep(0.05)
            return False

        def _dropping_killpg(pgid, sig):
            if sig != 0:
                cl_kills.append(sig)  # recorded, never sent: the anchor is lost here
                return None
            return real_cl_killpg(pgid, sig)
        gmod["_await_exit_unreaped"] = _outside_reap
        os.killpg = _dropping_killpg

    def _echild_reported(seen):
        outcome = seen.get("outcome")
        if outcome is None:
            return "no outcome returned"
        if outcome.returncode is not None or not outcome.anchor_lost or cl_kills:
            return ("returncode {!r} (want None), anchor lost {}, destructive signals "
                    "{!r}".format(outcome.returncode, outcome.anchor_lost, cl_kills))
        return None

    cl_pass_args = ["-c", "pass"]
    for cl_label, cl_args, cl_ready_path, cl_injected, cl_install, cl_close, cl_extra in (
            ("readiness", cl_hang_args, "injected: never opened", inj_ready,
             _install_ready, None, None),
            ("selector", cl_hang_args, None, inj_selector, _install_selector, None, None),
            ("pipe-read", ["-c", "print('x', flush=True)\nimport time\ntime.sleep(60)"],
             None, inj_read, _install_read, None, None),
            ("pipe-close", ["-c", "print('x')"], None, inj_close,
             _install_nothing, inj_close, None),
            ("exit-observation", cl_pass_args, None, inj_exit, _install_exit, None, None),
            ("allocation", cl_hang_args, None, inj_alloc, _install_alloc, None, None),
            ("anchor-check", cl_pass_args, "injected: never opened", inj_ready,
             _install_anchor, None, _no_destructive_signal),
            ("final-signal", cl_pass_args, "injected: never opened", inj_ready,
             _install_signal, None, None),
            ("close-runtime", cl_hang_args, "injected: never opened", inj_ready,
             _install_ready, inj_close_rt, None),
            ("close-alone", cl_pass_args, None, inj_close_alone,
             _install_drain_short, inj_close_alone, None),
            ("anchor-echild", ["-c", "import sys; sys.exit(3)"], None, None,
             _install_echild, None, _echild_reported)):
        cl_got = _drive_cleanup_vector(cl_args, cl_ready_path, cl_injected, cl_install,
                                       _restore_all, close_raises=cl_close,
                                       launches=cl_label != "allocation", extra=cl_extra,
                                       window=(_CHILD_TIMEOUT if cl_label == "anchor-echild"
                                               else 5))
        if cl_got is not None:
            failures.append("(vector launcher-cleanup-{}) the {} injection must leave the "
                            "launcher's cleanup guarantees intact: the expected exception "
                            "(or none) propagated unchanged, every cleanup stage run, each "
                            "pipe closed by exactly one close call, a returncode collected, "
                            "the leader reaped and the group empty (got {})".format(
                                cl_label, cl_label, cl_got))

    # Launcher timeout vector (fresh process): the child creates a live same-group
    # descendant, publishes a readiness line (descendant pid and group, its own pid and
    # group, and the descendant's liveness) and only then enters its hang; the launcher
    # starts the 5-second window only AT that readiness, so the window can never expire
    # before the descendant exists. The outcome must come back timed out with the leader
    # reaped (a returncode, never None) and the group empty, because the launcher SIGKILLs
    # the whole group while the unreaped leader still anchors it; the parsed readiness
    # fields must put the live descendant in the group the child leads. Red when
    # start_new_session is dropped, red when the descendant is no longer created, and red
    # when readiness never completes (missing readiness FAILS: ready False, never a
    # silently narrower exercise); the kill-before-drain order and the readiness-anchored
    # window are pinned by the launcher-readiness-window vector above, not here (dropping
    # only the first timeout kill leaves THIS vector green: the final anchored kill still
    # empties the group within the bounded drain).
    try:
        hang_dir = tempfile.mkdtemp(prefix="aiqt-portability-selftest-hang-")
    except OSError as exc:
        _not_run(failures, "the launcher-timeout vector",
                 "no writable temp directory ({})".format(exc))
    else:
        try:
            hang_ready = os.path.join(hang_dir, "ready")
            hang = ("import os, subprocess, sys, time\n"
                    "desc = subprocess.Popen([sys.executable, '-I', '-c', "
                    "'import time; time.sleep(60)'])\n"
                    "line = '{} {} {} {} {}\\n'.format(desc.pid, os.getpgid(desc.pid), "
                    "os.getpid(), os.getpgid(0), desc.poll() is None)\n"
                    "with open(sys.argv[1] + '.tmp', 'w') as fh:\n"
                    "    fh.write(line)\n"
                    "os.replace(sys.argv[1] + '.tmp', sys.argv[1])\n"
                    "time.sleep(60)\n")
            outcome = _run_bounded_child(["-c", hang, hang_ready], os.environ, timeout=5,
                                         ready_path=hang_ready)
            fields = []
            try:
                with open(hang_ready, "r", encoding="utf-8") as fh:
                    fields = fh.read().split()
            except OSError:
                pass
            handshake_ok = (outcome.ready and len(fields) == 5 and fields[4] == "True"
                            and fields[1] == fields[3] and fields[2] == fields[3])
            if (not handshake_ok or not outcome.timed_out or outcome.returncode is None
                    or outcome.survivors):
                failures.append("(vector launcher-timeout) a child whose readiness line "
                                "puts a LIVE descendant in the group the child leads, and "
                                "which then outlives the wait bound, must be killed AS A "
                                "GROUP while the unreaped leader anchors it, leaving the "
                                "leader reaped and the group empty; missing or wrong "
                                "readiness fails (got ready {}, readiness fields {!r}, "
                                "timed out {}, returncode {!r}, survivors {}, stderr "
                                "{!r})".format(outcome.ready, fields, outcome.timed_out,
                                               outcome.returncode, outcome.survivors,
                                               outcome.stderr[:120]))
        finally:
            shutil.rmtree(hang_dir, ignore_errors=True)

    def _unavailable(*_args, **_kwargs):
        raise OSError("injected: unavailable")

    def _bigint_unavailable(*args, **kwargs):
        if "aiqt-portability-bigint-" in _mkdtemp_prefix(args, kwargs):
            raise OSError("injected: unavailable")
        return tempfile.mkdtemp(*args, **kwargs)

    for label, kwargs, wants in (
            ("no-tempdir", {"_mkdtemp": _unavailable},
             ("NOT RUN: {}: no writable temp directory".format(_E2E_SURFACE_CASES),)),
            # BOTH identity-source iterations allocate under the bigint prefix; each must
            # record its OWN NOT RUN line, asserted independently below, so the over-long
            # line can never hide a missing parser-overflow line (or the reverse).
            ("no-tempdir-bigint", {"_mkdtemp": _bigint_unavailable},
             ("NOT RUN: the over-long integer identity source case: no writable temp "
              "directory",
              "NOT RUN: the parser overflow identity source case: no writable temp "
              "directory")),
            ("no-symlink", {"_symlink": _unavailable},
             ("NOT RUN: {}: no symlink support".format(_DISK_KIND_CASES),)),
            ("git-absent", {"_which": lambda _name: None},
             ("NOT RUN: {}: git is unavailable".format(_REAL_GIT_CASE),))):
        out = io.StringIO()
        with fixture_git_lifecycle(), redirect_stdout(out), redirect_stderr(io.StringIO()):
            rc = _self_test_main_isolated(_vectors=False, **kwargs)
        text = out.getvalue()
        missing = [w for w in wants if w not in text]
        if rc == 0 or missing:
            failures.append("(NOT RUN vector {}) must exit nonzero AND record each of {!r} "
                            "independently (one NOT RUN line never hides a missing sibling), "
                            "never a PASS (PARTIAL) exit 0 (got exit {}, missing {!r})".format(
                                label, wants, rc, missing))

    # Symlink-guard scope (plain-file setup): an OSError injected into the moved mkdir, into the
    # moved write_text, or into the _fresh surface build must PROPAGATE out of the isolated run,
    # never be recorded as the no-symlink NOT RUN (only the three symlink creations sit inside
    # that guard; red when the plain-file setup is moved back inside it).
    for label, attr, needle in (
            ("disk-kind mkdir", "mkdir", "disk-kind"),
            ("disk-kind write_text", "write_text", "disk-kind"),
            ("rootlink-real _fresh build", "write_text", "rootlink-real")):
        real_method = getattr(pathlib.Path, attr)
        denial = "injected: plain-file setup denied ({})".format(label)

        def _deny_in(self, *args, _real=real_method, _denial=denial, _needle=needle, **kwargs):
            if _needle in str(self):
                raise PermissionError(_denial)
            return _real(self, *args, **kwargs)

        setattr(pathlib.Path, attr, _deny_in)
        propagated = False
        try:
            with fixture_git_lifecycle(), redirect_stdout(io.StringIO()), \
                    redirect_stderr(io.StringIO()):
                _self_test_main_isolated(_vectors=False)
        except PermissionError as exc:
            propagated = denial in str(exc)
        finally:
            setattr(pathlib.Path, attr, real_method)
        if not propagated:
            failures.append("(propagation vector {}) an injected PermissionError in the "
                            "plain-file disk-kind/root-link setup must PROPAGATE, never be "
                            "recorded as the no-symlink NOT RUN".format(label))

    # Nested no-vectors seam (in-process): each of the five entry-vector drives below must
    # reach its nested public entry through _nested_entry_no_vectors, and the helper must hand
    # _vectors=False to self_test_main. The drives run with the suppress marker removed from
    # os.environ (restored after), so what the recorder proves is the HANDED value under an
    # absent marker: red when the helper's argument is reverted to the default, when the
    # helper reads the absent marker and passes True, and when a call site stops going through
    # the helper; a helper that passed False by some other expression under an absent marker
    # is not distinguished. The recorder replaces self_test_main (no nested entry body runs,
    # so no drive can recurse).
    _mod = sys.modules[__name__]
    real_entry = _mod.self_test_main
    real_helper = _mod._nested_entry_no_vectors
    recorded = []
    through = []

    def _recording_entry(*args, **kwargs):
        recorded.append(args[0] if args else kwargs.get("_vectors", True))
        return 0

    def _marking_helper():
        through.append(True)
        return real_helper()

    marker = os.environ.pop(_SUPPRESS_VECTORS_ENV, None)
    _mod.self_test_main = _recording_entry
    _mod._nested_entry_no_vectors = _marking_helper
    seam_ok = True
    try:
        for site, drive, calls in (("lifecycle", _entry_vector_lifecycle, 1),
                                   ("wrapper", _entry_vector_wrapper, 2),
                                   ("post-entry", _entry_vector_post_entry, 2),
                                   ("pre-entry", _entry_vector_pre_entry, 1),
                                   ("pass-text", _entry_vector_pass_text, 1)):
            del recorded[:]
            del through[:]
            drive([])
            if (len(recorded) != calls or len(through) != calls
                    or any(v is not False for v in recorded)):
                seam_ok = False
                failures.append("(seam vector {}) the {} call site must reach its nested "
                                "public entry through _nested_entry_no_vectors, handing "
                                "_vectors False under an absent suppress marker (expected {} "
                                "helper call(s), each recording _vectors False; got {} helper "
                                "call(s) recording {!r})".format(site, site, calls,
                                                                 len(through), list(recorded)))
    finally:
        _mod.self_test_main = real_entry
        _mod._nested_entry_no_vectors = real_helper
        if marker is not None:
            os.environ[_SUPPRESS_VECTORS_ENV] = marker

    if seam_ok:
        # The five REAL in-process nested public entries, driven for their own predicates.
        _entry_vector_lifecycle(failures)
        _entry_vector_wrapper(failures)
        _entry_vector_post_entry(failures)
        _entry_vector_pre_entry(failures)
        _entry_vector_pass_text(failures)
    else:
        # A broken seam could let a nested public entry recurse into vectors of its own with
        # no bounding child process, so the in-process entry drives do not run; the run is
        # already red from the seam failures above, and this skip is NAMED, never silent.
        _not_run(failures, "the lifecycle, wrapper, post-entry and pre-entry entry vectors "
                           "and the no-vectors-pass-text vector",
                 "the nested no-vectors seam checks failed")

    tool = str(Path(__file__).resolve())
    entry_env = dict(os.environ)
    entry_env[_SUPPRESS_VECTORS_ENV] = "1"  # a child never runs vectors of its own
    children = []
    try:
        no_git_dir = tempfile.mkdtemp(prefix="aiqt-portability-selftest-nogit-")
    except OSError as exc:
        _not_run(failures, "the fresh-process public-entry git-absent vector",
                 "no writable temp directory ({})".format(exc))
    else:
        try:
            child_env = dict(entry_env)
            child_env["PATH"] = no_git_dir  # an empty directory: no git resolvable on PATH
            children.append(("public-entry git-absent",
                             _run_bounded_child([tool, "--self-test"], child_env),
                             "NOT RUN: {}: fixture setup failed: git is unavailable".format(
                                 _EVERY_CASE)))
        finally:
            shutil.rmtree(no_git_dir, ignore_errors=True)
    inject = ("import runpy, sys, tempfile\n"
              "def _unavailable(*_args, **_kwargs):\n"
              "    raise OSError('injected: unavailable')\n"
              "tempfile.mkdtemp = _unavailable\n"
              "tool = sys.argv[1]\n"
              "sys.argv[:] = [tool, '--self-test']\n"
              "runpy.run_path(tool, run_name='__main__')\n")
    children.append(("public-entry no-tempdir",
                     _run_bounded_child(["-c", inject, tool], entry_env),
                     "NOT RUN: {}: fixture setup failed: no writable temp directory".format(
                         _EVERY_CASE)))
    for label, outcome, want in children:
        if (outcome.returncode != 1 or want.encode("utf-8") not in outcome.stdout
                or outcome.timed_out or outcome.survivors):
            failures.append("(NOT RUN vector {}) must exit 1 AND record {!r} through the real "
                            "--self-test entry, never a bare traceback, a hang or a surviving "
                            "group member (got exit {}, timed out {}, survivors {})".format(
                                label, want, outcome.returncode, outcome.timed_out,
                                outcome.survivors))

    # Inherited-marker guard: a FULL public-entry run that inherits _SUPPRESS_VECTORS_ENV (the
    # scrub keeps it: it is not GIT_-prefixed) must exit 1 and print the named suppression
    # NOT RUN line as an exact LF-delimited BYTES line exactly once (a prefixed, suffixed or
    # CR-separated-prefix line fails: the launcher captures bytes, so newline translation can
    # never fold a CR-separated prefix away), with no PASS text of either form, never the
    # unchanged PASS over silently skipped vectors; the bounded launcher turns a hang into
    # this vector's named failure within the launcher's group-and-timeout bounds, never a
    # stall of the whole self-test past those bounds.
    marked_expect = []
    _not_run(marked_expect, "the red-on-revert NOT RUN and entry vectors",
             "suppressed by {} (a vector child, never a full run)".format(_SUPPRESS_VECTORS_ENV))
    suppressed_line = "  - " + marked_expect[0]
    outcome = _run_bounded_child([tool, "--self-test"], entry_env)
    exact_lines = _exact_line_count(outcome.stdout, suppressed_line)
    marked_ok = (not outcome.timed_out and not outcome.survivors and outcome.returncode == 1
                 and exact_lines == 1 and b"SELF-TEST PASS" not in outcome.stdout)
    if outcome.timed_out:
        got = ("a timeout: the marked child hung instead of failing named (the group was "
               "killed before the leader was reaped; survivors {})".format(outcome.survivors))
    else:
        got = "exit {} (exact-line count {}, PASS text printed: {}, survivors {})".format(
            outcome.returncode, exact_lines, b"SELF-TEST PASS" in outcome.stdout,
            outcome.survivors)
    if not marked_ok:
        failures.append("(vector inherited-marker) a full public-entry run inheriting {} must "
                        "exit 1 AND print the exact LF-delimited line {!r} exactly once with "
                        "no PASS text, never the unchanged PASS over silently skipped vectors "
                        "(got {})".format(_SUPPRESS_VECTORS_ENV, suppressed_line, got))

    # Exact-line predicate vector: the count is over captured BYTES split on LF, so a
    # CR-separated prefix (which text-mode newline translation would split into a line of
    # its own) never counts as the exact line; red when the count reverts to text-mode
    # splitting or translates newlines before splitting.
    line_bytes = suppressed_line.encode("utf-8")
    for case_label, data, want_count in (
            ("exact", line_bytes + b"\n", 1),
            ("prefixed", b"prefix " + line_bytes + b"\n", 0),
            ("CR-prefixed", b"prefix\r" + line_bytes + b"\n", 0),
            ("suffixed", line_bytes + b" suffix\n", 0),
            ("CR-suffixed", line_bytes + b"\r\n", 0),
            ("duplicated", line_bytes + b"\n" + line_bytes + b"\n", 2)):
        got_count = _exact_line_count(data, suppressed_line)
        if got_count != want_count:
            failures.append("(vector marker-line-bytes {}) _exact_line_count must count the "
                            "suppression line as an exact LF-delimited BYTES line (want {}, "
                            "got {})".format(case_label, want_count, got_count))

    # _SUPPRESS_VECTORS_ENV guard: the spy child proves ONLY that a marker-carrying child
    # reaches its own named SELF-TEST FAIL without launching any subprocess (subprocess.run
    # AND subprocess.Popen are replaced); its denied mkdtemp and absent git leave the
    # end-to-end, disk-kind and real-git groups NOT RUN inside it, so it does not prove
    # those groups complete.
    spy = ("import importlib.util, subprocess, sys\n"
           "spec = importlib.util.spec_from_file_location("
           "'aiqt_portability_vector_child', sys.argv[1])\n"
           "mod = importlib.util.module_from_spec(spec)\n"
           "sys.modules[spec.name] = mod\n"
           "spec.loader.exec_module(mod)\n"
           "def _spy(*_args, **_kwargs):\n"
           "    print('VECTOR-CHILD-SPAWNED', file=sys.stderr)\n"
           "    raise RuntimeError('vector child spawned despite the suppress marker')\n"
           "subprocess.run = _spy\n"
           "subprocess.Popen = _spy\n"
           "def _deny(*_args, **_kwargs):\n"
           "    raise OSError('injected: unavailable')\n"
           "sys.exit(mod._self_test_main_isolated(_mkdtemp=_deny, _which=lambda _n: None))\n")
    outcome = _run_bounded_child(["-c", spy, tool], entry_env)
    spawned = (b"vector child spawned" in outcome.stderr
               or b"VECTOR-CHILD-SPAWNED" in outcome.stderr)
    if (outcome.returncode != 1 or b"SELF-TEST FAIL" not in outcome.stdout or spawned
            or outcome.timed_out or outcome.survivors):
        failures.append("(vector suppress-marker) a child carrying {} must run its cases to its "
                        "own named SELF-TEST FAIL without spawning vector children (got exit {}, "
                        "spawned: {}, timed out {}, survivors {})".format(
                            _SUPPRESS_VECTORS_ENV, outcome.returncode, spawned,
                            outcome.timed_out, outcome.survivors))

    # Unmarked drifted-injection child: the REAL lifecycle entry vector runs in a fresh child
    # WITHOUT the suppress marker, with _LIFECYCLE_ALLOC_PREFIX drifted so its injection can
    # never match. The nested no-vectors entry must COMPLETE, so the vector records exactly
    # its named exit-0 failure as the child's ENTIRE stdout (compared as bytes) and the child
    # exits 0 without recursing into vectors of its own: the EXPLICIT no-vectors seam is what
    # stops the recursion, and the launcher's _CHILD_DEPTH_ENV makes a reverted seam refuse
    # at depth 1 instead of chaining children. The bounded launcher kills and probes the
    # child's OWN process group, so a recursing revert ends with a named failure and no
    # surviving member of that group (a process that left the group by starting a new
    # session, or by setpgid into another group, is outside the launcher's bounds); the
    # launcher signals the group only while its unreaped leader anchors the id, and after
    # the reap it only observes, never a process listing and never a post-reap kill.
    drift = ("import importlib.util, sys\n"
             "spec = importlib.util.spec_from_file_location("
             "'aiqt_portability_drift_child', sys.argv[1])\n"
             "mod = importlib.util.module_from_spec(spec)\n"
             "sys.modules[spec.name] = mod\n"
             "spec.loader.exec_module(mod)\n"
             "mod._LIFECYCLE_ALLOC_PREFIX = 'aiqt-fixture-git-bin-DRIFTED-'\n"
             "failures = []\n"
             "mod._entry_vector_lifecycle(failures)\n"
             "for failure in failures:\n"
             "    print(failure)\n"
             "sys.exit(0)\n")
    drift_env = dict(os.environ)
    drift_env.pop(_SUPPRESS_VECTORS_ENV, None)
    drift_want = (_lifecycle_alloc_failure("exit 0") + "\n").encode("utf-8")
    outcome = _run_bounded_child(["-c", drift, tool], drift_env)
    if outcome.timed_out:
        got = ("a timeout: the unmarked nested entry recursed instead of completing (the "
               "process group was killed BEFORE the leader was reaped; members surviving "
               "the bounded probe: {})".format(outcome.survivors))
    else:
        got = ("exit {} (stdout is exactly the named failure line: {}, group members "
               "surviving completion: {})".format(
                   outcome.returncode, outcome.stdout == drift_want, outcome.survivors))
    drift_ok = (not outcome.timed_out and outcome.returncode == 0
                and outcome.stdout == drift_want and not outcome.survivors)
    if not drift_ok:
        failures.append("(vector unmarked-drifted-lifecycle) the real lifecycle entry vector, "
                        "driven UNMARKED with _LIFECYCLE_ALLOC_PREFIX drifted, must record "
                        "exactly its named exit-0 failure {!r} as the ENTIRE stdout of a child "
                        "whose process group ends empty (got {})".format(drift_want, got))


def self_test_main(_vectors=True):
    """The public --self-test entry. A dependency the SETUP itself needs is reported as ONE named
    NOT RUN failure covering the whole run (exit 1; nothing ran, so no smaller case group may be
    named as the only thing skipped), never a bare traceback: fixture_git_lifecycle raises
    RuntimeError("self-test requires git") when git is absent, and the scrub's scratch HOME or
    the lifecycle's wrapper directory ALLOCATION raises OSError when no temp directory is
    writable. Each catch is narrow: the RuntimeError must carry the lifecycle's message, and the
    allocation failure is typed at the allocation call itself (_mark_setup_allocations), so an
    OSError from the lifecycle wrapper's write_text or chmod, and anything raised after entry
    (entered), still propagates. The lifecycle itself is entered in _self_test_main_lifecycle,
    which holds the lifecycle allowlist's shape; on_entry, its one listed statement in the with,
    lifts the allocation typing and marks the setup entered before the isolated cases run."""
    failures = []
    entered = []
    restore_mkdtemp = _mark_setup_allocations()

    def on_entry():
        restore_mkdtemp()
        entered.append(True)

    try:
        return _self_test_main_lifecycle(_vectors, on_entry)
    except RuntimeError as exc:
        if entered or "self-test requires git" not in str(exc):
            raise
        _not_run(failures, _EVERY_CASE,
                 "fixture setup failed: git is unavailable ({})".format(exc))
    except _SetupAllocationError as exc:
        if entered:
            raise
        _not_run(failures, _EVERY_CASE,
                 "fixture setup failed: no writable temp directory ({})".format(exc))
    finally:
        restore_mkdtemp()
    print("SELF-TEST FAIL:")
    for failure in failures:
        print("  - " + failure)
    return 1


def _self_test_main_lifecycle(_vectors, on_entry):
    # The lifecycle applies the git fixture scrub itself after saving the caller's environment; a
    # scrub before it would make it save, and so restore, the already scrubbed values.
    from _git_fixture_env import fixture_git_lifecycle
    with fixture_git_lifecycle():
        on_entry()
        return _self_test_main_isolated(_vectors)


def _nested_entry_no_vectors():
    """Every IN-PROCESS nested public-entry call in _not_run_vectors goes through this seam:
    the EXPLICIT no-vectors argument (never the environment, which the in-process nesting
    shares with this run) is what keeps a nested entry whose injection's scoping has DRIFTED
    from recursing into vectors of its own; the seam vectors drive every call site against a
    recorder, and the unmarked-drifted-lifecycle vector proves the completion end-to-end in an
    unmarked child."""
    return self_test_main(_vectors=False)


def _self_test_main_isolated(_vectors=True, _mkdtemp=None, _symlink=None, _which=None):
    from _git_fixture_env import scrub_git_environment
    scrub_git_environment()
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

    # F-TOML-BARE-VALUEERROR-CLASS: an integer literal past CPython's 4300-digit int-string limit makes
    # tomllib raise a BARE ValueError (not TOMLDecodeError). Both identity-manifest parse sites (the
    # attribution mask and load_identity) must still fail closed as GateError. The digit limit is pinned to
    # the default 4300 (test-hermeticity) and restored in finally; the fixture lives in its own tempdir.
    # A parser overflow (tomllib raises RecursionError, not a ValueError) must fail closed the same way at
    # both sites, with the overflow in the refusal.
    # The overflow is INJECTED (tomllib.loads and tomllib.load raise RecursionError on a marked, otherwise valid
    # input) rather than provoked by a deeply nested body: the depth at which tomllib overflows is an
    # interpreter limit, so a fixed body overflows under one recursion limit and parses (or trips an unrelated
    # refusal) under another.
    import shutil as _shutil
    import tempfile as _tempfile
    _prev_digits = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(4300)
    real_loads, real_load = tomllib.loads, tomllib.load

    def overflowing_loads(text, **kwargs):
        if "injected-overflow" in text:
            raise RecursionError("injected parser overflow")
        return real_loads(text, **kwargs)

    tomllib.loads = overflowing_loads
    tomllib.load = lambda fp, **kwargs: overflowing_loads(fp.read().decode("utf-8"), **kwargs)
    try:
        for big_label, big_value, big_needle in (("over-long integer", "9" * 4400, "does not parse"),
                                                 ("parser overflow", "1  # injected-overflow",
                                                  "does not parse (injected parser overflow)")):
            big_toml = "[plugin]\nover-long = " + big_value + "\n"
            _expect_gate_error(big_label + " attribution source",
                               lambda: _mask_manifest_identity(big_toml), big_needle)
            # Only the ALLOCATION is guarded: a temp directory lost MID-RUN at this mkdtemp is
            # a named NOT RUN failure for the identity-source half of this case, never a
            # traceback; an OSError from the mkdir or write_text below PROPAGATES unchanged.
            try:
                big_root = (_mkdtemp or _tempfile.mkdtemp)(prefix="aiqt-portability-bigint-")
            except OSError as exc:
                _not_run(failures, "the {} identity source case".format(big_label),
                         "no writable temp directory ({})".format(exc))
                continue
            try:
                big_path = Path(big_root) / IDENTITY_MANIFEST
                big_path.parent.mkdir(parents=True)
                big_path.write_text(big_toml, encoding="utf-8")
                _expect_gate_error(big_label + " identity source",
                                   lambda: load_identity(Path(big_root)), big_needle)
            finally:
                _shutil.rmtree(big_root, ignore_errors=True)
    finally:
        tomllib.loads, tomllib.load = real_loads, real_load
        sys.set_int_max_str_digits(_prev_digits)

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

    # --- real-surface end-to-end layer (a NOT RUN failure where no writable tempdir exists) --------
    # Cases (a)-(n) use an injected tracked-entry roster over real files. Case (o) uses a real git index
    # when git is available; git's absence is a NAMED NOT RUN failure, never a skip beside a PASS.
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

    def _run_quiet(sroot, entries=None):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            if entries is None:
                return run(sroot)
            return run(sroot, lister=lambda _root: entries)

    e2e_name, e2e_email = "Test Operator", "operator@example.invalid"
    try:
        base_tmp = Path((_mkdtemp or tempfile.mkdtemp)(prefix="aiqt-portability-selftest-"))
    except OSError as exc:
        base_tmp = None
        _not_run(failures, _E2E_SURFACE_CASES, "no writable temp directory ({})".format(exc))

    if base_tmp is not None:
        def _fresh(tag):
            return _build_surface(base_tmp / tag, e2e_name, e2e_email)

        try:
            # GD-130: the REAL _disk_kind rejects a symlinked ANCESTOR (never follows it) and a terminal
            # symlink; run() canonicalizes a symlinked ROOT anchor. ONLY the symlink creations are inside
            # the no-symlink guard, so just a symlink-less host records that NAMED NOT RUN failure (a
            # mkdir/write failure in the plain-file setup propagates like the rest of this block); the
            # root-boundary check DISCRIMINATES (it fails if root.resolve() is absent, because the
            # injected lister would then receive the symlink rather than the canonical root).
            _dk = base_tmp / "disk-kind"
            _symlink_fn = _symlink or os.symlink
            (_dk / "ext").mkdir(parents=True); (_dk / "ext" / "f.md").write_text("decoy")
            (_dk / "sub" / "real").mkdir(parents=True); (_dk / "sub" / "real" / "g.md").write_text("ok")
            _rsroot, _rentries = _fresh("rootlink-real")
            _rlink = base_tmp / "rootlink-link"
            try:
                _symlink_fn(_dk / "ext", _dk / "sub" / "mid")
                _symlink_fn(_dk / "ext" / "f.md", _dk / "sub" / "real" / "term.md")
                _symlink_fn(_rsroot, _rlink)
            except OSError as exc:
                _not_run(failures, _DISK_KIND_CASES, "no symlink support ({})".format(exc))
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

            # (d2) a portability violation under the adopter-installed enforcement pack (opf/enforcement,
            # U16) is caught: the root is scanned like every other required root, so operational
            # vocabulary in a shipped CI file fails.
            sroot, entries = _fresh("enforcement-term")
            (sroot / "opf/enforcement").mkdir(parents=True, exist_ok=True)
            (sroot / "opf/enforcement/placeholder.md").write_text(
                "# Note\n\nsee the session-handoff before running CI\n", encoding="utf-8")
            if _run_quiet(sroot, entries) != 1:
                failures.append("e2e: an operational term under opf/enforcement expected exit 1")

            # (d3) the enforcement pack's suffixless pre-commit hook (TEXT_PATHS) is scanned as text: clean
            # bytes pass (without TEXT_PATHS it is a C3 file-class finding), and an operational term in it
            # fails, so the allowance is a text scan, never a skip.
            sroot, entries = _fresh("precommit-hook-clean")
            (sroot / "opf/enforcement/precommit").mkdir(parents=True, exist_ok=True)
            entries.append((0o100755, "opf/enforcement/precommit/pre-commit"))
            (sroot / "opf/enforcement/precommit/pre-commit").write_text(
                "#!/bin/sh\n# Check the staged snapshot.\nexit 0\n", encoding="utf-8")
            if _run_quiet(sroot, entries) != 0:
                failures.append("e2e: the clean suffixless pre-commit hook expected exit 0")
            sroot, entries = _fresh("precommit-hook-term")
            (sroot / "opf/enforcement/precommit").mkdir(parents=True, exist_ok=True)
            entries.append((0o100755, "opf/enforcement/precommit/pre-commit"))
            (sroot / "opf/enforcement/precommit/pre-commit").write_text(
                "#!/bin/sh\n# see the session-handoff first\nexit 0\n", encoding="utf-8")
            if _run_quiet(sroot, entries) != 1:
                failures.append("e2e: an operational term in the pre-commit hook expected exit 1")

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
            if (_which or shutil.which)("git") is None:
                _not_run(failures, _REAL_GIT_CASE, "git is unavailable (not found on PATH)")
            else:
                sroot, _entries = _fresh("real-git")
                from _git_fixture_env import git_fixture_env
                git_env = git_fixture_env()

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

    if _vectors and os.environ.get(_SUPPRESS_VECTORS_ENV) == "1":
        # An inherited marker must never skip the vectors silently: record ONE named NOT RUN
        # failure so a marked run exits 1 (a vector child carries the marker and expects its own
        # injected failure); the full "SELF-TEST PASS:" line below is printed only by an
        # unmarked run whose vectors RAN, and a no-vectors nested run prints the distinct
        # no-vectors note instead.
        _not_run(failures, "the red-on-revert NOT RUN and entry vectors",
                 "suppressed by {} (a vector child, never a full run)".format(
                     _SUPPRESS_VECTORS_ENV))
    elif _vectors:
        # A marked fresh-process vector child cannot spawn children of its own even when its
        # injected setup failure stops failing (the marked gate above fails it first, and the
        # launcher's _CHILD_DEPTH_ENV refusal at the top of _not_run_vectors bounds even a
        # route around this gate at depth 1); every fresh Python VECTOR-ENTRY child,
        # marked or unmarked, runs through the ONE bounded launcher: its own process group,
        # killed BEFORE reaping on timeout, with every wait and drain bounded (the parent's
        # direct git launches and the launcher-timeout child's plain descendant stay
        # outside that launcher, as its docstring states).
        _not_run_vectors(failures)
        # An in-process call of self_test_main() hands the caller's environment back exactly
        # (HOME, XDG_CONFIG_HOME, the GIT_CONFIG_* pins and the caller's own GIT_* variables),
        # with the git fixture scrub in effect inside: the entry point is run in a child around
        # a stand-in main, once with each _vectors value so a constant passed in its place
        # fails. The probe's child is not a vector-entry child: the stand-in replaces the
        # isolated main, so it cannot recurse, and it runs only here, never in a marked child
        # (which launches nothing) or a nested no-vectors run.
        from _entry_restore_selftest import entry_restore_failures
        failures.extend(
            entry_restore_failures(__file__, "self_test_main", "_self_test_main_isolated",
                                   args=(True,))
            + entry_restore_failures(__file__, "self_test_main", "_self_test_main_isolated",
                                     args=(False,)))

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
    pass_head = "SELF-TEST PASS:" if _vectors else _NO_VECTORS_PASS_NOTE + ":"
    print("{} {}; the end-to-end surface cases hold (clean pass, planted identity/email/"
          "vocabulary/pathname/marker/unknown-file findings, tracked no-skip enumeration, GD-130 "
          "tracked/untracked stray-artifact pairs, and missing/untracked roots fail-closed); an "
          "unavailable temp directory, symlink support or git is a NOT RUN failure (nonzero exit), "
          "never a PASS (PARTIAL)".format(pass_head, core))
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
