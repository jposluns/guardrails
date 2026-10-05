#!/usr/bin/env python3
"""OPF Claude Code deny hook (enforcement pack, spec 1.3.0 (draft) 14.1): the verified PreToolUse denial
of direct edits to the OPF store.

Spec sentences this member implements (OPF-SPEC.md 14.1): "The enforcement pack MUST freeze each
plan-enumerated old file that remains in the live tree until its retirement is recorded, MUST deny writes
under `.working/archive/adoption/<run-id>/`, and MUST protect both record series, counters, declared views
and evidence." and "MUST provide a verified deny hook on each supported platform whose official
documentation confirms denial support". Claude Code is such a platform: the hook I/O contract below is
doc-confirmed 2026-08-17 against code.claude.com/docs/en/hooks (the same confirmation pin the repository's
own hook dispatcher carries), re-verified at build time for this pack member. Warning never substitutes
for denial here: every protected operation takes a structured DENY decision or a blocking error, never an
advisory note.

Installation (per user or per project; `install-pack` wires this automatically once it ships, and the
adoption `enable-hook` op writes exactly this registration through its structured JSON merge): register
one PreToolUse group in `.claude/settings.json` whose matcher is "*" (match-all: a narrower matcher would
silently exempt tools) and whose single command entry launches this file isolated:

    hooks . PreToolUse : one group, matcher "*", with one hooks entry of type "command" whose command is
    python3 -I <absolute path to>/pretooluse_deny.py

I/O contract (doc-confirmed, above): the tool-call payload arrives as JSON on stdin, carrying
hook_event_name, tool_name, tool_input and the session cwd. A decision goes to stdout on exit 0 as a JSON
object whose hookSpecificOutput table carries hookEventName "PreToolUse", permissionDecision "deny" and a
permissionDecisionReason; this hook expresses ALLOW as NO output (exit 0 silent), so the user's own
permission flow is never bypassed, and a DENY blocks the tool call. Exit 2 is a blocking error whose
stderr is fed back to Claude; this hook uses it for input it cannot read at all (an oversize, undecodable
or unparseable payload, a payload that is not a JSON object, or a registration mis-wired onto another
hook event), so the fail-closed posture holds even where no structured decision can honestly be
constructed.

WHAT IT DENIES (each rule names the sanctioned path in its decision reason):

  R1 store writes. A Write, Edit, MultiEdit or NotebookEdit whose target path carries a `.working` path
     component is a direct store edit: records, counters, the machine store's ledgers and indexes,
     journals, staging, and the evidence homes under `.working/imported/` (spec 4.2, 14.2) all live
     there. Denied; OPF content changes only through the sanctioned writer (`opf record` and the other
     opf verbs, which run under their own write guard). Both the lexically normalized target and its
     os.path.realpath resolution are checked, so an existing symlink into the store does not evade R1.
  R2 adoption-archive writes. A target under `.working/archive/adoption/` is denied with the spec's own
     sentence: writes under `.working/archive/adoption/<run-id>/` are denied outright (spec 14.1).
     (R2 is a subset of R1; it exists so the archive denial is named, probed and reported on its own.)
  R3 frozen plan-enumerated old files. For every product root above the target (a directory holding a
     `.working/` entry), each `plan.toml` under `.working/imported/adoption/<run-id>/` whose format is
     opf.adoption.plan/v2 contributes its retire- and migrate-disposed source paths; a target equal to
     one of them is denied: the file stays frozen, byte-identical, until its retirement is recorded
     (spec 14.1). A move- or keep-disposed source is adopter content and is not frozen.
  R4 declared-view writes. Each machine-store manifest's views targets are declared view destinations;
     a target equal to one is denied (views change only through `opf render`; spec 5.8, 14.1).
  R5 Bash writes the matcher CAN see. A Bash command whose text references a protected token (the
     `.working` store tree, a frozen path from R3, or a declared-view target from R4, with the rosters
     resolved from the product roots above the session cwd) is denied unless the whole command is one of
     the three pristine allowances below. Reference, not proven mutation, is the trigger: a lexical hook
     cannot prove a referencing command read-only (sed -i, shell functions, aliases), so it fails closed
     and names the allowed routes.
  R6 unreadable inputs fail closed. A missing or non-string target field, a control character in a
     target, a relative target with no readable session cwd, an unreadable or ambiguous machine-store
     manifest, and an unreadable, unparseable or wrong-format plan.toml each DENY (the roster that would
     prove the operation safe cannot be computed), naming the unreadable input. A payload unreadable at
     the envelope level exits 2 (blocking error), as above.

PRISTINE ALLOWANCES for a Bash command that references a protected token (each requires the RAW command
string to be metacharacter-free: no semicolon, ampersand, pipe, angle bracket, backquote, dollar sign,
parenthesis, brace, backslash or newline anywhere, even quoted, so no second command, redirection,
substitution or expansion can ride along; leading VAR=value assignments are skipped):
  A1 the sanctioned writer: opf itself, or a python3 launcher (interpreter flags allowed) running a path
     whose basename is opf.py, with any verb; opf's own write guard, lease and journal govern what it
     may do. This allowance also holds under an R6 roster failure, so the in-session repair path stays
     open.
  A2 a read-only command word: cat, head, tail, wc, grep, diff, cmp, ls, stat, file, readlink, du,
     sha256sum or md5sum (flag-insensitive file readers; none takes a write-capable flag).
  A3 a read-only-or-store-safe git form: git whose subcommand token is status, log, show, diff, blame,
     grep, rev-parse, ls-files, add or commit (add and commit write only the repository metadata under
     .git/, never the protected file's bytes; a working-tree-writing git verb such as checkout, restore
     or stash takes the deny).

RESIDUALS (spec 14.1 requires each disclosed; the pack's residual register (slice (d)) and the plan's
per-platform residual coverage carry the same list):
  - A Bash write the matcher cannot see: a protected path reaching the filesystem through a shell
    variable, glob, alias, function, cd-relative spelling that drops the token, command or process
    substitution, an interpreter one-liner, or any other spelling in which no protected token appears
    textually in the command string. R5 is a lexical floor, not a sandbox.
  - Edits made outside Claude Code: any other editor, shell or tool bypasses this hook entirely; the
    pre-commit and CI floor members are the overlapping controls there.
  - Per-clone installation and bypass: the settings.json registration is local configuration; a clone
    that never registered the hook runs no hook, and the same user can deregister or edit it
    (same-user tampering, canonical hand edits).
  - Platform hook-startup failures may fall through to the platform's normal permission flow.
  - Shell or interpreter wrapping of the platform itself is outside the hook's reach.
  - Over-approximation is the accepted cost of the fail-closed posture: R5 denies some read-only
    commands (reads go through the platform's Read tool, A2 or opf), R3 keeps denying a frozen path
    even after its retirement is recorded and the live file is gone (re-creating it directly stays
    denied; a fresh plan is the sanctioned route), and a `.working` or frozen-path token inside prose
    (a commit message, say) still trips R5.
  - The IMPORTED record series is NOT yet protected here: the leaves `worklog.imported.toml` and
    `<type>.imported.index.toml` inside the machine store are exempt from R1 by name, because
    enforcement MUST NOT ship before the writer can perform every operation it forces (spec 14.1) and
    the import writer (`opf record import --batch`, spec 8.8) has not shipped. The imported-series
    protection slice lands with or after that writer and removes this exemption.

Offline, stdlib only (json, tomllib, os, re, sys), no subprocess, no network. Launched isolated
(python3 -I) so a file planted beside it cannot shadow a stdlib import. Exit statuses: 0 (with a deny
decision or silent allow) and 2 (blocking error) only.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 11):
    # tomllib (the plan and manifest reader) first ships in 3.11. A sub-floor interpreter cannot
    # evaluate the rosters, so it must BLOCK (exit 2 is a blocking error), never wave the call through.
    sys.stderr.write(
        "opf-pretooluse-deny: cannot evaluate: this hook requires Python 3.11 or newer; this is "
        "Python %d.%d.%d (%s). Failing closed: the tool call is blocked until the registration "
        "launches a supported interpreter.\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import json
import os
import re
import tomllib

# The platform payload bound: a PreToolUse payload (a MultiEdit's edit list included) is far below this;
# anything larger is not a payload this hook can honestly evaluate, so it fails closed at the envelope.
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024

WORKING = ".working"                      # the fixed store-tree name at a product root (spec 4.4)
ADOPTION_ARCHIVE = ("archive", "adoption")  # .working/archive/adoption/<run-id>/ (spec 14.1, 14.2)
ADOPTION_EVIDENCE = ("imported", "adoption")  # .working/imported/adoption/<run-id>/ (spec 14.2)
PLAN_FILENAME = "plan.toml"
PLAN_FORMAT = "opf.adoption.plan/v2"      # the bound plan format marker (spec 14.1)
FROZEN_DISPOSITIONS = ("migrate", "retire")  # the old-file dispositions that freeze in place (spec 14.1)
# The store-tree control subdirs (spec 4.2): a first component after .working/ outside this set is a
# machine-store candidate, where the imported-series leaf exemption below may apply.
CONTROL_SUBDIRS = frozenset(("imported", "archive", "staging", "journals"))
# The imported-series leaves (spec 8.3) stay EXEMPT until the import writer ships (module docstring).
IMPORTED_LEAF_RE = re.compile(r"\A(worklog\.imported\.toml|[A-Za-z0-9_-]+\.imported\.index\.toml)\Z")

# The write-capable file tools and the payload field naming each one's target.
FILE_TOOL_TARGET = dict(Write="file_path", Edit="file_path", MultiEdit="file_path",
                        NotebookEdit="notebook_path")

# R5/A1-A3 vocabularies. METACHARS is scanned over the RAW command string, quoted spans included:
# semicolon, ampersand, pipe, the two angle brackets, backquote, dollar sign, the two parentheses, the
# two braces, backslash and newline, each built from its code point so none appears literally here.
METACHARS = frozenset(chr(c) for c in (59, 38, 124, 60, 62, 96, 36, 40, 41, 123, 125, 92, 10))
READONLY_WORDS = frozenset(("cat", "head", "tail", "wc", "grep", "diff", "cmp", "ls", "stat", "file",
                            "readlink", "du", "sha256sum", "md5sum"))
GIT_SAFE_SUBCOMMANDS = frozenset(("status", "log", "show", "diff", "blame", "grep", "rev-parse",
                                  "ls-files", "add", "commit"))
_ASSIGNMENT_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*=")
_PYTHON_RE = re.compile(r"\Apython(3(\.\d+)?)?\Z")
_PYFLAGS_RE = re.compile(r"\A-[IBEsuPb]+\Z")

SANCTIONED = ("OPF content changes only through the sanctioned writer: run the opf CLI (opf record, "
              "opf render, and the other opf verbs), or make the change outside the store's scope")


class Unreadable(Exception):
    """An envelope-level payload this hook cannot read at all (mapped to exit 2, a blocking error)."""


def _emit_deny(reason):
    decision = dict(hookSpecificOutput=dict(hookEventName="PreToolUse",
                                            permissionDecision="deny",
                                            permissionDecisionReason="opf-pretooluse-deny: " + reason))
    sys.stdout.write(json.dumps(decision) + "\n")
    return 0


def _read_payload():
    """Read and parse the stdin payload; raise Unreadable on anything this hook cannot read."""
    try:
        data = sys.stdin.buffer.read(MAX_PAYLOAD_BYTES + 1)
    except OSError as exc:
        raise Unreadable("stdin could not be read (%r)" % (exc,))
    if len(data) > MAX_PAYLOAD_BYTES:
        raise Unreadable("the payload exceeds %d bytes" % (MAX_PAYLOAD_BYTES,))
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise Unreadable("the payload is not UTF-8 JSON (%s)" % (exc,))
    if not isinstance(payload, dict):
        raise Unreadable("the payload is not a JSON object")
    event = payload.get("hook_event_name")
    if event is not None and event != "PreToolUse":
        raise Unreadable("registered on hook event %r, not PreToolUse; fix the registration" % (event,))
    return payload


def _components(path):
    """The normalized path's components (empty and dot entries dropped)."""
    norm = os.path.normpath(path)
    return [c for c in norm.split(os.sep) if c not in ("", ".")]


def _candidates(target, cwd):
    """The absolute forms of `target` to judge: the lexically normalized path and its realpath (an
    existing symlink anywhere on the way resolves, so a link into the store is judged as the store)."""
    if not os.path.isabs(target):
        if not isinstance(cwd, str) or not os.path.isabs(cwd):
            return None
        target = os.path.join(cwd, target)
    norm = os.path.normpath(target)
    try:
        real = os.path.realpath(norm)
    except OSError:
        real = norm
    return sorted(set((norm, real)))


def _store_rule(candidate):
    """R1/R2 over one absolute candidate path: a deny reason, or None. The imported-series leaf
    exemption (module docstring) applies only inside a machine-store candidate subdir."""
    comps = _components(candidate)
    if WORKING not in comps:
        return None
    after = comps[comps.index(WORKING) + 1:]
    if len(after) >= 2 and (after[0], after[1]) == ADOPTION_ARCHIVE:
        return ("writes under %s/archive/adoption/<run-id>/ are denied: the adoption archive holds "
                "digest-bound preserved originals (spec 14.1). %s." % (WORKING, SANCTIONED))
    if after and after[0] not in CONTROL_SUBDIRS and IMPORTED_LEAF_RE.match(after[-1]):
        return None  # the imported-series leaves stay writer-less until the import writer ships
    if after and after[0] == "imported":
        return ("direct writes under %s/imported/ are denied: adoption and import evidence is written "
                "only by the opf writers and verified by the completion checks (spec 14.1, 14.2). "
                "%s." % (WORKING, SANCTIONED))
    return ("direct edits under %s/ are denied: OPF records, counters, ledgers, journals and staging "
            "change only through the sanctioned writer (spec 14.1). %s." % (WORKING, SANCTIONED))


def _roots_above(path):
    """Every product root at or above `path`: a directory holding a `.working` entry, nearest first."""
    roots = []
    cur = os.path.normpath(path)
    while True:
        if os.path.basename(cur) != WORKING and os.path.exists(os.path.join(cur, WORKING)):
            roots.append(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            return roots
        cur = parent


def _frozen_paths(root):
    """The plan-enumerated frozen old-file paths of `root` (R3): (set of root-relative paths, None), or
    (None, reason) when a roster input is unreadable (R6 fails closed on it)."""
    home = os.path.join(root, WORKING, *ADOPTION_EVIDENCE)
    if not os.path.isdir(home):
        return set(), None
    try:
        runs = sorted(os.listdir(home))
    except OSError as exc:
        return None, "the adoption evidence home %s is unreadable (%r)" % (home, exc)
    frozen = set()
    for run in runs:
        plan = os.path.join(home, run, PLAN_FILENAME)
        if not os.path.isdir(os.path.join(home, run)) or not os.path.exists(plan):
            continue
        try:
            with open(plan, "rb") as fh:
                doc = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            return None, "the adoption plan %s is unreadable or unparseable (%r)" % (plan, exc)
        if doc.get("format") != PLAN_FORMAT:
            return None, "the adoption plan %s does not carry format %r" % (plan, PLAN_FORMAT)
        rows = doc.get("sources", [])
        if not isinstance(rows, list):
            return None, "the adoption plan %s sources table is not a list" % (plan,)
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                return None, "the adoption plan %s carries a malformed source row" % (plan,)
            if row.get("disposition") in FROZEN_DISPOSITIONS:
                rel = row["path"]
                if os.path.isabs(rel) or ".." in _components(rel):
                    return None, "the adoption plan %s enumerates a non-contained path %r" % (plan, rel)
                frozen.add(os.path.normpath(rel))
    return frozen, None


def _view_targets(root):
    """The declared-view targets of the machine store at `root` (R4): (set of root-relative paths,
    None), or (None, reason) on an unreadable or ambiguous manifest (R6 fails closed on it)."""
    working = os.path.join(root, WORKING)
    try:
        names = sorted(os.listdir(working))
    except OSError as exc:
        return None, "the store tree %s is unreadable (%r)" % (working, exc)
    manifests = [os.path.join(working, n, "manifest.toml") for n in names
                 if n not in CONTROL_SUBDIRS and os.path.isdir(os.path.join(working, n))
                 and os.path.exists(os.path.join(working, n, "manifest.toml"))]
    if not manifests:
        return set(), None
    if len(manifests) > 1:
        return None, ("multiple machine-store manifests under %s (store resolution fails closed on "
                      "an ambiguous store)" % (working,))
    try:
        with open(manifests[0], "rb") as fh:
            doc = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        return None, "the manifest %s is unreadable or unparseable (%r)" % (manifests[0], exc)
    views = doc.get("views", dict())
    if not isinstance(views, dict):
        return None, "the manifest %s views table is not a table" % (manifests[0],)
    targets = set()
    for name, tbl in views.items():
        tgt = tbl.get("target") if isinstance(tbl, dict) else None
        if not isinstance(tgt, str) or not tgt or os.path.isabs(tgt) or ".." in _components(tgt):
            return None, "the manifest %s view %r has no contained relative target" % (manifests[0], name)
        targets.add(os.path.normpath(tgt))
    return targets, None


def _rosters(roots):
    """The union frozen and view rosters over `roots` as ABSOLUTE normalized paths, with the
    root-relative spellings kept for the Bash token scan: ((frozen_abs, frozen_rel), (views_abs,
    views_rel), None) or (None, None, reason) on an R6 roster failure."""
    frozen_abs, frozen_rel, views_abs, views_rel = set(), set(), set(), set()
    for root in roots:
        frozen, reason = _frozen_paths(root)
        if reason is not None:
            return None, None, reason
        views, reason = _view_targets(root)
        if reason is not None:
            return None, None, reason
        for rel in frozen:
            frozen_rel.add(rel)
            frozen_abs.add(os.path.normpath(os.path.join(root, rel)))
        for rel in views:
            views_rel.add(rel)
            views_abs.add(os.path.normpath(os.path.join(root, rel)))
    return (frozen_abs, frozen_rel), (views_abs, views_rel), None


def _file_tool_rule(tool_name, tool_input, cwd):
    """R1-R4 and R6 for the write-capable file tools; returns a deny reason or None (allow)."""
    field = FILE_TOOL_TARGET[tool_name]
    if not isinstance(tool_input, dict):
        return "the %s payload carries no tool_input object; failing closed (R6)" % (tool_name,)
    target = tool_input.get(field)
    if not isinstance(target, str) or not target or any(ord(c) < 0x20 for c in target):
        return ("the %s payload field %s is missing, empty, non-string or control-character-bearing; "
                "failing closed (R6)" % (tool_name, field))
    cands = _candidates(target, cwd)
    if cands is None:
        return ("the %s target %r is relative and the payload carries no absolute session cwd to "
                "resolve it against; failing closed (R6)" % (tool_name, target))
    for cand in cands:
        reason = _store_rule(cand)
        if reason is not None:
            return reason
        roots = _roots_above(os.path.dirname(cand))
        if not roots:
            continue
        frozen, views, reason = _rosters(roots)
        if reason is not None:
            return reason + "; failing closed (R6)"
        if cand in frozen[0]:
            return ("%r is enumerated by an approved adoption plan as a retire- or migrate-disposed "
                    "old file: it stays frozen, byte-identical, until its retirement is recorded "
                    "(spec 14.1). Changing the approved work takes a fresh plan." % (target,))
        if cand in views[0]:
            return ("%r is a declared-view destination: views change only through opf render "
                    "(spec 5.8, 14.1)." % (target,))
    return None


def _pristine_tokens(command):
    """The whitespace-split tokens of a METACHARS-free command with leading VAR=value assignments
    dropped, or None when the command is not pristine (a metacharacter anywhere, even quoted)."""
    if any(c in METACHARS for c in command):
        return None
    tokens = command.split()
    while tokens and _ASSIGNMENT_RE.match(tokens[0]):
        tokens = tokens[1:]
    return tokens or None


def _is_sanctioned_opf(tokens):
    """A1: opf itself, or a python launcher (interpreter flags allowed) running a <...>/opf.py."""
    word = os.path.basename(tokens[0])
    if word == "opf":
        return True
    if not _PYTHON_RE.match(word):
        return False
    rest = tokens[1:]
    while rest and _PYFLAGS_RE.match(rest[0]):
        rest = rest[1:]
    return bool(rest) and os.path.basename(rest[0]) == "opf.py"


def _bash_rule(tool_input, cwd):
    """R5 and R6 for Bash; returns a deny reason or None (allow)."""
    if not isinstance(tool_input, dict) or not isinstance(tool_input.get("command"), str):
        return "the Bash payload carries no command string; failing closed (R6)"
    command = tool_input["command"]
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return ("the Bash payload carries no absolute session cwd, so the protected rosters cannot "
                "be resolved; failing closed (R6)")
    tokens = _pristine_tokens(command)
    if tokens and _is_sanctioned_opf(tokens):
        return None  # A1 holds even under a roster failure: the sanctioned repair path stays open
    mentioned = WORKING if WORKING in command else None
    frozen, views, reason = _rosters(_roots_above(cwd))
    if reason is not None:
        return reason + "; failing closed (R6)"
    kind = "the %s store tree" % (WORKING,)
    if mentioned is None:
        for rel in sorted(frozen[1]):
            if rel in command:
                mentioned, kind = rel, "the plan-frozen old file %r" % (rel,)
                break
    if mentioned is None:
        for rel in sorted(views[1]):
            if rel in command:
                mentioned, kind = rel, "the declared view %r" % (rel,)
                break
    if mentioned is None:
        return None
    if tokens:
        word = os.path.basename(tokens[0])
        if word in READONLY_WORDS:
            return None  # A2
        if word == "git" and len(tokens) >= 2 and tokens[1] in GIT_SAFE_SUBCOMMANDS:
            return None  # A3
    return ("this Bash command references %s and is not a pristine opf invocation, read-only command "
            "or store-safe git form, so it is denied fail-closed: a lexical hook cannot prove it "
            "read-only (R5). %s; read files through the platform Read tool or a pristine read-only "
            "command." % (kind, SANCTIONED))


def main():
    try:
        payload = _read_payload()
    except Unreadable as exc:
        sys.stderr.write("opf-pretooluse-deny: cannot evaluate: %s. Failing closed: the tool call is "
                         "blocked (spec 14.1 denial posture).\n" % (exc,))
        return 2
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str):
        sys.stderr.write("opf-pretooluse-deny: cannot evaluate: the payload carries no tool_name. "
                         "Failing closed: the tool call is blocked.\n")
        return 2
    cwd = payload.get("cwd")
    tool_input = payload.get("tool_input")
    if tool_name in FILE_TOOL_TARGET:
        reason = _file_tool_rule(tool_name, tool_input, cwd)
    elif tool_name == "Bash":
        reason = _bash_rule(tool_input, cwd)
    else:
        reason = None  # not a write-capable tool this hook covers (match-all registration sees every tool)
    if reason is not None:
        return _emit_deny(reason)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001  fail-closed backstop: never a silent allow on a crash
        sys.stderr.write("opf-pretooluse-deny: cannot evaluate: unexpected error (%r). Failing "
                         "closed: the tool call is blocked.\n" % (exc,))
        sys.exit(2)
