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

# The first line-anchored `Version X.Y.Z` in a file. Greedy \d+ over three dotted groups then a word
# boundary matches both the source's "Version 1.0.3 ." and a condensation's "Version 1.0.3." spelling.
VERSION_RE = re.compile(r"^Version\s+(\d+\.\d+\.\d+)\b", re.M)


class GateError(Exception):
    """A fail-closed condition (a missing/unreadable input, a malformed version line): exit 2."""


def char_count(text):
    """The wc -m character count of `text`: the number of Unicode characters, the trailing newline
    included, matching how the condensations are budgeted."""
    return len(text)


def extract_version(text, where):
    """The version from the first line-anchored `Version X.Y.Z` in `text`, or GateError naming `where`."""
    m = VERSION_RE.search(text)
    if not m:
        raise GateError("{}: no line-anchored 'Version X.Y.Z' line found".format(where))
    return m.group(1)


def skill_version(root):
    """The authoritative skill version: the meta `version` in skill-source.md, read through gen_skill's own
    validated section splitter and meta parser (never a forked parser). GateError on a missing/unreadable
    source, a source with no meta section, or a meta with no version key."""
    path = root / SKILL_SRC_REL
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
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
    try:
        return (root / rel).read_text(encoding="utf-8")
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
        # Refuse to bless stale condensations: if the source hash changed but a condensation was not also
        # changed in git, the reconciliation did not happen (unless --force says no change was needed).
        if recorded_path.exists():
            try:
                recorded = recorded_path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError) as exc:
                print("error: cannot read {} ({}); fail-closed".format(RECORDED_REL, exc), file=sys.stderr)
                return 2
            if recorded and recorded != computed and not force:
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
#   (b) run() end to end on a temp root: a conformant tree passes (exit 0); an over-cap condensation,
#       a version mismatch, and an un-reconciled source drift (recorded != computed) each fail (exit 1);
#   (c) fail-closed (exit 2): a missing condensation and a missing recorded hash file.

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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("FAIL: check_sized_instructions self-test")
        for f in failures:
            print("  - " + f)
        return 1
    print("PASS: check_sized_instructions self-test: the pure content check flags an over-cap file and a "
          "version mismatch; run() passes a conformant tree and fails an over-cap file, a version mismatch, "
          "and an un-reconciled source drift (exit 1); a missing condensation and a missing recorded hash "
          "both fail closed (exit 2)")
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return _self_test()
    return run(Path(__file__).resolve().parents[1], update="--update" in args, force="--force" in args)


if __name__ == "__main__":
    sys.exit(main())
