#!/usr/bin/env python3
"""Placement gate for .claude/rules/ (corpus-management spec section 6, core violation codes).

Validates the read tree's STRUCTURE and NAMING independently of the generator: every rule file lives in a
recognized family with a name that both fits its family's derivation shape AND matches what its frontmatter
derives, none loose or misfiled, the apex is standard, and each vendored external/<source>/ carries its
provenance. Check-only; never writes.
  check_rule_placement.py [--root DIR]   exit 0 clean, 1 violations, 2 usage/read error
  check_rule_placement.py --self-test    assert the gate's own --root vectors, each discriminating
  check_rule_placement.py [--self-test] --execution-report ABS_PATH
                                         the self-test, also writing its execution report, finalized at
                                         interpreter exit (tools/_selftest_exit_report.py), as the
                                         execution gate runs it
An explicit --root must be given once and name an existing directory: a repeated --root, an empty value, a
path that does not exist (a dangling symlink included) or one that is not a directory is exit 2 (cannot
evaluate), never read as a tree with no .claude/rules/. An existing root with no .claude/rules/ tree still
passes; a .claude/rules that is there but cannot be read as the tree (a regular file, or a symlink that is
dangling, whose target is not a directory, or whose target resolves outside the root) is exit 2 (cannot
evaluate), named, never read as that absence, and so is such a .aiqt/standards, never read as no manifests. A
symlink whose target is a directory inside the root is read as that directory (_standards.dir_or_absent).
Residual: the root is checked once, before it is read, so a root removed or replaced between that check and
the read (by another writer at the same moment) is not detected and can still read as a tree with no
.claude/rules/, and so is a symlinked .claude/rules retargeted after it is resolved. Only the last component
of .claude/rules is judged: a .claude that is itself a symlink is followed.

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
import io
import os
import re
import stat
import subprocess
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_rules  # noqa: E402  for MAP_KEYS/SEQ_KEYS rebind under --root
from gen_rules import parse_source, derive  # noqa: E402
import _standards  # noqa: E402  the self-test's map_keys mutants patch its dir_or_absent
from _standards import dir_or_absent, dir_present, map_keys  # noqa: E402
from _standards import LINK_DANGLING, LINK_NOT_A_DIRECTORY, LINK_OUTSIDE_ROOT  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_REL = "tools/selftest_checks.toml"
# This gate's own self-test suite in the registry.
SUITE_ID = "rule-placement-selftest"
CHILD_TIMEOUT = 60
# The stat call and directory test the --root guard uses, named so the self-test can patch each one to
# build its mutants.
_stat = os.stat
_is_dir = stat.S_ISDIR

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


def root_problem(root_arg):
    """Return why an explicitly named --root cannot be evaluated, or None when it names an existing directory.

    The root is resolved non-strictly and an absent .claude/rules/ is a PASS, so without this an absent or
    mistyped root would read as a tree with no rules and pass over input never read, and an empty value would
    silently name the working directory. os.stat follows a symlink, as resolve() does, so a symlink to a
    directory is accepted and a dangling one is refused."""
    if not root_arg:
        return "--root is empty"
    try:
        st = _stat(root_arg)
    except OSError as exc:
        return "cannot read --root {}: {}".format(root_arg, exc)
    if not _is_dir(st.st_mode):
        return "--root {} is not a directory".format(root_arg)
    return None


def repeat_problem(root_args):
    """Return why more than one --root cannot be evaluated, or None for at most one.

    Only one root is read, so a second --root would otherwise silently replace the first and leave it never
    checked. The message names every value given, in order."""
    if len(root_args) > 1:
        return "--root is given {} times ({}); name one root".format(
            len(root_args), ", ".join(repr(root_arg) for root_arg in root_args))
    return None


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
    if (len(args) in (2, 3) and args[-2] == "--execution-report" and os.path.isabs(args[-1])
            and args[:-2] in ([], ["--self-test"])):
        return self_test(args[-1])
    if args == ["--self-test"]:
        return self_test()
    root_args = []
    i = 0
    while i < len(args):
        if args[i] == "--root" and i + 1 < len(args):
            root_args.append(args[i + 1])
            i += 2
        else:
            print("usage: check_rule_placement.py [--root DIR] | [--self-test] [--execution-report ABS_PATH]",
                  file=sys.stderr)
            return 2
    problem = repeat_problem(root_args)
    if problem is not None:
        print("error: {} (cannot evaluate)".format(problem), file=sys.stderr)
        return 2
    root = root_args[-1] if root_args else None
    if root is not None:
        problem = root_problem(root)
        if problem is not None:
            print("error: {} (cannot evaluate)".format(problem), file=sys.stderr)
            return 2
    root = (Path(root) if root is not None else repo_root_default()).resolve()
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
    # dir_or_absent (not is_dir or dir_present): an unreadable .claude/ parent must fail closed (exit 2),
    # not read as an absent tree and PASS, and so must a .claude/rules that is there but cannot be read as
    # the tree (a regular file, or a symlink that is dangling, whose target is not a directory or resolves
    # outside the root): only genuine absence passes. A symlink to a directory inside the root is read.
    try:
        present, problem = dir_or_absent(rules, root)
    except OSError as exc:
        print("cannot read {}: {}".format(rules, exc), file=sys.stderr)
        return 2
    if problem is not None:
        print("error: {} {} (cannot evaluate)".format(rules, problem), file=sys.stderr)
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


# ---------------------------------------------------------------------------------------------- self-test

NO_RULES = "PASS: no .claude/rules/ tree\n"
# (vector, fixture key, expected exit, expected stdout or None, expected stderr fragment or None); each
# "{}" or "{!r}" in a stderr fragment is filled, in order, with the fixture's --root values. Each root/
# vector is a --root the guard must refuse; each control/ vector is one it must still read.
VECTORS = (
    ("root/repeated", "repeated", 2, "", "error: --root is given 2 times ({!r}, {!r}); name one root (cannot "
     "evaluate)"),
    ("root/nonexistent", "missing", 2, "", "error: cannot read --root {}: "),
    ("root/comma-joined", "comma-joined", 2, "", "error: cannot read --root {}: "),
    ("root/dangling-symlink", "dangling", 2, "", "error: cannot read --root {}: "),
    ("root/regular-file", "regular-file", 2, "", "error: --root {} is not a directory (cannot evaluate)"),
    ("root/empty", "empty", 2, "", "error: --root is empty (cannot evaluate)"),
    ("rules/regular-file", "rules-file", 2, "", "/.claude/rules is not a directory (cannot evaluate)"),
    ("rules/dangling-symlink", "rules-dangling", 2, "", "/.claude/rules " + LINK_DANGLING + " (cannot evaluate)"),
    ("rules/symlink-to-file", "rules-to-file", 2, "", "/.claude/rules " + LINK_NOT_A_DIRECTORY + " (cannot evaluate)"),
    ("rules/symlink-outside-root", "rules-outside", 2, "",
     "/.claude/rules " + LINK_OUTSIDE_ROOT + " (cannot evaluate)"),
    ("standards/regular-file", "standards-file", 2, "", "/.aiqt/standards is not a directory (cannot evaluate)"),
    ("standards/dangling-symlink", "standards-dangling", 2, "",
     "/.aiqt/standards " + LINK_DANGLING + " (cannot evaluate)"),
    ("standards/symlink-to-file", "standards-to-file", 2, "",
     "/.aiqt/standards " + LINK_NOT_A_DIRECTORY + " (cannot evaluate)"),
    ("standards/symlink-outside-root", "standards-outside", 2, "",
     "/.aiqt/standards " + LINK_OUTSIDE_ROOT + " (cannot evaluate)"),
    ("control/no-rules-passes", "no-rules", 0, NO_RULES, None),
    ("control/symlinked-root-passes", "linked", 0, NO_RULES, None),
    ("control/violation-found", "violation", 1, None, None),
    ("control/rules-symlink-in-root-read", "rules-in-root", 1, None, None),
    ("control/standards-symlink-in-root-read", "standards-in-root", 0, NO_RULES, None),
    ("control/repository-passes", "repository", 0, "PASS: rule placement conforms to the taxonomy\n", None),
)
VIOLATION = "  .claude/rules/stray.md: loose: "
# The vectors whose root holds the stray rule, read through a real or a symlinked .claude/rules.
VIOLATING = {"control/violation-found", "control/rules-symlink-in-root-read"}


def _fixtures(base):
    """Build the fixture roots; returns the --root value for each fixture key (a tuple where the vector gives
    --root more than once)."""
    base = Path(base)
    regular = base / "regular-file"
    regular.write_text("not a directory\n", encoding="utf-8")
    no_rules = base / "no-rules"
    no_rules.mkdir()
    linked = base / "linked-root"
    linked.symlink_to(no_rules, target_is_directory=True)
    dangling = base / "dangling-root"
    dangling.symlink_to(base / "absent-target")
    violation = base / "violation"
    (violation / ".claude" / "rules").mkdir(parents=True)
    (violation / ".claude" / "rules" / "stray.md").write_text("# stray\n", encoding="utf-8")
    # Existing roots whose .claude/rules (rules-*) or, with no rules tree, .aiqt/standards (standards-*) is a
    # regular file, a dangling symlink, a symlink to a regular file in the root, a symlink to a directory
    # outside the root (empty, so following it would read as a clean tree), or a symlink to a directory
    # inside the root, which is read: for rules, one holding the stray rule.
    keys = {}
    for kind, parts in (("rules", (".claude", "rules")), ("standards", (".aiqt", "standards"))):
        trees = {shape: base / "{}-{}".format(kind, shape)
                 for shape in ("file", "dangling", "to-file", "outside", "in-root")}
        link = {}
        for shape, tree in trees.items():
            tree.joinpath(*parts[:-1]).mkdir(parents=True)
            keys["{}-{}".format(kind, shape)] = str(tree)
            link[shape] = tree.joinpath(*parts)
        link["file"].write_text("not a directory\n", encoding="utf-8")
        link["dangling"].symlink_to(base / ("absent-" + kind))
        (trees["to-file"] / "target.txt").write_text("not a directory\n", encoding="utf-8")
        link["to-file"].symlink_to(Path("..", "target.txt"))
        (base / ("outside-" + kind)).mkdir()
        link["outside"].symlink_to(base / ("outside-" + kind), target_is_directory=True)
        (trees["in-root"] / "shared" / kind).mkdir(parents=True)
        link["in-root"].symlink_to(Path("..", "shared", kind), target_is_directory=True)
    (base / "rules-in-root" / "shared" / "rules" / "stray.md").write_text("# stray\n", encoding="utf-8")
    keys.update({"missing": str(base / "no-such-root"), "comma-joined": str(base / "no-such-root") + ",also-missing",
                 "dangling": str(dangling), "regular-file": str(regular), "empty": "", "no-rules": str(no_rules),
                 "linked": str(linked), "violation": str(violation), "repository": str(ROOT),
                 "repeated": (str(base / "no-such-root"), str(no_rules))})
    return keys


def _gate(module, argv):
    """Run module.main(argv) in process; returns (exit code, or "crashed (...)", stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = module.main(argv)
    except Exception as exc:
        code = "crashed ({})".format(type(exc).__name__)
    return code, out.getvalue(), err.getvalue()


def _holds(vector, values, code, stdout, stderr, got):
    got_code, got_out, got_err = got
    if got_code != code or (stdout is not None and got_out != stdout):
        return False
    if vector in VIOLATING:
        return VIOLATION in got_out and got_err == ""
    return got_err == "" if stderr is None else stderr.format(*values) in got_err


def _run_vectors(module, roots):
    """Run every vector against module; returns [(vector, ok, (exit code, stdout, stderr))]."""
    results = []
    for vector, key, code, stdout, stderr in VECTORS:
        values = roots[key] if isinstance(roots[key], tuple) else (roots[key],)
        got = _gate(module, [arg for value in values for arg in ("--root", value)])
        results.append((vector, _holds(vector, values, code, stdout, stderr, got), got))
    return results


def _launch(*args):
    result = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).resolve())] + list(args),
                            capture_output=True, text=True, timeout=CHILD_TIMEOUT, cwd=str(ROOT),
                            env=dict(LC_ALL="C.UTF-8"))
    return result.returncode, result.stdout, result.stderr


@contextlib.contextmanager
def _patched(owner, name, replacement):
    original = getattr(owner, name)
    setattr(owner, name, replacement)
    try:
        yield
    finally:
        setattr(owner, name, original)


_ORIGINAL_ROOT_PROBLEM = root_problem
_ORIGINAL_MAP_KEYS = map_keys
_ORIGINAL_DIR_OR_ABSENT = dir_or_absent


def _stat_admitting_errors(path):
    """A stat that reads an unreachable path as a directory: the guard's stat-error refusal removed."""
    try:
        return os.stat(path)
    except OSError:
        return os.stat(ROOT)


def _empty_admitted(root_arg):
    """The guard with its empty-value refusal removed: an empty value is judged as the directory it names."""
    return _ORIGINAL_ROOT_PROBLEM(root_arg or ".")


def _non_directory_admitted(path, root):
    """The probe as it was before non-directories were refused: dir_present, which reads a regular file, a
    dangling symlink or a symlink to a regular file as absent and follows any symlink to a directory."""
    return (dir_present(path), None)


def _reverted(reason, outcome):
    """dir_or_absent with one symlink branch put back: where it gives `reason`, it gives `outcome`."""
    def probe(path, root):
        got = _ORIGINAL_DIR_OR_ABSENT(path, root)
        return outcome if got[1] == reason else got
    return probe


def _link_refused(path, root):
    """dir_or_absent as it was when every symlink was refused, a symlink to a directory inside the root too."""
    got = _ORIGINAL_DIR_OR_ABSENT(path, root)
    return (False, "is a symbolic link, not a directory") if got[0] and os.path.islink(path) else got


def _map_keys_under(probe):
    """map_keys with the dir_or_absent it judges .aiqt/standards by replaced by probe."""
    def mutant(root):
        with _patched(_standards, "dir_or_absent", probe):
            return _ORIGINAL_MAP_KEYS(root)
    return mutant


# The refusals root_problem makes; the repeated-root refusal comes before it and is not among them.
REFUSALS = {"root/nonexistent", "root/comma-joined", "root/dangling-symlink", "root/regular-file", "root/empty"}
# The refusals the .claude/rules and .aiqt/standards probes make on an existing root. Each names the probed
# path, so a --root guard that refused every root (its own message instead) fails them too.
RULES_REFUSALS = {"rules/regular-file", "rules/dangling-symlink", "rules/symlink-to-file",
                  "rules/symlink-outside-root"}
STANDARDS_REFUSALS = {"standards/regular-file", "standards/dangling-symlink", "standards/symlink-to-file",
                      "standards/symlink-outside-root"}
CONTROLS = {"control/no-rules-passes", "control/symlinked-root-passes", "control/violation-found",
            "control/rules-symlink-in-root-read", "control/standards-symlink-in-root-read",
            "control/repository-passes"}
# Each mutant removes one refusal from the --root guard or the .claude/rules or .aiqt/standards probe, puts
# back one symlink branch of that probe, or (the last) makes the --root guard refuse every root, by patching
# one module attribute: (mutant id, attribute, replacement, the exact set of vectors that must then fail).
# The self-test requires each mutant to fail exactly its set, so every vector discriminates and no mutant
# reaches a vector it should not.
MUTANTS = (
    ("guard-removed", "root_problem", lambda root_arg: None, REFUSALS),
    ("stat-error-admitted", "_stat", _stat_admitting_errors,
     {"root/nonexistent", "root/comma-joined", "root/dangling-symlink"}),
    ("non-directory-admitted", "_is_dir", lambda mode: True, {"root/regular-file"}),
    ("empty-admitted", "root_problem", _empty_admitted, {"root/empty"}),
    ("repeat-admitted", "repeat_problem", lambda root_args: None, {"root/repeated"}),
    ("rules-non-directory-admitted", "dir_or_absent", _non_directory_admitted, RULES_REFUSALS),
    ("rules-link-in-root-refused", "dir_or_absent", _link_refused, {"control/rules-symlink-in-root-read"}),
    ("rules-dangling-link-absent", "dir_or_absent", _reverted(LINK_DANGLING, (False, None)),
     {"rules/dangling-symlink"}),
    ("rules-link-to-file-absent", "dir_or_absent", _reverted(LINK_NOT_A_DIRECTORY, (False, None)),
     {"rules/symlink-to-file"}),
    ("rules-link-outside-followed", "dir_or_absent", _reverted(LINK_OUTSIDE_ROOT, (True, None)),
     {"rules/symlink-outside-root"}),
    ("standards-non-directory-admitted", "map_keys", _map_keys_under(_non_directory_admitted),
     STANDARDS_REFUSALS),
    ("standards-link-in-root-refused", "map_keys", _map_keys_under(_link_refused),
     {"control/standards-symlink-in-root-read"}),
    ("standards-dangling-link-absent", "map_keys", _map_keys_under(_reverted(LINK_DANGLING, (False, None))),
     {"standards/dangling-symlink"}),
    ("standards-link-to-file-absent", "map_keys",
     _map_keys_under(_reverted(LINK_NOT_A_DIRECTORY, (False, None))), {"standards/symlink-to-file"}),
    ("standards-link-outside-followed", "map_keys",
     _map_keys_under(_reverted(LINK_OUTSIDE_ROOT, (True, None))), {"standards/symlink-outside-root"}),
    ("every-root-refused", "_is_dir", lambda mode: False, CONTROLS | RULES_REFUSALS | STANDARDS_REFUSALS),
)


def _registered_check_ids():
    """This suite's registered check ids: (set of ids, None), or (None, reason)."""
    try:
        data = tomllib.loads((ROOT / REGISTRY_REL).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, RecursionError,
            MemoryError) as exc:
        return None, "{}: {}".format(REGISTRY_REL, exc)
    suites = data.get("suite")
    for row in suites if isinstance(suites, list) else ():
        if isinstance(row, dict) and row.get("id") == SUITE_ID:
            ids = row.get("expected-check-ids")
            if isinstance(ids, list) and all(isinstance(item, str) for item in ids) and len(set(ids)) == len(ids):
                return set(ids), None
            break
    return None, "{} registers no valid suite {}".format(REGISTRY_REL, SUITE_ID)


def self_test(report_path=None):
    results = []
    executed = []
    if report_path is not None:
        # The execution report is finalized at interpreter exit, after this run's cleanup
        # (tools/_selftest_exit_report.py); nothing writes it in band. Imported only here, so a plain gate
        # run needs no module beyond the ones it already imports.
        import _selftest_exit_report
        _selftest_exit_report.arm(report_path, SUITE_ID, executed)

    def check(check_id, ok, detail=""):
        results.append((check_id, bool(ok), detail))
        executed.append(check_id)

    this = sys.modules[__name__]
    with tempfile.TemporaryDirectory(prefix="rule-placement-selftest-") as base:
        roots = _fixtures(base)
        # 1. every vector holds against this gate
        outcomes = _run_vectors(this, roots)
        failing = [(vector, got) for vector, ok, got in outcomes if not ok]
        check("vector/every-vector-holds", len(outcomes) == len(VECTORS) and not failing, failing)

        # 2. the reported inputs, launched as an operator runs them, now give the fixed result: a missing
        # root, plain or comma-joined, is cannot-evaluate with no PASS line, and the message names the root
        for check_id, key in (("launch/nonexistent-root-cannot-evaluate", "missing"),
                              ("launch/comma-joined-root-cannot-evaluate", "comma-joined")):
            got = _launch("--root", roots[key])
            check(check_id, got[0] == 2 and got[1] == "" and "--root {}: ".format(roots[key]) in got[2], got)

        # 3. every mutant fails exactly its named vectors, and every vector fails under at least one mutant
        exposed, uncaught = set(), []
        for mutant, name, replacement, expected in MUTANTS:
            with _patched(this, name, replacement):
                failed = set(vector for vector, ok, _got in _run_vectors(this, roots) if not ok)
            exposed |= failed
            if failed != expected:
                uncaught.append((mutant, sorted(failed)))
        check("mutant/every-mutant-caught", MUTANTS and not uncaught, uncaught)
        never = sorted(set(vector for vector, *_rest in VECTORS) - exposed)
        check("mutant/every-vector-discriminates", not never, never)

    # 4. the live leg: the default root, launched as both rosters launch the gate, passes on this tree
    got = _launch()
    check("live/default-root-passes", got == (0, "PASS: rule placement conforms to the taxonomy\n", ""), got)

    # the in-run self-guard: the executed set is exactly this suite's registered set
    expected, problem = _registered_check_ids()
    harness = []
    if problem is not None:
        harness.append(("execution-set/registry", problem))
    elif len(set(executed)) != len(executed) or set(executed) != expected:
        harness.append(("execution-set/reconciled", dict(missing=sorted(expected - set(executed)),
                                                         extra=sorted(set(executed) - expected))))

    failed = [(check_id, detail) for check_id, ok, detail in results if not ok] + harness
    if failed:
        print("SELF-TEST FAIL: rule-placement {} of {} check(s) failed".format(len(failed), len(results)))
        for check_id, detail in failed:
            print("  {}: {!r}".format(check_id, detail))
        return 1
    print("SELF-TEST PASS: rule-placement {} checks ({} vectors: a repeated, empty, missing, comma-joined, "
          "dangling or non-directory --root, and a .claude/rules or .aiqt/standards that is a regular file or a "
          "symlink that is dangling, to a regular file or to a directory outside the root, is cannot-evaluate; "
          "an existing root with no rules tree, a symlinked root, a violating root, a .claude/rules or "
          ".aiqt/standards symlinked to a directory inside the root and the repository are still read; {} "
          "mutants each caught exactly, every vector discriminating; the default root passes)".format(
              len(results), len(VECTORS), len(MUTANTS)))
    return 0


if __name__ == "__main__":
    _code = main()
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
