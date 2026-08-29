#!/usr/bin/env python3
"""Sized-instructions gate: keep the hand-authored condensations in step with their source and caps.

The install page offers the portable AIQT instructions in four sizes. The largest,
site/downloads/aiqt-instructions.txt, is GENERATED from the chat-skill source by tools/gen_skill.py and is
drift-gated there. The three smaller ones,

  site/downloads/aiqt-instructions-8k.txt    <= 8000 characters
  site/downloads/aiqt-instructions-5k.txt    <= 5000 characters
  site/downloads/aiqt-instructions-1_5k.txt  <= 1500 characters

are HAND-AUTHORED condensations of that generated source: a machine cannot write the editorial trim, so
they have no generator. They sit in the ownership map's "derived" class (site/downloads/**), and this gate
is the drift control that makes that "derived" claim honest for them, the same shape cleanlanguage's
tools/check-portable-text-sync.sh uses for its own hand-maintained renderings. Without it a skill version
bump or a source edit would leave the three condensations silently stale.

What it verifies (fail-closed):

  (a) CHARACTER CAPS. Each condensation is within its labelled character budget (wc -m semantics: Unicode
      characters, not bytes; the trailing newline counts, as it does for wc -m).
  (b) VERSION CONSISTENCY. The `Version X.Y.Z` line in all three condensations, and in the full generated
      aiqt-instructions.txt, matches the authoritative skill version (the meta `version` in
      .aiqt/core/skill/skill-source.md, the same source gen_skill.py reads). A version bump fails this gate
      until the condensations are updated.
  (c) SOURCE DRIFT. A SHA-256 of the generated source (aiqt-instructions.txt) is recorded in a tracked file,
      tools/sized-instructions-source.sha256. When the source bytes change, the recorded hash no longer
      matches and the gate fails until a maintainer reconciles the three condensations and re-blesses with
      --update. --update REFUSES to bless if the source hash changed but a condensation was not also changed
      in git (the reconciliation did not happen), unless --force says no change was needed.

      DISCLOSED RESIDUAL (the reconciliation check is a heuristic, not a proof). The git check proves only
      that a condensation was "changed vs HEAD", NOT that the change semantically reconciled the meaning: a
      whitespace-only edit to a condensation satisfies it. And when the recorded baseline is MISSING or
      EMPTY there is no prior hash to compare, so the drift check cannot run at all; a fresh bless in that
      state is refused unless --force is passed, so an emptied or deleted baseline can never be blessed away
      silently. True editorial fidelity of a condensation to the source remains human judgment this gate
      cannot verify.

Usage:
  check_sized_instructions.py             check; exit non-zero on any finding or drift
  check_sized_instructions.py --update    reconcile: verify caps/versions, then record the source hash
  check_sized_instructions.py --self-test build synthetic fixtures and assert the fail-closed invariants

Exit 0 clean; 1 on a finding (over-cap, version mismatch, un-reconciled source drift, a refused bless); 2 on
a missing/unreadable required input (fail-closed), so an unreadable condensation can never read as clean.
"""
import hashlib
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_skill  # noqa: E402  reuse its validated section/meta parsers, never a second skill parser

# The three hand-authored condensations and their character caps (wc -m semantics).
SIZED = (
    ("site/downloads/aiqt-instructions-8k.txt", 8000),
    ("site/downloads/aiqt-instructions-5k.txt", 5000),
    ("site/downloads/aiqt-instructions-1_5k.txt", 1500),
)
SOURCE_REL = "site/downloads/aiqt-instructions.txt"           # the generated source they condense
SKILL_SRC_REL = ".aiqt/core/skill/skill-source.md"            # the authoritative version lives in its meta
RECORDED_REL = "tools/sized-instructions-source.sha256"       # the blessed source hash (tracked, tools/**)

# A line-anchored `Version X.Y.Z` line. HORIZONTAL whitespace only ([ \t], never \s, so a bare "Version"
# line cannot swallow the next line's number across the newline). Exactly three dotted numeric groups, and
# the version token is clean ONLY when those three segments are immediately followed by whitespace, the
# end of the line (an optional CR then the line end, so a CRLF file's version line still parses), or a
# single sentence period that is itself followed by whitespace or end-of-line. The
# positive lookahead `(?=[ \t]|\r?$|\.(?:[ \t]|\r?$))` enforces exactly that: it accepts the source spelling
# "Version 1.0.3 ." and the condensations' "Version 1.0.3." but rejects a 4th dotted segment (1.2.3.4), a
# non-numeric 4th segment (1.2.3.foo), a prerelease or build suffix (1.2.3-alpha, 1.2.3+build), and a
# trailing alphanumeric (1.2.3rc1). A malformed version matches nothing here and is caught as a fail-closed
# finding by the VERSION_LINE_RE sweep below, never a silent pass.
VERSION_RE = re.compile(r"^Version[ \t]+(\d+\.\d+\.\d+)(?=[ \t]|\r?$|\.(?:[ \t]|\r?$))", re.M)
# Any line that opens a version declaration ("Version" then horizontal whitespace). Every such line MUST
# parse cleanly through VERSION_RE; one that does not is a malformed declaration (e.g. a "Version 1.2.3.4"
# line sitting beside a valid "Version 1.0.3." line), which VERSION_RE alone would silently skip while the
# valid line won. The sweep in extract_version turns that into a fail-closed finding.
VERSION_LINE_RE = re.compile(r"^Version[ \t].*$", re.M)


class GateError(Exception):
    """A fail-closed condition (a missing/unreadable input, a malformed version line): exit 2."""


def char_count(text):
    """The wc -m character count of `text`: the number of Unicode characters, the trailing newline
    included, matching how the condensations are budgeted."""
    return len(text)


def extract_version(text, where):
    """The single version from the line-anchored `Version X.Y.Z` line(s) in `text`, or GateError naming
    `where`. Every valid version line is collected (not just the first), so two CONFLICTING version lines
    are a fail-closed finding rather than the first silently winning; a missing or malformed line is a
    finding too."""
    # A line that opens a version declaration but does not parse to a clean X.Y.Z is a malformed line, a
    # fail-closed finding even when a valid version line sits beside it (so a malformed line can never hide
    # next to a good one and let the good one silently win).
    for m in VERSION_LINE_RE.finditer(text):
        line = m.group(0)
        if not VERSION_RE.match(line):
            raise GateError("{}: malformed version line {!r}".format(where, line.strip()))
    versions = VERSION_RE.findall(text)
    if not versions:
        raise GateError("{}: no valid line-anchored 'Version X.Y.Z' line found".format(where))
    distinct = sorted(set(versions))
    if len(distinct) > 1:
        raise GateError("{}: conflicting version lines {}".format(where, distinct))
    return distinct[0]


def skill_version(root):
    """The authoritative skill version: the meta `version` in skill-source.md, read through gen_skill's own
    validated section splitter and meta parser (never a forked parser). GateError on a missing/unreadable
    source, a source with no meta section, or a meta with no version key."""
    path = root / SKILL_SRC_REL
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise GateError("cannot read {} ({})".format(SKILL_SRC_REL, exc))
    try:
        sections = gen_skill._split_sections(text)
    except ValueError as exc:
        raise GateError("{}: {}".format(SKILL_SRC_REL, exc))
    if "meta" not in sections:
        raise GateError("{}: no '=== meta ===' section".format(SKILL_SRC_REL))
    try:
        meta = gen_skill._parse_meta(sections["meta"])
    except ValueError as exc:
        raise GateError("{}: {}".format(SKILL_SRC_REL, exc))
    if "version" not in meta:
        raise GateError("{}: meta carries no 'version'".format(SKILL_SRC_REL))
    return meta["version"]


def _read_text(root, rel):
    """The file's text with NO newline translation, so char_count() equals `wc -m` exactly. read_text()
    normalizes CRLF to LF before len(), which would undercount a CRLF file by one character per line and
    let a just-over-cap CRLF condensation read as within its cap; decoding the raw bytes preserves every
    CR so the count matches wc -m."""
    try:
        return (root / rel).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise GateError("cannot read {} ({}); fail-closed".format(rel, exc))


def _source_hash(root):
    """SHA-256 of the generated source's exact bytes. GateError on an unreadable source."""
    try:
        return hashlib.sha256((root / SOURCE_REL).read_bytes()).hexdigest()
    except OSError as exc:
        raise GateError("cannot read {} ({}); fail-closed".format(SOURCE_REL, exc))


def compute_findings(authoritative_version, source_text, sized_texts):
    """The pure content findings: over-cap condensations and any version that disagrees with the
    authoritative skill version. `sized_texts` is a list of (rel, cap, text). Returns a list of strings.
    Source-drift is handled separately (it needs the recorded hash and, in --update, git)."""
    findings = []
    for rel, cap, text in sized_texts:
        n = char_count(text)
        if n > cap:
            findings.append("{}: {} characters, over its {} cap".format(rel, n, cap))
    src_version = extract_version(source_text, SOURCE_REL)
    if src_version != authoritative_version:
        findings.append("{}: version {} does not match the skill version {}".format(
            SOURCE_REL, src_version, authoritative_version))
    for rel, _cap, text in sized_texts:
        v = extract_version(text, rel)
        if v != authoritative_version:
            findings.append("{}: version {} does not match the skill version {}".format(
                rel, v, authoritative_version))
    return findings


def _git_unchanged(root, rel):
    """True iff `rel` has NO diff against HEAD (index and working tree), i.e. the reconciliation did not
    touch it. A git failure returns None (cannot confirm), so --update fails safe rather than blessing."""
    env_keys = [k for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")]  # scrub redirection vars
    import os
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    for k in env_keys:
        env.pop(k, None)
    try:
        proc = subprocess.run(["git", "-C", str(root), "diff", "--quiet", "HEAD", "--", rel],
                              capture_output=True, timeout=60, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode == 0:
        return True      # no diff
    if proc.returncode == 1:
        return False     # a diff exists
    return None          # any other exit (e.g. no HEAD, bad path): cannot confirm


def run(root, update=False, force=False):
    try:
        authoritative = skill_version(root)
        source_text = _read_text(root, SOURCE_REL)
        sized_texts = [(rel, cap, _read_text(root, rel)) for rel, cap in SIZED]
        computed = _source_hash(root)
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2

    try:
        findings = compute_findings(authoritative, source_text, sized_texts)
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2

    recorded_path = root / RECORDED_REL

    if update:
        # Read any prior blessed hash. A MISSING or EMPTY baseline offers nothing to reconcile the source
        # drift against, so a fresh bless there cannot run the reconciliation check at all: it must be an
        # explicit, deliberate act (--force), never a silent free bless. Without this, deleting or emptying
        # the baseline bypassed the reconciliation entirely (the delete-the-baseline bypass).
        recorded = ""
        if recorded_path.exists():
            try:
                recorded = recorded_path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError) as exc:
                print("error: cannot read {} ({}); fail-closed".format(RECORDED_REL, exc), file=sys.stderr)
                return 2
        if not recorded:
            if not force:
                print("refusing to bless: {} carries no prior blessed source hash (missing or empty), so "
                      "the source-drift reconciliation has no baseline to check against. A fresh bless must "
                      "be explicit: reconcile the condensations with the source, then re-run with "
                      "--force.".format(RECORDED_REL), file=sys.stderr)
                return 1
        elif recorded != computed and not force:
            # The source changed. Refuse to bless if a condensation was not also changed in git (the
            # reconciliation did not happen). Residual, disclosed in the docstring: this proves only
            # "changed vs HEAD", not that the edit actually reconciled the meaning; a whitespace-only edit
            # to a condensation passes this git-diff heuristic.
            for rel, _cap in SIZED:
                unchanged = _git_unchanged(root, rel)
                if unchanged is None:
                    print("refusing to bless: cannot confirm via git whether {} was reconciled; "
                          "commit the reconciliation or pass --force".format(rel), file=sys.stderr)
                    return 1
                if unchanged:
                    print("refusing to bless: the source changed but {} was not reconciled; update it, "
                          "or pass --force if no change is needed".format(rel), file=sys.stderr)
                    return 1
        if findings:
            print("refusing to record: the condensations still fail a content check:", file=sys.stderr)
            for f in findings:
                print("  - " + f, file=sys.stderr)
            return 1
        try:
            recorded_path.write_text(computed + "\n", encoding="utf-8")
        except OSError as exc:
            print("error: cannot write {} ({}); fail-closed".format(RECORDED_REL, exc), file=sys.stderr)
            return 2
        print("recorded source hash {} in {}; reconcile the condensations before committing".format(
            computed, RECORDED_REL))
        return 0

    # check mode: content findings plus source-drift against the recorded hash.
    if not recorded_path.exists():
        print("error: {} is missing; the source-drift check cannot evaluate. Record it with "
              "check_sized_instructions.py --update; fail-closed".format(RECORDED_REL), file=sys.stderr)
        return 2
    try:
        recorded = recorded_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        print("error: cannot read {} ({}); fail-closed".format(RECORDED_REL, exc), file=sys.stderr)
        return 2

    if recorded != computed:
        findings.append(
            "{}: source hash {} does not match the recorded {}; the generated source changed but the "
            "condensations were not reconciled. Review the three sized files against {}, then re-record "
            "with check_sized_instructions.py --update".format(
                SOURCE_REL, computed[:12] + "...", (recorded[:12] + "...") if recorded else "(empty)",
                SOURCE_REL))

    if findings:
        print("FAIL: {} sized-instructions finding(s)".format(len(findings)))
        for f in findings:
            print("  - " + f)
        return 1
    print("PASS: the three sized condensations are within their caps, agree on version {}, and match the "
          "recorded source hash".format(authoritative))
    return 0


# --- self-test ----------------------------------------------------------------------------------------
# Synthetic fixtures assert the fail-closed invariants without the real corpus:
#   (a) the pure content check: a clean set has no findings; an over-cap file, a condensation version
#       mismatch, and a source version mismatch each produce a finding;
#   (a2) version parsing (fix 1): the canonical and source spellings parse; a 4th dotted segment
#       (1.2.3.4), a non-numeric 4th segment (1.2.3.foo), a prerelease (1.2.3-alpha) or build
#       (1.2.3+build) suffix, a trailing alphanumeric (1.2.3rc1), a Version line split across a newline,
#       two conflicting version lines, and a malformed version line beside a valid one each fail-close.
#   (b) run() end to end on a temp root: a conformant tree passes (exit 0); an over-cap condensation,
#       a version mismatch, and an un-reconciled source drift (recorded != computed) each fail (exit 1);
#   (c) fail-closed (exit 2): a missing condensation and a missing recorded hash file;
#   (d) hardening mutations: a CRLF condensation one char over its cap fails (fix 2, exit 1); invalid
#       UTF-8 in skill-source.md fails closed (fix 3, exit 2); a 4th-segment source version fails closed
#       (fix 1, exit 2); and an empty or missing --update baseline refuses to bless without --force but
#       performs an explicit --force fresh bless (fix 4).

_MIN_SKILL = "=== meta ===\nversion: {v}\n"


def _wrap(version, body=""):
    """A minimal instruction-file body carrying a line-anchored Version line and optional filler."""
    return "AIQT instructions\nVersion {}. Licensed under CC BY-SA 4.0\n\n{}".format(version, body)


def _build(root, version="1.2.3", caps_ok=True, versions_ok=True, record=True):
    """A conformant synthetic tree: the skill source (meta version), the generated source, the three
    condensations (within caps, matching version), and the recorded source hash."""
    (root / ".aiqt" / "core" / "skill").mkdir(parents=True)
    (root / "site" / "downloads").mkdir(parents=True)
    (root / "tools").mkdir(parents=True)
    (root / SKILL_SRC_REL).write_text(_MIN_SKILL.format(v=version), encoding="utf-8")
    source_text = _wrap(version, "The full portable instructions.\n")
    (root / SOURCE_REL).write_text(source_text, encoding="utf-8")
    for rel, cap in SIZED:
        v = version if versions_ok else "9.9.9"
        text = _wrap(v)
        if not caps_ok and cap == 1500:
            text = _wrap(v, "x" * 2000)   # push the smallest over its cap
        (root / rel).write_text(text, encoding="utf-8")
    if record:
        h = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
        (root / RECORDED_REL).write_text(h + "\n", encoding="utf-8")
    return root


def _self_test():
    import contextlib
    import io
    import shutil
    import tempfile

    def quiet(root, **kw):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return run(root, **kw)

    failures = []

    # (a) pure content check.
    clean = [("f-8k", 8000, _wrap("1.2.3")), ("f-1_5k", 1500, _wrap("1.2.3"))]
    if compute_findings("1.2.3", _wrap("1.2.3"), clean):
        failures.append("pure: a clean set produced findings")
    over = [("f-1_5k", 1500, _wrap("1.2.3", "x" * 2000))]
    if not any("over its" in f for f in compute_findings("1.2.3", _wrap("1.2.3"), over)):
        failures.append("pure: an over-cap file produced no cap finding")
    mism = [("f-8k", 8000, _wrap("9.9.9"))]
    if not any("does not match" in f for f in compute_findings("1.2.3", _wrap("1.2.3"), mism)):
        failures.append("pure: a condensation version mismatch produced no finding")
    if not any(SOURCE_REL in f for f in compute_findings("1.2.3", _wrap("9.9.9"), clean)):
        failures.append("pure: a source version mismatch produced no finding")

    # (a2) version parsing (fix 1): horizontal-whitespace-only, exactly three dotted segments, and a
    # conflict between two disagreeing version lines is a fail-closed finding, not a silent first-wins.
    def raises_gate(text):
        try:
            extract_version(text, "x")
        except GateError:
            return True
        return False
    if extract_version(_wrap("1.0.3"), "x") != "1.0.3":
        failures.append("version: the canonical 'Version 1.0.3.' spelling did not parse")
    if extract_version("AIQT\nVersion 1.0.3 .\n", "x") != "1.0.3":
        failures.append("version: the source 'Version 1.0.3 .' spelling did not parse")
    if not raises_gate(_wrap("1.2.3.4")):
        failures.append("version: a 4th dotted segment (1.2.3.4) was accepted, not rejected")
    if not raises_gate(_wrap("1.2.3.foo")):
        failures.append("version: a non-numeric 4th segment (1.2.3.foo) was accepted, not rejected")
    if not raises_gate(_wrap("1.2.3-alpha")):
        failures.append("version: a prerelease suffix (1.2.3-alpha) was accepted, not rejected")
    if not raises_gate(_wrap("1.2.3+build")):
        failures.append("version: a build-metadata suffix (1.2.3+build) was accepted, not rejected")
    if not raises_gate(_wrap("1.2.3rc1")):
        failures.append("version: a trailing alphanumeric (1.2.3rc1) was accepted, not rejected")
    if not raises_gate("AIQT instructions\nVersion\n1.2.3\n"):
        failures.append("version: a split 'Version<newline>1.2.3' was accepted across the newline")
    if not raises_gate("AIQT\nVersion 1.2.3.\nVersion 9.9.9.\n"):
        failures.append("version: two conflicting version lines did not fail (first silently won)")
    if not raises_gate("AIQT\nVersion 1.2.3.4\nVersion 1.0.3.\n"):
        failures.append("version: a malformed version line beside a valid one did not fail (valid won)")

    tmp = Path(tempfile.mkdtemp(prefix="aiqt-sized-selftest-"))
    try:
        # (b) run() end to end.
        good = _build(tmp / "good")
        if quiet(good) != 0:
            failures.append("conformant tree expected exit 0")

        overcap = _build(tmp / "overcap", caps_ok=False)
        if quiet(overcap) != 1:
            failures.append("an over-cap condensation expected exit 1")

        badver = _build(tmp / "badver", versions_ok=False)
        if quiet(badver) != 1:
            failures.append("a version mismatch expected exit 1")

        drift = _build(tmp / "drift")
        (drift / RECORDED_REL).write_text("0" * 64 + "\n", encoding="utf-8")  # recorded != computed
        if quiet(drift) != 1:
            failures.append("an un-reconciled source drift expected exit 1")

        # (c) fail-closed (exit 2).
        miss_file = _build(tmp / "missfile")
        (miss_file / SIZED[0][0]).unlink()
        if quiet(miss_file) != 2:
            failures.append("a missing condensation expected exit 2 (fail-closed)")

        miss_rec = _build(tmp / "missrec", record=False)
        if quiet(miss_rec) != 2:
            failures.append("a missing recorded hash file expected exit 2 (fail-closed)")

        # (d) hardening mutations, each a fail-without-the-fix guard.
        # Fix 2 (CRLF char count): a condensation one character over its cap when CR is counted must fail.
        # read_text() would normalize the CRLF away and undercount it to exactly the cap (a silent pass).
        crlf = _build(tmp / "crlf")
        over_line = "Version 1.2.3.\r\n"
        crlf_body = over_line + "x" * (1501 - len(over_line))   # 1501 chars counting CR, over the 1500 cap
        (crlf / SIZED[2][0]).write_bytes(crlf_body.encode("utf-8"))
        if quiet(crlf) != 1:
            failures.append("a CRLF condensation one char over its cap did not fail (exit 1)")

        # Fix 3 (UTF-8 fail-close): invalid UTF-8 in skill-source.md must fail closed (exit 2), not raise
        # an uncaught UnicodeDecodeError that would exit 1.
        badutf8 = _build(tmp / "badutf8")
        (badutf8 / SKILL_SRC_REL).write_bytes(b"=== meta ===\nversion: 1.2.3\n\xff\xfe")
        if quiet(badutf8) != 2:
            failures.append("invalid UTF-8 in skill-source.md did not fail closed (exit 2)")

        # Fix 1 (end to end): a 4th-segment source version is a malformed line, a fail-closed finding (exit 2).
        badsrcver = _build(tmp / "badsrcver")
        (badsrcver / SOURCE_REL).write_text(_wrap("1.2.3.4", "body\n"), encoding="utf-8")
        if quiet(badsrcver) != 2:
            failures.append("a 4th-segment source version (1.2.3.4) did not fail closed (exit 2)")

        # Fix 4 (--update baseline bypass): an EMPTY or MISSING recorded baseline with a drifted source must
        # NOT bless without --force (the delete-the-baseline bypass); an explicit --force fresh bless passes.
        emptybase = _build(tmp / "emptybase")
        (emptybase / SOURCE_REL).write_text(_wrap("1.2.3", "drifted source\n"), encoding="utf-8")
        (emptybase / RECORDED_REL).write_text("", encoding="utf-8")
        if quiet(emptybase, update=True) != 1:
            failures.append("--update with an empty baseline blessed without --force (bypass)")
        if quiet(emptybase, update=True, force=True) != 0:
            failures.append("--update --force with an empty baseline did not perform the fresh bless (exit 0)")

        missbase = _build(tmp / "missbase")
        (missbase / SOURCE_REL).write_text(_wrap("1.2.3", "drifted source\n"), encoding="utf-8")
        (missbase / RECORDED_REL).unlink()
        if quiet(missbase, update=True) != 1:
            failures.append("--update with a missing baseline blessed without --force (bypass)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("FAIL: check_sized_instructions self-test")
        for f in failures:
            print("  - " + f)
        return 1
    print("PASS: check_sized_instructions self-test: the pure content check flags an over-cap file and a "
          "version mismatch; version parsing rejects a 4th segment, a non-numeric 4th segment, a "
          "prerelease or build suffix, a trailing alphanumeric, a newline-split Version line, conflicting "
          "version lines, and a malformed line beside a valid one; run() passes a conformant tree and fails "
          "an over-cap file, a version "
          "mismatch, and an un-reconciled source drift (exit 1); a missing condensation, a missing recorded "
          "hash, invalid UTF-8 in skill-source.md, and a malformed source version all fail closed (exit 2); "
          "a CRLF condensation over its cap fails (exit 1); and an empty or missing --update baseline "
          "refuses to bless without --force")
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return _self_test()
    return run(Path(__file__).resolve().parents[1], update="--update" in args, force="--force" in args)


if __name__ == "__main__":
    sys.exit(main())
