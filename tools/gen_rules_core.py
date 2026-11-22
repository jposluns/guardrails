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

BUDGET (.aiqt/core/core-budget.toml). Exactly two top-level keys: format-version (1) and accepted, a
non-empty array of tables, the log of every accepted budget in order. Each entry has exactly these keys:
kind ("seed" for the first entry and only the first, then "shrink" or "raise"); note, a non-empty string
saying why; entry-cap, the largest joined layer an entry may carry, in UTF-16 units, title and id excluded;
trigger-cap, the longest detail-trigger in characters; file-cap, the largest CORE render in UTF-16 units;
and pending, the sorted list of corpus-ids over entry-cap that are not split yet. Each cap is a positive
integer. The LAST entry is the budget in force. The apex is never split (the source grammar forbids it), so
it is always kept whole and no cap applies to its entry. A pending rule is rendered whole until it is
split. file-cap is enforced once pending is empty; until then the render is larger than it by construction,
and the measured size is printed against it.

THE RATCHET. Caps and pending change only by appending an entry, each compared with the entry before it:
  - the seed entry must equal the budget as this renderer was added (CAP_SEED and PENDING_SEED here);
  - a shrink entry may only lower a cap or drop a pending id, never raise a cap or list an id the entry
    before it does not, and must change something, so a shrink cannot be undone by a second shrink entry;
  - a raise entry is the only way to raise a cap or put an id back on pending (or add a new one); its kind
    and note make the raise visible in the file, and every passing run prints how many raise entries the log
    holds.
An id on the budget in force that no longer needs to be there (now split, now within entry-cap, or the
apex) is a finding until a shrink entry drops it. The gate reads only the committed file, not git history:
deleting or rewriting an entry already in the log is not caught here and shows only as removed lines in
review. A pending id must be a corpus-id in every entry, and one the corpus holds in the budget in force.

EXIT. 0 clean. 1 on drift (--check) or on a finding, and a finding in write mode writes nothing:
  - a non-apex rule over entry-cap with no split and not on pending; a split rule whose core layer is over
    entry-cap; a detail-trigger over trigger-cap; a seed entry that differs from CAP_SEED and PENDING_SEED;
    a shrink entry that lists a pending id the entry before it does not, raises a cap, or changes nothing;
    a pending id that no longer needs to be there; the render over file-cap while pending is empty;
  - in the fresh render, and in --check also in the committed file when it differs from the fresh render:
    a frontmatter block; an `@` anywhere (the instruction-budget gate reads an `@` as an import even inside a
    code span, so no `@` is allowed at all); a corpus-id with no entry; a corpus-id with more than one entry;
    an entry line that names no corpus-id or one the corpus does not hold; an entry that differs from the
    entry its source layer renders; and a pointer to a reference path that is not a regular file inside the
    repository (a dangling pointer).
DIAGNOSTIC GUARDS. Five of those render guards (frontmatter, missing id, duplicate id, unknown id, entry
differs) are diagnostic: no case the self-test builds makes one of them change an exit code. In --check,
every committed file they fire on already differs from the fresh render, which is drift (exit 1), so there
they only name the reason; and no self-test source makes them fire on a fresh render (they would fire there
only if this renderer wrote an entry line its own pattern misreads). Their flips show the named reason goes
while the exit stays 1. The other two render guards (an `@`, a dangling pointer) fire on a fresh render
from a source, and their flips show the exit change from 1 to 0.
DRIFT is byte identity: the committed file's bytes must equal the fresh render encoded as UTF-8, so a
CRLF or a lone CR in place of an LF is drift.
2 cannot evaluate, never a clean pass, for every malformed or missing input: a .aiqt/core/rules/ that is
absent, not a directory inside the repository, unreadable or holds no source; a malformed or non-UTF-8
source or a duplicate corpus-id (gen_rules.load_corpus); a source with no '# ' title, whose group is not in
GROUPS, whose body is empty, or whose split leaves an empty core layer; a core-budget.toml that is absent,
not a regular file (a symlink or a directory), unreadable, not UTF-8, not TOML, or off the schema above (an
unknown or missing key, an empty or missing log, a kind out of place or unknown, an empty note, a cap that
is not a positive integer, a format-version other than 1, a pending entry that is not an id, an unsorted or
repeated pending list, or a pending id in force the corpus does not hold); a committed render that is not
UTF-8; and a .aiqt/renders/ that cannot be read as a directory inside the repository. A committed render
that is absent is drift (exit 1), as for every generated file.

  gen_rules_core.py               regenerate .aiqt/renders/aiqt-core.md
  gen_rules_core.py --check       exit 1 on drift or a finding, 2 when it cannot evaluate; write nothing
  gen_rules_core.py --self-test   synthetic trees: every finding and every cannot-evaluate input; a flip
                                  for every guard, for the byte comparison and for the config-lane
                                  registration of this file's bare launch, showing its case changes outcome
                                  with it switched off (the exit, or for a diagnostic guard only its named
                                  reason); and a backstop row for each load check (no-title, empty-layer,
                                  group-outside), whose case another step also refuses
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
BUDGET_KEYS = ("format-version", "accepted")
ENTRY_KEYS = ("kind", "note", "entry-cap", "trigger-cap", "file-cap", "pending")
CAP_KEYS = ("entry-cap", "trigger-cap", "file-cap")
KINDS = ("seed", "shrink", "raise")
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
# The budget as it stood when this renderer was added. The seed entry of core-budget.toml must equal it, so
# the start of the log cannot be edited without a reviewed edit here; later entries are judged against the
# entry before them (THE RATCHET in the module docstring).
CAP_SEED = {"entry-cap": 400, "trigger-cap": 60, "file-cap": 56000}
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
    """(title text, the body lines below the title) of a rule source; (None, the body lines from the first
    non-blank one) when that line is not a '# ' title, which LOAD_CHECKS no-title refuses."""
    lines = text.split("\n")
    for index in range(body_first_line(text, name) - 1, len(lines)):
        if lines[index] != "":
            if not lines[index].startswith("# ") or not lines[index][2:].strip():
                return None, lines[index:]
            return lines[index][2:].strip(), lines[index + 1:]
    return None, []


# The load checks: each returns a cannot-evaluate reason for one loaded rule, or None. They sit in a
# name-keyed table, so the self-test can switch one off and show its case is then not cannot-evaluate.
def _no_title(rule, name):
    return "{}: the first body line is not a '# ' title".format(name) if rule.title is None else None


def _empty_layer(rule, name):
    if rule.layer:
        return None
    if rule.split:
        return "{}: the '## Detail' split leaves an empty core layer".format(name)
    return "{}: the body below the title is empty".format(name)


def _group_outside(rule, name):
    if rule.group in GROUP_ORDER:
        return None
    return "{}: group {!r} is not in gen_claude GROUPS".format(name, rule.group)


LOAD_CHECKS = {"no-title": _no_title, "empty-layer": _empty_layer, "group-outside": _group_outside}


def load_rules(root):
    """Every rule of root's corpus as a Rule. Raises ValueError or OSError (cannot evaluate) on a corpus
    directory that is absent, not a directory inside root or holds no source, on a malformed source or a
    duplicate corpus-id, and on a LOAD_CHECKS reason: no title, a group outside GROUPS, an empty body or an
    empty core layer."""
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
        else:
            layer, trigger = _joined(core.split("\n")), fm["detail-trigger"]
        rule = Rule(str(fm["corpus-id"]), fm, rel, title, layer, trigger)
        for name in LOAD_CHECKS:
            problem = LOAD_CHECKS[name](rule, src.name)
            if problem is not None:
                raise ValueError(problem)
        rules.append(rule)
    return rules


def load_budget(root, rules):
    """The validated core-budget.toml as a dict: the budget in force (the last accepted entry's caps and
    pending) and, under "log", every accepted entry in order. Raises ValueError or OSError (cannot evaluate)
    on a file that is absent, not a regular file, unreadable, not TOML or off the schema."""
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
    _exact_keys(data, BUDGET_KEYS, BUDGET_REL)
    version = data["format-version"]
    if type(version) is not int or version != 1:
        raise ValueError("{}: format-version must be 1, got {!r}".format(BUDGET_REL, version))
    log = data["accepted"]
    if not isinstance(log, list) or not log or not all(isinstance(entry, dict) for entry in log):
        raise ValueError("{}: accepted must be a non-empty array of tables ([[accepted]])".format(BUDGET_REL))
    for number, entry in enumerate(log, 1):
        where = "{} accepted entry {}".format(BUDGET_REL, number)
        _exact_keys(entry, ENTRY_KEYS, where)
        allowed = KINDS[:1] if number == 1 else KINDS[1:]
        if entry["kind"] not in allowed:
            raise ValueError("{}: kind must be {}, got {!r}".format(
                where, " or ".join('"{}"'.format(kind) for kind in allowed), entry["kind"]))
        if not isinstance(entry["note"], str) or not entry["note"].strip():
            raise ValueError("{}: note must be a non-empty string".format(where))
        for key in CAP_KEYS:
            if type(entry[key]) is not int or entry[key] <= 0:
                raise ValueError("{}: {} must be a positive integer, got {!r}".format(where, key, entry[key]))
        pending = entry["pending"]
        if not isinstance(pending, list) or not all(isinstance(cid, str) and CID_RE.match(cid)
                                                    for cid in pending):
            raise ValueError("{}: pending must be a list of corpus-ids, got {!r}".format(where, pending))
        if len(set(pending)) != len(pending):
            raise ValueError("{}: pending repeats an id".format(where))
        if pending != sorted(pending):
            raise ValueError("{}: pending is not sorted".format(where))
    known = {rule.cid for rule in rules}
    unknown = [cid for cid in log[-1]["pending"] if cid not in known]
    if unknown:
        raise ValueError("{}: pending in force names no rule of the corpus: {}".format(
            BUDGET_REL, ", ".join(unknown)))
    budget = {key: log[-1][key] for key in CAP_KEYS + ("pending",)}
    budget["log"] = log
    return budget


def _exact_keys(table, keys, where):
    """Raise ValueError unless table holds exactly keys."""
    missing = [key for key in keys if key not in table]
    extra = sorted(key for key in table if key not in keys)
    if missing or extra:
        raise ValueError("{}: missing key(s) {} and unknown key(s) {}; the keys are exactly {}".format(
            where, missing, extra, ", ".join(keys)))


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
# off and show the outcome of the case it covers then changes (the flip): the exit, or for a diagnostic
# guard (DIAGNOSTIC GUARDS in the module docstring) only its named reason.

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


def _seed_altered(_rules, budget, _text, seed):
    first = budget["log"][0]
    changed = [key for key in CAP_KEYS if first[key] != seed[key]]
    if set(first["pending"]) != set(seed["pending"]):
        changed.append("pending")
    return ["accepted entry 1 (the seed): {} differ(s) from CAP_SEED and PENDING_SEED in "
            "tools/gen_rules_core.py; append a shrink or raise entry instead".format(", ".join(changed))
            ] if changed else []


def _shrink_entries(budget):
    """(entry number, entry, the entry before it) for every shrink entry of the log."""
    log = budget["log"]
    return [(number, log[number - 1], log[number - 2]) for number in range(2, len(log) + 1)
            if log[number - 1]["kind"] == "shrink"]


def _pending_grew(_rules, budget, _text, _seed):
    return ["accepted entry {}: {} on pending but not on the entry before it; a shrink entry may not grow "
            "pending, which takes a raise entry".format(number, cid)
            for number, entry, before in _shrink_entries(budget)
            for cid in entry["pending"] if cid not in before["pending"]]


def _cap_raised(_rules, budget, _text, _seed):
    return ["accepted entry {}: {} {} is above {} on the entry before it; a shrink entry may not raise a cap, "
            "which takes a raise entry".format(number, key, entry[key], before[key])
            for number, entry, before in _shrink_entries(budget)
            for key in CAP_KEYS if entry[key] > before[key]]


def _shrink_no_change(_rules, budget, _text, _seed):
    return ["accepted entry {}: a shrink entry that changes nothing from the entry before it".format(number)
            for number, entry, before in _shrink_entries(budget)
            if all(entry[key] == before[key] for key in CAP_KEYS + ("pending",))]


def _pending_stale(rules, budget, _text, _seed):
    by_id = {rule.cid: rule for rule in rules}
    stale = []
    for cid in budget["pending"]:
        rule = by_id[cid]
        if rule.apex or rule.split or units(rule.layer) <= budget["entry-cap"]:
            why = "the apex" if rule.apex else "split" if rule.split else "within entry-cap"
            stale.append("{}: on pending but {} now; append a shrink entry that drops it".format(cid, why))
    return stale


def _file_over_cap(_rules, budget, text, _seed):
    if budget["pending"] or units(text) <= budget["file-cap"]:
        return []
    return ["the render is {} units, over file-cap {}".format(units(text), budget["file-cap"])]


SOURCE_GUARDS = {"over-cap-not-pending": _over_cap_not_pending, "core-over-cap": _core_over_cap,
                 "trigger-over-cap": _trigger_over_cap, "seed-altered": _seed_altered,
                 "pending-grew": _pending_grew, "cap-raised": _cap_raised, "shrink-no-change": _shrink_no_change,
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


def _same_bytes(raw, text):
    """True when the committed bytes raw are exactly the fresh render text encoded as UTF-8 (no line-ending
    translation). A name-keyed function, so the self-test can swap in a line-ending-blind comparison."""
    return raw == text.encode("utf-8")


def run(root, check_mode, seed=None):
    """Reconcile root's CORE render. Exit 0 clean, 1 on drift (check mode) or a finding, 2 when it cannot
    evaluate (see the module docstring). seed stands in for CAP_SEED and PENDING_SEED, a dict of the three
    caps and pending (the self-test's synthetic trees)."""
    seed = dict(CAP_SEED, pending=PENDING_SEED) if seed is None else seed
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
            raw = target.read_bytes() if target.exists() else None
            current = None if raw is None else raw.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            print("error: cannot read {} ({}) (cannot evaluate)".format(target, exc), file=sys.stderr)
            return 2
        if raw is None:
            drift.append("{} is absent".format(CORE_REL))
        elif not _same_bytes(raw, text):
            drift.append("{} differs from a fresh render (bytes compared, line endings included)".format(CORE_REL))
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
    log = budget["log"]
    print("rules-core: {} entries ({} whole, {} split, {} pending); {} units against file-cap {} ({}); budget "
          "log {} accepted entries, {} of them raise entries".format(
              len(rules), len(rules) - split, split, len(budget["pending"]), units(text), budget["file-cap"],
              "reported only while pending is not empty" if budget["pending"] else "enforced",
              len(log), sum(entry["kind"] == "raise" for entry in log)))
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
_SEED = {"entry-cap": 80, "trigger-cap": 60, "file-cap": 100000, "pending": frozenset(("longaa1",))}


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


def _entry_text(kind, entry_cap=80, trigger_cap=60, file_cap=100000, pending=("longaa1",), note="why"):
    return ('\n[[accepted]]\nkind = "{}"\nnote = "{}"\nentry-cap = {}\ntrigger-cap = {}\nfile-cap = {}\n'
            'pending = [{}]\n').format(kind, note, entry_cap, trigger_cap, file_cap,
                                       ", ".join('"{}"'.format(cid) for cid in pending))


def _budget_text(entry_cap=80, trigger_cap=60, file_cap=100000, pending=("longaa1",), extra="", kind=None,
                 entries=None):
    """A budget: the fixture seed entry (_SEED), then, when any value differs from the seed or kind is
    given, one more entry of kind (shrink by default) holding the values; or, given entries, the seed
    entry followed by those _entry_text keyword dicts in order. extra is appended last."""
    text = "format-version = 1\n" + _entry_text("seed")
    values = {"entry_cap": entry_cap, "trigger_cap": trigger_cap, "file_cap": file_cap, "pending": pending}
    if entries is None:
        entries = []
        if kind is not None or values != {"entry_cap": 80, "trigger_cap": 60, "file_cap": 100000,
                                          "pending": ("longaa1",)}:
            entries = [dict(values, kind=kind or "shrink")]
    for entry in entries:
        text += _entry_text(**entry)
    return text + extra


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


def _write_then_check(root, seed=_SEED):
    """(write exit, check exit) for a tree: the check sees the render the write made, if it made one."""
    return _run(root, False, seed)[0], _run(root, True, seed)[0]


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
          "the groups in index order; an over-cap entry not on pending, an '@' and a dangling pointer each exit "
          "1 and exit 0 with their guard switched off; a missing, duplicate or unknown id, an entry differing "
          "from its source layer and frontmatter in the committed file are each named, and lose only that "
          "name with their guard switched off (drift still exits 1); a CRLF or lone CR is drift; a pending id "
          "returning after a shrink, a cap raised by a shrink entry, an altered seed and a shrink that changes "
          "nothing each exit 1 and exit 0 with their guard switched off, as do a stale pending id, a long "
          "trigger, a core over the cap and the file over its cap, and a raise entry is accepted; every "
          "malformed or missing input is cannot-evaluate, exit 2, with nothing written; the config-lane tables "
          "exclude the bare launch and keep the --self-test launch)".format(len(EXECUTED)))
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
    # Byte identity: a CRLF, and a lone CR, in place of an LF are drift; the flip swaps in a comparison
    # that reads the file with line-ending translation, under which both pass.
    def blind(raw, text):
        import io
        return io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8").read() == text
    for label, case_id, flip_id, mutate in (
            ("crlf", "drift/crlf-is-exit-1", "flip/crlf-byte-compare", lambda t: t.replace("\n", "\r\n")),
            ("lone-cr", "drift/lone-cr-is-exit-1", "flip/lone-cr-byte-compare",
             lambda t: t.replace("\n\n", "\n\r", 1))):
        root = _committed_case(tmp, label, lambda t: t)
        (root / CORE_REL).write_bytes(mutate(_core_text(root)).encode("utf-8"))
        code, out = _run(root, True)
        check(case_id, code == 1 and "bytes compared" in out, out)
        saved = globals()["_same_bytes"]
        globals()["_same_bytes"] = blind
        try:
            flipped = _run(root, True)
        finally:
            globals()["_same_bytes"] = saved
        check(flip_id, flipped[0] == 0, flipped)

    # --- the seven plan cases, each with its flip. missing-id, duplicate-id, entry-differs, frontmatter and
    # unknown-id are diagnostic (module docstring): their flips show the named reason goes while the exit
    # stays 1 on drift; the other flips show the exit itself change ---
    root = _committed_case(tmp, "missing", lambda t: t.replace("Short rule (shrtaa1). A short fixture rule "
                                                               "body.\n\n", ""))
    code, out = _run(root, True)
    check("case/missing-id", code == 1 and "missing id: shrtaa1" in out, out)
    with _without(RENDER_GUARDS, "missing-id"):
        code, out = _run(root, True)
    check("flip/missing-id", code == 1 and "missing id: shrtaa1" not in out, out)

    root = _committed_case(tmp, "duplicate", lambda t: t + "\nShort rule (shrtaa1). A short fixture rule body.\n")
    code, out = _run(root, True)
    check("case/duplicate-id", code == 1 and "duplicate id: shrtaa1 has 2 entries" in out, out)
    with _without(RENDER_GUARDS, "duplicate-id"):
        code, out = _run(root, True)
    check("flip/duplicate-id", code == 1 and "duplicate id: shrtaa1" not in out, out)

    root = _committed_case(tmp, "differs", lambda t: t.replace("A short fixture rule body.", "A short rule."))
    code, out = _run(root, True)
    check("case/entry-differs", code == 1 and "the entry for shrtaa1 differs from its source layer" in out, out)
    with _without(RENDER_GUARDS, "entry-differs"):
        code, out = _run(root, True)
    check("flip/entry-differs", code == 1 and "differs from its source layer" not in out, out)

    root = _committed_case(tmp, "unknown", lambda t: t + "\nGhost rule (zzzzzz9). Not a rule of the corpus.\n")
    code, out = _run(root, True)
    check("case/unknown-id", code == 1 and "the entry names zzzzzz9, which the corpus does not hold" in out, out)
    with _without(RENDER_GUARDS, "unknown-id"):
        code, out = _run(root, True)
    check("flip/unknown-id", code == 1 and "zzzzzz9" not in out, out)

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
    check("flip/frontmatter", code == 1 and "frontmatter present" not in out, out)

    root = _tree(tmp / "dangling", reference=False)
    code, out = _run(root, True)
    check("case/dangling-pointer", (code, _run(root, False)[0], (root / CORE_REL).exists()) == (1, 1, False)
          and "dangling pointer: " + REFERENCE_PREFIX + _SPLIT_REL in out, out)
    with _without(RENDER_GUARDS, "dangling-pointer"):
        flipped = (_run(root, False)[0], _run(root, True)[0])
    check("flip/dangling-pointer", flipped == (0, 0), flipped)

    # --- the ratchet: each accepted entry against the one before it, each guard with its flip ---
    shortened = _fixture_sources()
    shortened["long.md"] = shortened["long.md"].replace(_LONG_BODY, "A long rule, now short.\n")
    shrunk = {"kind": "shrink", "pending": ()}
    root = _tree(tmp / "shrunk", sources=shortened, budget=_budget_text(entries=[shrunk]))
    check("ratchet/shrink-entry-accepted", _write_then_check(root) == (0, 0), _run(root, True))
    # The reviewed defect: the rule grows back and its id returns to pending in a second shrink entry.
    root = _tree(tmp / "regrow", budget=_budget_text(entries=[shrunk, {"kind": "shrink"}]))
    code, out = _run(root, True)
    check("ratchet/pending-regrow-after-shrink", (code,) + _both(root)[1:] == (1, 1, True)
          and not (root / CORE_REL).exists()
          and "accepted entry 3: longaa1 on pending but not on the entry before it" in out, out)
    with _without(SOURCE_GUARDS, "pending-grew"):
        flipped = _write_then_check(root)
    check("flip/pending-regrow-after-shrink", flipped == (0, 0), flipped)
    root = _tree(tmp / "raise", budget=_budget_text(entries=[shrunk, {"kind": "raise", "entry_cap": 90}]))
    check("ratchet/raise-entry-accepted", _write_then_check(root) == (0, 0), _run(root, True))
    root = _tree(tmp / "capraise", budget=_budget_text(entries=[shrunk, {"kind": "shrink", "entry_cap": 1000,
                                                                         "pending": ()}]))
    code, out = _run(root, True)
    check("ratchet/cap-raised", code == 1 and "accepted entry 3: entry-cap 1000 is above 80" in out, out)
    with _without(SOURCE_GUARDS, "cap-raised"):
        flipped = _write_then_check(root)
    check("flip/cap-raised", flipped == (0, 0), flipped)
    root = _tree(tmp / "seedalt", budget=_budget_text().replace("file-cap = 100000", "file-cap = 200000"))
    code, out = _run(root, True)
    check("ratchet/seed-altered", (code, _run(root, False)[0], (root / CORE_REL).exists()) == (1, 1, False)
          and "accepted entry 1 (the seed): file-cap differ(s)" in out, out)
    with _without(SOURCE_GUARDS, "seed-altered"):
        flipped = _write_then_check(root)
    check("flip/seed-altered", flipped == (0, 0), flipped)
    root = _tree(tmp / "nochange", budget=_budget_text(entries=[{"kind": "shrink"}]))
    code, out = _run(root, True)
    check("ratchet/shrink-no-change", code == 1 and "accepted entry 2: a shrink entry that changes nothing" in out,
          out)
    with _without(SOURCE_GUARDS, "shrink-no-change"):
        flipped = _write_then_check(root)
    check("flip/shrink-no-change", flipped == (0, 0), flipped)

    # --- the other findings ---
    root = _tree(tmp / "stale", budget=_budget_text(pending=("longaa1", "shrtaa1"), kind="raise"))
    code, out = _run(root, True)
    check("finding/pending-stale", code == 1 and "shrtaa1: on pending but within entry-cap now" in out, out)
    with _without(SOURCE_GUARDS, "pending-stale"):
        flipped = _write_then_check(root)
    check("flip/pending-stale", flipped == (0, 0), flipped)
    root = _tree(tmp / "trigger", budget=_budget_text(trigger_cap=5))
    code, out = _run(root, True)
    check("finding/trigger-over-cap", code == 1 and "splita1: the detail-trigger is 17 characters" in out, out)
    with _without(SOURCE_GUARDS, "trigger-over-cap"):
        flipped = _write_then_check(root)
    check("flip/trigger-over-cap", flipped == (0, 0), flipped)
    root = _tree(tmp / "corecap", budget=_budget_text(entry_cap=5, pending=("longaa1", "secaa11", "shrtaa1"),
                                                      kind="raise"))
    code, out = _run(root, True)
    check("finding/core-over-cap", code == 1 and "splita1: the core layer is 18 units" in out, out)
    with _without(SOURCE_GUARDS, "core-over-cap"):
        flipped = _write_then_check(root)
    check("flip/core-over-cap", flipped == (0, 0), flipped)
    sources = _fixture_sources()
    sources["long.md"] = sources["long.md"].replace(_LONG_BODY, "A long rule, now short.\n")
    root = _tree(tmp / "filecap", sources=sources, budget=_budget_text(file_cap=10, pending=()))
    code, out = _run(root, True)
    check("finding/file-over-cap-when-pending-empty", code == 1 and "over file-cap 10" in out, out)
    with _without(SOURCE_GUARDS, "file-over-cap"):
        flipped = _write_then_check(root)
    check("flip/file-over-cap", flipped == (0, 0), flipped)

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

    # The load checks are backstops: each case below is also refused (exit 2) by the shared rule reader or
    # a later step, so the backstop rows show the case still exits 2, without the local reason, with the
    # load check switched off. The case rows pin which reason a run gives today (the local one only for an
    # empty body), and load/group-outside-reason shows the group check's own verdict on a rule the shared
    # reader cannot produce.
    for label, case_id, backstop_id, check_name, local, edit, local_fires in (
            ("group-outside-groups", "cannot-evaluate/group-outside-groups", "backstop/group-outside-groups",
             "group-outside", "is not in gen_claude GROUPS", lambda s: s.replace("facet: ACCUR", "facet: ZZZZ"),
             False),
            ("empty-body", "cannot-evaluate/empty-body", "backstop/empty-body", "empty-layer",
             "the body below the title is empty", lambda s: s.replace("A short fixture\nrule body.\n", ""), True),
            ("no-title", "cannot-evaluate/no-title", "backstop/no-title", "no-title", "is not a '# ' title",
             lambda s: s.replace("# Short rule\n\n", ""), False)):
        sources = _fixture_sources()
        sources["short.md"] = edit(sources["short.md"])
        root = _tree(tmp / ("l-" + label), sources=sources)
        code, out = _run(root, True)
        check(case_id, _both(root) == (2, 2, True) and code == 2 and (local in out) == local_fires, out)
        with _without(LOAD_CHECKS, check_name):
            flipped = _run(root, True)
        check(backstop_id, flipped[0] == 2 and local not in flipped[1], flipped)
    outside = Rule("shrtaa1", {"facet": "ZZZZ", "family": "aiqt"}, "x.md", "T", "L", None)
    inside = Rule("shrtaa1", {"facet": "ACCUR", "family": "aiqt"}, "x.md", "T", "L", None)
    verdicts = (_group_outside(outside, "x.md"), _group_outside(inside, "x.md"))
    check("load/group-outside-reason", verdicts[1] is None and verdicts[0] is not None
          and "is not in gen_claude GROUPS" in verdicts[0], verdicts)

    # Undecodable inputs (the decode is in the shared readers, so these rows have no flip of their own).
    root = _tree(tmp / "b-notutf8")
    (root / BUDGET_REL).write_bytes(b'format-version = 1\n# \xff\n')
    check("cannot-evaluate/budget-not-utf8", _both(root) == (2, 2, True))
    root = _tree(tmp / "s-notutf8")
    (root / ".aiqt" / "core" / "rules" / "short.md").write_bytes(b"\xff\xfe\n")
    check("cannot-evaluate/source-not-utf8", _both(root) == (2, 2, True))

    # The budget log's schema.
    check("cannot-evaluate/budget-log-empty",
          cannot("b-log-empty", budget="format-version = 1\naccepted = []\n") == (2, 2, True))
    kinds = [cannot("b-kind-" + str(index), budget=text) for index, text in enumerate((
        _budget_text().replace('kind = "seed"', 'kind = "shrink"'),
        _budget_text(entries=[{"kind": "seed", "pending": ()}]),
        _budget_text(entries=[{"kind": "lower", "pending": ()}])))]
    check("cannot-evaluate/budget-kind-out-of-place", kinds == [(2, 2, True)] * 3, kinds)
    check("cannot-evaluate/budget-note-empty",
          cannot("b-note", budget=_budget_text(entries=[{"kind": "shrink", "pending": (), "note": " "}]))
          == (2, 2, True))
    root = _tree(tmp / "t-notutf8")
    _run(root, False)
    (root / CORE_REL).write_bytes(b"\xff\n")
    check("cannot-evaluate/render-not-utf8", _run(root, True)[0] == 2, _run(root, True))
    root = _tree(tmp / "t-renders-file")
    (root / ".aiqt" / "renders").write_text("not a directory\n", encoding="utf-8")
    check("cannot-evaluate/renders-not-a-directory", _both(root)[:2] == (2, 2))
    failures = _argv_failures()
    check("cannot-evaluate/unknown-argument", not failures, failures)

    # --- the config lanes: a bare launch of this file is its write mode, so tools/selftest_git_fixture_env.py
    # must exclude it and keep the direct --self-test launch; the flip drops both registrations ---
    bare, direct = ("tools/gen_rules_core.py",), ("tools/gen_rules_core.py", "--self-test")
    try:
        exclusions, directs = _lane_tables()
    except (OSError, SyntaxError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        exclusions, directs = {}, {}
        print("lane tables unreadable: {}".format(exc))
    members = _lane_members(exclusions, directs)
    check("lanes/bare-launch-excluded-self-test-kept", bare not in members and direct in members, members)
    flipped = _lane_members({key: value for key, value in exclusions.items() if key != bare},
                            {key: value for key, value in directs.items() if key != SUITE_ID})
    check("flip/lanes-bare-launch", bare in flipped and direct not in flipped, flipped)


def _lane_tables():
    """(CONFIG_EXCLUSIONS["all"], CONFIG_DIRECT_SELFTESTS) of tools/selftest_git_fixture_env.py, read as
    literals from its source (read only, never imported, so that file stays out of this renderer's import
    closure). Raises on a file that is unreadable or whose two tables are missing or not literals."""
    import ast
    path = Path(__file__).resolve().parent / "selftest_git_fixture_env.py"
    tables = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in ("CONFIG_EXCLUSIONS", "CONFIG_DIRECT_SELFTESTS"):
            tables[node.targets[0].id] = ast.literal_eval(node.value)
    return tables["CONFIG_EXCLUSIONS"]["all"], tables["CONFIG_DIRECT_SELFTESTS"]


def _lane_members(exclusions, directs):
    """The config-lane members tools/selftest_git_fixture_env.py _registered_selftests builds from this
    suite's registered step, under the given CONFIG_EXCLUSIONS["all"] and CONFIG_DIRECT_SELFTESTS: the bare
    runner and any direct self-test launch, less every excluded identity (a model of that rule, kept to the
    one suite step)."""
    step = ("tools/check_selftest_execution.py", "--suite", SUITE_ID)
    members = {step}
    if step not in exclusions:
        members.add(("tools/gen_rules_core.py",))
        if SUITE_ID in directs:
            members.add(directs[SUITE_ID])
    return {member for member in members if member not in exclusions}


def shutil_rmtree(path):
    import shutil
    shutil.rmtree(path)


if __name__ == "__main__":
    _code = main()
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
