#!/usr/bin/env python3
"""Render .aiqt/core/renderers.toml: the manifest-covered RENDERER/GENERATOR DECLARATION (VER-CORE 6.5,
R9-5). Offline, stdlib only, fail-closed.

For each declared adapter renderer (gen_agents, gen_adapters, gen_claude, gen_cursor, gen_rules,
gen_skill) this tool STATICALLY PARSES (ast, never import) the generator's module-level RENDERER_DECL
literal ({"renderer-id": <str>, "semantics-revision": <int>}) and its GENSRC_OUTPUTS targets (recovered
through gen_gensrc's own validated loader, the house import-reuse pattern), computes the ORDERED PACK-LOCAL
IMPORT CLOSURE (the entrypoint plus every tools/<module>.py it transitively imports, entrypoint first then
the rest bytewise), and computes a FRAMED generator-code digest over per-file records "<path>\\t<bytes>\\t
<sha256>\\n" in closure order, NEVER a raw concatenation (so a boundary-shifting edit stays detectable).
Any edit anywhere in a closure changes the framed digest, forcing a declaration diff at the Step-4 delta
gate; check_manifest.py recomputes the ARTIFACTS roster from these targets.

Fail-closed (GateError -> exit 2): a missing/malformed RENDERER_DECL or GENSRC_OUTPUTS; a pack-local
import that cannot be statically resolved (a relative import, or a wildcard `from <pack-local> import *`);
an unreadable closure member; or any other cannot-evaluate. DISCLOSED RESIDUAL (disclose-guard-residuals):
the closure is computed from statically-parsed Import/ImportFrom nodes only. A truly-dynamic pack-local
import (importlib.import_module, __import__, or globals() manipulation) is beyond static analysis; it is
not used by any declared generator today, and the delta gate's fail-closed-on-incomplete-closure leg is
the backstop, but this generator does not detect that class and discloses it here rather than implying it.

  gen_renderers.py             regenerate .aiqt/core/renderers.toml
  gen_renderers.py --check     exit 1 if the committed file differs from a fresh regeneration; write nothing
  gen_renderers.py --self-test build synthetic generators and assert the fail-closed invariants
  gen_renderers.py --root DIR  operate on DIR instead of the repo root (fixtures)

Exit convention (matches the repo's gates): 0 clean; 1 drift; 2 malformed/unreadable input or any
cannot-evaluate.
"""
import ast
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, reconcile  # noqa: E402
from gen_rules import SLUG_RE  # noqa: E402  the authoritative renderer-id slug syntax
import gen_gensrc  # noqa: E402  reuse its validated GENSRC_OUTPUTS loader, never a second parser

GENSRC_OUTPUTS = (
    {"target": ".aiqt/core/renderers.toml", "kind": "file",
     "sources": ("tools/",), "regenerate": "python3 tools/gen_renderers.py"},
)
RENDERERS_REL = ".aiqt/core/renderers.toml"
# The declared adapter renderers, in a fixed bytewise renderer-id order for a deterministic render.
RENDERERS = ("gen_agents", "gen_adapters", "gen_claude", "gen_cursor", "gen_rules", "gen_skill")


class GateError(Exception):
    """A fail-closed condition (malformed declaration, unresolvable import, unreadable input): exit 2."""


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _toml_str(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _read_renderer_decl(path, where):
    """Recover a generator's single module-level RENDERER_DECL literal by parsing to an AST and evaluating
    ONLY that literal (ast.literal_eval); the module is never imported. Fail-closed (GateError) when there
    is no single top-level literal assignment to RENDERER_DECL, when the right-hand side is non-literal, or
    when the shape is wrong ({'renderer-id': <non-empty str>, 'semantics-revision': <non-negative int>})."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GateError("{}: cannot read ({})".format(where, exc))
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise GateError("{}: cannot parse for RENDERER_DECL ({})".format(where, exc))
    assigns = [node for node in tree.body
               if isinstance(node, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "RENDERER_DECL" for t in node.targets)]
    if len(assigns) != 1:
        raise GateError("{}: expected exactly one top-level RENDERER_DECL assignment, found {}"
                        .format(where, len(assigns)))
    try:
        decl = ast.literal_eval(assigns[0].value)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError) as exc:
        raise GateError("{}: RENDERER_DECL must be a literal dict ({})".format(where, exc))
    if not isinstance(decl, dict) or set(decl) != {"renderer-id", "semantics-revision"}:
        raise GateError("{}: RENDERER_DECL keys must be exactly renderer-id/semantics-revision".format(where))
    rid = decl["renderer-id"]
    rev = decl["semantics-revision"]
    # renderer-id is the authoritative slug (this round's #3): reject a non-slug (e.g. "Bad ID") at the
    # source RENDERER_DECL, so a malformed id can never be rendered into a "fresh" renderers.toml.
    if not isinstance(rid, str) or not SLUG_RE.fullmatch(rid):
        raise GateError("{}: renderer-id {!r} is not a valid slug".format(where, rid))
    if not isinstance(rev, int) or isinstance(rev, bool) or rev < 0:
        raise GateError("{}: semantics-revision must be a non-negative integer".format(where))
    return rid, rev


def _local_imports(path, tools_dir, where):
    """The set of pack-local module stems (a tools/<stem>.py exists) directly imported by `path`. Parses
    Import/ImportFrom nodes only. Fail-closed (GateError) on a relative import (level > 0) or a wildcard
    `from <pack-local> import *`, both of which a static closure cannot resolve honestly."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise GateError("{}: cannot parse for its imports ({})".format(where, exc))
    stems = set()

    def _consider(root):
        if (tools_dir / (root + ".py")).is_file():
            stems.add(root)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _consider(alias.name.partition(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                raise GateError("{}: a relative import cannot be resolved into the pack-local closure "
                                "(fail-closed)".format(where))
            root = (node.module or "").partition(".")[0]
            is_wildcard = any(alias.name == "*" for alias in node.names)
            if is_wildcard and (tools_dir / (root + ".py")).is_file():
                raise GateError("{}: a wildcard import of the pack-local module {!r} cannot be resolved "
                                "into the closure (fail-closed)".format(where, root))
            if root:
                _consider(root)
    return stems


def compute_closure(entry_stem, tools_dir):
    """The ordered pack-local import closure of tools/<entry_stem>.py: the entrypoint first, then every
    transitively-imported tools/<module>.py bytewise by repo-relative path. Returns a list of repo-relative
    paths ('tools/<stem>.py'). Fail-closed on an unresolvable import or unreadable member."""
    entry_rel = "tools/{}.py".format(entry_stem)
    seen = {entry_stem}
    frontier = [entry_stem]
    while frontier:
        stem = frontier.pop()
        deps = _local_imports(tools_dir / (stem + ".py"), tools_dir, "tools/{}.py".format(stem))
        for dep in sorted(deps):
            if dep not in seen:
                seen.add(dep)
                frontier.append(dep)
    rest = sorted("tools/{}.py".format(s) for s in seen if s != entry_stem)
    return [entry_rel] + rest


def framed_code_digest(closure, root):
    """SHA-256 over the concatenated framed records '<path>\\t<bytes>\\t<sha256>\\n' per closure file in
    order (never a raw byte concatenation), so a per-file change or a cross-boundary byte move both change
    the digest. Fail-closed on an unreadable member."""
    records = []
    for rel in closure:
        try:
            data = (root / rel).read_bytes()
        except OSError as exc:
            raise GateError("cannot read closure member {} ({})".format(rel, exc))
        records.append("{}\t{}\t{}\n".format(rel, len(data), _sha256(data)))
    return _sha256("".join(records).encode("utf-8"))


def build_rows(root):
    """One row per declared renderer: renderer-id, entrypoint, semantics-revision, targets (from the
    generator's GENSRC_OUTPUTS, verbatim including a tree's trailing '/'), the ordered import closure, and
    the framed code-digest. Fail-closed on any malformed declaration or unresolvable import."""
    tools_dir = root / "tools"
    rows = []
    seen_ids = set()
    for stem in RENDERERS:
        entry = tools_dir / (stem + ".py")
        where = "tools/{}.py".format(stem)
        if not entry.is_file():
            raise GateError("{}: declared renderer generator is absent".format(where))
        rid, rev = _read_renderer_decl(entry, where)
        if rid in seen_ids:
            raise GateError("duplicate renderer-id {!r}".format(rid))
        seen_ids.add(rid)
        try:
            decl = gen_gensrc._read_declaration(entry, where)
        except (ValueError, OSError) as exc:
            raise GateError("{}: cannot read GENSRC_OUTPUTS ({})".format(where, exc))
        targets = [e["target"] for e in decl]
        closure = compute_closure(stem, tools_dir)
        rows.append({"renderer-id": rid, "entrypoint": "tools/{}.py".format(stem),
                     "semantics-revision": rev, "targets": targets, "closure": closure,
                     "code-digest": framed_code_digest(closure, root)})
    return rows


def render(rows):
    lines = ["# .aiqt/core/renderers.toml: the RENDERER/GENERATOR DECLARATION (VER-CORE 6.5, R9-5).",
             "# GENERATED by tools/gen_renderers.py from each adapter generator's RENDERER_DECL, its",
             "# GENSRC_OUTPUTS targets, and its ordered pack-local import closure; do not hand-edit.",
             "# code-digest is SHA-256 over framed '<path>\\t<bytes>\\t<sha256>' records in closure order,",
             "# so any edit anywhere in a generator's closure forces a declaration diff (6.5).",
             "",
             "format-version = 1",
             ""]
    for row in rows:
        lines.append("[[renderer]]")
        lines.append("renderer-id = {}".format(_toml_str(row["renderer-id"])))
        lines.append("entrypoint = {}".format(_toml_str(row["entrypoint"])))
        lines.append("semantics-revision = {}".format(row["semantics-revision"]))
        lines.append("targets = [{}]".format(", ".join(_toml_str(t) for t in row["targets"])))
        lines.append("closure = [{}]".format(", ".join(_toml_str(c) for c in row["closure"])))
        lines.append("code-digest = {}".format(_toml_str(row["code-digest"])))
        lines.append("")
    return "\n".join(lines)


def run(root, check):
    try:
        text = render(build_rows(root))
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    if reconcile(root / RENDERERS_REL, text, check):
        print("drift: {} is out of date; run tools/gen_renderers.py".format(RENDERERS_REL),
              file=sys.stderr)
        return 1
    if not check:
        print("wrote {} ({} renderers)".format(RENDERERS_REL, text.count("[[renderer]]")))
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test_main()
    root = repo_root()
    if "--root" in args:
        i = args.index("--root")
        if i + 1 >= len(args):
            print("usage: gen_renderers.py [--check] [--root DIR] | --self-test", file=sys.stderr)
            return 2
        root = Path(args[i + 1]).resolve()
    return run(root, "--check" in args)


# --- self-test ----------------------------------------------------------------------------------------
# Synthetic generators in a tempdir prove this generator's own fail-closed invariants:
#   (a) a conformant set generates then re-checks drift-clean, and two runs are byte-identical (determinism);
#   (b) a helper edit inside a closure changes the framed code-digest (closure completeness);
#   (c) a mutated renderers.toml is caught by --check (exit 1);
#   (d) a wildcard import of a pack-local module fails closed (exit 2), an unresolvable-closure case;
#   (e) a missing/malformed RENDERER_DECL fails closed (exit 2).

_HELPER = "VALUE = 1\n"

_ENTRY_GOOD = ('import sys\n'
               'from pathlib import Path\n'
               'from selfhelper import VALUE\n'
               'RENDERER_DECL = {"renderer-id": "alpha", "semantics-revision": 1}\n'
               'GENSRC_OUTPUTS = (\n'
               '    {"target": "OUT.md", "kind": "file",\n'
               '     "sources": ("src.txt",), "regenerate": "python3 tools/gen_alpha.py"},\n'
               ')\n')

_ENTRY_NODECL = ('from selfhelper import VALUE\n'
                 'GENSRC_OUTPUTS = (\n'
                 '    {"target": "OUT.md", "kind": "file",\n'
                 '     "sources": ("src.txt",), "regenerate": "python3 tools/gen_alpha.py"},\n'
                 ')\n')

_ENTRY_WILDCARD = ('from selfhelper import *\n'
                   'RENDERER_DECL = {"renderer-id": "alpha", "semantics-revision": 1}\n'
                   'GENSRC_OUTPUTS = (\n'
                   '    {"target": "OUT.md", "kind": "file",\n'
                   '     "sources": ("src.txt",), "regenerate": "python3 tools/gen_alpha.py"},\n'
                   ')\n')

_ENTRY_BADID = ('from selfhelper import VALUE\n'
                'RENDERER_DECL = {"renderer-id": "Bad ID", "semantics-revision": 1}\n'
                'GENSRC_OUTPUTS = (\n'
                '    {"target": "OUT.md", "kind": "file",\n'
                '     "sources": ("src.txt",), "regenerate": "python3 tools/gen_alpha.py"},\n'
                ')\n')


def _fixture(base, entry_body, helper_body=_HELPER):
    tools = base / "tools"
    tools.mkdir(parents=True)
    (base / ".aiqt" / "core").mkdir(parents=True)
    (base / "src.txt").write_text("source\n", encoding="utf-8")
    (tools / "gen_alpha.py").write_text(entry_body, encoding="utf-8")
    (tools / "selfhelper.py").write_text(helper_body, encoding="utf-8")


def self_test_main():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr

    global RENDERERS
    saved = RENDERERS
    RENDERERS = ("gen_alpha",)

    def run_quiet(root, check):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                return run(root, check)
            except SystemExit as exc:
                return "raised SystemExit({!r})".format(exc.code)

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-renderers-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    try:
        # (a) conformant + determinism.
        good = tmp / "good"
        _fixture(good, _ENTRY_GOOD)
        if run_quiet(good, check=False) != 0:
            failures.append("conformant: generation expected exit 0")
        if run_quiet(good, check=True) != 0:
            failures.append("conformant: regeneration expected drift-clean exit 0")
        first = (good / RENDERERS_REL).read_text(encoding="utf-8")
        run_quiet(good, check=False)
        if (good / RENDERERS_REL).read_text(encoding="utf-8") != first:
            failures.append("determinism: two runs are not byte-identical")

        # (b) a helper edit inside the closure changes the framed code-digest.
        edited = tmp / "edited"
        _fixture(edited, _ENTRY_GOOD, helper_body="VALUE = 2\n")
        run_quiet(edited, check=False)
        if (edited / RENDERERS_REL).read_text(encoding="utf-8") == first:
            failures.append("closure completeness: a helper edit did not change the code-digest")

        # (c) a mutated renderers.toml is caught by --check.
        if run_quiet(good, check=False) == 0:
            target = good / RENDERERS_REL
            target.write_text(target.read_text(encoding="utf-8") + "\n# tamper\n", encoding="utf-8")
            if run_quiet(good, check=True) != 1:
                failures.append("mutated renderers.toml expected exit 1 (drift)")

        # (d) a wildcard import of a pack-local module fails closed (exit 2).
        wild = tmp / "wild"
        _fixture(wild, _ENTRY_WILDCARD)
        if run_quiet(wild, check=False) != 2:
            failures.append("wildcard pack-local import expected exit 2 (fail-closed)")

        # (e) a missing RENDERER_DECL fails closed (exit 2).
        nodecl = tmp / "nodecl"
        _fixture(nodecl, _ENTRY_NODECL)
        if run_quiet(nodecl, check=False) != 2:
            failures.append("missing RENDERER_DECL expected exit 2 (fail-closed)")

        # (f) a non-slug renderer-id in RENDERER_DECL ("Bad ID") fails closed (exit 2), so a malformed id
        # can never be rendered into a fresh renderers.toml (this round's #3).
        badid = tmp / "badid"
        _fixture(badid, _ENTRY_BADID)
        if run_quiet(badid, check=False) != 2:
            failures.append("non-slug renderer-id 'Bad ID' expected exit 2 (fail-closed, this round's #3)")
    finally:
        RENDERERS = saved
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: a conformant renderer set generates and regenerates drift-clean and is "
          "deterministic; a helper edit inside a closure changes the framed code-digest; a mutated "
          "renderers.toml fails --check (exit 1); and a wildcard pack-local import, a missing "
          "RENDERER_DECL, and a non-slug renderer-id each fail closed (exit 2)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
