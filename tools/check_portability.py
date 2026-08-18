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
     allow-listed attribution locations (the manifest [plugin] block and the generated plugin.json).
  C2 repo-operational vocabulary: the terms are public (they appear in the repo CLAUDE.md), so obscurity is
     the wrong tool; a small in-gate plaintext list is matched with the SAME n-gram normalization the leak
     gate uses (imported _tokens/ngram_forms), so spaced/hyphenated/camelCase variants all match one term.
  C3 unscannable content: check_leaks silently skips binaries and non-UTF-8; in the shippable surface that
     is a fail-open, so here every file outside the text-suffix set (or failing UTF-8 decode) must be on the
     binary allow-list or it is a finding.
  C4 surface existence and walkability: check_leaks has no concept of a required surface; here every scan
     root must exist and walk cleanly (an absent or unlistable root is fail-closed, never "nothing to scan").
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
  check_portability.py --self-test   deterministic self-test (pure cases always run; tempdir cases skipped
                                      with a printed note where the environment cannot produce them)

Exit convention (matches the repo's gates):
  0  clean
  1  a real finding (operator identity, operational vocabulary, an exemption marker, or an unscannable file)
  2  an unreadable identity source, an absent or unwalkable required root, or a read error (fail-closed).
     A text file that does not decode as UTF-8 is the ONE read outcome that is a C3 finding (exit 1), not a
     fail-closed exit 2, since an unscannable shipped file is itself the thing this gate is asserting against.
"""
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: check_portability.py requires Python 3.11+ (tomllib).")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  the shared fail-closed tree walk (os.walk, not rglob)
from check_leaks import _tokens, ngram_forms  # noqa: E402  reuse the leak gate's n-gram normalization


class GateError(Exception):
    """An input the gate cannot read, resolve, or walk. Caught at run() and reported as exit 2
    (fail-closed): an unreadable identity source or an absent/unwalkable root is never treated as clean."""


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

# The identity is legitimate ONLY here: the manifest [plugin] attribution block, and the plugin.json the
# hooks generator renders from it. Both are the identity's single source and its faithful render; an
# adopter keeps the original attribution (CC BY-SA), so these ship the identity by design.
IDENTITY_ALLOW = {
    ".aiqt/core/hooks/manifest.toml",
    "plugin/aiqt-guardrails-hooks/.claude-plugin/plugin.json",
}

# The only shippable file that is not scannable text. It is byte-reconciled by gen_skill.py --check from
# sources this gate DOES scan, so its content portability follows transitively.
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

SKIP_DIRS = {".git", "node_modules", "__pycache__"}

# An email-shaped token. The operator email is a subset of this, so denying every email-shaped token outside
# the attribution allow-list covers the exact address without hardcoding it in this gate.
EMAIL = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')


# --- pure logic (always run in --self-test) ---------------------------------------------------------

def normalize_term(term):
    """The canonical hyphen-joined token form of a term, using the leak gate's normalization, so every
    spelling variant of the same term collapses to one gram (session handoff -> session-handoff)."""
    return "-".join(_tokens(term))


def find_operational_terms(text, term_grams, maxn):
    """Return the sorted operational grams present in text (matched as 1..maxn token n-grams, so a term
    matches regardless of the spacing, hyphenation, or casing it is written with)."""
    grams = ngram_forms(text, maxn)
    return sorted(t for t in term_grams if t in grams)


def find_emails(text):
    """Return every email-shaped token in text."""
    return EMAIL.findall(text)


def scan_text(rel, text, name, term_grams, maxn, identity_allowed):
    """Scan one file's text for C1 (identity), C2 (operational vocabulary), and C5 (exemption marker).
    identity_allowed suppresses ONLY the C1 identity checks (the attribution locations); C2 and C5 always
    apply. Returns a list of finding strings."""
    findings = []
    lines = text.splitlines()
    if not identity_allowed:
        for number, line in enumerate(lines, 1):
            if name and name in line:
                findings.append("{}:{}: operator identity name in shipped content (portability C1)".format(
                    rel, number))
            for token in EMAIL.findall(line):
                findings.append("{}:{}: email address {!r} in shipped content (portability C1)".format(
                    rel, number, token))
    for term in find_operational_terms(text, term_grams, maxn):
        findings.append("{}: repo-operational term {!r} (portability C2)".format(rel, term))
    for number, line in enumerate(lines, 1):
        if "leak-allow" in line:
            findings.append("{}:{}: shipped leak-allow exemption marker (portability C5)".format(rel, number))
    return findings


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


def gather_files(root):
    """Return every file in the shippable surface. Each required dir root must exist and walk cleanly, and
    each required file root must exist; anything else is fail-closed (GateError, or an OSError from the
    walker on an unlistable subtree, both caught at run() as exit 2)."""
    files = []
    for rel in REQUIRED_DIR_ROOTS:
        d = root / rel
        if not d.is_dir():
            raise GateError("required surface root {} is absent or not a directory".format(rel))
        files.extend(walk_files(d, SKIP_DIRS))
    for rel in REQUIRED_FILE_ROOTS:
        f = root / rel
        if not f.is_file():
            raise GateError("required surface file {} is absent".format(rel))
        files.append(f)
    return files


def scan_file(root, path, name, term_grams, maxn):
    """Scan one surface file. An allow-listed binary is passed. A non-text suffix or a UTF-8 decode failure
    outside the allow-list is a C3 finding (exit 1), never a fail-closed exit 2: an unscannable shipped file
    is exactly what this gate asserts against. Returns a list of finding strings."""
    rel = path.relative_to(root).as_posix()
    if rel in BINARY_ALLOW:
        return []
    if path.suffix not in TEXT_SUFFIXES:
        return ["{}: non-portable file class (suffix {!r} is not scannable text and is not on the binary "
                "allow-list) (portability C3)".format(rel, path.suffix)]
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ["{}: shipped text file is not valid UTF-8 and is not on the binary allow-list "
                "(portability C3)".format(rel)]
    return scan_text(rel, text, name, term_grams, maxn, identity_allowed=(rel in IDENTITY_ALLOW))


def run(root):
    """Scan the shippable surface under root. Returns the exit code 0/1/2."""
    try:
        name, _email = load_identity(root)
        term_grams = {normalize_term(t) for t in OPERATIONAL_TERMS}
        maxn = max(len(_tokens(t)) for t in OPERATIONAL_TERMS)
        files = gather_files(root)
        findings = []
        for path in sorted(files):
            findings.extend(scan_file(root, path, name, term_grams, maxn))
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
    print("PASS: the shippable surface is portable (no operator identity outside its attribution, no "
          "repo-operational vocabulary, no exemption markers, no unscannable file classes off the allow-list)")
    return 0


# --- self-test --------------------------------------------------------------------------------------
# Pure-function cases (term normalization, email pattern, allow-list keying) always run and are
# deterministic. The surface-level cases build a synthetic surface in a private tempdir and are skipped with
# a printed note (never a false pass) where no writable tempdir exists; CI always has one. No wall clock, no
# randomness.

_CLEAN_MD = "# Heading\n\nPortable governance content with no operator identity and no operating vocabulary.\n"


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
    plugin_json = base / "plugin/aiqt-guardrails-hooks/.claude-plugin/plugin.json"
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
    name, email = "Test Operator", "operator@example.invalid"
    term_grams = {normalize_term(t) for t in OPERATIONAL_TERMS}
    maxn = max(len(_tokens(t)) for t in OPERATIONAL_TERMS)
    failures = []

    # Term normalization: every spelling variant of a term collapses to one gram, and the camelCase form is
    # matched inside text.
    for variant in ["session-handoff", "session handoff", "session_handoff", "SessionHandoff"]:
        if normalize_term(variant) != "session-handoff":
            failures.append("normalize_term({!r}) != 'session-handoff'".format(variant))
    if "claude-local" not in find_operational_terms("see CLAUDE.local.md today", {"claude-local"}, 3):
        failures.append("find_operational_terms missed CLAUDE.local.md -> claude-local")
    if find_operational_terms("wholly portable prose", term_grams, maxn):
        failures.append("find_operational_terms found a term in clean prose")

    # Email pattern.
    if find_emails("reach me at a.b+x@sub.example.co please") != ["a.b+x@sub.example.co"]:
        failures.append("find_emails did not extract the address")
    if find_emails("no address, just the word email here"):
        failures.append("find_emails matched non-email text")

    # Allow-list keying (pure): identity is suppressed only in the attribution locations; C2/C5 are not.
    if scan_text("plugin/aiqt-guardrails-hooks/.claude-plugin/plugin.json",
                 'email = "{}"'.format(email), name, term_grams, maxn, identity_allowed=True):
        failures.append("scan_text flagged identity in an allow-listed location")
    if not scan_text(".claude/rules/x.md", 'contact {}'.format(email), name, term_grams, maxn,
                     identity_allowed=False):
        failures.append("scan_text did not flag an email outside the allow-list")
    if "site/downloads/aiqt-skill.zip" not in BINARY_ALLOW:
        failures.append("the skill zip is not on the binary allow-list")

    # Surface-level cases against a real synthetic surface. Needs a writable tempdir; skipped (not failed)
    # with a printed note where none exists, since CI always has one and the pure coverage above still ran.
    import io
    import os
    import shutil
    import tempfile
    from contextlib import redirect_stderr, redirect_stdout

    def _run_quiet(sroot):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return run(sroot)

    tmp_ran = False
    unreadable_ran = False
    try:
        base_tmp = Path(tempfile.mkdtemp(prefix="aiqt-portability-selftest-"))
    except OSError:
        base_tmp = None
        print("SELF-TEST NOTE: no writable temp directory; surface-level cases SKIPPED (the pure "
              "normalization/email/allow-list coverage above still ran)", file=sys.stderr)

    if base_tmp is not None:
        tmp_ran = True

        def _fresh(tag):
            return _build_surface(base_tmp / tag, name, email)

        try:
            # (a) a clean surface, identity present ONLY in the two attribution locations, passes.
            if _run_quiet(_fresh("clean")) != 0:
                failures.append("a clean synthetic surface (identity in its attribution locations) "
                                "expected exit 0")

            # (b) a planted email in a rule fails.
            s = _fresh("planted-email")
            (s / ".claude/rules/leak.md").write_text(
                "# Rule\n\nquestions to {}\n".format(email), encoding="utf-8")
            if _run_quiet(s) != 1:
                failures.append("a planted email in a rule expected exit 1")

            # (c) the operator identity name planted outside the attribution locations fails.
            s = _fresh("planted-name")
            (s / "AGENTS.md").write_text("# Adapter\n\nauthored by {}\n".format(name), encoding="utf-8")
            if _run_quiet(s) != 1:
                failures.append("the operator name outside its attribution expected exit 1")

            # (d) a camel-cased operational term fails.
            s = _fresh("camel-term")
            (s / ".aiqt/core/placeholder.md").write_text(
                "# Note\n\nrun the SessionHandoff at close\n", encoding="utf-8")
            if _run_quiet(s) != 1:
                failures.append("a camel-cased operational term expected exit 1")

            # (e) an unknown non-text file fails while the allow-listed zip (present in every surface) passes.
            s = _fresh("unknown-binary")
            (s / "site/downloads/logo.png").write_bytes(b"\x89PNG\r\n\x1a\n not text")
            if _run_quiet(s) != 1:
                failures.append("an unknown .png in the surface expected exit 1")

            # (f) a planted leak-allow exemption marker fails.
            s = _fresh("planted-marker")
            (s / ".claude/rules/marked.md").write_text(
                "# Rule\n\nthis line carries a leak-allow marker\n", encoding="utf-8")
            if _run_quiet(s) != 1:
                failures.append("a shipped leak-allow marker expected exit 1")

            # (g) a missing required root is fail-closed exit 2.
            s = _fresh("missing-root")
            shutil.rmtree(s / ".cursor/rules/aiqt-guardrails")
            if _run_quiet(s) != 2:
                failures.append("a missing required root expected fail-closed exit 2")

            # (h) an absent identity manifest is fail-closed exit 2.
            s = _fresh("no-manifest")
            (s / IDENTITY_MANIFEST).unlink()
            if _run_quiet(s) != 2:
                failures.append("an absent identity manifest expected fail-closed exit 2")

            # (i) an unreadable directory under a required root is fail-closed exit 2. Skipped with a note
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
                      "the exit-2 unreadable-dir case was SKIPPED", file=sys.stderr)
            else:
                unreadable_ran = True
                if _run_quiet(s) != 2:
                    failures.append("an unreadable directory expected fail-closed exit 2")
            os.chmod(locked, 0o755)  # restore so the tree removes cleanly
        finally:
            shutil.rmtree(base_tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    detail = "term normalization, email pattern, and allow-list keying hold"
    if tmp_ran:
        detail += "; the clean-surface pass and the identity/vocabulary/marker/unknown-file/missing-root/"
        detail += "absent-manifest findings hold"
        detail += "; the unreadable-dir exit-2 case " + ("holds" if unreadable_ran else "was SKIPPED")
    else:
        detail += "; the surface-level cases were SKIPPED (no writable temp directory), so those "
        detail += "invariants are UNVERIFIED this run"
    print("SELF-TEST PASS: " + detail)
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
