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
              The roster document carries exactly format-version and the [[platform]] rows, and the
              format-version is the exact integer 1 (a boolean true or a float 1.0 is refused).
  deny hook   a deny-hook row is allowed only for a platform whose deny mechanism this repository
              documents and tests: a platform in tools/gen_hooks.py PLATFORMS, the only hook platform
              the AIQT pack renders and tests. Any other platform takes the instructions tier. The
              deny-hook row's member must be exactly the canonical hook path
              (opf/enforcement/claude/pretooluse_deny.py), the one file this gate loads, renders and
              probes. Any other path is a finding, a byte-identical copy included: the hook derives the
              pack trees it guards from its own location (__file__), so a relocated copy guards
              different directories than the hook this gate verified. The destination must be one of
              the hook registration paths the hook itself protects.
  location    an instructions row installs at the location the AIQT pack's own adapter generator writes
              for that platform: AGENTS.md for Codex (tools/gen_agents.py), GEMINI.md for Gemini CLI
              (tools/gen_adapters.py), a .mdc rule under .cursor/rules/ for Cursor (tools/gen_cursor.py),
              each read statically from that generator's own literals. The Cursor rule must lie outside
              the reserved subtree tools/gen_cursor.py rewrites and orphan-prunes (OUT_PARTS,
              .cursor/rules/aiqt-guardrails/, compared case-insensitively), where a regeneration would
              delete it. A platform with no such generator location has no instructions row. The
              member's own file name is not a name a platform loads (AGENTS.md, AGENTS.override.md,
              GEMINI.md, CLAUDE.md, anything under a .cursor directory), so the shipped pack tree does
              not apply its rules to sessions working on it.
  coexistence AGENTS.md and GEMINI.md are files the AIQT pack regenerates whole (tools/gen_agents.py,
              tools/gen_adapters.py), so the OPF instructions cannot coexist with the AIQT output there
              today. Each of those two members must state that, as the exact line COEXIST_LINE names
              for its generator and destination. This gate provides no composition of the two and
              verifies none; coexistence is an unresolved adoption prerequisite.
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
              (the hook's _registration_idents over a probe root; an identity the probe root does not
              contain cannot be rendered as a root-relative entry and REFUSES, never a silent drop),
              the sanctioned writer verbs (WRITER_VERBS) and the imported-series exemption
              (IMPORTED_LEAF_RE). A change to any of those in the hook changes the rendered block, so
              every platform's list then differs from the hook's and this gate is red until the
              members carry the new block.
  behaviour   the gate calls the LOADED hook's own file-tool rule (_file_tool_rule) directly, for the
              Write tool only, at one throwaway fixture product root: a Write to one concrete instance
              of every rendered protected-path entry must be denied, and a Write to the two rendered
              imported-series exemption leaves, to one keep-disposed plan source and to one unlisted
              file must be allowed. So a hook whose file-tool rule stops denying the probed instance of
              a rendered entry (a disposition no longer frozen, the store, evidence, archive, view,
              pack-tree or registration rule disabled) turns this gate red even where the hook
              constants, and so the block, are unchanged. The probe set is fixed: where the hook's
              constants leave a probe unbuildable (an exemption leaf its pattern does not match, no
              non-frozen disposition), and where the rule raises on a probe, the gate cannot evaluate
              (exit 2) rather than drop the probe. NOT probed here: the Edit, MultiEdit and
              NotebookEdit tools, the hook's main() dispatch and its deny output, every other fixture
              layout, and hook behaviour the block does not render (the Bash rules, the plain and
              coarse classifiers, R6/R7 handling, the control-subdirectory set and every other rule);
              opf/tools/check_opf_doctor.py launches the hook against its own denial vectors.
  Cursor      the Cursor member opens with the always-apply frontmatter tools/gen_cursor.py emits.

  check_opf_enforce_platforms.py              check the real tree
  check_opf_enforce_platforms.py --self-test  fixture copies of the inputs; each enforced rule above is
              held red by at least one fixture mutant, and each behavioural probe leg (store, evidence,
              archive, view, frozen source, pack tree, registration, exemption, keep-disposed source,
              unlisted file, and the refusal when the rule raises) by a hook rule-logic mutant that
              leaves every rendered constant unchanged.

Exit convention (the repository's gates): 0 clean, 1 a finding, 2 cannot evaluate (an unreadable or
malformed input, a hook that cannot be loaded, a generator literal that cannot be read).

DISCLOSED RESIDUALS. The rendered block derives every token the hook holds in a constant or computes in
the functions named above; the prose around those tokens and two literals the hook spells inline (the
manifest's views table and its target key) are fixed text in this gate, so a hook change to those two
literals alone is not seen here. The behavioural probes cover ONLY the rendered entries, the rendered
exemption leaves, one keep-disposed source and one unlisted file, each at one fixture layout and through
the file-tool rule for a Write alone: a hook change outside that surface (the other file tools, main()
dispatch and the deny output, a rule that holds only at the probed layout, the Bash rules, the
classifiers, the control-subdirectory set, R6/R7 handling) is not seen by this gate. The tier scan is
lexical: it reads words, not meaning, skips inline code spans and the rendered block, and cannot judge a
claim worded without the listed words. The deny-hook rule reads tools/gen_hooks.py PLATFORMS as the repository's
record of a tested deny mechanism; it does not itself verify a platform's documentation. Input files are
read after a symlinked-parent check and a regular-file check, so a file swapped between those checks and
the read is a check-to-use race. This gate checks the pack's text and the loaded hook's behaviour on
the probes above; it does not prove that any platform follows an instructions member, and it does not
make the Codex and Gemini CLI members coexist with the AIQT pack's generated AGENTS.md and GEMINI.md
(an unresolved adoption prerequisite, stated in those members and in the roster).
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_opf_enforce_platforms.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

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
# The line a member whose destination the AIQT pack regenerates whole must carry (module docstring,
# "coexistence"), filled with that generator and destination.
COEXIST_LINE = ("Coexistence is an unresolved adoption prerequisite: the AIQT pack's %s regenerates %s "
                "whole, so these contents cannot coexist with the AIQT instructions there today.")
# The two exemption leaves the behavioural probes write; the hook's IMPORTED_LEAF_RE must match both.
EXEMPTION_PROBE_LEAVES = ("worklog.imported.toml", "probe.imported.index.toml")
# File names a platform loads as instructions; a pack member never carries one (module docstring).
LOADED_NAMES = frozenset(("AGENTS.md", "AGENTS.override.md", "GEMINI.md", "CLAUDE.md"))
# A probe root no filesystem holds, for reading the registration identities the hook derives per root.
PROBE_ROOT = os.path.join(os.sep, "opf-protected-path-probe-root")


class CannotEvaluate(Exception):
    """An input this gate cannot read or interpret (exit 2)."""


def _input(root, rel, label):
    """The path of the input `rel` under `root`, refused when a component on the way is a symlink:
    the realpath of the joined path must equal the same join below the realpathed root, so an input
    reached through a symlinked parent (content read from outside the tree) is CannotEvaluate,
    never silently read."""
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


def _registration_rels(hook):
    """The hook registration entries as product-root-relative paths, from the hook's own
    _registration_idents over a probe root. An identity the probe root does not contain cannot be
    represented as a root-relative protected path, so it REFUSES (fail closed), never a silent drop:
    a protected identity must never disappear from the rendered list."""
    try:
        idents = hook._registration_idents([PROBE_ROOT])
    except Exception as exc:  # noqa: BLE001  a hook without this surface cannot be rendered
        raise CannotEvaluate("the Claude Code deny hook cannot derive its registration identities (%r)"
                             % (exc,))
    try:
        keys = list(idents)
    except Exception as exc:  # noqa: BLE001  identities that cannot be listed cannot be rendered
        raise CannotEvaluate("the Claude Code deny hook's registration identities cannot be listed (%r)"
                             % (exc,))
    prefix = PROBE_ROOT + os.sep
    rels = []
    for key in keys:
        # Judged on the normalised spelling, never lexically: "<probe>/.claude/../../x" starts with the
        # probe root but names a path outside it.
        if not isinstance(key, str) or os.path.normpath(key) != key or not key.startswith(prefix):
            raise CannotEvaluate("the Claude Code deny hook derives the registration identity %r, which is "
                                 "not a normalised path inside the probe root %s, so this gate cannot "
                                 "render it as a product-root-relative protected path; it refuses rather "
                                 "than dropping or misplacing a protected identity" % (key, PROBE_ROOT))
        rels.append(os.path.relpath(key, PROBE_ROOT).replace(os.sep, "/"))
    rels.sort()
    if not rels:
        raise CannotEvaluate("the Claude Code deny hook yields no registration path")
    return rels


def _pack_rels(hook, root):
    """The pack's own guarded trees, relative to the repository root (the pack, then the writer's
    tools), from the hook's _guarded_prefixes."""
    try:
        pack = [_relative(p, root) for p in hook._guarded_prefixes()]
    except CannotEvaluate:
        raise
    except Exception as exc:  # noqa: BLE001  a hook without this surface cannot be rendered
        raise CannotEvaluate("the Claude Code deny hook lacks a guarded-prefix member (%r)" % (exc,))
    if len(pack) != 2:
        raise CannotEvaluate("the Claude Code deny hook does not yield exactly its two guarded trees "
                             "(the pack, then the writer's tools)")
    return pack


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
    except CannotEvaluate:
        raise
    except Exception as exc:  # noqa: BLE001  a hook without these members cannot be rendered
        raise CannotEvaluate("the Claude Code deny hook lacks a protected-path member (%r)" % (exc,))
    if "render" not in verbs:
        raise CannotEvaluate("the Claude Code deny hook's WRITER_VERBS carries no render verb")
    writers = " and ".join("`opf %s`" % v for v in verbs)
    pack = _pack_rels(hook, root)
    registrations = _registration_rels(hook)
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
        "On Claude Code the registered deny hook refuses a direct write to each path on this list. "
        "tools/check_opf_enforce_platforms.py calls only that hook's file-tool rule, for a Write at one "
        "throwaway fixture layout, never the other file tools, the hook's entry point or its deny output; "
        "opf/tools/check_opf_doctor.py launches the hook against its own denial vectors. On every other "
        "platform nothing refuses such a write, and this list is advisory.",
        "",
        "Not on the list until the import writer ships: a leaf directly inside the machine store directory "
        "`%s/<machine>/` whose name matches `%s`." % (working, leaf_pattern),
        "",
        END,
    ])
    return "\n".join(lines)


def _probe_token(value, what):
    """A hook token destined for a fixture TOML document: refused unless it can be written verbatim
    inside a TOML basic string (no quote, backslash or control character)."""
    if not isinstance(value, str) or not value or chr(34) in value or chr(92) in value \
            or any(ord(c) < 0x20 for c in value):
        raise CannotEvaluate("the Claude Code deny hook %s %r cannot be written into the behavioural "
                             "fixture" % (what, value))
    return value


def _behaviour_findings(hook, root, pack_rels, registrations):
    """The behavioural probes (module docstring, "behaviour"): at a throwaway fixture product root, the
    loaded hook's own file-tool rule must DENY a Write to a concrete instance of every rendered
    protected-path entry, and ALLOW a Write to the rendered imported-series exemption leaves, to a
    keep-disposed plan source and to an unlisted file. The allow probes keep the deny probes honest: a
    hook that denied everything would satisfy them vacuously. The probe set never shrinks: a hook whose
    constants leave an allow probe unbuildable, or whose rule raises on a probe, cannot be evaluated."""
    try:
        rule = hook._file_tool_rule
        field = hook.FILE_TOOL_TARGET["Write"]
        working = _probe_token(hook.WORKING, "store-tree name")
        archive = tuple(hook.ADOPTION_ARCHIVE)
        evidence = tuple(hook.ADOPTION_EVIDENCE)
        plan_name = hook.PLAN_FILENAME
        plan_format = _probe_token(hook.PLAN_FORMAT, "plan format")
        frozen = tuple(hook.FROZEN_DISPOSITIONS)
        valid = frozenset(hook.VALID_DISPOSITIONS)
        standard = _probe_token(hook.MANIFEST_STANDARD, "manifest standard")
        leaf_re = hook.IMPORTED_LEAF_RE
    except CannotEvaluate:
        raise
    except Exception as exc:  # noqa: BLE001  a hook without this surface cannot be probed
        raise CannotEvaluate("the Claude Code deny hook lacks the file-tool rule surface this gate "
                             "probes behaviourally (%r)" % (exc,))
    for leaf in EXEMPTION_PROBE_LEAVES:
        try:
            matched = leaf_re.match(leaf)
        except Exception as exc:  # noqa: BLE001  a pattern that cannot match cannot be probed
            raise CannotEvaluate("the Claude Code deny hook's IMPORTED_LEAF_RE cannot be applied (%r)" % (exc,))
        if not matched:
            raise CannotEvaluate("the Claude Code deny hook's IMPORTED_LEAF_RE does not match the exemption "
                                 "probe leaf %r, so the rendered exemption cannot be probed; the probe set "
                                 "never shrinks silently" % (leaf,))
    kept = [d for d in sorted(valid - set(frozen), key=repr) if isinstance(d, str)][:1]
    if not kept:
        raise CannotEvaluate("the Claude Code deny hook's VALID_DISPOSITIONS carries no non-frozen string "
                             "disposition, so no keep-disposed plan source can be probed; the probe set "
                             "never shrinks silently")
    findings = []
    with tempfile.TemporaryDirectory(prefix="opf-enforce-behaviour-") as base:
        proot = os.path.join(os.path.realpath(base), "product")
        machine = os.path.join(proot, working, "m1")
        run_home = os.path.join(proot, working, *(evidence + ("run1",)))
        arch_home = os.path.join(proot, working, *(archive + ("run1",)))
        try:
            os.makedirs(machine)
            os.makedirs(run_home)
            os.makedirs(arch_home)
            plan = ["format = \"%s\"" % (plan_format,), ""]
            for d in frozen:
                _probe_token(d, "frozen disposition")
                plan += ["[[sources]]", "path = \"frozen-%s.md\"" % (d,),
                         "disposition = \"%s\"" % (d,), ""]
            _probe_token(kept[0], "disposition")
            plan += ["[[sources]]", "path = \"kept.md\"", "disposition = \"%s\"" % (kept[0],), ""]
            Path(run_home, plan_name).write_text("\n".join(plan), encoding="utf-8")
            Path(machine, "manifest.toml").write_text(
                "[%s]\nstandard = \"%s\"\n\n[views.probe]\ntarget = \"VIEW-PROBE.md\"\n"
                % (standard, standard), encoding="utf-8")
        except (OSError, TypeError, ValueError) as exc:
            raise CannotEvaluate("the behavioural fixture cannot be built from the hook's constants "
                                 "(%r)" % (exc,))
        probes = [
            (os.path.join(machine, "record-probe.toml"), "the store tree (R1)", True),
            (os.path.join(proot, working, evidence[0], "evidence-probe"), "the evidence home (R1)", True),
            (os.path.join(arch_home, "original-probe"), "the adoption archive (R2)", True),
            (os.path.join(proot, "VIEW-PROBE.md"), "the declared view (R4)", True),
        ]
        for d in frozen:
            probes.append((os.path.join(proot, "frozen-%s.md" % (d,)),
                           "the %s-disposed plan source (R3)" % (d,), True))
        for rel in pack_rels:
            probes.append((os.path.join(str(root), *(rel.split("/") + ["pack-probe"])),
                           "the pack tree %s (R8)" % (rel,), True))
        for rel in registrations:
            probes.append((os.path.join(proot, *rel.split("/")),
                           "the hook registration %s (R8)" % (rel,), True))
        for leaf in EXEMPTION_PROBE_LEAVES:
            probes.append((os.path.join(machine, leaf), "the rendered imported-series exemption", False))
        probes.append((os.path.join(proot, "kept.md"), "a keep-disposed plan source", False))
        probes.append((os.path.join(proot, "unlisted-probe.md"), "an unlisted file", False))
        for target, entry, expect_deny in probes:
            try:
                reason = rule("Write", {field: target}, proot)
            except Exception as exc:  # noqa: BLE001  a rule that cannot judge the probe
                raise CannotEvaluate("the Claude Code deny hook's file-tool rule fails on the "
                                     "behavioural probe %s (%r)" % (target, exc))
            if expect_deny and reason is None:
                findings.append("behaviour: the loaded hook allows the probe write to %s, but the "
                                "rendered protected-path list names %s; the members would overstate "
                                "what the hook denies on Claude Code" % (target, entry))
            elif not expect_deny and reason is not None:
                findings.append("behaviour: the loaded hook denies the probe write to %s, which the "
                                "rendered list leaves unprotected (%s); the rendered list would "
                                "understate the hook" % (target, entry))
    return findings


def _vocabulary(root):
    adopt = _literals(_input(root, ADOPT_REL, "the vocabulary source"),
                      ("ENFORCEMENT_MEANS", "ENFORCEMENT_RESIDUALS", "ENFORCEMENT_REQUIRED_RESIDUALS"))
    hooks = _literals(_input(root, GEN_HOOKS_REL, "the hook-platform source"), ("PLATFORMS",))
    agents = _literals(_input(root, GEN_AGENTS_REL, "the Codex adapter generator"), ("GENSRC_OUTPUTS",))
    adapters = _literals(_input(root, GEN_ADAPTERS_REL, "the Gemini CLI adapter generator"),
                         ("GENSRC_OUTPUTS",))
    cursor = _literals(_input(root, GEN_CURSOR_REL, "the Cursor adapter generator"),
                       ("OUT_PARTS", "FRONTMATTER"))
    means = adopt["ENFORCEMENT_MEANS"]
    if not isinstance(means, dict) or not all(isinstance(v, tuple) for v in means.values()) \
            or not all(isinstance(m, str) for v in means.values() for m in v):
        raise CannotEvaluate("%s ENFORCEMENT_MEANS is not a table of string tuples" % (ADOPT_REL,))
    residuals = adopt["ENFORCEMENT_RESIDUALS"]
    if not isinstance(residuals, tuple) or not all(isinstance(r, str) for r in residuals):
        raise CannotEvaluate("%s ENFORCEMENT_RESIDUALS is not a tuple of strings" % (ADOPT_REL,))
    required = adopt["ENFORCEMENT_REQUIRED_RESIDUALS"]
    if not isinstance(required, dict) or not all(isinstance(v, tuple) for v in required.values()) \
            or not all(isinstance(r, str) for v in required.values() for r in v):
        raise CannotEvaluate("%s ENFORCEMENT_REQUIRED_RESIDUALS is not a table of string tuples"
                             % (ADOPT_REL,))
    deny_platforms = hooks["PLATFORMS"]
    if not isinstance(deny_platforms, (set, frozenset, tuple)) \
            or not all(isinstance(p, str) for p in deny_platforms):
        raise CannotEvaluate("%s PLATFORMS is not a set or tuple of strings" % (GEN_HOOKS_REL,))
    frontmatter = cursor["FRONTMATTER"]
    if not isinstance(frontmatter, str) or not frontmatter:
        raise CannotEvaluate("%s FRONTMATTER is not a non-empty string" % (GEN_CURSOR_REL,))

    def target(outputs, leaf, rel):
        if not isinstance(outputs, (list, tuple)):
            raise CannotEvaluate("%s GENSRC_OUTPUTS is not a sequence of output tables" % (rel,))
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
        residuals=residuals,
        required=required,
        deny_platforms=deny_platforms,
        # (kind, location, generator, the subtree that generator rewrites and prunes, or None)
        locations=dict((
            ("codex", ("file", target(agents["GENSRC_OUTPUTS"], "AGENTS.md", GEN_AGENTS_REL), GEN_AGENTS_REL,
                       None)),
            ("gemini-cli", ("file", target(adapters["GENSRC_OUTPUTS"], "GEMINI.md", GEN_ADAPTERS_REL),
                            GEN_ADAPTERS_REL, None)),
            ("cursor", ("mdc", "/".join(parts[:2]) + "/", GEN_CURSOR_REL, "/".join(parts) + "/")),
        )),
        frontmatter=frontmatter,
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
    member_path = _input(root, PACK_REL + "/" + member, "the member")
    if means == DENY_MEANS:
        if platform not in vocab["deny_platforms"]:
            findings.append("%s: a deny-hook row needs a deny mechanism this repository documents and tests, "
                            "and %s PLATFORMS %s does not name %s; use the instructions tier"
                            % (where, GEN_HOOKS_REL, sorted(vocab["deny_platforms"]), platform))
        if PACK_REL + "/" + member != HOOK_REL:
            findings.append("%s: member %r is not %r, the canonical deny hook path %s: the roster's "
                            "deny-hook member must be the hook this gate loads, renders and probes, never "
                            "a copy (the hook derives its guarded trees from its own location, so even a "
                            "byte-identical copy elsewhere guards different directories)"
                            % (where, member, HOOK_REL[len(PACK_REL) + 1:], HOOK_REL))
        if destination not in vocab["registrations"]:
            findings.append("%s: destination %r is not a hook registration path the deny hook itself "
                            "protects %s" % (where, destination, vocab["registrations"]))
        return findings
    location = vocab["locations"].get(platform)
    if location is None:
        findings.append("%s: no AIQT adapter generator names an instructions location for %s" % (where, platform))
        return findings
    kind, spot, generator, pruned = location
    if (kind == "file" and destination != spot) or (kind == "mdc" and not (
            destination.startswith(spot) and destination.endswith(".mdc"))):
        findings.append("%s: destination %r is not the location %s generates for this platform (%s%s)"
                        % (where, destination, generator, spot, "<name>.mdc" if kind == "mdc" else ""))
    elif pruned is not None and destination.casefold().startswith(pruned.casefold()):
        findings.append("%s: destination %r lies inside %s, the subtree %s rewrites and orphan-prunes, so a "
                        "regeneration would delete it; install it under %s outside that subtree"
                        % (where, destination, pruned, generator, spot))
    parts = member.split("/")
    if parts[-1] in LOADED_NAMES or any(p.startswith(".cursor") for p in parts):
        findings.append("%s: member %r carries a name its platform loads; the pack tree would apply it to "
                        "sessions working on the pack" % (where, member))
    text = _text(member_path, "the instructions member")
    findings.extend(_member_findings(PACK_REL + "/" + member, text, block,
                                     vocab["frontmatter"] if kind == "mdc" else None))
    if kind == "file" and text.split("\n").count(COEXIST_LINE % (generator, spot)) != 1:
        findings.append("%s: does not state exactly once, on a line of its own, that %s regenerates %s whole "
                        "and coexistence is unresolved: %r"
                        % (PACK_REL + "/" + member, generator, spot, COEXIST_LINE % (generator, spot)))
    return findings


def evaluate(root, hook=None):
    """The findings for the tree at `root` (a list, empty when clean), or CannotEvaluate. `hook` is the
    loaded deny hook (loaded from `root` when None)."""
    root = Path(root)
    vocab = _vocabulary(root)
    if hook is None:
        hook = load_hook(_input(root, HOOK_REL, "the Claude Code deny hook"))
    registrations = _registration_rels(hook)
    vocab["registrations"] = registrations
    pack_rels = _pack_rels(hook, root)
    block = render_block(hook, root)
    findings = _behaviour_findings(hook, root, pack_rels, registrations)
    try:
        doc = tomllib.loads(_text(_input(root, ROSTER_REL, "the platform roster"), "the platform roster"))
    except (tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        raise CannotEvaluate("%s does not parse (%s)" % (ROSTER_REL, exc))
    version = doc.get("format-version")
    if set(doc) != frozenset(("format-version", "platform")) or type(version) is not int \
            or version != 1 or not isinstance(doc.get("platform"), list):
        raise CannotEvaluate("%s carries no exact integer format-version = 1 and [[platform]] rows only "
                             "(a boolean true, a float 1.0 or another near-1 spelling is refused)"
                             % (ROSTER_REL,))
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
          "member carries the protected-path list rendered from %s, and the loaded hook's file-tool rule "
          "denies a Write to each rendered entry on the behavioural probes)" % (ROSTER_REL, HOOK_REL))
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


def _swap_codex_gemini_rows(tree):
    path = tree / ROSTER_REL
    text = path.read_text(encoding="utf-8")
    c = text.index('[[platform]]\nplatform = "codex"')
    g = text.index('[[platform]]\nplatform = "gemini-cli"')
    u = text.index('[[platform]]\nplatform = "cursor"')
    path.write_text(text[:c] + text[g:u] + text[c:g] + text[u:], encoding="utf-8")


def _codex_deny_hook(tree):
    _edit(tree, ROSTER_REL, 'means = "instructions"\nmember = "codex/AGENTS.opf.md"',
          'means = "deny-hook"\nmember = "codex/AGENTS.opf.md"')


def _codex_deny_hook_with_renderer(tree):
    _codex_deny_hook(tree)
    _edit(tree, GEN_HOOKS_REL, "PLATFORMS = " + chr(123) + '"claude-code"' + chr(125),
          "PLATFORMS = " + chr(123) + '"claude-code", "codex"' + chr(125))
    _edit(tree, ROSTER_REL,
          'means = "deny-hook"\nmember = "codex/AGENTS.opf.md"\ndestination = "AGENTS.md"',
          'means = "deny-hook"\nmember = "claude/pretooluse_deny.py"\ndestination = ".claude/settings.json"')


def _deny_member_byte_copy(tree):
    shutil.copyfile(tree / HOOK_REL, tree / PACK_REL / "claude" / "pretooluse_deny_copy.py")
    _edit(tree, ROSTER_REL, 'member = "claude/pretooluse_deny.py"',
          'member = "claude/pretooluse_deny_copy.py"')


RELOCATED_HOOK_REL = PACK_REL + "/codex/nested/hook.py"


def _deny_member_relocated_copy(tree):
    """The unchanged hook bytes at another depth, named as the deny-hook member: the copy derives its
    guarded trees from its own location, so it guards different directories (self_test checks that)."""
    (tree / RELOCATED_HOOK_REL).parent.mkdir(parents=True)
    shutil.copyfile(tree / HOOK_REL, tree / RELOCATED_HOOK_REL)
    _edit(tree, ROSTER_REL, 'member = "claude/pretooluse_deny.py"',
          'member = "%s"' % (RELOCATED_HOOK_REL[len(PACK_REL) + 1:],))


def _loaded_name(tree):
    (tree / CODEX).rename(tree / PACK_REL / "codex" / "AGENTS.md")
    _edit(tree, ROSTER_REL, 'member = "codex/AGENTS.opf.md"', 'member = "codex/AGENTS.md"')


def _dot_cursor_member(tree):
    nest = tree / PACK_REL / "cursor" / ".cursorext"
    nest.mkdir()
    (tree / CURSOR).rename(nest / "opf.mdc")
    _edit(tree, ROSTER_REL, 'member = "cursor/opf.mdc"', 'member = "cursor/.cursorext/opf.mdc"')


def _member_directory(tree):
    (tree / GEMINI).unlink()
    (tree / GEMINI).mkdir()


def _symlinked_member_parent(tree):
    real = tree / PACK_REL / "codex-real"
    (tree / PACK_REL / "codex").rename(real)
    os.symlink("codex-real", tree / PACK_REL / "codex")


def _vectors():
    """(id, mutate(tree), expected substring or None for a clean result, exact finding count or None for a
    cannot-evaluate result). Each red vector contradicts at least one rule this gate enforces and pins
    the exact finding count plus a message substring, so a weakened rule that stops reporting it turns
    the self-test red; the clean vectors prove the named permissions fire no finding. The vectors mutate
    fixture INPUTS only: what each one demonstrates is the gate's response to that mutated input; the
    behavioural vectors name the probe leg each one holds, and the full record of which gate-code
    mutation each vector catches lives in the PR evidence."""
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
        ("hook-leaf-pattern", edit(HOOK_REL, r'[A-Za-z0-9_-]+\.imported\.index\.toml)',
                                   r'[A-Za-z0-9_.-]+\.imported\.index\.toml)'), differs, 3),
        ("hook-pack-tree", edit(HOOK_REL, 'tools = os.path.realpath(os.path.join(here, os.pardir, os.pardir, '
                                '"tools"))', 'tools = os.path.realpath(os.path.join(here, os.pardir, os.pardir, '
                                '"bin"))'), differs, 3),
        ("hook-unloadable", edit(HOOK_REL, "\nimport errno\n", "\nimport errno\nraise RuntimeError('broken')\n"),
         "CANNOT EVALUATE", None),
        # behaviour: a hook whose RULE LOGIC stops matching a rendered entry leaves every constant, and
        # so the block, unchanged; only the behavioural probes turn these red. Each probe leg of
        # _behaviour_findings is held by the vector named beside it: dropping that leg from the gate
        # changes the vector's exact finding count, so the self-test turns red.
        ("hook-store-logic", edit(HOOK_REL, '    return ("direct edits under %s/ are denied:',
                                  '    return None and ("direct edits under %s/ are denied:'),
         "allows the probe write", 1),  # the store-tree probe
        ("hook-evidence-logic", edit(HOOK_REL, '        return ("direct writes under %s/imported/ are denied:',
                                     '        return None and ("direct writes under %s/imported/ are denied:'),
         "allows the probe write", 1),  # the evidence-home probe
        ("hook-archive-logic", edit(HOOK_REL, '        return ("writes under %s/archive/adoption/<run-id>/ are',
                                    '        return None and ("writes under %s/archive/adoption/<run-id>/ are'),
         "allows the probe write", 1),  # the adoption-archive probe
        ("hook-frozen-logic", edit(HOOK_REL, "            if disposition in FROZEN_DISPOSITIONS:",
                                   '            if disposition == "retire":'),
         "allows the probe write", 1),  # the migrate-disposed source probe
        ("hook-retire-logic", edit(HOOK_REL, "            if disposition in FROZEN_DISPOSITIONS:",
                                   '            if disposition == "migrate":'),
         "allows the probe write", 1),  # the retire-disposed source probe
        ("hook-views-logic", edit(HOOK_REL, "if cand in views[0]:", "if cand in ():", 3),
         "allows the probe write", 1),  # the declared-view probe
        ("hook-guard-logic", edit(HOOK_REL, "        if candidate == prefix or candidate.startswith(prefix + os.sep):",
                                  "        if False:"),
         "allows the probe write", 2),  # the two pack-tree probes
        ("hook-registration-logic", edit(HOOK_REL, "    hit = idents.get(candidate)",
                                         "    hit = idents.get(candidate) and None"),
         "allows the probe write", 2),  # the registration probes
        ("hook-exemption-logic", edit(HOOK_REL, "    if (len(after) == 2 and",
                                      "    if (False and len(after) == 2 and"),
         "denies the probe write", 2),  # the two exemption-leaf probes
        ("hook-every-source-frozen", edit(HOOK_REL, "            if disposition in FROZEN_DISPOSITIONS:",
                                          "            if disposition in VALID_DISPOSITIONS:"),
         "denies the probe write", 1),  # the keep-disposed source probe
        ("hook-deny-all", edit(HOOK_REL, "    return None\n\n\ndef _plain_command_word",
                               '    return "deny-all mutant"\n\n\ndef _plain_command_word'),
         "denies the probe write", 4),  # every allow probe, the unlisted-file probe among them
        ("hook-rule-raises", edit(HOOK_REL, "    field = FILE_TOOL_TARGET[tool_name]\n",
                                  "    field = FILE_TOOL_TARGET[tool_name]\n    raise RuntimeError('mutant')\n"),
         "fails on the behavioural probe", None),  # the refusal when the rule raises
        # behaviour: a hook whose constants leave a probe unbuildable refuses; the set never shrinks.
        ("hook-leaf-pattern-unprobeable", edit(HOOK_REL, r'IMPORTED_LEAF_RE = re.compile(r"\A(worklog\.imported'
                                               r'\.toml|', r'IMPORTED_LEAF_RE = re.compile(r"\A(worklog\.imported'
                                               r'\.tom|'), "does not match the exemption probe leaf", None),
        ("hook-no-kept-disposition", edit(HOOK_REL, 'VALID_DISPOSITIONS = frozenset(("keep", "move", "migrate", '
                                          '"retire"))', 'VALID_DISPOSITIONS = frozenset(("migrate", "retire"))'),
         "no non-frozen string disposition", None),
        # the rendered registration list is sorted, so the hook's iteration order leaves the block unchanged.
        ("hook-registration-order", edit(HOOK_REL, "        for base in sorted(REGISTRATION_LEAVES):",
                                         "        for base in sorted(REGISTRATION_LEAVES, reverse=True):"),
         None, 0),
        # renderer refusals: a hook surface the renderer cannot represent is cannot-evaluate.
        ("hook-registration-outside-probe", edit(HOOK_REL, 'REGISTRATION_LEAVES = frozenset(("settings.json", '
                                                 '"settings.local.json"))', 'REGISTRATION_LEAVES = frozenset(('
                                                 '"settings.json", "settings.local.json", '
                                                 '"../../outside-settings.json"))'),
         "not a normalised path inside the probe root", None),
        ("hook-no-render-verb", edit(HOOK_REL, 'WRITER_VERBS = frozenset(("record", "render"))',
                                     'WRITER_VERBS = frozenset(("record",))'), "no render verb", None),
        ("hook-one-guarded-tree", edit(HOOK_REL, "    return (pack, tools)", "    return (pack,)"),
         "two guarded trees", None),
        ("hook-tree-outside-root", edit(HOOK_REL, 'tools = os.path.realpath(os.path.join(here, os.pardir, '
                                        'os.pardir, "tools"))', "tools = os.path.realpath(os.sep)"),
         "outside the repository root", None),
        ("member-markers-missing", edit(CODEX, END, ""), "marker pair", 2),
        ("marker-begin-duplicate", append(CODEX, "\n" + BEGIN + "\n"), "marker pair", 2),
        ("marker-end-duplicate", append(CODEX, "\n" + END + "\n"), "marker pair", 2),
        # tier: the advisory line is required exactly once, and a denial claim outside it is a finding.
        ("tier-line-missing", edit(GEMINI, TIER_LINE + "\n", ""), "tier line", 1),
        ("tier-line-twice", append(GEMINI, "\n" + TIER_LINE + "\n"), "tier line", 1),
        ("tier-claim-codex", append(CODEX, "\nCodex blocks every write to the paths above.\n"),
         "nothing on this platform enforces", 1),
        ("tier-claim-cursor", append(CURSOR, "\nThis rule is enforced by Cursor.\n"),
         "nothing on this platform enforces", 1),
        ("tier-claim-code-span-exempt", append(CODEX, "\nThe Claude Code hook file is `pretooluse_deny.py`.\n"),
         None, 0),
        ("cursor-frontmatter", edit(CURSOR, "alwaysApply: true", "alwaysApply: false"), "frontmatter", 1),
        # deny hook: allowed only where the repository documents and tests a deny mechanism, and the
        # member must be the canonical hook path exactly, at a registration destination.
        ("deny-hook-codex", _codex_deny_hook, "needs a deny mechanism", 3),
        ("deny-hook-gemini", edit(ROSTER_REL, 'means = "instructions"\nmember = "gemini-cli/GEMINI.opf.md"',
                                  'means = "deny-hook"\nmember = "gemini-cli/GEMINI.opf.md"'),
         "needs a deny mechanism", 3),
        ("deny-hook-tested-renderer", _codex_deny_hook_with_renderer, None, 0),
        ("deny-member-not-hook", edit(ROSTER_REL, 'member = "claude/pretooluse_deny.py"',
                                      'member = "codex/AGENTS.opf.md"'),
         "the canonical deny hook path", 1),
        ("deny-member-byte-identical-copy", _deny_member_byte_copy, "the canonical deny hook path", 1),
        ("deny-member-relocated-copy", _deny_member_relocated_copy, "the canonical deny hook path", 1),
        ("deny-destination-unpinned", edit(ROSTER_REL, 'destination = ".claude/settings.json"',
                                           'destination = "README.md"'),
         "is not a hook registration path", 1),
        ("deny-member-missing", edit(ROSTER_REL, 'member = "claude/pretooluse_deny.py"',
                                     'member = "claude/no-such-hook.py"'),
         "the canonical deny hook path", 1),
        # roster: vocabulary, required residuals, evidence, coverage, order, keys and the exact schema.
        ("means-outside-vocabulary", edit(ROSTER_REL, 'means = "instructions"\nmember = "cursor/opf.mdc"',
                                          'means = "ci-checks"\nmember = "cursor/opf.mdc"'),
         "is not one cursor allows", 1),
        ("residual-required-missing", edit(ROSTER_REL, INSTRUCTION_RESIDUALS,
                                           'residuals = ["shell-or-interpreter-wrapping"]', 3),
         "must disclose the residual 'unverified-platform-denial'", 3),
        ("residual-unknown", edit(ROSTER_REL, '"same-user-tampering"]\nevidence = "This repository documents and '
                                  'tests no Cursor', '"same-user-tampering", "nothing"]\nevidence = "This '
                                  'repository documents and tests no Cursor'), "residuals must be unique", 1),
        ("residual-duplicate", edit(ROSTER_REL, '"same-user-tampering"]\nevidence = "This repository documents and '
                                    'tests no Cursor', '"same-user-tampering", "same-user-tampering"]\n'
                                    'evidence = "This repository documents and tests no Cursor'),
         "residuals must be unique", 1),
        ("evidence-empty", edit(ROSTER_REL, 'evidence = "This repository documents and tests no Gemini CLI',
                                'evidence = " "\n# This repository documents and tests no Gemini CLI'),
         "carries no evidence", 1),
        ("platform-missing", _drop_cursor_row, "rows must cover", 1),
        ("platform-order-swapped", _swap_codex_gemini_rows, "in that order", 1),
        ("platform-duplicate", edit(ROSTER_REL, 'platform = "gemini-cli"', 'platform = "codex"'),
         "duplicate platform row", 2),
        ("platform-unknown", edit(ROSTER_REL, 'platform = "cursor"', 'platform = "copilot"'),
         "not a supported assistant", 2),
        ("row-extra-key", edit(ROSTER_REL, 'platform = "cursor"\nmeans',
                               'platform = "cursor"\nnotes = "x"\nmeans'), "keys must be exactly", 2),
        ("roster-format-version-bool", edit(ROSTER_REL, "format-version = 1\n", "format-version = true\n"),
         "exact integer format-version", None),
        ("roster-format-version-float", edit(ROSTER_REL, "format-version = 1\n", "format-version = 1.0\n"),
         "exact integer format-version", None),
        ("roster-extra-top-table", append(ROSTER_REL, "\n[extra]\nx = 1\n"),
         "exact integer format-version", None),
        ("roster-oversize", append(ROSTER_REL, "\n# " + "x" * MAX_INPUT_BYTES + "\n"), "exceeds", None),
        # location: the destination the AIQT adapter generator writes, and a member name no platform loads.
        ("destination-codex", edit(ROSTER_REL, 'destination = "AGENTS.md"', 'destination = "docs/AGENTS.md"'),
         "is not the location", 1),
        ("destination-gemini", edit(ROSTER_REL, 'destination = "GEMINI.md"', 'destination = ".gemini/GEMINI.md"'),
         "is not the location", 1),
        ("destination-cursor", edit(ROSTER_REL, 'destination = ".cursor/rules/opf.mdc"',
                                    'destination = ".cursor/opf.md"'), "is not the location", 1),
        ("destination-not-contained", edit(ROSTER_REL, 'destination = ".cursor/rules/opf.mdc"',
                                           'destination = "/etc/opf.mdc"'), "contained relative paths", 1),
        # location: a Cursor rule inside the subtree tools/gen_cursor.py orphan-prunes would be deleted.
        ("destination-cursor-pruned-subtree", edit(ROSTER_REL, 'destination = ".cursor/rules/opf.mdc"',
                                                   'destination = ".cursor/rules/aiqt-guardrails/opf.mdc"'),
         "orphan-prunes", 1),
        ("destination-cursor-pruned-subtree-case", edit(ROSTER_REL, 'destination = ".cursor/rules/opf.mdc"',
                                                        'destination = ".cursor/rules/AIQT-Guardrails/opf.mdc"'),
         "orphan-prunes", 1),
        ("destination-cursor-beside-subtree", edit(ROSTER_REL, 'destination = ".cursor/rules/opf.mdc"',
                                                   'destination = ".cursor/rules/aiqt-guardrails-opf/opf.mdc"'),
         None, 0),
        # coexistence: a member whose destination the AIQT pack regenerates whole states so exactly once.
        ("coexist-line-missing", edit(CODEX, COEXIST_LINE % (GEN_AGENTS_REL, "AGENTS.md") + "\n", ""),
         "coexistence is unresolved", 1),
        ("coexist-line-wrong-generator", edit(GEMINI, COEXIST_LINE % (GEN_ADAPTERS_REL, "GEMINI.md"),
                                              COEXIST_LINE % (GEN_AGENTS_REL, "GEMINI.md")),
         "coexistence is unresolved", 1),
        ("coexist-line-twice", append(CODEX, "\n" + COEXIST_LINE % (GEN_AGENTS_REL, "AGENTS.md") + "\n"),
         "coexistence is unresolved", 1),
        # coexistence: the line counts only on a line of its own, and only naming the member's destination.
        ("coexist-line-embedded", edit(CODEX, COEXIST_LINE % (GEN_AGENTS_REL, "AGENTS.md"),
                                       "Note: " + COEXIST_LINE % (GEN_AGENTS_REL, "AGENTS.md")),
         "coexistence is unresolved", 1),
        ("coexist-line-wrong-destination-codex", edit(CODEX, COEXIST_LINE % (GEN_AGENTS_REL, "AGENTS.md"),
                                                      COEXIST_LINE % (GEN_AGENTS_REL, "GEMINI.md")),
         "coexistence is unresolved", 1),
        ("coexist-line-wrong-destination-gemini", edit(GEMINI, COEXIST_LINE % (GEN_ADAPTERS_REL, "GEMINI.md"),
                                                       COEXIST_LINE % (GEN_ADAPTERS_REL, "AGENTS.md")),
         "coexistence is unresolved", 1),
        ("member-loaded-name", _loaded_name, "carries a name its platform loads", 1),
        ("member-dot-cursor-component", _dot_cursor_member, "carries a name its platform loads", 1),
        ("member-absent", remove(GEMINI), "CANNOT EVALUATE", None),
        ("member-is-directory", _member_directory, "is not a regular file", None),
        ("member-symlinked-parent", _symlinked_member_parent, "through a symlink", None),
        ("member-not-contained", edit(ROSTER_REL, 'member = "cursor/opf.mdc"', 'member = "../cursor/opf.mdc"'),
         "contained relative paths", 1),
        ("roster-unparseable", append(ROSTER_REL, "\n[[platform\n"), "CANNOT EVALUATE", None),
        # vocabulary and generator literals: absent, doubled or wrongly shaped ones are cannot-evaluate.
        ("adopt-vocabulary-unreadable", remove(ADOPT_REL), "CANNOT EVALUATE", None),
        ("adopt-means-not-table", edit(ADOPT_REL, 'ENFORCEMENT_MEANS = {\n    "ci": ("ci-checks",),',
                                       'ENFORCEMENT_MEANS = {\n    "ci": "ci-checks",'),
         "is not a table of", None),
        ("adopt-required-not-table", edit(ADOPT_REL, "ENFORCEMENT_REQUIRED_RESIDUALS = {",
                                          "ENFORCEMENT_REQUIRED_RESIDUALS = 0\n_IGNORED_RR = {"),
         "ENFORCEMENT_REQUIRED_RESIDUALS", None),
        ("gen-agents-outputs-not-sequence", edit(GEN_AGENTS_REL, "\nGENSRC_OUTPUTS = (",
                                                 "\nGENSRC_OUTPUTS = 7\n_IGNORED_GO = ("),
         "GENSRC_OUTPUTS is not a sequence", None),
        ("gen-agents-no-single-output", edit(GEN_AGENTS_REL, '{"target": "AGENTS.md", "kind": "file",',
                                             '{"target": "AGENTS2.md", "kind": "file",'),
         "declares no single", None),
        ("gen-hooks-doubled-literal", append(GEN_HOOKS_REL, "\nPLATFORMS = 0\n"), "more than once", None),
        ("gen-cursor-frontmatter-not-string", edit(GEN_CURSOR_REL, "\nFRONTMATTER = ",
                                                   "\nFRONTMATTER = 5\n_IGNORED_FM = "),
         "FRONTMATTER is not a non-empty string", None),
        ("gen-cursor-out-parts-not-tuple", edit(GEN_CURSOR_REL, "\nOUT_PARTS = ",
                                                "\nOUT_PARTS = 5\n_IGNORED_OP = "),
         "OUT_PARTS is not a tuple", None),
        ("generator-literal-missing", edit(GEN_CURSOR_REL, "\nOUT_PARTS = ", "\nOUT_PARTS_X = "),
         "CANNOT EVALUATE", None),
    )


def self_test():
    failures = []
    runs = 0
    # The real tree's rendered block names each protected token, so a derivation that rendered an empty or
    # partial list would not pass for a clean one.
    try:
        block = render_block(load_hook(_input(ROOT, HOOK_REL, "the Claude Code deny hook")), ROOT)
    except CannotEvaluate as exc:
        block = ""
        failures.append("real-block: %s" % (exc,))
    for token in ("`**/.working/**`", "`.working/archive/adoption/<run-id>/**`", "`.working/imported/**`",
                  "`.working/imported/adoption/<run-id>/plan.toml`", "`migrate` or `retire`",
                  "`opf/enforcement/**`", "`opf/tools/**`", "`.claude/settings.json`",
                  "`.claude/settings.local.json`", "`opf record` and `opf render`",
                  "calls only that hook's file-tool rule, for a Write at one throwaway fixture layout"):
        runs += 1
        if token not in block:
            failures.append("real-block: the rendered list lacks %s" % (token,))
    # The registration renderer sorts identities, judges them on their normalised spelling and refuses,
    # never raises, on identities it cannot sort or list (stub hooks: each returns fixed identities).
    probe = PROBE_ROOT + os.sep + os.path.join(".claude", "settings.json")
    for case_id, idents, expect in (
            ("registration-clean", {probe: None}, [".claude/settings.json"]),
            ("registration-unsorted", {probe.replace("settings.json", "settings.local.json"): None,
                                       probe: None}, [".claude/settings.json", ".claude/settings.local.json"]),
            ("registration-not-normalised", {probe: None, os.path.join(PROBE_ROOT, ".claude", os.pardir,
                                                                      os.pardir, "x.json"): None}, None),
            ("registration-mixed-types", {1: None, "a": None}, None),
            ("registration-not-iterable", 5, None)):
        runs += 1
        stub = type("StubHook", (), {"_registration_idents": staticmethod(lambda roots, i=idents: i)})
        try:
            got = _registration_rels(stub)
        except CannotEvaluate:
            got = None
        except Exception as exc:  # noqa: BLE001  any other exception is the failure under test
            got = "raised %r" % (exc,)
        if got != expect:
            failures.append("%s: expected %r, got %r" % (case_id, expect, got))
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
        # Why the deny-hook member must be the canonical path (deny-member-relocated-copy): the same
        # bytes at another depth guard other trees, so the copy allows a write the canonical hook denies.
        runs += 1
        try:
            tree = _fixture(base, "relocated-copy-guards-elsewhere")
            _deny_member_relocated_copy(tree)
            canonical = load_hook(_input(tree, HOOK_REL, "the Claude Code deny hook"))
            relocated = load_hook(_input(tree, RELOCATED_HOOK_REL, "the relocated copy"))
            target = {"file_path": os.path.join(str(tree), "opf", "tools", "opf.py")}
            got = (_pack_rels(canonical, tree) != _pack_rels(relocated, tree),
                   canonical._file_tool_rule("Write", target, str(tree)) is not None,
                   relocated._file_tool_rule("Write", target, str(tree)) is None)
        except (AssertionError, OSError, ValueError, CannotEvaluate) as exc:
            got = "raised %r" % (exc,)
        if got != (True, True, True):
            failures.append("relocated-copy-guards-elsewhere: expected the copy to guard other trees and "
                            "to allow a write to opf/tools that the canonical hook denies, got %r" % (got,))
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
