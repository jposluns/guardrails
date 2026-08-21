#!/usr/bin/env python3
"""Generate the .aiqt/gensrc.json registry of generated outputs from each generator's own declaration.

Every tools/gen_*.py declares a module-level GENSRC_OUTPUTS constant naming what it generates (its
targets, their kind, the sources it derives them from, and how to regenerate them). This tool discovers
those generators, requires the declaration, validates and unions the entries, and renders the single
registry .aiqt/gensrc.json so the pack has one machine-readable inventory of every generated artefact.

Fail-closed by construction: a generator that ships without a GENSRC_OUTPUTS declaration is the
STRUCTURAL staleness guard (a new generator cannot land without declaring its outputs), so a missing
declaration exits 2 naming the file, never a silent partial registry. Every entry is validated (a
non-empty repo-relative target with no "..", a known kind, a non-empty tuple of existing repo-relative
sources, a regenerate command, and a trailing "/" on a tree target), duplicate targets are rejected,
and the entries are sorted by target so the render is deterministic. The registry lists its OWN output
too, so .aiqt/gensrc.json appears in the inventory it produces. An unreadable source or target fails
closed (exit 2) exactly like the sibling generators, never a silent clean.

  gen_gensrc.py            regenerate .aiqt/gensrc.json
  gen_gensrc.py --check    fail (exit 1) on drift; exit 2 on a missing declaration or a read/write error
  gen_gensrc.py --self-test  build synthetic trees and assert the generator's own fail-closed invariants
"""
import importlib
import json
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


def discover_modules(tools_dir):
    """Import every tools/gen_*.py in tools_dir except this tool, in sorted stem order, and return a
    list of (stem, module). Fail-closed: a module that does not declare GENSRC_OUTPUTS raises ValueError
    naming the file (the structural staleness guard), and an unreadable or broken module raises through
    the import (OSError/ImportError); the caller maps all of these to exit 2."""
    tools_dir = Path(tools_dir)
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    importlib.invalidate_caches()  # so a freshly written module (the self-test trees) is discoverable
    stems = sorted(p.stem for p in tools_dir.glob("gen_*.py") if p.stem != SELF)
    modules = []
    for stem in stems:
        mod = importlib.import_module(stem)  # ImportError/OSError -> caller's fail-closed try
        if not hasattr(mod, "GENSRC_OUTPUTS"):
            raise ValueError("generator {}.py does not declare GENSRC_OUTPUTS; a generator cannot ship "
                             "without declaring its outputs (fail-closed)".format(stem))
        modules.append((stem, mod))
    return modules


def _validate_entry(raw, where, root, seen):
    """Validate one declaration entry against root and return the canonical dict {target, kind, sources
    (list), regenerate}. Raises ValueError on any malformed field, an unknown/absent source path, or a
    duplicate target; the caller maps that to exit 2. `where` names the declaring file for the message."""
    if not isinstance(raw, dict):
        raise ValueError("{}: every GENSRC_OUTPUTS entry must be a dict".format(where))
    extra = set(raw) - ENTRY_KEYS
    if extra:
        raise ValueError("{}: entry has unknown key(s): {}".format(where, ", ".join(sorted(extra))))
    missing = ENTRY_KEYS - set(raw)
    if missing:
        raise ValueError("{}: entry missing key(s): {}".format(where, ", ".join(sorted(missing))))

    target = raw["target"]
    if not isinstance(target, str) or not target:
        raise ValueError("{}: target must be a non-empty string".format(where))
    if target.startswith("/") or ".." in Path(target).parts:
        raise ValueError("{}: target {!r} must be repo-relative with no '..'".format(where, target))

    kind = raw["kind"]
    if kind not in KINDS:
        raise ValueError("{}: kind {!r} must be one of {}".format(where, kind, "/".join(sorted(KINDS))))
    if kind == "tree" and not target.endswith("/"):
        raise ValueError("{}: a tree target ({!r}) must end with '/'".format(where, target))

    sources = raw["sources"]
    if not isinstance(sources, (tuple, list)) or not sources:
        raise ValueError("{}: sources must be a non-empty tuple of paths".format(where))
    for src in sources:
        if not isinstance(src, str) or not src:
            raise ValueError("{}: every source must be a non-empty string".format(where))
        if src.startswith("/") or ".." in Path(src).parts:
            raise ValueError("{}: source {!r} must be repo-relative with no '..'".format(where, src))
        if not (root / src).exists():  # existence probe; an unreadable parent raises OSError -> exit 2
            raise ValueError("{}: source {!r} does not exist under {}".format(where, src, root))

    regenerate = raw["regenerate"]
    if not isinstance(regenerate, str) or not regenerate:
        raise ValueError("{}: regenerate must be a non-empty string".format(where))

    if target in seen:
        raise ValueError("{}: duplicate target {!r} (a target is generated by exactly one entry)"
                         .format(where, target))
    seen.add(target)
    return {"target": target, "kind": kind, "sources": list(sources), "regenerate": regenerate}


def collect_entries(modules, root):
    """Union and validate the declarations of every discovered generator plus this tool's own output,
    then return the entries sorted by target for a deterministic render. Raises ValueError on any
    malformed entry or duplicate target."""
    entries = []
    seen = set()
    for stem, mod in modules:
        decl = mod.GENSRC_OUTPUTS
        if not isinstance(decl, (tuple, list)):
            raise ValueError("{}.py: GENSRC_OUTPUTS must be a tuple of entries".format(stem))
        for raw in decl:
            entries.append(_validate_entry(raw, "{}.py".format(stem), root, seen))
    for raw in OWN_OUTPUTS:
        entries.append(_validate_entry(raw, "gen_gensrc.py", root, seen))
    entries.sort(key=lambda e: e["target"])
    return entries


def build_registry(root):
    """The full .aiqt/gensrc.json text: {"version": 1, "generated": [...sorted entries...]} rendered
    with sort_keys for determinism, plus a trailing newline. Raises ValueError/OSError/ImportError on a
    missing declaration or an unreadable input; the caller maps those to exit 2."""
    modules = discover_modules(root / "tools")
    entries = collect_entries(modules, root)
    obj = {"version": 1, "generated": entries}
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def run(root, check):
    """Render the registry into root/.aiqt/gensrc.json, or (check mode) report drift. Fail-closed
    (exit 2) on a missing declaration or any unreadable input, mirroring the sibling generators."""
    try:
        text = build_registry(root)
    except (ValueError, OSError, ImportError) as exc:
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
#   (c) a mutated .aiqt/gensrc.json is caught by --check (exit 1).

_FAKE_GOOD = '''# self-test generator that declares its output
GENSRC_OUTPUTS = (
    {"target": "OUT.md", "kind": "file",
     "sources": ("src.txt",), "regenerate": "python3 tools/gen_selftestgood.py"},
)
'''

_FAKE_BAD = '''# self-test generator that FORGOT to declare its output
PLACEHOLDER = 1
'''


def _build_good(base):
    """A conformant synthetic repo: a tools/ dir with one declaring generator, the source it names, and
    an empty .aiqt/ so reconcile can write the registry."""
    (base / "tools").mkdir(parents=True)
    (base / "tools" / "gen_selftestgood.py").write_text(_FAKE_GOOD, encoding="utf-8")
    (base / "src.txt").write_text("source\n", encoding="utf-8")
    (base / ".aiqt").mkdir()


def _build_bad(base):
    """A synthetic repo whose only generator omits GENSRC_OUTPUTS: discovery must fail closed."""
    (base / "tools").mkdir(parents=True)
    (base / "tools" / "gen_selftestbad.py").write_text(_FAKE_BAD, encoding="utf-8")
    (base / ".aiqt").mkdir()


def self_test_main():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr

    def run_quiet(root, check):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return run(root, check)

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-gensrc-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    added_paths = []
    failures = []
    try:
        # (a) A conformant tree generates, then re-checks drift-clean, and the registry exists.
        good = tmp / "good"
        _build_good(good)
        added_paths.append(str((good / "tools")))
        if run_quiet(good, check=False) != 0:
            failures.append("conformant tree: generation expected exit 0")
        if run_quiet(good, check=True) != 0:
            failures.append("conformant tree: regeneration expected drift-clean exit 0")
        registry = good / REGISTRY_REL
        if not registry.is_file():
            failures.append("conformant tree: expected {} to be written".format(REGISTRY_REL))

        # (b) A generator missing GENSRC_OUTPUTS fails closed (exit 2): the structural staleness guard.
        bad = tmp / "bad"
        _build_bad(bad)
        added_paths.append(str((bad / "tools")))
        if run_quiet(bad, check=True) != 2:
            failures.append("missing GENSRC_OUTPUTS expected exit 2 (fail-closed)")

        # (c) A mutated registry is caught by --check (exit 1).
        if registry.is_file():
            registry.write_text(registry.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            if run_quiet(good, check=True) != 1:
                failures.append("mutated {} expected exit 1 (drift)".format(REGISTRY_REL))
    finally:
        for p in added_paths:
            while p in sys.path:
                sys.path.remove(p)
        importlib.invalidate_caches()
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: a conformant tree generates and regenerates drift-clean; a generator that "
          "omits GENSRC_OUTPUTS fails closed (exit 2); and a mutated .aiqt/gensrc.json fails --check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
