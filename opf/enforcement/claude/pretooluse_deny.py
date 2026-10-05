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
     opf verbs, which run under their own write guard). Both the lexically normalized target and the
     realpath resolution OF THE ORIGINAL SPELLING are checked (symlinks resolve before any `..`
     collapses), so neither an existing symlink nor a symlink-then-dotdot spelling evades R1.
  R2 adoption-archive writes. A target under `.working/archive/adoption/` is denied with the spec's own
     sentence: writes under `.working/archive/adoption/<run-id>/` are denied outright (spec 14.1).
     (R2 is a subset of R1; it exists so the archive denial is named, probed and reported on its own.)
  R3 frozen plan-enumerated old files. For every product root above the target (a directory holding a
     `.working` entry), each `plan.toml` under `.working/imported/adoption/<run-id>/` whose format is
     opf.adoption.plan/v2 contributes its retire- and migrate-disposed source paths; a target equal to
     one of them is denied: the file stays frozen, byte-identical, until its retirement is recorded
     (spec 14.1). A move- or keep-disposed source is adopter content and is not frozen.
  R4 declared-view writes. Each machine-store manifest's views targets are declared view destinations;
     a target equal to one is denied (views change only through `opf render`; spec 5.8, 14.1).
  R5 Bash writes the matcher CAN see. A Bash command whose text references a protected token is denied
     unless the WHOLE command is a single plain invocation of the sanctioned writer (allowance A1
     below); there is no other allowance. The protected tokens are the `.working` store tree (any
     substring spelling, and any session cwd that itself sits inside a `.working` tree, where every
     relative spelling lands in the store), and the frozen (R3) and declared-view (R4) paths, matched
     with path boundaries (a longer word such as PYTHON_VERSION does not trip a VERSION view; an
     absolute spelling of the same file does), over the raw command string AND, when the command lexes
     pristine, over its dequoted tokens (so a quote-split spelling such as VER''SION still references
     the view). The rosters are resolved from the product roots above the session cwd, above every
     absolute path spelled in the command, and above every dequoted absolute token (a quoted root with
     spaces binds through its whole operand, not only its space-free prefix); a command that spells
     more absolute paths than the discovery budget DENIES (cannot-evaluate) rather than truncating the
     scan. Reference, not proven mutation, is the trigger: a lexical hook cannot prove a referencing
     command read-only (sed -i, tee, cp, mv, truncate, dd, install, ln, rm, shell functions, aliases,
     and equally cat, grep, ls or git, whose environment assignments and options can make them write),
     so it fails closed and names the allowed route.
  R6 unreadable inputs fail closed. A missing or non-string target field, a control character in a
     target, a relative target with no readable session cwd, and every unreadable roster input DENY
     (the roster that would prove the operation safe cannot be computed), naming the unreadable input:
     an unreadable evidence home or store tree, a present-but-unreadable, dangling-symlink,
     NON-REGULAR (FIFO, device, socket), oversized, undecodable, unparseable or wrong-format plan.toml
     or manifest.toml (a manifest that parses without declaring the OPF standard, [opf]
     standard = "opf", fails validation the same way: never an empty view roster), a plan without its
     [[sources]] rows, a source row whose path is missing, non-string, empty or
     control-character-bearing, a disposition outside the planner vocabulary keep/retire/move/migrate,
     and an ambiguous (multi-manifest) machine store. Roster files are opened without blocking (O_NONBLOCK where the
     platform has it) after a regular-file check, re-checked on the open descriptor, and read through
     a bounded loop, so a FIFO or other trap input yields a prompt structured deny, never a stall and
     never an empty protection set. A payload unreadable at the envelope level exits 2 (blocking
     error), as above.
  R7 tools this hook cannot prove read-only. The named rules above cover the write-capable built-ins;
     every OTHER tool name (an MCP server's write tool, a shell tool other than Bash, a future
     built-in) is denied when any string in its payload references a protected token (the same tokens
     and rosters as R5) or RESOLVES, judged exactly as a file-tool target would be (cwd-joined, tilde
     expanded, realpathed), to the store, a frozen path or a declared view, and is denied
     cannot-evaluate when the payload exceeds the string-scan budget (the hook never judges a partial
     scan), because the hook cannot prove such a tool read-only. The known read-only
     built-ins (Read, Glob, Grep and the other names in READONLY_TOOLS) are allowed outright; tools
     that only launch further hooked tool calls (Task, Skill) are treated as read-only here because
     the launched calls are judged on their own.

THE SINGLE PRISTINE ALLOWANCE for a Bash command that references a protected token (the allowance
machinery is itself attack surface, so the read-only command words and read-only git forms earlier
revisions allowed are REMOVED rather than patched; the over-refusal is disclosed below). The command
must be PRISTINE under a quote-aware scan of the raw string: outside quotes no metacharacter may appear
(no semicolon, ampersand, pipe, angle bracket, backquote, dollar sign, parenthesis, brace, backslash,
carriage return or newline), a single-quoted span is wholly literal argument data, a double-quoted span
may carry no dollar sign, backquote or backslash (those expansions stay live inside double quotes), and
every quote must be terminated. So no second command, redirection, substitution or expansion can ride
along, while a sanctioned invocation may still QUOTE prose or a path that names a protected token (an
`opf record` title, an `opf render --root` operand with spaces or parentheses). A leading VAR=value
assignment is NOT skipped: an environment assignment changes what a program does (GIT_EXTERNAL_DIFF and
GIT_CONFIG_* make `git diff` execute an arbitrary writer), so an assignment-bearing command is never
the allowance.
  A1 the sanctioned writer, as a whole single plain invocation: `opf record ...` or `opf render ...`
     (the installed entry point as a bare word), or a bare python3 word (allowlisted interpreter flags
     only) running THE repository's own opf/tools/opf.py with verb record or render. The launched
     script is identified by realpath EQUALITY against the writer this hook ships beside (resolved
     from the hook's own installed location), never by a filename: a same-named opf.py anywhere else
     is not the writer. opf's own write guard, lease and journal govern what the writer may do. This
     allowance also holds under an R6 roster failure, so the in-session repair path stays open.

RESIDUALS (spec 14.1 requires each disclosed; the pack's residual register (slice (d)) and the plan's
per-platform residual coverage carry the same list):
  - A Bash write the matcher cannot see: a protected path reaching the filesystem through a shell
    variable, glob, alias, function, cd-relative spelling that drops the token, command or process
    substitution, an interpreter one-liner, or any other spelling in which no protected token appears
    textually in the command string. R5 is a lexical floor, not a sandbox.
  - A relative protected spelling judged from outside the product tree: when the session cwd sits
    outside every product root, only the ABSOLUTE spellings in the command can bind the rosters; a
    relative spelling of a frozen or view path resolves to no roster and passes the token scan.
  - A tool outside the named rules whose payload neither names a protected token nor resolves to one:
    R7's path pass judges every payload string as a resolvable target, so a relative or tilde
    spelling that RESOLVES to a protected path is caught, but a spelling the hook cannot resolve
    lexically (a server-side variable, an encoded path) is not; so is a read-only-listed tool that is
    in fact write-capable on some server. Edits made outside Claude Code entirely (any other editor, shell or
    tool) bypass this hook as before; the pre-commit and CI floor members are the overlapping controls.
  - Case-insensitive or normalizing filesystems (default APFS, NTFS): the `.working` component and the
    roster paths are compared byte-exactly, so a differently cased spelling (`.Working`) that aliases
    the same directory on such a filesystem is not caught (the spec 4.2 reserved-home aliasing
    disclosure, restated for this hook).
  - A relocated store (spec 4.1/4.3): the hook finds product roots only through a `.working` entry and
    reads no `.opf.toml` pointer, so after a relocation the product-root paths bind no rosters here;
    the floor members that read the pointer carry that topology.
  - The bare `opf` entry point of A1 resolves through PATH outside the hook's sight: a same-named
    program planted earlier on PATH runs instead of the writer (same-user tampering, as with the
    registration itself); the python3 launcher form carries no such residual, its script being
    realpath-bound to the repository's writer.
  - Per-clone installation and bypass: the settings.json registration is local configuration; a clone
    that never registered the hook runs no hook, and the same user can deregister or edit it
    (same-user tampering, canonical hand edits).
  - Platform hook-startup failures may fall through to the platform's normal permission flow.
  - Shell or interpreter wrapping of the platform itself is outside the hook's reach.
  - Over-approximation is the accepted cost of the fail-closed posture: R5/R7 deny EVERY
    protected-token-referencing command and tool call outside A1, read-only forms included, for
    example `cat .working/toml/counters.toml`, `grep -n x .working/toml/counters.toml`,
    `git diff .working/toml/counters.toml`, `git add TODO.md` for a declared view TODO.md, `git log
    LEGACY.md` for a plan-frozen LEGACY.md, and `opf doctor --root .working/..` (only the record and
    render verbs are the writer; reads go through the platform's Read tool). A command spelling more
    absolute paths than the discovery budget and an unknown-tool payload over the string-scan budget
    deny as cannot-evaluate even when reference-free. R6
    denies every write under a root whose roster carries ANY unreadable or malformed entry (a stray
    non-directory run entry included), R3 keeps denying a frozen path even after its retirement is
    recorded and the live file is gone (re-creating it directly stays denied; a fresh plan is the
    sanctioned route), and a `.working` or boundary-matched protected token inside prose (a commit
    message, say) still trips R5.
  - The IMPORTED record series is NOT yet protected here: the leaves `worklog.imported.toml` and
    `<type>.imported.index.toml` DIRECTLY inside the machine store directory (exactly
    `.working/<machine>/<leaf>`, no other depth) are exempt from R1 by name, because
    enforcement MUST NOT ship before the writer can perform every operation it forces (spec 14.1) and
    the import writer (`opf record import --batch`, spec 8.8) has not shipped. The imported-series
    protection slice lands with or after that writer and removes this exemption.

Offline, stdlib only (json, tomllib, os, re, stat, sys), no subprocess, no network. Launched isolated
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
import stat
import tomllib

# The platform payload bound: a PreToolUse payload (a MultiEdit's edit list included) is far below this;
# anything larger is not a payload this hook can honestly evaluate, so it fails closed at the envelope.
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024
# The roster-file bound (R6): a plan or manifest is a few KiB; a larger file is not a roster this hook
# can honestly evaluate, so it fails closed rather than reading unbounded bytes on the hot path.
MAX_ROSTER_BYTES = 1024 * 1024
# The scan budgets (R5/R7): a command or payload past either bound cannot be fully examined, and a
# partial scan must never be judged, so exceeding a budget DENIES (cannot-evaluate), never truncates.
MAX_ABS_PATHS = 64
MAX_PAYLOAD_STRINGS = 4096

WORKING = ".working"                      # the fixed store-tree name at a product root (spec 4.4)
ADOPTION_ARCHIVE = ("archive", "adoption")  # .working/archive/adoption/<run-id>/ (spec 14.1, 14.2)
ADOPTION_EVIDENCE = ("imported", "adoption")  # .working/imported/adoption/<run-id>/ (spec 14.2)
PLAN_FILENAME = "plan.toml"
PLAN_FORMAT = "opf.adoption.plan/v2"      # the bound plan format marker (spec 14.1)
# The machine-store discovery marker (spec 4.5, mirrored from _opf_store.STANDARD_TOKEN): a
# .working/<name>/manifest.toml is a machine store only when its [opf] table declares this standard;
# one that parses WITHOUT declaring it fails validation and denies, never an empty view roster.
MANIFEST_STANDARD = "opf"
# The planner's closed disposition vocabulary (_opf_adopt_plan._decisions); any other value is a
# malformed plan and R6 fails closed on it rather than silently skipping the row.
VALID_DISPOSITIONS = frozenset(("keep", "move", "migrate", "retire"))
FROZEN_DISPOSITIONS = ("migrate", "retire")  # the old-file dispositions that freeze in place (spec 14.1)
# The store-tree control subdirs (spec 4.2): a first component after .working/ outside this set is a
# machine-store candidate, where the imported-series leaf exemption below may apply.
CONTROL_SUBDIRS = frozenset(("imported", "archive", "staging", "journals"))
# The imported-series leaves (spec 8.3) stay EXEMPT until the import writer ships (module docstring).
IMPORTED_LEAF_RE = re.compile(r"\A(worklog\.imported\.toml|[A-Za-z0-9_-]+\.imported\.index\.toml)\Z")

# The write-capable file tools and the payload field naming each one's target.
FILE_TOOL_TARGET = dict(Write="file_path", Edit="file_path", MultiEdit="file_path",
                        NotebookEdit="notebook_path")
# The known read-only built-ins (R7): allowed outright. Task and Skill only launch further tool calls,
# each judged by this hook on its own, so they sit here too. Every OTHER tool name takes R7's scan.
READONLY_TOOLS = frozenset(("Read", "Glob", "Grep", "LS", "NotebookRead", "WebFetch", "WebSearch",
                            "Task", "Agent", "TodoWrite", "ExitPlanMode", "AskUserQuestion",
                            "BashOutput", "TaskOutput", "KillShell", "KillBash", "SlashCommand",
                            "Skill"))

# R5/A1 vocabularies. METACHARS is the UNQUOTED-dangerous set for the quote-aware pristine scan:
# semicolon, ampersand, pipe, the two angle brackets, backquote, dollar sign, the two parentheses, the
# two braces, backslash, carriage return and newline, each built from its code point so none appears
# literally here. DQ_LIVE is the subset that stays live INSIDE double quotes (dollar, backquote,
# backslash: expansion and substitution still run there).
METACHARS = frozenset(chr(c) for c in (59, 38, 124, 60, 62, 96, 36, 40, 41, 123, 125, 92, 13, 10))
DQ_LIVE = frozenset(chr(c) for c in (36, 96, 92))
QUOTES = frozenset(chr(c) for c in (39, 34))
# The writer verbs A1 accepts (module docstring): a single plain `opf record ...` or `opf render ...`
# invocation is the WHOLE allowance surface; every other opf verb, wrapper or launcher takes the deny.
WRITER_VERBS = frozenset(("record", "render"))
# The path-word characters for the boundary-matched roster-token scan (R5/R7): a roster path embedded
# in a longer run of these on its left, or of these or a separator on its right, is a DIFFERENT path
# (PYTHON_VERSION vs the view VERSION); a left slash still matches (an absolute spelling of the file).
WORD_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-")
# Absolute POSIX-path spellings inside a command or payload string (used to BIND rosters, never to
# allow): best-effort over the raw text (a path with spaces binds its whole operand only through the
# dequoted-token and payload-string passes); more matches than MAX_ABS_PATHS denies, never truncates.
ABS_PATH_RE = re.compile(r"/[A-Za-z0-9_./@%+,=~^-]+")
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
    """The absolute forms of `target` to judge: the lexically normalized path and the realpath of the
    ORIGINAL spelling. Resolving the original spelling first is load-bearing: in a spelling such as
    `link/../x`, the filesystem resolves the symlink BEFORE `..` climbs out of its destination, so a
    lexical collapse first (normpath dropping `link/..`) would judge a different file than the one the
    write reaches. normpath is applied only to the already-resolved result and to the lexical twin.
    A tilde spelling expands FIRST (the launched tool expands it too, so the hook must judge the
    expanded path, never a cwd-joined literal `~`)."""
    target = os.path.expanduser(target)
    if not os.path.isabs(target):
        if not isinstance(cwd, str) or not os.path.isabs(cwd):
            return None
        target = os.path.join(cwd, target)
    norm = os.path.normpath(target)
    try:
        real = os.path.normpath(os.path.realpath(target))
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
    if (len(after) == 2 and after[0] not in CONTROL_SUBDIRS and IMPORTED_LEAF_RE.match(after[1])):
        return None  # the imported-series machine-store leaves stay writer-less until the import writer ships
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


def _read_roster_toml(path, label):
    """Read one roster file fail-closed (R6): (doc, None) on a readable regular-file TOML document;
    (None, None) when `path` has no directory entry at all (the caller treats absence as no roster);
    (None, reason) on EVERYTHING else: a present-but-unreadable entry, a dangling symlink, a
    non-regular file (FIFO, device, socket), an oversized file, undecodable bytes, or unparseable
    TOML. The file is opened without blocking (O_NONBLOCK where the platform has it) only after a
    regular-file lstat/stat check, the open descriptor is re-checked, and the read loop is bounded,
    so a trap input yields a prompt reason, never a stall and never an empty roster."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, "%s %s is unreadable (%r)" % (label, path, exc)
    if stat.S_ISLNK(st.st_mode):
        try:
            st = os.stat(path)
        except OSError as exc:
            return None, ("%s %s is a symlink that does not resolve to a readable file (%r)"
                          % (label, path, exc))
    if not stat.S_ISREG(st.st_mode):
        return None, "%s %s is not a regular file" % (label, path)
    if st.st_size > MAX_ROSTER_BYTES:
        return None, "%s %s exceeds the %d-byte roster bound" % (label, path, MAX_ROSTER_BYTES)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError as exc:
        return None, "%s %s cannot be opened (%r)" % (label, path, exc)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None, "%s %s is not a regular file" % (label, path)
        chunks, budget = [], MAX_ROSTER_BYTES + 1
        while budget > 0:
            try:
                chunk = os.read(fd, min(65536, budget))
            except OSError as exc:
                return None, "%s %s cannot be read without blocking (%r)" % (label, path, exc)
            if not chunk:
                break
            chunks.append(chunk)
            budget -= len(chunk)
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if len(data) > MAX_ROSTER_BYTES:
        return None, "%s %s exceeds the %d-byte roster bound" % (label, path, MAX_ROSTER_BYTES)
    try:
        return tomllib.loads(data.decode("utf-8")), None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, ValueError) as exc:
        return None, "%s %s is unreadable or unparseable (%s)" % (label, path, exc)


def _frozen_paths(root):
    """The plan-enumerated frozen old-file paths of `root` (R3): (set of root-relative paths, None), or
    (None, reason) when a roster input is unreadable or malformed (R6 fails closed on it)."""
    home = os.path.join(root, WORKING, *ADOPTION_EVIDENCE)
    try:
        os.lstat(home)
    except FileNotFoundError:
        return set(), None  # no adoption evidence at this root: nothing is frozen by it
    except OSError as exc:
        return None, "the adoption evidence home %s is unreadable (%r)" % (home, exc)
    try:
        runs = sorted(os.listdir(home))
    except OSError as exc:
        return None, "the adoption evidence home %s is unreadable (%r)" % (home, exc)
    frozen = set()
    for run in runs:
        plan = os.path.join(home, run, PLAN_FILENAME)
        doc, reason = _read_roster_toml(plan, "the adoption plan")
        if reason is not None:
            return None, reason
        if doc is None:
            continue  # this run directory carries no plan entry at all: nothing to freeze from it
        if doc.get("format") != PLAN_FORMAT:
            return None, "the adoption plan %s does not carry format %r" % (plan, PLAN_FORMAT)
        rows = doc.get("sources")
        if not isinstance(rows, list):
            return None, ("the adoption plan %s carries no [[sources]] list (a plan whose rows "
                          "cannot be read freezes nothing it should)" % (plan,))
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("path"), str) \
                    or not row["path"] or any(ord(c) < 0x20 for c in row["path"]):
                return None, "the adoption plan %s carries a malformed source row" % (plan,)
            disposition = row.get("disposition")
            if disposition not in VALID_DISPOSITIONS:
                return None, ("the adoption plan %s carries disposition %r outside the planner "
                              "vocabulary %s" % (plan, disposition, sorted(VALID_DISPOSITIONS)))
            if disposition in FROZEN_DISPOSITIONS:
                rel = row["path"]
                if os.path.isabs(rel) or ".." in _components(rel):
                    return None, "the adoption plan %s enumerates a non-contained path %r" % (plan, rel)
                frozen.add(os.path.normpath(rel))
    return frozen, None


def _view_targets(root):
    """The declared-view targets of the machine store at `root` (R4): (set of root-relative paths,
    None), or (None, reason) on an unreadable, malformed or ambiguous manifest (R6 fails closed)."""
    working = os.path.join(root, WORKING)
    try:
        names = sorted(os.listdir(working))
    except OSError as exc:
        return None, "the store tree %s is unreadable (%r)" % (working, exc)
    found = []
    for name in names:
        if name in CONTROL_SUBDIRS:
            continue
        sub = os.path.join(working, name)
        try:
            st = os.stat(sub)
        except FileNotFoundError as exc:
            return None, "the store entry %s is a symlink that does not resolve (%r)" % (sub, exc)
        except OSError as exc:
            return None, "the store entry %s is unreadable (%r)" % (sub, exc)
        if not stat.S_ISDIR(st.st_mode):
            continue
        doc, reason = _read_roster_toml(os.path.join(sub, "manifest.toml"),
                                        "the machine-store manifest")
        if reason is not None:
            return None, reason
        if doc is None:
            continue  # a store subdir without a manifest entry is not a machine store
        mpath = os.path.join(sub, "manifest.toml")
        base = doc.get(MANIFEST_STANDARD)
        if not isinstance(base, dict) or base.get("standard") != MANIFEST_STANDARD:
            return None, ("the manifest %s parses but does not declare the OPF standard ([%s] "
                          "standard = %r, spec 4.5); a manifest that fails validation yields no "
                          "roster" % (mpath, MANIFEST_STANDARD, MANIFEST_STANDARD))
        found.append((mpath, doc))
    if not found:
        return set(), None
    if len(found) > 1:
        return None, ("multiple machine-store manifests under %s (store resolution fails closed on "
                      "an ambiguous store)" % (working,))
    manifest, doc = found[0]
    views = doc.get("views", dict())
    if not isinstance(views, dict):
        return None, "the manifest %s views table is not a table" % (manifest,)
    targets = set()
    for name, tbl in views.items():
        tgt = tbl.get("target") if isinstance(tbl, dict) else None
        if (not isinstance(tgt, str) or not tgt or os.path.isabs(tgt) or ".." in _components(tgt)
                or any(ord(c) < 0x20 for c in tgt)):
            return None, "the manifest %s view %r has no contained relative target" % (manifest, name)
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
    """The shell-aware tokens of a PRISTINE command (module docstring: no unquoted metacharacter or
    control character, single-quoted spans wholly literal, double-quoted spans with no live dollar
    sign, backquote or backslash, every quote terminated); or None when the command is not pristine.
    Leading VAR=value assignments are KEPT: an assignment changes what a program does, so an
    assignment-bearing command is never the sanctioned writer. Quoted spans tokenize as argument
    DATA, so a sanctioned invocation may quote prose that names a protected token."""
    tokens, cur, has_cur, mode = [], [], False, ""
    for ch in command:
        if mode == chr(39):
            if ch == chr(39):
                mode = ""
            else:
                cur.append(ch)
            continue
        if mode == chr(34):
            if ch == chr(34):
                mode = ""
            elif ch in DQ_LIVE or (ord(ch) < 0x20 and ch != "\t"):
                return None
            else:
                cur.append(ch)
            continue
        if ch in METACHARS or (ord(ch) < 0x20 and ch != "\t"):
            return None
        if ch in QUOTES:
            mode, has_cur = ch, True
            continue
        if ch in (" ", "\t"):
            if has_cur:
                tokens.append("".join(cur))
                cur, has_cur = [], False
            continue
        cur.append(ch)
        has_cur = True
    if mode:
        return None  # an unterminated quote is not a command this hook can read
    if has_cur:
        tokens.append("".join(cur))
    return tokens or None


def _sanctioned_writer():
    """The sanctioned writer's resolved identity: the opf CLI of the repository THIS hook ships in
    (opf/tools/opf.py, resolved relative to the hook's own realpathed location). A1 compares a
    launched script by realpath EQUALITY against this path, never by a basename, so a same-named
    opf.py anywhere else is not the writer."""
    here = os.path.dirname(os.path.realpath(__file__))
    return os.path.realpath(os.path.join(here, os.pardir, os.pardir, "tools", "opf.py"))


def _is_sanctioned_opf(tokens, cwd):
    """A1 (module docstring): the whole command is a single plain invocation of the sanctioned
    writer. `opf record ...` or `opf render ...` with the entry point as a BARE word, or a bare
    python3 word (allowlisted interpreter flags only) running the repository's own opf/tools/opf.py
    (realpath equality against the writer the hook ships beside) with verb record or render. A
    leading VAR=value assignment, a slash-bearing launcher word, another script or another verb is
    NOT the writer."""
    word = tokens[0]
    if _ASSIGNMENT_RE.match(word) or os.sep in word:
        return False
    if word == "opf":
        return len(tokens) >= 2 and tokens[1] in WRITER_VERBS
    if not _PYTHON_RE.match(word):
        return False
    rest = tokens[1:]
    while rest and _PYFLAGS_RE.match(rest[0]):
        rest = rest[1:]
    if len(rest) < 2 or rest[1] not in WRITER_VERBS:
        return False
    script = os.path.expanduser(rest[0])
    if not os.path.isabs(script):
        if not isinstance(cwd, str) or not os.path.isabs(cwd):
            return False
        script = os.path.join(cwd, script)
    try:
        resolved = os.path.realpath(script)
    except OSError:
        return False
    writer = _sanctioned_writer()
    return resolved == writer and os.path.isfile(writer)


def _mentions_rel(rel, text):
    """True when the root-relative roster path `rel` appears in `text` bounded as a path: embedded in
    a longer word on the left (PYTHON_VERSION vs the view VERSION) or continued by a word char or a
    separator on the right (VERSION.bak, VERSION/) it is a DIFFERENT path and does not match; a left
    slash still matches, because an absolute spelling of the same file cannot be told apart
    lexically (an over-match is an over-refusal, never a bypass)."""
    start, n = 0, len(rel)
    while True:
        i = text.find(rel, start)
        if i < 0:
            return False
        before = text[i - 1] if i > 0 else ""
        after = text[i + n] if i + n < len(text) else ""
        if (before == "" or before not in WORD_CHARS) and (
                after == "" or (after not in WORD_CHARS and after != "/")):
            return True
        start = i + 1


def _bound_roots(text, cwd, extras=()):
    """The product roots the rosters are resolved from (R5/R7): (roots, None) or (None, reason) when
    the absolute-path discovery budget is exceeded (a truncated scan could silently drop the one
    protected spelling, so the hook denies instead). Roots are taken at or above the session cwd
    (when there is one), above every absolute path spelled in `text`, and above every `extras` entry
    (a dequoted Bash token or payload string, tilde-expanded) that is absolute, so an absolute
    protected spelling is judged even when the session sits outside its product tree and a quoted
    root with spaces binds through its whole operand."""
    roots = list(_roots_above(cwd)) if isinstance(cwd, str) and os.path.isabs(cwd) else []
    matches = ABS_PATH_RE.findall(text)
    if len(matches) > MAX_ABS_PATHS:
        return None, ("the command or payload spells %d absolute paths, over the %d-path roster "
                      "discovery budget, and this hook will not judge a truncated scan"
                      % (len(matches), MAX_ABS_PATHS))
    cands = set(matches)
    for extra in extras:
        if isinstance(extra, str) and extra:
            expanded = os.path.expanduser(extra)
            if os.path.isabs(expanded):
                cands.add(expanded)
    for cand in sorted(cands):
        for root in _roots_above(os.path.normpath(cand)):
            if root not in roots:
                roots.append(root)
    return roots, None


def _reference_kind(text, cwd, rosters_text=None):
    """The protected token `text` references, as a prose kind, or None. `rosters_text` carries the
    ((frozen_abs, frozen_rel), (views_abs, views_rel)) pair already resolved by the caller."""
    if WORKING in text:
        return "the %s store tree" % (WORKING,)
    if isinstance(cwd, str) and WORKING in _components(cwd):
        return ("the %s store tree (the session cwd is inside it, so every relative spelling lands "
                "there)" % (WORKING,))
    frozen, views = rosters_text
    for rel in sorted(frozen[1]):
        if _mentions_rel(rel, text):
            return "the plan-frozen old file %r" % (rel,)
    for rel in sorted(views[1]):
        if _mentions_rel(rel, text):
            return "the declared view %r" % (rel,)
    return None


def _bash_rule(tool_input, cwd):
    """R5 and R6 for Bash; returns a deny reason or None (allow)."""
    if not isinstance(tool_input, dict) or not isinstance(tool_input.get("command"), str):
        return "the Bash payload carries no command string; failing closed (R6)"
    command = tool_input["command"]
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return ("the Bash payload carries no absolute session cwd, so the protected rosters cannot "
                "be resolved; failing closed (R6)")
    tokens = _pristine_tokens(command)
    if tokens and _is_sanctioned_opf(tokens, cwd):
        return None  # A1 holds even under a roster failure: the sanctioned repair path stays open
    scan = command if tokens is None else command + chr(10) + chr(10).join(tokens)
    roots, reason = _bound_roots(command, cwd, tokens or ())
    if reason is not None:
        return reason + "; failing closed (R6)"
    frozen, views, reason = _rosters(roots)
    if reason is not None:
        return reason + "; failing closed (R6)"
    kind = _reference_kind(scan, cwd, ((frozen[0], frozen[1]), (views[0], views[1])))
    if kind is None:
        return None
    return ("this Bash command references %s and is not a single plain invocation of the sanctioned "
            "writer (opf record or opf render): a lexical hook cannot prove any other referencing "
            "command read-only, so it is denied fail-closed (R5). %s; read protected files through "
            "the platform Read tool." % (kind, SANCTIONED))


def _payload_strings(value):
    """Every string in a JSON payload value (keys included), depth-first: (strings, False), or
    (partial strings, True) when the MAX_PAYLOAD_STRINGS budget is exceeded, in which case the
    caller DENIES (a truncated scan could have dropped the one protected spelling)."""
    out, stack = [], [value]
    while stack:
        v = stack.pop()
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            for k, sub in v.items():
                if isinstance(k, str):
                    out.append(k)
                stack.append(sub)
        elif isinstance(v, (list, tuple)):
            stack.extend(v)
        if len(out) > MAX_PAYLOAD_STRINGS:
            return out, True
    return out, False


def _other_tool_rule(tool_name, tool_input, cwd):
    """R7 for every tool outside the named rules: the hook cannot prove such a tool read-only, so
    its call is denied when any payload string references a protected token textually (the same scan
    as R5) OR resolves, judged exactly as a file-tool target would be (cwd-joined, tilde expanded,
    realpathed), to the store, a frozen path or a declared view; and denied cannot-evaluate when the
    string-scan budget is exceeded (the hook never judges a partial scan). A payload with neither a
    textual nor a resolvable protected reference is allowed (the disclosed residual). The known
    read-only built-ins are allowed outright."""
    if tool_name in READONLY_TOOLS:
        return None
    strings, truncated = _payload_strings(tool_input)
    if truncated:
        return ("the tool %r is not one this hook knows to be read-only and its payload exceeds the "
                "%d-string scan budget, so it cannot be fully examined; failing closed (R6, R7)"
                % (tool_name, MAX_PAYLOAD_STRINGS))
    text = chr(10).join(strings)
    if not text:
        return None
    paths = []
    for s in strings:
        if 0 < len(s) <= 4096 and not any(ord(c) < 0x20 for c in s):
            cands = _candidates(s, cwd)
            if cands:
                paths.extend(cands)
    roots, reason = _bound_roots(text, cwd, paths)
    if reason is not None:
        return reason + "; failing closed (R6)"
    frozen, views, reason = _rosters(roots)
    if reason is not None:
        return reason + "; failing closed (R6)"
    kind = _reference_kind(text, cwd, ((frozen[0], frozen[1]), (views[0], views[1])))
    if kind is None:
        for cand in paths:
            if _store_rule(cand) is not None:
                kind = "the %s store tree (a payload string resolves into it)" % (WORKING,)
                break
            if cand in frozen[0]:
                kind = "a plan-frozen old file (a payload string resolves to it)"
                break
            if cand in views[0]:
                kind = "a declared view (a payload string resolves to it)"
                break
    if kind is None:
        return None
    return ("the tool %r is not one this hook knows to be read-only and its payload references %s, "
            "so it is denied fail-closed (R7). %s; use the platform's Write/Edit tools or Bash for "
            "unprotected paths, and the opf CLI for the store." % (tool_name, kind, SANCTIONED))


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
        reason = _other_tool_rule(tool_name, tool_input, cwd)
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
