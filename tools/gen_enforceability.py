#!/usr/bin/env python3
"""Generate .aiqt/enforceability.json, the machine-readable enforceability ledger (EN-5 PR-E).

For every rule in the corpus the ledger records which shipped mechanical controls cite it: the runtime
hooks from .aiqt/core/hooks/manifest.toml and the deterministic repository gates from
.aiqt/core/gates/manifest.toml. Each control carries its own residue verbatim (what it does NOT catch),
so linkage and its honest gap travel together. The manifests are the single edit points; this ledger is
generated and drift-gated, so the two can never fork. It reuses the sibling generators' validated
loaders (gen_rules.load_corpus, gen_hooks.load_manifest) so the hooks-manifest validation is never
forked, and _gen_common.reconcile for the drift/write step.

HONEST BOUNDARY. See the BOUNDARY constant below, emitted verbatim into the ledger.

STATUS DERIVATION. A non-empty `gates` array makes a rule gate-linked; else a non-empty `hooks` array
makes it hook-linked; else prose-only. The label names the most deterministic linkage point only; the
two arrays carry the FULL linkage, so the precedence collapses nothing (a rule cited by both a hook and
a gate reads gate-linked yet still lists its hook). Each gate and hook row carries its class letter, so a
rule linked only through class c gates reads as such from its rows.

PREVIEW HOOKS. The hooks published in .preview/ are not shipped controls: they are not in the plugin and
nothing installs them. .aiqt/core/hooks/preview.toml declares each one and the corpus ids its own module
docstring names (an entry may name none). The ledger lists every declared preview at the top level and, per
rule, the ids of the previews that name it; a preview never changes a status. The declaration is checked
fail-closed against .preview/ in both directions (a published hook with no entry, or an entry with no
published hook, exits 2) and each declared corpus id must resolve in the corpus AND its slug must appear in
that hook's own module docstring, so a link the hook does not itself name cannot be declared. Those .preview/
reads are validation only and never change the ledger bytes, so .preview/ is not a GENSRC_OUTPUTS source.

GRADING RUBRIC (the class letter grades a control's DECISION PROCEDURE against the rule's violation
surface; the a-versus-c line is TOTALITY over the examined class, not determinism of the scan):
  a, deterministic gate: a machine predicate over committed artefacts whose verdict is total for what it
    examines; the same tree always yields the same verdict, and a violation inside the examined class
    cannot pass. The byte-identity drift gates are the archetype: a drifted target cannot pass --check.
  b, hook-detectable: a runtime interception judging an ACTION at execution time, lexically or
    heuristically, best-effort against the accidental case. It lives only in the hooks manifest; it
    never appears in a gates manifest.
  c, partial: a control, deterministic in execution, that covers a recognizable SUBSET of the surface.
    check_secrets.py is class c even though the same input always yields the same verdict, because a
    high-entropy secret with no telltale shape passes the gate while the rule is still violated.
  d, judgement: compliance is decidable only by human or model judgement over intent and context. d is
    never authored on a control row: a control graded d would claim mechanical enforcement of a
    judgement call, and that claim would itself be the fabrication this ledger exists to prevent. A
    judgement rule appears as status prose-only with no control rows, and this tool fails closed
    (exit 2) on any control carrying d.

AUTHOR-GRADED LIMIT. The class letter and the residue are maintainer-authored assertions; this generator
validates only their PRESENCE and SHAPE (vocabulary and non-emptiness), never their TRUTH - a wrong class
or a stale residue survives the gate and is caught only by review.

  gen_enforceability.py             regenerate .aiqt/enforceability.json
  gen_enforceability.py --check     fail (exit 1) on drift; exit 2 on a bad input or a read/write error
  gen_enforceability.py --self-test build synthetic trees and assert the generator's own fail-closed invariants

Exit convention: 0 clean; 1 EXCLUSIVELY ledger byte-drift; 2 for everything else: an unreadable or
absent gates manifest or roster file; a malformed manifest; an orphan corpus-id; a class-d control; a
roster mismatch in either direction (or an asymmetry between the two roster files); a missing gate
script file; or a write error (reconcile's own SystemExit(2)). An unreadable or absent corpus or hooks
manifest likewise exits 2, but through the reused loaders (gen_rules.load_corpus, gen_hooks.load_manifest)
which raise on that input; those delegated loader paths are covered by their own suites, not this
self-test (see the self-test note).

PROTOCOL LIMITS. The inverse roster check sees only isolated `python3 -I -B tools/*.py` steps (the house
launcher grammar the check_python_launcher_isolation gate enforces); the gitleaks binary step, any
non-`python3 tools/*.py` gate, and any gate wired WITHOUT the `-I -B` isolation flags are invisible to it
and carry no manifest entry by that route (a manifest-listed gate wired un-isolated therefore falls out
of the roster parse and fails reconciliation, a second enforcement layer). That check is a line-based
lexical scan of trusted, controlled roster files; a `python3 -I -B tools/*.py` token embedded in a quoted
argument, a heredoc, or an eval string may be miscounted. The authoritative single-source of the roster
(generating both runners from this manifest) is deferred.
"""
import ast
import json
import re
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, load_toml, reconcile  # noqa: E402
from _standards import dir_present  # noqa: E402
from gen_rules import load_corpus  # noqa: E402
from gen_hooks import load_manifest, ID_RE, CID_RE  # noqa: E402  reuse the hooks-manifest loader and shapes

LEDGER_REL = ".aiqt/enforceability.json"
GATES_MANIFEST_REL = ".aiqt/core/gates/manifest.toml"
HOOKS_MANIFEST_REL = ".aiqt/core/hooks/manifest.toml"
RULES_DIR_REL = ".aiqt/core/rules"
PREVIEW_DECL_REL = ".aiqt/core/hooks/preview.toml"
PREVIEW_DIR_REL = ".preview"
PREVIEW_KEYS = {"id", "rules"}
# The Quality roster: the local mirror plus its CI workflow. Reading them to scan their gate steps is a
# VALIDATION-ONLY read that never changes the ledger bytes, so they are deliberately NOT GENSRC_OUTPUTS
# sources (the exclusion gen_gensrc's docstring defines; gen_hooks reading the corpus to cross-check ids
# is the cited precedent).
ROSTER_FILES = ("tools/run_all_checks.sh", ".github/workflows/quality.yml")

GATE_KEYS = {"id", "script", "rules", "platform", "default", "class", "residue"}
PLATFORMS = {"ci"}
DEFAULTS = {"block"}
CLASSES = {"a", "c"}   # b is the hook axis; d is never authored on a control (see the rubric)
SCRIPT_RE = re.compile(r"^(?:opf/)?tools/[A-Za-z0-9_]+\.py$")  # opf/ accepted toward OPF-SELF-CONTAIN; inert until an opf/tools path exists
# Matches the isolated python3 -I -B tools/*.py roster steps (the house launcher grammar the
# check_python_launcher_isolation gate enforces), so the inverse roster check sees exactly those steps:
# the gitleaks binary step is a shell step, invisible to this regex, and carries no manifest entry by
# design; a gate wired WITHOUT the -I -B flags likewise falls out of this parse, so an un-isolated
# manifest-listed gate fails reconciliation (a second enforcement layer). roster_scripts strips an inline
# comment before matching, so a trailing-comment token is not miscounted; this remains a line-based
# lexical scan of trusted, controlled roster files, so a `python3 -I -B tools/*.py` token embedded in a
# quoted argument, a heredoc, or an eval string may still be miscounted. The authoritative single-source
# of the roster (generating both runners from this manifest) is deferred.
ROSTER_RE = re.compile(r"python3 -I -B ((?:opf/)?tools/[A-Za-z0-9_]+\.py)")  # opf/ accepted toward OPF-SELF-CONTAIN; inert until an opf/tools path exists

# The BOUNDARY string carried at the ledger top level: the honest half of the artefact, in the file.
BOUNDARY = (
    "This ledger records LINKAGE, not coverage. A status says which shipped mechanical controls cite a "
    "rule, and each control carries a residue naming what it does not catch; the residue is the honest "
    "half of every claim. None of the statuses means a rule is enforced: gate-linked means at least one "
    "deterministic repository gate cites the rule, hook-linked means at least one runtime hook cites it "
    "and no gate does, prose-only means no shipped control cites it and the rule binds through the "
    "governing prose alone. Each gate and hook row carries its class letter; a rule linked only through "
    "class c gates is linked only for a recognizable subset of its surface. A preview is not a shipped "
    "control: the previews list names hooks published in .preview/ for manual installation, outside the "
    "plugin, whose own description names the rule, and a preview never changes a status. Nothing in this "
    "file is a completeness or coverage measure, and deriving a score from it misreads it.")

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata
# only, it does not affect what this generator produces. Sources are the content-bearing inputs the
# ledger DERIVES from (the corpus, the two manifests and the preview declaration); the roster files are
# excluded (see ROSTER_FILES).
GENSRC_OUTPUTS = (
    {"target": ".aiqt/enforceability.json", "kind": "file",
     "sources": (".aiqt/core/rules/", ".aiqt/core/hooks/manifest.toml",
                 ".aiqt/core/gates/manifest.toml", ".aiqt/core/hooks/preview.toml"),
     "regenerate": "python3 tools/gen_enforceability.py"},
)


def _exists(path):
    """Fail-closed existence probe (the gen_hooks idiom): Path.stat() raises on EACCES so an unreadable
    parent surfaces as OSError (the caller maps it to exit 2), rather than Path.exists() swallowing it
    and masking a present-but-unreadable target as absent."""
    try:
        path.stat()
    except FileNotFoundError:
        return False
    return True


def _req_str(table, key, where):
    value = table.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError("{}: field '{}' must be a non-empty string".format(where, key))
    return value


def load_gates_manifest(path, root):
    """Parse and fully validate the gates manifest; return the list of gate tables. Raises ValueError on
    any malformed field (tomllib.TOMLDecodeError subclasses ValueError) and OSError on an unreadable
    file; the caller's fail-closed try maps both to exit 2. Only SHAPE is validated here; each `rules`
    id's EXISTENCE is cross-checked against the corpus in cross_checks (the no-orphan pattern)."""
    data = load_toml(path)
    name = path.name
    top_extra = set(data) - {"gate"}
    if top_extra:
        raise ValueError("{}: unknown top-level key(s): {}".format(name, ", ".join(sorted(top_extra))))
    gates = data.get("gate")
    if not isinstance(gates, list) or not gates:
        raise ValueError("{}: at least one [[gate]] entry is required".format(name))
    seen_ids = set()
    seen_scripts = set()
    for gate in gates:
        if not isinstance(gate, dict):
            raise ValueError("{}: every [[gate]] must be a table".format(name))
        missing = GATE_KEYS - set(gate)
        if missing:
            raise ValueError("{}: [[gate]] missing key(s): {}".format(name, ", ".join(sorted(missing))))
        extra = set(gate) - GATE_KEYS
        if extra:
            raise ValueError("{}: [[gate]] unknown key(s): {}".format(name, ", ".join(sorted(extra))))
        gid = _req_str(gate, "id", "{}: [[gate]]".format(name))
        where = "{}: [[gate]] {}".format(name, gid)
        if not ID_RE.match(gid):
            raise ValueError("{}: id must match ^[a-z][a-z0-9-]*$ (a kebab-case control id)".format(where))
        if gid in seen_ids:
            raise ValueError("{}: duplicate gate id".format(where))
        seen_ids.add(gid)
        script = _req_str(gate, "script", where)
        if not SCRIPT_RE.match(script):
            raise ValueError("{}: script must be a repo-relative tools/<name>.py".format(where))
        if script in seen_scripts:
            raise ValueError("{}: duplicate script '{}' (one entry per gate script)".format(where, script))
        seen_scripts.add(script)
        if not _exists(root / script):
            raise ValueError("{}: script '{}' does not exist on disk".format(where, script))
        rules = gate.get("rules")
        if not isinstance(rules, list) or not all(isinstance(r, str) and r for r in rules):
            raise ValueError("{}: rules must be a list of corpus-id strings (it may be empty)".format(where))
        for rule in rules:
            if not CID_RE.match(rule):
                raise ValueError("{}: rules entry '{}' is not a corpus-id shape (^[a-z0-9]{{6,}}$)"
                                 .format(where, rule))
        if _req_str(gate, "platform", where) not in PLATFORMS:
            raise ValueError("{}: platform must be one of {}".format(where, "/".join(sorted(PLATFORMS))))
        if _req_str(gate, "default", where) not in DEFAULTS:
            raise ValueError("{}: default must be one of {}".format(where, "/".join(sorted(DEFAULTS))))
        if _req_str(gate, "class", where) not in CLASSES:
            raise ValueError("{}: class must be one of {} (b is the hook axis; d is never authored on a "
                             "control)".format(where, "/".join(sorted(CLASSES))))
        _req_str(gate, "residue", where)  # required, never empty: the gate's honest residue gap
    return gates


def _published_previews(root):
    """The stems of the hook files published in .preview/ (every *.py entry), or an empty set when the
    directory is absent. A .preview path that is present but unreadable raises OSError (fail-closed)."""
    try:
        names = sorted(entry.name for entry in (root / PREVIEW_DIR_REL).iterdir())
    except FileNotFoundError:
        return set()
    return {name[:-3] for name in names if name.endswith(".py")}


def _preview_docstring(root, rel):
    """The module docstring of the declared preview hook file root/rel, validated before any of its rules
    are read: the path must be a regular file (a symlink or a directory is not), readable, UTF-8, parse
    as Python, and carry a non-empty module docstring. An unreadable file raises OSError; every other
    failure raises ValueError (a SyntaxError is converted, since it is not a ValueError subclass), so
    run() fails closed with exit 2 either way."""
    path = root / rel
    if not stat.S_ISREG(path.lstat().st_mode):  # lstat raises OSError on an absent or unreadable path
        raise ValueError("{} is not a regular file".format(rel))
    text = path.read_text(encoding="utf-8")  # OSError, or UnicodeDecodeError (a ValueError)
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError as exc:
        raise ValueError("{} does not parse as Python ({}, line {})".format(
            rel, exc.msg, exc.lineno)) from None
    doc = ast.get_docstring(tree)
    if not doc:
        raise ValueError("{} has no module docstring".format(rel))
    return doc


def _names_slug(doc, slug):
    """True when the docstring NAMES the slug: the slug appears, in any letter case, with no word
    character (a letter, digit or underscore) and no hyphen directly on either side. The forward check (a
    declared rule must be named) and the reverse check (a named rule must be declared) share this one
    predicate, so the two directions cannot disagree about what naming is."""
    pattern = r"(?<![\w-]){}(?![\w-])".format(re.escape(slug))
    return re.search(pattern, doc, re.IGNORECASE) is not None


def load_previews(root, slugs):
    """Parse and validate the preview declaration against .preview/ and the corpus; return its entries
    sorted by id, each as {id, file, rules}. `slugs` maps corpus id to slug. An absent declaration with no
    published preview hook is an empty list; every other disagreement raises ValueError (exit 2)."""
    published = _published_previews(root)
    decl = root / PREVIEW_DECL_REL
    if not _exists(decl):
        if published:
            raise ValueError("{} is absent but .preview/ publishes hook(s): {}".format(
                PREVIEW_DECL_REL, ", ".join(sorted(published))))
        return []
    data = load_toml(decl)
    top_extra = set(data) - {"preview"}
    if top_extra:
        raise ValueError("{}: unknown top-level key(s): {}".format(PREVIEW_DECL_REL,
                                                                  ", ".join(sorted(top_extra))))
    entries = data.get("preview", [])
    if not isinstance(entries, list):
        raise ValueError("{}: preview must be an array of tables".format(PREVIEW_DECL_REL))
    out = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != PREVIEW_KEYS:
            raise ValueError("{}: every [[preview]] must be a table with exactly the keys {}".format(
                PREVIEW_DECL_REL, ", ".join(sorted(PREVIEW_KEYS))))
        pid = _req_str(entry, "id", "{}: [[preview]]".format(PREVIEW_DECL_REL))
        where = "{}: [[preview]] {}".format(PREVIEW_DECL_REL, pid)
        if not ID_RE.match(pid):
            raise ValueError("{}: id must match ^[a-z][a-z0-9-]*$".format(where))
        if any(p["id"] == pid for p in out):
            raise ValueError("{}: duplicate preview id".format(where))
        rules = entry["rules"]
        if not isinstance(rules, list) or not all(isinstance(r, str) for r in rules):
            raise ValueError("{}: rules must be a list of corpus-id strings (it may be empty)".format(where))
        if rules != sorted(set(rules)):
            raise ValueError("{}: rules must be unique and sorted".format(where))
        rel = "{}/{}.py".format(PREVIEW_DIR_REL, pid)
        if pid not in published:
            raise ValueError("{}: no published preview hook {}".format(where, rel))
        doc = _preview_docstring(root, rel)  # every declared file, whether or not it lists rules
        for rule in rules:
            if rule not in slugs:
                raise ValueError("{}: cites corpus-id '{}' not in .aiqt/core/rules/".format(where, rule))
            if not _names_slug(doc, slugs[rule]):
                raise ValueError("{}: the module docstring of {} does not name rule '{}' (slug {})".format(
                    where, rel, rule, slugs[rule]))
        unlisted = sorted(cid for cid, slug in slugs.items() if cid not in rules and _names_slug(doc, slug))
        if unlisted:
            raise ValueError("{}: the module docstring of {} names rule(s) the entry does not list: "
                             "{}".format(where, rel, ", ".join("{} (slug {})".format(cid, slugs[cid])
                                                               for cid in unlisted)))
        out.append({"id": pid, "file": rel, "rules": rules})
    undeclared = sorted(published - {p["id"] for p in out})
    if undeclared:
        raise ValueError("{}: published preview hook(s) with no entry: {}".format(
            PREVIEW_DECL_REL, ", ".join(undeclared)))
    return sorted(out, key=lambda p: p["id"])


def roster_scripts(root):
    """Return (union, per_file): the isolated python3 -I -B tools/*.py scripts the roster files invoke, as a union set
    and as a per-file dict. Each ROSTER_FILES entry is read (an absent or unreadable roster raises
    OSError, fail-closed per the check-fails-closed rule, never an empty set); comment lines are dropped;
    ROSTER_RE matches are collected per file. A roster that parses to zero scripts is malformed
    (ValueError), mirroring gen_gensrc's zero-generators guard."""
    per_file = {}
    for rel in ROSTER_FILES:
        text = (root / rel).read_text(encoding="utf-8")  # OSError -> caller's fail-closed try
        scripts = set()
        for line in text.splitlines():
            # Strip an inline comment before matching (a conservative lexical cut, documented at
            # ROSTER_RE): the first space- or tab-preceded `#` ends a trailing comment, so
            # `true # python3 -I -B tools/ghost.py` (space or tab before the #) no longer miscounts ghost.py;
            # a leading "#" then drops a whole comment line. This does not parse the shell, so a token
            # inside a quoted argument or a heredoc may still be miscounted.
            code = re.split(r"[ \t]#", line, maxsplit=1)[0]
            if code.lstrip().startswith("#"):
                continue
            scripts.update(ROSTER_RE.findall(code))
        if not scripts:
            raise ValueError("roster {} names no python3 -I -B tools/*.py gate step (malformed)".format(rel))
        per_file[rel] = scripts
    union = set().union(*per_file.values())
    return union, per_file


def cross_checks(gates, hooks, corpus_ids, roster_union, per_file):
    """The manifests' outward references and the inverse roster, all fail-closed (ValueError -> exit 2)."""
    # No orphans: every rules element of every gate AND every hook resolves in the corpus.
    for gate in gates:
        for rule in gate["rules"]:
            if rule not in corpus_ids:
                raise ValueError("gates manifest: gate '{}' cites corpus-id '{}' not in .aiqt/core/rules/"
                                 .format(gate["id"], rule))
    for hook in hooks:
        for rule in hook["rules"]:
            if rule not in corpus_ids:
                raise ValueError("hooks manifest: hook '{}' cites corpus-id '{}' not in .aiqt/core/rules/"
                                 .format(hook["id"], rule))
    # Class d is never authored on a control (the rubric). Gates are already restricted to {a, c} by
    # load_gates_manifest; hooks reach a-d through gen_hooks.load_manifest, so re-check them here at the
    # ledger boundary (and re-check gates too, as defence in depth against a future loader change).
    for gate in gates:
        if gate["class"] == "d":
            raise ValueError("gates manifest: gate '{}' is class d; d is never authored on a control"
                             .format(gate["id"]))
    for hook in hooks:
        if hook["class"] == "d":
            raise ValueError("hooks manifest: hook '{}' is class d; d is never authored on a control"
                             .format(hook["id"]))
    # Inverse roster, bidirectional AND symmetric across the two roster files. A roster script with no
    # manifest entry is an uncatalogued gate; a manifest entry whose script runs in no roster is a
    # linkage claim with no roster step; a script in one roster file but not the other is an asymmetry
    # between the local mirror and CI. All fail closed.
    manifest_scripts = {gate["script"] for gate in gates}
    files = sorted(per_file)
    first, second = per_file[files[0]], per_file[files[1]]
    if first != second:
        only_first = sorted(first - second)
        only_second = sorted(second - first)
        raise ValueError("roster asymmetry: {} and {} do not invoke the same gate scripts; only in {}: "
                         "{}; only in {}: {}".format(files[0], files[1], files[0], only_first,
                                                     files[1], only_second))
    uncatalogued = sorted(roster_union - manifest_scripts)
    if uncatalogued:
        raise ValueError("roster script(s) with no gates-manifest entry (an uncatalogued gate): {}"
                         .format(", ".join(uncatalogued)))
    no_gate = sorted(manifest_scripts - roster_union)
    if no_gate:
        raise ValueError("gates-manifest script(s) run in no roster (a linkage claim with no roster step): "
                         "{}".format(", ".join(no_gate)))


def _hook_row(hook):
    """The ledger linkage row for one hook: id, event, optional matcher, platform, default, class,
    residue, carried verbatim from the manifest so the ledger cannot fork from its single edit point."""
    row = {"id": hook["id"], "event": hook["event"], "platform": hook["platform"],
           "default": hook["default"], "class": hook["class"], "residue": hook["residue"]}
    if "matcher" in hook:
        row["matcher"] = hook["matcher"]
    return row


def _gate_row(gate):
    """The ledger linkage row for one gate: id, script, platform, default, class, residue, verbatim."""
    return {"id": gate["id"], "script": gate["script"], "platform": gate["platform"],
            "default": gate["default"], "class": gate["class"], "residue": gate["residue"]}


def build_ledger(root):
    """The full .aiqt/enforceability.json text, rendered json.dumps(indent=2, sort_keys=True) plus a
    trailing newline for a deterministic byte-identity target. Raises ValueError/OSError on a malformed
    or unreadable input; the caller maps those to exit 2."""
    rules_dir = root / RULES_DIR_REL
    # dir_present (not is_dir): an unreadable .aiqt/ parent must fail closed as exit 2, not read as an
    # absent corpus (which would emit an empty ledger, a false clean).
    if not dir_present(rules_dir):
        raise ValueError("cannot build the ledger: no {} to load".format(rules_dir))
    corpus = load_corpus(rules_dir)
    corpus_ids = {str(fm["corpus-id"]) for _src, fm, _rel in corpus}
    slugs = {str(fm["corpus-id"]): str(fm["slug"]) for _src, fm, _rel in corpus}
    _plugin, hooks = load_manifest(root / HOOKS_MANIFEST_REL)
    gates = load_gates_manifest(root / GATES_MANIFEST_REL, root)
    roster_union, per_file = roster_scripts(root)
    cross_checks(gates, hooks, corpus_ids, roster_union, per_file)
    previews = load_previews(root, slugs)

    entries = []
    for src, fm, _rel in sorted(corpus, key=lambda t: str(t[1]["corpus-id"])):
        cid = str(fm["corpus-id"])
        rule_hooks = sorted((h for h in hooks if cid in h["rules"]), key=lambda h: h["id"])
        rule_gates = sorted((g for g in gates if cid in g["rules"]), key=lambda g: g["id"])
        if rule_gates:
            status = "gate-linked"
        elif rule_hooks:
            status = "hook-linked"
        else:
            status = "prose-only"
        entries.append({
            "corpus-id": cid,
            "source": src.relative_to(root).as_posix(),
            "family": str(fm["family"]),
            "status": status,
            "hooks": [_hook_row(h) for h in rule_hooks],
            "gates": [_gate_row(g) for g in rule_gates],
            "previews": [p["id"] for p in previews if cid in p["rules"]],
        })
    obj = {"version": 1, "boundary": BOUNDARY, "rules": entries, "previews": previews}
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def run(root, check):
    """Render the ledger into root/.aiqt/enforceability.json, or (check mode) report drift. Fail-closed
    (exit 2) on a bad input or an unreadable/unwritable file, mirroring the sibling generators."""
    try:
        text = build_ledger(root)
    except (ValueError, OSError) as exc:
        print("error: cannot build {} ({}); fail-closed".format(LEDGER_REL, exc), file=sys.stderr)
        return 2
    drifted = reconcile(root / LEDGER_REL, text, check)  # shared reconcile fail-closes (exit 2) on an
                                                         # OSError or an invalid-UTF-8 target
    if drifted:
        print("drift: {} is out of date; run tools/gen_enforceability.py".format(LEDGER_REL),
              file=sys.stderr)
        return 1
    if not check:
        # A console-only status summary; the artefact itself carries no counts and no score.
        obj = json.loads(text)
        counts = {}
        for entry in obj["rules"]:
            counts[entry["status"]] = counts.get(entry["status"], 0) + 1
        summary = ", ".join("{} {}".format(counts[s], s) for s in sorted(counts))
        print("wrote {} ({} rules: {})".format(LEDGER_REL, len(obj["rules"]), summary))
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test_main()
    return run(repo_root(), "--check" in args)


# --- self-test ----------------------------------------------------------------------------------------
# Proves the generator's own fail-closed invariants against synthetic trees (the gen_hooks/gen_gensrc
# pattern), so gen_enforceability never becomes an ungated generator:
#   (a) a conformant tree generates (exit 0) and re-checks drift-clean (exit 0); the ledger exists, all
#       four synthetic rules derive the correct status (a hook+gate rule reads gate-linked yet still
#       lists its hook), and the boundary string is present at the top level,
#   (b) a mutated ledger fails --check (exit 1),
#   (c) a gates manifest citing an unknown corpus-id fails closed (exit 2): the no-orphan cross-check,
#   (d) an empty gate residue fails closed (exit 2),
#   (e) a gate class outside {a, c} (both "b" and a nonsense letter) fails closed (exit 2),
#   (f) a hook mutated to class "d" fails closed (exit 2): d never authored, even where gen_hooks accepts it,
#   (g) a roster script with no manifest entry fails closed (exit 2): the uncatalogued-gate direction,
#   (h) a manifest entry whose script runs in no roster fails closed (exit 2): a gate claim with no gate,
#   (i) a manifest `script` absent on disk fails closed (exit 2),
#   (j) an unreadable gates manifest fails closed (exit 2); skipped when the runner reads a chmod-0 file,
#   (k) a duplicate gate id, and separately a duplicate script, each fail closed (exit 2),
#   (l) a gates manifest absent entirely fails closed (exit 2): absence is never an empty roster,
#   (m) a roster file absent, and separately one yielding zero scripts, each fail closed (exit 2),
#   (n) a hooks manifest citing an unknown corpus-id fails closed (exit 2): the hook no-orphan direction,
#   (o) a gates manifest with an unknown top-level key, and separately an unknown [[gate]] entry key,
#       each fail closed (exit 2): the load_gates_manifest shape validation,
#   (p) a roster whose ONLY occurrence of a manifest script is inside an inline comment does not
#       enumerate that script, so its manifest entry reads as a linkage claim with no roster step and
#       fails closed (exit 2): proves the F-148 comment strip (the quoted-argument / heredoc residual
#       stays a disclosed limit and is deliberately not asserted here).
#   (q) a tree with published preview hooks and a matching declaration lists every preview at the top
#       level and per rule, and a rule named only by a preview stays prose-only (a preview is never a
#       shipped control); with no .preview/ and no declaration every rule lists no preview,
#   (r) a published preview hook with no declaration entry, (s) an entry naming no published hook, (t) an
#       entry citing a rule whose slug the hook's own docstring does not name, (u) an entry citing an
#       unknown corpus-id, and (v) published preview hooks with the declaration absent each fail closed
#       (exit 2),
#   (w) a declared preview file that lists NO rules is still validated: one that is unreadable, not
#       UTF-8, a directory, a symlink, unparseable, or has no module docstring each fails closed (exit 2),
#   (x) a linked preview file that does not parse returns exit 2 from run() instead of raising,
#   (y) a slug with a letter (either case), digit, underscore or hyphen directly beside it is not named,
#       while the slug in another letter case between punctuation is named, and
#   (z) an entry whose docstring names a corpus slug it does not list fails closed (exit 2), with
#       naming defined exactly as in (y).
# These cases exercise this tool's OWN logic (the gates manifest shape, the class-d ledger boundary, the
# hook and gate no-orphan checks, the roster reconciliation, and drift). The corpus and hooks-manifest
# read-failure paths and the empty-hook-residue path are validated fail-closed by the reused loaders
# (gen_rules.load_corpus, gen_hooks.load_manifest) and covered by THEIR suites, so they are not
# re-tested here.
# A raised SystemExit anywhere in a run() call is caught by run_quiet and recorded as a FAILURE, so the
# self-test can never itself exit early (green or otherwise) on an unexpected raise.

_APEX = """---
corpus-id: apex01
origin: pack
family: aiqt
apex: true
slug: project-integrity
---
# Project integrity

The apex rule for the self-test corpus.
"""


def _rule_src(cid, facet, slug):
    return ("---\ncorpus-id: {}\norigin: pack\nfamily: aiqt\ntier: 10\nfacet: {}\nslug: {}\n---\n"
            "# {}\n\nA rule for the gen_enforceability self-test corpus.\n".format(cid, facet, slug, slug))


_HOOKS = """[plugin]
name = "aiqt-selftest-hooks"
version = "0.1.0"
description = "Self-test plugin."
author-name = "Self Test"
author-email = "selftest@example.invalid"
homepage = "https://example.invalid"

[[hook]]
id = "hook-rule1"
rules = ["ruleaa"]
platform = "claude-code"
event = "PreToolUse"
matcher = "Bash"
handler = "h_one"
default = "block"
class = "b"
residue = "A self-test hook on rule 1."

[[hook]]
id = "hook-rule3"
rules = ["rulecc"]
platform = "claude-code"
event = "Stop"
handler = "h_two"
default = "warn"
class = "b"
residue = "A self-test hook on rule 3."
"""

_GATES = """[[gate]]
id = "gate-alpha"
script = "tools/g_alpha.py"
rules = ["ruleaa", "rulebb"]
platform = "ci"
default = "block"
class = "a"
residue = "A self-test drift gate."

[[gate]]
id = "gate-empty"
script = "tools/g_empty.py"
rules = []
platform = "ci"
default = "block"
class = "a"
residue = "A self-test explicit-empty gate."
"""

_ROSTER_SH = """#!/usr/bin/env bash
# a self-test roster mirror
run_gate "alpha-selftest" python3 -I -B tools/g_alpha.py --self-test
run_gate "alpha" python3 -I -B tools/g_alpha.py --check
run_gate "empty" python3 -I -B tools/g_empty.py
"""

_ROSTER_YML = """name: Quality
jobs:
  quality:
    steps:
      # a self-test CI roster
      - run: python3 -I -B tools/g_alpha.py --self-test
      - run: python3 -I -B tools/g_alpha.py --check
      - run: python3 -I -B tools/g_empty.py
"""


_PREVIEW_ONE = '''"""A self-test preview hook motivated by the selftest-rule-dd rule."""
'''
_PREVIEW_TWO = '''"""A self-test preview hook whose docstring names no rule."""
'''
_PREVIEW_DECL = """[[preview]]
id = "p-one"
rules = ["ruledd"]

[[preview]]
id = "p-two"
rules = []
"""


def _add_previews(base):
    """Publish two preview hooks in .preview/ (one naming rule dd by slug, one naming no rule), a README
    that is not a hook, and a declaration matching them."""
    preview = base / ".preview"
    preview.mkdir()
    (preview / "README.md").write_text("# self-test preview channel\n", encoding="utf-8")
    (preview / "p-one.py").write_text(_PREVIEW_ONE, encoding="utf-8")
    (preview / "p-two.py").write_text(_PREVIEW_TWO, encoding="utf-8")
    (base / PREVIEW_DECL_REL).write_text(_PREVIEW_DECL, encoding="utf-8")
    return base


def _build(base):
    """A conformant synthetic tree: a mini corpus (apex + 4 rules), a hooks manifest citing rule 1 and
    rule 3, a gates manifest citing rule 1 (so one rule is hook+gate) and rule 2 plus an explicit-empty
    gate, the two gate script files, and two roster files invoking exactly the manifest's scripts."""
    rules = base / ".aiqt" / "core" / "rules"
    rules.mkdir(parents=True)
    (rules / "00-project-integrity.md").write_text(_APEX, encoding="utf-8")
    (rules / "rule-aa.md").write_text(_rule_src("ruleaa", "TRUST", "selftest-rule-aa"), encoding="utf-8")
    (rules / "rule-bb.md").write_text(_rule_src("rulebb", "INTEG", "selftest-rule-bb"), encoding="utf-8")
    (rules / "rule-cc.md").write_text(_rule_src("rulecc", "QUALI", "selftest-rule-cc"), encoding="utf-8")
    (rules / "rule-dd.md").write_text(_rule_src("ruledd", "ACCUR", "selftest-rule-dd"), encoding="utf-8")
    hooks_dir = base / ".aiqt" / "core" / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "manifest.toml").write_text(_HOOKS, encoding="utf-8")
    gates_dir = base / ".aiqt" / "core" / "gates"
    gates_dir.mkdir(parents=True)
    (gates_dir / "manifest.toml").write_text(_GATES, encoding="utf-8")
    tools = base / "tools"
    tools.mkdir(parents=True)
    (tools / "g_alpha.py").write_text("# self-test gate script\n", encoding="utf-8")
    (tools / "g_empty.py").write_text("# self-test gate script\n", encoding="utf-8")
    (tools / "run_all_checks.sh").write_text(_ROSTER_SH, encoding="utf-8")
    workflows = base / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "quality.yml").write_text(_ROSTER_YML, encoding="utf-8")
    return base


def self_test_main():
    import io
    import os
    import shutil
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr

    def run_quiet(root, check):
        # A raised SystemExit (e.g. reconcile's OSError path) is caught and returned as a non-int
        # sentinel so it registers as a FAILURE against any expected exit code, rather than aborting the
        # self-test or letting it exit early green.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                return run(root, check)
            except SystemExit as exc:
                return "raised SystemExit({!r})".format(exc.code)

    def run_caught(root, check):
        # Like run_quiet, but an unexpected exception of any kind is also returned as a non-int
        # sentinel, so a case expecting exit 2 records a failure rather than crashing the self-test.
        try:
            return run_quiet(root, check)
        except Exception as exc:  # noqa: BLE001  the escape itself is what the case detects
            return "raised {}".format(type(exc).__name__)

    def replace_in(path, old, new):
        text = path.read_text(encoding="utf-8")
        if old not in text:
            failures.append("self-test setup: {!r} not found in {}".format(old, path.name))
        path.write_text(text.replace(old, new), encoding="utf-8")

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-enforceability-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    skipped = []
    unread_manifest = None

    # (opf) OPF-SELF-CONTAIN grammar-widening (change-carries-check): SCRIPT_RE and ROSTER_RE now
    # accept an opf/tools/*.py path as well as the current tools/*.py, while staying anchored so a
    # path under any other parent is still rejected. These module-level assertions fail without the
    # widening and are independent of the synthetic-tree cases below.
    if not SCRIPT_RE.match("tools/g_alpha.py"):
        failures.append("opf: SCRIPT_RE must still accept a current tools/*.py script")
    if not SCRIPT_RE.match("opf/tools/g_alpha.py"):
        failures.append("opf: SCRIPT_RE must accept an opf/tools/*.py script (grammar-widening)")
    if SCRIPT_RE.match("evil/tools/g_alpha.py"):
        failures.append("opf: SCRIPT_RE must stay anchored (no parent prefix other than opf/)")
    if ROSTER_RE.findall("python3 -I -B tools/g_alpha.py --check") != ["tools/g_alpha.py"]:
        failures.append("opf: ROSTER_RE must still extract a current tools/*.py roster step")
    if ROSTER_RE.findall("python3 -I -B opf/tools/g_alpha.py --check") != ["opf/tools/g_alpha.py"]:
        failures.append("opf: ROSTER_RE must extract an opf/tools/*.py roster step (grammar-widening)")

    try:
        # (a) Conformant tree: generate, re-check drift-clean, statuses correct, boundary present.
        good = tmp / "good"
        _build(good)
        if run_quiet(good, check=False) != 0:
            failures.append("conformant tree: generation expected exit 0")
        if run_quiet(good, check=True) != 0:
            failures.append("conformant tree: regeneration expected drift-clean exit 0")
        ledger = good / LEDGER_REL
        if not ledger.is_file():
            failures.append("conformant tree: expected {} to be written".format(LEDGER_REL))
        else:
            obj = json.loads(ledger.read_text(encoding="utf-8"))
            by_id = {e["corpus-id"]: e for e in obj.get("rules", [])}
            expected = {"ruleaa": "gate-linked", "rulebb": "gate-linked", "rulecc": "hook-linked",
                        "ruledd": "prose-only", "apex01": "prose-only"}
            for cid, status in expected.items():
                if cid not in by_id:
                    failures.append("conformant tree: rule {} missing from the ledger".format(cid))
                elif by_id[cid]["status"] != status:
                    failures.append("conformant tree: rule {} status {!r}, expected {!r}".format(
                        cid, by_id[cid]["status"], status))
            if "ruleaa" in by_id and (not by_id["ruleaa"]["hooks"] or not by_id["ruleaa"]["gates"]):
                failures.append("conformant tree: the hook+gate rule must list BOTH its hook and its gate")
            if obj.get("boundary") != BOUNDARY:
                failures.append("conformant tree: the boundary string is absent or altered")
            if obj.get("previews") != [] or any(e.get("previews") != [] for e in by_id.values()):
                failures.append("conformant tree: with no .preview/ every rule must list no preview")

        # (b) Mutated ledger fails --check (exit 1).
        if ledger.is_file():
            ledger.write_text(ledger.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            if run_quiet(good, check=True) != 1:
                failures.append("mutated {} expected exit 1 (drift)".format(LEDGER_REL))

        # (c) Gates manifest citing an unknown corpus-id fails closed (exit 2).
        unk = _build(tmp / "unknown-cid")
        replace_in(unk / GATES_MANIFEST_REL, 'rules = ["ruleaa", "rulebb"]', 'rules = ["nosuch9"]')
        if run_quiet(unk, check=True) != 2:
            failures.append("unknown corpus-id in the gates manifest expected exit 2 (fail-closed)")

        # (d) An empty gate residue fails closed (exit 2).
        dres = _build(tmp / "empty-residue")
        replace_in(dres / GATES_MANIFEST_REL, 'residue = "A self-test explicit-empty gate."',
                   'residue = ""')
        if run_quiet(dres, check=True) != 2:
            failures.append("empty gate residue expected exit 2 (fail-closed)")

        # (e) A gate class outside {a, c} fails closed (exit 2): both "b" and a nonsense letter.
        for bad in ("b", "z"):
            ecls = _build(tmp / ("bad-gate-class-" + bad))
            replace_in(ecls / GATES_MANIFEST_REL,
                       'class = "a"\nresidue = "A self-test drift gate."',
                       'class = "{}"\nresidue = "A self-test drift gate."'.format(bad))
            if run_quiet(ecls, check=True) != 2:
                failures.append("gate class {!r} outside {{a, c}} expected exit 2 (fail-closed)".format(bad))

        # (f) A hook mutated to class "d" fails closed (exit 2), even though gen_hooks would accept d.
        fhd = _build(tmp / "hook-class-d")
        replace_in(fhd / HOOKS_MANIFEST_REL,
                   'class = "b"\nresidue = "A self-test hook on rule 1."',
                   'class = "d"\nresidue = "A self-test hook on rule 1."')
        if run_quiet(fhd, check=True) != 2:
            failures.append("a hook control at class d expected exit 2 (d never authored)")

        # (g) A roster script with no manifest entry fails closed (exit 2): the uncatalogued-gate
        #     direction. Added to BOTH roster files so the two stay symmetric and the uncatalogued check
        #     (not the asymmetry check) is the one exercised.
        gex = _build(tmp / "roster-extra")
        for rel in ROSTER_FILES:
            p = gex / rel
            p.write_text(p.read_text(encoding="utf-8") +
                         "\n# extra step\nrun: python3 -I -B tools/g_extra.py\n",
                         encoding="utf-8")
        if run_quiet(gex, check=True) != 2:
            failures.append("a roster script with no manifest entry expected exit 2 (uncatalogued gate)")

        # (h) A manifest entry whose script runs in no roster fails closed (exit 2): a gate claim with no
        #     gate. The script exists on disk (so load passes) but is absent from both rosters.
        hng = _build(tmp / "gate-no-roster")
        (hng / "tools" / "g_orphan.py").write_text("# self-test gate script\n", encoding="utf-8")
        gm = hng / GATES_MANIFEST_REL
        gm.write_text(gm.read_text(encoding="utf-8") +
                      '\n[[gate]]\nid = "gate-orphan"\nscript = "tools/g_orphan.py"\nrules = []\n'
                      'platform = "ci"\ndefault = "block"\nclass = "a"\n'
                      'residue = "A self-test orphan gate."\n', encoding="utf-8")
        if run_quiet(hng, check=True) != 2:
            failures.append("a manifest gate whose script runs in no roster expected exit 2 (no gate)")

        # (i) A manifest `script` absent on disk fails closed (exit 2): the stat probe in load.
        ims = _build(tmp / "gate-missing-script")
        gm = ims / GATES_MANIFEST_REL
        gm.write_text(gm.read_text(encoding="utf-8") +
                      '\n[[gate]]\nid = "gate-ghost"\nscript = "tools/g_ghost.py"\nrules = []\n'
                      'platform = "ci"\ndefault = "block"\nclass = "a"\n'
                      'residue = "A self-test ghost gate."\n', encoding="utf-8")
        if run_quiet(ims, check=True) != 2:
            failures.append("a manifest gate whose script is absent on disk expected exit 2 (fail-closed)")

        # (j) An unreadable gates manifest fails closed (exit 2). Skipped where the runner can still read
        #     a chmod-0 file (root/DAC-bypass), observed via os.access, as the sibling generators do.
        unread = _build(tmp / "unreadable-gates")
        unread_manifest = unread / GATES_MANIFEST_REL
        os.chmod(unread_manifest, 0)
        if os.access(unread_manifest, os.R_OK):
            skipped.append("j unreadable-gates-manifest")
        elif run_quiet(unread, check=True) != 2:
            failures.append("an unreadable gates manifest expected exit 2 (fail-closed)")
        os.chmod(unread_manifest, 0o644)  # restore so cleanup can remove it
        unread_manifest = None

        # (k) A duplicate gate id, and separately a duplicate script, each fail closed (exit 2).
        kid = _build(tmp / "dup-id")
        replace_in(kid / GATES_MANIFEST_REL, 'id = "gate-empty"', 'id = "gate-alpha"')
        if run_quiet(kid, check=True) != 2:
            failures.append("a duplicate gate id expected exit 2 (fail-closed)")
        kscript = _build(tmp / "dup-script")
        replace_in(kscript / GATES_MANIFEST_REL, 'script = "tools/g_empty.py"',
                   'script = "tools/g_alpha.py"')
        if run_quiet(kscript, check=True) != 2:
            failures.append("a duplicate gate script expected exit 2 (fail-closed)")

        # (l) A gates manifest absent entirely fails closed (exit 2): absence is never an empty roster.
        lab = _build(tmp / "no-gates-manifest")
        (lab / GATES_MANIFEST_REL).unlink()
        if run_quiet(lab, check=True) != 2:
            failures.append("an absent gates manifest expected exit 2 (fail-closed)")

        # (m) A roster file absent, and separately one yielding zero scripts, each fail closed (exit 2).
        mabs = _build(tmp / "roster-absent")
        (mabs / ROSTER_FILES[0]).unlink()
        if run_quiet(mabs, check=True) != 2:
            failures.append("an absent roster file expected exit 2 (fail-closed)")
        mzero = _build(tmp / "roster-zero")
        (mzero / ROSTER_FILES[1]).write_text("name: Quality\n# no python3 gate steps here\n",
                                             encoding="utf-8")
        if run_quiet(mzero, check=True) != 2:
            failures.append("a roster file yielding zero scripts expected exit 2 (fail-closed)")

        # (n) A hooks manifest citing an unknown corpus-id fails closed (exit 2): the hook no-orphan
        #     direction, re-checked at the ledger boundary (case (c) is its gate twin).
        nhk = _build(tmp / "hook-unknown-cid")
        replace_in(nhk / HOOKS_MANIFEST_REL, 'rules = ["ruleaa"]', 'rules = ["nosuch9"]')
        if run_quiet(nhk, check=True) != 2:
            failures.append("an unknown corpus-id in the hooks manifest expected exit 2 (fail-closed)")

        # (o) A gates manifest with an unknown top-level key, and separately an unknown [[gate]] entry
        #     key, each fail closed (exit 2): the load_gates_manifest shape validation.
        otop = _build(tmp / "gates-unknown-top-key")
        gm = otop / GATES_MANIFEST_REL
        gm.write_text("bogus = 1\n" + gm.read_text(encoding="utf-8"), encoding="utf-8")
        if run_quiet(otop, check=True) != 2:
            failures.append("an unknown top-level key in the gates manifest expected exit 2 (fail-closed)")
        oent = _build(tmp / "gates-unknown-entry-key")
        replace_in(oent / GATES_MANIFEST_REL,
                   'class = "a"\nresidue = "A self-test drift gate."',
                   'class = "a"\nbogus = 1\nresidue = "A self-test drift gate."')
        if run_quiet(oent, check=True) != 2:
            failures.append("an unknown [[gate]] entry key expected exit 2 (fail-closed)")

        # (p) A roster whose ONLY occurrence of a manifest script is inside a TRAILING inline comment does
        #     not enumerate that script (the F-148 strip), so its manifest entry reads as a linkage claim
        #     with no roster step and fails closed (exit 2). Without the strip the commented token would
        #     enumerate and the tree would read clean, so this case proves the tightened parser. Added to
        #     BOTH roster files so the two stay symmetric and the no-roster (not the asymmetry) check
        #     fires. The quoted-argument / heredoc residual stays a disclosed limit, not asserted here.
        pinl = _build(tmp / "roster-inline-comment")
        (pinl / "tools" / "g_inline.py").write_text("# self-test gate script\n", encoding="utf-8")
        gm = pinl / GATES_MANIFEST_REL
        gm.write_text(gm.read_text(encoding="utf-8") +
                      '\n[[gate]]\nid = "gate-inline"\nscript = "tools/g_inline.py"\nrules = []\n'
                      'platform = "ci"\ndefault = "block"\nclass = "a"\n'
                      'residue = "A self-test inline-comment gate."\n', encoding="utf-8")
        for rel in ROSTER_FILES:
            p = pinl / rel
            p.write_text(p.read_text(encoding="utf-8") +
                         "\ntrue  # a commented step: python3 -I -B tools/g_inline.py --check\n",
                         encoding="utf-8")
        if run_quiet(pinl, check=True) != 2:
            failures.append("a manifest script named only in a roster inline comment must not enumerate "
                            "(F-148 strip); expected exit 2 (a linkage claim with no roster step)")
        # (q) Published preview hooks with a matching declaration: listed at the top level and per rule,
        #     and a rule named only by a preview keeps its prose-only status.
        qtree = _add_previews(_build(tmp / "previews"))
        if run_quiet(qtree, check=False) != 0:
            failures.append("preview tree: generation expected exit 0")
        else:
            qobj = json.loads((qtree / LEDGER_REL).read_text(encoding="utf-8"))
            want = [{"id": "p-one", "file": ".preview/p-one.py", "rules": ["ruledd"]},
                    {"id": "p-two", "file": ".preview/p-two.py", "rules": []}]
            if qobj.get("previews") != want:
                failures.append("preview tree: top-level previews {!r}, expected {!r}".format(
                    qobj.get("previews"), want))
            qby = {e["corpus-id"]: e for e in qobj.get("rules", [])}
            if qby.get("ruledd", {}).get("previews") != ["p-one"]:
                failures.append("preview tree: rule dd must list the preview that names it")
            if qby.get("ruledd", {}).get("status") != "prose-only":
                failures.append("preview tree: a preview must never change a status (rule dd prose-only)")
            if qby.get("ruleaa", {}).get("previews") != []:
                failures.append("preview tree: a rule no preview names must list none")

        # (r) A published preview hook with no declaration entry fails closed.
        rtree = _add_previews(_build(tmp / "preview-undeclared"))
        (rtree / ".preview" / "p-three.py").write_text(_PREVIEW_TWO, encoding="utf-8")
        if run_quiet(rtree, check=False) != 2:
            failures.append("a published preview hook with no declaration entry expected exit 2")
        # (s) A declaration entry naming no published hook fails closed.
        stree = _add_previews(_build(tmp / "preview-unpublished"))
        (stree / ".preview" / "p-two.py").unlink()
        if run_quiet(stree, check=False) != 2:
            failures.append("a declaration entry with no published hook expected exit 2")
        # (t) A declared rule the hook's own docstring does not name fails closed.
        ttree = _add_previews(_build(tmp / "preview-unnamed-rule"))
        replace_in(ttree / PREVIEW_DECL_REL, 'id = "p-two"\nrules = []', 'id = "p-two"\nrules = ["rulecc"]')
        if run_quiet(ttree, check=False) != 2:
            failures.append("a declared rule the preview's docstring does not name expected exit 2")
        # (u) A declared unknown corpus-id fails closed.
        utree = _add_previews(_build(tmp / "preview-unknown-cid"))
        replace_in(utree / PREVIEW_DECL_REL, 'rules = ["ruledd"]', 'rules = ["nosuch9"]')
        if run_quiet(utree, check=False) != 2:
            failures.append("a declared unknown corpus-id expected exit 2")
        # (v) Published preview hooks with the declaration absent fail closed.
        vtree = _add_previews(_build(tmp / "preview-no-decl"))
        (vtree / PREVIEW_DECL_REL).unlink()
        if run_quiet(vtree, check=False) != 2:
            failures.append("published preview hooks with no declaration expected exit 2")
        # (w) A declared file that lists no rules is validated all the same.
        wcases = (
            ("not UTF-8", lambda f: f.write_bytes(b"\xff\n")),
            ("a directory", lambda f: (f.unlink(), f.mkdir())),
            ("a symlink", lambda f: (f.unlink(), f.with_name("target.txt").write_text(
                '"""A valid hook body reached through a link."""\n', encoding="utf-8"),
                f.symlink_to("target.txt"))),
            ("unparseable", lambda f: f.write_text("def (:\n", encoding="utf-8")),
            ("without a module docstring", lambda f: f.write_text("X = 1\n", encoding="utf-8")),
        )
        for wi, (wname, wmutate) in enumerate(wcases):
            wtree = _add_previews(_build(tmp / "preview-empty-bad-{}".format(wi)))
            wmutate(wtree / ".preview" / "p-two.py")
            got = run_caught(wtree, check=False)
            if got != 2:
                failures.append("a rule-less declared preview file {} expected exit 2, got {!r}".format(
                    wname, got))
        wtree = _add_previews(_build(tmp / "preview-empty-unreadable"))
        wfile = wtree / ".preview" / "p-two.py"
        os.chmod(wfile, 0)
        try:
            if os.access(wfile, os.R_OK):
                skipped.append("w unreadable-preview")
            else:
                got = run_caught(wtree, check=False)
                if got != 2:
                    failures.append("an unreadable rule-less preview file expected exit 2, got "
                                    "{!r}".format(got))
        finally:
            os.chmod(wfile, 0o644)
        # (x) A linked preview file that does not parse returns exit 2 rather than raising SyntaxError.
        xtree = _add_previews(_build(tmp / "preview-linked-syntax"))
        (xtree / ".preview" / "p-one.py").write_text("def (:\n", encoding="utf-8")
        got = run_caught(xtree, check=True)
        if got != 2:
            failures.append("a linked preview file that does not parse expected exit 2, got {!r}".format(got))
        # (y) The slug boundary: any word character or hyphen beside the slug means it is not named.
        for yi, (ydoc, ywant) in enumerate((
                ("Xselftest-rule-ddX", 2), ("xselftest-rule-ddx", 2), ("_selftest-rule-dd_", 2),
                ("9selftest-rule-dd", 2), ("selftest-rule-dd-x", 2), ("-selftest-rule-dd", 2),
                ("selftest-rule-dd\u00e9", 2), ("Motivated by (SELFTEST-RULE-DD).", 0))):
            ytree = _add_previews(_build(tmp / "preview-boundary-{}".format(yi)))
            (ytree / ".preview" / "p-one.py").write_text('"""{}"""\n'.format(ydoc), encoding="utf-8")
            got = run_caught(ytree, check=False)
            if got != ywant:
                failures.append("a p-one docstring {!r} expected exit {}, got {!r}".format(ydoc, ywant, got))
        # (z) The reverse direction: a docstring naming a corpus slug its entry does not list fails closed,
        #     so an entry cannot hide a rule behind rules = [] (same naming predicate as (y)).
        for zi, (zdoc, zwant) in enumerate((
                ("Motivated by selftest-rule-cc.", 2), ("Motivated by Selftest-Rule-CC.", 2),
                ("Motivated by selftest-rule-ccx.", 0), ("Motivated by selftest_rule_cc.", 0))):
            ztree = _add_previews(_build(tmp / "preview-unlisted-{}".format(zi)))
            (ztree / ".preview" / "p-two.py").write_text('"""{}"""\n'.format(zdoc), encoding="utf-8")
            got = run_caught(ztree, check=False)
            if got != zwant:
                failures.append("a rule-less p-two docstring {!r} expected exit {}, got {!r}".format(
                    zdoc, zwant, got))
    finally:
        if unread_manifest is not None:
            os.chmod(unread_manifest, 0o644)  # restore even on an unexpected early exit
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    note = ("" if not skipped else
            " NOTE: skipped {} case(s) the runner cannot exercise (chmod-0 still readable): {}"
            .format(len(skipped), ", ".join(skipped)))
    print("SELF-TEST PASS: a conformant tree generates and regenerates drift-clean with every rule's "
          "status derived correctly (a hook+gate rule reads gate-linked yet lists both controls) and the "
          "boundary string present; a mutated ledger fails --check (exit 1); and an unknown corpus-id, an "
          "empty gate residue, a gate class outside {a, c}, a hook at class d, a roster script with no "
          "manifest entry, a manifest gate running in no roster, a missing gate script, an unreadable "
          "gates manifest, a duplicate gate id, a duplicate gate script, an absent gates manifest, an "
          "absent roster file, a roster with zero scripts, an unknown corpus-id in the hooks manifest, an "
          "unknown gates-manifest top-level key, an unknown [[gate]] entry key, and a manifest script "
          "named only inside a roster inline comment all fail closed (exit 2); preview hooks are listed "
          "at the top level and per rule without changing a status, and an undeclared or unpublished "
          "preview, a declared rule the hook's docstring does not name, a docstring naming a rule its entry "
          "does not list, a declared preview file that is unreadable, not UTF-8, not a regular file, "
          "unparseable or without a docstring (rules listed or not), an unknown declared corpus-id, and an "
          "absent declaration beside published previews all fail closed (exit 2)" + note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
