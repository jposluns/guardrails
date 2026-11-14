#!/usr/bin/env python3
"""OPF enforcement-pack platform gate, pack-local path: one protected-path list shared by every platform.

This is OPF's own acceptance of its enforcement pack, run from the OPF tree alone: every input it reads
lies under the OPF root (the directory holding enforcement/ and tools/), so a standalone OPF checkout
with no parent repository verifies itself, and no file a parent repository holds, around or beside the
OPF tree, changes its result. While the OPF tree lives inside a parent repository as its opf/ subtree,
the parent's tools/check_opf_enforce_platforms.py keeps a duplicate of these rules (its evaluate_pack)
and compares the two in its self-test; both are coexistence-only there, removed by the parent's cleanup
step, and neither is an authority over this file. The rules that compare the roster with the AIQT
pack's adapter generators are the parent's integration checks (see "not here" below).

OPF-SPEC 14.1 names four supported assistant platforms (Claude Code, Codex, Gemini CLI and Cursor) and lets
the enforcement pack cover each one EITHER by a verified deny hook OR by instructions, disclosing that
platform's residual. The pack's roster of that choice is enforcement/platforms.toml. This gate holds the
roster and its members to these rules:

  roster      exactly one row per supported assistant platform (every ENFORCEMENT_MEANS platform of
              tools/_opf_adopt.py that allows a deny hook), in that order, each row's means one its
              platform allows, its residuals drawn from ENFORCEMENT_RESIDUALS, unique, and carrying the
              residual its means requires (ENFORCEMENT_REQUIRED_RESIDUALS), with a non-empty evidence text.
              The roster document carries exactly format-version and the [[platform]] rows, and the
              format-version is the exact integer 1 (a boolean true or a float 1.0 is refused).
  residuals   the residual register enforcement/residuals.toml carries the same vocabulary the roster
              draws from: its [[residual]] ids are ENFORCEMENT_RESIDUALS, in that tuple's order, so a
              token renamed on one side only is a finding here. The register's other rules (members,
              anchors) are the parent repository's tools/check_opf_enforce_residuals.py.
  deny hook   the deny-hook row's member must be exactly the canonical hook path
              (enforcement/claude/pretooluse_deny.py), the one file this gate loads, renders and probes.
              Any other path is a finding, a byte-identical copy included: the hook derives the pack trees
              it guards from its own location (__file__), so a relocated copy guards different
              directories than the hook this gate verified. The destination must be one of the hook
              registration paths the hook itself protects.
  names       an instructions member's own file name is not a name a platform loads (AGENTS.md,
              AGENTS.override.md, GEMINI.md, CLAUDE.md, anything under a .cursor directory), so the shipped
              pack tree does not apply its rules to sessions working on it. Member and destination are
              contained relative paths.
  tier        an instructions member states the exact tier line "Enforcement tier: instructions
              (advisory, not enforced)." and, outside that line, inline code spans and the rendered
              block, makes no denial, blocking or enforcement claim (CLAIM_RE).
  one list    an instructions member carries, between the BEGIN and END marker lines, exactly the
              protected-path block this gate renders from the Claude Code deny hook (loaded through
              importlib, never launched): the store tree, the adoption archive and evidence home, the
              frozen plan sources, the declared views, the pack's own trees (the hook's
              _guarded_prefixes, made relative to the OPF root and named at the installed location
              opf/, INSTALL_DIR), the hook registration (the hook's _registration_idents over a probe
              root; an identity outside the probe root REFUSES, never a silent drop), the sanctioned
              writer verbs and the imported-series exemption.
  behaviour   the gate calls the LOADED hook's own file-tool rule (_file_tool_rule) directly, for the
              Write tool only, at one throwaway fixture product root: a Write to one concrete instance of
              every rendered protected-path entry must be denied, and a Write to the rendered exemption
              leaves, to one keep-disposed plan source and to one unlisted file must be allowed. Where the
              hook's constants leave a probe unbuildable, or the rule raises on a probe, the gate cannot
              evaluate (exit 2) rather than drop the probe.

  not here    these rules read the AIQT pack's generators and are the parent's integration checks
              (tools/check_opf_enforce_platforms.py, evaluate_adapters, which reads this pack's contract
              from the pinned OPF release after extraction): which platform may take a deny hook
              (tools/gen_hooks.py PLATFORMS), the location each instructions row installs at
              (tools/gen_agents.py, tools/gen_adapters.py, tools/gen_cursor.py), the Cursor subtree the
              generator orphan-prunes, the Cursor frontmatter and the coexistence line. A standalone OPF
              checkout has none of those generators, so this path neither reads nor checks them, and its
              self-test pins each such case as clean here.

  check_opf_enforce_platforms.py              check the OPF tree this file lies in
  check_opf_enforce_platforms.py --self-test  OPF-only fixture copies of the inputs; each rule above is held
              red by at least one fixture mutant, each behavioural probe leg by a hook rule-logic mutant,
              and each parent-only rule is pinned clean here. An OPF tree with no parent repository
              beside it is clean, and so is one placed inside a parent directory whose own roster,
              register, hook, vocabulary source, adapter generators and platform gate are all broken.

Exit convention (the repository's gates): 0 clean, 1 a finding, 2 cannot evaluate (an unreadable or
malformed input, a hook that cannot be loaded).

DISCLOSED RESIDUALS. The rendered block derives every token the hook holds in a constant or computes in
the functions named above; the prose around those tokens and two literals the hook spells inline (the
manifest's views table and its target key) are fixed text in this gate. The behavioural probes cover ONLY
the rendered entries, the rendered exemption leaves, one keep-disposed source and one unlisted file, each
at one fixture layout and through the file-tool rule for a Write alone. The tier scan is lexical. The
installed location opf/ is a constant (INSTALL_DIR): the members name it, and this path cannot see the
directory a product installs the pack under. Input files are read after a symlinked-parent check and a
regular-file check, so a file swapped between those checks and the read is a check-to-use race. This
gate does not prove that any platform follows an instructions member.
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

# The OPF root: the directory holding enforcement/ and tools/, never the parent repository.
ROOT = Path(__file__).resolve().parents[1]
# The directory name the pack is installed under in a product and in the parent repository; the
# rendered list names the pack's own trees there (module docstring, "one list").
INSTALL_DIR = "opf"
PACK_REL = "enforcement"
ROSTER_REL = PACK_REL + "/platforms.toml"
RESIDUALS_REL = PACK_REL + "/residuals.toml"
HOOK_REL = PACK_REL + "/claude/pretooluse_deny.py"
ADOPT_REL = "tools/_opf_adopt.py"
INPUT_RELS = (ROSTER_REL, RESIDUALS_REL, HOOK_REL, ADOPT_REL)
MAX_INPUT_BYTES = 4 * 1024 * 1024

ROW_KEYS = frozenset(("platform", "means", "member", "destination", "residuals", "evidence"))
DENY_MEANS = "deny-hook"
TIER_LINE = "Enforcement tier: instructions (advisory, not enforced)."
# The marker names the hook and the gate by their paths in the parent repository, the text the shipped
# members carry; from the OPF root the gate is this file, tools/check_opf_enforce_platforms.py.
BEGIN = ("<!-- opf-protected-paths begin: rendered from opf/enforcement/claude/pretooluse_deny.py by "
         "tools/check_opf_enforce_platforms.py; change the hook, never this list -->")
END = "<!-- opf-protected-paths end -->"
# Words that claim a platform stops an operation. An instructions member enforces nothing, so none may
# appear outside the tier line, inline code spans and the rendered block.
CLAIM_RE = re.compile(r"\b(?:den(?:y|ies|ied|ial|ials|ying)|block(?:s|ed|ing)?|enforc(?:e|es|ed|ing)|"
                      r"prevent(?:s|ed|ing)?|refus(?:e|es|ed|ing|al)|guarantee(?:s|d)?|ensure(?:s|d)?)\b",
                      re.IGNORECASE)
CODE_SPAN_RE = re.compile(r"`[^`\n]*`")
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


def _toml(root, rel, label):
    """The parsed TOML document at `rel` under `root`, or CannotEvaluate."""
    try:
        return tomllib.loads(_text(_input(root, rel, label), label))
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("%s does not parse (%s)" % (rel, exc))


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
    spec = importlib.util.spec_from_file_location("_opf_enforce_platforms_local_hook", str(path))
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
        raise CannotEvaluate("the hook's guarded tree %s lies outside the OPF root %s" % (path, root))
    return rel.replace(os.sep, "/")


def _registration_rels(hook):
    """The hook registration entries as product-root-relative paths, from the hook's own
    _registration_idents over a probe root. An identity the probe root does not contain cannot be
    represented as a root-relative protected path, so it REFUSES (fail closed), never a silent drop."""
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
    """The pack's own guarded trees, relative to the OPF root (the pack, then the writer's tools), from
    the hook's _guarded_prefixes."""
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
    pack = ["%s/%s" % (INSTALL_DIR, rel) for rel in _pack_rels(hook, root)]
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
    keep-disposed plan source and to an unlisted file. The probe set never shrinks: a hook whose
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


def _pack_vocabulary(root):
    """The pack-local vocabulary, read from tools/_opf_adopt.py alone: the supported platforms, the means
    each allows, the residual vocabulary and the residuals each means requires."""
    adopt = _literals(_input(root, ADOPT_REL, "the vocabulary source"),
                      ("ENFORCEMENT_MEANS", "ENFORCEMENT_RESIDUALS", "ENFORCEMENT_REQUIRED_RESIDUALS"))
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
    return dict(
        platforms=tuple(p for p, allowed in means.items() if DENY_MEANS in allowed),
        means=means,
        residuals=residuals,
        required=required,
    )


def _register_findings(root, vocab):
    """The residual register's vocabulary (module docstring, "residuals"): its [[residual]] ids, in
    order, must be ENFORCEMENT_RESIDUALS. A register that does not parse or carries no [[residual]]
    row list is CannotEvaluate."""
    doc = _toml(root, RESIDUALS_REL, "the residual register")
    rows = doc.get("residual")
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise CannotEvaluate("%s carries no [[residual]] rows" % (RESIDUALS_REL,))
    ids = tuple(r.get("id") for r in rows)
    if ids != vocab["residuals"]:
        return ["%s: its [[residual]] ids %s are not %s ENFORCEMENT_RESIDUALS %s in that order, so the "
                "roster and the register do not share one residual vocabulary"
                % (RESIDUALS_REL, list(ids), ADOPT_REL, list(vocab["residuals"]))]
    return []


def _contained(rel):
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel:
        return False
    return all(p not in ("", ".", "..") for p in rel.split("/")) and not any(ord(c) < 0x20 for c in rel)


def _member_findings(where, text, block):
    """The findings for one instructions member's text: the rendered block, the tier line and the claim
    scan."""
    findings = []
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
    """The findings for one well-formed roster row of a known, first-seen platform: its residuals and
    evidence, contained paths, the canonical deny-hook member at a registration the hook protects, and
    an instructions member's name, rendered block, tier line and claim scan."""
    means, member, destination = row["means"], row["member"], row["destination"]
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
    parts = member.split("/")
    if parts[-1] in LOADED_NAMES or any(p.startswith(".cursor") for p in parts):
        findings.append("%s: member %r carries a name its platform loads; the pack tree would apply it to "
                        "sessions working on the pack" % (where, member))
    text = _text(member_path, "the instructions member")
    findings.extend(_member_findings(PACK_REL + "/" + member, text, block))
    return findings


def _roster_rows(root, vocab):
    """(findings, rows) for the roster at `root`: the roster-level findings (keys, platform, duplicate,
    means and coverage), and (where, row) for every well-formed row of a known, first-seen platform
    whose means that platform allows. A roster that does not parse or carries another schema is
    CannotEvaluate."""
    doc = _toml(root, ROSTER_REL, "the platform roster")
    version = doc.get("format-version")
    if set(doc) != frozenset(("format-version", "platform")) or type(version) is not int \
            or version != 1 or not isinstance(doc.get("platform"), list):
        raise CannotEvaluate("%s carries no exact integer format-version = 1 and [[platform]] rows only "
                             "(a boolean true, a float 1.0 or another near-1 spelling is refused)"
                             % (ROSTER_REL,))
    findings = []
    rows = []
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
        rows.append((where, row))
    if tuple(seen) != vocab["platforms"]:
        findings.append("%s: rows must cover %s once each, in that order (found %s)"
                        % (ROSTER_REL, list(vocab["platforms"]), seen))
    return findings, rows


def evaluate(root, hook=None):
    """The findings for the OPF tree at `root` (a list, empty when clean), or CannotEvaluate. It reads
    only INPUT_RELS and the members the roster names, all under `root`. `hook` is the loaded deny hook
    (loaded from `root` when None)."""
    root = Path(root)
    vocab = _pack_vocabulary(root)
    if hook is None:
        hook = load_hook(_input(root, HOOK_REL, "the Claude Code deny hook"))
    registrations = _registration_rels(hook)
    vocab["registrations"] = registrations
    pack_rels = _pack_rels(hook, root)
    block = render_block(hook, root)
    findings = _behaviour_findings(hook, root, pack_rels, registrations)
    roster_findings, rows = _roster_rows(root, vocab)
    for where, row in rows:
        findings.extend(_row_findings(root, vocab, block, where, row))
    return findings + roster_findings + _register_findings(root, vocab)


def main(argv):
    if argv:
        print("usage: check_opf_enforce_platforms.py [--self-test]", file=sys.stderr)
        return 2
    try:
        findings = evaluate(ROOT)
    except CannotEvaluate as exc:
        print("check_opf_enforce_platforms (OPF): CANNOT EVALUATE: %s" % (exc,))
        return 2
    for finding in findings:
        print("check_opf_enforce_platforms (OPF): %s" % (finding,))
    if findings:
        print("check_opf_enforce_platforms (OPF): FAIL (%d finding(s))" % (len(findings),))
        return 1
    print("check_opf_enforce_platforms (OPF): OK (%s covers every supported assistant platform; every "
          "instructions member carries the protected-path list rendered from %s, and the loaded hook's "
          "file-tool rule denies a Write to each rendered entry on the behavioural probes; the adapter "
          "generator rules are the parent repository's)" % (ROSTER_REL, HOOK_REL))
    return 0


# --- self-test -----------------------------------------------------------------------------------------

CODEX = PACK_REL + "/codex/AGENTS.opf.md"
GEMINI = PACK_REL + "/gemini-cli/GEMINI.opf.md"
CURSOR = PACK_REL + "/cursor/opf.mdc"
INSTRUCTION_RESIDUALS = ('residuals = ["unverified-platform-denial", "shell-or-interpreter-wrapping", '
                         '"same-user-tampering"]')
REGISTRATION_LINE = "- `.claude/settings.json` (R8): the Claude Code hook registration of the product root.\n"
# The coexistence line the parent gate requires of the Codex member (parent-only rule, clean here).
CODEX_COEXIST_LINE = ("Coexistence is an unresolved adoption prerequisite: the AIQT pack's tools/gen_agents.py "
                      "regenerates AGENTS.md whole, so these contents cannot coexist with the AIQT instructions "
                      "there today.")
RELOCATED_HOOK_REL = PACK_REL + "/codex/nested/hook.py"
# Files a parent directory could hold around the OPF tree, under the names this gate or the parent's
# platform gate would read, each broken so that reading, importing or running it would refuse or raise.
# This gate's result must not depend on any of them (self_test, opf-acceptance-ignores-parent).
BROKEN_PARENT_FILES = (
    (ROSTER_REL, "[[platform\n"),
    (RESIDUALS_REL, "[[residual\n"),
    (HOOK_REL, "raise RuntimeError('a parent hook was loaded')\n"),
    (ADOPT_REL, "ENFORCEMENT_MEANS = (\n"),
    ("tools/check_opf_enforce_platforms.py", "raise RuntimeError('a parent platform gate was run')\n"),
    ("tools/gen_hooks.py", "PLATFORMS = (\n"),
    ("tools/gen_agents.py", "GENSRC_OUTPUTS = (\n"),
    ("tools/gen_adapters.py", "GENSRC_OUTPUTS = (\n"),
    ("tools/gen_cursor.py", "OUT_PARTS = (\n"),
)


def _fixture(base, name):
    """A fresh OPF-only copy of every input this gate reads, under base/name: the enforcement pack and the
    vocabulary source, and nothing from a parent repository."""
    tree = Path(base) / name
    shutil.copytree(ROOT / PACK_REL, tree / PACK_REL)
    (tree / ADOPT_REL).parent.mkdir(parents=True)
    shutil.copyfile(ROOT / ADOPT_REL, tree / ADOPT_REL)
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


def _codex_deny_hook_canonical(tree):
    """A Codex deny-hook row naming the canonical hook at a registration: which platform may take a
    deny hook is read from tools/gen_hooks.py, a parent-only rule, so this path reports nothing."""
    _codex_deny_hook(tree)
    _edit(tree, ROSTER_REL,
          'means = "deny-hook"\nmember = "codex/AGENTS.opf.md"\ndestination = "AGENTS.md"',
          'means = "deny-hook"\nmember = "claude/pretooluse_deny.py"\ndestination = ".claude/settings.json"')


def _deny_member_byte_copy(tree):
    shutil.copyfile(tree / HOOK_REL, tree / PACK_REL / "claude" / "pretooluse_deny_copy.py")
    _edit(tree, ROSTER_REL, 'member = "claude/pretooluse_deny.py"',
          'member = "claude/pretooluse_deny_copy.py"')


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
    cannot-evaluate result). Each red vector contradicts at least one rule this path enforces and pins the
    exact finding count plus a message substring; the clean vectors prove the named permissions, and the
    parent-only rules (module docstring, "not here"), fire no finding on this path. Every vector mutates an
    OPF-only fixture, so each also shows the path needs no parent file."""
    def edit(rel, old, new, count=1):
        return lambda tree: _edit(tree, rel, old, new, count)

    def append(rel, extra):
        return lambda tree: _append(tree, rel, extra)

    def remove(rel):
        return lambda tree: (tree / rel).unlink()

    differs = "differs from the list rendered"
    canonical = "the canonical deny hook path"
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
        # so the block, unchanged; only the behavioural probes turn these red.
        ("hook-store-logic", edit(HOOK_REL, '    return ("direct edits under %s/ are denied:',
                                  '    return None and ("direct edits under %s/ are denied:'),
         "allows the probe write", 1),
        ("hook-evidence-logic", edit(HOOK_REL, '        return ("direct writes under %s/imported/ are denied:',
                                     '        return None and ("direct writes under %s/imported/ are denied:'),
         "allows the probe write", 1),
        ("hook-archive-logic", edit(HOOK_REL, '        return ("writes under %s/archive/adoption/<run-id>/ are',
                                    '        return None and ("writes under %s/archive/adoption/<run-id>/ are'),
         "allows the probe write", 1),
        ("hook-frozen-logic", edit(HOOK_REL, "            if disposition in FROZEN_DISPOSITIONS:",
                                   '            if disposition == "retire":'),
         "allows the probe write", 1),
        ("hook-retire-logic", edit(HOOK_REL, "            if disposition in FROZEN_DISPOSITIONS:",
                                   '            if disposition == "migrate":'),
         "allows the probe write", 1),
        ("hook-views-logic", edit(HOOK_REL, "if cand in views[0]:", "if cand in ():", 3),
         "allows the probe write", 1),
        ("hook-guard-logic", edit(HOOK_REL, "        if candidate == prefix or candidate.startswith(prefix + os.sep):",
                                  "        if False:"),
         "allows the probe write", 2),
        ("hook-registration-logic", edit(HOOK_REL, "    hit = idents.get(candidate)",
                                         "    hit = idents.get(candidate) and None"),
         "allows the probe write", 2),
        ("hook-exemption-logic", edit(HOOK_REL, "    if (len(after) == 2 and",
                                      "    if (False and len(after) == 2 and"),
         "denies the probe write", 2),
        ("hook-every-source-frozen", edit(HOOK_REL, "            if disposition in FROZEN_DISPOSITIONS:",
                                          "            if disposition in VALID_DISPOSITIONS:"),
         "denies the probe write", 1),
        ("hook-deny-all", edit(HOOK_REL, "    return None\n\n\ndef _plain_command_word",
                               '    return "deny-all mutant"\n\n\ndef _plain_command_word'),
         "denies the probe write", 4),
        ("hook-rule-raises", edit(HOOK_REL, "    field = FILE_TOOL_TARGET[tool_name]\n",
                                  "    field = FILE_TOOL_TARGET[tool_name]\n    raise RuntimeError('mutant')\n"),
         "fails on the behavioural probe", None),
        ("hook-leaf-pattern-unprobeable", edit(HOOK_REL, r'IMPORTED_LEAF_RE = re.compile(r"\A(worklog\.imported'
                                               r'\.toml|', r'IMPORTED_LEAF_RE = re.compile(r"\A(worklog\.imported'
                                               r'\.tom|'), "does not match the exemption probe leaf", None),
        ("hook-no-kept-disposition", edit(HOOK_REL, 'VALID_DISPOSITIONS = frozenset(("keep", "move", "migrate", '
                                          '"retire"))', 'VALID_DISPOSITIONS = frozenset(("migrate", "retire"))'),
         "no non-frozen string disposition", None),
        ("hook-registration-order", edit(HOOK_REL, "        for base in sorted(REGISTRATION_LEAVES):",
                                         "        for base in sorted(REGISTRATION_LEAVES, reverse=True):"),
         None, 0),
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
         "outside the OPF root", None),
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
        ("tier-claim-guarantee", append(CODEX, "\nCodex guarantees that the paths above stay unchanged.\n"),
         "nothing on this platform enforces", 1),
        ("tier-claim-prevent", append(GEMINI, "\nThis instruction prevents writes to the paths above.\n"),
         "nothing on this platform enforces", 1),
        ("tier-claim-code-span-exempt", append(CODEX, "\nThe Claude Code hook file is `pretooluse_deny.py`.\n"),
         None, 0),
        # deny hook: the member must be the canonical hook path exactly, at a registration destination.
        ("deny-hook-codex", _codex_deny_hook, canonical, 2),
        ("deny-hook-gemini", edit(ROSTER_REL, 'means = "instructions"\nmember = "gemini-cli/GEMINI.opf.md"',
                                  'means = "deny-hook"\nmember = "gemini-cli/GEMINI.opf.md"'), canonical, 2),
        ("deny-member-not-hook", edit(ROSTER_REL, 'member = "claude/pretooluse_deny.py"',
                                      'member = "codex/AGENTS.opf.md"'), canonical, 1),
        ("deny-member-byte-identical-copy", _deny_member_byte_copy, canonical, 1),
        ("deny-member-relocated-copy", _deny_member_relocated_copy, canonical, 1),
        ("deny-destination-unpinned", edit(ROSTER_REL, 'destination = ".claude/settings.json"',
                                           'destination = "README.md"'),
         "is not a hook registration path", 1),
        ("deny-member-missing", edit(ROSTER_REL, 'member = "claude/pretooluse_deny.py"',
                                     'member = "claude/no-such-hook.py"'), canonical, 1),
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
        ("roster-unparseable", append(ROSTER_REL, "\n[[platform\n"), "CANNOT EVALUATE", None),
        # residuals: the register shares the roster's vocabulary, token for token and in order.
        ("register-id-renamed", edit(RESIDUALS_REL, 'id = "same-user-tampering"', 'id = "same-user-tamper"'),
         "do not share one residual vocabulary", 1),
        ("register-row-dropped", edit(RESIDUALS_REL, '[[residual]]\nid = "unverified-platform-denial"',
                                      '[[ignored]]\nid = "unverified-platform-denial"'),
         "do not share one residual vocabulary", 1),
        ("register-rows-reordered", lambda tree: (
            _edit(tree, RESIDUALS_REL, 'id = "same-user-tampering"', 'id = "SWAP"'),
            _edit(tree, RESIDUALS_REL, 'id = "unverified-platform-denial"', 'id = "same-user-tampering"'),
            _edit(tree, RESIDUALS_REL, 'id = "SWAP"', 'id = "unverified-platform-denial"')),
         "do not share one residual vocabulary", 1),
        ("register-unparseable", append(RESIDUALS_REL, "\n[[residual\n"), "CANNOT EVALUATE", None),
        ("register-absent", remove(RESIDUALS_REL), "CANNOT EVALUATE", None),
        # names and containment.
        ("destination-not-contained", edit(ROSTER_REL, 'destination = ".cursor/rules/opf.mdc"',
                                           'destination = "/etc/opf.mdc"'), "contained relative paths", 1),
        ("member-not-contained", edit(ROSTER_REL, 'member = "cursor/opf.mdc"', 'member = "../cursor/opf.mdc"'),
         "contained relative paths", 1),
        ("member-loaded-name", _loaded_name, "carries a name its platform loads", 1),
        ("member-dot-cursor-component", _dot_cursor_member, "carries a name its platform loads", 1),
        ("member-absent", remove(GEMINI), "CANNOT EVALUATE", None),
        ("member-is-directory", _member_directory, "is not a regular file", None),
        ("member-symlinked-parent", _symlinked_member_parent, "through a symlink", None),
        # vocabulary: absent, doubled or wrongly shaped literals are cannot-evaluate.
        ("adopt-vocabulary-unreadable", remove(ADOPT_REL), "CANNOT EVALUATE", None),
        ("adopt-means-not-table", edit(ADOPT_REL, 'ENFORCEMENT_MEANS = {\n    "ci": ("ci-checks",),',
                                       'ENFORCEMENT_MEANS = {\n    "ci": "ci-checks",'),
         "is not a table of", None),
        ("adopt-required-not-table", edit(ADOPT_REL, "ENFORCEMENT_REQUIRED_RESIDUALS = {",
                                          "ENFORCEMENT_REQUIRED_RESIDUALS = 0\n_IGNORED_RR = {"),
         "ENFORCEMENT_REQUIRED_RESIDUALS", None),
        ("adopt-doubled-literal", append(ADOPT_REL, "\nENFORCEMENT_RESIDUALS = ()\n"), "more than once", None),
        # not here: the rules the parent adapter path reads from an AIQT generator fire nothing on this
        # path (module docstring, "not here"); the parent gate's self-test holds each one red.
        ("parent-only-deny-platform", _codex_deny_hook_canonical, None, 0),
        ("parent-only-destination-codex", edit(ROSTER_REL, 'destination = "AGENTS.md"',
                                               'destination = "docs/AGENTS.md"'), None, 0),
        ("parent-only-destination-gemini", edit(ROSTER_REL, 'destination = "GEMINI.md"',
                                                'destination = ".gemini/GEMINI.md"'), None, 0),
        ("parent-only-destination-cursor", edit(ROSTER_REL, 'destination = ".cursor/rules/opf.mdc"',
                                                'destination = ".cursor/opf.md"'), None, 0),
        ("parent-only-pruned-subtree", edit(ROSTER_REL, 'destination = ".cursor/rules/opf.mdc"',
                                            'destination = ".cursor/rules/aiqt-guardrails/opf.mdc"'), None, 0),
        ("parent-only-cursor-frontmatter", edit(CURSOR, "alwaysApply: true", "alwaysApply: false"), None, 0),
        ("parent-only-coexist-line", edit(CODEX, CODEX_COEXIST_LINE + "\n", ""), None, 0),
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
    with tempfile.TemporaryDirectory(prefix="opf-enforce-platforms-local-") as base:
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
        # The OPF-only fixture holds no parent file: nothing above the fixture's own root is read, so the
        # clean result above came from the OPF tree alone. Placed one level below an empty directory, the
        # fixture also stays clean when the directory above it holds no tools/ or .aiqt/ at all.
        runs += 1
        try:
            outer = Path(base) / "no-parent" / "opf"
            outer.parent.mkdir()
            tree = _fixture(outer.parent, "opf")
            entries = sorted(p.name for p in outer.parent.iterdir())
            got = (entries, evaluate(tree))
        except (AssertionError, OSError, ValueError, CannotEvaluate) as exc:
            got = "raised %r" % (exc,)
        if got != (["opf"], []):
            failures.append("opf-only-tree: expected a clean result from an OPF tree with no parent "
                            "repository beside it, got %r" % (got,))
        # Nor does a parent file change it: the same fixture placed as opf/ inside a parent directory
        # holding every BROKEN_PARENT_FILES entry is still clean, so this gate neither prefers nor falls
        # back to a parent's roster, register, hook, vocabulary source, generator or platform gate.
        runs += 1
        try:
            parent = Path(base) / "broken-parent"
            for rel, text in BROKEN_PARENT_FILES:
                (parent / rel).parent.mkdir(parents=True, exist_ok=True)
                (parent / rel).write_text(text, encoding="utf-8")
            got = evaluate(_fixture(parent, INSTALL_DIR))
        except (AssertionError, OSError, ValueError, CannotEvaluate) as exc:
            got = "raised %r" % (exc,)
        if got != []:
            failures.append("opf-acceptance-ignores-parent: expected a clean result from an OPF tree inside a "
                            "parent directory whose own platform inputs are all broken, got %r" % (got,))
        # Why the deny-hook member must be the canonical path (deny-member-relocated-copy): the same
        # bytes at another depth guard other trees, so the copy allows a write the canonical hook denies.
        runs += 1
        try:
            tree = _fixture(base, "relocated-copy-guards-elsewhere")
            _deny_member_relocated_copy(tree)
            canonical_hook = load_hook(_input(tree, HOOK_REL, "the Claude Code deny hook"))
            relocated = load_hook(_input(tree, RELOCATED_HOOK_REL, "the relocated copy"))
            target = {"file_path": os.path.join(str(tree), "tools", "opf.py")}
            got = (_pack_rels(canonical_hook, tree) != _pack_rels(relocated, tree),
                   canonical_hook._file_tool_rule("Write", target, str(tree)) is not None,
                   relocated._file_tool_rule("Write", target, str(tree)) is None)
        except (AssertionError, OSError, ValueError, CannotEvaluate) as exc:
            got = "raised %r" % (exc,)
        if got != (True, True, True):
            failures.append("relocated-copy-guards-elsewhere: expected the copy to guard other trees and "
                            "to allow a write to tools/ that the canonical hook denies, got %r" % (got,))
    for failure in failures:
        print("SELF-TEST FAIL: %s" % (failure,))
    if failures:
        print("check_opf_enforce_platforms (OPF) --self-test: FAIL (%d failure(s) over %d checks)"
              % (len(failures), runs))
        return 1
    print("check_opf_enforce_platforms (OPF) --self-test: OK (%d checks)" % (runs,))
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main(sys.argv[1:]))
