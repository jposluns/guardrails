#!/usr/bin/env python3
"""Fail-closed applicability gate (GD-99): every rule is tagged, from a controlled vocabulary.

The vocabulary (conditions + profiles) is .aiqt/core/applicability.toml; the per-rule assignments
(corpus-id -> conditions) are .aiqt/core/applies.toml. This gate enforces BIDIRECTIONAL coverage against
the live corpus: every rule's corpus-id has exactly one assignment (no untagged rule), no assignment names
a corpus-id that is not in the corpus (no orphan), and every assignment is a non-empty list of distinct
conditions drawn from the vocabulary. A missing or malformed input, or an unreadable corpus, fails closed.
  check_applies.py            check the repo
  check_applies.py --root DIR check an install at DIR
  check_applies.py --self-test  assert the gate's own good/bad cases
Exit: 0 clean; 1 coverage/assignment violations; 2 usage / cannot-evaluate (fail-closed).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import load_toml, repo_root  # noqa: E402
import gen_rules  # noqa: E402  (load_corpus, for the authoritative corpus-id set)

APPLICABILITY_REL = ".aiqt/core/applicability.toml"
APPLIES_REL = ".aiqt/core/applies.toml"
RULES_REL = ".aiqt/core/rules"
SLUG_RE = gen_rules.SLUG_RE
APPLICABILITY_KEYS = {"version", "condition", "profile"}
CONDITION_KEYS = {"slug", "question", "description"}
PROFILE_KEYS = {"name", "slug", "conditions"}


def _exact_table(value, keys, where):
    if not isinstance(value, dict):
        raise ValueError("{} must be a table".format(where))
    missing = keys - set(value)
    extra = set(value) - keys
    if missing:
        raise ValueError("{} missing key(s): {}".format(where, ", ".join(sorted(missing))))
    if extra:
        raise ValueError("{} unknown key(s): {}".format(where, ", ".join(sorted(extra))))


def _text(row, key, where):
    v = row[key]
    if not isinstance(v, str) or not v.strip():
        raise ValueError("{}: '{}' must be a non-empty string".format(where, key))
    return v


def load_applicability_model(path):
    """Load and fully validate applicability.toml; return its structured model."""
    data = load_toml(Path(path))
    name = Path(path).name
    _exact_table(data, APPLICABILITY_KEYS, name)
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("{}: version must be integer 1".format(name))
    conditions, profiles = data["condition"], data["profile"]
    if not isinstance(conditions, list) or not conditions:
        raise ValueError("{}: at least one [[condition]] is required".format(name))
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("{}: at least one [[profile]] is required".format(name))
    by_slug = {}
    for i, row in enumerate(conditions, 1):
        where = "{}: [[condition]] #{}".format(name, i)
        _exact_table(row, CONDITION_KEYS, where)
        slug = _text(row, "slug", where); _text(row, "question", where); _text(row, "description", where)
        if not SLUG_RE.fullmatch(slug):
            raise ValueError("{}: slug must be kebab-case".format(where))
        if slug in by_slug:
            raise ValueError("{}: duplicate condition slug '{}'".format(where, slug))
        by_slug[slug] = row
    prof_slugs, prof_names = set(), set()
    for i, row in enumerate(profiles, 1):
        where = "{}: [[profile]] #{}".format(name, i)
        _exact_table(row, PROFILE_KEYS, where)
        pname = _text(row, "name", where); pslug = _text(row, "slug", where)
        if not SLUG_RE.fullmatch(pslug):
            raise ValueError("{}: slug must be kebab-case".format(where))
        if pslug in prof_slugs:
            raise ValueError("{}: duplicate profile slug '{}'".format(where, pslug))
        if pname in prof_names:
            raise ValueError("{}: duplicate profile name '{}'".format(where, pname))
        refs = row["conditions"]
        if (not isinstance(refs, list) or not refs
                or not all(isinstance(r, str) and r for r in refs)):
            raise ValueError("{}: conditions must be a non-empty list of strings".format(where))
        if len(refs) != len(set(refs)):
            raise ValueError("{}: conditions contains a duplicate".format(where))
        unknown = sorted(set(refs) - set(by_slug))
        if unknown:
            raise ValueError("{}: unknown condition(s): {}".format(where, ", ".join(unknown)))
        prof_slugs.add(pslug); prof_names.add(pname)
    known = frozenset(by_slug)
    if "always" not in known:
        raise ValueError("{}: the 'always' condition is required".format(name))
    full = next((p for p in profiles if p["slug"] == "full-corpus"), None)
    if full is None:
        raise ValueError("{}: the 'full-corpus' profile is required".format(name))
    if set(full["conditions"]) != known:
        raise ValueError("{}: full-corpus must list every condition".format(name))
    for p in profiles:
        if "always" not in p["conditions"]:
            raise ValueError("{}: profile '{}' must include 'always'".format(name, p["slug"]))
    return data


def load_applicability(path):
    """Compatibility loader returning the validated condition-slug set."""
    data = load_applicability_model(path)
    return frozenset(row["slug"] for row in data["condition"])


def load_assignments(path):
    """Load applies.toml. Returns {corpus-id: [conditions]}. Structural validation only; fails closed."""
    data = load_toml(Path(path))
    name = Path(path).name
    if set(data) != {"version", "assignments"}:
        raise ValueError("{}: exactly top-level keys version + assignments required".format(name))
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("{}: version must be integer 1".format(name))
    assigns = data["assignments"]
    if not isinstance(assigns, dict) or not assigns:
        raise ValueError("{}: [assignments] must be a non-empty table".format(name))
    return assigns


def coverage_findings(corpus_ids, assignments, known_conditions):
    """Pure bidirectional check. Returns a sorted list of finding strings; empty means clean."""
    findings = []
    for cid in sorted(set(corpus_ids) - set(assignments)):
        findings.append("{}: untagged: no applies.toml assignment".format(cid))
    for cid in sorted(set(assignments) - set(corpus_ids)):
        findings.append("{}: orphan: assignment names no rule in the corpus".format(cid))
    for cid in sorted(set(assignments) & set(corpus_ids)):
        conds = assignments[cid]
        if not isinstance(conds, list) or not conds or not all(isinstance(c, str) and c for c in conds):
            findings.append("{}: invalid: conditions must be a non-empty list of strings".format(cid)); continue
        if len(conds) != len(set(conds)):
            dups = sorted({c for c in conds if conds.count(c) > 1})
            findings.append("{}: duplicate condition(s): {}".format(cid, ", ".join(dups)))
        unknown = sorted(set(conds) - set(known_conditions))
        if unknown:
            findings.append("{}: unknown condition(s): {}".format(cid, ", ".join(unknown)))
        if "always" in conds and len(set(conds)) > 1:
            findings.append("{}: always-subsumption: 'always' is the floor in every profile, so it must be "
                            "the ONLY listed condition (a second tag adds no activation context)".format(cid))
    return findings


def run(root):
    root = Path(root).resolve()
    try:
        known = load_applicability(root / APPLICABILITY_REL)
        assignments = load_assignments(root / APPLIES_REL)
        corpus = gen_rules.load_corpus(root / RULES_REL)
        corpus_ids = [str(fm["corpus-id"]) for _p, fm, _rel in corpus]
    except (ValueError, OSError, UnicodeError) as exc:
        print("error: cannot evaluate applicability ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    if not corpus_ids:
        print("error: rule corpus is empty; fail-closed", file=sys.stderr)
        return 2
    findings = coverage_findings(corpus_ids, assignments, known)
    if findings:
        print("FAIL: {} applicability violation(s)".format(len(findings)))
        for f in findings:
            print("  " + f)
        return 1
    print("PASS: {} rule(s) each carry a non-empty, controlled applies assignment".format(len(corpus_ids)))
    return 0


def self_test_main():
    known = frozenset(["always", "writes-code", "tools-retrieval"])
    checks = []
    # coverage_findings: clean
    checks.append(coverage_findings(["a", "b"], {"a": ["always"], "b": ["writes-code"]}, known) == [])
    # untagged
    checks.append(any("untagged" in f for f in coverage_findings(["a", "b"], {"a": ["always"]}, known)))
    # orphan
    checks.append(any("orphan" in f for f in coverage_findings(["a"], {"a": ["always"], "z": ["always"]}, known)))
    # unknown condition
    checks.append(any("unknown" in f for f in coverage_findings(["a"], {"a": ["nope"]}, known)))
    # empty
    checks.append(any("invalid" in f for f in coverage_findings(["a"], {"a": []}, known)))
    # duplicate
    checks.append(any("duplicate" in f for f in coverage_findings(["a"], {"a": ["always", "always"]}, known)))
    # always-subsumption (always beside another condition)
    checks.append(any("always-subsumption" in f for f in coverage_findings(["a"], {"a": ["always", "writes-code"]}, known)))
    # load_applicability rejects a bad vocab (missing always)
    import tempfile, os
    ok_bad = False
    try:
        d = tempfile.mkdtemp(prefix="aiqt-applies-selftest-")
        p = Path(d) / "app.toml"
        p.write_text('version = 1\n[[condition]]\nslug="x"\nquestion="q"\ndescription="d"\n'
                     '[[profile]]\nname="Full corpus"\nslug="full-corpus"\nconditions=["x"]\n', encoding="utf-8")
        try:
            load_applicability(p)
        except ValueError:
            ok_bad = True
    finally:
        import shutil; shutil.rmtree(d, ignore_errors=True)
    checks.append(ok_bad)
    if all(checks):
        print("SELF-TEST PASS: coverage detects untagged/orphan/unknown/empty/duplicate/always-subsumption; a "
              "vocab without 'always' fails closed. ({} checks)".format(len(checks)))
        return 0
    print("SELF-TEST FAIL: {}".format([i for i, c in enumerate(checks) if not c]))
    return 1


def main():
    args = sys.argv[1:]
    if args == ["--self-test"]:
        return self_test_main()
    if not args:
        return run(repo_root())
    if len(args) == 2 and args[0] == "--root":
        return run(Path(args[1]))
    print("usage: check_applies.py [--root DIR] | --self-test", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
