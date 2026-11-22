#!/usr/bin/env python3
"""Render the CORE preview of the rule corpus: one file, .aiqt/renders/aiqt-core.md, holding one entry per rule.

The CORE render is the second render of the one rule source (gen_rules writes the first, the full render, to
.claude/rules/). This step writes it to a PREVIEW path that nothing loads: it is not under .claude/, it is not
named like CLAUDE.md, and no loaded file imports it. Nothing about what a session loads changes here.

FORMAT. No frontmatter. The file opens with a whole-line generated-file comment, a blank line, the title
`# AIQT core rules`, a blank line and the preamble paragraph (at most PREAMBLE_CAP UTF-16 units). Then one
`## <group>` heading per group that has rules, in tools/gen_claude.py GROUPS order, each rule a one-line
paragraph sorted as tools/gen_agents.py sort_key sorts it:
  - a rule with no `## Detail` split (gen_rules RULE SOURCE FORMAT) is kept whole:
        <Title> (<corpus-id>). <every body line below the title, stripped, joined by one space>
  - a split rule gives its core layer (gen_rules.core_layer, joined the same way) and a pointer to its full
    text, a plain path in a code span and never an import:
        <Title> (<corpus-id>). <core layer> Full rule, read before <detail-trigger>: `<reference path>`
    where the reference path is .aiqt/reference/rules/ followed by the rule's derived read path
    (gen_rules.derive, such as aiqt/10-ACCUR-<slug>.md).

BUDGET (.aiqt/core/core-budget.toml). Exactly these keys: format-version (1); entry-cap, the largest joined
layer an entry may carry, in UTF-16 units, title and id excluded; trigger-cap, the longest detail-trigger in
characters; file-cap, the largest CORE render in UTF-16 units; and pending, the sorted list of corpus-ids
over entry-cap that are not split yet. Each cap is a positive integer. The apex is never split (the source
grammar forbids it), so it is always kept whole and no cap applies to its entry. A pending rule is rendered
whole until it is split. The pending list may only shrink: every id on it must also be in PENDING_SEED, the
list as it stood when this renderer was added, so growing it means editing this reviewed file (and so a
renderers.toml code-digest change); and an id that no longer needs to be there (now split, now within
entry-cap, or the apex) is a finding until it is removed. file-cap is enforced once pending is empty; until
then the render is larger than it by construction, and the measured size is printed against it.

EXIT. 0 clean. 1 on drift (--check) or on a finding, and a finding in write mode writes nothing:
  - a non-apex rule over entry-cap with no split and not on pending; a split rule whose core layer is over
    entry-cap; a detail-trigger over trigger-cap; a pending id outside PENDING_SEED; a pending id that no
    longer needs to be there; the render over file-cap while pending is empty;
  - in the fresh render, and in --check also in the committed file when it differs from the fresh render:
    a frontmatter block; an `@` anywhere (the instruction-budget gate reads an `@` as an import even inside a
    code span, so no `@` is allowed at all); a corpus-id with no entry; a corpus-id with more than one entry;
    an entry line that names no corpus-id or one the corpus does not hold; an entry that differs from the
    entry its source layer renders; and a pointer to a reference path that is not a regular file inside the
    repository (a dangling pointer).
2 cannot evaluate, never a clean pass, for every malformed or missing input: a .aiqt/core/rules/ that is
absent, not a directory inside the repository, unreadable or holds no source; a malformed source or a
duplicate corpus-id (gen_rules.load_corpus); a source whose group is not in GROUPS, whose body is empty, or
whose split leaves an empty core layer; a core-budget.toml that is absent, not a regular file (a symlink or
a directory), unreadable, not TOML, or off the schema above (an unknown or missing key, a cap that is not a
positive integer, a format-version other than 1, a pending entry that is not an id, an unsorted or repeated
pending list, or a pending id the corpus does not hold); a committed render that is not UTF-8; and a
.aiqt/renders/ that cannot be read as a directory inside the repository. A committed render that is absent
is drift (exit 1), as for every generated file.

  gen_rules_core.py               regenerate .aiqt/renders/aiqt-core.md
  gen_rules_core.py --check       exit 1 on drift or a finding, 2 when it cannot evaluate; write nothing
  gen_rules_core.py --self-test   synthetic trees: every finding, every cannot-evaluate input, and for each
                                  guard the plan names a flip that shows the case fails without it
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: gen_rules_core.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import load_toml, reconcile, repo_root, target_dir_present, unknown_args_refused  # noqa: E402
from _standards import dir_or_absent, dir_present  # noqa: E402
from gen_rules import CID_RE, body_first_line, core_layer, load_corpus, read_rule_source  # noqa: E402
from gen_claude import GROUP_LABEL, GROUP_ORDER, GROUPS, _group_key  # noqa: E402
from gen_agents import sort_key  # noqa: E402

# Renderer identity for the manifest-covered declaration (tools/gen_renderers.py).
RENDERER_DECL = {"renderer-id": "rules-core", "semantics-revision": 1}
# Declares this generator's output for the gensrc registry (tools/gen_gensrc.py). The budget file is read to
# judge the render, never to shape its bytes, so it is not a source.
GENSRC_OUTPUTS = (
    {"target": ".aiqt/renders/aiqt-core.md", "kind": "file",
     "sources": (".aiqt/core/rules/",), "regenerate": "python3 tools/gen_rules_core.py"},
)
CORE_REL = ".aiqt/renders/aiqt-core.md"
BUDGET_REL = ".aiqt/core/core-budget.toml"
REFERENCE_PREFIX = ".aiqt/reference/rules/"
BUDGET_KEYS = ("format-version", "entry-cap", "trigger-cap", "file-cap", "pending")
CAP_KEYS = ("entry-cap", "trigger-cap", "file-cap")
PREAMBLE_CAP = 1000
GENERATED_COMMENT = ("<!-- GENERATED by tools/gen_rules_core.py from .aiqt/core/rules/; do not hand-edit. A "
                     "preview that nothing loads. -->")
TITLE = "# AIQT core rules"
PREAMBLE = ("Every rule below is in force, whether its entry holds the whole rule or only its core. Where an "
            "entry ends in a pointer, the full rule at that path is authoritative: read it before the "
            "operation the pointer names, and whenever the core leaves a case open. The Apex entry states "
            "the priority ordering.")
POINTER = "Full rule, read before {}: `{}`"
POINTER_PATH_RE = re.compile(r"`(" + re.escape(REFERENCE_PREFIX) + r"[^`]*)`")
ENTRY_RE = re.compile(r"^(.+?) \(([a-z0-9]{6,})\)\. (.+)$")
# The pending list as it stood when this renderer was added (sorted). core-budget.toml pending must stay a
# subset of it, so the list can only shrink without a reviewed edit here.
PENDING_SEED = frozenset((
    "abspth", "atmclm", "brnrot", "chgchk", "chkfcl", "citint", "clmobs", "cnsdif",
    "cnsprd", "cnstpr", "cntdef", "cntprd", "deccls", "dfdpth", "dscres", "estsep",
    "evgcmp", "exetgt", "expbnd", "gatdis", "gensrc", "goalfd", "grdinp", "kltwat",
    "mindep", "mstreg", "obsbeh", "prsunc", "prtwhl", "recncl", "reqstp", "rmvpth",
    "rvwpin", "secazn", "seccet", "seccry", "secegr", "secfcl", "secgci", "sechau",
    "secout", "secpin", "secprv", "secpsn", "secpth", "secres", "secrst", "secsec",
    "secspr", "secsup", "sectok", "secunt", "secupl", "septsk", "setcmp", "stgprm",
    "tmrrst", "trkasy", "tstamp", "tsthrm", "vfxcmt", "vrfdiv", "vrfdlv", "wowo01",
))


def units(text):
    """The UTF-16 code-unit length of text, the unit the instruction-budget gate counts."""
    return len(text.encode("utf-16-le")) // 2


def _joined(lines):
    """The non-blank lines, each stripped, joined by one space."""
    return " ".join(line.strip() for line in lines if line.strip())


class Rule:
    """One rule as the CORE render needs it."""

    def __init__(self, cid, fm, rel, title, layer, trigger):
        self.cid, self.fm, self.rel, self.title, self.layer, self.trigger = cid, fm, rel, title, layer, trigger
        self.split = trigger is not None
        self.apex = fm.get("apex") is True
        self.group = _group_key(fm)

    def entry(self):
        """The rule's CORE entry line, from its source layer."""
        text = "{} ({}). {}".format(self.title, self.cid, self.layer)
        if self.split:
            text += " " + POINTER.format(self.trigger, REFERENCE_PREFIX + self.rel)
        return text


def _title_and_rest(text, name):
    """(title text, the body lines below the title) of a rule source. Raises ValueError with no title."""
    lines = text.split("\n")
    for index in range(body_first_line(text, name) - 1, len(lines)):
        if lines[index] != "":
            if not lines[index].startswith("# ") or not lines[index][2:].strip():
                raise ValueError("{}: the first body line is not a '# ' title".format(name))
            return lines[index][2:].strip(), lines[index + 1:]
    raise ValueError("{}: the body has no title line".format(name))


def load_rules(root):
    """Every rule of root's corpus as a Rule. Raises ValueError or OSError (cannot evaluate) on a corpus
    directory that is absent, not a directory inside root or holds no source, on a malformed source or a
    duplicate corpus-id, and on a group outside GROUPS, an empty body or an empty core layer."""
    src_dir = root / ".aiqt" / "core" / "rules"
    _present, problem = dir_or_absent(src_dir, root)
    if problem is not None:
        raise ValueError("{} {}".format(src_dir, problem))
    if not dir_present(src_dir, root=root):
        raise ValueError("{} is absent, so there is no corpus to render".format(src_dir))
    corpus = load_corpus(src_dir)
    if not corpus:
        raise ValueError("{} holds no rule source".format(src_dir))
    rules = []
    for src, fm, rel in corpus:
        text = read_rule_source(src)
        title, rest = _title_and_rest(text, src.name)
        core = core_layer(text, src.name)
        if core is None:
            layer, trigger = _joined(rest), None
            if not layer:
                raise ValueError("{}: the body below the title is empty".format(src.name))
        else:
            layer, trigger = _joined(core.split("\n")), fm["detail-trigger"]
            if not layer:
                raise ValueError("{}: the '## Detail' split leaves an empty core layer".format(src.name))
        rule = Rule(str(fm["corpus-id"]), fm, rel, title, layer, trigger)
        if rule.group not in GROUP_ORDER:
            raise ValueError("{}: group {!r} is not in gen_claude GROUPS".format(src.name, rule.group))
        rules.append(rule)
    return rules


def load_budget(root, rules):
    """The validated core-budget.toml as a dict. Raises ValueError or OSError (cannot evaluate) on a file
    that is absent, not a regular file, unreadable, not TOML or off the schema."""
    path = root / BUDGET_REL
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        raise ValueError("{} is absent".format(BUDGET_REL)) from None
    except OSError as exc:
        raise ValueError("{} cannot be examined ({})".format(BUDGET_REL, exc)) from None
    if not stat.S_ISREG(mode):
        raise ValueError("{} is not a regular file (a symlink, a directory or another kind)".format(BUDGET_REL))
    data = load_toml(path)
    missing = [key for key in BUDGET_KEYS if key not in data]
    extra = sorted(key for key in data if key not in BUDGET_KEYS)
    if missing or extra:
        raise ValueError("{}: missing key(s) {} and unknown key(s) {}; the keys are exactly {}".format(
            BUDGET_REL, missing, extra, ", ".join(BUDGET_KEYS)))
    version = data["format-version"]
    if type(version) is not int or version != 1:
        raise ValueError("{}: format-version must be 1, got {!r}".format(BUDGET_REL, version))
    for key in CAP_KEYS:
        if type(data[key]) is not int or data[key] <= 0:
            raise ValueError("{}: {} must be a positive integer, got {!r}".format(BUDGET_REL, key, data[key]))
    pending = data["pending"]
    if not isinstance(pending, list) or not all(isinstance(cid, str) and CID_RE.match(cid) for cid in pending):
        raise ValueError("{}: pending must be a list of corpus-ids, got {!r}".format(BUDGET_REL, pending))
    if len(set(pending)) != len(pending):
        raise ValueError("{}: pending repeats an id".format(BUDGET_REL))
    if pending != sorted(pending):
        raise ValueError("{}: pending is not sorted".format(BUDGET_REL))
    known = {rule.cid for rule in rules}
    unknown = [cid for cid in pending if cid not in known]
    if unknown:
        raise ValueError("{}: pending names no rule of the corpus: {}".format(BUDGET_REL, ", ".join(unknown)))
    return data


def render(rules):
    """The CORE render text."""
    lines = [GENERATED_COMMENT, "", TITLE, "", PREAMBLE]
    ordered = sorted(rules, key=lambda rule: (GROUP_ORDER[rule.group], sort_key(rule.fm)))
    current = None
    for rule in ordered:
        if rule.group != current:
            lines += ["", "## " + GROUP_LABEL[rule.group]]
            current = rule.group
        lines += ["", rule.entry()]
    return "\n".join(lines) + "\n"


# --- the guards ---------------------------------------------------------------------------------------
# Each guard returns its finding lines. They sit in two name-keyed tables, so the self-test can switch one
# off and show the case it covers then fails (the flip).

def _over_cap_not_pending(rules, budget, _text, _seed):
    return ["{}: the entry is {} units, over entry-cap {}, and the rule is neither split nor on pending".format(
        rule.cid, units(rule.layer), budget["entry-cap"]) for rule in rules
        if not rule.apex and not rule.split and units(rule.layer) > budget["entry-cap"]
        and rule.cid not in budget["pending"]]


def _core_over_cap(rules, budget, _text, _seed):
    return ["{}: the core layer is {} units, over entry-cap {}".format(rule.cid, units(rule.layer),
                                                                        budget["entry-cap"])
            for rule in rules if rule.split and units(rule.layer) > budget["entry-cap"]]


def _trigger_over_cap(rules, budget, _text, _seed):
    return ["{}: the detail-trigger is {} characters, over trigger-cap {}".format(
        rule.cid, len(rule.trigger), budget["trigger-cap"]) for rule in rules
        if rule.split and len(rule.trigger) > budget["trigger-cap"]]


def _pending_grew(_rules, budget, _text, seed):
    return ["{}: on pending but not in PENDING_SEED; the pending list may only shrink".format(cid)
            for cid in budget["pending"] if cid not in seed]


def _pending_stale(rules, budget, _text, _seed):
    by_id = {rule.cid: rule for rule in rules}
    stale = []
    for cid in budget["pending"]:
        rule = by_id[cid]
        if rule.apex or rule.split or units(rule.layer) <= budget["entry-cap"]:
            why = "the apex" if rule.apex else "split" if rule.split else "within entry-cap"
            stale.append("{}: on pending but {} now; remove it from pending".format(cid, why))
    return stale


def _file_over_cap(_rules, budget, text, _seed):
    if budget["pending"] or units(text) <= budget["file-cap"]:
        return []
    return ["the render is {} units, over file-cap {}".format(units(text), budget["file-cap"])]


SOURCE_GUARDS = {"over-cap-not-pending": _over_cap_not_pending, "core-over-cap": _core_over_cap,
                 "trigger-over-cap": _trigger_over_cap, "pending-grew": _pending_grew,
                 "pending-stale": _pending_stale, "file-over-cap": _file_over_cap}


def _entries(text):
    """(line number, line, corpus-id or None) for every entry line: each non-blank line after the first
    `## ` heading that is not itself a heading."""
    found, seen_heading = [], False
    for number, line in enumerate(text.split("\n"), 1):
        if line.startswith("## "):
            seen_heading = True
        elif seen_heading and line.strip():
            match = ENTRY_RE.match(line)
            found.append((number, line, match.group(2) if match else None))
    return found


def _frontmatter(text, _rules, _root):
    first = next((line for line in text.split("\n") if line.strip()), "")
    return ["frontmatter present (the first non-blank line is '---')"] if first.strip() == "---" else []


def _at_import(text, _rules, _root):
    return ["line {}: holds an '@', which the instruction-budget gate reads as an import".format(number)
            for number, line in enumerate(text.split("\n"), 1) if "@" in line]


def _missing_id(text, rules, _root):
    present = {cid for _number, _line, cid in _entries(text)}
    return ["missing id: {} has no entry".format(rule.cid) for rule in rules if rule.cid not in present]


def _duplicate_id(text, _rules, _root):
    counts = {}
    for _number, _line, cid in _entries(text):
        if cid is not None:
            counts[cid] = counts.get(cid, 0) + 1
    return ["duplicate id: {} has {} entries".format(cid, count) for cid, count in sorted(counts.items())
            if count > 1]


def _unknown_id(text, rules, _root):
    known = {rule.cid for rule in rules}
    return ["line {}: the entry names {}".format(number, "no corpus-id" if cid is None else
                                                 "{}, which the corpus does not hold".format(cid))
            for number, _line, cid in _entries(text) if cid not in known]


def _entry_differs(text, rules, _root):
    expected = {rule.cid: rule.entry() for rule in rules}
    return ["line {}: the entry for {} differs from its source layer".format(number, cid)
            for number, line, cid in _entries(text) if cid in expected and line != expected[cid]]


def _dangling_pointer(text, _rules, root):
    findings = []
    real_root = os.path.realpath(root)
    for number, line in enumerate(text.split("\n"), 1):
        for match in POINTER_PATH_RE.finditer(line):
            rel = match.group(1)
            real = os.path.realpath(os.path.join(real_root, rel))
            inside = real.startswith(real_root.rstrip(os.sep) + os.sep)
            try:
                regular = inside and stat.S_ISREG(os.stat(real).st_mode)
            except OSError:
                regular = False
            if not regular:
                findings.append("line {}: dangling pointer: {} is not a regular file inside the "
                                "repository".format(number, rel))
    return findings


RENDER_GUARDS = {"frontmatter": _frontmatter, "at-import": _at_import, "missing-id": _missing_id,
                 "duplicate-id": _duplicate_id, "unknown-id": _unknown_id, "entry-differs": _entry_differs,
                 "dangling-pointer": _dangling_pointer}


def source_findings(rules, budget, text, seed):
    return [line for name in SOURCE_GUARDS for line in SOURCE_GUARDS[name](rules, budget, text, seed)]


def render_findings(text, rules, root):
    return [line for name in RENDER_GUARDS for line in RENDER_GUARDS[name](text, rules, root)]


def run(root, check_mode, seed=None):
    """Reconcile root's CORE render. Exit 0 clean, 1 on drift (check mode) or a finding, 2 when it cannot
    evaluate (see the module docstring). seed stands in for PENDING_SEED (the self-test's synthetic trees)."""
    seed = PENDING_SEED if seed is None else seed
    try:
        rules = load_rules(root)
        budget = load_budget(root, rules)
    except (ValueError, OSError) as exc:
        print("error: {} (cannot evaluate)".format(exc), file=sys.stderr)
        return 2
    text = render(rules)
    findings = source_findings(rules, budget, text, seed) + render_findings(text, rules, root)
    target = root / CORE_REL
    drift = []
    if check_mode:
        target_dir_present(target, root)
        try:
            current = target.read_text(encoding="utf-8") if target.exists() else None
        except (OSError, UnicodeError) as exc:
            print("error: cannot read {} ({}) (cannot evaluate)".format(target, exc), file=sys.stderr)
            return 2
        if current is None:
            drift.append("{} is absent".format(CORE_REL))
        elif current != text:
            drift.append("{} differs from a fresh render".format(CORE_REL))
            findings += ["committed: " + line for line in render_findings(current, rules, root)]
    for line in findings:
        print("finding: " + line)
    for line in drift:
        print("drift: " + line)
    if drift:
        print("run tools/gen_rules_core.py to regenerate")
    if findings or drift:
        return 1
    if not check_mode:
        reconcile(target, text, False, root, make_parents=True)
    split = sum(rule.split for rule in rules)
    print("rules-core: {} entries ({} whole, {} split, {} pending); {} units against file-cap {} ({})".format(
        len(rules), len(rules) - split, split, len(budget["pending"]), units(text), budget["file-cap"],
        "reported only while pending is not empty" if budget["pending"] else "enforced"))
    return 0


USAGE = "gen_rules_core.py [--check | --self-test]"


def main():
    argv = sys.argv[1:]
    # The execution gate (tools/check_selftest_execution.py) appends `--execution-report ABS_PATH` to the
    # self-test launch; accepted only in that trailing two-token form, with an absolute path, alone or after
    # --self-test. Every other unknown token is refused (exit 2) before any dispatch.
    report_path = None
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        report_path, argv = argv[-1], ["--self-test"]
    if unknown_args_refused(argv, USAGE):
        return 2
    if "--self-test" in argv:
        if report_path is not None:
            # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return self_test_main()
    return run(repo_root(), "--check" in argv)


# --- self-test ----------------------------------------------------------------------------------------
# Synthetic trees in a temporary directory only, never the real tree. The registered execution-suite
# identity (tools/selftest_checks.toml) and the executed-check-id record the check() helper appends to.
SUITE_ID = "rules-core-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name))
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


_FM = "---\ncorpus-id: {cid}\norigin: pack\nfamily: {family}\n{keys}slug: {slug}\n---\n\n"
_SPLIT_REL = "aiqt/10-QUALI-split-rule.md"
_LONG_BODY = ("This fixture body is long on purpose, so that its joined text runs past the small entry cap the "
              "fixture budget sets.\n")
_SEED = frozenset(("longaa1",))


def _source(cid, slug, keys, body, family="aiqt"):
    return _FM.format(cid=cid, family=family, keys=keys, slug=slug) + body


def _fixture_sources():
    return {
        "00-apex.md": _source("prjint1", "project-integrity", "apex: true\n",
                              "# Apex rule\n\nThe apex fixture rule, which states the ordering.\n"),
        "short.md": _source("shrtaa1", "short-rule", "tier: 10\nfacet: ACCUR\n",
                            "# Short rule\n\nA short fixture\nrule body.\n"),
        "long.md": _source("longaa1", "long-rule", "tier: 10\nfacet: INTEG\n", "# Long rule\n\n" + _LONG_BODY),
        "split.md": _source("splita1", "split-rule", "tier: 10\nfacet: QUALI\ndetail-trigger: editing a fixture\n",
                            "# Split rule\n\nThe core sentence.\n\n## Detail\n\nThe full rule text, longer than "
                            "the core layer, holding every clause.\n"),
        "sec.md": _source("secaa11", "sec-rule", "facet: SECC\n", "# Security rule\n\nA security fixture rule.\n",
                          family="security"),
    }


def _budget_text(entry_cap=80, trigger_cap=60, file_cap=100000, pending=("longaa1",), extra=""):
    return "format-version = 1\nentry-cap = {}\ntrigger-cap = {}\nfile-cap = {}\npending = [{}]\n{}".format(
        entry_cap, trigger_cap, file_cap, ", ".join('"{}"'.format(cid) for cid in pending), extra)


def _tree(base, sources=None, budget=None, reference=True):
    """A synthetic tree at base: a git marker, the corpus, the budget and, when reference, the split rule's
    reference file. Returns base."""
    (base / ".git").mkdir(parents=True)
    rules_dir = base / ".aiqt" / "core" / "rules"
    rules_dir.mkdir(parents=True)
    for name, text in (_fixture_sources() if sources is None else sources).items():
        (rules_dir / name).write_text(text, encoding="utf-8")
    (base / BUDGET_REL).write_text(_budget_text() if budget is None else budget, encoding="utf-8")
    if reference:
        ref = base / REFERENCE_PREFIX / _SPLIT_REL
        ref.parent.mkdir(parents=True)
        ref.write_text("The full rule.\n", encoding="utf-8")
    return base


def _run(root, check_mode, seed=_SEED):
    """(exit, captured stdout and stderr) of run(root, check_mode), SystemExit read as its code."""
    import io
    from contextlib import redirect_stderr, redirect_stdout
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(out):
        try:
            code = run(root, check_mode, seed)
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue()


@contextmanager
def _without(table, name):
    """The guard table[name] switched off for the block (the flip)."""
    saved = table[name]
    table[name] = lambda *_args: []
    try:
        yield
    finally:
        table[name] = saved


def _core_text(root):
    return (root / CORE_REL).read_text(encoding="utf-8")


def _committed_case(tmp, label, mutate):
    """A generated tree whose committed render mutate(text) rewrites; returns the tree."""
    root = _tree(tmp / label)
    _run(root, False)
    (root / CORE_REL).write_text(mutate(_core_text(root)), encoding="utf-8")
    return root


def _both(root, seed=_SEED):
    """(check exit, write exit, render unchanged) for a tree that may already hold a render."""
    target = root / CORE_REL
    before = target.read_bytes() if target.exists() else None
    codes = (_run(root, True, seed)[0], _run(root, False, seed)[0])
    after = target.read_bytes() if target.exists() else None
    return codes[0], codes[1], before == after


def _argv_failures():
    """One line per refusal vector main() mishandles: exit 2, run never reached."""
    import io
    from contextlib import redirect_stderr, redirect_stdout
    calls, failures = [], []
    namespace = globals()
    saved = namespace["run"], sys.argv
    namespace["run"] = lambda _root, check_mode, *_rest: calls.append(check_mode) or 0
    try:
        for argv in (["--chek"], ["--check", "--chek"], ["--self-test", "--chek"], ["--help"], ["check"],
                     ["--root", "--chek"], ["--execution-report", "relative/report"]):
            del calls[:]
            sys.argv = ["gen_rules_core.py"] + argv
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), redirect_stderr(err):
                got = main()
            if got != 2 or calls or not err.getvalue().rstrip("\n").endswith("usage: " + USAGE):
                failures.append("{}: exit {}, run calls {}, stderr {!r}".format(argv, got, calls, err.getvalue()))
    finally:
        namespace["run"], sys.argv = saved
    return failures


def self_test_main():
    import shutil
    import tempfile
    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-rules-core-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc))
        return 2
    try:
        _self_test_cases(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if FAILURES:
        print("SELF-TEST FAIL: gen_rules_core {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: gen_rules_core {} checks (a synthetic tree renders, re-checks clean and renders the "
          "same bytes twice, with a whole entry, a split entry with its pointer, a pending entry kept whole and "
          "the groups in index order; a missing id, a duplicate id, an entry differing from its source layer, an "
          "over-cap entry not on pending, an '@', frontmatter and a dangling pointer are each caught, and each "
          "case fails with its guard switched off; every malformed or missing input is cannot-evaluate, exit 2, "
          "with nothing written)".format(len(EXECUTED)))
    return 0


def _self_test_cases(tmp):
    # --- a well-formed tree ---
    good = _tree(tmp / "good")
    first = _run(good, False)
    first_bytes = (good / CORE_REL).read_bytes() if (good / CORE_REL).exists() else None
    again = _run(good, False)
    check("good/generate-then-check-clean", (first[0], again[0], _run(good, True)[0]) == (0, 0, 0)
          and first_bytes == (good / CORE_REL).read_bytes(), (first, again))
    text = _core_text(good) if (good / CORE_REL).exists() else ""
    lines = text.split("\n")
    check("good/opens-with-comment-title-preamble",
          lines[:5] == [GENERATED_COMMENT, "", TITLE, "", PREAMBLE] and units(PREAMBLE) <= PREAMBLE_CAP,
          lines[:5])
    check("good/whole-entry-is-joined-body", "Short rule (shrtaa1). A short fixture rule body." in lines, lines)
    check("good/split-entry-is-core-plus-pointer",
          "Split rule (splita1). The core sentence. Full rule, read before editing a fixture: `"
          + REFERENCE_PREFIX + _SPLIT_REL + "`" in lines, lines)
    check("good/pending-entry-kept-whole", "Long rule (longaa1). " + _LONG_BODY.strip() in lines, lines)
    check("good/groups-in-index-order", [line for line in lines if line.startswith("## ")]
          == ["## Apex", "## Accuracy", "## Integrity", "## Quality", "## Security"], lines)
    capped = _tree(tmp / "capped", budget=_budget_text(file_cap=10))
    check("good/file-cap-reported-while-pending", _run(capped, False)[0] == 0, _run(capped, True))

    # --- drift ---
    edited = _committed_case(tmp, "edited", lambda t: t.replace(PREAMBLE, PREAMBLE + " Edited."))
    check("drift/hand-edit-is-exit-1", _run(edited, True)[0] == 1, _run(edited, True))
    absent = _tree(tmp / "absent")
    check("drift/absent-render-is-exit-1", _run(absent, True)[0] == 1, _run(absent, True))

    # --- the seven plan cases, each with its flip ---
    root = _committed_case(tmp, "missing", lambda t: t.replace("Short rule (shrtaa1). A short fixture rule "
                                                               "body.\n\n", ""))
    code, out = _run(root, True)
    check("case/missing-id", code == 1 and "missing id: shrtaa1" in out, out)
    with _without(RENDER_GUARDS, "missing-id"):
        code, out = _run(root, True)
    check("flip/missing-id", "missing id: shrtaa1" not in out, out)

    root = _committed_case(tmp, "duplicate", lambda t: t + "\nShort rule (shrtaa1). A short fixture rule body.\n")
    code, out = _run(root, True)
    check("case/duplicate-id", code == 1 and "duplicate id: shrtaa1 has 2 entries" in out, out)
    with _without(RENDER_GUARDS, "duplicate-id"):
        code, out = _run(root, True)
    check("flip/duplicate-id", "duplicate id: shrtaa1" not in out, out)

    root = _committed_case(tmp, "differs", lambda t: t.replace("A short fixture rule body.", "A short rule."))
    code, out = _run(root, True)
    check("case/entry-differs", code == 1 and "the entry for shrtaa1 differs from its source layer" in out, out)
    with _without(RENDER_GUARDS, "entry-differs"):
        code, out = _run(root, True)
    check("flip/entry-differs", "differs from its source layer" not in out, out)

    root = _tree(tmp / "overcap", budget=_budget_text(pending=()))
    code, out = _run(root, True)
    written = (root / CORE_REL).exists()
    check("case/over-cap-not-pending", (code, _run(root, False)[0], (root / CORE_REL).exists(), written)
          == (1, 1, False, False) and "longaa1: the entry is" in out, out)
    with _without(SOURCE_GUARDS, "over-cap-not-pending"):
        flipped = (_run(root, False)[0], _run(root, True)[0])
    check("flip/over-cap-not-pending", flipped == (0, 0), flipped)

    sources = _fixture_sources()
    sources["short.md"] = sources["short.md"].replace("rule body.", "rule body, see docs/x.md and @docs/x.md.")
    root = _tree(tmp / "at", sources=sources)
    code, out = _run(root, True)
    check("case/at-import", (code, _run(root, False)[0], (root / CORE_REL).exists()) == (1, 1, False)
          and "holds an '@'" in out, out)
    with _without(RENDER_GUARDS, "at-import"):
        flipped = (_run(root, False)[0], _run(root, True)[0])
    check("flip/at-import", flipped == (0, 0), flipped)

    root = _committed_case(tmp, "frontmatter", lambda t: "---\ntitle: core\n---\n\n" + t)
    code, out = _run(root, True)
    check("case/frontmatter", code == 1 and "committed: frontmatter present" in out, out)
    with _without(RENDER_GUARDS, "frontmatter"):
        code, out = _run(root, True)
    check("flip/frontmatter", "frontmatter present" not in out, out)

    root = _tree(tmp / "dangling", reference=False)
    code, out = _run(root, True)
    check("case/dangling-pointer", (code, _run(root, False)[0], (root / CORE_REL).exists()) == (1, 1, False)
          and "dangling pointer: " + REFERENCE_PREFIX + _SPLIT_REL in out, out)
    with _without(RENDER_GUARDS, "dangling-pointer"):
        flipped = (_run(root, False)[0], _run(root, True)[0])
    check("flip/dangling-pointer", flipped == (0, 0), flipped)

    # --- the other findings ---
    root = _tree(tmp / "grew")
    code, out = _run(root, True, frozenset())
    check("finding/pending-grew", code == 1 and "longaa1: on pending but not in PENDING_SEED" in out, out)
    root = _tree(tmp / "stale", budget=_budget_text(pending=("longaa1", "shrtaa1")))
    code, out = _run(root, True, frozenset(("longaa1", "shrtaa1")))
    check("finding/pending-stale", code == 1 and "shrtaa1: on pending but within entry-cap now" in out, out)
    root = _tree(tmp / "trigger", budget=_budget_text(trigger_cap=5))
    code, out = _run(root, True)
    check("finding/trigger-over-cap", code == 1 and "splita1: the detail-trigger is 17 characters" in out, out)
    root = _tree(tmp / "corecap", budget=_budget_text(entry_cap=5, pending=("longaa1", "secaa11", "shrtaa1")))
    code, out = _run(root, True, frozenset(("longaa1", "secaa11", "shrtaa1")))
    check("finding/core-over-cap", code == 1 and "splita1: the core layer is 18 units" in out, out)
    sources = _fixture_sources()
    sources["long.md"] = sources["long.md"].replace(_LONG_BODY, "A long rule, now short.\n")
    root = _tree(tmp / "filecap", sources=sources, budget=_budget_text(file_cap=10, pending=()))
    code, out = _run(root, True)
    check("finding/file-over-cap-when-pending-empty", code == 1 and "over file-cap 10" in out, out)

    # --- every malformed or missing input is cannot-evaluate (exit 2), with nothing written ---
    def cannot(label, sources=None, budget=None, setup=None):
        root = _tree(tmp / label, sources=sources, budget=budget)
        if setup is not None:
            setup(root)
        return _both(root)

    def drop_budget(root):
        (root / BUDGET_REL).unlink()

    def dir_budget(root):
        (root / BUDGET_REL).unlink()
        (root / BUDGET_REL).mkdir()

    def link_budget(root):
        real = root / "budget-elsewhere.toml"
        (root / BUDGET_REL).rename(real)
        (root / BUDGET_REL).symlink_to(real)

    check("cannot-evaluate/budget-absent", cannot("b-absent", setup=drop_budget) == (2, 2, True))
    check("cannot-evaluate/budget-directory", cannot("b-dir", setup=dir_budget) == (2, 2, True))
    check("cannot-evaluate/budget-symlink", cannot("b-link", setup=link_budget) == (2, 2, True))
    check("cannot-evaluate/budget-not-toml", cannot("b-toml", budget="entry-cap = \n") == (2, 2, True))
    check("cannot-evaluate/budget-unknown-key",
          cannot("b-unknown", budget=_budget_text(extra="ceiling = 5\n")) == (2, 2, True))
    check("cannot-evaluate/budget-missing-key",
          cannot("b-missing", budget=_budget_text().replace("trigger-cap = 60\n", "")) == (2, 2, True))
    check("cannot-evaluate/budget-format-version",
          cannot("b-version", budget=_budget_text().replace("format-version = 1", "format-version = 2"))
          == (2, 2, True))
    caps = [cannot("b-cap-" + str(index), budget=_budget_text(entry_cap=value)) for index, value in
            enumerate(("0", "-5", "true", '"80"', "8.5"))]
    check("cannot-evaluate/budget-cap-not-positive-int", caps == [(2, 2, True)] * 5, caps)
    check("cannot-evaluate/budget-pending-not-ids",
          cannot("b-pending", budget=_budget_text().replace('pending = ["longaa1"]', "pending = [7]"))
          == (2, 2, True))
    check("cannot-evaluate/budget-pending-unsorted",
          cannot("b-unsorted", budget=_budget_text(pending=("shrtaa1", "longaa1"))) == (2, 2, True))
    check("cannot-evaluate/budget-pending-repeated",
          cannot("b-repeat", budget=_budget_text(pending=("longaa1", "longaa1"))) == (2, 2, True))
    check("cannot-evaluate/budget-pending-unknown-id",
          cannot("b-unknownid", budget=_budget_text(pending=("longaa1", "zzzzzz9"))) == (2, 2, True))

    def drop_rules(root):
        shutil_rmtree(root / ".aiqt" / "core" / "rules")

    def empty_rules(root):
        for path in (root / ".aiqt" / "core" / "rules").iterdir():
            path.unlink()

    check("cannot-evaluate/rules-absent", cannot("r-absent", setup=drop_rules) == (2, 2, True))
    check("cannot-evaluate/rules-empty", cannot("r-empty", setup=empty_rules) == (2, 2, True))
    sources = _fixture_sources()
    sources["short.md"] = sources["short.md"].replace("corpus-id: shrtaa1\n", "")
    check("cannot-evaluate/source-missing-corpus-id", cannot("s-noid", sources=sources) == (2, 2, True))
    sources = _fixture_sources()
    sources["short2.md"] = sources["short.md"].replace("slug: short-rule", "slug: short-rule-two")
    check("cannot-evaluate/source-duplicate-corpus-id", cannot("s-dupid", sources=sources) == (2, 2, True))
    sources = _fixture_sources()
    sources["split.md"] = sources["split.md"].replace("The core sentence.\n\n", "")
    check("cannot-evaluate/split-empty-core", cannot("s-emptycore", sources=sources) == (2, 2, True))
    root = _tree(tmp / "t-notutf8")
    _run(root, False)
    (root / CORE_REL).write_bytes(b"\xff\n")
    check("cannot-evaluate/render-not-utf8", _run(root, True)[0] == 2, _run(root, True))
    root = _tree(tmp / "t-renders-file")
    (root / ".aiqt" / "renders").write_text("not a directory\n", encoding="utf-8")
    check("cannot-evaluate/renders-not-a-directory", _both(root)[:2] == (2, 2))
    failures = _argv_failures()
    check("cannot-evaluate/unknown-argument", not failures, failures)


def shutil_rmtree(path):
    import shutil
    shutil.rmtree(path)


if __name__ == "__main__":
    _code = main()
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
