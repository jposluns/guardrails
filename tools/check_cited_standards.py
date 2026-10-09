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

Grammar (case-insensitive; a designator may wrap across one line break; an en dash reads as a hyphen):
  ISO[/IEC][/IEEE] n[-part][:yyyy]        identity ISO n[-part]           (organisation and year dropped)
  [NIST] SP n-n[letter]                   identity NIST SP n-n[LETTER]    (SP 800-53A is not SP 800-53)
  NIST AI n-n                             identity NIST AI n-n
  NIST IR n[-n][letter], NISTIR n         identity NIST IR n[-n][LETTER]
  FIPS [PUB] n[-n]                        identity FIPS n[-n]             (FIPS 140-2 is not FIPS 140-3)
  RFC n                                   identity RFC n

The corpus is scanned as raw text, code spans and fences included, so backticks are not a bypass. The
threshold is fixed at one document: a single citation in a single file fails. It is not configurable.

Residual, printed on every run: only numbered designators are discovered. A framework named without a number
(an OWASP, MITRE or CSA title, "AI RMF", "Google SAIF", "OWASP SCVS") is not seen; prose control ids
(CWE-79, LLM01) are not checked; a prose edition is not compared with the manifest `edition`; designators of
other bodies (IEC alone, IEEE alone, ANSI, ETSI, CIS) are outside the grammar.

  check_cited_standards.py                  check the repository's rule corpus
  check_cited_standards.py --root DIR       check the tree at DIR (manifests from DIR/.aiqt/standards)
  check_cited_standards.py --config PATH    read the corpus globs and exclusions from a TOML file
  check_cited_standards.py --self-test      run the fixture vectors

Without --config the built-in default applies: corpus [".aiqt/core/rules/*.md"] and no exclusions; no
default file is read. A config file carries:
  format-version = 1                        required
  corpus = ["<root-relative glob>", ...]    optional; each glob must match at least one file
  [[exclusion]]                             optional, repeatable
  designator = "RFC 3339"                   must parse, in full, to exactly one identity
  paths = ["<corpus file>", ...]            non-empty; each must be a file of the corpus
  reason = "..."                            non-empty
An exclusion waives only its listed paths, so a later citation elsewhere still fails, and it goes stale (a
finding) when its identity no longer occurs in one of those paths.

Exit: 0 clean; 1 an unresolved identity or a stale exclusion; 2 cannot evaluate (a malformed or unreadable
manifest, config or corpus file, a corpus glob that matches nothing, an unlistable directory, two manifests
deriving one identity, or an exclusion that a manifest contradicts). Fail-closed: never a false clean.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: check_cited_standards.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import contextlib
import io
import os
import re
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
    "Residual: only numbered designators are discovered (ISO, ISO/IEC, NIST SP, NIST AI, NIST IR, FIPS, RFC).",
    "  A framework named without a number (an OWASP, MITRE or CSA title, AI RMF, Google SAIF, OWASP SCVS) "
    "is not seen.",
    "  Prose control ids (CWE-79, LLM01) are not checked.",
    "  A prose edition is not compared with the manifest edition (ISO/IEC 42001:2015 resolves to ISO 42001).",
    "  Code spans and fences are scanned, not stripped, so backticks are not a bypass.",
)

# Whitespace with at most one line break: SEP needs at least one character, OPT may be empty.
_SEP = r"(?:[^\S\n]+|[^\S\n]*\n[^\S\n]*)"
_OPT = r"[^\S\n]*\n?[^\S\n]*"
_DASH = "[-\u2013]"
_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"(?P<iso>ISO(?:[^\S\n]*/[^\S\n]*(?:IEC|IEEE))*" + _SEP + r"(?P<iso_n>\d+)"
    r"(?:" + _DASH + r"(?P<iso_part>\d+))?(?!\d)(?::[^\S\n]*\d{4}(?!\d))?)"
    r"|(?P<ai>NIST" + _SEP + r"AI" + _SEP + r"(?P<ai_a>\d+)" + _DASH + r"(?P<ai_b>\d+)(?!\d))"
    r"|(?P<sp>(?:NIST" + _SEP + r")?SP" + _SEP + r"(?P<sp_a>\d+)" + _DASH + r"(?P<sp_b>\d+)"
    r"(?:(?P<sp_x>[A-Za-z])(?![A-Za-z0-9]))?(?!\d))"
    r"|(?P<ir>NIST" + _OPT + r"IR" + _SEP + r"(?P<ir_a>\d+)(?:" + _DASH + r"(?P<ir_b>\d+))?"
    r"(?:(?P<ir_x>[A-Za-z])(?![A-Za-z0-9]))?(?!\d))"
    r"|(?P<fips>FIPS(?:" + _SEP + r"PUB)?" + _SEP + r"(?P<fips_a>\d+)(?:" + _DASH + r"(?P<fips_b>\d+))?(?!\d))"
    r"|(?P<rfc>RFC" + _OPT + r"(?P<rfc_n>\d+)(?!\d))"
    r")",
    re.IGNORECASE)


class CannotEvaluate(Exception):
    """An input the gate cannot read or trust: exit 2, never a clean pass."""


def _num(text):
    return str(int(text))


def _identity(m):
    """The canonical identity of one designator match."""
    if m.group("iso"):
        part = "-" + _num(m.group("iso_part")) if m.group("iso_part") else ""
        return "ISO " + _num(m.group("iso_n")) + part
    if m.group("ai"):
        return "NIST AI {}-{}".format(_num(m.group("ai_a")), _num(m.group("ai_b")))
    if m.group("sp"):
        return "NIST SP {}-{}{}".format(_num(m.group("sp_a")), _num(m.group("sp_b")),
                                        (m.group("sp_x") or "").upper())
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
        out.append((_identity(m), line, " ".join(m.group(0).split())))
    return out


def parse_designator(text):
    """The single identity a whole designator string names, or None if it is not exactly one designator."""
    m = _PATTERN.fullmatch(text.strip())
    return _identity(m) if m else None


def manifest_identities(manifests):
    """{identity: manifest file name}, read from each manifest's own name and edition fields with the corpus
    grammar. Two manifests deriving one identity is a contradiction the gate cannot resolve: exit 2."""
    owner = {}
    for key in sorted(manifests):
        man = manifests[key]
        for field in (man.name, man.edition):
            for ident, _line, _text in designators(field):
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
    except (tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
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
            if dir_present(base):
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
            manifests = load_manifests(root / STANDARDS_REL)
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
            print("  {}: cited in {} document(s); no manifest registers it and no exclusion covers it".format(
                ident, len({rel for rel, _l, _t in sites})))
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
_EXCL = 'format-version = 1\n[[exclusion]]\ndesignator = "{}"\npaths = ["{}"]\nreason = "{}"\n'


def _manifest_text(stem, name, edition="2023", kind="control"):
    return ('map-key = "map-{}"\nname = "{}"\npublisher = "p"\nedition = "{}"\nkind = "{}"\nstatus = "stable"\n'
            'catalogue = "subset"\ncitation-unit = "clause"\nid-pattern = "^[0-9]+$"\nsource-artefact = "s"\n'
            'retrieved = "2026-01-01"\n\n[[id]]\ncode = "1"\n').format(stem, name, edition, kind)


def _fixture(tmp, manifests=None, rules=None, config=None):
    """A temp root with a .git marker, the given manifests ({stem: toml text}) and rule files ({name: bytes or
    str}). config is TOML text written beside the root, or used as the literal path when it is a Path."""
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
        (rules_dir / name).write_bytes(body.encode("utf-8") if isinstance(body, str) else body)
    cfg = None
    if isinstance(config, Path):
        cfg = config
    elif config is not None:
        cfg = Path(tmp) / "cited.toml"
        cfg.write_text(config, encoding="utf-8")
    return root, cfg


def _drive(manifests=None, rules=None, config=None):
    with tempfile.TemporaryDirectory(prefix="aiqt-cited-standards-selftest-") as tmp:
        root, cfg = _fixture(tmp, manifests, rules, config)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = run(root, cfg)
        return code, buf.getvalue()


def _two_manifests(stem, name, edition="2023"):
    return {"iso-42001": _manifest_text("iso-42001", _BASE_NAME), stem: _manifest_text(stem, name, edition)}


# (label, expected exit, manifests, rules, config, a predicate the combined output must satisfy). V1 to V16 each
# fail under one named weakening of the gate (register-driven discovery, ignored manifests, a dropped /IEC or year
# normalisation, an edition check, a global exclusion, no reason check, no stale check, an allowed contradiction,
# a threshold of 2, stripped code, a skipped undecodable file, an empty corpus read as clean, a missing config
# falling back to the default, ignored unknown keys, a swallowed manifest error, last writer wins); the rest
# widen the fail-closed coverage.
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
)

_NOT_DESIGNATORS = ("ISO", "ISO/IEC", "SP 800", "NIST", "XRFC 3339", "OWASP Top 10", "CWE-79", "LLM01",
                    "AI 100-1", "ISOLATION 42", "fips")

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
    for text in _NOT_DESIGNATORS:
        if designators(text):
            failures.append("{!r} must not read as a designator: {}".format(text, designators(text)))
    derived = sorted(ident for fields in _ROSTER for field in fields for ident, _l, _t in designators(field))
    if derived != _ROSTER_IDENTITIES:
        failures.append("manifest-field identities: expected {}, got {}".format(_ROSTER_IDENTITIES, derived))
    for label, expect, manifests, rules, config, predicate in _VECTORS:
        code, out = _drive(manifests, rules, config)
        if code != expect or not predicate(out):
            failures.append("{}: expected exit {}, got {}; output:\n{}".format(label, expect, code, out))
    if failures:
        print("SELF-TEST FAIL: {} case(s)".format(len(failures)))
        for line in failures:
            print("  " + line)
        return 1
    print("SELF-TEST PASS: {} normalisation case(s), {} non-designator case(s), the roster's manifest-field "
          "identities, and {} fixture vector(s): an unresolved designator fails, a manifest resolves it, an "
          "exclusion is per path, needs a reason and goes stale, and a contradicted or malformed input fails "
          "closed".format(len(_NORMALISATION), len(_NOT_DESIGNATORS), len(_VECTORS)))
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
            return 2
        opts[flag] = Path(argv[i + 1])
        i += 2
    return run(opts.get("--root") or repo_root(), opts.get("--config"))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
