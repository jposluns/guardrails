#!/usr/bin/env python3
"""Placement gate for .claude/rules/ (corpus-management spec section 6, core violation codes).

Validates the read tree's STRUCTURE and NAMING independently of the generator: every rule file lives in a
recognized family with a name that both fits its family's derivation shape AND matches what its frontmatter
derives, none loose or misfiled, the apex is standard, and each vendored external/<source>/ carries its
provenance. Check-only; never writes (the self-test builds fixtures under a tempdir only).
  check_rule_placement.py [--root DIR]   exit 0 clean, 1 violations, 2 usage/read error
  check_rule_placement.py --self-test    tempdir fixtures only; assert the probe and gate vectors
  check_rule_placement.py --execution-report ABS_PATH
                                         the self-test, also writing its execution report, finalized
                                         at interpreter exit (tools/_selftest_exit_report.py), as the
                                         execution gate runs it

An existing .claude/rules that is NOT a real directory (a regular file, a FIFO, a dangling symlink, or
a symlink even to a directory) exits 2: _standards.dir_present is a three-way probe that refuses a
wrong-type entry rather than reading it as absent, so this gate can never PASS a wrong-type input as
"no tree".

Emits the deterministic section 6.6 codes: loose, unknown-family, nested-dir, bad-name,
tier-code-mismatch, numbered-non-aiqt, apex-nonstandard, frontmatter-drift, missing-provenance. Each
violation line is `<relpath>: <code>: <detail>` per section 6.2. The adopter .corpus-config.yml support
(6.4/6.5 families and metadata, 6.7 pending: tolerance) and the pack-drift WARNING treatment (6.6) serve
the not-yet-built adopter cleanup workflow and are deferred (recorded as GD-9); this gate runs fail-closed
on the built-in aiqt/security/external families and accepts both pack- and adopter-origin rules.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_rule_placement.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import contextlib
import errno
import importlib.util
import io
import os
import re
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _selftest_exit_report  # noqa: E402  (the execution report, finalized at interpreter exit)
import _standards  # noqa: E402  (the module itself, so the self-test can patch its probe primitive)
import gen_rules  # noqa: E402  for MAP_KEYS/SEQ_KEYS rebind under --root
from gen_rules import parse_source, derive  # noqa: E402
from _standards import dir_present, map_keys  # noqa: E402

METADATA = {"PROVENANCE.md", "README.md"}
BUILTIN_FAMILIES = {"aiqt", "security", "external"}

# The fixed AIQT facet-to-tier map (spec 6.6 tier-code-mismatch).
AIQT_TIER_OF = {
    "ACCUR": "10", "INTEG": "10", "QUALI": "10", "TRUST": "10",
    "PROGR": "20", "SPEED": "30", "COST": "40",
}
_SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
# aiqt non-apex derivation shape <tier>-<facet>-<slug>.md; any tier with any AIQT facet is
# lexically well-formed (tier/facet consistency is checked separately as tier-code-mismatch).
AIQT_NONAPEX_RE = re.compile(r"^(10|20|30|40)-(ACCUR|INTEG|QUALI|TRUST|PROGR|SPEED|COST)-" + _SLUG + r"\.md$")
# security derivation shape <facet>-<slug>.md. Codes SEC* per the design-of-record (supersedes the seed
# corpus-management spec 6.5 CONFI/INTEG/AVAIL/PRIV set): SECC=Confidentiality SECI=Integrity SECA=Availability SECP=Privacy.
SECURITY_RE = re.compile(r"^(SECC|SECI|SECA|SECP)-" + _SLUG + r"\.md$")


def repo_root_default():
    p = Path.cwd().resolve()
    return next((a for a in [p, *p.parents] if (a / ".git").exists()), p)


def check_name(family, name):
    """Return a (code, detail) lexical violation for the file NAME alone, or None if well-formed.

    Name-only checks (no frontmatter): numbered-non-aiqt, apex-nonstandard, bad-name, tier-code-mismatch.
    """
    if family != "aiqt" and re.match(r"\d+-", name):
        return ("numbered-non-aiqt", "a leading digit-dash prefix is reserved for aiqt/")
    if family == "aiqt":
        if name.startswith("00-"):
            if name == "00-project-integrity.md":
                return None
            return ("apex-nonstandard", "only 00-project-integrity.md may take the 00- prefix")
        m = AIQT_NONAPEX_RE.match(name)
        if not m:
            return ("bad-name", "does not match the aiqt <tier>-<facet>-<slug>.md shape")
        tier, facet = m.group(1), m.group(2)
        if AIQT_TIER_OF[facet] != tier:
            return ("tier-code-mismatch", "facet {} belongs to tier {}, not {}".format(facet, AIQT_TIER_OF[facet], tier))
        return None
    if family == "security":
        if SECURITY_RE.match(name):
            return None
        return ("bad-name", "does not match the security <facet>-<slug>.md shape")
    return None


def check_drift(f, rules):
    """Return a (code, detail) frontmatter-drift finding for file f, or None. Raises OSError on read."""
    try:
        fm = parse_source(f)
    except ValueError as exc:
        return ("frontmatter-drift", str(exc))
    try:
        want = derive(fm, f.name, ("pack", "adopter"))
    except ValueError as exc:
        return ("frontmatter-drift", str(exc))
    # as_posix(), not str(): derive() returns a forward-slash path, so a backslash from str() on
    # Windows would make every correctly-placed rule read as frontmatter-drift.
    if f.relative_to(rules).as_posix() != want:
        return ("frontmatter-drift", "derives {}".format(want))
    return None


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    if args == ["--self-test"]:
        return self_test()
    if len(args) == 2 and args[0] == "--execution-report" and os.path.isabs(args[1]):
        return self_test(args[1])
    root = None
    i = 0
    while i < len(args):
        if args[i] == "--root" and i + 1 < len(args):
            root = Path(args[i + 1])
            i += 2
        else:
            print("usage: check_rule_placement.py [--root DIR | --self-test | "
                  "--execution-report ABS_PATH]", file=sys.stderr)
            return 2
    root = (root or repo_root_default()).resolve()
    # gen_rules binds MAP_KEYS/SEQ_KEYS at import from ITS OWN repo_root(); derive() (called by
    # check_drift) validates a rule's map-* keys against them. Under --root against an adopter tree with
    # its own .aiqt/standards/, rebind from that root so their map-keys are judged against their
    # manifests, not this checker's. With no --root, root == repo_root() so this is a no-op.
    # Fail-closed if that dir exists but cannot be listed: map_keys() raises on an unlistable dir (via
    # _standards._ensure_listable) rather than letting Path.glob read it as empty.
    try:
        gen_rules.MAP_KEYS = map_keys(root)
        gen_rules.SEQ_KEYS = {"secondary"} | gen_rules.MAP_KEYS
    except OSError as exc:
        print("cannot read {}: {}".format(root / ".aiqt" / "standards", exc), file=sys.stderr)
        return 2
    rules = root / ".claude" / "rules"
    # dir_present (not is_dir): an unreadable .claude/ parent must fail closed (exit 2), not read as an
    # absent tree and PASS.
    try:
        present = dir_present(rules)
    except OSError as exc:
        print("cannot read {}: {}".format(rules, exc), file=sys.stderr)
        return 2
    if not present:
        print("PASS: no .claude/rules/ tree")
        return 0
    findings = []

    def report(path, code, detail):
        findings.append("{}: {}: {}".format(path.relative_to(root), code, detail))

    try:
        for item in sorted(rules.iterdir()):
            if item.name.startswith("."):
                continue
            if item.is_file():
                if item.suffix == ".md" and item.name not in METADATA:
                    report(item, "loose", "a rule file at the tree root, not on the metadata allowlist")
                continue
            family = item.name
            if family not in BUILTIN_FAMILIES:
                report(item, "unknown-family", "not a registered family directory")
                continue
            if family == "external":
                for sub in sorted(p for p in item.iterdir() if p.is_dir()):
                    if not (sub / "PROVENANCE.md").exists() or not (sub / "LICENSE").exists():
                        report(sub, "missing-provenance", "a vendored source lacks PROVENANCE.md or LICENSE")
                continue
            # aiqt and security are FLAT families (spec 6.3): only *.md directly in the family dir.
            # Any nested directory is a nested-dir violation (empty or not); dotfiles and non-md files
            # are out of scope and skipped. iterdir (not rglob) surfaces a read error as exit 2.
            for entry in sorted(item.iterdir()):
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    report(entry, "nested-dir", "a flat family admits no subdirectory")
                    continue
                if entry.suffix != ".md":
                    continue
                nc = check_name(family, entry.name)
                if nc:
                    report(entry, nc[0], nc[1])
                    continue
                drift = check_drift(entry, rules)
                if drift:
                    report(entry, drift[0], drift[1])
    except OSError as exc:
        print("error: cannot read the rules tree: {}".format(exc), file=sys.stderr)
        return 2
    if findings:
        print("FAIL: {} placement violation(s)".format(len(findings)))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    print("PASS: rule placement conforms to the taxonomy")
    return 0


# --- self-test ----------------------------------------------------------------------------------------
# Proves the three-way _standards.dir_present probe and this gate's use of it, over tempdir fixtures
# only (never the real repo): a real directory reads present, genuine absence reads absent, a
# wrong-type entry (a regular file, a FIFO, a dangling symlink, or any symlink where a directory is
# required) is refused with NotADirectoryError rather than read as absent, and an unreachable path
# still raises OSError; the gate exits 0 on an absent tree, scans an existing empty directory as a
# tree, and exits 2 on a wrong-type .claude/rules or .aiqt/standards entry; and each listed probe
# mutant (a scratch copy of _standards.py with one guard reverted, loaded with importlib) is caught by
# its named probe vector.

SUITE_ID = "rule-placement-selftest"
CHECKS_MANIFEST = Path(__file__).resolve().parent / "selftest_checks.toml"
FAILURES = []
EXECUTED = []
_EXECUTED_SET = set()


def check(name, ok, detail=""):
    if name in _EXECUTED_SET:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    _EXECUTED_SET.add(name)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


def _expected_check_ids():
    try:
        with open(CHECKS_MANIFEST, "rb") as handle:
            data = tomllib.load(handle)
    # ValueError and RecursionError too, as in _standards.load_manifests.
    except (OSError, tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        print("SELF-TEST HARNESS ERROR: cannot read {}: {}".format(CHECKS_MANIFEST, exc),
              file=sys.stderr)
        return None
    for row in data.get("suite", []):
        if isinstance(row, dict) and row.get("id") == SUITE_ID:
            ids = row.get("expected-check-ids")
            if isinstance(ids, list) and ids and all(isinstance(item, str) and item for item in ids):
                return set(ids)
            break
    print("SELF-TEST HARNESS ERROR: missing or malformed suite {!r} in {}".format(
        SUITE_ID, CHECKS_MANIFEST), file=sys.stderr)
    return None


@contextlib.contextmanager
def _patched_lstat(module, replacement):
    original = module._lstat
    module._lstat = replacement
    try:
        yield
    finally:
        module._lstat = original


def _deny_lstat(path):
    """An lstat refused as an unreadable parent would refuse it, whoever runs the self-test (a
    chmod-based fixture is invisible to root)."""
    raise PermissionError(errno.EACCES, "probe refused by the self-test", str(path))


def _probe(module, fixture, paths):
    """One dir_present outcome: "present", "absent", "refused" (NotADirectoryError), or
    "error:<type>" for any other OSError."""
    try:
        if fixture == "unreachable":
            with _patched_lstat(module, _deny_lstat):
                present = module.dir_present(paths["dir"])
        else:
            present = module.dir_present(paths[fixture])
    except NotADirectoryError:
        return "refused"
    except OSError as exc:
        return "error:" + type(exc).__name__
    return "present" if present else "absent"


def _probe_paths(base):
    """The probe fixtures, one per outcome class, under the self-test's tempdir."""
    paths = {
        "absent-path": base / "missing",
        "dir": base / "realdir",
        "regular-file": base / "regular.txt",
        "dangling-symlink": base / "dangling",
        "symlink-to-dir": base / "dirlink",
        "symlink-to-file": base / "filelink",
        "fifo": base / "fifo",
    }
    paths["dir"].mkdir()
    paths["regular-file"].write_text("not a directory\n", encoding="utf-8")
    paths["dangling-symlink"].symlink_to(base / "no-such-target")
    paths["symlink-to-dir"].symlink_to(paths["dir"])
    paths["symlink-to-file"].symlink_to(paths["regular-file"])
    os.mkfifo(paths["fifo"])
    return paths


def _entry_fixture(path, kind):
    """One synthetic entry: absent (None), an empty directory, a regular file, a dangling symlink, or
    a symlink to a real sibling directory."""
    if kind is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "dir":
        path.mkdir()
    elif kind == "file":
        path.write_text("not a directory\n", encoding="utf-8")
    elif kind == "dangling":
        path.symlink_to(path.with_name("no-such-target"))
    elif kind == "dirlink":
        target = path.with_name("real-" + path.name)
        target.mkdir()
        path.symlink_to(target)


def _root_fixture(base, name, rules=None, standards=None):
    root = base / name
    root.mkdir()
    _entry_fixture(root / ".claude" / "rules", rules)
    _entry_fixture(root / ".aiqt" / "standards", standards)
    return root


def _gate(argv):
    """Run main(argv) in process with both streams captured: (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def _load_probe_mutant(source, directory, number):
    """Write one mutated copy of _standards.py to the scratch directory and load it with importlib."""
    path = directory / "standards_mutant_{}.py".format(number)
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("standards_mutant_{}".format(number), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def self_test(report_path=None):
    if report_path is not None:
        # The execution report is finalized at interpreter exit, after this run's cleanup
        # (tools/_selftest_exit_report.py); nothing writes it in band.
        _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
    saved_map_keys, saved_seq_keys = gen_rules.MAP_KEYS, gen_rules.SEQ_KEYS
    with tempfile.TemporaryDirectory(prefix="rule-placement-selftest-") as tmp:
        base = Path(tmp)
        try:
            # 1. a unit vector per probe outcome: present, genuinely absent, each wrong type refused,
            # and an unreachable path still an OSError (never read as absent).
            paths = _probe_paths(base)
            for cid, fixture, want in (
                    ("probe/absent-reads-false", "absent-path", "absent"),
                    ("probe/directory-reads-true", "dir", "present"),
                    ("probe/regular-file-refused", "regular-file", "refused"),
                    ("probe/dangling-symlink-refused", "dangling-symlink", "refused"),
                    ("probe/symlink-to-dir-refused", "symlink-to-dir", "refused"),
                    ("probe/symlink-to-file-refused", "symlink-to-file", "refused"),
                    ("probe/fifo-refused", "fifo", "refused"),
                    ("probe/unreachable-raises", "unreachable", "error:PermissionError")):
                got = _probe(_standards, fixture, paths)
                check(cid, got == want, got)

            # 2. the gate over synthetic --root trees: absence still passes, an existing empty
            # directory still reads as a tree (scanned, not "no tree"), and a wrong-type
            # .claude/rules or .aiqt/standards exits 2.
            for cid, rules_kind, want_code, want_text in (
                    ("gate/absent-tree-passes", "absent", 0, "PASS: no .claude/rules/ tree"),
                    ("gate/empty-directory-reads-tree", "dir", 0,
                     "PASS: rule placement conforms to the taxonomy"),
                    ("gate/regular-file-exits-2", "file", 2, "cannot read"),
                    ("gate/dangling-symlink-exits-2", "dangling", 2, "cannot read"),
                    ("gate/symlink-to-directory-exits-2", "dirlink", 2, "cannot read")):
                root = _root_fixture(base, "gate-" + rules_kind,
                                     rules=None if rules_kind == "absent" else rules_kind)
                code, out, err = _gate(["--root", str(root)])
                check(cid, code == want_code and want_text in out + err, (code, out, err))
            root = _root_fixture(base, "gate-standards-file", rules="dir", standards="file")
            code, out, err = _gate(["--root", str(root)])
            check("gate/standards-non-directory-exits-2", code == 2 and "cannot read" in err,
                  (code, out, err))

            # 3. each probe mutant (one guard reverted in a scratch copy of _standards.py) must be
            # caught by its named probe vector: the mutant's outcome differs from the intact one.
            source = (Path(__file__).resolve().parent / "_standards.py").read_text(encoding="utf-8")
            mutant_dir = base / "mutants"
            mutant_dir.mkdir()
            number = 0
            for cid, old, new, fixture, want in (
                    ("mutant/stat-follows-links-caught", "st = _lstat(path)", "st = os.stat(path)",
                     "symlink-to-dir", "refused"),
                    ("mutant/wrong-type-reads-absent-caught", "raise NotADirectoryError(",
                     "return False and NotADirectoryError(", "regular-file", "refused"),
                    ("mutant/eacces-reads-absent-caught", "except FileNotFoundError:",
                     "except OSError:", "unreachable", "error:PermissionError")):
                number += 1
                if source.count(old) != 1:
                    caught, got = False, "mutation anchor found {} times".format(source.count(old))
                else:
                    mutant = _load_probe_mutant(source.replace(old, new), mutant_dir, number)
                    got = _probe(mutant, fixture, paths)
                    caught = got != want
                check(cid, caught, got)
        finally:
            gen_rules.MAP_KEYS, gen_rules.SEQ_KEYS = saved_map_keys, saved_seq_keys

    # the in-run self-guard: the executed set is exactly this suite's registered set
    expected = _expected_check_ids()
    if expected is None:
        return 2
    for check_id in sorted(expected - _EXECUTED_SET):
        FAILURES.append("execution-set/missing: {}".format(check_id))
    for check_id in sorted(_EXECUTED_SET - expected):
        FAILURES.append("execution-set/extra: {}".format(check_id))
    if FAILURES:
        print("SELF-TEST FAIL: check_rule_placement {} check(s) failed".format(len(FAILURES)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_rule_placement {} checks (dir_present reads a real directory as "
          "present and genuine absence as absent, refuses a regular file, a FIFO and every symlink "
          "with NotADirectoryError, and raises OSError on an unreachable path; the gate passes an "
          "absent tree, scans an existing empty directory as a tree, and exits 2 on a wrong-type "
          "rules tree or standards dir; 3 probe mutants each caught); execution set reconciled "
          "against tools/selftest_checks.toml".format(len(EXECUTED)))
    return 0


if __name__ == "__main__":
    _selftest_exit_report.exit_with(main())
