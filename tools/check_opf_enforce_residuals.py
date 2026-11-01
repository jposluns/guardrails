#!/usr/bin/env python3
"""OPF enforcement-pack residual register gate: every residual disclosed, and disclosed where it is named.

OPF-SPEC 14.1 requires the enforcement pack to disclose the residuals it does not eliminate. The pack's
register of where each member discloses each residual is opf/enforcement/residuals.toml. This gate holds
the register, its header and the pack to these rules:

  rows        exactly one [[residual]] row per token of ENFORCEMENT_RESIDUALS (opf/tools/_opf_adopt.py,
              read statically), in that order. The document carries exactly format-version and the rows,
              the format-version is the exact integer 1 (a boolean true or a float 1.0 is refused), and
              each row carries exactly id, spec, members, anchors and statement: spec and statement
              non-empty strings, members a non-empty list of unique contained paths, anchors a table whose
              keys are exactly the row's members and whose values are non-empty strings.
  members     each member is a regular file of the pack (opf/enforcement/) reached through no symlink,
              and never one of the two registers (residuals.toml, platforms.toml), which are not members.
  anchors     each anchor occurs in its member verbatim. The register states no other normalisation
              ("a phrase that member contains verbatim"), so none is applied: a respaced, rewrapped or
              re-cased anchor is a finding.
  coverage    every regular file of the pack except the registers is named by at least one row, except
              the members of UNNAMED_MEMBERS. Each such exception must be a regular pack file that no row
              names; a stale exception is a finding. A pack entry that is a symlink or not a regular file
              is a finding.
  locators    every anchor is a disclosure except the entries of LOCATOR_ANCHORS: (row, member) to the
              exact anchor that row gives that member. A locator entry whose row does not name that
              member with that anchor is a finding, and so is a row whose anchors are all locators.
  header      the register's header (its leading comment lines, read with each line's comment marker
              removed and every whitespace run as one space) is held to both lists, both ways: exactly one
              sentence "Every pack member [except ...] is named by at least one row" whose except list is
              UNNAMED_MEMBERS; the sentences 'the MEMBER anchor ("ANCHOR") is a locator, not a disclosure'
              are exactly the members and anchors of LOCATOR_ANCHORS, and "is a locator" appears nowhere
              else; and the header carries each entry's stated reason phrase and each locator's row id.
  means       for each means of ENFORCEMENT_REQUIRED_RESIDUALS, the row of each residual that means
              requires lists every member of that means: the ci and pre-commit members are the
              ENFORCEMENT_LAUNCHERS members of the platform carrying that one means (ENFORCEMENT_MEANS),
              and an assistant platform's member is its platforms.toml row's member under that row's means.

  check_opf_enforce_residuals.py              check the real tree
  check_opf_enforce_residuals.py --self-test  fixture copies of the inputs; every vector has a flip, a
              neighbouring input that must reverse its status, and both are observed.

Exit convention (the repository's gates): 0 clean, 1 a finding, 2 cannot evaluate (an unreadable,
oversized or malformed input, a vocabulary literal that cannot be read).

DISCLOSED RESIDUALS. The gate cannot tell a disclosure from a locator, or judge whether an anchor's
member really discloses the residual: it checks that each anchor occurs, and holds the declared locators
and unnamed members to its two lists and the header. The spec field is not compared with
opf/spec/OPF-SPEC.md, and the statement is not compared with the members. The header reading is lexical:
it reads the listed sentence forms, not meaning. platforms.toml is read only for each row's platform,
means and member; tools/check_opf_enforce_platforms.py validates it. Inputs are read after a
symlinked-parent check and a regular-file check, so a file swapped between those checks and the read is a
check-to-use race.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_opf_enforce_residuals.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import os
import re
import shutil
import stat
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACK_REL = "opf/enforcement"
REGISTER_REL = PACK_REL + "/residuals.toml"
ROSTER_REL = PACK_REL + "/platforms.toml"
ADOPT_REL = "opf/tools/_opf_adopt.py"
REGISTERS = frozenset(("residuals.toml", "platforms.toml"))
MAX_INPUT_BYTES = 4 * 1024 * 1024

ROW_KEYS = frozenset(("id", "spec", "members", "anchors", "statement"))
# The pack files no row names, each with the phrase the register header gives as its reason.
UNNAMED_MEMBERS = {
    "claude/settings.opf.json": "carries no prose and so discloses nothing",
}
# The anchors that locate a member rather than disclose a residual: (row id, member) to (the exact anchor,
# the phrase the register header gives as the reason).
LOCATOR_ANCHORS = {
    ("server-side-protection-adopter-attested", "ci/github-actions.yml"):
        ("branches: [main]", "says nothing about server-side protection"),
}
COVERAGE_RE = re.compile(r"Every pack member(?: except (.+?))? is named by at least one row")
LOCATOR_RE = re.compile(r'the (\S+) anchor \("([^"]*)"\) is a locator, not a disclosure')


class CannotEvaluate(Exception):
    """An input this gate cannot read or interpret (exit 2)."""


def _input(root, rel, label):
    """The path of the input `rel` under `root`, refused when a component on the way is a symlink."""
    parts = rel.split("/")
    path = os.path.join(str(root), *parts)
    expected = os.path.join(os.path.realpath(str(root)), *parts)
    try:
        actual = os.path.realpath(path)
    except (OSError, ValueError) as exc:
        raise CannotEvaluate("%s %s cannot be resolved (%r)" % (label, path, exc))
    if actual != expected:
        raise CannotEvaluate("%s %s is reached through a symlink (it resolves to %s, not %s), so its "
                             "bytes are not the tree's own" % (label, path, actual, expected))
    return path


def _text(path, label):
    """The UTF-8 text of a regular input file of at most MAX_INPUT_BYTES, or CannotEvaluate naming it."""
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise CannotEvaluate("%s %s cannot be read (%s)" % (label, path, exc))
    if not stat.S_ISREG(st.st_mode):
        raise CannotEvaluate("%s %s is not a regular file" % (label, path))
    if st.st_size > MAX_INPUT_BYTES:
        raise CannotEvaluate("%s %s exceeds %d bytes" % (label, path, MAX_INPUT_BYTES))
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise CannotEvaluate("%s %s cannot be read (%s)" % (label, path, exc))
    if len(data) > MAX_INPUT_BYTES:
        raise CannotEvaluate("%s %s exceeds %d bytes" % (label, path, MAX_INPUT_BYTES))
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("%s %s is not UTF-8 (%s)" % (label, path, exc))


def _toml(root, rel, label):
    try:
        return tomllib.loads(_text(_input(root, rel, label), label))
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("%s does not parse (%s)" % (rel, exc))


def _literals(path, names):
    """The module-level literal values of `names` in the Python file at `path`, read statically (parsed,
    never imported or run). Each name must be assigned exactly once, to a literal."""
    try:
        tree = ast.parse(_text(path, "the vocabulary source"), filename=str(path))
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("%s does not parse (%s)" % (path, exc))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in names:
                if name in found:
                    raise CannotEvaluate("%s assigns %s more than once" % (path, name))
                try:
                    found[name] = ast.literal_eval(node.value)
                except (ValueError, TypeError, SyntaxError, RecursionError, MemoryError) as exc:
                    raise CannotEvaluate("%s: %s is not a literal (%s)" % (path, name, exc))
    missing = sorted(set(names) - set(found))
    if missing:
        raise CannotEvaluate("%s carries no literal %s" % (path, ", ".join(missing)))
    return found


def _strings(value):
    return isinstance(value, tuple) and all(isinstance(v, str) for v in value)


def _vocabulary(root):
    adopt = _literals(_input(root, ADOPT_REL, "the vocabulary source"),
                      ("ENFORCEMENT_MEANS", "ENFORCEMENT_RESIDUALS", "ENFORCEMENT_REQUIRED_RESIDUALS",
                       "ENFORCEMENT_LAUNCHERS"))
    means = adopt["ENFORCEMENT_MEANS"]
    if not isinstance(means, dict) or not all(isinstance(k, str) and _strings(v) for k, v in means.items()):
        raise CannotEvaluate("%s ENFORCEMENT_MEANS is not a table of string tuples" % (ADOPT_REL,))
    residuals = adopt["ENFORCEMENT_RESIDUALS"]
    if not _strings(residuals) or not residuals or len(set(residuals)) != len(residuals):
        raise CannotEvaluate("%s ENFORCEMENT_RESIDUALS is not a tuple of unique strings" % (ADOPT_REL,))
    required = adopt["ENFORCEMENT_REQUIRED_RESIDUALS"]
    if not isinstance(required, dict) or not all(isinstance(k, str) and _strings(v) for k, v in required.items()):
        raise CannotEvaluate("%s ENFORCEMENT_REQUIRED_RESIDUALS is not a table of string tuples" % (ADOPT_REL,))
    launchers = adopt["ENFORCEMENT_LAUNCHERS"]
    if not isinstance(launchers, tuple) or not all(_strings(row) and len(row) >= 2 for row in launchers):
        raise CannotEvaluate("%s ENFORCEMENT_LAUNCHERS is not a tuple of string rows" % (ADOPT_REL,))
    members = {}
    for row in launchers:
        platform, member = row[0], row[1]
        if len(means.get(platform, ())) != 1 or not member.startswith("enforcement/"):
            raise CannotEvaluate("%s ENFORCEMENT_LAUNCHERS row %r does not name a pack member of a platform "
                                 "with exactly one means" % (ADOPT_REL, row[:2]))
        members.setdefault(means[platform][0], set()).add(member[len("enforcement/"):])
    roster = _toml(root, ROSTER_REL, "the platform roster")
    rows = roster.get("platform")
    if not isinstance(rows, list):
        raise CannotEvaluate("%s carries no [[platform]] rows" % (ROSTER_REL,))
    for row in rows:
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ("platform", "means", "member")):
            raise CannotEvaluate("%s carries a row without string platform, means and member" % (ROSTER_REL,))
        members.setdefault(row["means"], set()).add(row["member"])
    return dict(residuals=residuals, required=required, members=members)


def _contained(rel):
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel:
        return False
    return all(p not in ("", ".", "..") for p in rel.split("/")) and not any(ord(c) < 0x20 for c in rel)


def _member_problem(root, rel):
    """None when `rel` is a regular pack file reached through no symlink, else what it is instead."""
    if not _contained(rel):
        return "is not a contained relative path"
    if rel in REGISTERS:
        return "is a register, not a pack member"
    parts = (PACK_REL + "/" + rel).split("/")
    path = os.path.join(str(root), *parts)
    try:
        if os.path.realpath(path) != os.path.join(os.path.realpath(str(root)), *parts):
            return "is reached through a symlink"
        st = os.lstat(path)
    except (OSError, ValueError):
        return "is not a regular pack file (absent or unreadable)"
    if not stat.S_ISREG(st.st_mode):
        return "is not a regular pack file"
    return None


def _pack_files(root):
    """(the regular pack files other than the registers, relative to the pack; findings for every other
    pack entry: a symlink or a file that is not regular)."""
    base = _input(root, PACK_REL, "the enforcement pack")
    files, findings = set(), []

    def refuse(exc):
        raise CannotEvaluate("the enforcement pack %s cannot be listed (%s)" % (base, exc))

    for top, dirs, names in os.walk(base, onerror=refuse, followlinks=False):
        rel_top = os.path.relpath(top, base).replace(os.sep, "/")
        for name in sorted(dirs + names):
            rel = name if rel_top == "." else rel_top + "/" + name
            try:
                st = os.lstat(os.path.join(top, name))
            except OSError as exc:
                raise CannotEvaluate("the pack entry %s cannot be read (%s)" % (rel, exc))
            if stat.S_ISDIR(st.st_mode):
                continue
            if not stat.S_ISREG(st.st_mode):
                findings.append("%s/%s: a pack entry that is a symlink or not a regular file" % (PACK_REL, rel))
            elif rel not in REGISTERS:
                files.add(rel)
    return files, findings


def _header(text):
    """The register's leading comment lines, each without its comment marker, every whitespace run as one
    space."""
    lines = []
    for line in text.split("\n"):
        if not line.startswith("#"):
            break
        lines.append(line[1:])
    return " ".join(" ".join(lines).split())


def _listed(text):
    """The paths of an "except" list: "a", "a and b", "a, b and c"."""
    return set(p.strip() for p in re.split(r",\s*|\s+and\s+", text) if p.strip())


def _header_findings(header, unnamed, locators):
    where = REGISTER_REL + " header"
    findings = []
    coverage = COVERAGE_RE.findall(header)
    if len(coverage) != 1:
        findings.append("%s: carries %d sentences 'Every pack member [except ...] is named by at least one "
                        "row', not exactly one" % (where, len(coverage)))
    else:
        stated = _listed(coverage[0]) if coverage[0] else set()
        for rel in sorted(stated - set(unnamed)):
            findings.append("%s: excepts %s from coverage, which this gate's UNNAMED_MEMBERS does not "
                            "carry" % (where, rel))
        for rel in sorted(set(unnamed) - stated):
            findings.append("%s: does not except %s from coverage, though this gate's UNNAMED_MEMBERS "
                            "carries it" % (where, rel))
    for rel, reason in sorted(unnamed.items()):
        if reason not in header:
            findings.append("%s: does not give the reason %r for the unnamed member %s" % (where, reason, rel))
    claimed = LOCATOR_RE.findall(header)
    declared = set((member, anchor) for (row, member), (anchor, reason) in locators.items())
    for member, anchor in sorted(set(claimed) - declared):
        findings.append("%s: names the %s anchor %r a locator, which this gate's LOCATOR_ANCHORS does not "
                        "carry" % (where, member, anchor))
    for member, anchor in sorted(declared - set(claimed)):
        findings.append("%s: does not name the %s anchor %r a locator, though this gate's LOCATOR_ANCHORS "
                        "carries it" % (where, member, anchor))
    if header.count("is a locator") != len(claimed):
        findings.append("%s: says 'is a locator' %d times but states %d locator sentences of the form "
                        "'the MEMBER anchor (\"ANCHOR\") is a locator, not a disclosure'"
                        % (where, header.count("is a locator"), len(claimed)))
    for (row, member), (anchor, reason) in sorted(locators.items()):
        if row not in header:
            findings.append("%s: does not name the row %s of the locator %s" % (where, row, member))
        if reason not in header:
            findings.append("%s: does not give the reason %r for the locator %s" % (where, reason, member))
    return findings


def _row_findings(root, where, row, texts):
    """The findings for one row with exactly the row keys; `texts` caches member text."""
    findings = []
    for key in ("spec", "statement"):
        if not isinstance(row[key], str) or not row[key].strip():
            findings.append("%s: %s must be a non-empty string" % (where, key))
    members, anchors = row["members"], row["anchors"]
    if not isinstance(members, list) or not members or not all(isinstance(m, str) for m in members) \
            or len(set(members)) != len(members):
        findings.append("%s: members must be a non-empty list of unique strings" % (where,))
        return findings
    if not isinstance(anchors, dict) or set(anchors) != set(members):
        findings.append("%s: anchors must give exactly one anchor per member %s" % (where, members))
        return findings
    for member in members:
        problem = _member_problem(root, member)
        if problem is not None:
            findings.append("%s: member %r %s" % (where, member, problem))
            continue
        anchor = anchors[member]
        if not isinstance(anchor, str) or not anchor:
            findings.append("%s: the anchor for %s must be a non-empty string" % (where, member))
            continue
        if member not in texts:
            texts[member] = _text(_input(root, PACK_REL + "/" + member, "the member"), "the member")
        if anchor not in texts[member]:
            findings.append("%s: the anchor %r is not present verbatim in %s/%s" % (where, anchor, PACK_REL, member))
    return findings


def evaluate(root, unnamed=None, locators=None):
    """The findings for the tree at `root` (a list, empty when clean), or CannotEvaluate. `unnamed` and
    `locators` replace UNNAMED_MEMBERS and LOCATOR_ANCHORS for the self-test only; the real run passes
    neither."""
    root = Path(root)
    unnamed = UNNAMED_MEMBERS if unnamed is None else unnamed
    locators = LOCATOR_ANCHORS if locators is None else locators
    vocab = _vocabulary(root)
    text = _text(_input(root, REGISTER_REL, "the residual register"), "the residual register")
    try:
        doc = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("%s does not parse (%s)" % (REGISTER_REL, exc))
    version = doc.get("format-version")
    if set(doc) != frozenset(("format-version", "residual")) or type(version) is not int \
            or version != 1 or not isinstance(doc.get("residual"), list):
        raise CannotEvaluate("%s carries no exact integer format-version = 1 and [[residual]] rows only "
                             "(a boolean true, a float 1.0 or another near-1 spelling is refused)"
                             % (REGISTER_REL,))
    findings = _header_findings(_header(text), unnamed, locators)
    texts, seen, named, rows = {}, [], set(), {}
    for index, row in enumerate(doc["residual"], 1):
        where = "%s row %d" % (REGISTER_REL, index)
        if not isinstance(row, dict) or set(row) != ROW_KEYS:
            findings.append("%s: keys must be exactly %s" % (where, sorted(ROW_KEYS)))
            continue
        where = "%s (%s)" % (where, row["id"])
        seen.append(row["id"])
        row_findings = _row_findings(root, where, row, texts)
        findings.extend(row_findings)
        if not row_findings:
            named.update(row["members"])
            rows.setdefault(row["id"], row)
    if tuple(seen) != vocab["residuals"]:
        findings.append("%s: rows must carry %s once each, in that order (found %s)"
                        % (REGISTER_REL, list(vocab["residuals"]), seen))
    files, pack_findings = _pack_files(root)
    findings.extend(pack_findings)
    for rel in sorted(files - named - set(unnamed)):
        findings.append("%s/%s: a pack member named by no row" % (PACK_REL, rel))
    for rel in sorted(unnamed):
        if rel not in files:
            findings.append("UNNAMED_MEMBERS: %s is not a regular pack file" % (rel,))
        elif rel in named:
            findings.append("UNNAMED_MEMBERS: %s is named by a row, so its exception is stale" % (rel,))
    for (row_id, member), (anchor, reason) in sorted(locators.items()):
        row = rows.get(row_id)
        if row is None or row["anchors"].get(member) != anchor:
            findings.append("LOCATOR_ANCHORS: the row %s does not give %s the anchor %r" % (row_id, member, anchor))
    for row_id, row in rows.items():
        if all((row_id, m) in locators for m in row["members"]):
            findings.append("%s (%s): every anchor of the row is a locator, so it discloses nothing"
                            % (REGISTER_REL, row_id))
    for means, residuals in sorted(vocab["required"].items()):
        for residual in residuals:
            listed = set(rows[residual]["members"]) if residual in rows else set()
            for member in sorted(vocab["members"].get(means, set()) - listed):
                findings.append("%s: the %s means requires the residual %s, and its row does not list the "
                                "%s member %s" % (REGISTER_REL, means, residual, means, member))
    return findings


def main(argv):
    if argv:
        print("usage: check_opf_enforce_residuals.py [--self-test]", file=sys.stderr)
        return 2
    try:
        findings = evaluate(ROOT)
    except CannotEvaluate as exc:
        print("check_opf_enforce_residuals: CANNOT EVALUATE: %s" % (exc,))
        return 2
    for finding in findings:
        print("check_opf_enforce_residuals: %s" % (finding,))
    if findings:
        print("check_opf_enforce_residuals: FAIL (%d finding(s))" % (len(findings),))
        return 1
    print("check_opf_enforce_residuals: OK (%s carries one row per residual token; every anchor is present "
          "verbatim in its member, every pack member is named but the stated exception, and the header "
          "agrees with the exception and locator lists)" % (REGISTER_REL,))
    return 0


# --- self-test -----------------------------------------------------------------------------------------

SETTINGS = "claude/settings.opf.json"
LOCAL_SETTINGS = "claude/settings.local.json"
LOCATOR_KEY = ("server-side-protection-adopter-attested", "ci/github-actions.yml")
UNVERIFIED_MEMBERS = 'members = ["codex/AGENTS.opf.md", "gemini-cli/GEMINI.opf.md", "cursor/opf.mdc"]\n'
UNVERIFIED_ANCHORS = 'anchors = {"codex/AGENTS.opf.md" = '
SAME_USER_STUB = "the same user can remove the stub"
INSTALL_ANCHOR = "the stub is local to this clone"
HOOK_PHRASE = "spec 14.1 accepts same-user tampering as a residual"
COVERAGE_SENTENCE = "# Every pack member except claude/settings.opf.json is named by at least one row"
LOCATOR_TAIL = "is\n# a locator, not a disclosure."
HEADER_LAST = "# that row's disclosure. Every other anchor is a disclosure.\n"
LOCATOR_ROW_PHRASE = "server-side-protection-adopter-attested row because"
INSTALL_LAUNCHER = ('    ("pre-commit", "enforcement/precommit/install.sh", "opf/enforcement/precommit/install.sh",\n'
                    '     \'[ -f "$here/pre-commit" ] && [ -r "$here/pre-commit" ] || \\\\\', "member", "pre-commit"),\n')


def _fixture(base, name):
    """A fresh copy of every input this gate reads, under base/name."""
    tree = Path(base) / name
    shutil.copytree(ROOT / PACK_REL, tree / PACK_REL)
    (tree / ADOPT_REL).parent.mkdir(parents=True)
    shutil.copyfile(ROOT / ADOPT_REL, tree / ADOPT_REL)
    return tree


def _edit(tree, rel, old, new, count=1):
    """Replace `old` (which must occur exactly `count` times) by `new` in the fixture file `rel`."""
    path = tree / rel
    text = path.read_text(encoding="utf-8")
    if text.count(old) != count:
        raise AssertionError("fixture edit: %r occurs %d times in %s, expected %d"
                             % (old, text.count(old), rel, count))
    path.write_text(text.replace(old, new), encoding="utf-8")


def _write(tree, rel, text):
    (tree / rel).write_text(text, encoding="utf-8")


def _add_member(tree, member, anchor):
    """Add `member` with `anchor` to the unverified-platform-denial row."""
    _edit(tree, REGISTER_REL, UNVERIFIED_MEMBERS,
          UNVERIFIED_MEMBERS[:-2] + ', "%s"]\n' % (member,))
    _edit(tree, REGISTER_REL, UNVERIFIED_ANCHORS, 'anchors = {"%s" = "%s", "codex/AGENTS.opf.md" = '
          % (member, anchor))


def _pad_register(tree, size):
    """Pad the register with one trailing comment line to exactly `size` bytes."""
    path = tree / REGISTER_REL
    data = path.read_bytes() + b"# "
    path.write_bytes(data + b"x" * (size - len(data) - 1) + b"\n")


def _swap_last_rows(tree):
    path = tree / REGISTER_REL
    text = path.read_text(encoding="utf-8")
    a = text.index('[[residual]]\nid = "same-user-tampering"')
    b = text.index('[[residual]]\nid = "unverified-platform-denial"')
    path.write_text(text[:a] + text[b:].rstrip("\n") + "\n\n" + text[a:b].rstrip("\n") + "\n", encoding="utf-8")


def _drop_last_row(tree):
    path = tree / REGISTER_REL
    text = path.read_text(encoding="utf-8")
    path.write_text(text[:text.index('[[residual]]\nid = "unverified-platform-denial"')], encoding="utf-8")


def _second_locator(tree, row):
    """Declare the ci/opf-ci.sh anchor of `row` a locator in the header (with the row id and a reason)."""
    doc = tomllib.loads((tree / REGISTER_REL).read_text(encoding="utf-8"))
    anchor = [r for r in doc["residual"] if r["id"] == row][0]["anchors"]["ci/opf-ci.sh"]
    _edit(tree, REGISTER_REL, HEADER_LAST, HEADER_LAST + '# In the %s row the ci/opf-ci.sh anchor ("%s") is '
          'a locator, not a disclosure: a probe reason.\n' % (row, anchor))
    return {**LOCATOR_ANCHORS, (row, "ci/opf-ci.sh"): (anchor, "a probe reason")}


def _vectors():
    """(id, vector, flip): each a (mutate(tree) returning gate-list overrides or None, expected status, a
    substring of the result). Statuses are "clean", "finding" and "cannot"; a vector and its flip must
    differ in status, and both are observed."""
    def edit(rel, old, new, count=1):
        return lambda tree: _edit(tree, rel, old, new, count)

    def seq(*steps):
        def run(tree):
            out = None
            for step in steps:
                got = step(tree)
                out = got if isinstance(got, dict) else out
            return out
        return run

    def lists(unnamed=None, locators=None):
        return lambda tree: dict(unnamed=unnamed, locators=locators)

    def mode(rel, bits):
        return lambda tree: os.chmod(tree / rel, bits)

    clean = ("clean", "")
    drift = edit(PACK_REL + "/precommit/pre-commit", SAME_USER_STUB, "the same user may remove the stub")
    respaced = INSTALL_ANCHOR.replace("local to", "local  to")
    local_file = lambda tree: _write(tree, PACK_REL + "/" + LOCAL_SETTINGS, "{}\n")
    header_grown = edit(REGISTER_REL, COVERAGE_SENTENCE, COVERAGE_SENTENCE.replace(
        "settings.opf.json", "settings.opf.json and " + LOCAL_SETTINGS))
    unnamed_grown = lists(unnamed={**UNNAMED_MEMBERS, LOCAL_SETTINGS: UNNAMED_MEMBERS[SETTINGS]})
    no_locator = edit(REGISTER_REL, LOCATOR_TAIL, "is\n# a disclosure, not a locator.")
    return (
        ("clean", (lambda tree: None, "clean", ""),
         (drift, "finding", "is not present verbatim")),
        # anchors: verbatim, with no normalisation; a change on both sides is clean.
        ("anchor-drift", (drift, "finding", "is not present verbatim"),
         (seq(drift, edit(REGISTER_REL, '"' + SAME_USER_STUB + '"', '"the same user may remove the stub"')),) + clean),
        ("anchor-respaced", (edit(REGISTER_REL, '"' + INSTALL_ANCHOR + '"', '"' + respaced + '"'), "finding",
                             "is not present verbatim"),
         (seq(edit(REGISTER_REL, '"' + INSTALL_ANCHOR + '"', '"' + respaced + '"'),
              edit(PACK_REL + "/precommit/install.sh", INSTALL_ANCHOR, respaced)),) + clean),
        ("anchor-key-not-member", (edit(REGISTER_REL, UNVERIFIED_ANCHORS, 'anchors = {"claude/pretooluse_deny.py" = '
                                        '"%s", "codex/AGENTS.opf.md" = ' % (HOOK_PHRASE,)),
                                   "finding", "exactly one anchor per member"),
         (lambda tree: _add_member(tree, "claude/pretooluse_deny.py", HOOK_PHRASE),) + clean),
        # members: regular pack files reached through no symlink, never a register, never outside the pack.
        ("member-duplicate", (edit(REGISTER_REL, UNVERIFIED_MEMBERS, UNVERIFIED_MEMBERS[:-2] + ', "cursor/opf.mdc"]\n'),
                              "finding", "unique strings"),
         (lambda tree: _add_member(tree, "precommit/install.sh", INSTALL_ANCHOR),) + clean),
        ("member-absent", (lambda tree: _add_member(tree, "codex/NOTES.md", "a probe phrase"), "finding",
                           "is not a regular pack file"),
         (seq(lambda tree: _add_member(tree, "codex/NOTES.md", "a probe phrase"),
              lambda tree: _write(tree, PACK_REL + "/codex/NOTES.md", "a probe phrase\n")),) + clean),
        ("member-symlink", (seq(lambda tree: os.symlink("AGENTS.opf.md", tree / PACK_REL / "codex" / "LINK.md"),
                                lambda tree: _add_member(tree, "codex/LINK.md", "Same-user tampering")),
                            "finding", "is reached through a symlink"),
         (seq(lambda tree: shutil.copyfile(tree / PACK_REL / "codex" / "AGENTS.opf.md",
                                           tree / PACK_REL / "codex" / "LINK.md"),
              lambda tree: _add_member(tree, "codex/LINK.md", "Same-user tampering")),) + clean),
        ("member-symlinked-parent", (seq(lambda tree: os.symlink("codex", tree / PACK_REL / "codex2"),
                                         lambda tree: _add_member(tree, "codex2/AGENTS.opf.md", "Same-user tampering")),
                                     "finding", "is reached through a symlink"),
         (seq(lambda tree: shutil.copytree(tree / PACK_REL / "codex", tree / PACK_REL / "codex2"),
              lambda tree: _add_member(tree, "codex2/AGENTS.opf.md", "Same-user tampering")),) + clean),
        ("member-outside-pack", (lambda tree: _add_member(tree, "../tools/_opf_adopt.py", "ENFORCEMENT_RESIDUALS"),
                                 "finding", "is not a contained relative path"),
         (lambda tree: _add_member(tree, "precommit/install.sh", INSTALL_ANCHOR),) + clean),
        ("member-register", (lambda tree: _add_member(tree, "platforms.toml", "format-version = 1"),
                             "finding", "is a register, not a pack member"),
         (lambda tree: _add_member(tree, "precommit/install.sh", INSTALL_ANCHOR),) + clean),
        # coverage: a pack file no row names is a finding unless UNNAMED_MEMBERS and the header both carry it.
        ("unnamed-file", (local_file, "finding", "a pack member named by no row"),
         (seq(local_file, lambda tree: _add_member(tree, LOCAL_SETTINGS, "{}")),) + clean),
        ("unnamed-exception-allowed", (lambda tree: None, "clean", ""),
         (lists(unnamed={}), "finding", SETTINGS + ": a pack member named by no row")),
        ("unnamed-exception-stale", (lambda tree: _add_member(tree, SETTINGS, "PreToolUse"), "finding",
                                     "its exception is stale"),
         (seq(lambda tree: _add_member(tree, SETTINGS, "PreToolUse"),
              edit(REGISTER_REL, " except claude/settings.opf.json is named", " is named"), lists(unnamed={})),)
         + clean),
        ("unnamed-second-member", (seq(local_file, unnamed_grown), "finding",
                                   "does not except claude/settings.local.json"),
         (seq(local_file, header_grown, unnamed_grown),) + clean),
        ("header-unnamed-grown", (seq(local_file, header_grown), "finding",
                                  "which this gate's UNNAMED_MEMBERS does not carry"),
         (seq(local_file, header_grown, unnamed_grown),) + clean),
        ("header-unnamed-dropped", (edit(REGISTER_REL, " except claude/settings.opf.json is named", " is named"),
                                    "finding", "does not except claude/settings.opf.json"),
         (seq(edit(REGISTER_REL, " except claude/settings.opf.json is named", " is named"), lists(unnamed={}),
              lambda tree: _add_member(tree, SETTINGS, "PreToolUse")),) + clean),
        ("header-unnamed-reason-dropped", (edit(REGISTER_REL, "carries no prose and so discloses nothing",
                                                "carries no prose"), "finding", "does not give the reason"),
         (seq(edit(REGISTER_REL, "carries no prose and so discloses nothing", "carries no prose"),
              lists(unnamed={SETTINGS: "carries no prose"})),) + clean),
        ("header-coverage-twice", (edit(REGISTER_REL, HEADER_LAST, HEADER_LAST + COVERAGE_SENTENCE + ".\n"),
                                   "finding", "not exactly one"),
         (edit(REGISTER_REL, HEADER_LAST, HEADER_LAST + "# Every member is listed.\n"),) + clean),
        # locators: held to LOCATOR_ANCHORS, the header and the row, both ways.
        ("locator-exception-allowed", (lambda tree: None, "clean", ""),
         (no_locator, "finding", "does not name the ci/github-actions.yml anchor")),
        ("header-locator-dropped", (no_locator, "finding", "does not name the ci/github-actions.yml anchor"),
         (seq(no_locator, lists(locators={})),) + clean),
        ("header-locator-loose", (edit(REGISTER_REL, HEADER_LAST, HEADER_LAST + "# The ci/opf-ci.sh anchor is a "
                                       "locator too.\n"), "finding", "says 'is a locator' 2 times"),
         (edit(REGISTER_REL, HEADER_LAST, HEADER_LAST + "# The ci/opf-ci.sh anchor is a disclosure.\n"),) + clean),
        ("header-locator-row-dropped", (edit(REGISTER_REL, LOCATOR_ROW_PHRASE, "CI protection row because"),
                                        "finding", "does not name the row server-side-protection-adopter-attested"),
         (seq(edit(REGISTER_REL, LOCATOR_ROW_PHRASE, "CI protection row because"),
              edit(REGISTER_REL, HEADER_LAST, HEADER_LAST + "# (row server-side-protection-adopter-attested)\n")),)
         + clean),
        ("header-locator-reason-dropped", (edit(REGISTER_REL, "says nothing about server-side protection",
                                                "is silent"), "finding", "does not give the reason"),
         (seq(edit(REGISTER_REL, "says nothing about server-side protection", "is silent"),
              lists(locators={LOCATOR_KEY: ("branches: [main]", "is silent")})),) + clean),
        ("locator-anchor-changed", (edit(REGISTER_REL, '"ci/github-actions.yml" = "branches: [main]"',
                                         '"ci/github-actions.yml" = "pull_request:"'),
                                    "finding", "does not give ci/github-actions.yml the anchor"),
         (seq(edit(REGISTER_REL, '"ci/github-actions.yml" = "branches: [main]"',
                   '"ci/github-actions.yml" = "pull_request:"'),
              edit(REGISTER_REL, '("branches: [main]")', '("pull_request:")'),
              lists(locators={LOCATOR_KEY: ("pull_request:", LOCATOR_ANCHORS[LOCATOR_KEY][1])})),) + clean),
        ("locator-only-row", (lambda tree: dict(locators=_second_locator(tree, LOCATOR_KEY[0])), "finding",
                              "every anchor of the row is a locator"),
         (lambda tree: dict(locators=_second_locator(tree, "canonical-hand-edits")),) + clean),
        # rows: one per token, in order, with the closed row schema.
        ("row-order-swapped", (_swap_last_rows, "finding", "in that order"),
         (seq(_swap_last_rows, edit(ADOPT_REL, '"same-user-tampering",\n                         '
                                    '"unverified-platform-denial")', '"unverified-platform-denial",\n'
                                    '                         "same-user-tampering")')),) + clean),
        ("row-dropped", (_drop_last_row, "finding", "in that order"),
         (seq(_drop_last_row, edit(ADOPT_REL, '"same-user-tampering",\n                         '
                                   '"unverified-platform-denial")', '"same-user-tampering")'),
              edit(ADOPT_REL, '"instructions": ("unverified-platform-denial",),',
                   '"instructions": ("shell-or-interpreter-wrapping",),')),) + clean),
        ("row-id-unknown", (edit(REGISTER_REL, 'id = "unverified-platform-denial"', 'id = "unverified-denial"'),
                            "finding", "in that order"),
         (seq(edit(REGISTER_REL, 'id = "unverified-platform-denial"', 'id = "unverified-denial"'),
              edit(ADOPT_REL, '"unverified-platform-denial")', '"unverified-denial")', 4),
              edit(ADOPT_REL, '("unverified-platform-denial",)', '("unverified-denial",)', 2)),) + clean),
        ("row-extra-key", (edit(REGISTER_REL, 'id = "canonical-hand-edits"\n', 'id = "canonical-hand-edits"\n'
                                'note = "x"\n'), "finding", "keys must be exactly"),
         (edit(REGISTER_REL, 'id = "canonical-hand-edits"\n', 'id = "canonical-hand-edits"\n# note = "x"\n'),)
         + clean),
        ("statement-empty", (edit(REGISTER_REL, 'statement = "Anyone acting', 'statement = " "\n# "Anyone acting'),
                             "finding", "statement must be a non-empty string"),
         (edit(REGISTER_REL, 'statement = "Anyone acting', 'statement = "Whoever acts'),) + clean),
        # means: each means' required residual lists that means' members.
        ("means-member-unlisted", (seq(edit(REGISTER_REL, '"precommit/install.sh" = "%s", ' % (INSTALL_ANCHOR,), ""),
                                       edit(REGISTER_REL, '"claude/pretooluse_deny.py", "precommit/install.sh", '
                                            '"precommit/pre-commit"]', '"claude/pretooluse_deny.py", '
                                            '"precommit/pre-commit"]')),
                                   "finding", "does not list the staged-pre-commit member precommit/install.sh"),
         (seq(edit(REGISTER_REL, '"precommit/install.sh" = "%s", ' % (INSTALL_ANCHOR,), ""),
              edit(REGISTER_REL, '"claude/pretooluse_deny.py", "precommit/install.sh", "precommit/pre-commit"]',
                   '"claude/pretooluse_deny.py", "precommit/pre-commit"]'),
              edit(ADOPT_REL, INSTALL_LAUNCHER, "")),) + clean),
        ("means-platform-changed", (edit(ROSTER_REL, 'means = "deny-hook"', 'means = "instructions"'), "finding",
                                    "does not list the instructions member claude/pretooluse_deny.py"),
         (seq(edit(ROSTER_REL, 'means = "deny-hook"', 'means = "instructions"'),
              lambda tree: _add_member(tree, "claude/pretooluse_deny.py", HOOK_PHRASE)),) + clean),
        # cannot evaluate: the document shape, the vocabulary, the size bound and unreadable inputs.
        ("format-version-bool", (edit(REGISTER_REL, "format-version = 1\n", "format-version = true\n"), "cannot",
                                 "exact integer format-version"),
         (edit(REGISTER_REL, "format-version = 1\n", "format-version = 0x1\n"),) + clean),
        ("format-version-float", (edit(REGISTER_REL, "format-version = 1\n", "format-version = 1.0\n"), "cannot",
                                  "exact integer format-version"),
         (edit(REGISTER_REL, "format-version = 1\n", "format-version = +1\n"),) + clean),
        ("extra-top-table", (lambda tree: _write(tree, REGISTER_REL, (tree / REGISTER_REL).read_text(
            encoding="utf-8") + "\n[extra]\nx = 1\n"), "cannot", "exact integer format-version"),
         (lambda tree: _write(tree, REGISTER_REL, (tree / REGISTER_REL).read_text(encoding="utf-8")
                              + "\n# [extra]\n"),) + clean),
        ("register-unparseable", (lambda tree: _write(tree, REGISTER_REL, (tree / REGISTER_REL).read_text(
            encoding="utf-8") + "\n[[residual\n"), "cannot", "does not parse"),
         (lambda tree: _write(tree, REGISTER_REL, (tree / REGISTER_REL).read_text(encoding="utf-8")
                              + "\n# [[residual\n"),) + clean),
        ("roster-unparseable", (lambda tree: _write(tree, ROSTER_REL, (tree / ROSTER_REL).read_text(
            encoding="utf-8") + "\n[[platform\n"), "cannot", "does not parse"),
         (lambda tree: _write(tree, ROSTER_REL, (tree / ROSTER_REL).read_text(encoding="utf-8")
                              + "\n# [[platform\n"),) + clean),
        ("adopt-literal-doubled", (lambda tree: _write(tree, ADOPT_REL, (tree / ADOPT_REL).read_text(
            encoding="utf-8") + "\nENFORCEMENT_RESIDUALS = ()\n"), "cannot", "more than once"),
         (lambda tree: _write(tree, ADOPT_REL, (tree / ADOPT_REL).read_text(encoding="utf-8")
                              + "\ndef _probe():\n    ENFORCEMENT_RESIDUALS = ()\n"),) + clean),
        # the size bound: just inside and exactly at MAX_INPUT_BYTES are read, one byte over is refused.
        ("register-just-inside", (lambda tree: _pad_register(tree, MAX_INPUT_BYTES - 1),) + clean,
         (lambda tree: _pad_register(tree, MAX_INPUT_BYTES + 1), "cannot", "exceeds")),
        ("register-at-bound", (lambda tree: _pad_register(tree, MAX_INPUT_BYTES),) + clean,
         (lambda tree: _pad_register(tree, MAX_INPUT_BYTES + 1), "cannot", "exceeds")),
        ("register-over-bound", (lambda tree: _pad_register(tree, MAX_INPUT_BYTES + 1), "cannot", "exceeds"),
         (lambda tree: _pad_register(tree, MAX_INPUT_BYTES),) + clean),
        ("member-mode-000", (mode(PACK_REL + "/precommit/pre-commit", 0), "cannot", "cannot be read"),
         (mode(PACK_REL + "/precommit/pre-commit", 0o444),) + clean),
        ("register-mode-000", (mode(REGISTER_REL, 0), "cannot", "cannot be read"),
         (mode(REGISTER_REL, 0o400),) + clean),
    )


MODE_000 = frozenset(("member-mode-000", "register-mode-000"))


def _observe(base, name, mutate):
    """The status and text of the gate over a fresh fixture mutated by `mutate`."""
    tree = _fixture(base, name)
    overrides = mutate(tree)
    overrides = overrides if isinstance(overrides, dict) else {}
    try:
        got = evaluate(tree, **overrides)
    except CannotEvaluate as exc:
        return "cannot", "CANNOT EVALUATE: %s" % (exc,)
    return ("finding" if got else "clean"), "\n".join(got)


def self_test():
    failures = []
    runs = skipped = 0
    with tempfile.TemporaryDirectory(prefix="opf-enforce-residuals-") as base:
        try:
            for case_id, vector, flip in _vectors():
                if case_id in MODE_000 and os.geteuid() == 0:
                    skipped += 1
                    print("check_opf_enforce_residuals --self-test: SKIP %s (root reads a mode 000 file)" % (case_id,))
                    continue
                statuses = []
                for leg, (mutate, status, text) in (("vector", vector), ("flip", flip)):
                    runs += 1
                    try:
                        got_status, got_text = _observe(base, "%s-%s" % (case_id, leg), mutate)
                    except (AssertionError, OSError, ValueError, KeyError, IndexError) as exc:
                        failures.append("%s %s: fixture setup failed (%r)" % (case_id, leg, exc))
                        continue
                    statuses.append(got_status)
                    if got_status != status or text not in got_text:
                        failures.append("%s %s: expected %s containing %r, got %s %r"
                                        % (case_id, leg, status, text, got_status, got_text))
                if len(statuses) == 2 and statuses[0] == statuses[1]:
                    failures.append("%s: the flip does not reverse the vector's status (%s)" % (case_id, statuses[0]))
        finally:
            for top, dirs, names in os.walk(base):
                for name in dirs + names:
                    path = os.path.join(top, name)
                    if not os.path.islink(path):
                        os.chmod(path, 0o700)
    for failure in failures:
        print("SELF-TEST FAIL: %s" % (failure,))
    if failures:
        print("check_opf_enforce_residuals --self-test: FAIL (%d failure(s) over %d checks, %d skipped)"
              % (len(failures), runs, skipped))
        return 1
    print("check_opf_enforce_residuals --self-test: OK (%d checks, %d skipped)" % (runs, skipped))
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main(sys.argv[1:]))
