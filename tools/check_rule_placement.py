#!/usr/bin/env python3
"""Placement gate for .claude/rules/ (corpus-management spec section 6, core violation codes).

Validates the read tree's STRUCTURE and NAMING independently of the generator: every rule file lives in a
recognized family with a name that both fits its family's derivation shape AND matches what its frontmatter
derives, none loose or misfiled, the apex is standard, and each vendored external/<source>/ carries its
provenance. Check-only; never writes (the self-test builds fixtures under a tempdir only).
  check_rule_placement.py [--root DIR]   exit 0 clean, 1 violations, 2 usage/read error
  check_rule_placement.py --self-test    assert the probe and gate vectors over tempdir fixtures and the
                                         gate's own --root vectors, each discriminating
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
.claude/rules/.

An existing .claude/rules that cannot be read as a directory (also a FIFO) exits 2: _standards.open_dir
probes it as the three-way dir_present does, refusing a wrong-type entry rather than reading it as absent,
so this gate can never PASS a wrong-type input as "no tree". The probe is bound beneath the gate's root,
so a wrong-type entry, or a symlink that is dangling or whose target is not a directory or resolves
outside the root, at an ANCESTOR below the root (.claude itself, or .aiqt on the standards side) is
refused the same way (exit 2), never followed and never read as absence, while a symlink to a directory
inside the root is read as that directory there too; a genuinely absent component still reads as
absence, but one whose lstat found it and which is gone by its open (removed or renamed in between), or
which is replaced there by another directory (.claude included), exits 2 as a changed tree, never read as
absence and never walked into.

The tree is read only through the descriptor open_dir returns for the .claude/rules it judged
(.claude and then rules each opened beneath its parent's descriptor with O_DIRECTORY|O_NOFOLLOW, and
each one's st_dev and st_ino checked against the probe's lstat of it; a symlink to a directory inside
the root is walked to from the resolved root the same way), never by name: every listing is made
through a held descriptor, and every family directory and vendored source is opened, and every rule
file read, beneath its parent's descriptor with O_NOFOLLOW, or, for an entry that is a symlink to a
directory inside the root, through open_dir beneath the root, which reads it as that directory. So a
.claude/rules swapped for a symlink or another directory between the probe and the listing, or a
.claude replaced by another directory between its lstat and its open, exits 2 naming it, a swap after
the open changes nothing read, and any other symlink at an entry the walk inspects (an entry of the tree
root, of a family directory or of external/, or a vendored source's PROVENANCE.md or LICENSE; a
dot-named entry of the tree root or of a flat family is skipped unread, as is everything else inside a
vendored source) exits 2 naming it, never followed. A rule file that is not a regular file (a FIFO or a
symlink included), in a family directory or loose at the tree root, exits 2 the same way even when its
name is bad: its listed type is judged before its name, and the descriptor it is read through is judged
again (opened without blocking). A dot-named entry is skipped before its type is checked, and the
tree-root metadata names (README.md, PROVENANCE.md) are not type-checked as rule files.

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

import collections
import contextlib
import errno
import importlib.util
import io
import os
import re
import stat
import subprocess
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _selftest_exit_report  # noqa: E402  (the execution report, finalized at interpreter exit)
import _standards  # noqa: E402  (the module itself, so the self-test can patch its probe primitive)
import gen_rules  # noqa: E402  for MAP_KEYS/SEQ_KEYS rebind under --root
from gen_rules import parse_source, parse_source_text, derive  # noqa: E402
from _standards import dir_or_absent, dir_present, map_keys, open_dir  # noqa: E402
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
# The listing primitive, named so the self-test can swap the rules directory between the bound open and
# the listing.
_scandir = os.scandir
# A directory below the held rules directory is opened, and a rule file read, beneath its parent's
# descriptor without following a link; O_NONBLOCK keeps a FIFO at a rule's name from blocking the open
# (the fstat check then refuses it).
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC

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


def check_drift(f, rules, data=None):
    """Return a (code, detail) frontmatter-drift finding for file f, or None. Raises OSError on read.
    data, when given, is f's content already read through a bound descriptor (the gate's walk), parsed
    in place of reading f by name; content that is not UTF-8 is drift either way."""
    try:
        fm = parse_source(f) if data is None else parse_source_text(data.decode("utf-8"), f.name)
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


def _listing(dir_fd, path):
    """The entries of the directory path held at dir_fd, sorted by name, listed through the descriptor,
    each with its lstat taken (and cached by entry.stat) as the listing is made, so the identity
    _open_at checks is the one the listing judged; an entry gone by its lstat raises OSError naming it."""
    with _scandir(dir_fd) as entries:
        listed = sorted(entries, key=lambda entry: entry.name)
    for entry in listed:
        try:
            entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise OSError(exc.errno, exc.strerror, str(path / entry.name)) from exc
    return listed


def _refuse_link(entry, path, root):
    """Whether the listed entry is a symlink to a real directory inside root, which the walk reads as
    that directory (judged by dir_present beneath root, as the probe of .claude/rules judges it); any
    other symlink (dangling, to a non-directory, or resolving outside root) raises OSError naming
    path, never followed."""
    if not entry.is_symlink():
        return False
    try:
        if dir_present(path, root=root):
            return True
    except NotADirectoryError:
        pass
    raise OSError(errno.ELOOP, "a symlink in the rules tree that is not to a directory inside the root, "
                  "which is never followed", str(path))


def _open_dir_entry(dir_fd, path, entry, linked, root):
    """A descriptor for the directory path, listed as entry beneath dir_fd: _open_at's when it is a
    real directory; for a symlink to a directory inside root (linked), open_dir's beneath root, which
    holds the directory it resolves to, and raises OSError naming path when it is gone by then."""
    if not linked:
        return _open_at(dir_fd, path, _DIR_FLAGS, entry)
    fd = open_dir(path, root=root)
    if fd is None:
        raise OSError(errno.ESTALE, "the symlinked directory the listing judged is gone (removed in "
                      "between)", str(path))
    return fd


def _open_at(dir_fd, path, flags, entry):
    """Open path's final component beneath dir_fd with flags (O_NOFOLLOW among them), returning the
    descriptor only when it is the entry the listing judged: its st_dev and st_ino must match entry's
    lstat, taken as the listing was made, so an entry replaced after the listing (by another directory
    or file) closes the descriptor and raises OSError(ESTALE) naming path. Any open error, a symlink
    swapped in after the listing included, raises OSError naming path."""
    listed = entry.stat(follow_symlinks=False)
    try:
        fd = os.open(path.name, flags, dir_fd=dir_fd)
    except OSError as exc:
        raise OSError(exc.errno, exc.strerror, str(path)) from exc
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (listed.st_dev, listed.st_ino):
            raise OSError(errno.ESTALE, "the entry opened is not the one the listing judged (it was "
                          "replaced in between)", str(path))
    except BaseException:
        os.close(fd)
        raise
    return fd


_NOT_REGULAR = "not a regular file where a rule file is required"


def _read_rule(dir_fd, path, entry):
    """The bytes of the rule file path, listed as entry, read beneath dir_fd only when the opened
    descriptor is that entry and fstat on it finds a regular file; anything else raises OSError naming
    path."""
    fd = _open_at(dir_fd, path, _FILE_FLAGS, entry)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, _NOT_REGULAR, str(path))
        with open(fd, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(fd)


def _entry_present(dir_fd, path):
    """Whether path's final component exists beneath dir_fd, judged by lstat; a symlink raises."""
    try:
        st = os.lstat(path.name, dir_fd=dir_fd)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(st.st_mode):
        raise OSError(errno.ELOOP, "a symlink in the rules tree, which is never followed", str(path))
    return True


def _scan_family(family, item, family_fd, rules, report, checked):
    """Check one family directory, held open at family_fd, as _scan_tree does."""
    root = rules.parent.parent
    if family == "external":
        for entry in _listing(family_fd, item):
            sub = item / entry.name
            linked = _refuse_link(entry, sub, root)
            if not (linked or entry.is_dir(follow_symlinks=False)):
                continue
            checked(sub)
            sub_fd = _open_dir_entry(family_fd, sub, entry, linked, root)
            try:
                present = [_entry_present(sub_fd, sub / name) for name in ("PROVENANCE.md", "LICENSE")]
            finally:
                os.close(sub_fd)
            if not all(present):
                report(sub, "missing-provenance", "a vendored source lacks PROVENANCE.md or LICENSE")
        return
    # aiqt and security are FLAT families (spec 6.3): only *.md directly in the family dir. Any nested
    # directory is a nested-dir violation (empty or not); dotfiles and non-md files are out of scope and
    # skipped. A listing (not rglob) surfaces a read error as exit 2.
    for entry in _listing(family_fd, item):
        if entry.name.startswith("."):
            continue
        path = item / entry.name
        if _refuse_link(entry, path, root) or entry.is_dir(follow_symlinks=False):
            report(path, "nested-dir", "a flat family admits no subdirectory")
            continue
        if path.suffix != ".md":
            continue
        checked(path)
        # The listed type is judged before the name, so a rule file that is not a regular file (a
        # FIFO, a socket, a device) raises whatever its name, never reading as only a naming
        # violation; _read_rule judges the opened descriptor again.
        if not entry.is_file(follow_symlinks=False):
            raise OSError(errno.EINVAL, _NOT_REGULAR, str(path))
        nc = check_name(family, entry.name)
        if nc:
            report(path, nc[0], nc[1])
            continue
        drift = check_drift(path, rules, _read_rule(family_fd, path, entry))
        if drift:
            report(path, drift[0], drift[1])


def _scan_tree(rules, rules_fd, report, checked=lambda path: None):
    """Check the rules tree held open at rules_fd (the directory open_dir judged at rules), calling
    report(path, code, detail) for each violation, and checked(path) once for each rule file and
    vendored source it evaluates (conformance C3 counts them). Every listing is made through a held
    descriptor, and every directory it descends into is opened, and every rule file read, beneath its
    parent's descriptor with O_NOFOLLOW, and each opened descriptor's st_dev and st_ino are checked
    against the lstat its listing took (_open_at), so an entry replaced after that lstat raises OSError
    naming it, and the tree read is the one the probe and the listings judged, whatever later happens to
    its names. Residual: an entry replaced between the listing's read of the directory and its lstat of
    that entry is judged as the replacement. An entry of the tree root, of a family directory or of
    external/ that is a symlink to a real directory inside the root (rules is <root>/.claude/rules) is
    read as that directory (_refuse_link), opened through open_dir beneath the root; any other symlink
    at an entry the walk inspects (such an entry, or a vendored source's PROVENANCE.md or LICENSE)
    raises OSError naming it, never followed, as does a rule file that is not a regular file, in a
    family or loose at the tree root, whatever its name (its type is judged before its name). Residual:
    a symlink retargeted between its judgement and that open is read as retargeted, still only to a
    directory inside the root. Dot-named entries of the tree root and of a flat family are skipped
    unread, and nothing else inside a vendored source is inspected."""
    root = rules.parent.parent
    for entry in _listing(rules_fd, rules):
        if entry.name.startswith("."):
            continue
        item = rules / entry.name
        linked = _refuse_link(entry, item, root)
        if entry.is_file(follow_symlinks=False):
            if item.suffix == ".md" and item.name not in METADATA:
                report(item, "loose", "a rule file at the tree root, not on the metadata allowlist")
            continue
        if (item.suffix == ".md" and item.name not in METADATA and not linked
                and not entry.is_dir(follow_symlinks=False)):
            # A loose rule file that is not a regular file raises, as one in a family does, never
            # reading as only an unknown family.
            raise OSError(errno.EINVAL, _NOT_REGULAR, str(item))
        family = entry.name
        if family not in BUILTIN_FAMILIES:
            report(item, "unknown-family", "not a registered family directory")
            continue
        family_fd = _open_dir_entry(rules_fd, item, entry, linked, root)
        try:
            _scan_family(family, item, family_fd, rules, report, checked)
        finally:
            os.close(family_fd)


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
    # dir_or_absent (not is_dir): an unreadable .claude/ parent must fail closed (exit 2), not read as an
    # absent tree and PASS, and so must a .claude/rules that is there but cannot be read as the tree (a
    # regular file, or a symlink that is dangling, whose target is not a directory or resolves outside the
    # root), refused naming the reason as the standards probe above names it: only genuine absence passes.
    # Then open_dir, bound beneath root, judges every component above it the same way (a .claude that is
    # dangling, not a directory or a symlink resolving outside the root is refused, never followed and never
    # read as absence); a symlink to a directory inside the root is read, at rules or above it. The tree is
    # then listed and read only through the descriptor open_dir returns, never by name, so a rules
    # directory swapped for a symlink or another directory after the probe is never what is read.
    try:
        _present, problem = dir_or_absent(rules, root)
        rules_fd = None if problem is not None else open_dir(rules, root=root)
    except OSError as exc:
        print("cannot read {}: {}".format(rules, exc), file=sys.stderr)
        return 2
    if problem is not None:
        print("cannot read {}: {} {} (cannot evaluate)".format(rules, rules, problem), file=sys.stderr)
        return 2
    if rules_fd is None:
        print("PASS: no .claude/rules/ tree")
        return 0
    findings = []

    def report(path, code, detail):
        findings.append("{}: {}: {}".format(path.relative_to(root), code, detail))

    try:
        _scan_tree(rules, rules_fd, report)
    except OSError as exc:
        print("error: cannot read the rules tree: {}".format(exc), file=sys.stderr)
        return 2
    finally:
        os.close(rules_fd)
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
# wrong-type entry (a regular file, a FIFO, a dangling symlink, or a symlink to a regular file where a
# directory is required) is refused with NotADirectoryError rather than read as absent, a symlink to a
# directory inside the root reads present, and an unreachable path still raises OSError; every
# component below the probe's root is walked beneath directory descriptors, so a dangling symlink at an
# ancestor is refused while a symlink to a directory inside the root is read there, a directory swapped
# for a symlink between an ancestor's lstat and its open is refused, and one removed there, or replaced
# there by another directory,
# raises as a changed tree, while a genuinely absent ancestor still reads absent; a path outside the
# root raises; every descriptor the probe opens is closed exactly once; open_dir holds the directory
# it judged (for a symlink to a directory inside the root, the directory it resolves to) and refuses a
# final directory swapped for a symlink, replaced, or removed, between its lstat and its open;
# load_manifests refuses a dangling .aiqt, reads one symlinked to a directory inside the root, and
# reads only through the descriptor it
# holds (a real manifest loads; a symlinked manifest, and a standards dir swapped for a symlink
# after its probe, are refused); the gate exits 0 on
# an absent tree, scans an existing empty directory as a tree, fails a populated tree on its
# violation, reads a .claude/rules, .claude or .aiqt symlinked to a directory inside the root, and
# exits 2 on a wrong-type .claude/rules or .aiqt/standards entry and on a dangling .claude or .aiqt;
# it reads only the rules directory its probe judged, through the held
# descriptor (a swap or a removal before the listing exits 2, as does a .claude replaced by another
# directory before its open, and a swap after the open changes nothing
# read), reads a family directory or vendored source below it symlinked to a directory inside the
# root, and exits 2 on a symlinked rule file or a FIFO rule file (with a bad name or loose at the tree
# root too), never following or blocking on either, and on a family directory or vendored source
# replaced by another directory after its listing, never walked into; each
# listed probe mutant (a scratch copy of _standards.py with one guard reverted, loaded with
# importlib) is caught by its named probe vector, and the placement mutant (a scratch copy of this
# file with the listing identity check dropped) by the family vector. It then proves the gate's own --root guard: every
# --root vector holds, each patched guard mutant fails exactly its named vectors, and the default
# root, launched as both rosters launch the gate, passes on this tree.

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


def _registered_check_ids():
    """This suite's registered check ids: (set of ids, None), or (None, reason)."""
    try:
        data = tomllib.loads((ROOT / REGISTRY_REL).read_text(encoding="utf-8"))
    # ValueError and RecursionError too, as in _standards.load_manifests.
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, RecursionError,
            MemoryError) as exc:
        return None, "{}: {}".format(REGISTRY_REL, exc)
    suites = data.get("suite")
    for row in suites if isinstance(suites, list) else ():
        if isinstance(row, dict) and row.get("id") == SUITE_ID:
            ids = row.get("expected-check-ids")
            if (isinstance(ids, list) and ids and all(isinstance(item, str) and item for item in ids)
                    and len(set(ids)) == len(ids)):
                return set(ids), None
            break
    return None, "{} registers no valid suite {}".format(REGISTRY_REL, SUITE_ID)


@contextlib.contextmanager
def _patched_lstat(module, replacement):
    original = module._lstat
    module._lstat = replacement
    try:
        yield
    finally:
        module._lstat = original


def _deny_lstat(path, dir_fd=None):
    """An lstat refused as an unreadable parent would refuse it, whoever runs the self-test (a
    chmod-based fixture is invisible to root)."""
    raise PermissionError(errno.EACCES, "probe refused by the self-test", str(path))


def _swapping_lstat(real_lstat, top):
    """An lstat that judges top as it is (a real directory), then replaces it with a symlink to its
    renamed self before returning: the window between an ancestor's lstat and its open, made
    deterministic, so only the open's O_NOFOLLOW can refuse the link."""
    def _lstat(name, dir_fd=None):
        st = real_lstat(name, dir_fd=dir_fd)
        if name == top.name:
            moved = top.with_name(top.name + "-real")
            top.rename(moved)
            top.symlink_to(moved)
        return st
    return _lstat


def _replacing_lstat(real_lstat, target, link_to=None, vanish=False):
    """An lstat that judges target as it is (a real directory), then, once, moves it aside and puts at
    its name a symlink to link_to, or with no link_to a fresh empty directory, or with vanish nothing,
    before returning: the window between the probe's judgement of a directory and its open, made
    deterministic, so only the open's O_NOFOLLOW (a link), the opened descriptor's st_dev and st_ino
    check (a directory, at an ancestor or at the final component) or the open's ENOENT, raised as a
    changed tree (nothing), can refuse the replacement."""
    moved = []

    def _lstat(name, dir_fd=None):
        st = real_lstat(name, dir_fd=dir_fd)
        if name == target.name and not moved:
            moved.append(target.rename(target.with_name(target.name + "-judged")))
            if link_to is not None:
                target.symlink_to(link_to)
            elif not vanish:
                target.mkdir()
        return st
    return _lstat


def _swapping_scandir(real_scandir, target, link_to):
    """A listing that, once, moves target aside and puts a symlink to link_to at its name before listing
    the descriptor it is given: a swap after the gate's bound open, which must not change what is read."""
    moved = []

    def _scandir(fd):
        if not moved:
            moved.append(target.rename(target.with_name(target.name + "-judged")))
            target.symlink_to(link_to)
        return real_scandir(fd)
    return _scandir


def _replacing_listing(real_listing, target, fill=()):
    """A walk listing that, once, right after the listing of target's parent, moves target aside and
    puts at its name a fresh directory holding the files named in fill: an entry replaced by another
    real directory after the listing judged it, so only _open_at's st_dev and st_ino check can refuse
    the replacement."""
    moved = []

    def _listing(dir_fd, path):
        listed = real_listing(dir_fd, path)
        if not moved and path == target.parent:
            moved.append(target.rename(target.with_name(target.name + "-judged")))
            target.mkdir()
            for name in fill:
                (target / name).write_text("{}\n".format(name), encoding="utf-8")
        return listed
    return _listing


def _replaced_after_listing(module, base, name, entry, inside, fill):
    """Run module's gate over a fresh root whose .claude/rules/<entry>, a directory holding the files
    named in inside, is replaced by one holding the files named in fill right after the listing that
    judged it; returns ((exit code, stdout, stderr), the replaced path)."""
    root = _root_fixture(base, name, rules="dir").resolve()
    target = root / ".claude" / "rules" / entry
    target.mkdir(parents=True)
    for file_name in inside:
        (target / file_name).write_text("# {}\n".format(file_name), encoding="utf-8")
    with _patched(module, "_listing", _replacing_listing(module._listing, target, fill)):
        got = _gate(module, ["--root", str(root)])
    return got, target


def _refused_naming(got, path):
    """Whether a gate run exited 2 on the rules tree, naming path, with nothing on stdout."""
    code, out, err = got
    return code == 2 and out == "" and "cannot read the rules tree: " in err and "'{}'".format(path) in err


def _probe(module, fixture, paths, root):
    """One outcome under root: for dir_present "present", "absent", "refused" (NotADirectoryError,
    or ELOOP: an O_NOFOLLOW open of a symlink fails ENOTDIR on some kernels and ELOOP on others), or
    "error:<type>" for any other OSError; for a "loader-*" fixture, load_manifests over that fixture
    root's .aiqt/standards, "loaded:<count>" or the same refusal and error outcomes. An
    "ancestor-vanished" fixture is a fresh <top>/sub whose top is removed between its lstat and its
    open, and an "ancestor-replaced" one is replaced there by an empty directory; a "loader-swapped"
    fixture is a fresh root whose empty standards directory is swapped for a symlink to one holding a
    manifest right after the loader's probe judged it."""
    try:
        if fixture == "unreachable":
            with _patched_lstat(module, _deny_lstat):
                present = module.dir_present(paths["dir"], root=root)
        elif fixture in ("ancestor-swapped", "ancestor-vanished", "ancestor-replaced"):
            # A fresh <top>/sub per run: the swap consumes its fixture.
            top = Path(tempfile.mkdtemp(prefix="swap-", dir=str(root)))
            (top / "sub").mkdir()
            swap = (_swapping_lstat(module._lstat, top) if fixture == "ancestor-swapped"
                    else _replacing_lstat(module._lstat, top, vanish=fixture == "ancestor-vanished"))
            with _patched_lstat(module, swap):
                present = module.dir_present(top / "sub", root=root)
        elif fixture == "loader-swapped":
            # A fresh root per run, as above.
            fresh = Path(tempfile.mkdtemp(prefix="loader-swap-", dir=str(root)))
            std = fresh / ".aiqt" / "standards"
            std.mkdir(parents=True)
            with _patched_lstat(module, _replacing_lstat(module._lstat, std, paths["manifests"])):
                loaded = module.load_manifests(std, root=fresh)
            return "loaded:{}".format(len(loaded))
        elif fixture.startswith("loader-"):
            loaded = module.load_manifests(paths[fixture] / ".aiqt" / "standards", root=paths[fixture])
            return "loaded:{}".format(len(loaded))
        else:
            present = module.dir_present(paths[fixture], root=root)
    except NotADirectoryError:
        return "refused"
    except OSError as exc:
        return "refused" if exc.errno == errno.ELOOP else "error:" + type(exc).__name__
    return "present" if present else "absent"


def _held_probe(module, fixture, paths, root):
    """One open_dir outcome under root: "held" when the descriptor returned is the directory now at the
    path, or that a symlink there resolves to (closed here through module.os, so a descriptor count sees it), "absent", "refused" (as for
    _probe), "replaced" (ESTALE: the tree changed between the judgement and the open, so the directory
    opened is not the one judged or the entry judged is gone), or "error:<type>". A "final-swapped",
    "final-replaced" or "final-vanished" fixture is a fresh directory whose final component is swapped
    for a symlink, replaced by another directory, or removed, between its lstat and its open."""
    try:
        if fixture == "unreachable":
            probed = paths["dir"]
            with _patched_lstat(module, _deny_lstat):
                fd = module.open_dir(probed, root=root)
        elif fixture in ("final-swapped", "final-replaced", "final-vanished"):
            # A fresh directory per run: the swap consumes its fixture.
            probed = Path(tempfile.mkdtemp(prefix="held-", dir=str(root)))
            swap = (_swapping_lstat(module._lstat, probed) if fixture == "final-swapped"
                    else _replacing_lstat(module._lstat, probed, vanish=fixture == "final-vanished"))
            with _patched_lstat(module, swap):
                fd = module.open_dir(probed, root=root)
        else:
            probed = paths[fixture]
            fd = module.open_dir(probed, root=root)
    except NotADirectoryError:
        return "refused"
    except OSError as exc:
        if exc.errno == errno.ESTALE:
            return "replaced"
        return "refused" if exc.errno == errno.ELOOP else "error:" + type(exc).__name__
    if fd is None:
        return "absent"
    try:
        same = os.path.samestat(os.fstat(fd), os.stat(probed))
    finally:
        module.os.close(fd)
    return "held" if same else "held-another"


class _CountingOs:
    """Stands in for the os module inside a probe module, holding exactly the names dir_present and
    open_dir read from it when called (their open flags, open, fstat and close; any other name fails
    the vector), and records each descriptor os.open returns and each one os.close is given."""
    O_RDONLY = os.O_RDONLY
    O_DIRECTORY = os.O_DIRECTORY
    O_NOFOLLOW = os.O_NOFOLLOW
    O_CLOEXEC = os.O_CLOEXEC

    def __init__(self):
        self.opened = []
        self.closed = []

    def open(self, *args, **kwargs):
        fd = os.open(*args, **kwargs)
        self.opened.append(fd)
        return fd

    def fstat(self, fd):
        return os.fstat(fd)

    def close(self, fd):
        self.closed.append(fd)
        os.close(fd)


# One fixture per probe path that holds a descriptor when it ends: present, absent, refused at the
# final component and at an ancestor, an error raised after the root is open, and an ancestor gone, or
# replaced by another directory, between its lstat and its open; open_dir also meets a final component
# swapped for a symlink, replaced, or removed, between its lstat and its open.
_SHARED_DESCRIPTOR_FIXTURES = ("dir", "absent-path", "regular-file", "nested-dir", "ancestor-symlink",
                               "ancestor-dangling", "ancestor-absent", "unreachable")
_DESCRIPTOR_FIXTURES = _SHARED_DESCRIPTOR_FIXTURES + ("ancestor-vanished", "ancestor-replaced")
_HELD_DESCRIPTOR_FIXTURES = _SHARED_DESCRIPTOR_FIXTURES + ("final-swapped", "final-replaced", "final-vanished")


def _descriptor_outcome(module, paths, root):
    """Probe every _DESCRIPTOR_FIXTURES entry with dir_present, and every _HELD_DESCRIPTOR_FIXTURES
    entry with open_dir (closing a held descriptor as its caller would), with module.os counting:
    "each-closed-once" when every descriptor opened was closed exactly once, else
    "unbalanced:+<left open>/-<closed unopened>". A descriptor a mutant left open is closed here, so
    the self-test does not keep it."""
    counter = _CountingOs()
    saved = module.os
    module.os = counter
    try:
        for fixture in _DESCRIPTOR_FIXTURES:
            _probe(module, fixture, paths, root)
        for fixture in _HELD_DESCRIPTOR_FIXTURES:
            _held_probe(module, fixture, paths, root)
    finally:
        module.os = saved
    left = collections.Counter(counter.opened) - collections.Counter(counter.closed)
    extra = collections.Counter(counter.closed) - collections.Counter(counter.opened)
    for fd in left:
        os.close(fd)
    if left or extra or not counter.opened:
        return "unbalanced:+{}/-{} of {}".format(sum(left.values()), sum(extra.values()),
                                                 len(counter.opened))
    return "each-closed-once"


# One valid manifest, for the loader fixtures.
_MANIFEST = ('map-key = "map-cur"\nname = "Self-test cur"\npublisher = "AIQT self-test"\n'
             'edition = "2026"\nkind = "control"\nstatus = "stable"\ncitation-unit = "control"\n'
             'id-pattern = "ST[0-9]{2}"\nsource-artefact = "self-test fixture"\nretrieved = "2026-01-01"\n'
             'catalogue = "full"\n[[id]]\ncode = "ST01"\ntitle = "one"\n')


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
    # The rooted-walk fixtures: each is probed with root=base, so the walk inspects every
    # component below base, an ancestor included.
    paths["nested-dir"] = base / "walkdir" / "sub"
    paths["nested-dir"].mkdir(parents=True)
    (base / "walklink").symlink_to(base / "walkdir")
    paths["ancestor-symlink"] = base / "walklink" / "sub"
    (base / "walkdangling").symlink_to(base / "no-such-target")
    paths["ancestor-dangling"] = base / "walkdangling" / "sub"
    paths["ancestor-absent"] = base / "walkmissing" / "sub"
    # The loader fixtures: each is its own root, its .aiqt a dangling link, a live link to a real
    # directory inside that root whose standards/ holds one valid manifest (so reading through the
    # link loads it), or absent.
    for name, kind in (("loader-dangling", "dangling"), ("loader-symlink", "live"), ("loader-absent", None)):
        paths[name] = base / name
        paths[name].mkdir()
        if kind == "dangling":
            (paths[name] / ".aiqt").symlink_to(paths[name] / "no-such-target")
        elif kind == "live":
            (paths[name] / "real-aiqt" / "standards").mkdir(parents=True)
            (paths[name] / "real-aiqt" / "standards" / "cur.toml").write_text(_MANIFEST, encoding="utf-8")
            (paths[name] / ".aiqt").symlink_to(paths[name] / "real-aiqt")
    # A directory holding one valid manifest, outside every loader root: a real standards directory
    # holds a copy, which loads through the held descriptor, and a symlinked manifest (and the
    # swapped standards directory of the "loader-swapped" fixture) links here, so following the
    # link would load it.
    paths["manifests"] = base / "manifests"
    paths["manifests"].mkdir()
    (paths["manifests"] / "cur.toml").write_text(_MANIFEST, encoding="utf-8")
    for name in ("loader-real", "loader-manifest-symlink"):
        paths[name] = base / name
        (paths[name] / ".aiqt" / "standards").mkdir(parents=True)
    (paths["loader-real"] / ".aiqt" / "standards" / "cur.toml").write_text(_MANIFEST, encoding="utf-8")
    (paths["loader-manifest-symlink"] / ".aiqt" / "standards" / "cur.toml").symlink_to(
        paths["manifests"] / "cur.toml")
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


def _load_probe_mutant(source, directory, number):
    """Write one mutated copy of _standards.py to the scratch directory and load it with importlib."""
    path = directory / "standards_mutant_{}.py".format(number)
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("standards_mutant_{}".format(number), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_placement_mutant(source, directory):
    """Write a mutated copy of this file to the scratch directory and load it with importlib, keeping
    sys.path as it was (the copy's import prelude puts its own directory on it)."""
    path = directory / "placement_mutant.py"
    path.write_text(source, encoding="utf-8")
    saved = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location("placement_mutant", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = saved
    return module


NO_RULES ="PASS: no .claude/rules/ tree\n"
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
    """dir_or_absent with every reason dropped: dir_present beneath root, which gives no reason (it raises
    its own NotADirectoryError on an entry it cannot read as a directory) and follows a symlink to a
    directory inside the root, so no refusal names the reason."""
    return (dir_present(path, root=root), None)


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


def self_test(report_path=None):
    if report_path is not None:
        # The execution report is finalized at interpreter exit, after this run's cleanup
        # (tools/_selftest_exit_report.py); nothing writes it in band.
        _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
    this = sys.modules[__name__]
    saved_map_keys, saved_seq_keys = gen_rules.MAP_KEYS, gen_rules.SEQ_KEYS
    with tempfile.TemporaryDirectory(prefix="rule-placement-selftest-") as tmp:
        base = Path(tmp)
        try:
            # 1. a unit vector per probe outcome: present, genuinely absent, each wrong type refused,
            # a symlink to a directory inside the root read as that directory, and an unreachable
            # path still an OSError (never read as absent).
            paths = _probe_paths(base)
            for cid, fixture, want in (
                    ("probe/absent-reads-false", "absent-path", "absent"),
                    ("probe/directory-reads-true", "dir", "present"),
                    ("probe/regular-file-refused", "regular-file", "refused"),
                    ("probe/dangling-symlink-refused", "dangling-symlink", "refused"),
                    ("probe/symlink-to-dir-reads-true", "symlink-to-dir", "present"),
                    ("probe/symlink-to-file-refused", "symlink-to-file", "refused"),
                    ("probe/fifo-refused", "fifo", "refused"),
                    ("probe/unreachable-raises", "unreachable", "error:PermissionError")):
                got = _probe(_standards, fixture, paths, base)
                check(cid, got == want, got)

            # 1b. the walk below the root: every component below the root is inspected the same
            # three-way way, so a dangling symlink is refused at an ancestor too, and a symlink to a
            # directory inside the root is read there too, a genuinely absent ancestor still reads absent, nested real directories still read
            # present, and an ancestor swapped for a symlink between its lstat and its open is
            # refused by the open, never followed, while one removed there, or replaced there by an
            # empty directory (on the st_dev and st_ino check), raises as a changed tree, never read
            # as absent. A path outside the root, or one that
            # climbs with '..', raises rather than being walked.
            for cid, fixture, want in (
                    ("probe/ancestor-symlink-reads-true", "ancestor-symlink", "present"),
                    ("probe/ancestor-dangling-refused", "ancestor-dangling", "refused"),
                    ("probe/ancestor-absent-reads-false", "ancestor-absent", "absent"),
                    ("probe/nested-directory-reads-true", "nested-dir", "present"),
                    ("probe/ancestor-swapped-before-open-refused", "ancestor-swapped", "refused"),
                    ("probe/ancestor-vanished-before-open-raises", "ancestor-vanished", "error:OSError"),
                    ("probe/ancestor-replaced-before-open-raises", "ancestor-replaced", "error:OSError")):
                got = _probe(_standards, fixture, paths, base)
                check(cid, got == want, got)
            for cid, probed in (
                    ("probe/outside-root-raises", paths["dir"]),
                    ("probe/parent-climb-raises", base / "walkdir" / ".." / "realdir")):
                got = _probe(_standards, "probed", {"probed": probed}, base / "walkdir")
                check(cid, got == "error:OSError", got)
            got = _descriptor_outcome(_standards, paths, base)
            check("probe/descriptors-closed-once", got == "each-closed-once", got)

            # 1c. the manifest loader probes its standards dir beneath its root: a dangling .aiqt is
            # refused, never read as an absent (empty) manifest set, a .aiqt symlinked to a directory
            # inside the root is read (its manifest loads), and a genuinely absent .aiqt still loads
            # as empty. It reads only through the
            # descriptor it holds: a real manifest loads, a symlinked manifest is refused, and a
            # standards dir swapped for a symlink to a manifest tree after its probe is refused,
            # where a read by name would follow either link and load the manifest.
            for cid, fixture, want in (
                    ("loader/aiqt-ancestor-dangling-refused", "loader-dangling", "refused"),
                    ("loader/aiqt-ancestor-symlink-reads", "loader-symlink", "loaded:1"),
                    ("loader/absent-standards-reads-empty", "loader-absent", "loaded:0"),
                    ("loader/real-manifest-loads", "loader-real", "loaded:1"),
                    ("loader/manifest-symlink-refused", "loader-manifest-symlink", "refused"),
                    ("loader/standards-swapped-after-probe-refused", "loader-swapped", "refused")):
                got = _probe(_standards, fixture, paths, base)
                check(cid, got == want, got)

            # 1d. open_dir holds the very directory it judged: a real directory is held (the descriptor
            # is that directory), absence reads None, a symlink to a directory inside the root holds
            # the directory it resolves to, and a final directory
            # swapped for a symlink between its lstat and its open is refused by the open, while one
            # replaced by another directory there raises on the st_dev and st_ino check, and one
            # removed there raises as a changed tree, never read as absent; none is held.
            # probe/descriptors-closed-once above counts these opens too.
            for cid, fixture, want in (
                    ("probe/open-dir-holds-directory", "dir", "held"),
                    ("probe/open-dir-absent-reads-none", "absent-path", "absent"),
                    ("probe/open-dir-symlink-holds-target", "symlink-to-dir", "held"),
                    ("probe/open-dir-swapped-before-open-refused", "final-swapped", "refused"),
                    ("probe/open-dir-replaced-before-open-raises", "final-replaced", "replaced"),
                    ("probe/open-dir-vanished-before-open-raises", "final-vanished", "replaced")):
                got = _held_probe(_standards, fixture, paths, base)
                check(cid, got == want, got)

            # 2. the gate over synthetic --root trees: absence still passes, an existing empty
            # directory still reads as a tree (scanned, not "no tree"), as does one symlinked to a
            # directory inside the root, and a wrong-type .claude/rules or .aiqt/standards exits 2.
            for cid, rules_kind, want_code, want_text in (
                    ("gate/absent-tree-passes", "absent", 0, "PASS: no .claude/rules/ tree"),
                    ("gate/empty-directory-reads-tree", "dir", 0,
                     "PASS: rule placement conforms to the taxonomy"),
                    ("gate/regular-file-exits-2", "file", 2, "cannot read"),
                    ("gate/dangling-symlink-exits-2", "dangling", 2, "cannot read"),
                    ("gate/symlink-to-directory-reads-tree", "dirlink", 0,
                     "PASS: rule placement conforms to the taxonomy")):
                root = _root_fixture(base, "gate-" + rules_kind,
                                     rules=None if rules_kind == "absent" else rules_kind)
                code, out, err = _gate(this, ["--root", str(root)])
                check(cid, code == want_code and want_text in out + err, (code, out, err))
            root = _root_fixture(base, "gate-standards-file", rules="dir", standards="file")
            code, out, err = _gate(this, ["--root", str(root)])
            check("gate/standards-non-directory-exits-2", code == 2 and "cannot read" in err,
                  (code, out, err))
            # The empty-directory pass above cannot show the tree was scanned; a populated tree with a
            # known violation can: it must fail, naming the violation.
            root = _root_fixture(base, "gate-populated", rules="dir")
            (root / ".claude" / "rules" / "bogusfamily").mkdir()
            code, out, err = _gate(this, ["--root", str(root)])
            check("gate/populated-tree-violation-fails",
                  code == 1 and ".claude/rules/bogusfamily: unknown-family:" in out.replace(os.sep, "/"),
                  (code, out, err))

            # 2b. a dangling ancestor symlink at .claude or at .aiqt below the gate's --root is
            # refused (exit 2, naming the probed directory), never read as an absent tree or an
            # absent standards dir. A live link to a real sibling directory inside the root that
            # holds the expected child is read as that directory: the empty rules tree is scanned
            # (not "no tree"), and the empty standards dir is read with no rules tree beside it.
            for cid, entry, live in (
                    ("gate/claude-ancestor-symlink-reads-tree", ".claude", True),
                    ("gate/claude-ancestor-dangling-exits-2", ".claude", False),
                    ("gate/aiqt-ancestor-symlink-reads", ".aiqt", True),
                    ("gate/aiqt-ancestor-dangling-exits-2", ".aiqt", False)):
                child = "rules" if entry == ".claude" else "standards"
                root = base / "gate-{}-{}".format(entry.lstrip("."), "live" if live else "dangling")
                root.mkdir()
                if live:
                    target = root / ("linked-" + entry.lstrip("."))
                    (target / child).mkdir(parents=True)
                    (root / entry).symlink_to(target)
                else:
                    (root / entry).symlink_to(root / "no-such-target")
                probed = root.resolve() / entry / child
                code, out, err = _gate(this, ["--root", str(root)])
                if not live:
                    ok = code == 2 and "cannot read {}".format(probed) in err
                elif entry == ".claude":
                    ok = (code, out, err) == (0, "PASS: rule placement conforms to the taxonomy\n", "")
                else:
                    ok = (code, out, err) == (0, NO_RULES, "")
                check(cid, ok, (code, out, err))

            # 2c. the gate reads only the directory its probe judged, through the held descriptor. A
            # .claude/rules holding a violation is swapped for a symlink to a clean tree, or replaced
            # by a clean directory, right after the probe's lstat of it (a hook on the probe's lstat):
            # the gate exits 2 naming it, where a listing by name would read the clean tree and pass;
            # removed there instead, it exits 2 the same way as a changed tree, never read as an
            # absent tree that passes. Its ancestor .claude replaced by an empty directory right after
            # the probe's lstat of it exits 2 naming .claude, where the walk would go on inside the
            # replacement, find no rules there, and pass as no tree. Swapped after the bound open (a
            # hook on the listing), the judged directory is still what is read, so its violation is
            # found.
            for cid, swap in (
                    ("gate/rules-swapped-before-listing-exits-2", "link"),
                    ("gate/rules-replaced-before-listing-exits-2", "directory"),
                    ("gate/rules-vanished-before-open-exits-2", "vanish"),
                    ("gate/claude-replaced-before-open-exits-2", "ancestor"),
                    ("gate/rules-swapped-after-open-reads-judged", "after-open")):
                root = _root_fixture(base, "gate-swap-" + swap, rules="dir").resolve()
                rules = root / ".claude" / "rules"
                (rules / "stray.md").write_text("# stray\n", encoding="utf-8")
                clean = root / "clean-rules"
                clean.mkdir()
                if swap == "after-open":
                    hook = _patched(this, "_scandir", _swapping_scandir(os.scandir, rules, clean))
                elif swap == "ancestor":
                    hook = _patched_lstat(_standards, _replacing_lstat(_standards._lstat, rules.parent))
                else:
                    hook = _patched_lstat(_standards, _replacing_lstat(
                        _standards._lstat, rules, clean if swap == "link" else None,
                        vanish=swap == "vanish"))
                with hook:
                    code, out, err = _gate(this, ["--root", str(root)])
                if swap == "after-open":
                    ok = code == 1 and VIOLATION in out.replace(os.sep, "/") and err == ""
                else:
                    ok = code == 2 and out == "" and "cannot read {}: ".format(rules) in err
                    if swap == "ancestor":
                        ok = ok and "'{}'".format(rules.parent) in err
                check(cid, ok, (code, out, err))

            # 2d. a rule file below .claude/rules that is a symlink is refused (exit 2 naming it),
            # never followed, though its target is clean and following it would pass. A FIFO where a rule file is expected exits 2 the same way, never
            # blocking the read, also when its name is bad or it is loose at the tree root (its type
            # is judged before its name, so it never reads as only a placement violation); and a
            # rule read through its descriptor is still parsed, so its frontmatter drift is found.
            rule = ("---\ncorpus-id: abcdef1\norigin: adopter\nfamily: aiqt\nslug: sample\ntier: 10\n"
                    "facet: ACCUR\n---\n")
            for cid, entry, kind in (
                    ("gate/rule-file-symlink-exits-2", "aiqt/10-ACCUR-sample.md", "rule"),
                    ("gate/rule-file-fifo-exits-2", "aiqt/10-ACCUR-sample.md", "fifo"),
                    ("gate/rule-file-fifo-bad-name-exits-2", "aiqt/bad.md", "fifo"),
                    ("gate/loose-rule-fifo-exits-2", "stray.md", "fifo")):
                root = _root_fixture(base, "gate-" + cid.split("/")[1], rules="dir").resolve()
                linked = root / ".claude" / "rules" / entry
                linked.parent.mkdir(exist_ok=True)
                target = root / "outside"
                if kind == "rule":
                    target.write_text(rule, encoding="utf-8")
                if kind == "fifo":
                    os.mkfifo(linked)
                else:
                    linked.symlink_to(target)
                code, out, err = _gate(this, ["--root", str(root)])
                check(cid, code == 2 and out == "" and "cannot read the rules tree: " in err
                      and "'{}'".format(linked) in err, (code, out, err))
            # A family directory or a vendored source below .claude/rules that is a symlink to a
            # directory inside the root is read as that directory: a badly named rule in the family
            # it names, and a vendored source lacking its LICENSE, are each found (exit 1, named by
            # the path through the link), where a walk that refused or skipped the link would not.
            for cid, entry, inside, finding in (
                    ("gate/family-symlink-reads-family", "aiqt", ("bad.md",),
                     ".claude/rules/aiqt/bad.md: bad-name: "),
                    ("gate/vendored-source-symlink-reads-source", "external/vendor", ("PROVENANCE.md",),
                     ".claude/rules/external/vendor: missing-provenance: ")):
                root = _root_fixture(base, "gate-" + cid.split("/")[1], rules="dir").resolve()
                linked = root / ".claude" / "rules" / entry
                linked.parent.mkdir(exist_ok=True)
                target = root / "linked-target"
                target.mkdir()
                for name in inside:
                    (target / name).write_text("{}\n".format(name), encoding="utf-8")
                linked.symlink_to(target)
                code, out, err = _gate(this, ["--root", str(root)])
                check(cid, code == 1 and finding in out.replace(os.sep, "/") and err == "", (code, out, err))
            root = _root_fixture(base, "gate-rule-drift", rules="dir")
            (root / ".claude" / "rules" / "aiqt").mkdir()
            (root / ".claude" / "rules" / "aiqt" / "10-ACCUR-other.md").write_text(rule, encoding="utf-8")
            code, out, err = _gate(this, ["--root", str(root)])
            check("gate/rule-drift-fails",
                  code == 1 and (".claude/rules/aiqt/10-ACCUR-other.md: frontmatter-drift: derives "
                                 "aiqt/10-ACCUR-sample.md") in out.replace(os.sep, "/"), (code, out, err))

            # 2e. a family directory holding a badly named rule, or a vendored source lacking its
            # provenance, replaced by another real directory right after the listing that judged it (a
            # hook on the walk's listing) exits 2 naming it, where a walk into the replacement (an
            # empty family, or a source holding PROVENANCE.md and LICENSE) would find nothing and pass.
            for cid, entry, inside, fill in (
                    ("gate/family-replaced-after-listing-exits-2", "aiqt", ("bad.md",), ()),
                    ("gate/vendored-source-replaced-after-listing-exits-2", "external/vendor", (),
                     ("PROVENANCE.md", "LICENSE"))):
                got, target = _replaced_after_listing(this, base, "gate-" + cid.split("/")[1], entry,
                                                      inside, fill)
                check(cid, _refused_naming(got, target), got)

            # 3. each probe mutant (one guard reverted in a scratch copy of _standards.py) must be
            # caught by its named probe vector: the mutant's outcome differs from the intact one.
            # The walk mutant judges only the final component beneath its followed parent, the
            # semantics of a probe without a root; the loader mutant probes beneath the standards
            # dir's own parent, which is that same form at the loader. The ancestor identity mutant
            # checks st_dev and st_ino only at the final component, so an ancestor replaced by
            # another directory is walked into; the two held mutants drop that check at the held
            # final component only, or leave it unopened; the vanished mutant reads an entry gone
            # between its lstat and its open as absence.
            source = (Path(__file__).resolve().parent / "_standards.py").read_text(encoding="utf-8")
            mutant_dir = base / "mutants"
            mutant_dir.mkdir()
            number = 0
            for cid, old, new, fixture, want in (
                    ("mutant/stat-follows-links-caught", "st = _lstat(name, dir_fd=fds[-1])",
                     "st = os.stat(name, dir_fd=fds[-1])", "dangling-symlink", "refused"),
                    ("mutant/wrong-type-reads-absent-caught",
                     "raise NotADirectoryError(\n                        errno.ENOTDIR,",
                     "return False and NotADirectoryError(\n                        errno.ENOTDIR,",
                     "regular-file", "refused"),
                    ("mutant/eacces-reads-absent-caught", "except FileNotFoundError:\n            return None",
                     "except OSError:\n            return None", "unreachable", "error:PermissionError"),
                    ("mutant/ancestor-walk-skipped-caught", "base = Path(root)",
                     "base = path.parent", "ancestor-dangling", "refused"),
                    ("mutant/nofollow-dropped-caught",
                     "os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC",
                     "os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC", "ancestor-swapped", "refused"),
                    ("mutant/descriptor-close-skipped-caught", "os.close(fd)", "pass", "descriptors",
                     "each-closed-once"),
                    ("mutant/loader-unrooted-caught", "std_fd = open_dir(std_dir, root=root, search_only",
                     "std_fd = open_dir(std_dir, root=std_dir.parent, search_only", "loader-dangling",
                     "refused"),
                    ("mutant/ancestor-identity-unchecked-caught",
                     "if (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino):",
                     "if i + 1 == len(parts) and (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino):",
                     "ancestor-replaced", "error:OSError"),
                    ("mutant/held-identity-unchecked-caught",
                     "if (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino):",
                     "if i + 1 < len(parts) and (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino):",
                     "final-replaced", "replaced"),
                    ("mutant/held-final-unopened-caught", "if hold or i + 1 < len(parts):",
                     "if i + 1 < len(parts):", "dir", "held"),
                    ("mutant/vanished-reads-absent-caught",
                     'raise OSError(errno.ESTALE, "the entry the probe judged is gone',
                     'return None and OSError(errno.ESTALE, "the entry the probe judged is gone',
                     "ancestor-vanished", "error:OSError")):
                number += 1
                if source.count(old) != 1:
                    caught, got = False, "mutation anchor found {} times".format(source.count(old))
                else:
                    mutant = _load_probe_mutant(source.replace(old, new), mutant_dir, number)
                    if fixture == "descriptors":
                        got = _descriptor_outcome(mutant, paths, base)
                    elif cid.startswith("mutant/held-"):
                        got = _held_probe(mutant, fixture, paths, base)
                    else:
                        got = _probe(mutant, fixture, paths, base)
                    caught = got != want
                check(cid, caught, got)

            # 3b. the placement mutant (a scratch copy of this file with _open_at's st_dev and st_ino
            # comparison dropped) walks into a family directory replaced after the listing and passes,
            # so the family vector above must tell it from the intact gate. The anchor is joined here
            # from two literals so this file holds it exactly once.
            own = Path(__file__).resolve().read_text(encoding="utf-8")
            old = "if (opened.st_dev, opened.st_ino) != " + "(listed.st_dev, listed.st_ino):"
            if own.count(old) != 1:
                caught, got = False, "mutation anchor found {} times".format(own.count(old))
            else:
                mutant = _load_placement_mutant(own.replace(old, "if False and " + old[3:]), mutant_dir)
                got, target = _replaced_after_listing(mutant, base, "gate-mutant-family-replaced", "aiqt",
                                                      ("bad.md",), ())
                caught = not _refused_naming(got, target)
            check("mutant/listing-identity-unchecked-caught", caught, got)

            # 4. every --root vector holds against this gate
            roots = _fixtures(base)
            outcomes = _run_vectors(this, roots)
            failing = [(vector, got) for vector, ok, got in outcomes if not ok]
            check("vector/every-vector-holds", len(outcomes) == len(VECTORS) and not failing, failing)

            # 5. the reported inputs, launched as an operator runs them, now give the fixed result: a
            # missing root, plain or comma-joined, is cannot-evaluate with no PASS line, and the message
            # names the root
            for check_id, key in (("launch/nonexistent-root-cannot-evaluate", "missing"),
                                  ("launch/comma-joined-root-cannot-evaluate", "comma-joined")):
                got = _launch("--root", roots[key])
                check(check_id, got[0] == 2 and got[1] == "" and "--root {}: ".format(roots[key]) in got[2],
                      got)

            # 6. every --root guard mutant fails exactly its named vectors, and every vector fails under
            # at least one mutant
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
        finally:
            gen_rules.MAP_KEYS, gen_rules.SEQ_KEYS = saved_map_keys, saved_seq_keys

    # 7. the live leg: the default root, launched as both rosters launch the gate, passes on this tree
    got = _launch()
    check("live/default-root-passes", got == (0, "PASS: rule placement conforms to the taxonomy\n", ""), got)

    # the in-run self-guard: the executed set is exactly this suite's registered set
    expected, problem = _registered_check_ids()
    if problem is not None:
        print("SELF-TEST HARNESS ERROR: {}".format(problem), file=sys.stderr)
        return 2
    for check_id in sorted(expected - _EXECUTED_SET):
        FAILURES.append("execution-set/missing: {}".format(check_id))
    for check_id in sorted(_EXECUTED_SET - expected):
        FAILURES.append("execution-set/extra: {}".format(check_id))
    if FAILURES:
        print("SELF-TEST FAIL: check_rule_placement {} of {} check(s) failed".format(len(FAILURES),
                                                                                    len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_rule_placement {} checks (dir_present reads a real directory as "
          "present and genuine absence as absent, refuses a regular file, a FIFO, a dangling symlink "
          "and a symlink to a regular file with NotADirectoryError, reads a symlink to a directory "
          "inside its root as that directory, raises OSError on an unreachable path, and walks every "
          "component below its root, refusing a dangling symlink at an ancestor and reading one to a "
          "directory inside the root there, refusing a directory swapped for a symlink "
          "between its lstat and its open, and raising on one removed or replaced by another "
          "directory there, while a genuinely absent "
          "ancestor still reads absent; it raises on a path outside its root or climbing with '..', "
          "and closes every descriptor it opens exactly once; open_dir holds the directory it judged, "
          "or the one a symlink inside the root resolves to, "
          "and refuses one swapped for a symlink, replaced or removed between its lstat and its open; "
          "load_manifests refuses a dangling .aiqt, reads one symlinked inside the root, loads an "
          "absent one as empty, "
          "loads a real manifest through the descriptor it holds, and refuses a symlinked manifest "
          "and a standards dir swapped for a symlink after its probe; the gate passes an absent tree, "
          "scans an existing empty directory as a tree, also one symlinked inside the root, fails a "
          "populated tree on its violation, reads a .claude or .aiqt symlinked inside the root, and "
          "exits 2 on a wrong-type rules tree or standards dir and on a dangling .claude "
          "or .aiqt; it reads only the rules directory its probe judged, exiting 2 on one swapped for "
          "a symlink, replaced or removed before the listing, and on a .claude replaced before its "
          "open, and still finding the violation of one "
          "swapped after its open, reads a family directory or vendored source symlinked inside the "
          "root, and exits 2 on a symlinked rule file "
          "and on a FIFO rule file, with a bad name or loose at the tree root too, and on a "
          "family directory or vendored source replaced by another directory after its listing, "
          "while still finding a rule's frontmatter drift; 11 probe mutants and a placement mutant "
          "each caught; {} --root vectors: a repeated, empty, missing, comma-joined, dangling or "
          "non-directory --root, and a .claude/rules or .aiqt/standards that is a regular file or a "
          "symlink that is dangling, to a regular file or to a directory outside the root, is "
          "cannot-evaluate; an existing root with no rules tree, a symlinked root, a violating root, a "
          ".claude/rules or .aiqt/standards symlinked to a directory inside the root and the repository "
          "are still read; {} --root guard and probe mutants each "
          "caught exactly, every vector discriminating; the default root passes); execution set "
          "reconciled against tools/selftest_checks.toml".format(len(EXECUTED), len(VECTORS), len(MUTANTS)))
    return 0


if __name__ == "__main__":
    _selftest_exit_report.exit_with(main())
