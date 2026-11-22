#!/usr/bin/env python3
"""Render the CORE preview of the rule corpus: one file, .aiqt/renders/aiqt-core.md, holding one entry per rule.

The CORE render is the second render of the one rule source (gen_rules writes the first, the full render, to
.claude/rules/). This step writes it to a PREVIEW path that no file imports: it is not under .claude/, it is
not named like CLAUDE.md, and it is outside the paths the instruction-budget gate models as loaded. That no
client loads it is inferred from those facts, not observed on a client.

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

HISTORY. The budget is judged against the log as committed at the merge base of HEAD with main, read from
git (read only: rev-parse, merge-base, ls-tree and cat-file, with replacement refs ignored, the graft file
pinned to the empty null device, the commit-graph not consulted, and no caller GIT_ variable, global or
system config). main is refs/remotes/origin/main when it resolves to a commit, else refs/heads/main. Where
that commit holds a budget file:
  - every entry committed there must be held here unchanged, in place, every key compared (the note and the
    kind included), so deleting or rewriting an entry committed at the merge base is a finding;
  - the budget in force here may not hold a pending id that is not on the budget in force there, or a cap
    above it, unless a raise entry appended after the entries committed there names it (lists that id while
    the entry before it does not, or raises that cap above the entry before it).
Where that commit holds no budget file (the change that adds one), nothing is compared there, and the seed
and the pin below bound the log. Where the base cannot be read (git cannot run, the root is not the top of
a git work tree, the repository is shallow, neither main ref resolves, HEAD is not a commit, there is no
merge base or more than one, or the budget there is not a regular file, not UTF-8, not TOML or off the
schema), the result is cannot-evaluate (exit 2), never a pass. An entry appended since the merge base is
not history yet: until it is on main, these two checks do not cover it, and the per-entry rules below do.
What this does not show: that main itself was reviewed, or anything about an entry before it reached main.

THE RATCHET (per entry, within the log here):
  - ACCEPTED here pins one accepted entry: its number in the log, its caps and its pending. The log must
    hold that entry and it must equal the pin. The pin proves no history (HISTORY does). What it adds: a
    raise is in force only once ACCEPTED is moved to it, so a raise is a reviewed edit of this file as well
    as an entry in the log; and where main holds no budget file yet, it is what keeps every later entry
    shrink-only;
  - every entry after the pinned one, of any kind, may only lower a cap or drop a pending id, never raise a
    cap or list an id the entry before it does not, so no entry can lift the budget in force above the pin;
  - the seed entry must equal the budget as this renderer was added (CAP_SEED and PENDING_SEED here);
  - up to the pin, a shrink entry may only lower a cap or drop a pending id, as above, and a raise entry is
    the only kind that may raise a cap or put an id on pending;
  - every entry after the seed must change something, so consecutive entries always differ.
Every passing run prints the pinned entry number, how many raise entries the log holds and the merge base
it compared with. An id on the budget in force that no longer needs to be there (now split, now within
entry-cap, or the apex) is a finding until a shrink entry drops it. Every logged pending id must fully
match the corpus-id pattern (an id with a trailing newline is refused) in every entry, here and at the
merge base, and the corpus must hold each id of the budget in force.

EXIT. 0 clean. 1 on drift (--check) or on a finding, and a finding in write mode writes nothing:
  - a non-apex rule over entry-cap with no split and not on pending; a split rule whose core layer is over
    entry-cap; a detail-trigger over trigger-cap; a seed entry that differs from CAP_SEED and PENDING_SEED;
    a log that does not hold the entry ACCEPTED pins, or holds it with other values; a shrink entry, or
    any entry after the pinned one, that lists a pending id the entry before it does not or raises a cap;
    an entry after the seed that changes nothing; an entry committed at the merge base with main that is
    deleted or rewritten here; a pending id or a cap in force above the budget in force there that no raise
    entry appended since names; a pending id that no longer needs to be there; the render over file-cap
    while pending is empty;
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
UTF-8, or is there but is not a regular file (a symbolic link, wherever it points, a directory or another
kind), which neither mode reads or writes through; a .aiqt/renders/ that cannot be read as a directory
inside the repository; and a budget at the merge base with main that cannot be read (HISTORY), in both
modes. A committed render that is absent is drift (exit 1), as for every generated file.
Write mode opens the render without following a symbolic link in its last component.

  gen_rules_core.py               regenerate .aiqt/renders/aiqt-core.md
  gen_rules_core.py --check       exit 1 on drift or a finding, 2 when it cannot evaluate; write nothing
  gen_rules_core.py --self-test   synthetic trees (real git repositories for HISTORY): every finding and
                                  every cannot-evaluate input; a flip for every guard, for the byte
                                  comparison, the id pattern, the render target check, the linked-directory
                                  probe, the replacement-ref and graft settings and the fail-closed base
                                  read, showing its case changes outcome with it switched off (the exit, or
                                  for a diagnostic guard only its named reason); a backstop row for each
                                  load check (no-title, empty-layer, group-outside), whose case another step
                                  also refuses; and the config-lane rows, which read each lane runner's real
                                  roster (--list-shard 1/1, in a child) for this file's launches
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
import subprocess
import tomllib
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import load_toml, repo_root, target_dir_present, unknown_args_refused  # noqa: E402
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
                     "preview no file imports, outside .claude/ and the paths the instruction-budget gate models "
                     "as loaded (not observed on a client). -->")
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
# The last accepted entry of core-budget.toml (THE RATCHET): its number in the log ("entries") and its caps
# and pending. The log must hold that entry with exactly these values, and no entry after it may raise a cap
# or list a pending id the entry before it does not. A raise entry takes moving this pin to it, here, with
# the values that entry holds. The pinned entry is the seed today. The pin proves no history (the merge-base
# read below does that); what it adds is that a raise is a reviewed edit of this file as well as of the log,
# and that where main holds no budget file yet, the entries after it may still only shrink.
ACCEPTED = dict(CAP_SEED, entries=1, pending=PENDING_SEED)
# The refs read as main, in order: the first that resolves to a commit is used (HISTORY in the docstring).
MAIN_REFS = ("refs/remotes/origin/main", "refs/heads/main")
GIT_TIMEOUT = 60


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
    log = _validated_log(load_toml(path), BUDGET_REL)
    known = {rule.cid for rule in rules}
    unknown = [cid for cid in log[-1]["pending"] if cid not in known]
    if unknown:
        raise ValueError("{}: pending in force names no rule of the corpus: {}".format(
            BUDGET_REL, ", ".join(unknown)))
    budget = {key: log[-1][key] for key in CAP_KEYS + ("pending",)}
    budget["log"] = log
    return budget


def _validated_log(data, label):
    """The accepted log of a parsed budget, checked against the schema in the module docstring; label names
    the file in each message. Raises ValueError (cannot evaluate) on anything off the schema."""
    _exact_keys(data, BUDGET_KEYS, label)
    version = data["format-version"]
    if type(version) is not int or version != 1:
        raise ValueError("{}: format-version must be 1, got {!r}".format(label, version))
    log = data["accepted"]
    if not isinstance(log, list) or not log or not all(isinstance(entry, dict) for entry in log):
        raise ValueError("{}: accepted must be a non-empty array of tables ([[accepted]])".format(label))
    for number, entry in enumerate(log, 1):
        where = "{} accepted entry {}".format(label, number)
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
        if not isinstance(pending, list) or not all(isinstance(cid, str) and _is_cid(cid) for cid in pending):
            raise ValueError("{}: pending must be a list of corpus-ids, got {!r}".format(where, pending))
        if len(set(pending)) != len(pending):
            raise ValueError("{}: pending repeats an id".format(where))
        if pending != sorted(pending):
            raise ValueError("{}: pending is not sorted".format(where))
    return log


# --- the budget as committed at the merge base with main (HISTORY in the module docstring) ---------------

def _base_git_env():
    """The caller's environment with every GIT_ variable dropped (no repository, index or config
    redirection), the system and global config skipped, HOME and XDG_CONFIG_HOME at the null device, and an
    offline, non-interactive, lock-free posture with replacement refs ignored and the graft file pinned to
    the empty null device, so neither a replacement ref nor a grafts line can move the merge base or swap
    the budget read there. A name-keyed function, so the self-test can show both settings carry weight."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, HOME=os.devnull,
               XDG_CONFIG_HOME=os.devnull, GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0",
               GIT_OPTIONAL_LOCKS="0", GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull)
    return env


def _base_git(root, *args):
    """A read-only git command in root under _base_git_env. Raises ValueError (cannot evaluate) when git
    cannot be launched or does not finish in time."""
    try:
        # The auto-maintenance pins ride the argv in option position, so no read here can start a detached
        # gc or maintenance child; the commit-graph is not consulted, so a crafted one cannot rewrite parents.
        return subprocess.run(["git", "-C", str(root), "-c", "gc.auto=0", "-c", "gc.autoDetach=false",
                               "-c", "maintenance.auto=false", "-c", "core.commitGraph=false", *args],
                              capture_output=True, timeout=GIT_TIMEOUT, env=_base_git_env(),
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("git {} could not run ({}), so the budget at the merge base with main cannot be "
                         "read".format(args[0], exc)) from None


def _git_stderr(proc):
    return proc.stderr.decode("utf-8", "backslashreplace").strip() or "no message"


def _git_lines(root, what, *args):
    proc = _base_git(root, *args)
    if proc.returncode != 0:
        raise ValueError("{}: git exited {} ({})".format(what, proc.returncode, _git_stderr(proc)))
    return [line for line in proc.stdout.decode("utf-8", "backslashreplace").splitlines() if line]


def _read_base(root):
    """The budget log as committed at the merge base of HEAD with main: a dict of the main ref used, the
    merge-base commit and "log", the validated log there or None when that commit holds no budget file.
    Raises ValueError (cannot evaluate, never a pass) when it cannot be read: no git, root not the top of a
    git work tree, a shallow repository, no main ref, no HEAD commit, no merge base or more than one, or a
    budget there that is not a regular file, not UTF-8, not TOML or off the schema. A name-keyed function."""
    top = _git_lines(root, "root is not a git work tree", "rev-parse", "--show-toplevel")
    if len(top) != 1 or os.path.realpath(top[0]) != os.path.realpath(root):
        raise ValueError("{} is not the top of a git work tree (git names {!r})".format(root, top))
    if _git_lines(root, "the shallow-repository probe failed", "rev-parse", "--is-shallow-repository") \
            != ["false"]:
        raise ValueError("the repository is shallow, so its merge base with main can be wrong; fetch the "
                         "full history")
    main = None
    for ref in MAIN_REFS:
        proc = _base_git(root, "rev-parse", "--verify", "--quiet", "--end-of-options", ref + "^{commit}")
        if proc.returncode == 0:
            main = (ref, proc.stdout.decode("ascii", "replace").strip())
            break
        if proc.returncode != 1:
            raise ValueError("{} cannot be read: git exited {} ({})".format(ref, proc.returncode,
                                                                            _git_stderr(proc)))
    if main is None:
        raise ValueError("neither {} resolves to a commit, so there is no main to compare the budget "
                         "with".format(" nor ".join(MAIN_REFS)))
    head = _git_lines(root, "HEAD does not resolve to a commit", "rev-parse", "--verify", "--quiet",
                      "--end-of-options", "HEAD^{commit}")
    proc = _base_git(root, "merge-base", "--all", main[1], head[0] if head else "HEAD")
    if proc.returncode not in (0, 1):
        raise ValueError("git merge-base exited {} ({})".format(proc.returncode, _git_stderr(proc)))
    bases = [line for line in proc.stdout.decode("ascii", "replace").splitlines() if line]
    if len(bases) != 1:
        raise ValueError("HEAD and {} have {} merge bases ({}); exactly one is needed".format(
            main[0], len(bases), ", ".join(bases) or "no common ancestor"))
    base = {"ref": main[0], "commit": bases[0], "log": None}
    where = "{} at the merge base {} with {}".format(BUDGET_REL, bases[0][:12], main[0])
    proc = _base_git(root, "ls-tree", "-z", "--full-tree", bases[0], "--", BUDGET_REL)
    if proc.returncode != 0:
        raise ValueError("{}: git ls-tree exited {} ({})".format(where, proc.returncode, _git_stderr(proc)))
    records = [record for record in proc.stdout.split(b"\x00") if record]
    if not records:
        return base
    meta, _tab, name = records[0].partition(b"\t")
    parts = meta.split(b" ")
    if len(records) != 1 or name != BUDGET_REL.encode("ascii") or len(parts) != 3:
        raise ValueError("{}: unexpected git ls-tree output {!r}".format(where, proc.stdout[:120]))
    mode, kind, oid = parts
    if kind != b"blob" or mode not in (b"100644", b"100755"):
        raise ValueError("{} is not a regular file (mode {}, {})".format(
            where, mode.decode("ascii", "replace"), kind.decode("ascii", "replace")))
    proc = _base_git(root, "cat-file", "blob", oid.decode("ascii", "replace"))
    if proc.returncode != 0:
        raise ValueError("{}: git cat-file exited {} ({})".format(where, proc.returncode, _git_stderr(proc)))
    try:
        data = tomllib.loads(proc.stdout.decode("utf-8"))
    except UnicodeError as exc:
        raise ValueError("{} is not UTF-8 ({})".format(where, exc)) from None
    except (ValueError, RecursionError, MemoryError) as exc:
        raise ValueError("{} cannot be parsed as TOML ({}: {})".format(where, type(exc).__name__, exc)) from None
    base["log"] = _validated_log(data, where)
    return base


def _is_cid(cid):
    """True when the whole of cid is a corpus-id: a full match, so an id with a trailing newline (which `$`
    alone accepts) is refused. A name-keyed function, so the self-test can swap in the prefix match."""
    return CID_RE.fullmatch(cid) is not None


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


def _seed_altered(_rules, budget, _text, pins):
    seed = pins["seed"]
    first = budget["log"][0]
    changed = [key for key in CAP_KEYS if first[key] != seed[key]]
    if set(first["pending"]) != set(seed["pending"]):
        changed.append("pending")
    return ["accepted entry 1 (the seed): {} differ(s) from CAP_SEED and PENDING_SEED in "
            "tools/gen_rules_core.py; append a shrink or raise entry instead".format(", ".join(changed))
            ] if changed else []


def _shrink_only_entries(budget, pins):
    """(entry number, entry, the entry before it, why it may not grow) for every entry of the log that may
    only shrink: a shrink entry, and every entry of any kind after the one ACCEPTED pins."""
    log, pinned = budget["log"], pins["accepted"]["entries"]
    found = []
    for number in range(2, len(log) + 1):
        if number > pinned:
            why = ("it follows the accepted pin (entry {}); a raise takes a raise entry and moving ACCEPTED in "
                   "tools/gen_rules_core.py to it".format(pinned))
        elif log[number - 1]["kind"] == "shrink":
            why = "it is a shrink entry; a raise takes a raise entry"
        else:
            continue
        found.append((number, log[number - 1], log[number - 2], why))
    return found


def _pending_grew(_rules, budget, _text, pins):
    return ["accepted entry {}: {} on pending but not on the entry before it, and the entry may not grow "
            "pending: {}".format(number, cid, why)
            for number, entry, before, why in _shrink_only_entries(budget, pins)
            for cid in entry["pending"] if cid not in before["pending"]]


def _cap_raised(_rules, budget, _text, pins):
    return ["accepted entry {}: {} {} is above {} on the entry before it, and the entry may not raise a cap: "
            "{}".format(number, key, entry[key], before[key], why)
            for number, entry, before, why in _shrink_only_entries(budget, pins)
            for key in CAP_KEYS if entry[key] > before[key]]


def _shrink_no_change(_rules, budget, _text, _pins):
    log = budget["log"]
    return ["accepted entry {}: a {} entry that changes nothing from the entry before it".format(
        number, log[number - 1]["kind"]) for number in range(2, len(log) + 1)
        if all(log[number - 1][key] == log[number - 2][key] for key in CAP_KEYS)
        and set(log[number - 1]["pending"]) == set(log[number - 2]["pending"])]


def _accepted_pin(_rules, budget, _text, pins):
    pin, log = pins["accepted"], budget["log"]
    number = pin["entries"]
    if len(log) < number:
        return ["the log holds {} accepted entries, fewer than the {} the accepted pin (ACCEPTED in "
                "tools/gen_rules_core.py) records; an accepted entry was deleted".format(len(log), number)]
    entry = log[number - 1]
    changed = [key for key in CAP_KEYS if entry[key] != pin[key]]
    if set(entry["pending"]) != set(pin["pending"]):
        changed.append("pending")
    return ["accepted entry {}: {} differ(s) from the accepted pin (ACCEPTED in tools/gen_rules_core.py); an "
            "accepted entry was deleted or rewritten, or a raise was made without moving the pin".format(
                number, ", ".join(changed))] if changed else []


def _base_where(base):
    return "the merge base {} with {}".format(base["commit"][:12], base["ref"])


def _base_rewritten(_rules, budget, _text, pins):
    """Every entry committed at the merge base with main must be held here unchanged, in place: none
    deleted, none rewritten (every key compared, the note and the kind included)."""
    base = pins["base"]
    if base["log"] is None:
        return []
    log, held = budget["log"], base["log"]
    findings = []
    if len(log) < len(held):
        findings.append("the log holds {} accepted entries, fewer than the {} committed at {}; an entry "
                        "committed there was deleted".format(len(log), len(held), _base_where(base)))
    for number in range(1, min(len(log), len(held)) + 1):
        changed = [key for key in ENTRY_KEYS if log[number - 1][key] != held[number - 1][key]]
        if changed:
            findings.append("accepted entry {}: {} differ(s) from the entry committed at {}; an entry "
                            "committed there may not be rewritten, append an entry instead".format(
                                number, ", ".join(changed), _base_where(base)))
    return findings


def _base_grew(_rules, budget, _text, pins):
    """Against the budget in force at the merge base with main, the budget in force here may not hold an
    id that is not there or a cap above it, unless a raise entry appended after the entries committed
    there names it: lists the id while the entry before it does not, or raises that cap above it."""
    base = pins["base"]
    if base["log"] is None:
        return []
    log, held = budget["log"], base["log"]
    before = held[-1]
    raises = [(log[index - 1], log[index]) for index in range(max(len(held), 1), len(log))
              if log[index]["kind"] == "raise"]
    findings = ["{}: on pending in force but not on pending at {}, and no raise entry appended since names "
                "it".format(cid, _base_where(base)) for cid in budget["pending"]
                if cid not in before["pending"]
                and not any(cid in entry["pending"] and cid not in prior["pending"] for prior, entry in raises)]
    findings += ["{} {} is above {} at {}, and no raise entry appended since raises it".format(
        key, budget[key], before[key], _base_where(base)) for key in CAP_KEYS
        if budget[key] > before[key] and not any(entry[key] > prior[key] for prior, entry in raises)]
    return findings


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
                 "accepted-pin": _accepted_pin, "pending-grew": _pending_grew, "cap-raised": _cap_raised,
                 "shrink-no-change": _shrink_no_change, "pending-stale": _pending_stale,
                 "file-over-cap": _file_over_cap, "base-rewritten": _base_rewritten, "base-grew": _base_grew}


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


def source_findings(rules, budget, text, pins):
    return [line for name in SOURCE_GUARDS for line in SOURCE_GUARDS[name](rules, budget, text, pins)]


def render_findings(text, rules, root):
    return [line for name in RENDER_GUARDS for line in RENDER_GUARDS[name](text, rules, root)]


def _same_bytes(raw, text):
    """True when the committed bytes raw are exactly the fresh render text encoded as UTF-8 (no line-ending
    translation). A name-keyed function, so the self-test can swap in a line-ending-blind comparison."""
    return raw == text.encode("utf-8")


def _render_target_problem(target):
    """None when the committed render at target is absent or a regular file; otherwise why it cannot be
    evaluated (a symbolic link, wherever it points, or another kind), judged by lstat so nothing is read or
    written through a link. A name-keyed function, so the self-test can switch it off (the flip)."""
    try:
        mode = os.lstat(target).st_mode
    except FileNotFoundError:
        return None
    except OSError as exc:
        return "{} cannot be examined ({})".format(CORE_REL, exc)
    if stat.S_ISREG(mode):
        return None
    return "{} is not a regular file (a symbolic link, a directory or another kind)".format(CORE_REL)


def _write_render(target, text):
    """Write text to target, creating its absent directories, without following a symbolic link in the last
    component (O_NOFOLLOW). Exit 2 with a message on an OSError."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o666)
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
    except OSError as exc:
        print("error: cannot write {} ({}); fail-closed".format(target, exc), file=sys.stderr)
        raise SystemExit(2)


def run(root, check_mode, seed=None, accepted=None, base=None):
    """Reconcile root's CORE render. Exit 0 clean, 1 on drift (check mode) or a finding, 2 when it cannot
    evaluate (see the module docstring). seed stands in for CAP_SEED and PENDING_SEED, a dict of the three
    caps and pending, and accepted for ACCEPTED, a dict of the pinned entry number ("entries"), the three
    caps and pending (the self-test's synthetic trees). base, when given, stands in for what _read_base
    reads from git (a self-test tree that is not a git repository); when None, it is read."""
    seed = dict(CAP_SEED, pending=PENDING_SEED) if seed is None else seed
    try:
        rules = load_rules(root)
        budget = load_budget(root, rules)
        base = _read_base(root) if base is None else base
    except (ValueError, OSError) as exc:
        print("error: {} (cannot evaluate)".format(exc), file=sys.stderr)
        return 2
    pins = {"seed": seed, "accepted": ACCEPTED if accepted is None else accepted, "base": base}
    target = root / CORE_REL
    # The directory chain first (exit 2 on a link out of the tree), then the render itself, in both modes.
    target_dir_present(target, root)
    problem = _render_target_problem(target)
    if problem is not None:
        print("error: {} (cannot evaluate)".format(problem), file=sys.stderr)
        return 2
    text = render(rules)
    findings = source_findings(rules, budget, text, pins) + render_findings(text, rules, root)
    drift = []
    if check_mode:
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
        # As the shared reconcile does, a committed render that cannot be read as UTF-8 is cannot-evaluate
        # in write mode too, never overwritten unseen.
        try:
            if target.exists():
                target.read_bytes().decode("utf-8")
        except (OSError, UnicodeError) as exc:
            print("error: cannot read {} ({}) (cannot evaluate)".format(target, exc), file=sys.stderr)
            return 2
        _write_render(target, text)
    split = sum(rule.split for rule in rules)
    log = budget["log"]
    print("rules-core: {} entries ({} whole, {} split, {} pending); {} units against file-cap {} ({}); budget "
          "log {} accepted entries, {} of them raise entries, accepted pin at entry {}; {}: {}".format(
              len(rules), len(rules) - split, split, len(budget["pending"]), units(text), budget["file-cap"],
              "reported only while pending is not empty" if budget["pending"] else "enforced",
              len(log), sum(entry["kind"] == "raise" for entry in log), pins["accepted"]["entries"],
              _base_where(base), "no budget file committed there" if base["log"] is None else
              "{} accepted entries committed there, held unchanged".format(len(base["log"]))))
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


def _pin(entries, entry_cap=80, trigger_cap=60, file_cap=100000, pending=("longaa1",)):
    """A fixture accepted pin: entry number entries of the log holds these values."""
    return {"entries": entries, "entry-cap": entry_cap, "trigger-cap": trigger_cap, "file-cap": file_cap,
            "pending": frozenset(pending)}


_PIN = _pin(1)
# The stand-in base for a synthetic tree that is not a git repository: main holds no budget file, so only
# the seed and the accepted pin bound the log. READ_BASE makes run() read the base from git instead.
_NO_BASE = {"ref": "refs/heads/main", "commit": "0" * 40, "log": None}
READ_BASE = None


def _run(root, check_mode, seed=_SEED, accepted=_PIN, base=_NO_BASE):
    """(exit, captured stdout and stderr) of run(root, check_mode), SystemExit read as its code."""
    import io
    from contextlib import redirect_stderr, redirect_stdout
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(out):
        try:
            code = run(root, check_mode, seed, accepted, base)
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


def _both(root, seed=_SEED, accepted=_PIN, base=_NO_BASE):
    """(check exit, write exit, render unchanged) for a tree that may already hold a render."""
    target = root / CORE_REL
    before = target.read_bytes() if target.exists() else None
    codes = (_run(root, True, seed, accepted, base)[0], _run(root, False, seed, accepted, base)[0])
    after = target.read_bytes() if target.exists() else None
    return codes[0], codes[1], before == after


def _write_then_check(root, seed=_SEED, accepted=_PIN, base=_NO_BASE):
    """(write exit, check exit) for a tree: the check sees the render the write made, if it made one."""
    return _run(root, False, seed, accepted, base)[0], _run(root, True, seed, accepted, base)[0]


def _fx_git(repo, *args):
    """A fixture git command in repo under the shared fixture scrub (tools/_git_fixture_env.py), with the
    auto-maintenance pins in option position. Raises RuntimeError when it fails (a broken fixture)."""
    from _git_fixture_env import git_fixture_env
    proc = subprocess.run(["git", "-C", str(repo), "-c", "gc.auto=0", "-c", "gc.autoDetach=false",
                           "-c", "maintenance.auto=false", "-c", "user.name=rules-core fixture",
                           "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                           "-c", "core.hooksPath=/dev/null", *args],
                          capture_output=True, timeout=GIT_TIMEOUT, env=git_fixture_env(),
                          stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise RuntimeError("fixture git {} exited {}: {}".format(args[0], proc.returncode, _git_stderr(proc)))
    return proc.stdout.decode("utf-8", "replace").strip()


def _put(root, sources, budget):
    """Write sources (replacing every rule source) and budget into root's working tree; a None leaves that
    part as it is, and a budget of False deletes the budget file."""
    if sources is not None:
        rules_dir = root / ".aiqt" / "core" / "rules"
        for path in rules_dir.iterdir():
            path.unlink()
        for name, text in sources.items():
            (rules_dir / name).write_text(text, encoding="utf-8")
    if budget is False:
        (root / BUDGET_REL).unlink()
    elif budget is not None:
        (root / BUDGET_REL).write_text(budget, encoding="utf-8")


def _git_tree(base, main_sources=None, main_budget=None, head_sources=None, head_budget=None,
              branch="main"):
    """A git repository at base: one commit on branch holding main_sources and main_budget (main_budget
    False: no budget file there), then branch topic checked out from it with head_sources and head_budget
    written to the working tree, uncommitted. Returns base."""
    root = _tree(base, sources=main_sources, budget=None if main_budget is False else main_budget)
    (root / ".git").rmdir()
    if main_budget is False:
        (root / BUDGET_REL).unlink()
    _fx_git(root, "init", "-q", "-b", branch)
    _fx_git(root, "add", "-A")
    _fx_git(root, "commit", "-q", "--no-verify", "-m", "main")
    _fx_git(root, "checkout", "-q", "-b", "topic")
    _put(root, head_sources, head_budget)
    return root


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
          "returning after a shrink, a cap raised by a shrink entry, an altered seed, a shrink or raise entry "
          "that changes nothing, an accepted shrink entry deleted or rewritten, and a raise entry after the "
          "accepted pin (pending or cap) each exit 1 and exit 0 with their guard switched off, as do a stale "
          "pending id, a long trigger, a core over the cap and the file over its cap, and a raise entry the pin "
          "records is accepted; every malformed or missing input is cannot-evaluate, exit 2, with nothing "
          "written, among them a logged id with a trailing newline and a committed render that is a symbolic "
          "link (in --check, and in write mode with nothing written through it), each exit 0 with its check "
          "switched off; .aiqt/renders/ linked outside the tree is exit 2 and inside it is rewritten, the "
          "shared cases going red with the probe switched off; in real git repositories, a shrink merged to "
          "main and then undone (its entry deleted, its rule regrown) is refused by each history guard alone "
          "and passes with both switched off, a rewritten cap, kind or note of an entry committed at the merge "
          "base exits 1 and exit 0 with its guard switched off, a replacement ref or a grafts line cannot move "
          "the merge base (exit 0 once honoured), a shrink or a named raise appended since main and a main with "
          "no budget pass, and no git, no repository, a shallow clone, no main, no merge base and a budget at "
          "the merge base off the schema or not a regular file are each exit 2 with nothing written, an "
          "unreadable base read as absent letting the undo pass; each config lane's real roster keeps the "
          "--self-test launch and the suite step and leaves the bare launch out)".format(len(EXECUTED)))
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
    # A raise entry is accepted once the accepted pin records it (the pin moves to it in this file).
    root = _tree(tmp / "raise", budget=_budget_text(entries=[shrunk, {"kind": "raise", "entry_cap": 90}]))
    raised = _pin(3, entry_cap=90)
    check("ratchet/raise-entry-accepted", _write_then_check(root, accepted=raised) == (0, 0),
          _run(root, True, accepted=raised))
    # The reviewed defects: against the accepted pin, deleting or rewriting an accepted
    # shrink entry, or appending a raise entry the pin does not record, gave exit 0 with an id back on
    # pending or a cap above the accepted one.
    after_shrink = _pin(2, pending=())

    def refused(root, accepted, reason):
        """(write exit, check exit, render written, reason named by the write) against accepted."""
        written, out = _run(root, False, accepted=accepted)
        return (written, _run(root, True, accepted=accepted)[0], (root / CORE_REL).exists(), reason in out), out

    for case_id, flip_id, guard, label, sources, entries, reason in (
            ("ratchet/accepted-shrink-deleted", "flip/accepted-shrink-deleted", "accepted-pin", "deleted", None,
             [], "accepted pin"),
            ("ratchet/accepted-shrink-rewritten", "flip/accepted-shrink-rewritten", "accepted-pin", "rewritten",
             None, [{"kind": "shrink", "entry_cap": 70}], "accepted pin"),
            ("ratchet/raise-after-pin-pending", "flip/raise-after-pin-pending", "pending-grew", "raise-unpinned",
             None, [shrunk, {"kind": "raise"}],
             "accepted entry 3: longaa1 on pending but not on the entry before it"),
            ("ratchet/raise-after-pin-cap", "flip/raise-after-pin-cap", "cap-raised", "raise-cap-unpinned",
             shortened, [shrunk, {"kind": "raise", "entry_cap": 90, "pending": ()}],
             "accepted entry 3: entry-cap 90 is above 80")):
        root = _tree(tmp / label, sources=sources, budget=_budget_text(entries=entries))
        got, out = refused(root, after_shrink, reason)
        check(case_id, got == (1, 1, False, True), (got, out))
        with _without(SOURCE_GUARDS, guard):
            flipped = _write_then_check(root, accepted=after_shrink)
        check(flip_id, flipped == (0, 0), flipped)
    root = _tree(tmp / "capraise", budget=_budget_text(entries=[shrunk, {"kind": "shrink", "entry_cap": 1000,
                                                                         "pending": ()}]))
    code, out = _run(root, True)
    check("ratchet/cap-raised", code == 1 and "accepted entry 3: entry-cap 1000 is above 80" in out, out)
    with _without(SOURCE_GUARDS, "cap-raised"):
        flipped = _write_then_check(root)
    check("flip/cap-raised", flipped == (0, 0), flipped)
    root = _tree(tmp / "seedalt", budget=_budget_text().replace("file-cap = 100000", "file-cap = 200000"))
    altered = _pin(1, file_cap=200000)
    code, out = _run(root, True, accepted=altered)
    check("ratchet/seed-altered", (code, _run(root, False, accepted=altered)[0], (root / CORE_REL).exists())
          == (1, 1, False) and "accepted entry 1 (the seed): file-cap differ(s)" in out, out)
    with _without(SOURCE_GUARDS, "seed-altered"):
        flipped = _write_then_check(root, accepted=altered)
    check("flip/seed-altered", flipped == (0, 0), flipped)
    root = _tree(tmp / "nochange", budget=_budget_text(entries=[{"kind": "shrink"}]))
    code, out = _run(root, True)
    check("ratchet/shrink-no-change", code == 1 and "accepted entry 2: a shrink entry that changes nothing" in out,
          out)
    with _without(SOURCE_GUARDS, "shrink-no-change"):
        flipped = _write_then_check(root)
    check("flip/shrink-no-change", flipped == (0, 0), flipped)
    # A raise entry that changes nothing is refused too, so consecutive entries always differ and deleting
    # an entry before the pin always moves or changes the pinned one.
    root = _tree(tmp / "raise-nochange", budget=_budget_text(entries=[{"kind": "raise"}]))
    code, out = _run(root, True, accepted=_pin(2))
    check("ratchet/raise-no-change", code == 1 and "accepted entry 2: a raise entry that changes nothing" in out,
          out)
    with _without(SOURCE_GUARDS, "shrink-no-change"):
        flipped = _write_then_check(root, accepted=_pin(2))
    check("flip/raise-no-change", flipped == (0, 0), flipped)

    _history_cases(tmp, shortened, shrunk)

    # --- the other findings ---
    root = _tree(tmp / "stale", budget=_budget_text(pending=("longaa1", "shrtaa1"), kind="raise"))
    stale_pin = _pin(2, pending=("longaa1", "shrtaa1"))
    code, out = _run(root, True, accepted=stale_pin)
    check("finding/pending-stale", code == 1 and "shrtaa1: on pending but within entry-cap now" in out, out)
    with _without(SOURCE_GUARDS, "pending-stale"):
        flipped = _write_then_check(root, accepted=stale_pin)
    check("flip/pending-stale", flipped == (0, 0), flipped)
    root = _tree(tmp / "trigger", budget=_budget_text(trigger_cap=5))
    code, out = _run(root, True)
    check("finding/trigger-over-cap", code == 1 and "splita1: the detail-trigger is 17 characters" in out, out)
    with _without(SOURCE_GUARDS, "trigger-over-cap"):
        flipped = _write_then_check(root)
    check("flip/trigger-over-cap", flipped == (0, 0), flipped)
    root = _tree(tmp / "corecap", budget=_budget_text(entry_cap=5, pending=("longaa1", "secaa11", "shrtaa1"),
                                                      kind="raise"))
    corecap_pin = _pin(2, entry_cap=5, pending=("longaa1", "secaa11", "shrtaa1"))
    code, out = _run(root, True, accepted=corecap_pin)
    check("finding/core-over-cap", code == 1 and "splita1: the core layer is 18 units" in out, out)
    with _without(SOURCE_GUARDS, "core-over-cap"):
        flipped = _write_then_check(root, accepted=corecap_pin)
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
    check("cannot-evaluate/render-not-utf8", _both(root) == (2, 2, True), _run(root, True))
    root = _tree(tmp / "t-renders-file")
    (root / ".aiqt" / "renders").write_text("not a directory\n", encoding="utf-8")
    check("cannot-evaluate/renders-not-a-directory", _both(root)[:2] == (2, 2))
    # The reviewed defect: a logged id with a trailing newline passed the id pattern ($ matches
    # before a final LF), here in an earlier raise entry that a later shrink entry drops.
    newline_budget = _budget_text(entries=[{"kind": "raise", "pending": ("longaa1", "zzzzzz9\\n")},
                                           {"kind": "shrink"}])
    newline_pin = _pin(3)
    root = _tree(tmp / "b-newline-id", budget=newline_budget)
    check("cannot-evaluate/budget-pending-id-trailing-newline", _both(root, accepted=newline_pin) == (2, 2, True),
          _run(root, True, accepted=newline_pin))
    saved = globals()["_is_cid"]
    globals()["_is_cid"] = lambda cid: CID_RE.match(cid) is not None
    try:
        flipped = _write_then_check(root, accepted=newline_pin)
    finally:
        globals()["_is_cid"] = saved
    check("flip/budget-pending-id-trailing-newline", flipped == (0, 0), flipped)
    # The reviewed defect: a committed render that is a symbolic link passed the check (here a link
    # to identical bytes inside the tree), and write mode wrote through a link to a file outside the tree.
    root = _tree(tmp / "t-link-check")
    _run(root, False)
    held = root / "held-render.md"
    (root / CORE_REL).rename(held)
    (root / CORE_REL).symlink_to(held)
    check("cannot-evaluate/render-symlink-check", _run(root, True)[0] == 2, _run(root, True))
    @contextmanager
    def swapped(check_off=False, following_write=False):
        """The lstat check switched off and/or a link-following write swapped in, for the block."""
        saved = globals()["_render_target_problem"], globals()["_write_render"]
        if check_off:
            globals()["_render_target_problem"] = lambda _target: None
        if following_write:
            globals()["_write_render"] = lambda target, text: target.write_text(text, encoding="utf-8")
        try:
            yield
        finally:
            globals()["_render_target_problem"], globals()["_write_render"] = saved

    with swapped(check_off=True):
        flipped = _run(root, True)
    check("flip/render-symlink-check", flipped[0] == 0, flipped)
    root = _tree(tmp / "t-link-write")
    outside = tmp / "outside-render.md"
    (root / ".aiqt" / "renders").mkdir()
    (root / CORE_REL).symlink_to(outside)

    def write_through(check_off=False, following_write=False):
        """(write exit, the outside file left as it was) with swapped(...) in force for the run."""
        outside.write_text("stale\n", encoding="utf-8")
        with swapped(check_off, following_write):
            code = _run(root, False)[0]
        return code, outside.read_text(encoding="utf-8") == "stale\n"

    got = write_through()
    check("cannot-evaluate/render-symlink-write", got == (2, True), got)
    # Either barrier holds alone: with the lstat check off, the write's O_NOFOLLOW still refuses the link;
    # with both off (a link-following write swapped in), the write goes through to the outside file.
    got = write_through(check_off=True)
    check("cannot-evaluate/render-symlink-write-nofollow", got == (2, True), got)
    got = write_through(check_off=True, following_write=True)
    check("flip/render-symlink-write", got == (0, False), got)
    # The shared linked-directory cases: .aiqt/renders/ linked outside the tree is exit 2 in both
    # modes with nothing written there; linked inside the tree it is written and re-checks clean.
    from _gen_common import linked_dir_failures
    linked = linked_dir_failures(tmp / "linked", "renders", _tree,
                                 lambda tree, check_mode: run(tree, check_mode, _SEED, _PIN, _NO_BASE),
                                 ".aiqt/renders", CORE_REL)
    check("linked-dir/renders", not linked, linked)
    # The flip: with the directory-chain probe switched off (here and in the shared module), the outside
    # case reads through the link and the shared cases go red.
    import _gen_common
    saved = globals()["target_dir_present"], _gen_common.target_dir_present
    globals()["target_dir_present"] = _gen_common.target_dir_present = lambda *_args, **_kwargs: True
    try:
        flipped = linked_dir_failures(tmp / "linked-flip", "renders", _tree,
                                      lambda tree, check_mode: run(tree, check_mode, _SEED, _PIN, _NO_BASE),
                                      ".aiqt/renders", CORE_REL)
    finally:
        globals()["target_dir_present"], _gen_common.target_dir_present = saved
    check("flip/linked-dir-renders", bool(flipped), flipped)
    failures = _argv_failures()
    check("cannot-evaluate/unknown-argument", not failures, failures)

    # --- the config lanes: a bare launch of this file is its write mode, so each config lane must leave it
    # out and keep the direct --self-test launch and the suite step. The rows read the REAL roster: each
    # lane runner's own --list-shard 1/1 listing (built by tools/selftest_git_fixture_env.py's rule from the
    # registries, in a child that runs nothing). The flip feeds the same predicate each real listing with
    # the bare launch put in and the direct launch taken out, so the predicate is shown to tell them apart ---
    listings = {lane: _lane_listing(lane) for lane in LANES}
    verdicts = {lane: _lane_ok(listing) for lane, listing in listings.items()}
    check("lanes/bare-launch-excluded-self-test-kept", all(verdicts.values()) and len(verdicts) == 3,
          {lane: "ok" if verdicts[lane] else listing for lane, listing in listings.items()})
    flipped = {lane: _lane_ok(None if listing is None else
                              (listing - {" ".join(LANE_DIRECT)}) | {" ".join(LANE_BARE)})
               for lane, listing in listings.items()}
    check("flip/lanes-bare-launch", not any(flipped.values()) and all(listings.values()), flipped)


def _history_cases(tmp, shortened, shrunk):
    """The rows for HISTORY (module docstring): each builds a real git repository, main holding one budget
    and the topic branch's working tree another, and runs with the base read from git."""
    seed_budget = _budget_text()
    shrunk_budget = _budget_text(entries=[shrunk])

    def git_case(label, *args, **kwargs):
        """(the fixture repository or None, why it could not be built)."""
        try:
            return _git_tree(tmp / label, *args, **kwargs), ""
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            return None, "fixture: {}".format(exc)

    def judged(root, accepted=_PIN):
        """((write exit, check exit, render written), output) with the base read from git."""
        if root is None:
            return None, "no fixture"
        written, out_w = _run(root, False, accepted=accepted, base=READ_BASE)
        checked, out_c = _run(root, True, accepted=accepted, base=READ_BASE)
        return (written, checked, (root / CORE_REL).exists()), out_w + out_c

    # The reviewed defect: a shrink is accepted and merged (main holds the shortened rule and the shrink
    # entry that drops its id), then a later change restores the longer rule and deletes that shrink entry.
    # The pin never moved, so every in-file check agrees; against the merge base with main it is refused,
    # by each of the two history guards on its own.
    root, why = git_case("h-undone", shortened, shrunk_budget, _fixture_sources(), seed_budget)
    got, out = judged(root)
    check("history/shrink-merged-then-undone", got == (1, 1, False)
          and "fewer than the 2 committed at the merge base" in out
          and "longaa1: on pending in force but not on pending at the merge base" in out, (got, out, why))
    with _without(SOURCE_GUARDS, "base-rewritten"):
        alone = judged(root)
    check("history/shrink-undone-refused-by-base-grew-alone", alone[0] == (1, 1, False)
          and "longaa1: on pending in force" in alone[1], alone)
    with _without(SOURCE_GUARDS, "base-grew"):
        alone = judged(root)
    check("history/shrink-undone-refused-by-base-rewritten-alone", alone[0] == (1, 1, False)
          and "an entry committed there was deleted" in alone[1], alone)
    with _without(SOURCE_GUARDS, "base-rewritten"), _without(SOURCE_GUARDS, "base-grew"):
        flipped = judged(root)
    check("flip/shrink-merged-then-undone", flipped[0] == (0, 0, True), flipped)

    # The reviewed defect: the public text said deleting or rewriting an accepted entry is a finding, but
    # with the pin at the seed a rewrite of a later entry's cap or kind, or of the seed's note, passed.
    # Against the merge base every key of every entry committed there is compared.
    three = _budget_text(entries=[{"kind": "shrink", "file_cap": 90000}, {"kind": "shrink", "file_cap": 80000}])
    for label, case_id, flip_id, edit in (
            ("h-cap", "history/entry-cap-rewritten", "flip/entry-cap-rewritten",
             lambda text: text.replace("file-cap = 90000", "file-cap = 85000")),
            ("h-kind", "history/entry-kind-rewritten", "flip/entry-kind-rewritten",
             lambda text: text.replace('kind = "shrink"', 'kind = "raise"', 1)),
            ("h-note", "history/seed-note-rewritten", "flip/seed-note-rewritten",
             lambda text: text.replace('note = "why"', 'note = "reworded"', 1))):
        root, why = git_case(label, None, three, None, edit(three))
        got, out = judged(root)
        check(case_id, got == (1, 1, False) and "from the entry committed at the merge base" in out,
              (got, out, why))
        with _without(SOURCE_GUARDS, "base-rewritten"):
            flipped = judged(root)
        check(flip_id, flipped[0] == (0, 0, True), flipped)

    # What history still accepts: a shrink appended since main, a raise appended since main that names the
    # id it puts back (with the pin moved to it), and a log on a branch whose main holds no budget file yet
    # (the seed and the pin then bound it).
    root, why = git_case("h-shrink", None, seed_budget, shortened, shrunk_budget)
    got = judged(root)
    check("history/shrink-appended-accepted", got[0] == (0, 0, True), (got, why))
    root, why = git_case("h-raise", shortened, shrunk_budget, _fixture_sources(),
                         _budget_text(entries=[shrunk, {"kind": "raise"}]))
    got = judged(root, accepted=_pin(3))
    check("history/raise-appended-naming-the-id-accepted", got[0] == (0, 0, True), (got, why))
    root, why = git_case("h-absent", None, False, None, seed_budget)
    got = judged(root)
    check("history/no-budget-at-merge-base-accepted", got[0] == (0, 0, True)
          and "no budget file committed there" in got[1], (got, why))

    # Replacement refs and grafts are ignored: main is C1 (seed only) then C2 (the shrink), the topic
    # branch starts at C2 and undoes the shrink. A replacement ref C2 -> C1, or a grafts line giving the
    # topic commit C1 as its parent, would make the merge base read as C1 and the undo pass.
    def undo_after_shrink(label, commit_topic):
        root, why = git_case(label, None, seed_budget)
        if root is None:
            return None, None, None, why
        try:
            first = _fx_git(root, "rev-parse", "HEAD")
            _fx_git(root, "checkout", "-q", "main")
            _put(root, shortened, shrunk_budget)
            _fx_git(root, "commit", "-q", "--no-verify", "-a", "-m", "shrink")
            second = _fx_git(root, "rev-parse", "HEAD")
            _fx_git(root, "checkout", "-q", "-B", "topic")
            _put(root, _fixture_sources(), seed_budget)
            if commit_topic:
                _fx_git(root, "commit", "-q", "--no-verify", "-a", "-m", "undo")
            return root, first, second, ""
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            return None, None, None, "fixture: {}".format(exc)

    def honouring_env():
        env = _BASE_GIT_ENV_SAVED()
        del env["GIT_NO_REPLACE_OBJECTS"], env["GIT_GRAFT_FILE"]
        return env

    _BASE_GIT_ENV_SAVED = globals()["_base_git_env"]

    @contextmanager
    def honouring():
        globals()["_base_git_env"] = honouring_env
        try:
            yield
        finally:
            globals()["_base_git_env"] = _BASE_GIT_ENV_SAVED

    root, first, second, why = undo_after_shrink("h-replace", False)
    if root is not None:
        try:
            _fx_git(root, "replace", second, first)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            root, why = None, "fixture: {}".format(exc)
    got = judged(root)
    check("history/replace-ref-ignored", got[0] == (1, 1, False), (got, why))
    with honouring():
        flipped = judged(root)
    check("flip/replace-ref-ignored", flipped[0] == (0, 0, True), flipped)
    root, first, second, why = undo_after_shrink("h-graft", True)
    if root is not None:
        topic = _fx_git(root, "rev-parse", "HEAD")
        (root / ".git" / "info").mkdir(exist_ok=True)
        (root / ".git" / "info" / "grafts").write_text("{} {}\n".format(topic, first), encoding="utf-8")
    got = judged(root)
    check("history/graft-ignored", got[0] == (1, 1, False), (got, why))
    with honouring():
        flipped = judged(root)
    check("flip/graft-ignored", flipped[0] == (0, 0, True), flipped)

    # Where the base cannot be read the result is cannot-evaluate (exit 2 in both modes, nothing written),
    # never a pass: no git, not a git repository, a shallow repository, no main, no merge base, and a
    # budget at the merge base that is off the schema or not a regular file.
    def cannot(root, why):
        if root is None:
            return None, why
        return _both(root, base=READ_BASE), _run(root, True, base=READ_BASE)[1]

    root, why = git_case("h-nogit", shortened, shrunk_budget, _fixture_sources(), seed_budget)
    empty_path = tmp / "h-empty-path"
    empty_path.mkdir()
    saved_path = os.environ.get("PATH")
    os.environ["PATH"] = str(empty_path)
    try:
        got = cannot(root, why)
    finally:
        if saved_path is None:
            del os.environ["PATH"]
        else:
            os.environ["PATH"] = saved_path
    check("cannot-evaluate/base-no-git", got[0] == (2, 2, True) and "could not run" in got[1], got)
    # The flip: an unreadable base read as "no budget at main" lets the undone shrink pass.
    saved = globals()["_read_base"]

    def lenient(tree):
        try:
            return saved(tree)
        except ValueError:
            return dict(_NO_BASE)

    globals()["_read_base"] = lenient
    try:
        os.environ["PATH"] = str(empty_path)
        try:
            flipped = judged(root)
        finally:
            if saved_path is None:
                del os.environ["PATH"]
            else:
                os.environ["PATH"] = saved_path
    finally:
        globals()["_read_base"] = saved
    check("flip/base-unreadable-read-as-absent", flipped[0] == (0, 0, True), flipped)
    got = cannot(_tree(tmp / "h-norepo"), "")
    check("cannot-evaluate/base-not-a-repository", got[0] == (2, 2, True), got)
    root, why = git_case("h-shallow-src", None, seed_budget, shortened, shrunk_budget)
    clone = tmp / "h-shallow"
    try:
        if root is None:
            raise RuntimeError(why)
        _fx_git(root, "commit", "-q", "--no-verify", "-a", "-m", "second")
        _fx_git(tmp, "clone", "-q", "--depth", "1", "--no-single-branch", root.as_uri(), str(clone))
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        clone, why = None, "fixture: {}".format(exc)
    got = cannot(clone, why)
    check("cannot-evaluate/base-shallow", got[0] == (2, 2, True) and "shallow" in got[1], got)
    root, why = git_case("h-nomain", None, seed_budget, None, seed_budget, branch="trunk")
    got = cannot(root, why)
    check("cannot-evaluate/base-no-main", got[0] == (2, 2, True) and "resolves to a commit" in got[1], got)
    root, why = git_case("h-orphan", None, seed_budget)
    try:
        if root is not None:
            _fx_git(root, "checkout", "-q", "--orphan", "lonely")
            _fx_git(root, "commit", "-q", "--no-verify", "-m", "unrelated")
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        root, why = None, "fixture: {}".format(exc)
    got = cannot(root, why)
    check("cannot-evaluate/base-no-merge-base", got[0] == (2, 2, True) and "no common ancestor" in got[1], got)
    root, why = git_case("h-badbase", None, seed_budget.replace("format-version = 1", "format-version = 2"),
                         None, seed_budget)
    got = cannot(root, why)
    check("cannot-evaluate/base-budget-off-schema", got[0] == (2, 2, True) and "at the merge base" in got[1], got)
    root, why = git_case("h-linkbase", None, seed_budget)
    try:
        if root is not None:
            _fx_git(root, "checkout", "-q", "main")
            (root / "budget-elsewhere.toml").write_text(seed_budget, encoding="utf-8")
            (root / BUDGET_REL).unlink()
            (root / BUDGET_REL).symlink_to(root / "budget-elsewhere.toml")
            _fx_git(root, "add", "-A")
            _fx_git(root, "commit", "-q", "--no-verify", "-m", "linked budget")
            _fx_git(root, "checkout", "-q", "-B", "topic")
            (root / BUDGET_REL).unlink()
            (root / BUDGET_REL).write_text(seed_budget, encoding="utf-8")
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        root, why = None, "fixture: {}".format(exc)
    got = cannot(root, why)
    check("cannot-evaluate/base-budget-symlink", got[0] == (2, 2, True) and "not a regular file" in got[1], got)


LANES = ("combined", "malformed", "system")
LANE_BARE = ("tools/gen_rules_core.py",)
LANE_DIRECT = ("tools/gen_rules_core.py", "--self-test")
LANE_STEP = ("tools/check_selftest_execution.py", "--suite", SUITE_ID)


def _lane_listing(lane):
    """The set of member lines tools/selftest_git_fixture_env_config_<lane>.py --list-shard 1/1 prints (its
    whole roster), or None when the child cannot run or does not exit 0. The runner is launched, never
    imported, so the git-fixture engine stays out of this renderer's import closure."""
    runner = Path(__file__).resolve().parent / "selftest_git_fixture_env_config_{}.py".format(lane)
    try:
        proc = subprocess.run([sys.executable, "-I", "-B", str(runner), "--list-shard", "1/1"],
                              capture_output=True, timeout=GIT_TIMEOUT * 5, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        print("lane {} listing could not run: {}".format(lane, exc))
        return None
    if proc.returncode != 0:
        print("lane {} listing exited {}: {}".format(lane, proc.returncode, _git_stderr(proc)))
        return None
    return {line for line in proc.stdout.decode("utf-8", "replace").splitlines() if line}


def _lane_ok(listing):
    """True when a lane listing keeps the suite step and the direct --self-test launch of this file and
    leaves its bare launch (its write mode) out."""
    return listing is not None and " ".join(LANE_STEP) in listing and " ".join(LANE_DIRECT) in listing \
        and " ".join(LANE_BARE) not in listing


def shutil_rmtree(path):
    import shutil
    shutil.rmtree(path)


if __name__ == "__main__":
    _code = main()
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
