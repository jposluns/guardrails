#!/usr/bin/env python3
"""Cited-standards gate: a numbered standard designator in a rule must resolve to a manifest or a recorded
exclusion.

.aiqt/standards/README.md makes two claims. A rule's `map-<key>` frontmatter may cite a framework only after its
manifest lands there; check_mappings holds that. A numbered designator in rule prose must resolve to a manifest
or a recorded exclusion; this gate holds that, over the whole text of each corpus file, frontmatter included.
It reads the corpus without consulting any register row, finds every numbered standard designator, normalises
it to one canonical identity, and fails on an identity that no manifest registers and no recorded exclusion
covers. A manifest registers the identities this same grammar finds in its own `name` and `edition` fields, so
there is no hand-kept alias list to drift.

Grammar (case-insensitive). These spellings, and only these, are discovered:
  ISO[/IEC][/IEEE][/type] n[-part][:yyyy]   identity ISO n[-part]             (organisation, type, year dropped)
  [NIST] SP n-n[letter][-n]                 identity NIST SP n-n[LETTER][-n]  (SP 800-53A is not SP 800-53)
  NIST 800-n[letter][-n]                    identity NIST SP 800-n[LETTER][-n]
  NIST SP n                                 identity NIST SP n                (a single number needs NIST)
  NIST AI n-n                               identity NIST AI n-n
  NIST IR n[-n][letter]                     identity NIST IR n[-n][LETTER]    (also NISTIR n[-n][letter])
  [NIST] FIPS [PUB] n[-n]                   identity FIPS n[-n]               (FIPS 140-2 is not FIPS 140-3)
  RFC n                                     identity RFC n
ISO is joined to IEC, IEEE or a type by a slash (whitespace allowed around it), one dash, or whitespace, and several
may follow it (ISO/IEC/IEEE, ISO-IEC, ISO IEC, ISO/IEC CD TR). A type is one of the ten prefixes the grammar reads:
a deliverable type (TR, TS, PAS) or a project-stage abbreviation (AWI, WD, CD, DIS, FDIS, DTR, DTS). Other ISO
prefixes exist; the residual names some and does not claim to list them all. SP is also spelled Special Publication,
optionally followed by (SP) (NIST Special Publication (SP) 800-63B). NIST is joined to SP, AI, IR or FIPS, and FIPS
to PUB, by whitespace, one dash, or one dot (NIST.SP.800-53, NIST-AI-100-1, NIST.FIPS.140-3, FIPS-PUB-199), NISTIR
also by nothing. Between a body and its number the separator is whitespace, one dash, one dot, or nothing
(RFC3339, ISO-27001, RFC.3339, SP800-53, FIPS Pub. 140-2, the dot after Pub). A dash is any of
U+002D, U+2010 to U+2015, U+2212, U+FE58, U+FE63 and U+FF0D. Wherever the grammar allows whitespace (between words,
before and after the ISO slash, after a dash or dot that joins, after the year colon) that whitespace may hold one
line break (ISO-<break>27001, RFC-<break>3339, NIST.AI.<break>600-1), and Markdown blockquote markers (">") may open
the continued line; no other line prefix is read (a comment marker #, // or *). A line break is an LF (a CR before
it is whitespace): every other whitespace character, a lone CR, VT, FF, U+0085, U+2028 and U+2029 among them, is
same-line whitespace to the gate. Every spelling here is matched from its first character, so a finding prints the
whole designator.

Token boundaries. A designator starts only where the character before it is not a letter or digit, and it runs to
the end of its token. Letters or digits the grammar does not read as a known part (RFC3339Parser, SP 800-53Ar5,
SP 800-53AB) join it, and so does a dot, colon or slash with a digit right after it (ISO 27001/27002). A dash after
it joins it too, unless, after any further dashes, a letter follows (RFC 3339-compliant, NIST-AI-100-1--AI-RMF) or
same-line whitespace and then a character that is neither whitespace nor a digit (ISO 27001- and 27002). So a dash
followed by a digit (SP 800-63-3-1), by whitespace and a digit, one line break and blockquote markers allowed
(ISO 42001- 2023, NIST SP 800-<break>53, ISO 42001-<break>> 2023), by a line break, directly or after same-line
whitespace, whatever opens the next line (ISO 42001-<break># 2023, ISO 42001- <break># 2023), by markup or
punctuation (ISO 42001-<br>2023), or by the end of the text joins the token; the digits after it join as well only
when whitespace and blockquote markers alone come before them. Such a run is reported as token "<text>": the
matched text with each whitespace run or blockquote marker printed as one space, each dash as a hyphen, and its
letter case kept (ISO 42001-<break># 2023 prints as token "ISO 42001-"). A token that ends at a dash keeps nothing
after the dash, so ISO 99999-<break># 2023, ISO 99999-<br>1 and ISO 99999-**2031** all print as token "ISO 99999-",
and one exclusion naming that token waives each of them in its listed paths, whatever followed the dash. A token
is never resolved to a shorter identity; no manifest registers a token, and only an exclusion naming the whole
printed text waives it (NIST.SP.800-53r5, not SP.800-53r5). An exclusion is read with the corpus grammar, which
reads a space wherever it reads a line break, so it may give a token exactly as a finding prints it. An underscore,
other punctuation, whitespace, or a dash as above ends a token (strict_rfc3339 and RFC 3339-compliant both read
RFC 3339).

The corpus is scanned as raw text, code spans and fences included, so backticks are not a bypass. The
threshold is fixed at one document: a single citation in a single file fails. It is not configurable.

Residual, printed on every run (RESIDUAL below): only the spellings above are discovered. Two kinds of omission are
listed there. A named omission is spelled out by name, and the self-test pins each one as not discovered: the ISO
prefixes DPAS, PWI, CDV, PDTR, PDTS, TTA, NP and PRF (ISO/IEC PRF 42005), the IWA and ISO Guide series (IWA 14,
ISO Guide 73), the partner ISO/SAE (ISO/SAE 21434), a NIST series other than SP, AI, IR and FIPS (NIST.CSWP.29), SP
with one number and no NIST (SP 1270), NIST with a number outside the 800 series and no SP (NIST 1270), and an
underscore joining words (NIST_SP_800-53). The named list is neither complete nor drawn from an authoritative
register of ISO or NIST designations. The generic omission covers everything else outside the grammar: any other
body, series, partner, type, stage or spelling, a framework named without a number (an OWASP, MITRE or CSA title,
"AI RMF", "Google SAIF", "OWASP SCVS"), and a designator of another body (IEC alone, IEEE alone, ANSI, ETSI, CIS).
Also not seen: a designator broken by markup, an entity or two line breaks in one gap (*ISO/IEC* 42001,
ISO/IEC&nbsp;42001), one whose gap or join continues onto a line opened by anything but whitespace and blockquote
markers (ISO/IEC<break># 42001, ISO-<break>// 27001), or a line break inside a number where the text before the
break is no designator (SP 800-<break>53, NIST AI 100-<break>1). Read from a later word: NIST joined to its series
any other way (NIST/SP 800-53, NIST - SP 800-53, NIST_SP800-53 all read from SP; the identity is the same). Read
short, so it can resolve to a registered shorter identity: whatever ends a token ends the designator there and what
follows is not read, and the cases named here are not a complete list. Whitespace before a dash ends a designator
(ISO 42001 - 2023 reads ISO 42001), as do an entity, markup or any other character outside the dash list before or
in place of a dash (ISO 42001&ndash;2023, a soft hyphen); a dash followed by same-line whitespace and then a
character that is neither whitespace nor a digit, such as markup after a space (ISO 42001- **2023**,
ISO 42001- (2023), ISO 42001- <sup>2023</sup>) or, since only LF ends a line, a lone CR, VT, FF, U+0085, U+2028 or
U+2029 and then such a character (ISO 42001-<CR># 2023 reads ISO 42001); and a dot, colon or slash followed by
whitespace, on the same line or at a line end, other than the ISO year colon (ISO 27001/ 27002 and
ISO 27001/<break>27002 read ISO 27001). Prose control ids (CWE-79, LLM01) are not checked; a prose edition is not
compared with the manifest `edition`, and a hyphenated year reads as a part: ISO 42001-2023 is its own identity, so
it does not resolve to ISO 42001, and it resolves when a manifest registers ISO 42001-2023 itself. Matching errs
toward reporting: it is case-insensitive and an underscore is a boundary, so a look-alike (sp 3-4, strict_rfc3339,
ISO WD-40) is reported, never ignored, a dash that ends a line makes a token even before a hyphenated word
(RFC 3339-<break>compliant is token "RFC 3339-"), and a sentence ending in a body before a number reads as a
designator (RFC. 2 and RFC.<break>2. Next read RFC 2).

  check_cited_standards.py                  check the repository's rule corpus
  check_cited_standards.py --root DIR       check the tree at DIR (manifests from DIR/.aiqt/standards)
  check_cited_standards.py --config PATH    read the corpus globs and exclusions from a TOML file
  check_cited_standards.py --self-test      run the fixture vectors

Without --config the built-in default applies: corpus [".aiqt/core/rules/*.md"] and no exclusions; no
default file is read. This repository's own CI and tools/run_all_checks.sh pass --config
.aiqt/cited-standards.toml (tracked, not shipped), whose corpus adds the OPF prompt-pack members
(opf/prompt-pack/**/*.md) to the rule files. A config file carries:
  format-version = 1                        required
  corpus = ["<root-relative glob>", ...]    optional; each glob must match at least one file
  [[exclusion]]                             optional, repeatable
  designator = "RFC 3339"                   must parse, in full, to exactly one identity
  paths = ["<corpus file>", ...]            non-empty; each must be a file of the corpus
  reason = "..."                            non-empty
An exclusion waives only its listed paths, so a later citation elsewhere still fails, and it goes stale (a
finding) when its identity no longer occurs in one of those paths.

Exit: 0 clean; 1 an unresolved identity or token, or a stale exclusion; 2 cannot evaluate (a malformed or
unreadable manifest, config or corpus file, a corpus glob that matches nothing, an unlistable directory, two
manifests deriving one identity, an exclusion that a manifest contradicts, or a usage error). Fail-closed: an
input it cannot evaluate exits 2, never 0; a spelling it reads short (see the residual) can still pass clean.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_cited_standards.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import contextlib
import io
import os
import re
import socket
import stat
import tempfile
import tomllib
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402
from _standards import load_manifests, ManifestError, dir_present, ensure_listable  # noqa: E402

DEFAULT_CORPUS = (".aiqt/core/rules/*.md",)
STANDARDS_REL = ".aiqt/standards"
MIN_DOCUMENTS = 1           # fixed, not configurable: one citation in one document is a finding
CONFIG_KEYS = {"format-version", "corpus", "exclusion"}
EXCLUSION_KEYS = {"designator", "paths", "reason"}

RESIDUAL = (
    "Residual: only the designator spellings in the gate's grammar are discovered: ISO joined by a slash, a dash or "
    "whitespace to any of IEC, IEEE, a deliverable type (TR, TS, PAS) or a project-stage abbreviation (AWI, WD, "
    "CD, DIS, FDIS, DTR, DTS); NIST SP or NIST Special Publication with a number; SP or Special Publication with "
    "an n-n number; NIST 800-n; NIST AI; NIST IR or NISTIR; FIPS, NIST FIPS or FIPS PUB; and RFC; each followed by "
    "its number. NIST is joined to its series, and FIPS to PUB, by whitespace, a dash or a dot (NIST.SP.800-53), "
    "a body to its number by whitespace, a dash, a dot or nothing, and a joining dash or dot may end a line.",
    "  Not seen, named (not a complete list): the ISO prefixes DPAS, PWI, CDV, PDTR, PDTS, TTA, NP and PRF "
    "(ISO/IEC PRF 42005); the IWA and ISO Guide series (IWA 14, ISO Guide 73); the partner ISO/SAE "
    "(ISO/SAE 21434); a NIST series other than SP, AI, IR and FIPS (NIST.CSWP.29); SP with one number and no NIST "
    "(SP 1270); NIST with a number outside the 800 series and no SP (NIST 1270); an underscore joining words "
    "(NIST_SP_800-53).",
    "  Not seen, generic: any other body, series, partner, type, stage or spelling outside the grammar, such as a "
    "framework named without a number (an OWASP, MITRE or CSA title, AI RMF, Google SAIF, OWASP SCVS) or a "
    "designator of another body (IEC alone, IEEE alone, ANSI, ETSI, CIS).",
    "  Not seen: a designator broken by markup or an entity (*ISO/IEC* 42001, ISO/IEC&nbsp;42001), by two line "
    "breaks in one gap, by a line break onto a line opened by anything but whitespace and blockquote markers "
    "(ISO/IEC<break># 42001, ISO-<break>// 27001), or by a line break inside a number where the text before the "
    "break is no designator (SP 800-<break>53, NIST AI 100-<break>1).",
    "  Read from a later word, same identity: NIST/SP 800-53, NIST - SP 800-53 and NIST_SP800-53 read from SP.",
    "  Read short, so it can resolve to a registered shorter identity (whatever ends a token ends the designator "
    "there and what follows is not read; the cases named here are not a complete list): whitespace before a dash "
    "(ISO 42001 - 2023 reads ISO 42001); an entity, markup or any other character outside the dash list before or "
    "in place of a dash (ISO 42001&ndash;2023, a soft hyphen); a dash followed by same-line whitespace and then a "
    "character that is neither whitespace nor a digit, such as markup (ISO 42001- **2023**, ISO 42001- (2023), "
    "ISO 42001- <sup>2023</sup>) or, since only LF ends a line, a lone CR, VT, FF, U+0085, U+2028 or U+2029 and "
    "then such a character (ISO 42001-<CR># 2023); and a dot, colon or slash followed by whitespace, on the same "
    "line or at a line end, other than the ISO year colon (ISO 27001/ 27002 and ISO 27001/<break>27002 read "
    "ISO 27001).",
    "  Prose control ids (CWE-79, LLM01) are not checked.",
    "  A prose edition is not compared with the manifest edition (ISO/IEC 42001:2015 resolves to ISO 42001); a "
    "hyphenated year reads as a part (ISO 42001-2023 is its own identity: it does not resolve to ISO 42001, and it "
    "resolves when a manifest registers ISO 42001-2023).",
    "  A designator run on into letters or digits it does not read (RFC3339Parser, SP 800-53Ar5), or into a dash "
    "that, after any further dashes, neither a letter nor same-line whitespace and then a character that is neither "
    "whitespace nor a digit follows, is reported as a whole token, never resolved to a shorter identity. A dash "
    "before an LF, directly or after same-line whitespace, counts whatever opens the next line: "
    "ISO 42001-<break># 2023 is token ISO 42001-. Only an exclusion naming the whole token as printed waives it. A "
    "token that ends at a dash keeps nothing after it (ISO 99999-<break># 2023, ISO 99999-<br>1 and "
    "ISO 99999-**2031** all print as token ISO 99999-), so one exclusion of it waives each such citation in its "
    "listed paths.",
    "  Matching is case-insensitive and an underscore is a boundary, so a look-alike (sp 3-4, strict_rfc3339, "
    "ISO WD-40) is reported, never ignored; a dash that ends a line makes a token even before a word "
    "(RFC 3339-<break>compliant), and a sentence ending in a body before a number reads as a designator "
    "(RFC. 2 and RFC.<break>2. Next read RFC 2).",
    "  Code spans and fences are scanned, not stripped, so backticks are not a bypass.",
)

# The dashes: U+002D, the General Punctuation dashes U+2010 to U+2015, U+2212 MINUS SIGN, and the compatibility
# forms U+FE58 SMALL EM DASH, U+FE63 SMALL HYPHEN-MINUS and U+FF0D FULLWIDTH HYPHEN-MINUS. Each has a vector.
_DASHES = "-\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"
_DASH = "[" + _DASHES + "]"
# BREAK is one line break, after which Markdown blockquote markers may open the continued line. A gap between words
# is whitespace holding at most one BREAK. GAP needs at least one character, OPT may be empty. JOIN sits between
# NIST and its series word, or FIPS and PUB: a gap, or one dash or one dot with OPT after it, so a joining dash or
# dot may be followed by a space or end a line (FIPS Pub. 140-2, ISO-<break>27001). NUMSEP sits between a body and
# its number: a JOIN or nothing. Wherever a BREAK is read a space is read too, and a finding prints a space in its
# place, so one pattern reads both the corpus and an exclusion naming a finding as printed.
_BREAK = r"[^\S\n]*\n[^\S\n]*(?:>[^\S\n]*)*"
_GAP = r"(?:[^\S\n]+|" + _BREAK + ")"
_OPT = "(?:" + _GAP + ")?"
_JOIN = "(?:" + _GAP + "|" + _DASH + _OPT + r"|\." + _OPT + ")"
_NUMSEP = _JOIN + "?"
# The ISO deliverable types (TR, TS, PAS) and project-stage abbreviations (AWI, WD, CD, DIS, FDIS, DTR, DTS) the
# grammar reads. They are not every ISO prefix: RESIDUAL names some it does not read. Each has a vector.
_TYPE = "(?:TR|TS|PAS|AWI|WD|CD|FDIS|DIS|DTR|DTS)"
# SP, or Special Publication with an optional (SP) after it.
_SP = r"(?:SP|SPECIAL" + _GAP + r"PUBLICATION(?:" + _GAP + r"\(SP\))?)"

# The designator pattern. A designator starts where no letter or digit precedes it. GLUED takes the rest of its
# token: letters or digits, a dot, colon or slash followed by a digit, and a dash unless, after any further dashes, a
# letter follows it or same-line whitespace and then a character that is neither whitespace nor a digit does (so
# ISO-IEC-27001-2022--full-text ends at 2022); the digits after a dash join it across OPT. A non-empty GLUED makes the
# whole match a token, so a dash right after a designator and before an LF, directly or after same-line whitespace,
# leaves no shorter identity behind it, whatever opens the next line. Only LF ends a line here: a dash before a lone
# CR, U+2028 or other non-LF whitespace and then a character that is neither whitespace nor a digit (a comment marker,
# markup) ends the designator before the dash, which reads short (RESIDUAL names such cases, not claiming all).
_PATTERN = re.compile(
    r"(?<![^\W_])(?:"
    r"(?P<iso>ISO(?:(?:" + _OPT + "/" + _OPT + "|" + _DASH + _OPT + "|" + _GAP + ")(?:IEC|IEEE|" + _TYPE + "))*"
    + _NUMSEP + r"(?P<iso_n>\d+)(?:" + _DASH + r"(?P<iso_part>\d+))?(?::" + _OPT + r"\d{4})?)"
    r"|(?P<ai>NIST" + _JOIN + "AI" + _NUMSEP + r"(?P<ai_a>\d+)" + _DASH + r"(?P<ai_b>\d+))"
    r"|(?P<sp>(?:(?:NIST" + _JOIN + ")?" + _SP + _NUMSEP + "|NIST" + _NUMSEP + "(?=800" + _DASH + r"\d))"
    r"(?P<sp_a>\d+)" + _DASH + r"(?P<sp_b>\d+)(?P<sp_x>[A-Z])?(?:" + _DASH + r"(?P<sp_c>\d+))?)"
    r"|(?P<sp1>NIST" + _JOIN + _SP + _NUMSEP + r"(?P<sp1_a>\d+))"
    r"|(?P<ir>NIST" + _JOIN + "?IR" + _NUMSEP + r"(?P<ir_a>\d+)(?:" + _DASH + r"(?P<ir_b>\d+))?(?P<ir_x>[A-Z])?)"
    r"|(?P<fips>(?:NIST" + _JOIN + ")?FIPS(?:" + _JOIN + "PUB)?" + _NUMSEP + r"(?P<fips_a>\d+)(?:" + _DASH
    + r"(?P<fips_b>\d+))?)"
    r"|(?P<rfc>RFC" + _NUMSEP + r"(?P<rfc_n>\d+))"
    r")(?P<glued>(?:[^\W_]|[.:/](?=\d)|" + _DASH + "(?:" + _OPT + r"(?=\d)|(?!" + _DASH
    + r"*(?:[^\W\d_]|[^\S\n]+[^\s\d]))))*)",
    re.IGNORECASE)
_TOKEN = 'token "'      # the identity prefix of a designator run on into a suffix the grammar does not read
_ONE_DASH = str.maketrans({ch: "-" for ch in _DASHES})


class CannotEvaluate(Exception):
    """An input the gate cannot read or trust: exit 2, never a clean pass."""


def _num(text):
    return str(int(text))


def _flat(text):
    """Match text on one line: blockquote markers and whitespace runs become one space, every dash a hyphen."""
    return " ".join(text.replace(">", " ").split()).translate(_ONE_DASH)


def _identity(m):
    """The canonical identity of one designator match; a match with a glued suffix is its whole token."""
    if m.group("glued"):
        return _TOKEN + _flat(m.group(0)) + '"'
    if m.group("iso"):
        part = "-" + _num(m.group("iso_part")) if m.group("iso_part") else ""
        return "ISO " + _num(m.group("iso_n")) + part
    if m.group("ai"):
        return "NIST AI {}-{}".format(_num(m.group("ai_a")), _num(m.group("ai_b")))
    if m.group("sp"):
        tail = "-" + _num(m.group("sp_c")) if m.group("sp_c") else ""
        return "NIST SP {}-{}{}{}".format(_num(m.group("sp_a")), _num(m.group("sp_b")),
                                          (m.group("sp_x") or "").upper(), tail)
    if m.group("sp1"):
        return "NIST SP " + _num(m.group("sp1_a"))
    if m.group("ir"):
        tail = "-" + _num(m.group("ir_b")) if m.group("ir_b") else ""
        return "NIST IR {}{}{}".format(_num(m.group("ir_a")), tail, (m.group("ir_x") or "").upper())
    if m.group("fips"):
        tail = "-" + _num(m.group("fips_b")) if m.group("fips_b") else ""
        return "FIPS " + _num(m.group("fips_a")) + tail
    return "RFC " + _num(m.group("rfc_n"))


def designators(text):
    """[(identity, line, matched text)] for every numbered designator in text, in order."""
    out = []
    for m in _PATTERN.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        out.append((_identity(m), line, _flat(m.group(0))))
    return out


def parse_designator(text):
    """The single identity a whole designator string names, or None if it is not exactly one designator. A token
    is read as a finding prints it."""
    m = _PATTERN.fullmatch(text.strip())
    return _identity(m) if m else None


def manifest_identities(manifests):
    """{identity: manifest file name}, read from each manifest's own name and edition fields with the corpus
    grammar. A token registers nothing, so it never resolves. Two manifests deriving one identity is a
    contradiction the gate cannot resolve: exit 2."""
    owner = {}
    for key in sorted(manifests):
        man = manifests[key]
        for field in (man.name, man.edition):
            for ident, _line, _text in designators(field):
                if ident.startswith(_TOKEN):
                    continue
                prior = owner.get(ident)
                if prior is not None and prior != man.path.name:
                    raise CannotEvaluate("manifests {} and {} both derive the identity {}".format(
                        prior, man.path.name, ident))
                owner[ident] = man.path.name
    return owner


def _regular_file(path, what, shown=None):
    shown = path if shown is None else shown
    try:
        st = os.stat(path)
    except OSError as exc:
        raise CannotEvaluate("{} {} cannot be read: {}".format(what, shown, exc.strerror or exc))
    if not stat.S_ISREG(st.st_mode):
        raise CannotEvaluate("{} {} is not a regular file".format(what, shown))


def load_config(path):
    """(corpus globs, raw exclusion tables) from a config file; CannotEvaluate on anything malformed."""
    _regular_file(path, "config")
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise CannotEvaluate("config {} cannot be read: {}".format(path, exc))
    # ValueError and RecursionError too: tomllib raises a bare ValueError past the int-string digit limit and a
    # RecursionError on deep nesting, the same family _standards.load_manifests catches.
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("config {} is not valid TOML: {}".format(path, exc))
    unknown = sorted(set(data) - CONFIG_KEYS)
    if unknown:
        raise CannotEvaluate("config {}: unknown key(s): {}".format(path, ", ".join(unknown)))
    version = data.get("format-version")
    if type(version) is not int or version != 1:
        raise CannotEvaluate("config {}: format-version must be the integer 1".format(path))
    corpus = data.get("corpus", list(DEFAULT_CORPUS))
    if not isinstance(corpus, list) or not corpus or not all(isinstance(g, str) and g for g in corpus):
        raise CannotEvaluate("config {}: corpus must be a non-empty list of non-empty strings".format(path))
    exclusions = data.get("exclusion", [])
    if not isinstance(exclusions, list) or not all(isinstance(e, dict) for e in exclusions):
        raise CannotEvaluate("config {}: exclusion must be an array of tables ([[exclusion]])".format(path))
    return tuple(corpus), exclusions


def _check_glob(pattern):
    if "\\" in pattern or pattern.startswith("/") or PurePosixPath(pattern).is_absolute():
        raise CannotEvaluate("corpus glob {!r} must be a root-relative POSIX path".format(pattern))
    parts = PurePosixPath(pattern).parts
    if not parts or ".." in parts:
        raise CannotEvaluate("corpus glob {!r} must stay inside the root (no '..')".format(pattern))
    return parts


def _raise(exc):
    raise exc


def expand_corpus(root, globs):
    """{root-relative posix path: Path} for every regular file the globs match. Each glob must match at least
    one file; an unlistable directory on the way is a read error, never an empty match."""
    real_root = root.resolve()
    files = {}
    for pattern in globs:
        parts = _check_glob(pattern)
        static = []
        for part in parts[:-1]:
            if any(ch in part for ch in "*?["):
                break
            static.append(part)
        base = root.joinpath(*static)
        deep = len(static) < len(parts) - 1      # a wildcard in a directory component
        matched = 0
        try:
            if dir_present(base, root=root):
                # Path.glob silently yields nothing for a directory it cannot list; force the read error.
                ensure_listable(base)
                if deep:
                    for _dirpath, _dirs, _names in os.walk(base, onerror=_raise):
                        pass
                for path in sorted(root.glob(pattern)):
                    if path.is_dir():
                        continue      # any other non-regular match fails closed in read_corpus
                    if not path.resolve().is_relative_to(real_root):
                        raise CannotEvaluate("corpus file {} resolves outside the root".format(path))
                    files[path.relative_to(root).as_posix()] = path
                    matched += 1
        except OSError as exc:
            raise CannotEvaluate("corpus glob {!r} cannot be listed: {}".format(pattern, exc))
        if matched == 0:
            raise CannotEvaluate("corpus glob {!r} matches no file; an empty corpus is not a clean one".format(
                pattern))
    return files


def read_corpus(files):
    """{rel: [(identity, line, text)]}; an unreadable or non-UTF-8 corpus file is CannotEvaluate."""
    cited = {}
    for rel in sorted(files):
        _regular_file(files[rel], "corpus file", rel)
        try:
            with open(files[rel], "rb") as handle:
                raw = handle.read()
        except OSError as exc:
            raise CannotEvaluate("corpus file {} cannot be read: {}".format(rel, exc))
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CannotEvaluate("corpus file {} is not valid UTF-8: {}".format(rel, exc))
        cited[rel] = designators(text)
    return cited


def validate_exclusions(raw, registered, corpus_rels):
    """{identity: (designator, [paths], reason)}; CannotEvaluate on a malformed or contradicted exclusion."""
    out = {}
    for i, row in enumerate(raw, 1):
        where = "exclusion #{}".format(i)
        unknown = sorted(set(row) - EXCLUSION_KEYS)
        missing = sorted(EXCLUSION_KEYS - set(row))
        if unknown or missing:
            raise CannotEvaluate("{}: unknown key(s) [{}], missing key(s) [{}]".format(
                where, ", ".join(unknown), ", ".join(missing)))
        designator, paths, reason = row["designator"], row["paths"], row["reason"]
        if not isinstance(reason, str) or not reason.strip():
            raise CannotEvaluate("{}: reason must be a non-empty string".format(where))
        ident = parse_designator(designator) if isinstance(designator, str) else None
        if ident is None:
            raise CannotEvaluate("{}: designator {!r} does not parse to exactly one identity".format(
                where, designator))
        if ident in registered:
            raise CannotEvaluate("{}: {} is registered by manifest {}; an exclusion contradicts it".format(
                where, ident, registered[ident]))
        if ident in out:
            raise CannotEvaluate("{}: {} already has an exclusion; list every path in one entry".format(
                where, ident))
        if (not isinstance(paths, list) or not paths or not all(isinstance(p, str) and p for p in paths)
                or len(paths) != len(set(paths))):
            raise CannotEvaluate("{}: paths must be a non-empty list of distinct strings".format(where))
        outside = sorted(p for p in paths if p not in corpus_rels)
        if outside:
            raise CannotEvaluate("{}: path(s) not in the corpus: {}".format(where, ", ".join(outside)))
        out[ident] = (designator, list(paths), reason)
    return out


def evaluate(cited, registered, exclusions):
    """(unresolved {identity: [(rel, line, text)]}, stale [(identity, rel)]). Pure."""
    by_ident = {}
    for rel in sorted(cited):
        for ident, line, text in cited[rel]:
            by_ident.setdefault(ident, []).append((rel, line, text))
    unresolved = {}
    for ident in sorted(by_ident):
        if ident in registered:
            continue
        waived = set(exclusions[ident][1]) if ident in exclusions else set()
        sites = [s for s in by_ident[ident] if s[0] not in waived]
        if len({rel for rel, _l, _t in sites}) >= MIN_DOCUMENTS:
            unresolved[ident] = sites
    stale = []
    for ident in sorted(exclusions):
        for rel in exclusions[ident][1]:
            if not any(i == ident for i, _l, _t in cited.get(rel, ())):
                stale.append((ident, rel))
    return unresolved, stale


def run(root, config=None):
    root = Path(root)
    try:
        try:
            manifests = load_manifests(root / STANDARDS_REL, root=root)
        except (ManifestError, OSError) as exc:
            raise CannotEvaluate("standards manifests: {}".format(exc))
        registered = manifest_identities(manifests)
        if config is None:
            globs, raw_exclusions = DEFAULT_CORPUS, []
        else:
            globs, raw_exclusions = load_config(Path(config))
        files = expand_corpus(root, globs)
        cited = read_corpus(files)
        exclusions = validate_exclusions(raw_exclusions, registered, set(files))
    except CannotEvaluate as exc:
        print("error: {}; cannot evaluate (fail-closed)".format(exc), file=sys.stderr)
        for line in RESIDUAL:
            print(line)
        return 2
    unresolved, stale = evaluate(cited, registered, exclusions)
    if unresolved or stale:
        print("FAIL: {} unresolved standard identity(ies), {} stale exclusion path(s)".format(
            len(unresolved), len(stale)))
        for ident in sorted(unresolved):
            sites = unresolved[ident]
            why = ("a designator run on into a suffix the grammar does not read; it never resolves to a shorter "
                   "identity, and only an exclusion naming the whole token covers it" if ident.startswith(_TOKEN)
                   else "no manifest registers it and no exclusion covers it")
            print("  {}: cited in {} document(s); {}".format(ident, len({rel for rel, _l, _t in sites}), why))
            for rel, line, text in sites:
                print("    {}:{}: {}".format(rel, line, text))
        for ident, rel in stale:
            print("  stale exclusion: {} no longer occurs in {}; remove that path from the exclusion".format(
                ident, rel))
        for line in RESIDUAL:
            print(line)
        return 1
    n_ids = len({i for sites in cited.values() for i, _l, _t in sites})
    print("PASS: {} corpus document(s) checked; {} cited numbered standard identity(ies) all resolve "
          "({} derived from {} manifest(s), {} exclusion(s))".format(
              len(cited), n_ids, len(registered), len(manifests), len(exclusions)))
    for line in RESIDUAL:
        print(line)
    return 0


# ---------------------------------------------------------------------------------------------- self-test

_BASE_NAME = "ISO/IEC 42001:2023 test"
_A = ".aiqt/core/rules/a.md"
_B = ".aiqt/core/rules/b.md"
_EXCL = 'format-version = 1\n[[exclusion]]\ndesignator = "{}"\npaths = ["{}"]\nreason = "{}"\n'


def _manifest_text(stem, name, edition="2023", kind="control"):
    return ('map-key = "map-{}"\nname = "{}"\npublisher = "p"\nedition = "{}"\nkind = "{}"\nstatus = "stable"\n'
            'catalogue = "subset"\ncitation-unit = "clause"\nid-pattern = "^[0-9]+$"\nsource-artefact = "s"\n'
            'retrieved = "2026-01-01"\n\n[[id]]\ncode = "1"\n').format(stem, name, edition, kind)


def _fixture(tmp, manifests=None, rules=None, config=None, others=None):
    """A temp root with a .git marker, the given manifests ({stem: toml text}) and rule files ({name: bytes or
    str}; None is a dangling symlink, ("outside", text) a symlink to a file beside the root, ("socket",) a unix
    socket). config is TOML text written beside the root, or used as the literal path when it is a Path. others
    ({root-relative path: text}) are files outside the rules directory, such as an OPF prompt-pack member."""
    root = Path(tmp) / "root"
    (root / ".git").mkdir(parents=True)
    std = root / STANDARDS_REL
    std.mkdir(parents=True)
    if manifests is None:
        manifests = {"iso-42001": _manifest_text("iso-42001", _BASE_NAME)}
    for stem, text in manifests.items():
        (std / (stem + ".toml")).write_text(text, encoding="utf-8")
    rules_dir = root / ".aiqt" / "core" / "rules"
    rules_dir.mkdir(parents=True)
    for name, body in (rules if rules is not None else {"r.md": "# Rule\n\nNothing cited.\n"}).items():
        if body is None:      # a dangling symlink: present in the listing, unreadable as a file
            (rules_dir / name).symlink_to(rules_dir / "absent-target.md")
            continue
        if body == ("socket",):       # listed, not a regular file, and open() fails at once instead of blocking
            cwd = os.getcwd()
            os.chdir(rules_dir)       # bind a short relative name: AF_UNIX caps the path length
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                    sock.bind(name)
            finally:
                os.chdir(cwd)
            continue
        if isinstance(body, tuple):   # ("outside", text): a regular file outside the root, linked from inside
            target = Path(tmp) / ("outside-" + name)
            target.write_text(body[1], encoding="utf-8")
            (rules_dir / name).symlink_to(target)
            continue
        (rules_dir / name).write_bytes(body.encode("utf-8") if isinstance(body, str) else body)
    for rel, text in (others or {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    cfg = None
    if isinstance(config, Path):
        cfg = config
    elif config is not None:
        cfg = Path(tmp) / "cited.toml"
        cfg.write_text(config, encoding="utf-8")
    return root, cfg


def _drive(manifests=None, rules=None, config=None, patch=None, others=None):
    """run() over a fixture; patch ({global name: value}) is swapped into this module for the run only.
    Every patch name must already be a module global: an unknown name (a misspelling, say) is rejected
    before anything is applied, because installing it would leave the intended fault uninjected and
    restoring the saved names could never remove it from this module."""
    unknown = sorted(name for name in (patch or {}) if name not in globals())
    if unknown:
        raise ValueError("_drive patch name(s) not module globals: {}".format(", ".join(unknown)))
    saved = {name: value for name, value in globals().items() if name in (patch or {})}
    with tempfile.TemporaryDirectory(prefix="aiqt-cited-standards-selftest-") as tmp:
        root, cfg = _fixture(tmp, manifests, rules, config, others)
        buf = io.StringIO()
        globals().update(patch or {})
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                code = run(root, cfg)
        finally:
            globals().update(saved)
        return code, buf.getvalue()


def _unlistable(directory):
    raise PermissionError(13, "Permission denied (injected by the self-test)", str(directory))


def _devnull_corpus(_root, _globs):
    return {"r.md": Path(os.devnull)}


def _two_manifests(stem, name, edition="2023"):
    return {"iso-42001": _manifest_text("iso-42001", _BASE_NAME), stem: _manifest_text(stem, name, edition)}


# (label, expected exit, manifests, rules, config, a predicate the combined output must satisfy[, a patch of module
# globals for the run]). V1 to V16 each fail under one named weakening of the gate (register-driven discovery,
# ignored manifests, a dropped /IEC or year normalisation, an edition check, a global exclusion, no reason check, no
# stale check, an allowed contradiction, a threshold of 2, stripped code, a skipped undecodable file, an empty
# corpus read as clean, a missing config falling back to the default, ignored unknown keys, a swallowed manifest
# error, last writer wins). V26 to V35 hold the token boundaries and wrapping: each before V35 fails on a grammar
# with no trailing boundary or no line break after "ISO/". V36 to V42 each fail when one guarded branch is removed:
# the duplicate-exclusion check, per-path staleness, the outside-the-root check, the distinct-paths check, the
# integer type of format-version, ensure_listable before a glob, and the regular-file check in read_corpus. The
# rest widen the fail-closed coverage.
_RFC = _two_manifests("rfc-3339", "RFC 3339 date and time")
_SP53 = _two_manifests("nist-80053", "NIST SP 800-53 controls")
_TWO_PATHS = 'format-version = 1\n[[exclusion]]\ndesignator = "RFC 3339"\npaths = ["{}", "{}"]\nreason = "r"\n'
_VECTORS = (
    ("V0 clean corpus, no citation", 0, None, None, None, lambda o: "PASS: 1 corpus document(s)" in o),
    ("V1 unregistered ISO/IEC 99999:2031 fails, naming file and identity", 1, None,
     {"r.md": "# Rule\n\nThis rule follows ISO/IEC 99999:2031.\n"}, None,
     lambda o: "ISO 99999: cited in 1 document(s)" in o and ".aiqt/core/rules/r.md:3:" in o),
    ("V2 a manifest name carrying ISO/IEC 99999 resolves it", 0, _two_manifests("iso-99999", "ISO/IEC 99999 t"),
     {"r.md": "This rule follows ISO/IEC 99999:2031.\n"}, None, lambda o: o.startswith("PASS")),
    ("V3 ISO 42001 (no /IEC, no year) resolves", 0, None, {"r.md": "Follows ISO 42001.\n"}, None,
     lambda o: o.startswith("PASS") and "1 cited" in o),
    ("V4 ISO/IEC 42001:2015 resolves (no edition check yet) and the residual is printed", 0, None,
     {"r.md": "Follows ISO/IEC 42001:2015.\n"}, None,
     lambda o: o.startswith("PASS") and "A prose edition is not compared" in o),
    ("V5 an exclusion waives only its listed paths", 1, None,
     {"a.md": "Timestamps follow RFC 3339.\n", "b.md": "Timestamps follow RFC 3339.\n"},
     _EXCL.format("RFC 3339", _A, "a timestamp format, not a crosswalk"),
     lambda o: ".aiqt/core/rules/b.md:1:" in o and "rules/a.md:" not in o and "RFC 3339: cited in 1" in o),
    ("V6 an exclusion with an empty reason cannot evaluate", 2, None, {"a.md": "RFC 3339.\n"},
     _EXCL.format("RFC 3339", _A, "  "), lambda o: "reason must be a non-empty string" in o),
    ("V7 a stale exclusion fails", 1, None, {"a.md": "Nothing cited.\n"}, _EXCL.format("RFC 3339", _A, "r"),
     lambda o: "stale exclusion: RFC 3339 no longer occurs in .aiqt/core/rules/a.md" in o),
    ("V8 an exclusion of a registered identity cannot evaluate", 2, None, {"a.md": "ISO/IEC 42001.\n"},
     _EXCL.format("ISO/IEC 42001", _A, "r"), lambda o: "an exclusion contradicts it" in o),
    ("V9 one citation in exactly one document fails", 1, None,
     {"r.md": "See NIST SP 800-999.\n", "s.md": "Nothing.\n"}, None,
     lambda o: "NIST SP 800-999: cited in 1 document(s)" in o),
    ("V10 a citation in a code span or a fence fails", 1, None,
     {"r.md": "Uses `RFC 9999`.\n\n```\nFIPS 999\n```\n"}, None,
     lambda o: "RFC 9999: cited in 1" in o and "FIPS 999: cited in 1" in o),
    ("V11 a corpus file that is not UTF-8 cannot evaluate", 2, None, {"r.md": b"# Rule\n\xff\xfe\n"}, None,
     lambda o: "not valid UTF-8" in o),
    ("V12 a corpus glob matching zero files cannot evaluate", 2, None, None,
     'format-version = 1\ncorpus = ["docs/*.md"]\n', lambda o: "matches no file" in o),
    ("V13 a missing --config file cannot evaluate", 2, None, None,
     Path("/nonexistent-cited-standards-selftest/cited.toml"), lambda o: "cannot be read" in o),
    ("V14 an unknown config key cannot evaluate", 2, None, None, "format-version = 1\nthreshold = 2\n",
     lambda o: "unknown key(s): threshold" in o),
    ("V15 a malformed manifest cannot evaluate", 2,
     {"iso-42001": _manifest_text("iso-42001", _BASE_NAME, kind="bogus")}, None, None,
     lambda o: "kind 'bogus'" in o),
    ("V16 two manifests deriving one identity cannot evaluate", 2, _two_manifests("iso-other", "Other", "ISO 42001"),
     None, None, lambda o: "both derive the identity ISO 42001" in o),
    ("V17 a designator wrapped across a line break fails", 1, None,
     {"r.md": "This follows ISO/IEC\n99999 closely.\n"}, None, lambda o: ".aiqt/core/rules/r.md:1:" in o),
    ("V18 an absolute corpus glob cannot evaluate", 2, None, None, 'format-version = 1\ncorpus = ["/etc/*.md"]\n',
     lambda o: "root-relative" in o),
    ("V19 a corpus glob climbing out of the root cannot evaluate", 2, None, None,
     'format-version = 1\ncorpus = ["../*.md"]\n', lambda o: "no '..'" in o),
    ("V20 a config without format-version cannot evaluate", 2, None, None,
     'corpus = [".aiqt/core/rules/*.md"]\n', lambda o: "format-version must be" in o),
    ("V21 an exclusion path outside the corpus cannot evaluate", 2, None, {"a.md": "RFC 3339.\n"},
     _EXCL.format("RFC 3339", "docs/x.md", "r"), lambda o: "not in the corpus" in o),
    ("V22 an exclusion designator naming two identities cannot evaluate", 2, None, {"a.md": "RFC 3339.\n"},
     _EXCL.format("RFC 3339 and RFC 2119", _A, "r"), lambda o: "does not parse to exactly one identity" in o),
    ("V23 an exclusion covering every citing path passes", 0, None, {"a.md": "RFC 3339.\n"},
     _EXCL.format("rfc3339", _A, "r"), lambda o: o.startswith("PASS") and "1 exclusion(s)" in o),
    ("V24 a config file that is not valid TOML cannot evaluate", 2, None, None, "format-version = = 1\n",
     lambda o: "is not valid TOML" in o),
    ("V25 a dangling symlink matched by the corpus glob cannot evaluate", 2, None,
     {"r.md": "Nothing.\n", "gone.md": None}, None, lambda o: "corpus file .aiqt/core/rules/gone.md cannot be read" in o),
    ("V26 RFC3339Parser never resolves to the registered RFC 3339: it fails as its whole token", 1, _RFC,
     {"r.md": "Use the RFC3339Parser class.\n"}, None, lambda o: 'token "RFC3339Parser": cited in 1' in o),
    ("V27 a URL path ending in RFC3339Parser fails as its whole token", 1, _RFC,
     {"r.md": "See https://example.test/RFC3339Parser for it.\n"}, None,
     lambda o: 'token "RFC3339Parser": cited in 1' in o),
    ("V28 NIST SP 800-53AB never resolves to the registered NIST SP 800-53", 1, _SP53,
     {"r.md": "Follows NIST SP 800-53AB.\n"}, None, lambda o: 'token "NIST SP 800-53AB": cited in 1' in o),
    ("V29 NIST SP 800-53Ar5 never resolves to the registered NIST SP 800-53", 1, _SP53,
     {"r.md": "Controls follow NIST SP 800-53Ar5 procedures.\n"}, None,
     lambda o: 'token "NIST SP 800-53Ar5": cited in 1' in o),
    ("V30 NIST SP 800-63-3 is not the registered NIST SP 800-63", 1,
     _two_manifests("nist-80063", "NIST SP 800-63 x"), {"r.md": "Follows NIST SP 800-63-3.\n"}, None,
     lambda o: "NIST SP 800-63-3: cited in 1" in o),
    ("V31 a manifest name run on into a suffix registers nothing", 1,
     _two_manifests("nist-80053", "NIST SP 800-53Ar5 assessment"),
     {"r.md": "Follows NIST SP 800-53Ar5 and NIST SP 800-53.\n"}, None,
     lambda o: "NIST SP 800-53: cited in 1" in o and 'token "NIST SP 800-53Ar5": cited in 1' in o),
    ("V32 an exclusion naming the whole token waives it", 0, None, {"a.md": "Use RFC3339Parser.\n"},
     _EXCL.format("RFC3339Parser", _A, "a class name"), lambda o: o.startswith("PASS") and "1 exclusion(s)" in o),
    ("V33 a designator wrapped right after ISO/ fails", 1, None, {"r.md": "This follows ISO/\nIEC 99999:2031.\n"},
     None, lambda o: "ISO 99999: cited in 1" in o and ".aiqt/core/rules/r.md:1: ISO/ IEC 99999:2031" in o),
    ("V34 a designator wrapped into a blockquote continuation line fails", 1, None,
     {"r.md": "> This follows ISO/IEC\n> 99999 closely.\n"}, None,
     lambda o: "ISO 99999: cited in 1" in o and ".aiqt/core/rules/r.md:1: ISO/IEC 99999\n" in o),
    ("V35 an underscore is a boundary, so strict_rfc3339 reads RFC 3339 and is reported", 1, None,
     {"r.md": "Call strict_rfc3339 here.\n"}, None, lambda o: "RFC 3339: cited in 1" in o),
    ("V36 a second exclusion of one identity cannot evaluate", 2, None, {"a.md": "RFC 3339.\n"},
     _EXCL.format("RFC 3339", _A, "r") + _EXCL.format("rfc3339", _A, "r").replace("format-version = 1\n", ""),
     lambda o: "already has an exclusion" in o),
    ("V37 an exclusion path whose file no longer cites it is stale although another path still does", 1, None,
     {"a.md": "RFC 3339.\n", "b.md": "Nothing.\n"}, _TWO_PATHS.format(_A, _B),
     lambda o: "0 unresolved" in o and "stale exclusion: RFC 3339 no longer occurs in .aiqt/core/rules/b.md" in o),
    ("V38 a corpus symlink resolving outside the root cannot evaluate", 2, None,
     {"r.md": "Nothing.\n", "out.md": ("outside", "Nothing.\n")}, None, lambda o: "resolves outside the root" in o),
    ("V39 an exclusion listing one path twice cannot evaluate", 2, None, {"a.md": "RFC 3339.\n"},
     _TWO_PATHS.format(_A, _A), lambda o: "non-empty list of distinct strings" in o),
    ("V40 format-version = true cannot evaluate", 2, None, None, "format-version = true\n",
     lambda o: "format-version must be the integer 1" in o),
    ("V41 a corpus directory that cannot be listed cannot evaluate", 2, None, None, None,
     lambda o: "cannot be listed" in o, {"ensure_listable": _unlistable}),
    ("V42 a corpus entry that is not a regular file cannot evaluate", 2, None,
     {"r.md": "Nothing.\n", "s.md": ("socket",)}, None, lambda o: "s.md is not a regular file" in o),
)

# V43 to V46 hold a dash continued across a line break onto a blockquote line: each registers the shorter identity,
# so a truncation passes and the vector fails. V47 and V48 hold an exclusion naming a token exactly as printed, V49
# and V50 that an exclusion naming less than the whole printed text does not waive it, and V51 the regular-file check
# in read_corpus by outcome: without it /dev/null reads as an empty, clean corpus file.
_VECTORS += (
    ("V43 ISO 42001-<break>> 2023 never resolves to the registered ISO 42001", 1, None,
     {"r.md": "> Follows ISO 42001-\n> 2023 here.\n"}, None,
     lambda o: 'token "ISO 42001- 2023": cited in 1' in o and ".aiqt/core/rules/r.md:1: ISO 42001- 2023\n" in o),
    ("V44 NIST SP 800-53-<break>> 1 never resolves to the registered NIST SP 800-53", 1, _SP53,
     {"r.md": "> Follows NIST SP 800-53-\n>  >  1 here.\n"}, None,
     lambda o: 'token "NIST SP 800-53- 1": cited in 1' in o),
    ("V45 NIST SP 800-<break>> 53 never resolves to the registered NIST SP 800", 1,
     _two_manifests("nist-800", "NIST SP 800 x"), {"r.md": "> Per NIST SP 800-\n> 53.\n"}, None,
     lambda o: 'token "NIST SP 800- 53": cited in 1' in o),
    ("V46 RFC 3339-<break>> 1 never resolves to the registered RFC 3339", 1, _RFC,
     {"r.md": "> Per RFC 3339\u2014\n> 1.\n"}, None, lambda o: 'token "RFC 3339- 1": cited in 1' in o),
    ("V47 an exclusion naming a wrapped token as printed waives it", 0, None, {"a.md": "> Per ISO 42001-\n> 2023.\n"},
     _EXCL.format("ISO 42001- 2023", _A, "a wrapped year"), lambda o: o.startswith("PASS") and "1 exclusion(s)" in o),
    ("V48 an exclusion naming a token in its printed case waives it", 0, None, {"a.md": "Use rfc3339parser.\n"},
     _EXCL.format("rfc3339parser", _A, "a class name"), lambda o: o.startswith("PASS") and "1 exclusion(s)" in o),
    ("V49 an exclusion naming SP.800-53r5 does not waive NIST.SP.800-53r5", 1, _SP53,
     {"a.md": "See NIST.SP.800-53r5.\n"}, _EXCL.format("SP.800-53r5", _A, "r"),
     lambda o: 'token "NIST.SP.800-53r5": cited in 1' in o and 'stale exclusion: token "SP.800-53r5"' in o),
    ("V50 an exclusion naming FIPS-140-3x does not waive NIST-FIPS-140-3x", 1, None,
     {"a.md": "See NIST-FIPS-140-3x.\n"}, _EXCL.format("FIPS-140-3x", _A, "r"),
     lambda o: 'token "NIST-FIPS-140-3x": cited in 1' in o and 'stale exclusion: token "FIPS-140-3x"' in o),
    ("V51 a corpus file that is not a regular file cannot evaluate, although it reads as empty", 2, None, None, None,
     lambda o: "r.md is not a regular file" in o, {"expand_corpus": _devnull_corpus}),
)

# V52 onward, one per documented spelling: the plain form is discovered from its first character (the finding prints
# it whole), and its suffixed form is a token that an exclusion naming the whole printed text waives.
_FORMS = (
    ("ISO/IEC 99999", "ISO 99999"), ("ISO-IEC 99999", "ISO 99999"), ("ISO/IEC/IEEE 99999", "ISO 99999"),
    ("ISO 99999", "ISO 99999"), ("ISO99999", "ISO 99999"), ("ISO-99999", "ISO 99999"), ("ISO.99999", "ISO 99999"),
    ("NIST SP 800-999", "NIST SP 800-999"), ("SP 800-999", "NIST SP 800-999"), ("SP800-999", "NIST SP 800-999"),
    ("NIST.SP.800-999", "NIST SP 800-999"), ("NIST-SP-800-999", "NIST SP 800-999"),
    ("NIST 800-999", "NIST SP 800-999"), ("NIST SP 1999", "NIST SP 1999"), ("NIST.SP.1999", "NIST SP 1999"),
    ("NIST AI 999-1", "NIST AI 999-1"), ("NIST.AI.999-1", "NIST AI 999-1"), ("NIST-AI-999-1", "NIST AI 999-1"),
    ("NIST IR 9999", "NIST IR 9999"), ("NISTIR 9999", "NIST IR 9999"), ("NIST.IR.9999", "NIST IR 9999"),
    ("NIST-IR-9999", "NIST IR 9999"), ("FIPS 999-1", "FIPS 999-1"), ("FIPS PUB 999", "FIPS 999"),
    ("NIST FIPS 999-1", "FIPS 999-1"), ("NIST.FIPS.999-1", "FIPS 999-1"), ("NIST-FIPS-999-1", "FIPS 999-1"),
    ("FIPS.PUB.999", "FIPS 999"), ("FIPS-PUB-999", "FIPS 999"), ("RFC 9999", "RFC 9999"), ("RFC9999", "RFC 9999"),
    ("RFC-9999", "RFC 9999"), ("RFC.9999", "RFC 9999"),
    ("NIST Special Publication 800-999", "NIST SP 800-999"),
    ("NIST Special Publication (SP) 800-999", "NIST SP 800-999"), ("Special Publication 800-999", "NIST SP 800-999"),
    ("NIST Special Publication 1999", "NIST SP 1999"), ("FIPS Pub. 999", "FIPS 999"),
)


def _form_vector(number, text, ident):
    return ("V{} {} is discovered whole and {}r9 is excluded as printed".format(number, text, text), 1, None,
            {"a.md": "Cites {} here.\nAlso {}r9 there.\n".format(text, text)}, _EXCL.format(text + "r9", _A, "r"),
            lambda o: ("{}: cited in 1".format(ident) in o and ".aiqt/core/rules/a.md:1: {}\n".format(text) in o
                       and "FAIL: 1 unresolved standard identity(ies), 0 stale" in o))


_VECTORS += tuple(_form_vector(52 + i, text, ident) for i, (text, ident) in enumerate(_FORMS))

# V90 to V94 hold a dash that ends the designator's line or is followed by markup or same-line whitespace and a digit:
# each registers the shorter identity, so reading it short passes and the vector fails. V95 holds that such a token is
# excludable as printed, V96 and V97 a dash or dot join that ends a line, and V98 the strip of an exclusion designator.
_VECTORS += (
    ("V90 a dash that ends a line before a # comment line never resolves to the registered ISO 42001", 1, None,
     {"r.md": "```yaml\n# Follows ISO 42001-\n# 2023 here\n```\n"}, None,
     lambda o: 'token "ISO 42001-": cited in 1' in o and ".aiqt/core/rules/r.md:2: ISO 42001-\n" in o),
    ("V91 a dash that ends a line before a // comment line never resolves to the registered NIST SP 800-53", 1, _SP53,
     {"r.md": "```c\n// Per NIST SP 800-53-\n// 1 here\n```\n"}, None,
     lambda o: 'token "NIST SP 800-53-": cited in 1' in o),
    ("V92 a dash that ends a line before a * comment line never resolves to the registered RFC 3339", 1, _RFC,
     {"r.md": "/*\n * Per RFC 3339\u2013\n * 1 here\n */\n"}, None, lambda o: 'token "RFC 3339-": cited in 1' in o),
    ("V93 a dash followed by markup never resolves to the registered ISO 42001", 1, None,
     {"r.md": "Per ISO 42001-<br>2023 and ISO 42001-**2023**.\n"}, None,
     lambda o: 'token "ISO 42001-": cited in 1' in o and "FAIL: 1 unresolved" in o),
    ("V94 a dash followed by spaces and a digit never resolves to the registered ISO 42001", 1, None,
     {"r.md": "Per ISO 42001-  2023 here.\n"}, None, lambda o: 'token "ISO 42001- 2023": cited in 1' in o),
    ("V95 an exclusion naming a token that ends at a dash, as printed, waives it", 0, None,
     {"a.md": "# Per ISO 42001-\n# 2023\n"}, _EXCL.format("ISO 42001-", _A, "a wrapped year"),
     lambda o: o.startswith("PASS") and "1 exclusion(s)" in o),
    ("V96 a dash or dot join that ends a line is read whole", 1, None,
     {"r.md": "Per ISO-\n99999, RFC-\n> 9999, ISO-\nIEC 99998 and NIST.AI.\n600-1 here.\n"}, None,
     lambda o: all(s in o for s in ("ISO 99999: cited in 1", "RFC 9999: cited in 1", "ISO 99998: cited in 1",
                                    "NIST AI 600-1: cited in 1", "r.md:1: ISO- 99999\n", "r.md:2: RFC- 9999\n",
                                    "r.md:3: ISO- IEC 99998\n", "r.md:4: NIST.AI. 600-1\n"))),
    ("V97 an exclusion naming SP-800-53r5 does not waive NIST-<break>SP-800-53r5", 1, _SP53,
     {"a.md": "See NIST-\nSP-800-53r5.\n"}, _EXCL.format("SP-800-53r5", _A, "r"),
     lambda o: 'token "NIST- SP-800-53r5": cited in 1' in o and 'stale exclusion: token "SP-800-53r5"' in o),
    ("V98 an exclusion designator is read with surrounding whitespace stripped", 0, None, {"a.md": "RFC 3339.\n"},
     _EXCL.format("  RFC 3339  ", _A, "r"), lambda o: o.startswith("PASS") and "1 exclusion(s)" in o),
)

# V99 and V100 hold a dash before same-line whitespace (spaces, a tab, the CR of a CRLF) and then an LF: each registers
# the shorter identity, so reading that whitespace and the LF as whitespace before a non-digit passes V99 and leaves
# the exclusion in V100 stale. V101 and V102 hold the line break after Special and after Publication, each with a
# printed-token exclusion round trip: a gap that cannot hold a line break discovers nothing there. V103 pins the
# documented collapse of a token that ends at a dash: one exclusion waives each citation printed as that token.
_DASH_LF = "Follows ISO 42001- \n// 2023\nPer ISO 42001-\r\n# 2023\r\n```yaml\n# Per ISO 42001-  \t\n# 2023\n```\n"
_VECTORS += (
    ("V99 a dash before trailing whitespace and an LF never resolves to the registered ISO 42001", 1, None,
     {"r.md": _DASH_LF}, None,
     lambda o: all(s in o for s in ('token "ISO 42001-": cited in 1', "FAIL: 1 unresolved", "r.md:1: ISO 42001-\n",
                                    "r.md:3: ISO 42001-\n", "r.md:6: ISO 42001-\n"))),
    ("V100 an exclusion naming a token that ends at a dash before trailing whitespace waives it as printed", 0, None,
     {"a.md": _DASH_LF}, _EXCL.format("ISO 42001-", _A, "a wrapped year"),
     lambda o: o.startswith("PASS") and "1 exclusion(s)" in o),
    ("V101 NIST Special<break>Publication 800-999 is discovered whole and its r9 token is excluded as printed", 1,
     None, {"a.md": "Cites NIST Special\nPublication 800-999 here.\nAlso NIST Special\n> Publication 800-999r9.\n"},
     _EXCL.format("NIST Special Publication 800-999r9", _A, "r"),
     lambda o: ("NIST SP 800-999: cited in 1" in o and ".aiqt/core/rules/a.md:1: NIST Special Publication 800-999\n"
                in o and "FAIL: 1 unresolved standard identity(ies), 0 stale" in o)),
    ("V102 NIST Special Publication<break>(SP) 800-999 is discovered whole and its r9 token is excluded as printed",
     1, None,
     {"a.md": "Cites NIST Special Publication\n(SP) 800-999 here.\nAlso Special Publication\n> (SP) 800-999r9.\n"},
     _EXCL.format("Special Publication (SP) 800-999r9", _A, "r"),
     lambda o: ("NIST SP 800-999: cited in 1" in o
                and ".aiqt/core/rules/a.md:1: NIST Special Publication (SP) 800-999\n" in o
                and "FAIL: 1 unresolved standard identity(ies), 0 stale" in o)),
    ("V103 one exclusion of a token that ends at a dash waives each citation printed as that token", 0, None,
     {"a.md": "# Per ISO 99999-\n# 2023\n\nLater: ISO 99999-<br>1 and ISO 99999-**2031**.\n"},
     _EXCL.format("ISO 99999-", _A, "a wrapped year"), lambda o: o.startswith("PASS") and "1 exclusion(s)" in o),
)

# V104 to V106 hold a corpus wider than the rule files, as this repository's .aiqt/cited-standards.toml configures.
# V104 expects a designator in a prompt-pack member to fail the gate; run with the built-in default, which never reads
# the prompt pack, the gate passes and V104 fails. V105 pins that default, and V106 that an exclusion may name a
# member. Each has no patch (None) and, as an eighth element, the files written outside the rules directory.
# They carry their own corpus and read neither .aiqt/cited-standards.toml nor the runners that pass it; the
# repository-only .github/check_cited_standards_contract.py pins that config and wiring.
_PACK = {"opf/prompt-pack/flow/SKILL.md": "# Flow\n\nFollow ISO/IEC 99999:2031 here.\n"}
_WIDE = 'format-version = 1\ncorpus = [".aiqt/core/rules/*.md", "opf/prompt-pack/**/*.md"]\n'
_VECTORS += (
    ("V104 a designator in an OPF prompt-pack member fails under a config that adds the prompt pack", 1, None, None,
     _WIDE, lambda o: ("ISO 99999: cited in 1 document(s)" in o and "FAIL: 1 unresolved" in o
                       and "opf/prompt-pack/flow/SKILL.md:3: ISO/IEC 99999:2031\n" in o), None, _PACK),
    ("V105 the built-in default reads the rule files alone, so the same prompt-pack member is not seen", 0, None,
     None, None, lambda o: o.startswith("PASS: 1 corpus document(s) checked; 0 cited"), None, _PACK),
    ("V106 an exclusion may name a prompt-pack member", 0, None, None,
     _WIDE + _EXCL.format("ISO/IEC 99999", "opf/prompt-pack/flow/SKILL.md", "r").replace("format-version = 1\n", ""),
     lambda o: o.startswith("PASS: 2 corpus document(s)") and "1 exclusion(s)" in o, None, _PACK),
)

_NORMALISATION = (
    ("ISO/IEC 42001:2023", "ISO 42001"),
    ("iso 42001", "ISO 42001"),
    ("ISO/IEC/IEEE 29148:2018", "ISO 29148"),
    ("ISO 26262-6:2018", "ISO 26262-6"),
    ("NIST SP 800-53", "NIST SP 800-53"),
    ("SP 800-218", "NIST SP 800-218"),
    ("NIST SP 800\u201353", "NIST SP 800-53"),
    ("NIST SP 800-53A", "NIST SP 800-53A"),
    ("NIST AI 100-1", "NIST AI 100-1"),
    ("NISTIR 8259A", "NIST IR 8259A"),
    ("NIST IR 8286", "NIST IR 8286"),
    ("FIPS 140-3", "FIPS 140-3"),
    ("FIPS PUB 197", "FIPS 197"),
    ("RFC 3339", "RFC 3339"),
    ("RFC3339", "RFC 3339"),
    ("ISO/\nIEC 42001", "ISO 42001"),
    ("ISO /\n  IEC\n> 42001:\n2023", "ISO 42001"),
    ("ISO27001", "ISO 27001"),
    ("ISO-27001", "ISO 27001"),
    ("ISO/IEC TR 24028:2020", "ISO 24028"),
    ("ISO/TS 15066", "ISO 15066"),
    ("ISO/IEC 42001-2023", "ISO 42001-2023"),
    ("SP800-53", "NIST SP 800-53"),
    ("NIST 800-53", "NIST SP 800-53"),
    ("NIST SP 1270", "NIST SP 1270"),
    ("NIST SP 800-63-3", "NIST SP 800-63-3"),
    ("NIST SP 800-63B-4", "NIST SP 800-63B-4"),
    ("NIST SP 800\u201153", "NIST SP 800-53"),
    ("NIST SP 800\u221253", "NIST SP 800-53"),
    ("NISTIR8259", "NIST IR 8259"),
    ("FIPS-140-2", "FIPS 140-2"),
    ("RFC-3339", "RFC 3339"),
    ("rfc3339", "RFC 3339"),
    ("RFC3339Parser", 'token "RFC3339Parser"'),
    ("NIST SP 800-53AB", 'token "NIST SP 800-53AB"'),
    ("NIST SP 800-53Ar5", 'token "NIST SP 800-53Ar5"'),
    ("NIST SP 800-53r5", 'token "NIST SP 800-53r5"'),
    ("NIST SP 800-63-3-1", 'token "NIST SP 800-63-3-1"'),
    ("ISO 27001/27002", 'token "ISO 27001/27002"'),
    ("ISO 42001:20234", 'token "ISO 42001:20234"'),
    ("FIPS 140-2a", 'token "FIPS 140-2a"'),
    ("NIST AI 100-1x", 'token "NIST AI 100-1x"'),
    ("NIST IR 8259AB", 'token "NIST IR 8259AB"'),
    ("ISO 42001x", 'token "ISO 42001x"'),
    ("NIST SP 800-\n53", 'token "NIST SP 800- 53"'),
    ("ISO 26262\u2013\n  6", 'token "ISO 26262- 6"'),
    # the dot separator, the NIST and FIPS joins, and the ISO joins
    ("RFC.3339", "RFC 3339"),
    ("NIST.SP.800-53", "NIST SP 800-53"),
    ("NIST-SP-800-53", "NIST SP 800-53"),
    ("NIST.SP.1270", "NIST SP 1270"),
    ("NIST.AI.600-1", "NIST AI 600-1"),
    ("NIST-AI-100-1", "NIST AI 100-1"),
    ("NIST.IR.8259", "NIST IR 8259"),
    ("NIST-IR-8259", "NIST IR 8259"),
    ("NIST.FIPS.140-3", "FIPS 140-3"),
    ("NIST FIPS 140-3", "FIPS 140-3"),
    ("FIPS.PUB.140-2", "FIPS 140-2"),
    ("FIPS-PUB-199", "FIPS 199"),
    ("ISO-IEC 27001", "ISO 27001"),
    ("ISO IEC 27001", "ISO 27001"),
    # each ISO type and stage code (TR and TS above)
    ("ISO/PAS 5112", "ISO 5112"),
    ("ISO/IEC AWI 42005", "ISO 42005"),
    ("ISO/IEC WD 27090", "ISO 27090"),
    ("ISO/IEC CD 42006", "ISO 42006"),
    ("ISO/IEC DIS 42005", "ISO 42005"),
    ("ISO/FDIS 42005", "ISO 42005"),
    ("ISO/IEC DTR 5469", "ISO 5469"),
    ("ISO/IEC DTS 22440", "ISO 22440"),
    ("ISO/IEC CD TR 5469", "ISO 5469"),
    # each dash (U+002D, U+2011, U+2013 and U+2212 above)
    ("NIST SP 800\u201053", "NIST SP 800-53"),
    ("NIST SP 800\u201253", "NIST SP 800-53"),
    ("NIST SP 800\u201453", "NIST SP 800-53"),
    ("NIST SP 800\u201553", "NIST SP 800-53"),
    ("NIST SP 800\ufe5853", "NIST SP 800-53"),
    ("NIST SP 800\ufe6353", "NIST SP 800-53"),
    ("NIST SP 800\uff0d53", "NIST SP 800-53"),
    ("NIST SP 800\ufe5853r5", 'token "NIST SP 800-53r5"'),
    ("NIST SP 800\ufe6353r5", 'token "NIST SP 800-53r5"'),
    ("NIST SP 800\uff0d53r5", 'token "NIST SP 800-53r5"'),
    # a token, and a token as a finding prints it
    ("NIST.SP.800-53r5", 'token "NIST.SP.800-53r5"'),
    ("ISO 42001-\n> 2023", 'token "ISO 42001- 2023"'),
    ("NIST SP 800-53-\n>  >  1", 'token "NIST SP 800-53- 1"'),
    ("ISO 42001- 2023", 'token "ISO 42001- 2023"'),
    ("rfc3339parser", 'token "rfc3339parser"'),
    # a dash that ends the text, or a dash followed by spaces and a digit, joins the token
    ("ISO 42001-", 'token "ISO 42001-"'),
    ("ISO 42001-  2023", 'token "ISO 42001- 2023"'),
    ("ISO 14064- 1", 'token "ISO 14064- 1"'),
    # FIPS Pub., Special Publication, and a dash or dot join that ends a line
    ("FIPS Pub. 140-2", "FIPS 140-2"),
    ("NIST Special Publication 800-53", "NIST SP 800-53"),
    ("NIST Special Publication (SP) 800-63B", "NIST SP 800-63B"),
    ("Special Publication 800-30", "NIST SP 800-30"),
    ("NIST Special Publication 1270", "NIST SP 1270"),
    ("ISO-\n27001", "ISO 27001"),
    ("RFC-\n3339", "RFC 3339"),
    ("NIST.AI.\n600-1", "NIST AI 600-1"),
    ("NIST.SP. \n800-208", "NIST SP 800-208"),
    ("ISO-\n> IEC 27001", "ISO 27001"),
    ("NIST-\nSP-800-53r5", 'token "NIST- SP-800-53r5"'),
)

# (text, [identities designators() finds]): an identity ends at a token boundary, a hyphen before a letter ends
# it, and a look-alike is reported rather than ignored.
_IN_TEXT = (
    ("Timestamps are RFC 3339-compliant.", ["RFC 3339"]),
    ("See https://www.rfc-editor.org/rfc/rfc3339 for it.", ["RFC 3339"]),
    ("strict_rfc3339 and format_rfc3339()", ["RFC 3339", "RFC 3339"]),
    ("see sp 3-4 tokens", ["NIST SP 3-4"]),
    ("map-iso-42001-broad: [6.1]", ["ISO 42001"]),
    ("Use RFC3339Parser, then RFC 3339.", ['token "RFC3339Parser"', "RFC 3339"]),
    ("Read NIST_SP800-171_rev3 here.", ["NIST SP 800-171"]),
    ("Per RFC 3339.\n2. Next item", ["RFC 3339"]),
    ("See 10.6028/NIST.AI.600-1.", ["NIST AI 600-1"]),
    # a dash run before a letter, or before same-line whitespace and a non-digit, ends the designator
    ("See NIST-AI-100-1--AI-RMF.md and ISO-IEC-27001-2022--full-text.md", ["NIST AI 100-1", "ISO 27001-2022"]),
    ("Per ISO 27001- and 27002-certified.", ["ISO 27001"]),
    ("Per RFC 3339---see below.", ["RFC 3339"]),
    # named in RESIDUAL: read from a later word, read short, and look-alikes reported
    ("NIST/SP 800-53 and NIST - SP 800-53", ["NIST SP 800-53", "NIST SP 800-53"]),
    ("Per ISO 42001 - 2023 and ISO 42001&ndash;2023.", ["ISO 42001", "ISO 42001"]),
    ("Per ISO 27001/\n27002.", ["ISO 27001"]),
    ("Per ISO 42001- **2023**, ISO 42001- (2023) and ISO 42001- <sup>2023</sup>.", ["ISO 42001"] * 3),
    ("ISO 42001-\r# 1, ISO 42001-\x0b# 1, ISO 42001-\x0c# 1, ISO 42001-\x85# 1, ISO 42001-\u2028# 1, "
     "ISO 42001-\u2029# 1", ["ISO 42001"] * 6),
    ("Per ISO 27001/ 27002.", ["ISO 27001"]),
    # a token that ends at a dash keeps nothing after it
    ("Per ISO 99999-\n# 2023, ISO 99999-<br>1 and ISO 99999-**2031**.", ['token "ISO 99999-"'] * 3),
    ("Timestamps are RFC 3339-\ncompliant.", ['token "RFC 3339-"']),
    ("as in the initial RFC.\n2. Next item", ["RFC 2"]),
)

_NOT_DESIGNATORS = ("ISO", "ISO/IEC", "SP 800", "NIST", "XRFC 3339", "OWASP Top 10", "CWE-79", "LLM01",
                    "AI 100-1", "ISOLATION 42", "fips", "SP 1270", "NIST 1270", "nist-80053-tight", "rfc-editor",
                    "ISO/IEC\n\n42001", "SP 800-\n53", "NIST AI 100-\n1", "*ISO/IEC* 42001", "ISO/IEC&nbsp;42001",
                    "9RFC 3339", "\u00e9RFC 3339", "NIST 2023-10-01", "NIST.CSWP.29", "NIST_SP_800-53",
                    "ISO Guide 73", "ISO/SAE 21434", "ISO/IEC TRansparency 42001",
                    # the ISO prefixes RESIDUAL names as not read, and a line opened by a comment marker
                    "ISO/DPAS 45005", "ISO/IEC PWI 9999", "ISO/IEC CDV 62443", "ISO/IEC PDTR 24028", "ISO/IEC PDTS 1",
                    "ISO/TTA 5", "ISO/IEC NP 42006", "ISO/IEC PRF 42005", "IWA 14", "ISO IWA 42", "ISO/IEC\n# 42001",
                    "ISO-\n// 27001")

# The roster's numbered manifests, field for field, and the identities they must derive (and no others).
_ROSTER = (
    ("ISO/IEC 23894:2023 AI guidance on risk management", "2023"),
    ("ISO/IEC 42001:2023 AI management system", "2023"),
    ("NIST SP 800-53 Security and Privacy Controls", "Rev 5 (catalog 5.2.0)"),
    ("NIST AI Risk Management Framework", "1.0 (NIST AI 100-1)"),
    ("NIST Secure Software Development Framework", "1.1 (SP 800-218)"),
    ("OWASP Top 10 (Web Application Security Risks)", "2025"),
)
_ROSTER_IDENTITIES = ["ISO 23894", "ISO 42001", "NIST AI 100-1", "NIST SP 800-218", "NIST SP 800-53"]


def self_test_main():
    failures = []
    for text, expect in _NORMALISATION:
        got = parse_designator(text)
        if got != expect:
            failures.append("normalise {!r}: expected {!r}, got {!r}".format(text, expect, got))
    for text, expect in _NORMALISATION:
        if expect.startswith(_TOKEN) and parse_designator(expect[len(_TOKEN):-1]) != expect:
            failures.append("token {} printed: exclusion reads {!r}".format(
                expect, parse_designator(expect[len(_TOKEN):-1])))
    for text, expect in _IN_TEXT:
        got = [ident for ident, _l, _t in designators(text)]
        if got != expect:
            failures.append("in {!r}: expected {}, got {}".format(text, expect, got))
    for text in _NOT_DESIGNATORS:
        if designators(text):
            failures.append("{!r} must not read as a designator: {}".format(text, designators(text)))
    derived = sorted(ident for fields in _ROSTER for field in fields for ident, _l, _t in designators(field))
    if derived != _ROSTER_IDENTITIES:
        failures.append("manifest-field identities: expected {}, got {}".format(_ROSTER_IDENTITIES, derived))
    for label, expect, manifests, rules, config, predicate, *extra in _VECTORS:
        code, out = _drive(manifests, rules, config, extra[0] if extra else None, extra[1] if len(extra) > 1 else None)
        if code != expect or not predicate(out):
            failures.append("{}: expected exit {}, got {}; output:\n{}".format(label, expect, code, out))
    # _drive patch contract: an unknown patch name is rejected before anything is applied (installed, it
    # would leave the intended fault uninjected and contaminate every later vector), and every patched
    # name is restored after a run that returns non-zero, after one that returns 0 (the injected run must
    # have run, and the restored binding must be the saved original by identity) and after one that raises.
    try:
        _drive(patch={"QA_UNKNOWN_PATCH": object()})
        failures.append("_drive: an unknown patch name expected ValueError, got a completed run")
    except ValueError as exc:
        if "QA_UNKNOWN_PATCH" not in str(exc):
            failures.append("_drive: the unknown-name refusal must name the key, got {!r}".format(str(exc)))
    if "QA_UNKNOWN_PATCH" in globals():
        failures.append("_drive: a rejected patch name must not be installed in module globals")
    code, out = _drive(patch={"ensure_listable": _unlistable})
    if code != 2 or "cannot be listed" not in out:
        failures.append("_drive: a patched run that returns expected exit 2 naming the injected fault, got "
                        "exit {}; output:\n{}".format(code, out))
    if ensure_listable is _unlistable:
        failures.append("_drive: after a patched run that returns, the patch must not stay installed")
    saved_run = run
    succeeded = []

    def _succeeding_run(root, _config):
        succeeded.append(root)
        return 0

    code, out = _drive(patch={"run": _succeeding_run})
    if code != 0 or not succeeded:
        failures.append("_drive: a patched run that returns 0 expected exit 0 from the injected run, got exit "
                        "{} with the injected run called {} time(s); output:\n{}".format(code, len(succeeded), out))
    if run is not saved_run:
        failures.append("_drive: after a patched run that returns 0, run must be the saved original")

    def _raising_run(_root, _config):
        raise RuntimeError("injected by the self-test")

    try:
        _drive(patch={"run": _raising_run})
        failures.append("_drive: an injected run exception expected to propagate, got a completed run")
    except RuntimeError:
        pass
    if run is not saved_run:
        failures.append("_drive: after a patched run that raises, run must be the original")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = main(["--root"])
    if code != 2 or RESIDUAL[0] not in buf.getvalue():
        failures.append("a usage error: expected exit 2 and the residual, got {}; output:\n{}".format(
            code, buf.getvalue()))
    if failures:
        print("SELF-TEST FAIL: {} case(s)".format(len(failures)))
        for line in failures:
            print("  " + line)
        for line in RESIDUAL:
            print(line)
        return 1
    print("SELF-TEST PASS: {} normalisation case(s), {} in-text case(s), {} non-designator case(s), the roster's "
          "manifest-field identities, {} fixture vector(s), the _drive patch contract (an unknown name is "
          "rejected; a patched name is restored after a non-zero return, a zero return and a raise) and the "
          "usage error: an "
          "unresolved designator fails, a "
          "manifest resolves it, a suffixed token never resolves to a shorter identity, an exclusion is per path, "
          "needs a reason and goes stale, and a contradicted or malformed input fails closed".format(
              len(_NORMALISATION), len(_IN_TEXT), len(_NOT_DESIGNATORS), len(_VECTORS)))
    for line in RESIDUAL:
        print(line)
    return 0


def main(argv):
    if argv == ["--self-test"]:
        return self_test_main()
    opts = {}
    i = 0
    while i < len(argv):
        flag = argv[i]
        if flag not in ("--root", "--config") or flag in opts or i + 1 >= len(argv):
            print("usage: check_cited_standards.py [--root DIR] [--config PATH] | --self-test", file=sys.stderr)
            for line in RESIDUAL:
                print(line)
            return 2
        opts[flag] = Path(argv[i + 1])
        i += 2
    return run(opts.get("--root") or repo_root(), opts.get("--config"))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
