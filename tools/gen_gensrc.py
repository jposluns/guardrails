#!/usr/bin/env python3
"""Generate the .aiqt/gensrc.json registry of generated outputs from each generator's own declaration.

Every tools/gen_*.py declares a module-level GENSRC_OUTPUTS constant naming what it generates (its
targets, their kind, the sources it derives them from, and how to regenerate them). This tool discovers
those generators by STATICALLY PARSING each tools/gen_*.py and evaluating only its GENSRC_OUTPUTS
literal (ast.parse + ast.literal_eval), never importing or executing a generator, so no import-time
side effect (a SystemExit, a dynamic __getattr__, a broken import) can influence discovery. It requires
the declaration, validates and unions the entries, and renders the single registry .aiqt/gensrc.json so
the pack has one machine-readable inventory of every generated artefact.

A generator's `sources` are the CONTENT-BEARING inputs the output DERIVES FROM: change a source and the
output bytes can change. It is deliberately NOT every path the generator reads. Validation-only reads,
where a generator reads a file to CHECK its declaration but never lets it affect the output bytes (for
example gen_rules reading .aiqt/standards/ to validate MAP_KEYS, or gen_hooks reading the corpus to
cross-check ids), are EXCLUDED, because "adding a mapping key never affects a rule's derived path"
(gen_rules.py). The registry records the derivation, not the full read set.

Fail-closed by construction: the required GENSRC_OUTPUTS declaration is the STRUCTURAL staleness guard
(a new generator cannot land without declaring its outputs). A generator with no module-level
GENSRC_OUTPUTS assignment, more than one, a non-literal right-hand side (literal_eval cannot evaluate
it), or an empty literal exits 2 naming the file, never a silent partial registry. The tools dir is
enumerated with os.scandir, which RAISES on an unreadable dir (a glob would silently yield nothing).
Every entry is validated (a canonical repo-relative target with no backslash, "./" prefix, "..", or
redundant "/"; a known kind; a non-empty tuple of existing repo-relative sources; a regenerate command;
and a trailing "/" that appears only on a tree target, never on a file or block), and targets are
deduplicated on their SLASH-STRIPPED canonical body so a file "X" and a tree "X/" (and "X" vs "./X")
all collide. The entries are sorted by target so the render is deterministic. The registry
lists its OWN output too, so .aiqt/gensrc.json appears in the inventory it produces. A source's
EXISTENCE is checked (the registry declares paths); a source's READABILITY is the generator's gen-time
concern, not the registry's, so no readability probing is done here. An unreadable input still fails
closed (exit 2) exactly like the sibling generators, never a silent clean.

  gen_gensrc.py            regenerate .aiqt/gensrc.json
  gen_gensrc.py --check    fail (exit 1) on drift; exit 2 on a bad declaration or a read/write error
  gen_gensrc.py --self-test  build synthetic trees and assert the generator's own fail-closed invariants
"""
import ast
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, reconcile  # noqa: E402

# The registry this tool renders, repo-root-relative. It is listed in its own output (below), so the
# inventory is complete: the registry names itself alongside every other generated artefact.
REGISTRY_REL = ".aiqt/gensrc.json"
# This tool's own output declaration, unioned in like every generator's GENSRC_OUTPUTS. The sources are
# the declarations themselves, which live across the tools/ tree.
OWN_OUTPUTS = (
    {"target": REGISTRY_REL, "kind": "file",
     "sources": ("tools/",), "regenerate": "python3 tools/gen_gensrc.py"},
)

KINDS = {"file", "tree", "block"}
ENTRY_KEYS = {"target", "kind", "sources", "regenerate"}
SELF = "gen_gensrc"  # this module's stem, excluded from discovery (it declares its own output above)


def _read_declaration(path, where):
    """Return the value of path's single module-level GENSRC_OUTPUTS literal, recovered by parsing the
    file to an AST and evaluating ONLY that literal with ast.literal_eval. The module is never imported
    or executed, so an import-time SystemExit, a dynamic __getattr__, or any other side effect cannot
    influence the result. Fail-closed: raises ValueError (naming `where`) when there is no single
    top-level literal Assign to GENSRC_OUTPUTS, when the name is bound any OTHER way anywhere in the
    module (a second/nested/conditional assignment, an augmented assignment, an annotation, or a del),
    when the right-hand side is non-literal, or when the literal is empty; the caller maps that to exit
    2. An unreadable file raises OSError, also mapped to exit 2."""
    source = path.read_text(encoding="utf-8")  # OSError -> caller's fail-closed try
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise ValueError("{}: cannot parse for its GENSRC_OUTPUTS declaration ({})".format(where, exc))
    # The ONLY valid declaration is a single top-level plain Assign to GENSRC_OUTPUTS.
    top_assigns = [node for node in tree.body
                   if isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "GENSRC_OUTPUTS" for t in node.targets)]
    the_assign = top_assigns[0] if len(top_assigns) == 1 else None
    # Walk the WHOLE tree and reject any OTHER binding of the name: an augmented assignment, an
    # annotation (with or without value), a del, or a second/nested/conditional plain assignment. The
    # net rule is exactly one top-level literal Assign and no other store/aug/del of GENSRC_OUTPUTS
    # anywhere, so no computed value can slip in past the single literal one.
    for node in ast.walk(tree):
        if isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "GENSRC_OUTPUTS":
                raise ValueError("{}: GENSRC_OUTPUTS must not be augmented (e.g. '+='); exactly one "
                                 "top-level literal assignment is allowed (fail-closed)".format(where))
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "GENSRC_OUTPUTS":
                raise ValueError("{}: GENSRC_OUTPUTS must not be annotated; exactly one top-level "
                                 "literal assignment is allowed (fail-closed)".format(where))
        elif isinstance(node, ast.Delete):
            if any(isinstance(t, ast.Name) and t.id == "GENSRC_OUTPUTS" for t in node.targets):
                raise ValueError("{}: GENSRC_OUTPUTS must not be deleted; exactly one top-level "
                                 "literal assignment is allowed (fail-closed)".format(where))
        elif isinstance(node, ast.Assign):
            if (any(isinstance(t, ast.Name) and t.id == "GENSRC_OUTPUTS" for t in node.targets)
                    and node is not the_assign):
                raise ValueError("{}: GENSRC_OUTPUTS is assigned more than once, conditionally, or not "
                                 "at module level; exactly one top-level literal assignment is allowed "
                                 "(fail-closed)".format(where))
    if the_assign is None:
        raise ValueError("{} does not declare GENSRC_OUTPUTS; a generator cannot ship without declaring "
                         "its outputs (fail-closed)".format(where))
    try:
        decl = ast.literal_eval(the_assign.value)  # never exec/import: a computed RHS raises here
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError) as exc:
        raise ValueError("{}: GENSRC_OUTPUTS must be a literal (a tuple/list of dict literals), not a "
                         "computed expression ({})".format(where, exc))
    if not isinstance(decl, (tuple, list)):
        raise ValueError("{}: GENSRC_OUTPUTS must be a tuple/list of entries".format(where))
    if not decl:
        raise ValueError("{}: GENSRC_OUTPUTS is empty; a generator must declare at least one output "
                         "(fail-closed)".format(where))
    return decl


def discover_declarations(tools_dir):
    """Statically read every tools/gen_*.py in tools_dir except this tool, in sorted stem order, and
    return a list of (stem, GENSRC_OUTPUTS-value). Each declaration is recovered by parsing, never by
    importing (see _read_declaration), so the import-execution surface is removed entirely. Fail-closed:
    a bad or missing declaration raises ValueError naming the file (the structural staleness guard),
    discovering NO generator at all raises ValueError (a registry-only repo is malformed, never a
    silent self-only registry), and os.scandir RAISES on an unreadable tools_dir instead of silently
    yielding nothing as a glob would; the caller maps all of these, and any OSError reading a file, to
    exit 2."""
    tools_dir = Path(tools_dir)
    stems = []
    with os.scandir(tools_dir) as it:  # OSError on an unreadable/absent dir -> caller's fail-closed try
        for entry in it:
            name = entry.name
            if name.startswith("gen_") and name.endswith(".py") and name[:-3] != SELF:
                stems.append(name[:-3])
    stems.sort()
    if not stems:  # a repo carrying the registry tool but no generators is malformed, never self-only
        raise ValueError("no generators found under {}: expected at least one tools/gen_*.py besides "
                         "{}.py (fail-closed)".format(tools_dir, SELF))
    declarations = []
    for stem in stems:
        decl = _read_declaration(tools_dir / (stem + ".py"), "{}.py".format(stem))
        declarations.append((stem, decl))
    return declarations


def _canonical_target(target, kind, where):
    """Return the canonical POSIX-relative spelling of `target`, raising ValueError (naming `where`) if
    it is not already in that clean form. Rejects a backslash (a Windows separator), an absolute path, a
    '.' or '..' segment, a './' prefix, and redundant separators ('//'). A trailing '/' is allowed ONLY
    on a tree target (it is the tree marker); on a file or block target it is rejected. Callers
    deduplicate on the SLASH-STRIPPED canonical body, so a file 'X' and a tree 'X/', and 'X' and './X',
    all collide."""
    if "\\" in target:
        raise ValueError("{}: target {!r} must use POSIX '/' separators, not a backslash"
                         .format(where, target))
    if target.startswith("/"):
        raise ValueError("{}: target {!r} must be repo-relative, not absolute".format(where, target))
    has_trailing = target.endswith("/")
    if has_trailing and kind != "tree":
        raise ValueError("{}: a trailing '/' marks a tree target; {!r} (kind {!r}) must not end with "
                         "'/'".format(where, target, kind))
    body = target[:-1] if has_trailing else target  # a lone trailing '/' is the tree marker
    if any(seg in ("", ".", "..") for seg in body.split("/")):
        raise ValueError("{}: target {!r} must be a clean POSIX-relative path (no './' prefix, '..', "
                         "or redundant '/')".format(where, target))
    return target


def _validate_entry(raw, where, root, seen):
    """Validate one declaration entry against root and return the canonical dict {target, kind, sources
    (list), regenerate}. Raises ValueError on any malformed field (including a non-str key or kind, so a
    malformed literal fails closed rather than raising a bare TypeError), an unknown/absent source path,
    or a duplicate target (compared on its slash-stripped canonical body); the caller maps that to exit
    2. `where` names the declaring file for the message."""
    if not isinstance(raw, dict):
        raise ValueError("{}: every GENSRC_OUTPUTS entry must be a dict".format(where))
    # Type-check the keys BEFORE any set membership or .format: a non-str key would make sorted()/join
    # below raise a bare TypeError instead of the fail-closed ValueError the caller maps to exit 2.
    for key in raw:
        if not isinstance(key, str):
            raise ValueError("{}: entry keys must be strings, got {!r}".format(where, key))
    extra = set(raw) - ENTRY_KEYS
    if extra:
        raise ValueError("{}: entry has unknown key(s): {}".format(where, ", ".join(sorted(extra))))
    missing = ENTRY_KEYS - set(raw)
    if missing:
        raise ValueError("{}: entry missing key(s): {}".format(where, ", ".join(sorted(missing))))

    # Type-check kind BEFORE the set-membership test: an unhashable kind (a list/dict) would make
    # `kind not in KINDS` raise a bare TypeError rather than the fail-closed ValueError -> exit 2.
    kind = raw["kind"]
    if not isinstance(kind, str):
        raise ValueError("{}: kind must be a string, got {!r}".format(where, kind))
    if kind not in KINDS:
        raise ValueError("{}: kind {!r} must be one of {}".format(where, kind, "/".join(sorted(KINDS))))

    target = raw["target"]
    if not isinstance(target, str) or not target:
        raise ValueError("{}: target must be a non-empty string".format(where))
    canon = _canonical_target(target, kind, where)  # rejects a trailing '/' unless kind == "tree"
    if kind == "tree" and not canon.endswith("/"):
        raise ValueError("{}: a tree target ({!r}) must end with '/'".format(where, target))

    # `sources` are the CONTENT-BEARING inputs the output DERIVES from, not every path the generator
    # reads; validation-only reads are excluded by convention (see the module docstring). Here we check
    # only spelling sanity (no backslash, no '..', repo-relative) and EXISTENCE; a source's readability
    # is the generator's gen-time concern, not the registry's, so no readability probing is done.
    sources = raw["sources"]
    if not isinstance(sources, (tuple, list)) or not sources:
        raise ValueError("{}: sources must be a non-empty tuple of paths".format(where))
    for src in sources:
        if not isinstance(src, str) or not src:
            raise ValueError("{}: every source must be a non-empty string".format(where))
        if "\\" in src:
            raise ValueError("{}: source {!r} must use POSIX '/' separators, not a backslash"
                             .format(where, src))
        if src.startswith("/") or ".." in Path(src).parts:
            raise ValueError("{}: source {!r} must be repo-relative with no '..'".format(where, src))
        if not (root / src).exists():  # existence probe; an unreadable parent raises OSError -> exit 2
            raise ValueError("{}: source {!r} does not exist under {}".format(where, src, root))

    regenerate = raw["regenerate"]
    if not isinstance(regenerate, str) or not regenerate:
        raise ValueError("{}: regenerate must be a non-empty string".format(where))

    # Dedup on the SLASH-STRIPPED canonical body so a file 'X' and a tree 'X/' (and 'X' vs './X')
    # collide: one filesystem path is generated by exactly one entry, whatever its kind.
    dedup_key = canon[:-1] if canon.endswith("/") else canon
    if dedup_key in seen:
        raise ValueError("{}: duplicate target {!r} (a target is generated by exactly one entry)"
                         .format(where, target))
    seen.add(dedup_key)
    return {"target": canon, "kind": kind, "sources": list(sources), "regenerate": regenerate}


def collect_entries(declarations, root):
    """Union and validate the declarations of every discovered generator plus this tool's own output,
    then return the entries sorted by target for a deterministic render. Raises ValueError on any
    malformed entry or duplicate target."""
    entries = []
    seen = set()
    for stem, decl in declarations:
        for raw in decl:
            entries.append(_validate_entry(raw, "{}.py".format(stem), root, seen))
    for raw in OWN_OUTPUTS:
        entries.append(_validate_entry(raw, "gen_gensrc.py", root, seen))
    entries.sort(key=lambda e: e["target"])
    return entries


def build_registry(root):
    """The full .aiqt/gensrc.json text: {"version": 1, "generated": [...sorted entries...]} rendered
    with sort_keys for determinism, plus a trailing newline. Raises ValueError/OSError on a bad
    declaration or an unreadable input; the caller maps those to exit 2."""
    declarations = discover_declarations(root / "tools")
    entries = collect_entries(declarations, root)
    obj = {"version": 1, "generated": entries}
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def run(root, check):
    """Render the registry into root/.aiqt/gensrc.json, or (check mode) report drift. Fail-closed
    (exit 2) on a bad declaration or any unreadable input, mirroring the sibling generators."""
    try:
        text = build_registry(root)
    except (ValueError, OSError) as exc:
        print("error: cannot build {} ({}); fail-closed".format(REGISTRY_REL, exc), file=sys.stderr)
        return 2
    if reconcile(root / REGISTRY_REL, text, check):  # reconcile raises SystemExit(2) on an OSError
        print("drift: {} is out of date; run tools/gen_gensrc.py".format(REGISTRY_REL), file=sys.stderr)
        return 1
    if not check:
        print("wrote {} ({} generated-output entries)".format(REGISTRY_REL, text.count('"target":')))
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test_main()
    return run(repo_root(), "--check" in args)


# --- self-test ----------------------------------------------------------------------------------------
# Proves the generator's own fail-closed invariants against synthetic trees (the gen_hooks.py pattern),
# so gen_gensrc never becomes an ungated generator:
#   (a) a conformant tree generates and re-checks drift-clean,
#   (b) a generator module missing GENSRC_OUTPUTS fails closed (exit 2), the structural staleness guard,
#   (c) a mutated .aiqt/gensrc.json is caught by --check (exit 1),
#   (d) an EMPTY GENSRC_OUTPUTS=() fails closed (exit 2), the old empty-declaration bypass,
#   (e) a NON-LITERAL RHS beside a module-level raise SystemExit(0) fails closed (exit 2) and is NEVER
#       executed, proving discovery parses statically and does not import,
#   (f) an unreadable tools/ dir fails closed (exit 2), os.scandir raising rather than a silent glob,
#   (g) two entries whose canonical bodies are equal (a file 'X' and a tree 'X/') fail closed (exit 2),
#       the slash-stripped canonical-dedup collision,
#   (h) a malformed literal (a non-str kind such as []) fails closed with ValueError -> exit 2, never a
#       bare TypeError from a set-membership test.
# A raised SystemExit anywhere in a run() call is caught by run_quiet and recorded as a FAILURE, so the
# self-test can never itself exit early (green or otherwise) on an unexpected raise.

_FAKE_GOOD = '''# self-test generator that declares its output
GENSRC_OUTPUTS = (
    {"target": "OUT.md", "kind": "file",
     "sources": ("src.txt",), "regenerate": "python3 tools/gen_selftestgood.py"},
)
'''

_FAKE_BAD = '''# self-test generator that FORGOT to declare its output
PLACEHOLDER = 1
'''

_FAKE_EMPTY = '''# self-test generator whose declaration is empty (the old empty-() bypass)
GENSRC_OUTPUTS = ()
'''

# A non-literal RHS beside a module-level SystemExit(0). Under the old import-based discovery the
# SystemExit(0) would have run at import and masked the fault as a clean exit; static AST parsing never
# executes it, so the non-literal RHS is caught and the tree fails closed (exit 2).
_FAKE_NONLITERAL = '''# self-test generator with a computed declaration and an import-time SystemExit
raise SystemExit(0)
def _make():
    return ({"target": "OUT.md", "kind": "file",
             "sources": ("src.txt",), "regenerate": "x"},)
GENSRC_OUTPUTS = _make()
'''

# Two entries whose SLASH-STRIPPED canonical bodies are equal: a file 'OUT.md' and a tree 'OUT.md/'.
# Each is individually well formed, but they name the same filesystem path, so dedup collides them and
# the tree fails closed (exit 2). (The old case used 'OUT.md' vs './OUT.md', which was mislabeled: the
# './OUT.md' spelling is rejected as a non-canonical path before dedup is ever reached.)
_FAKE_CANON_DUP = '''# self-test generator with a true canonical-duplicate pair (file 'X' vs tree 'X/')
GENSRC_OUTPUTS = (
    {"target": "OUT.md", "kind": "file",
     "sources": ("src.txt",), "regenerate": "x"},
    {"target": "OUT.md/", "kind": "tree",
     "sources": ("src.txt",), "regenerate": "x"},
)
'''

# A malformed literal: kind is a list (a non-str, and unhashable) rather than a string. Without the
# type-check this would raise a bare TypeError from `kind not in KINDS`; with it, a fail-closed
# ValueError -> exit 2.
_FAKE_BADTYPE = '''# self-test generator whose kind is a non-str list literal
GENSRC_OUTPUTS = (
    {"target": "OUT.md", "kind": [],
     "sources": ("src.txt",), "regenerate": "x"},
)
'''


def _build_from(base, filename, text):
    """A synthetic repo whose only generator is `filename` carrying `text`, with the source it names and
    an empty .aiqt/ so reconcile can write the registry."""
    (base / "tools").mkdir(parents=True)
    (base / "tools" / filename).write_text(text, encoding="utf-8")
    (base / "src.txt").write_text("source\n", encoding="utf-8")
    (base / ".aiqt").mkdir()


def self_test_main():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr

    def run_quiet(root, check):
        # A raised SystemExit (e.g. reconcile's OSError path) is caught and returned as a non-int
        # sentinel so it registers as a FAILURE against any expected exit code, rather than aborting
        # the self-test or letting it exit early green.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                return run(root, check)
            except SystemExit as exc:
                return "raised SystemExit({!r})".format(exc.code)

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-gensrc-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    skipped = []
    unread_tools = None
    try:
        # (a) A conformant tree generates, then re-checks drift-clean, and the registry exists.
        good = tmp / "good"
        _build_from(good, "gen_selftestgood.py", _FAKE_GOOD)
        if run_quiet(good, check=False) != 0:
            failures.append("conformant tree: generation expected exit 0")
        if run_quiet(good, check=True) != 0:
            failures.append("conformant tree: regeneration expected drift-clean exit 0")
        registry = good / REGISTRY_REL
        if not registry.is_file():
            failures.append("conformant tree: expected {} to be written".format(REGISTRY_REL))

        # (b) A generator missing GENSRC_OUTPUTS fails closed (exit 2): the structural staleness guard.
        bad = tmp / "bad"
        _build_from(bad, "gen_selftestbad.py", _FAKE_BAD)
        if run_quiet(bad, check=True) != 2:
            failures.append("missing GENSRC_OUTPUTS expected exit 2 (fail-closed)")

        # (c) A mutated registry is caught by --check (exit 1).
        if registry.is_file():
            registry.write_text(registry.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            if run_quiet(good, check=True) != 1:
                failures.append("mutated {} expected exit 1 (drift)".format(REGISTRY_REL))

        # (d) An EMPTY GENSRC_OUTPUTS=() fails closed (exit 2): the old empty-declaration bypass.
        empty = tmp / "empty"
        _build_from(empty, "gen_selftestempty.py", _FAKE_EMPTY)
        if run_quiet(empty, check=True) != 2:
            failures.append("empty GENSRC_OUTPUTS=() expected exit 2 (fail-closed)")

        # (e) A non-literal RHS beside an import-time raise SystemExit(0) fails closed (exit 2) and is
        #     never executed: static AST parsing, no import. If discovery imported the module the
        #     SystemExit(0) would surface as exit 0 (or crash the run), not the fail-closed exit 2.
        nonlit = tmp / "nonlit"
        _build_from(nonlit, "gen_selftestnonlit.py", _FAKE_NONLITERAL)
        if run_quiet(nonlit, check=True) != 2:
            failures.append("non-literal GENSRC_OUTPUTS (with import-time SystemExit) expected exit 2 "
                            "(fail-closed, not executed)")

        # (f) An unreadable tools/ dir fails closed (exit 2): os.scandir raises where a glob would
        #     silently yield nothing. Skipped where the runner can still read a chmod-0 dir
        #     (root/DAC-bypass), observed via os.access, as gen_hooks does.
        unread = tmp / "unread"
        _build_from(unread, "gen_selftestgood.py", _FAKE_GOOD)
        unread_tools = unread / "tools"
        os.chmod(unread_tools, 0)
        if os.access(unread_tools, os.R_OK):
            skipped.append("f unreadable-tools-dir")
        elif run_quiet(unread, check=True) != 2:
            failures.append("unreadable tools/ dir expected exit 2 (fail-closed)")
        os.chmod(unread_tools, 0o755)  # restore so cleanup can remove it
        unread_tools = None

        # (g) A file 'X' and a tree 'X/' fail closed (exit 2): slash-stripped canonical-dedup collision.
        canondup = tmp / "canondup"
        _build_from(canondup, "gen_selftestcanondup.py", _FAKE_CANON_DUP)
        if run_quiet(canondup, check=True) != 2:
            failures.append("canonical-duplicate targets (file 'X', tree 'X/') expected exit 2 "
                            "(fail-closed)")

        # (h) A malformed literal (a non-str kind []) fails closed with ValueError -> exit 2, never a
        #     bare TypeError from the set-membership test.
        badtype = tmp / "badtype"
        _build_from(badtype, "gen_selftestbadtype.py", _FAKE_BADTYPE)
        if run_quiet(badtype, check=True) != 2:
            failures.append("malformed non-str kind ([]) expected exit 2 (fail-closed ValueError)")
    finally:
        if unread_tools is not None:
            os.chmod(unread_tools, 0o755)  # restore even on an unexpected early exit
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    note = ("" if not skipped else
            " NOTE: skipped {} case(s) the runner cannot exercise (chmod-0 still readable): {}"
            .format(len(skipped), ", ".join(skipped)))
    print("SELF-TEST PASS: a conformant tree generates and regenerates drift-clean; a mutated "
          ".aiqt/gensrc.json fails --check (exit 1); and a generator that omits GENSRC_OUTPUTS, an "
          "empty GENSRC_OUTPUTS=(), a non-literal declaration beside an import-time SystemExit "
          "(proving static parse, no import), an unreadable tools/ dir, canonical-duplicate targets "
          "(a file 'X' and a tree 'X/'), and a malformed non-str kind all fail closed (exit 2)" + note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
