#!/usr/bin/env python3
"""OPF enforcement-pack platform coverage gate: one protected-path list shared by every platform.

OPF-SPEC 14.1 names four supported assistant platforms (Claude Code, Codex, Gemini CLI and Cursor) and lets
the enforcement pack cover each one EITHER by a verified deny hook, where the platform's official
documentation confirms denial support, OR by instructions, disclosing that platform's residual. The pack's
roster of that choice is opf/enforcement/platforms.toml. This gate holds the roster and its members to
these rules:

  roster      exactly one row per supported assistant platform (every ENFORCEMENT_MEANS platform of
              opf/tools/_opf_adopt.py that allows a deny hook), in that order, each row's means one its
              platform allows, its residuals drawn from ENFORCEMENT_RESIDUALS, unique, and carrying the
              residual its means requires (ENFORCEMENT_REQUIRED_RESIDUALS), with a non-empty evidence text.
  deny hook   a deny-hook row is allowed only for a platform whose deny mechanism this repository
              documents and tests: a platform in tools/gen_hooks.py PLATFORMS, the only hook platform
              the AIQT pack renders and tests. Any other platform takes the instructions tier.
  location    an instructions row installs at the location the AIQT pack's own adapter generator writes
              for that platform: AGENTS.md for Codex (tools/gen_agents.py), GEMINI.md for Gemini CLI
              (tools/gen_adapters.py), a .mdc rule under .cursor/rules/ for Cursor (tools/gen_cursor.py),
              each read statically from that generator's own literals. A platform with no such generator
              location has no instructions row. The member's own file name is not a name a platform
              loads (AGENTS.md, AGENTS.override.md, GEMINI.md, CLAUDE.md, anything under a .cursor
              directory), so the shipped pack tree does not apply its rules to sessions working on it.
  tier        an instructions member states the exact tier line "Enforcement tier: instructions
              (advisory, not enforced)." and, outside that line, inline code spans and the rendered
              block, makes no denial, blocking or enforcement claim (CLAIM_RE), since nothing on that
              platform enforces it.
  one list    an instructions member carries, between the BEGIN and END marker lines, exactly the
              protected-path block this gate renders from the Claude Code deny hook
              (opf/enforcement/claude/pretooluse_deny.py, loaded through importlib, never launched):
              the store tree (WORKING), the adoption archive (ADOPTION_ARCHIVE), the adoption evidence
              home (ADOPTION_EVIDENCE), the frozen plan sources (PLAN_FILENAME, PLAN_FORMAT,
              FROZEN_DISPOSITIONS), the declared views (MANIFEST_STANDARD), the pack's own trees (the
              hook's _guarded_prefixes, made relative to the repository root), the hook registration
              (the hook's _registration_idents over a probe root), the sanctioned writer verbs
              (WRITER_VERBS) and the imported-series exemption (IMPORTED_LEAF_RE). A change to any of
              those in the hook changes the rendered block, so every platform's list then differs from
              the hook's and this gate is red until the members carry the new block.
  Cursor      the Cursor member opens with the always-apply frontmatter tools/gen_cursor.py emits.

  check_opf_enforce_platforms.py              check the real tree
  check_opf_enforce_platforms.py --self-test  fixture copies of the inputs, each rule held red by a mutant

Exit convention (the repository's gates): 0 clean, 1 a finding, 2 cannot evaluate (an unreadable or
malformed input, a hook that cannot be loaded, a generator literal that cannot be read).

DISCLOSED RESIDUALS. The rendered block derives every token the hook holds in a constant or computes in
the functions named above; the prose around those tokens and two literals the hook spells inline (the
manifest's views table and its target key) are fixed text in this gate, so a hook change to those two
literals alone is not seen here. The tier scan is lexical: it reads words, not meaning, skips inline code
spans and the rendered block, and cannot judge a claim worded without the listed words. The deny-hook rule
reads tools/gen_hooks.py PLATFORMS as the repository's record of a tested deny mechanism; it does not
itself verify a platform's documentation. Input files are read after a regular-file check, so a file
swapped between that check and the read is a check-to-use race. This gate checks the pack's text; it
does not prove that any platform follows an instructions member.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: check_opf_enforce_platforms.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import ast
import importlib.util
import os
import re
import shutil
import stat
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACK_REL = "opf/enforcement"
ROSTER_REL = PACK_REL + "/platforms.toml"
HOOK_REL = PACK_REL + "/claude/pretooluse_deny.py"
ADOPT_REL = "opf/tools/_opf_adopt.py"
GEN_HOOKS_REL = "tools/gen_hooks.py"
GEN_AGENTS_REL = "tools/gen_agents.py"
GEN_ADAPTERS_REL = "tools/gen_adapters.py"
GEN_CURSOR_REL = "tools/gen_cursor.py"
INPUT_RELS = (ROSTER_REL, HOOK_REL, ADOPT_REL, GEN_HOOKS_REL, GEN_AGENTS_REL, GEN_ADAPTERS_REL, GEN_CURSOR_REL)
MAX_INPUT_BYTES = 4 * 1024 * 1024

ROW_KEYS = frozenset(("platform", "means", "member", "destination", "residuals", "evidence"))
DENY_MEANS = "deny-hook"
TIER_LINE = "Enforcement tier: instructions (advisory, not enforced)."
BEGIN = ("<!-- opf-protected-paths begin: rendered from opf/enforcement/claude/pretooluse_deny.py by "
         "tools/check_opf_enforce_platforms.py; change the hook, never this list -->")
END = "<!-- opf-protected-paths end -->"
# Words that claim a platform stops an operation. An instructions member enforces nothing, so none may
# appear outside the tier line, inline code spans and the rendered block.
CLAIM_RE = re.compile(r"\b(?:den(?:y|ies|ied|ial|ials|ying)|block(?:s|ed|ing)?|enforc(?:e|es|ed|ing)|"
                      r"prevent(?:s|ed|ing)?|refus(?:e|es|ed|ing|al)|guarantee(?:s|d)?|ensure(?:s|d)?)\b",
                      re.IGNORECASE)
CODE_SPAN_RE = re.compile(r"`[^`\n]*`")
# File names a platform loads as instructions; a pack member never carries one (module docstring).
LOADED_NAMES = frozenset(("AGENTS.md", "AGENTS.override.md", "GEMINI.md", "CLAUDE.md"))
# A probe root no filesystem holds, for reading the registration identities the hook derives per root.
PROBE_ROOT = os.path.join(os.sep, "opf-protected-path-probe-root")


class CannotEvaluate(Exception):
    """An input this gate cannot read or interpret (exit 2)."""


def _read(path, label):
    """The bytes of a regular input file, or CannotEvaluate naming it."""
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise CannotEvaluate("%s %s cannot be read (%s)" % (label, path, exc))
    if not stat.S_ISREG(st.st_mode):
        raise CannotEvaluate("%s %s is not a regular file" % (label, path))
    if st.st_size > MAX_INPUT_BYTES:
        raise CannotEvaluate("%s %s exceeds %d bytes" % (label, path, MAX_INPUT_BYTES))
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise CannotEvaluate("%s %s cannot be read (%s)" % (label, path, exc))


def _text(path, label):
    try:
        return _read(path, label).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("%s %s is not UTF-8 (%s)" % (label, path, exc))


def _literals(path, names):
    """The module-level literal values of `names` in the Python file at `path`, read statically (parsed,
    never imported or run). Each name must be assigned exactly once, to a literal."""
    try:
        tree = ast.parse(_text(path, "the source"), filename=str(path))
    except SyntaxError as exc:
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
                except (ValueError, TypeError, SyntaxError, RecursionError) as exc:
                    raise CannotEvaluate("%s: %s is not a literal (%s)" % (path, name, exc))
    missing = sorted(set(names) - set(found))
    if missing:
        raise CannotEvaluate("%s carries no literal %s" % (path, ", ".join(missing)))
    return found


def load_hook(path):
    """The Claude Code deny hook as a module object, loaded from `path` through importlib under a private
    name (never entered in sys.modules, and its main() is never called)."""
    _read(path, "the Claude Code deny hook")
    spec = importlib.util.spec_from_file_location("_opf_enforce_platforms_hook", str(path))
    if spec is None or spec.loader is None:
        raise CannotEvaluate("the Claude Code deny hook %s cannot be loaded" % (path,))
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (Exception, SystemExit) as exc:  # noqa: BLE001  any load failure is cannot-evaluate
        raise CannotEvaluate("the Claude Code deny hook %s fails to load (%r)" % (path, exc))
    return module


def _relative(path, root):
    real_root = os.path.realpath(root)
    rel = os.path.relpath(os.path.realpath(path), real_root)
    if rel in (os.curdir, os.pardir) or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
        raise CannotEvaluate("the hook's guarded tree %s lies outside the repository root %s" % (path, root))
    return rel.replace(os.sep, "/")


def render_block(hook, root):
    """The protected-path block every instructions member carries, rendered from the hook's own constants
    and functions (module docstring, "one list")."""
    try:
        working = hook.WORKING
        archive = "/".join((working,) + tuple(hook.ADOPTION_ARCHIVE))
        evidence = "/".join((working,) + tuple(hook.ADOPTION_EVIDENCE))
        imported = "/".join((working, hook.ADOPTION_EVIDENCE[0]))
        plan = evidence + "/<run-id>/" + hook.PLAN_FILENAME
        plan_format = hook.PLAN_FORMAT
        frozen = " or ".join("`%s`" % d for d in sorted(hook.FROZEN_DISPOSITIONS))
        standard = hook.MANIFEST_STANDARD
        verbs = sorted(hook.WRITER_VERBS)
        leaf_pattern = hook.IMPORTED_LEAF_RE.pattern
        pack = [_relative(p, root) for p in hook._guarded_prefixes()]
        idents = hook._registration_idents([PROBE_ROOT])
    except CannotEvaluate:
        raise
    except Exception as exc:  # noqa: BLE001  a hook without these members cannot be rendered
        raise CannotEvaluate("the Claude Code deny hook lacks a protected-path member (%r)" % (exc,))
    if "render" not in verbs:
        raise CannotEvaluate("the Claude Code deny hook's WRITER_VERBS carries no render verb")
    writers = " and ".join("`opf %s`" % v for v in verbs)
    prefix = PROBE_ROOT + os.sep
    registrations = sorted(os.path.relpath(key, PROBE_ROOT).replace(os.sep, "/")
                           for key in idents if key.startswith(prefix))
    if not registrations or len(pack) != 2:
        raise CannotEvaluate("the Claude Code deny hook yields no registration path, or not exactly its two "
                             "guarded trees (the pack, then the writer's tools)")
    lines = [
        BEGIN,
        "",
        "- `**/%s/**` (R1): every path with a `%s` component: OPF records, counters, ledgers, indexes, "
        "journals, staging and evidence. Change them only through %s." % (working, working, writers),
        "- `%s/**` (R1): adoption and import evidence, written only by the opf writers." % (imported,),
        "- `%s/<run-id>/**` (R2): the adoption archive of preserved originals. Write nothing here." % (archive,),
        "- Frozen old files (R3): every `[[sources]]` path whose disposition is %s in `%s` (format `%s`), "
        "relative to the product root. Leave it byte-identical until its retirement is recorded."
        % (frozen, plan, plan_format),
        "- Declared views (R4): every `[views.<name>]` `target` in the machine-store manifest "
        "`%s/<machine>/manifest.toml` whose `[%s]` table carries `standard = \"%s\"`, relative to the "
        "product root. Change them only through `opf render`." % (working, standard, standard),
    ]
    lines.append("- `%s/**` (R8): the OPF enforcement pack, at its installed location." % (pack[0],))
    lines.append("- `%s/**` (R8): the opf writer and its tools, at their installed location." % (pack[1],))
    for rel in registrations:
        lines.append("- `%s` (R8): the Claude Code hook registration of the product root." % (rel,))
    lines.extend([
        "",
        "Not on the list until the import writer ships: a leaf directly inside the machine store directory "
        "`%s/<machine>/` whose name matches `%s`." % (working, leaf_pattern),
        "",
        END,
    ])
    return "\n".join(lines)


def _vocabulary(root):
    adopt = _literals(root / ADOPT_REL, ("ENFORCEMENT_MEANS", "ENFORCEMENT_RESIDUALS",
                                         "ENFORCEMENT_REQUIRED_RESIDUALS"))
    hooks = _literals(root / GEN_HOOKS_REL, ("PLATFORMS",))
    agents = _literals(root / GEN_AGENTS_REL, ("GENSRC_OUTPUTS",))
    adapters = _literals(root / GEN_ADAPTERS_REL, ("GENSRC_OUTPUTS",))
    cursor = _literals(root / GEN_CURSOR_REL, ("OUT_PARTS", "FRONTMATTER"))
    means = adopt["ENFORCEMENT_MEANS"]
    if not isinstance(means, dict) or not all(isinstance(v, tuple) for v in means.values()):
        raise CannotEvaluate("%s ENFORCEMENT_MEANS is not a table of tuples" % (ADOPT_REL,))

    def target(outputs, leaf, rel):
        hits = [o.get("target") for o in outputs if isinstance(o, dict)
                and isinstance(o.get("target"), str) and o["target"].rsplit("/", 1)[-1] == leaf]
        if len(hits) != 1:
            raise CannotEvaluate("%s declares no single %s output" % (rel, leaf))
        return hits[0]

    parts = cursor["OUT_PARTS"]
    if not isinstance(parts, tuple) or len(parts) < 2 or not all(isinstance(p, str) for p in parts):
        raise CannotEvaluate("%s OUT_PARTS is not a tuple of path parts" % (GEN_CURSOR_REL,))
    return dict(
        platforms=tuple(p for p, allowed in means.items() if DENY_MEANS in allowed),
        means=means,
        residuals=adopt["ENFORCEMENT_RESIDUALS"],
        required=adopt["ENFORCEMENT_REQUIRED_RESIDUALS"],
        deny_platforms=hooks["PLATFORMS"],
        locations=dict((
            ("codex", ("file", target(agents["GENSRC_OUTPUTS"], "AGENTS.md", GEN_AGENTS_REL), GEN_AGENTS_REL)),
            ("gemini-cli", ("file", target(adapters["GENSRC_OUTPUTS"], "GEMINI.md", GEN_ADAPTERS_REL),
                            GEN_ADAPTERS_REL)),
            ("cursor", ("mdc", "/".join(parts[:2]) + "/", GEN_CURSOR_REL)),
        )),
        frontmatter=cursor["FRONTMATTER"],
    )


def _contained(rel):
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel:
        return False
    return all(p not in ("", ".", "..") for p in rel.split("/")) and not any(ord(c) < 0x20 for c in rel)


def _member_findings(where, text, block, frontmatter=None):
    findings = []
    if frontmatter is not None and not text.startswith(frontmatter):
        findings.append("%s: does not open with the always-apply Cursor frontmatter %r" % (where, frontmatter))
    if text.count(BEGIN) != 1 or text.count(END) != 1 or text.index(END) < text.index(BEGIN):
        findings.append("%s: carries no single BEGIN and END protected-path marker pair" % (where,))
    else:
        start = text.index(BEGIN)
        stop = text.index(END) + len(END)
        if text[start:stop] != block:
            findings.append("%s: its protected-path list differs from the list rendered from %s "
                            "(expected block follows)\n%s" % (where, HOOK_REL, block))
        text = text[:start] + text[stop:]
    lines = text.split("\n")
    if lines.count(TIER_LINE) != 1:
        findings.append("%s: does not state the tier line %r exactly once on a line of its own"
                        % (where, TIER_LINE))
    for number, line in enumerate(lines, 1):
        if line == TIER_LINE:
            continue
        hit = CLAIM_RE.search(CODE_SPAN_RE.sub("", line))
        if hit:
            findings.append("%s: line %d claims %r, but nothing on this platform enforces it (the "
                            "instructions tier is advisory)" % (where, number, hit.group(0)))
    return findings


def _row_findings(root, vocab, block, where, row):
    """The findings for one well-formed roster row of a known, first-seen platform."""
    platform, means, member, destination = row["platform"], row["means"], row["member"], row["destination"]
    findings = []
    residuals = row["residuals"]
    if not isinstance(residuals, list) or len(set(map(repr, residuals))) != len(residuals) \
            or any(r not in vocab["residuals"] for r in residuals):
        findings.append("%s: residuals must be unique members of %s" % (where, list(vocab["residuals"])))
    else:
        for required in vocab["required"].get(means, ()):
            if required not in residuals:
                findings.append("%s: a %s row must disclose the residual %r" % (where, means, required))
    if not isinstance(row["evidence"], str) or not row["evidence"].strip():
        findings.append("%s: carries no evidence for its means" % (where,))
    if not _contained(member) or not _contained(destination):
        findings.append("%s: member and destination must be contained relative paths" % (where,))
        return findings
    member_path = root / PACK_REL / member
    if means == DENY_MEANS:
        if platform not in vocab["deny_platforms"]:
            findings.append("%s: a deny-hook row needs a deny mechanism this repository documents and tests, "
                            "and %s PLATFORMS %s does not name %s; use the instructions tier"
                            % (where, GEN_HOOKS_REL, sorted(vocab["deny_platforms"]), platform))
        _read(member_path, "the deny-hook member")
        return findings
    location = vocab["locations"].get(platform)
    if location is None:
        findings.append("%s: no AIQT adapter generator names an instructions location for %s" % (where, platform))
        return findings
    kind, spot, generator = location
    if (kind == "file" and destination != spot) or (kind == "mdc" and not (
            destination.startswith(spot) and destination.endswith(".mdc"))):
        findings.append("%s: destination %r is not the location %s generates for this platform (%s%s)"
                        % (where, destination, generator, spot, "<name>.mdc" if kind == "mdc" else ""))
    parts = member.split("/")
    if parts[-1] in LOADED_NAMES or any(p.startswith(".cursor") for p in parts):
        findings.append("%s: member %r carries a name its platform loads; the pack tree would apply it to "
                        "sessions working on the pack" % (where, member))
    text = _text(member_path, "the instructions member")
    findings.extend(_member_findings(PACK_REL + "/" + member, text, block,
                                     vocab["frontmatter"] if kind == "mdc" else None))
    return findings


def evaluate(root, hook=None):
    """The findings for the tree at `root` (a list, empty when clean), or CannotEvaluate. `hook` is the
    loaded deny hook (loaded from `root` when None)."""
    root = Path(root)
    vocab = _vocabulary(root)
    if hook is None:
        hook = load_hook(root / HOOK_REL)
    block = render_block(hook, root)
    try:
        doc = tomllib.loads(_text(root / ROSTER_REL, "the platform roster"))
    except (tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        raise CannotEvaluate("%s does not parse (%s)" % (ROSTER_REL, exc))
    if set(doc) != frozenset(("format-version", "platform")) or doc.get("format-version") != 1 \
            or not isinstance(doc.get("platform"), list):
        raise CannotEvaluate("%s carries no format-version = 1 and [[platform]] rows only" % (ROSTER_REL,))
    findings = []
    seen = []
    for index, row in enumerate(doc["platform"], 1):
        where = "%s row %d" % (ROSTER_REL, index)
        if not isinstance(row, dict) or set(row) != ROW_KEYS:
            findings.append("%s: keys must be exactly %s" % (where, sorted(ROW_KEYS)))
            continue
        platform, means = row["platform"], row["means"]
        where = "%s (%s)" % (where, platform)
        if platform not in vocab["platforms"]:
            findings.append("%s: not a supported assistant platform %s" % (where, list(vocab["platforms"])))
            continue
        if platform in seen:
            findings.append("%s: duplicate platform row" % (where,))
            continue
        seen.append(platform)
        if means not in vocab["means"][platform]:
            findings.append("%s: means %r is not one %s allows %s"
                            % (where, means, platform, list(vocab["means"][platform])))
            continue
        findings.extend(_row_findings(root, vocab, block, where, row))
    if tuple(seen) != vocab["platforms"]:
        findings.append("%s: rows must cover %s once each, in that order (found %s)"
                        % (ROSTER_REL, list(vocab["platforms"]), seen))
    return findings


def main(argv):
    if argv:
        print("usage: check_opf_enforce_platforms.py [--self-test]", file=sys.stderr)
        return 2
    try:
        findings = evaluate(ROOT)
    except CannotEvaluate as exc:
        print("check_opf_enforce_platforms: CANNOT EVALUATE: %s" % (exc,))
        return 2
    for finding in findings:
        print("check_opf_enforce_platforms: %s" % (finding,))
    if findings:
        print("check_opf_enforce_platforms: FAIL (%d finding(s))" % (len(findings),))
        return 1
    print("check_opf_enforce_platforms: OK (%s covers every supported assistant platform; every instructions "
          "member carries the protected-path list rendered from %s)" % (ROSTER_REL, HOOK_REL))
    return 0


# --- self-test -----------------------------------------------------------------------------------------

CODEX = PACK_REL + "/codex/AGENTS.opf.md"
GEMINI = PACK_REL + "/gemini-cli/GEMINI.opf.md"
CURSOR = PACK_REL + "/cursor/opf.mdc"
INSTRUCTION_RESIDUALS = ('residuals = ["unverified-platform-denial", "shell-or-interpreter-wrapping", '
                         '"same-user-tampering"]')
REGISTRATION_LINE = "- `.claude/settings.json` (R8): the Claude Code hook registration of the product root.\n"


def _fixture(base, name):
    """A fresh copy of every input this gate reads, under base/name."""
    tree = Path(base) / name
    shutil.copytree(ROOT / PACK_REL, tree / PACK_REL)
    for rel in INPUT_RELS:
        if not rel.startswith(PACK_REL + "/"):
            (tree / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / rel, tree / rel)
    return tree


def _edit(tree, rel, old, new, count=1):
    """Replace `old` (which must occur exactly `count` times) by `new` in the fixture file `rel`."""
    path = tree / rel
    text = path.read_text(encoding="utf-8")
    if text.count(old) != count:
        raise AssertionError("fixture edit: %r occurs %d times in %s, expected %d"
                             % (old, text.count(old), rel, count))
    path.write_text(text.replace(old, new), encoding="utf-8")


def _append(tree, rel, extra):
    path = tree / rel
    path.write_text(path.read_text(encoding="utf-8") + extra, encoding="utf-8")


def _drop_cursor_row(tree):
    path = tree / ROSTER_REL
    text = path.read_text(encoding="utf-8")
    start = text.index('[[platform]]\nplatform = "cursor"')
    path.write_text(text[:start], encoding="utf-8")


def _codex_deny_hook(tree):
    _edit(tree, ROSTER_REL, 'means = "instructions"\nmember = "codex/AGENTS.opf.md"',
          'means = "deny-hook"\nmember = "codex/AGENTS.opf.md"')


def _codex_deny_hook_with_renderer(tree):
    _codex_deny_hook(tree)
    _edit(tree, GEN_HOOKS_REL, "PLATFORMS = " + chr(123) + '"claude-code"' + chr(125),
          "PLATFORMS = " + chr(123) + '"claude-code", "codex"' + chr(125))


def _loaded_name(tree):
    (tree / CODEX).rename(tree / PACK_REL / "codex" / "AGENTS.md")
    _edit(tree, ROSTER_REL, 'member = "codex/AGENTS.opf.md"', 'member = "codex/AGENTS.md"')


def _vectors():
    """(id, mutate(tree), expected substring or None for a clean result, exact finding count or None for a
    cannot-evaluate result). Each red vector removes or contradicts one rule; the clean vectors prove the
    rule does not fire on what it permits."""
    def edit(rel, old, new, count=1):
        return lambda tree: _edit(tree, rel, old, new, count)

    def append(rel, extra):
        return lambda tree: _append(tree, rel, extra)

    def remove(rel):
        return lambda tree: (tree / rel).unlink()

    differs = "differs from the list rendered"
    return (
        ("clean", lambda tree: None, None, 0),
        # one list: a platform member whose list drops or adds a line differs from the hook's list.
        ("codex-list-drops-line", edit(CODEX, REGISTRATION_LINE, ""), differs, 1),
        ("gemini-list-adds-line", edit(GEMINI, REGISTRATION_LINE, REGISTRATION_LINE + "- `docs/` (R9): x.\n"),
         differs, 1),
        ("cursor-list-drops-line", edit(CURSOR, REGISTRATION_LINE, ""), differs, 1),
        # one list: a hook change re-renders the block, so every member then differs from the hook.
        ("hook-registration-leaf", edit(HOOK_REL, 'REGISTRATION_LEAVES = frozenset(("settings.json", '
                                        '"settings.local.json"))', 'REGISTRATION_LEAVES = frozenset(('
                                        '"settings.json", "settings.local.json", "hooks.json"))'), differs, 3),
        ("hook-working-name", edit(HOOK_REL, 'WORKING = ".working"', 'WORKING = ".work"'), differs, 3),
        ("hook-archive", edit(HOOK_REL, 'ADOPTION_ARCHIVE = ("archive", "adoption")',
                              'ADOPTION_ARCHIVE = ("archive", "adopt")'), differs, 3),
        ("hook-frozen-dispositions", edit(HOOK_REL, 'FROZEN_DISPOSITIONS = ("migrate", "retire")',
                                          'FROZEN_DISPOSITIONS = ("migrate", "retire", "move")'), differs, 3),
        ("hook-writer-verbs", edit(HOOK_REL, 'WRITER_VERBS = frozenset(("record", "render"))',
                                   'WRITER_VERBS = frozenset(("record", "render", "import"))'), differs, 3),
        ("hook-leaf-pattern", edit(HOOK_REL, r'IMPORTED_LEAF_RE = re.compile(r"\A(worklog\.imported\.toml|',
                                   r'IMPORTED_LEAF_RE = re.compile(r"\A(worklog\.imported\.tom|'), differs, 3),
        ("hook-pack-tree", edit(HOOK_REL, 'tools = os.path.realpath(os.path.join(here, os.pardir, os.pardir, '
                                '"tools"))', 'tools = os.path.realpath(os.path.join(here, os.pardir, os.pardir, '
                                '"bin"))'), differs, 3),
        ("hook-unloadable", edit(HOOK_REL, "\nimport errno\n", "\nimport errno\nraise RuntimeError('broken')\n"),
         "CANNOT EVALUATE", None),
        ("member-markers-missing", edit(CODEX, END, ""), "marker pair", 1),
        # tier: the advisory line is required, and a denial claim outside it is a finding.
        ("tier-line-missing", edit(GEMINI, TIER_LINE + "\n", ""), "tier line", 1),
        ("tier-claim-codex", append(CODEX, "\nCodex blocks every write to the paths above.\n"),
         "nothing on this platform enforces", 1),
        ("tier-claim-cursor", append(CURSOR, "\nThis rule is enforced by Cursor.\n"),
         "nothing on this platform enforces", 1),
        ("tier-claim-code-span-exempt", append(CODEX, "\nThe Claude Code hook file is `pretooluse_deny.py`.\n"),
         None, 0),
        ("cursor-frontmatter", edit(CURSOR, "alwaysApply: true", "alwaysApply: false"), "frontmatter", 1),
        # deny hook: allowed only where the repository documents and tests a deny mechanism.
        ("deny-hook-codex", _codex_deny_hook, "needs a deny mechanism", 1),
        ("deny-hook-gemini", edit(ROSTER_REL, 'means = "instructions"\nmember = "gemini-cli/GEMINI.opf.md"',
                                  'means = "deny-hook"\nmember = "gemini-cli/GEMINI.opf.md"'),
         "needs a deny mechanism", 1),
        ("deny-hook-tested-renderer", _codex_deny_hook_with_renderer, None, 0),
        # roster: vocabulary, required residuals, evidence, coverage and order.
        ("means-outside-vocabulary", edit(ROSTER_REL, 'means = "instructions"\nmember = "cursor/opf.mdc"',
                                          'means = "ci-checks"\nmember = "cursor/opf.mdc"'),
         "is not one cursor allows", 1),
        ("residual-required-missing", edit(ROSTER_REL, INSTRUCTION_RESIDUALS,
                                           'residuals = ["shell-or-interpreter-wrapping"]', 3),
         "must disclose the residual 'unverified-platform-denial'", 3),
        ("residual-unknown", edit(ROSTER_REL, '"same-user-tampering"]\nevidence = "This repository documents and '
                                  'tests no Cursor', '"same-user-tampering", "nothing"]\nevidence = "This '
                                  'repository documents and tests no Cursor'), "residuals must be unique", 1),
        ("evidence-empty", edit(ROSTER_REL, 'evidence = "This repository documents and tests no Gemini CLI',
                                'evidence = " "\n# This repository documents and tests no Gemini CLI'),
         "carries no evidence", 1),
        ("platform-missing", _drop_cursor_row, "rows must cover", 1),
        ("platform-duplicate", edit(ROSTER_REL, 'platform = "gemini-cli"', 'platform = "codex"'),
         "duplicate platform row", 2),
        ("platform-unknown", edit(ROSTER_REL, 'platform = "cursor"', 'platform = "copilot"'),
         "not a supported assistant", 2),
        # location: the destination the AIQT adapter generator writes, and a member name no platform loads.
        ("destination-codex", edit(ROSTER_REL, 'destination = "AGENTS.md"', 'destination = "docs/AGENTS.md"'),
         "is not the location", 1),
        ("destination-gemini", edit(ROSTER_REL, 'destination = "GEMINI.md"', 'destination = ".gemini/GEMINI.md"'),
         "is not the location", 1),
        ("destination-cursor", edit(ROSTER_REL, 'destination = ".cursor/rules/opf.mdc"',
                                    'destination = ".cursor/opf.md"'), "is not the location", 1),
        ("member-loaded-name", _loaded_name, "carries a name its platform loads", 1),
        ("member-absent", remove(GEMINI), "CANNOT EVALUATE", None),
        ("member-not-contained", edit(ROSTER_REL, 'member = "cursor/opf.mdc"', 'member = "../cursor/opf.mdc"'),
         "contained relative paths", 1),
        ("roster-unparseable", append(ROSTER_REL, "\n[[platform\n"), "CANNOT EVALUATE", None),
        ("adopt-vocabulary-unreadable", remove(ADOPT_REL), "CANNOT EVALUATE", None),
        ("generator-literal-missing", edit(GEN_CURSOR_REL, "\nOUT_PARTS = ", "\nOUT_PARTS_X = "),
         "CANNOT EVALUATE", None),
    )


def self_test():
    failures = []
    runs = 0
    # The real tree's rendered block names each protected token, so a derivation that rendered an empty or
    # partial list would not pass for a clean one.
    try:
        block = render_block(load_hook(ROOT / HOOK_REL), ROOT)
    except CannotEvaluate as exc:
        block = ""
        failures.append("real-block: %s" % (exc,))
    for token in ("`**/.working/**`", "`.working/archive/adoption/<run-id>/**`", "`.working/imported/**`",
                  "`.working/imported/adoption/<run-id>/plan.toml`", "`migrate` or `retire`",
                  "`opf/enforcement/**`", "`opf/tools/**`", "`.claude/settings.json`",
                  "`.claude/settings.local.json`", "`opf record` and `opf render`"):
        runs += 1
        if token not in block:
            failures.append("real-block: the rendered list lacks %s" % (token,))
    with tempfile.TemporaryDirectory(prefix="opf-enforce-platforms-") as base:
        for case_id, mutate, expect, count in _vectors():
            runs += 1
            try:
                tree = _fixture(base, case_id)
                mutate(tree)
            except (AssertionError, OSError, ValueError) as exc:
                failures.append("%s: fixture setup failed (%s)" % (case_id, exc))
                continue
            try:
                got = evaluate(tree)
            except CannotEvaluate as exc:
                got = "CANNOT EVALUATE: %s" % (exc,)
            if expect is None:
                if got != []:
                    failures.append("%s: expected a clean result, got %r" % (case_id, got))
            elif count is None:
                if not isinstance(got, str) or expect not in got:
                    failures.append("%s: expected %r, got %r" % (case_id, expect, got))
            elif isinstance(got, str) or len(got) != count or not any(expect in f for f in got):
                failures.append("%s: expected %d finding(s), one containing %r, got %r"
                                % (case_id, count, expect, got))
    for failure in failures:
        print("SELF-TEST FAIL: %s" % (failure,))
    if failures:
        print("check_opf_enforce_platforms --self-test: FAIL (%d failure(s) over %d checks)" % (len(failures), runs))
        return 1
    print("check_opf_enforce_platforms --self-test: OK (%d checks)" % (runs,))
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main(sys.argv[1:]))
