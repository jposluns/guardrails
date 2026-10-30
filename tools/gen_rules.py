#!/usr/bin/env python3
"""Generate the .claude/rules/ read tree from .aiqt/core/rules/ sources (the source-and-adapter machinery).

Each source is a Markdown rule with a minimal YAML `---` frontmatter carrying its classification; its read
path is DERIVED from that frontmatter per the two-axis taxonomy (aiqt/ numbered by priority, security/
coded CIA+P). The updater's write root is `.aiqt/core/`; CI runs this in --check so the read tree can never
silently drift (including orphaned generated files with no source). Vendored `external/` trees are untouched.
  gen_rules.py           regenerate .claude/rules/{aiqt,security}/
  gen_rules.py --check   fail (exit 1) on drift; exit 2 on a malformed source or a read/write failure
  gen_rules.py --self-test  assert an invalid-UTF-8 generated target, a source path that is not a
                            directory or a symlink to a directory inside the root, and a .claude/, a
                            .claude/rules or a family directory symlinked outside the root fail closed
                            (exit 2), and the MAP_KEYS read refusal exits 2 with empty stdout under each
                            stderr condition
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: gen_rules.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import os
import re
import stat
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import SourceReadRefused, repo_root, read_source_text  # noqa: E402
from _standards import dir_chain_or_absent, dir_or_absent, dir_present, map_keys  # noqa: E402

TIER_FACETS = {"10": {"ACCUR", "INTEG", "QUALI", "TRUST"}, "20": {"PROGR"},
               "30": {"SPEED"}, "40": {"COST"}}
CIA_FACETS = {"SECC", "SECI", "SECA", "SECP"}  # SECC=Confidentiality SECI=Integrity SECA=Availability SECP=Privacy
# The global known-facet set: any rule may carry ANY of these as a `secondary` tag, cross-family (an aiqt
# rule may tag a security facet and vice versa). Security codes are namespaced (SEC*) so none collides with
# an AIQT facet (e.g. SECI vs the AIQT INTEG). The secondary element rules (known-facet, differs-from-
# primary, no-repeat) come from the maintainer's recorded decision (design-of-record: secondary = any known
# facet), NOT spec section 4, which only types `secondary` as a sequence of facet-code strings.
KNOWN_FACETS = set().union(*TIER_FACETS.values()) | CIA_FACETS
# Keys allowed for EVERY rule family. secondary is NOT here: spec section 4 forbids it on the apex and
# allows it only on aiqt-non-apex and security, so it is added to those allow-sets individually.
BASE_KEYS = {"corpus-id", "origin", "family", "slug"}
# Optional standards-mapping keys, allowed on security and aiqt-non-apex rules. Each value is a flow
# sequence of external control/subcategory IDs, for the public /mappings crosswalk. Adding a mapping key
# never affects a rule's derived path. The set is DERIVED from the vendored manifests under
# .aiqt/standards/ (one key per manifest): a mapping key is valid only if its framework's pinned id
# manifest exists, so a rule can never cite an unsourced framework. check_mappings then validates each
# cited id against that manifest's enumerated set.
try:
    MAP_KEYS = map_keys(repo_root())
except OSError as _exc:
    # map_keys fails closed on an existing-but-unlistable .aiqt/standards/. This binding runs at import,
    # so convert that read error into a clean exit 2 with a message rather than a bare traceback; every
    # tool that imports gen_rules (against its OWN broken standards dir) then fails closed uniformly.
    # The canonical floor-guard shape (tools/check_python_floor.py GUARD_TEMPLATE): the write and its
    # flush are best-effort, and os._exit skips the interpreter-exit flush of the std streams, so the
    # exit stays 2 when sys.stderr is None, fails on write or fails on flush, and stdout stays empty
    # (`raise SystemExit` became exit 120 under a failing flush, and print, with sys.stderr None, wrote
    # the diagnostic to stdout instead).
    try:
        sys.stderr.write("error: cannot read {}/.aiqt/standards/ (fail-closed): {}\n".format(
            repo_root(), _exc))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)
# Keys whose value, when present, must be a flow sequence (a list): secondary and every mapping key.
SEQ_KEYS = {"secondary"} | MAP_KEYS
SLUG_RE = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
CID_RE = re.compile(r'^[a-z0-9]{6,}$')

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata
# only, it does not affect what this generator produces.
# Renderer identity for the manifest-covered declaration (tools/gen_renderers.py; VER-CORE 6.5).
RENDERER_DECL = {"renderer-id": "rules", "semantics-revision": 1}
GENSRC_OUTPUTS = (
    {"target": ".claude/rules/aiqt/", "kind": "tree",
     "sources": (".aiqt/core/rules/",), "regenerate": "python3 tools/gen_rules.py"},
    {"target": ".claude/rules/security/", "kind": "tree",
     "sources": (".aiqt/core/rules/",), "regenerate": "python3 tools/gen_rules.py"},
)


def _unquote(tok):
    """A single scalar STRING element: strip matching quotes, else the bare token. Fail-closed."""
    if tok and tok[0] in "\"'":
        if len(tok) >= 2 and tok[-1] == tok[0]:
            return tok[1:-1]
        raise ValueError("malformed quoted value {!r}".format(tok))
    return tok


def _value(v):
    if v and v[0] in "\"'":
        return _unquote(v)
    # Flow sequence of strings, e.g. [INTEG, QUALI] (spec section 4). Elements are strings; a bare
    # element is not coerced to int/bool. Malformed (unclosed, or an empty element) is fail-closed.
    if v.startswith("["):
        if not v.endswith("]"):
            raise ValueError("malformed flow sequence {!r} (unterminated)".format(v))
        inner = v[1:-1].strip()
        if inner == "":
            return []
        if "[" in inner or "]" in inner:
            raise ValueError("nested flow sequence not allowed in {!r} (strings only)".format(v))
        elems = []
        for part in inner.split(","):
            part = part.strip()
            if part == "":
                raise ValueError("empty element in flow sequence {!r}".format(v))
            elems.append(_unquote(part))
        return elems
    if v in ("true", "false"):
        return v == "true"
    if re.fullmatch(r'-?\d+', v):
        return int(v)
    return v


def parse_source(path):
    # FIFO/symlink/non-regular: SourceReadRefused (OSError), never a hang
    return parse_source_text(read_source_text(path), path.name)


def parse_source_text(text, name):
    """The frontmatter of a rule source's text, already read; name labels every error."""
    if not text.startswith("---\n"):
        raise ValueError("{}: no frontmatter".format(name))
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError("{}: unterminated frontmatter".format(name))
    fm = {}
    for line in text[4:end].splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError("{}: bad frontmatter line {!r}".format(name, line))
        key, val = line.split(":", 1)
        key = key.strip()
        if key in fm:
            raise ValueError("{}: duplicate key {}".format(name, key))
        try:
            fm[key] = _value(val.strip())
        except ValueError as exc:
            raise ValueError("{}: {}".format(name, exc))
    return fm


def _check_keys(fm, allowed, name):
    extra = set(fm) - allowed
    if extra:
        raise ValueError("{}: unknown or forbidden key(s): {}".format(name, ", ".join(sorted(extra))))


def _check_secondary(fm, primary, name):
    # `secondary` is optional; when present it is already validated as a list (SEQ_KEYS). Each element
    # must be a known facet code, must differ from the primary facet (a facet is not its own secondary),
    # and must not repeat. Fail-closed so a typo or a duplicate is caught at generation, not shipped.
    seen = set()
    for s in fm.get("secondary", []):
        if s not in KNOWN_FACETS:
            raise ValueError("{}: secondary facet '{}' is not a known facet code".format(name, s))
        if s == primary:
            raise ValueError("{}: secondary facet '{}' duplicates the primary facet".format(name, s))
        if s in seen:
            raise ValueError("{}: secondary facet '{}' listed more than once".format(name, s))
        seen.add(s)


def derive(fm, name, allowed_origins=("pack",)):
    # allowed_origins defaults to pack-only so the generator (which sources only pack rules from
    # .aiqt/core/) stays strict; the placement gate passes ("pack", "adopter") because it must accept
    # correctly-placed adopter-authored rules too (spec sections 3 and 4). Origin does not affect the
    # derived path, only which origins are valid.
    for req in ("corpus-id", "origin", "family", "slug"):
        if req not in fm:
            raise ValueError("{}: missing required key '{}'".format(name, req))
    if not CID_RE.match(str(fm["corpus-id"])):
        raise ValueError("{}: corpus-id must match ^[a-z0-9]{{6,}}$".format(name))
    if fm["origin"] not in allowed_origins:
        raise ValueError("{}: origin must be one of {}".format(name, "/".join(allowed_origins)))
    if not SLUG_RE.match(str(fm["slug"])):
        raise ValueError("{}: slug must be kebab-case".format(name))
    for k in SEQ_KEYS:
        if k in fm and not isinstance(fm[k], list):
            raise ValueError("{}: {} must be a flow sequence".format(name, k))
    family = fm["family"]
    if family == "aiqt":
        if fm.get("apex") is True:
            _check_keys(fm, BASE_KEYS | {"apex"}, name)
            if fm["slug"] != "project-integrity":
                raise ValueError("{}: apex slug must be 'project-integrity'".format(name))
            return "aiqt/00-project-integrity.md"
        _check_keys(fm, BASE_KEYS | {"tier", "facet", "secondary"} | MAP_KEYS, name)
        tier = str(fm.get("tier", ""))
        facet = fm.get("facet", "")
        if tier not in TIER_FACETS:
            raise ValueError("{}: tier must be 10/20/30/40".format(name))
        if facet not in TIER_FACETS[tier]:
            raise ValueError("{}: facet '{}' invalid for tier {}".format(name, facet, tier))
        _check_secondary(fm, facet, name)
        return "aiqt/{}-{}-{}.md".format(tier, facet, fm["slug"])
    if family == "security":
        _check_keys(fm, BASE_KEYS | {"facet", "secondary"} | MAP_KEYS, name)
        facet = fm.get("facet", "")
        if facet not in CIA_FACETS:
            raise ValueError("{}: security facet must be SECC/SECI/SECA/SECP".format(name))
        _check_secondary(fm, facet, name)
        return "security/{}-{}.md".format(facet, fm["slug"])
    raise ValueError("{}: unknown family '{}'".format(name, family))


def load_corpus(src_dir):
    """Parse and fully validate every source (schema + unique corpus-id + unique derived path); raise
    ValueError on any malformed or duplicate. Returns [(path, frontmatter, derived_rel_path)]."""
    seen_ids, seen_paths, out = {}, {}, []
    # Collect *.md via os.walk with a raising onerror, NOT rglob: rglob (and a top-level-only
    # ensure_listable) SILENTLY skips an unreadable dir at ANY depth, so an unlistable family subdir
    # (e.g. .aiqt/core/rules/aiqt/) would read as an empty corpus (a false clean). os.walk(onerror=)
    # surfaces the read error at every level, so an unreadable dir fails closed as an OSError.
    def _raise(exc):
        raise exc
    md_files = []
    for dirpath, dirs, filenames in os.walk(src_dir, onerror=_raise):
        # A '*.md' entry that is not a regular file (a directory, a symlink, a FIFO) is a named refusal,
        # never a silent drop: os.walk lists a directory named x.md among dirs (and descends into it), so
        # without this the rule would vanish from the corpus and write mode would delete its output.
        for name in sorted(dirs + filenames):
            if name.endswith(".md") and not stat.S_ISREG(os.lstat(Path(dirpath) / name).st_mode):
                raise SourceReadRefused("{}: refused, a rule source that is not a regular file (a "
                                        "directory, symlink, or special file)".format(Path(dirpath) / name))
        md_files.extend(Path(dirpath) / fn for fn in filenames if fn.endswith(".md"))
    for src in sorted(md_files):
        fm = parse_source(src)
        rel = derive(fm, src.name)
        cid = str(fm["corpus-id"])
        if cid in seen_ids:
            raise ValueError("{}: corpus-id {} already used by {}".format(src.name, cid, seen_ids[cid]))
        if rel in seen_paths:
            raise ValueError("{}: derives to {} already produced by {}".format(src.name, rel, seen_paths[rel]))
        seen_ids[cid] = src.name
        seen_paths[rel] = src.name
        out.append((src, fm, rel))
    return out


def run(root, check):
    """Reconcile the .claude/rules/ read tree under root against the .aiqt/core/rules/ corpus. Exit 0 in
    sync, 1 on drift (check mode), 2 on a malformed source or a read/write failure. Parameterized on root
    (rather than calling repo_root() inline) so the self-test can drive it against a synthetic tempdir
    tree, never the real repo."""
    src_dir = root / ".aiqt" / "core" / "rules"
    out_dir = root / ".claude" / "rules"
    desired = {}
    try:
        # dir_or_absent (not is_dir) inside the try: an unreadable .aiqt/ parent, or a source path that is
        # there but cannot be read as a directory, must fail closed as exit 2, not read as an absent corpus
        # (which would delete every generated file as an orphan below). A symlink to a directory inside the
        # root is read.
        # Then dir_present, bound beneath root, judges every component above it the same way, so a
        # .aiqt that is dangling, not a directory or a symlink resolving outside the root fails closed
        # too, never read as an absent corpus.
        _present, problem = dir_or_absent(src_dir, root)
        if problem is not None:
            print("error: {} {} (cannot evaluate)".format(src_dir, problem))
            return 2
        if dir_present(src_dir, root=root):
            for src, _fm, rel in load_corpus(src_dir):
                desired[rel] = read_source_text(src)
    except (ValueError, OSError) as exc:
        print("error: {}".format(exc))
        return 2
    # Reconcile even when src_dir is absent (desired empty) so orphaned generated files are never concealed.
    # The whole reconcile is fail-closed: an unreadable generated file (target.read_text) or an unreadable
    # generated dir at ANY depth (os.walk(onerror=raise), not rglob, which silently skips an unlistable
    # subdir) becomes a clean exit 2 rather than a traceback or a concealed orphan.
    drift = []

    def _raise(exc):
        raise exc
    try:
        # dir_chain_or_absent (not is_dir, dir_present or dir_or_absent), before the first read, write or delete
        # under out_dir: .claude/rules and each family directory in it (the directory holding every generated
        # rule) are judged at every component under the root (.claude/ too), so one at any depth that is there
        # but cannot be read as a directory (a regular file, or a symlink that is dangling, whose target is not
        # a directory or resolves outside the root) is exit 2 with nothing written or deleted, never followed
        # out of the repository, and an unreadable parent raises (exit 2 below), never skipping the orphan
        # scan. A symlink to a directory inside the root is read. A family absent here can only be created
        # below, holding desired files alone.
        probed = {}
        for path in (out_dir, out_dir / "aiqt", out_dir / "security"):
            probed[path], problem = dir_chain_or_absent(path, root)
            if problem is not None:
                print("error: {} {} (cannot evaluate)".format(path, problem))
                return 2
        for rel, content in sorted(desired.items()):
            target = out_dir / rel
            try:  # the shared reader: a symlinked or non-regular generated target is refused (OSError, exit 2)
                current = read_source_text(target)
            except FileNotFoundError:
                current = None
            if current != content:
                drift.append(rel)
                if not check:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
        for family in ("aiqt", "security"):
            fam_dir = out_dir / family
            if probed[fam_dir]:
                for dirpath, _dirs, filenames in os.walk(fam_dir, onerror=_raise):
                    for fn in sorted(f for f in filenames if f.endswith(".md")):
                        f = Path(dirpath) / fn
                        # as_posix(), not str(): desired keys are forward-slash derive() paths, so a
                        # backslash from str() on Windows would flag every generated file as an orphan.
                        rel = f.relative_to(out_dir).as_posix()
                        if rel not in desired:
                            drift.append("orphan " + rel)
                            if not check:
                                f.unlink()
    except (OSError, UnicodeError) as exc:
        # UnicodeError (UnicodeDecodeError) covers the generated-TARGET read above: a non-UTF-8 target
        # decodes as UTF-8 there, so a corrupt target fails closed (exit 2) rather than a raw traceback,
        # the same OSError path (a read-only fs, a permission error, a full disk) already fails closed on.
        print("error: {}".format(exc))
        return 2
    if check and drift:
        print("drift: " + "; ".join(drift))
        print("run tools/gen_rules.py to regenerate")
        return 1
    return 0


def main():
    argv = sys.argv[1:]
    if "--self-test" in argv:
        return self_test_main()
    return run(repo_root(), "--check" in argv)


# --- self-test ----------------------------------------------------------------------------------------
# One focused invariant (the sibling generators' idiom): an invalid-UTF-8 GENERATED TARGET fails closed
# (exit 2) rather than a raw UnicodeDecodeError traceback. The reconcile loop reads each desired target as
# UTF-8 (the drift compare), so a non-UTF-8 target must be caught by the widened (OSError, UnicodeError)
# arm (F-154). A revert to the narrow OSError-only arm makes run() RAISE instead of returning 2, so this
# case fails and guards the widening. Tempdir-only; never touches a real repo file.

_RULE_SRC = """---
corpus-id: selfr1
origin: pack
family: aiqt
tier: 10
facet: QUALI
slug: gen-rules-selftest-target
---
# Gen-rules self-test rule

A minimal rule so the reconcile has one desired target to read.
"""
_RULE_REL = "aiqt/10-QUALI-gen-rules-selftest-target.md"

# The second invariant: the import-time MAP_KEYS refusal keeps exit 2 and an empty stdout whatever
# sys.stderr does. A child imports this module with map_keys raising OSError, under a piped stderr
# (the diagnostic must arrive), a None stderr, a stderr whose write raises and one whose flush raises.
# The retired print/`raise SystemExit(2)` shape fails three of the four: print to a None stream writes
# the diagnostic to stdout, and a stream whose write or flush raises turns the exit into 120.
_MAP_KEYS_CHILD = """import sys
sys.path.insert(0, sys.argv[1])
import _standards
def _refuse(root):
    raise PermissionError(13, "self-test: unlistable standards directory")
_standards.map_keys = _refuse
class _Stream:
    def write(self, text):
        if sys.argv[2] == "write-raises":
            raise OSError(28, "self-test: write failed")
        return len(text)
    def flush(self):
        if sys.argv[2] in ("write-raises", "flush-raises"):
            raise OSError(28, "self-test: flush failed")
if sys.argv[2] != "piped":
    sys.stderr = None if sys.argv[2] == "none" else _Stream()
import gen_rules
"""
_MAP_KEYS_CONDITIONS = ("piped", "none", "write-raises", "flush-raises")


def _map_keys_refusal_failures():
    """One failure line per stderr condition under which the MAP_KEYS refusal does not exit 2 with
    empty stdout (and, with stderr piped, the diagnostic on stderr)."""
    import subprocess
    tools = str(Path(__file__).resolve().parent)
    failures = []
    for condition in _MAP_KEYS_CONDITIONS:
        try:
            done = subprocess.run([sys.executable, "-I", "-B", "-c", _MAP_KEYS_CHILD, tools, condition],
                                  capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            failures.append("MAP_KEYS refusal ({}): the child did not run: {}".format(condition, exc))
            continue
        got = (done.returncode, done.stdout, condition != "piped" or b"(fail-closed)" in done.stderr)
        if got != (2, b"", True):
            failures.append("MAP_KEYS refusal with stderr {}: expected exit 2, empty stdout and (piped) "
                            "the diagnostic; got exit {}, stdout {!r}, stderr {!r}".format(
                                condition, done.returncode, done.stdout[:200], done.stderr[-300:]))
    return failures


def self_test_main():
    import io
    import shutil
    import signal
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr

    def run_quiet(root, check):
        # A reverted narrow (OSError-only) arm raises UnicodeDecodeError out of run(); catch it and
        # return a non-int sentinel so it registers as a FAILURE against the expected exit code rather
        # than aborting the self-test or letting it exit early green.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                return run(root, check)
            except Exception as exc:  # noqa: BLE001  a revert surfaces here as UnicodeDecodeError
                return "raised {}".format(type(exc).__name__)

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-rules-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    try:
        # A synthetic corpus (one source rule) so `desired` carries exactly one generated target, then
        # pre-write that target as invalid UTF-8 bytes so the reconcile's drift-compare read hits it.
        src = tmp / ".aiqt" / "core" / "rules"
        src.mkdir(parents=True)
        (src / "gen-rules-selftest-target.md").write_text(_RULE_SRC, encoding="utf-8")
        target = tmp / ".claude" / "rules" / _RULE_REL
        target.parent.mkdir(parents=True)
        target.write_bytes(b"\xff\xfe not utf-8")
        if run_quiet(tmp, check=True) != 2:
            failures.append("invalid-UTF-8 generated target expected exit 2 (fail-closed)")
        # A source path that is there but is not a directory is exit 2 in write mode with the generated
        # target kept, never read as an absent corpus that prunes every generated file as an orphan.
        nondir = tmp / "nondir"
        kept = nondir / ".claude" / "rules" / _RULE_REL
        kept.parent.mkdir(parents=True)
        kept.write_text(_RULE_SRC, encoding="utf-8")
        (nondir / ".aiqt" / "core").mkdir(parents=True)
        (nondir / ".aiqt" / "core" / "rules").write_text("not a directory\n", encoding="utf-8")
        if run_quiet(nondir, check=False) != 2 or not kept.exists():
            failures.append("a source path that is a regular file expected exit 2 with the generated "
                            "target kept")
        # A source path that is a symlink: to a directory inside the root holding the rule, it is read (--check
        # exit 0, the generated target in sync); dangling, to a regular file inside the root, or to an empty
        # directory outside the root, it is exit 2 in write mode with the generated target kept, never read as
        # an absent corpus or followed to one that prunes it as an orphan.
        (tmp / "outside").mkdir()
        for shape, check, want in (("in-root", True, 0), ("dangling", False, 2), ("to-file", False, 2),
                                   ("outside", False, 2)):
            linked = tmp / ("linked-" + shape)
            kept = linked / ".claude" / "rules" / _RULE_REL
            kept.parent.mkdir(parents=True)
            kept.write_text(_RULE_SRC, encoding="utf-8")
            (linked / "shared" / "rules").mkdir(parents=True)
            (linked / "shared" / "rules" / "gen-rules-selftest-target.md").write_text(_RULE_SRC, encoding="utf-8")
            (linked / "shared" / "file.txt").write_text("not a directory\n", encoding="utf-8")
            (linked / ".aiqt" / "core").mkdir(parents=True)
            target = {"in-root": Path("..", "..", "shared", "rules"), "dangling": Path("absent"),
                      "to-file": Path("..", "..", "shared", "file.txt"), "outside": tmp / "outside"}[shape]
            (linked / ".aiqt" / "core" / "rules").symlink_to(target)
            got = run_quiet(linked, check=check)
            if got != want or not kept.exists():
                failures.append("a source path that is a symlink {} expected exit {} with the generated target "
                                "kept, got {}".format(shape, want, got))
        # The .claude/ directory above the read tree, the read tree, or a family directory in it, that is a
        # symlink to a directory holding a planted orphan and lacking the generated rule, in write mode: outside
        # the root, it is exit 2 with the orphan kept and the rule not written there, never followed to prune or
        # write out of the repository; inside the root, it is read (exit 0, the orphan pruned and, but for the
        # security family, the rule written there).
        for linked_rel, sub in ((".claude", ("rules", "aiqt")), (".claude/rules", ("aiqt",)),
                                (".claude/rules/aiqt", ()), (".claude/rules/security", ())):
            for where, want in (("outside", 2), ("in-root", 0)):
                out = tmp / ("out-" + linked_rel.lstrip(".").replace("/", "-") + "-" + where)
                (out / ".aiqt" / "core" / "rules").mkdir(parents=True)
                (out / ".aiqt" / "core" / "rules" / "gen-rules-selftest-target.md").write_text(
                    _RULE_SRC, encoding="utf-8")
                link = out / linked_rel
                link.parent.mkdir(parents=True, exist_ok=True)
                held = tmp / ("held-" + out.name) if where == "outside" else out / "shared" / "held"
                fam = held.joinpath(*sub)
                fam.mkdir(parents=True)
                (fam / "zz-planted-orphan.md").write_text("planted\n", encoding="utf-8")
                link.symlink_to(held if where == "outside" else os.path.relpath(held, link.parent))
                got = run_quiet(out, check=False)
                kept = (fam / "zz-planted-orphan.md").exists()
                wrote = (fam / Path(_RULE_REL).name).exists()
                if (got, kept, wrote) != (want, want == 2, want == 0 and linked_rel != ".claude/rules/security"):
                    failures.append("a {} that is a symlink {} expected exit {} with the orphan {} there, "
                                    "got exit {}, orphan kept {}, rule written {}".format(
                                        linked_rel, where, want, "kept and nothing written" if want == 2
                                        else "pruned", got, kept, wrote))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    failures.extend(_map_keys_refusal_failures())

    # F-CORPUS-FIFO-HANG: a FIFO (no writer) at a rule source must be the named refusal (exit 2, "not a
    # regular file") inside a bounded alarm, never a blocking read. The run() leg pins load_corpus's
    # non-regular-source scan, which refuses BEFORE parse_source reads anything; the direct parse_source
    # leg is the one that discriminates the reader itself. MUTATION: reverting parse_source to
    # path.read_text() makes the direct leg block on the FIFO and the alarm records the hang (the run()
    # leg alone stays green under that revert). POSIX only; where os.mkfifo or signal.SIGALRM is
    # missing, the vector is an explicit NOT RUN and the self-test fails closed.
    if hasattr(os, "mkfifo") and hasattr(signal, "SIGALRM"):
        class _Hang(Exception):
            pass

        def _on_alarm(_signum, _frame):
            raise _Hang()

        try:
            ftmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-rules-fifo-"))
        except OSError as exc:
            print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
            return 2
        out = io.StringIO()
        previous = signal.signal(signal.SIGALRM, _on_alarm)
        try:
            fsrc = ftmp / ".aiqt" / "core" / "rules"
            fsrc.mkdir(parents=True)
            os.mkfifo(fsrc / "gen-rules-selftest-target.md")
            signal.alarm(10)
            try:
                with redirect_stdout(out), redirect_stderr(io.StringIO()):
                    rc = run(ftmp, check=True)
            except _Hang:
                rc = "hung"
            finally:
                signal.alarm(0)
            if rc != 2 or "not a regular file" not in out.getvalue():
                failures.append("a FIFO rule source expected the named refusal (exit 2, 'not a regular "
                                "file') within the alarm, got {!r}".format(rc))
            signal.alarm(10)
            try:
                try:
                    parse_source(fsrc / "gen-rules-selftest-target.md")
                    prc = "returned"
                except _Hang:
                    prc = "hung"
                except SourceReadRefused as exc:
                    prc = "refused" if "not a regular file" in str(exc) else repr(exc)
            finally:
                signal.alarm(0)
            if prc != "refused":
                failures.append("parse_source on a FIFO expected the named SourceReadRefused "
                                "within the alarm, never a hang or a return, got {!r}".format(prc))
        finally:
            signal.signal(signal.SIGALRM, previous)
            shutil.rmtree(ftmp, ignore_errors=True)
    else:
        print("NOT RUN: the F-CORPUS-FIFO-HANG vectors (a FIFO rule source refused by run() and "
              "by parse_source directly) need os.mkfifo and signal.SIGALRM; failing closed")
        failures.append("F-CORPUS-FIFO-HANG vectors NOT RUN (os.mkfifo or signal.SIGALRM "
                        "unavailable); the FIFO refusal was not demonstrated on this platform")

    # SPECIAL-FILE-PRECHECK item 3: a '*.md' rule source that turned into a directory or a symlink is
    # a named refusal (exit 2, "not a regular file") in check AND write mode, never a silent drop; write
    # mode must keep the generated rule it would otherwise delete as an orphan. MUTATION: dropping the
    # load_corpus lstat check makes check mode exit 1 (orphan drift) and write mode delete the target.
    for kind in ("directory", "symlink"):
        for check in (True, False):
            try:
                ktmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-rules-nonfile-"))
            except OSError as exc:
                print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
                return 2
            try:
                ksrc = ktmp / ".aiqt" / "core" / "rules"
                ksrc.mkdir(parents=True)
                kout = ktmp / ".claude" / "rules" / _RULE_REL
                kout.parent.mkdir(parents=True)
                kout.write_text(_RULE_SRC, encoding="utf-8")
                if kind == "directory":
                    (ksrc / "gen-rules-selftest-target.md").mkdir()
                else:
                    (ktmp / "elsewhere.md").write_text(_RULE_SRC, encoding="utf-8")
                    (ksrc / "gen-rules-selftest-target.md").symlink_to(ktmp / "elsewhere.md")
                out = io.StringIO()
                with redirect_stdout(out), redirect_stderr(io.StringIO()):
                    try:
                        rc = run(ktmp, check)
                    except Exception as exc:  # noqa: BLE001  a regression surfaces as a failure, not an abort
                        rc = "raised {}".format(type(exc).__name__)
                if rc != 2 or "not a regular file" not in out.getvalue():
                    failures.append("a {} rule source ({} mode) expected the named refusal (exit 2, 'not a "
                                    "regular file'), got {!r}".format(kind, "check" if check else "write", rc))
                if not kout.is_file():
                    failures.append("write mode deleted a generated rule because its source became a "
                                    "{}".format(kind))
            except OSError as exc:
                failures.append("a {} rule source fixture could not be built: {}".format(kind, exc))
            finally:
                shutil.rmtree(ktmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: an invalid-UTF-8 generated target fails closed (exit 2), not a raw "
          "UnicodeDecodeError traceback (guards the widened reconcile arm); a FIFO rule source is refused "
          "(exit 2) inside an alarm, never a hang; a rule source that became a directory or a symlink is "
          "refused by name (exit 2) in check and write mode and its generated rule is kept; a source path "
          "that is a regular file or a symlink that is dangling, to a regular file or to a directory outside "
          "the root exits 2 with the generated target kept, and one symlinked to a directory inside the root "
          "is read; a .claude/, .claude/rules or family directory symlinked outside the root exits 2 in write "
          "mode with nothing written or deleted there, and one symlinked inside the root is read; the "
          "MAP_KEYS read refusal exits 2 with empty stdout under {} stderr conditions ({}).".format(
              len(_MAP_KEYS_CONDITIONS), ", ".join(_MAP_KEYS_CONDITIONS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
