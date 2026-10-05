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
  R5 Bash writes the matcher CAN see. EVERY Bash command is first dequoted WHOLE by a shell-aware
     loose lexer (quoted spans, backslash escapes, ANSI-C dollar-quoted spans, word-start comments,
     here-document bodies and arithmetic spans are read; unquoted shell operator characters split
     words), so a quoted operand stays one word even when redirection or sequencing rides beside it.
     A command whose quote or here-document structure cannot be read to the end (an unterminated
     quote, a trailing backslash, an undecodable dollar-quote escape, an unreadable here-document
     delimiter, an unclosed arithmetic span) DENIES as cannot-evaluate: the shell could run such a
     command differently than this hook read it, and a partially read command is never judged. A
     command whose raw text or dequoted words reference a protected token is denied unless the WHOLE
     command is a single plain invocation of the sanctioned writer (allowance A1 below); there is no
     other allowance. The protected tokens are the `.working` store tree (any substring spelling,
     and any session cwd that itself sits inside a `.working` tree, where every relative spelling
     lands in the store), and the frozen (R3) and declared-view (R4) paths, matched with path
     boundaries (a longer word such as PYTHON_VERSION does not trip a VERSION view; an absolute
     spelling of the same file does) over the raw command string AND over every dequoted word (so a
     quote-split spelling such as VER''SION still references the view). The rosters are resolved
     from the product roots above the session cwd, above every absolute path spelled in the raw
     text, and above every dequoted word that is absolute (a quoted root with spaces binds through
     its whole operand, beside redirection and sequencing too); a command that spells more absolute
     paths or operands than the discovery budget DENIES (cannot-evaluate) rather than truncating
     the scan. Every dequoted word is ALSO resolved as a path exactly as a file-tool target would
     be (cwd-joined, tilde expanded, realpathed) and the command is denied when a resolved word
     lands in the store, on a frozen or view path, or on the enforcement pack's own files (R8);
     relative words resolve only while the command stays within the word-resolution budget
     (absolute words always resolve; past the budget the disclosed lexical floor covers relative
     spellings). Reference, not proven mutation, is the trigger: a lexical hook cannot prove a referencing
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
     and an ambiguous (multi-manifest) machine store. An ABSENT roster leaf under a real directory
     chain is absence (nothing to read), but a roster or evidence path whose deepest EXISTING
     ancestor is a dangling symlink or a non-directory, and a `.working` entry that exists without
     resolving to a directory, are CANNOT-EVALUATE and deny: an unresolved ancestor is never read
     as an absent (empty) roster. Roster files are opened without blocking (O_NONBLOCK where the
     platform has it) after a regular-file check, re-checked on the open descriptor, and read through
     a bounded loop, so a FIFO or other trap input yields a prompt structured deny, never a stall and
     never an empty protection set. A payload unreadable at the envelope level exits 2 (blocking
     error), as above.
  R7 tools this hook cannot prove read-only. The named rules above cover the write-capable built-ins;
     every OTHER tool name (an MCP server's write tool, a shell tool other than Bash, a future
     built-in) is denied when any string in its payload references a protected token (the same tokens
     and rosters as R5) or RESOLVES, judged exactly as a file-tool target would be (cwd-joined, tilde
     expanded, realpathed, control characters included: a path may legally carry them), to the
     store, a frozen path, a declared view or the pack's own files (R8), and is denied
     cannot-evaluate when the payload exceeds the string-scan budget (the hook never judges a partial
     scan), because the hook cannot prove such a tool read-only. A payload string longer than a
     platform path (PATH_MAX) cannot name a reachable file and is judged textually only. The known read-only
     built-ins (Read, Glob, Grep and the other names in READONLY_TOOLS) are allowed outright; tools
     that only launch further hooked tool calls (Task, Skill) are treated as read-only here because
     the launched calls are judged on their own.
  R8 enforcement self-protection. A file-tool target, a resolved Bash word or a resolved payload
     string that lands inside the enforcement pack's own tree (this hook's opf/enforcement/
     directory or the writer's opf/tools/ directory, both resolved from the hook's own installed
     location) or on a bound product root's `.claude/settings.json` or `.claude/settings.local.json`
     (the hook registration) is denied: the gated tools must not be able to rewrite the gate, the
     writer or the registration in one call. A plain pristine `python3 <script in the pack tree>`
     invocation stays allowed (the pack's own tools must remain runnable; A1 alone governs the
     writer verbs). What R8 CANNOT protect is disclosed under RESIDUALS.

THE SINGLE PRISTINE ALLOWANCE for a Bash command that references a protected token (the allowance
machinery is itself attack surface, so the read-only command words and read-only git forms earlier
revisions allowed are REMOVED rather than patched; the over-refusal is disclosed below). The command
must be PRISTINE under a quote-aware scan of the raw string: outside quotes no metacharacter may appear
(no semicolon, ampersand, pipe, angle bracket, backquote, dollar sign, parenthesis, brace, backslash,
carriage return or newline), a single-quoted span is wholly literal argument data, a double-quoted span
may carry no dollar sign, backquote or backslash (those expansions stay live inside double quotes), no
quoted span may carry a control character (a writer title with a literal newline takes the deny, a
disclosed over-refusal: spell writer arguments without control characters), and
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
    outside every product root, only the ABSOLUTE spellings (raw or dequoted) bind the rosters; a
    relative spelling of a frozen or view path that climbs into an unbound product tree resolves to
    no bound roster and passes both the token scan and the word-resolution pass (the store tree
    itself still denies by its `.working` component).
  - A tool outside the named rules whose payload neither names a protected token nor resolves to one:
    R7's path pass judges every payload string as a resolvable target, so a relative or tilde
    spelling that RESOLVES to a protected path is caught, but a spelling the hook cannot resolve
    lexically (a server-side variable, an encoded path, a string longer than a platform path) is
    not; so is a read-only-listed tool that is
    in fact write-capable on some server. Edits made outside Claude Code entirely (any other editor, shell or
    tool) bypass this hook as before; the pre-commit and CI floor members are the overlapping controls.
  - Case-insensitive or normalizing filesystems (default APFS, NTFS): the `.working` component and the
    roster paths are compared byte-exactly, so a differently cased spelling (`.Working`) that aliases
    the same directory on such a filesystem is not caught (the spec 4.2 reserved-home aliasing
    disclosure, restated for this hook).
  - A relocated store (spec 4.1/4.3): the hook finds product roots only through a `.working` entry and
    reads no `.opf.toml` pointer, so after a relocation the product-root paths bind no rosters here;
    the floor members that read the pointer carry that topology.
  - Tampering outside R8's reach (spec 14.1 accepts same-user tampering as a residual; none of
    this is prevented here): the bare `opf` entry point AND the bare `python3` launcher word of A1
    BOTH resolve through PATH outside the hook's sight, so a same-named program planted earlier on
    PATH runs instead (the realpath binding pins the launched SCRIPT argument, never the
    interpreter word that reads it); a user-level or enterprise settings file outside every product
    root can deregister the hook, as can the platform's own configuration surfaces and any edit
    made outside Claude Code; and a pre-existing hardlink alias of a protected file is a DIFFERENT
    path to the same inode, which the path comparison (normpath and realpath) cannot see, so a
    write through such an alias passes (same-user preparation).
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
    absolute paths or operands than the discovery budget and an unknown-tool payload over the
    string-scan budget deny as cannot-evaluate even when reference-free, as does a command whose
    quote or here-document structure cannot be read to the end (the shell could run it differently
    than the hook read it). R8 denies rewriting the pack's own files and the per-product
    registration through the gated tools (read them with the Read tool; change them outside a
    hooked session), and the word-resolution pass denies a command that merely names a protected or
    pack-owned file as a resolvable argument. R6
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
# The scan budgets (R5/R7): a command or payload past the absolute-path or string budget cannot be
# fully examined, and a partial scan must never be judged, so exceeding one DENIES (cannot-evaluate),
# never truncates. MAX_RESOLVED_WORDS bounds only the EXTRA relative-word resolution pass (absolute
# words always resolve): past it the pass narrows to absolute words, which keeps the disclosed
# lexical floor intact without denying a large inline script outright. MAX_PATH_CHARS is PATH_MAX:
# a longer string cannot name a reachable file, so the path passes judge it textually only.
MAX_ABS_PATHS = 512
MAX_PAYLOAD_STRINGS = 4096
MAX_RESOLVED_WORDS = 512
MAX_PATH_CHARS = 4096

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
# The loose-lexer word separators (R5): unquoted shell operator characters end a word the way the
# shell's own lexer does (semicolon, ampersand, pipe, the two angle brackets, the two parentheses,
# the two braces, backquote, carriage return; space, tab, newline and the remaining control
# characters are handled in the lexer itself), so a quoted operand beside a redirection stays whole.
WORD_SEPARATORS = frozenset(chr(c) for c in (59, 38, 124, 60, 62, 40, 41, 123, 125, 96, 13))
# The backslash-escapable set INSIDE double quotes (dollar, backquote, double quote, backslash,
# newline); before any other character a double-quoted backslash stays literal, as in the shell.
DQ_ESCAPABLE = frozenset(chr(c) for c in (36, 96, 34, 92, 10))
# The single-character ANSI-C escapes of a dollar-quoted span, each decoded to its exact character.
ANSI_SIMPLE = dict((("a", chr(7)), ("b", chr(8)), ("e", chr(27)), ("E", chr(27)), ("f", chr(12)),
                    ("n", chr(10)), ("r", chr(13)), ("t", chr(9)), ("v", chr(11)),
                    (chr(92), chr(92)), (chr(39), chr(39)), (chr(34), chr(34)), ("?", "?")))
# The hook-registration leaves R8 protects directly under a bound product root's .claude/ entry.
REGISTRATION_LEAVES = frozenset(("settings.json", "settings.local.json"))
# The writer verbs A1 accepts (module docstring): a single plain `opf record ...` or `opf render ...`
# invocation is the WHOLE allowance surface; every other opf verb, wrapper or launcher takes the deny.
WRITER_VERBS = frozenset(("record", "render"))
# The path-word characters for the boundary-matched roster-token scan (R5/R7): a roster path embedded
# in a longer run of these on its left, or of these or a separator on its right, is a DIFFERENT path
# (PYTHON_VERSION vs the view VERSION); a left slash still matches (an absolute spelling of the file).
WORD_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-")
# Absolute POSIX-path spellings inside a command or payload string (used to BIND rosters, never to
# allow): best-effort over the raw text (a path with spaces binds its whole operand only through the
# dequoted-word and payload-string passes). A match glued to a preceding letter or digit is the
# inside of a relative spelling or of prose (and/or, a URL path), not an absolute operand, so it
# neither binds nor counts against the discovery budget (an absolute operand in shell or JSON is
# never glued to a word character); more matches than MAX_ABS_PATHS denies, never truncates.
ABS_PATH_RE = re.compile(r"(?<![A-Za-z0-9])/[A-Za-z0-9_./@%+,=~^-]+")
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
    except (OSError, ValueError):
        real = norm  # a NUL or unencodable spelling cannot reach the filesystem; judge the text
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
    """Every product root at or above `path` (a directory holding a `.working` entry, nearest
    first): (roots, None), or (None, reason) when a `.working` entry EXISTS somewhere above but
    does not resolve to a directory (a dangling symlink or a non-directory): a store tree that
    cannot be read is cannot-evaluate, never an absent root (R6)."""
    roots = []
    cur = os.path.normpath(path)
    while True:
        if os.path.basename(cur) != WORKING:
            entry = os.path.join(cur, WORKING)
            if os.path.isdir(entry):
                roots.append(cur)
            elif os.path.lexists(entry):
                return None, ("the store entry %s exists but does not resolve to a directory (a "
                              "dangling symlink or a non-directory store tree cannot be read)"
                              % (entry,))
        parent = os.path.dirname(cur)
        if parent == cur:
            return roots, None
        cur = parent


def _unresolved_ancestor(path):
    """The fail-closed absence check (R6): None when `path` is genuinely absent under a real
    directory chain (its deepest EXISTING ancestor resolves to a directory), or a reason when some
    existing ancestor is a dangling symlink or a non-directory: a FileNotFoundError through such an
    ancestor is an UNRESOLVED roster location, never an absent leaf, and must deny."""
    cur = os.path.dirname(os.path.normpath(path))
    while True:
        if os.path.lexists(cur):
            if os.path.isdir(cur):
                return None
            return ("%s exists on the path to it but does not resolve to a directory (a dangling "
                    "symlink or a non-directory ancestor)" % (cur,))
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def _read_roster_toml(path, label):
    """Read one roster file fail-closed (R6): (doc, None) on a readable regular-file TOML document;
    (None, None) when `path` is genuinely absent under a real directory chain (the caller treats
    absence as no roster; an absence reached through a dangling or non-directory ANCESTOR is a
    reason instead: an unresolved roster location is cannot-evaluate, never an empty roster);
    (None, reason) on EVERYTHING else: a present-but-unreadable entry, a dangling symlink, a
    non-regular file (FIFO, device, socket), an oversized file, undecodable bytes, or unparseable
    TOML. The file is opened without blocking (O_NONBLOCK where the platform has it) only after a
    regular-file lstat/stat check, the open descriptor is re-checked, and the read loop is bounded,
    so a trap input yields a prompt reason, never a stall and never an empty roster."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        reason = _unresolved_ancestor(path)
        if reason is not None:
            return None, "%s %s cannot be located: %s" % (label, path, reason)
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
        reason = _unresolved_ancestor(home)
        if reason is not None:
            return None, ("the adoption evidence home %s cannot be located: %s (an unresolved "
                          "evidence home is never read as an absent roster)" % (home, reason))
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


def _abs_spellings(root, rel):
    """The lexical AND realpath absolute spellings of one roster entry: a protected path that runs
    through a symlinked directory (docs -> site_docs) is the same file through its REAL directory,
    so each roster entry contributes both spellings and a write through either one matches."""
    apath = os.path.normpath(os.path.join(root, rel))
    try:
        rpath = os.path.normpath(os.path.realpath(apath))
    except (OSError, ValueError):
        return (apath,)
    return (apath, rpath)


def _rosters(roots):
    """The union frozen and view rosters over `roots` as ABSOLUTE normalized paths (each entry in
    both its lexical and its realpath spelling), with the root-relative spellings kept for the Bash
    token scan: ((frozen_abs, frozen_rel), (views_abs, views_rel), None) or (None, None, reason) on
    an R6 roster failure."""
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
            frozen_abs.update(_abs_spellings(root, rel))
        for rel in views:
            views_rel.add(rel)
            views_abs.update(_abs_spellings(root, rel))
    return (frozen_abs, frozen_rel), (views_abs, views_rel), None


def _file_tool_rule(tool_name, tool_input, cwd):
    """R1-R4, R6 and R8 for the write-capable file tools; returns a deny reason or None (allow)."""
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
        reason = _guard_rule(cand)
        if reason is not None:
            return reason
        roots, reason = _roots_above(os.path.dirname(cand))
        if reason is not None:
            return reason + "; failing closed (R6)"
        if not roots:
            continue
        reason = _registration_rule(cand, roots)
        if reason is not None:
            return reason
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


def _guarded_prefixes():
    """The enforcement pack's own directories (R8), resolved from the hook's installed location:
    the enforcement tree this hook ships in and the writer's opf/tools tree beside it."""
    here = os.path.dirname(os.path.realpath(__file__))
    pack = os.path.realpath(os.path.join(here, os.pardir))
    tools = os.path.realpath(os.path.join(here, os.pardir, os.pardir, "tools"))
    return (pack, tools)


def _guard_rule(candidate):
    """R8 over one absolute candidate path: a deny reason when it lands inside the enforcement
    pack's own tree (the hook, the sanctioned writer and their siblings), or None."""
    for prefix in _guarded_prefixes():
        if candidate == prefix or candidate.startswith(prefix + os.sep):
            return ("%r resolves into the enforcement pack's own tree (%s): the deny hook, the "
                    "sanctioned writer and their siblings cannot be rewritten through the tool "
                    "calls they gate (R8). Change enforcement code outside a hooked session."
                    % (candidate, prefix))
    return None


def _registration_rule(candidate, roots):
    """R8 over one absolute candidate path: a deny reason when it is a bound product root's hook
    registration (.claude/settings.json or .claude/settings.local.json), or None."""
    base = os.path.basename(candidate)
    if base not in REGISTRATION_LEAVES:
        return None
    for root in roots:
        if candidate == os.path.normpath(os.path.join(root, ".claude", base)):
            return ("%r is the Claude Code settings registration of the product root %r: the hook "
                    "registration cannot be rewritten through the tool calls it gates (R8). Edit "
                    "the registration outside a hooked session." % (candidate, root))
    return None


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


def _ansi_c_span(text, start):
    """Decode one ANSI-C dollar-quoted span body beginning at `start` (just past the opening
    quote): (characters, index past the closing quote, None), or (None, None, reason) when the span
    is unterminated or carries an escape this hook cannot decode EXACTLY as the shell does (a
    partially decoded operand could hide a protected spelling, so the caller fails closed)."""
    out = []
    i, n = start, len(text)
    while i < n:
        ch = text[i]
        if ch == chr(39):
            return out, i + 1, None
        if ch != chr(92):
            out.append(ch)
            i += 1
            continue
        i += 1
        if i >= n:
            break
        esc = text[i]
        if esc in ANSI_SIMPLE:
            out.append(ANSI_SIMPLE[esc])
            i += 1
            continue
        if esc in ("x", "u", "U"):
            width = dict(x=2, u=4, U=8)[esc]
            j = i + 1
            digits = []
            while j < n and len(digits) < width and text[j] in "0123456789abcdefABCDEF":
                digits.append(text[j])
                j += 1
            if not digits:
                return None, None, "a dollar-quoted escape this hook does not decode"
            value = int("".join(digits), 16)
            if value > 0x10FFFF or (esc == "x" and value > 0x7F):
                # a raw byte above ASCII or an out-of-range code point cannot be mapped
                # faithfully onto the str paths this hook compares, so it fails closed.
                return None, None, "a dollar-quoted escape this hook does not decode"
            out.append(chr(value))
            i = j
            continue
        if esc in "01234567":
            j = i
            digits = []
            while j < n and len(digits) < 3 and text[j] in "01234567":
                digits.append(text[j])
                j += 1
            value = int("".join(digits), 8)
            if value > 0x7F:
                return None, None, "a dollar-quoted escape this hook does not decode"
            out.append(chr(value))
            i = j
            continue
        return None, None, "a dollar-quoted escape this hook does not decode"
    return None, None, "an unterminated dollar-quoted span"


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


def _loose_words(command):
    """R5's shell-aware loose dequote of the WHOLE command: (words, None), or (None, reason) when
    the quote or here-document structure cannot be read to the end. A quoted span joins the current
    word (a quoted operand beside redirection or sequencing stays whole), an unquoted operator or
    control character splits words, a word-start # comment runs to end of line, a here-document
    body contributes each of its lines as one data word, and an arithmetic $((...)) span stays
    inside its word (never a false here-document). Unlike _pristine_tokens this lexer reads EVERY
    command; its words feed the roster token scan, the root binding and the word-resolution pass,
    never any allowance."""
    words = []
    cur = []
    has = False
    pending = []
    i, n = 0, len(command)
    while i < n:
        ch = command[i]
        if ch == chr(39):
            end = command.find(chr(39), i + 1)
            if end < 0:
                return None, "an unterminated single-quoted span"
            cur.append(command[i + 1:end])
            has = True
            i = end + 1
            continue
        if ch == chr(34):
            i += 1
            has = True
            while True:
                if i >= n:
                    return None, "an unterminated double-quoted span"
                dq = command[i]
                if dq == chr(34):
                    i += 1
                    break
                if dq == chr(92):
                    if i + 1 >= n:
                        return None, "an unterminated double-quoted span"
                    nxt = command[i + 1]
                    if nxt in DQ_ESCAPABLE:
                        if nxt != chr(10):
                            cur.append(nxt)
                    else:
                        cur.append(dq)
                        cur.append(nxt)
                    i += 2
                    continue
                cur.append(dq)
                i += 1
            continue
        if ch == chr(92):
            if i + 1 >= n:
                return None, "a trailing backslash"
            if command[i + 1] != chr(10):
                cur.append(command[i + 1])
                has = True
            i += 2
            continue
        if ch == chr(36) and command[i + 1:i + 2] == chr(39):
            got, nxt, reason = _ansi_c_span(command, i + 2)
            if reason is not None:
                return None, reason
            cur.append("".join(got))
            has = True
            i = nxt
            continue
        if ch == chr(36) and command[i + 1:i + 3] == "((":
            end = command.find("))", i + 3)
            if end < 0:
                return None, "an unclosed arithmetic span"
            cur.append(command[i:end + 2])
            has = True
            i = end + 2
            continue
        if ch == "#" and not has:
            end = command.find(chr(10), i)
            if end < 0:
                break
            i = end
            continue
        if ch == chr(10):
            if has:
                words.append("".join(cur))
            cur, has = [], False
            i += 1
            while pending:
                delim, strip_tabs = pending.pop(0)
                while i <= n:
                    end = command.find(chr(10), i)
                    stop = end if end >= 0 else n
                    line = command[i:stop]
                    i = end + 1 if end >= 0 else n
                    marker = line.lstrip(chr(9)) if strip_tabs else line
                    if marker == delim:
                        break
                    if line:
                        words.append(line)
                    if end < 0:
                        break
            continue
        if ch == "<" and command[i:i + 3] == "<<<":
            if has:
                words.append("".join(cur))
            cur, has = [], False
            i += 3
            continue
        if ch == "<" and command[i:i + 2] == "<<":
            if has:
                words.append("".join(cur))
            cur, has = [], False
            i += 2
            strip_tabs = command[i:i + 1] == "-"
            if strip_tabs:
                i += 1
            while i < n and command[i] in (" ", chr(9)):
                i += 1
            delim = []
            while i < n:
                dc = command[i]
                if dc in (" ", chr(9), chr(10)) or dc in WORD_SEPARATORS:
                    break
                if dc in QUOTES:
                    end = command.find(dc, i + 1)
                    if end < 0:
                        return None, "an unterminated here-document delimiter"
                    delim.append(command[i + 1:end])
                    i = end + 1
                    continue
                if dc == chr(92):
                    if i + 1 >= n:
                        return None, "a trailing backslash"
                    delim.append(command[i + 1])
                    i += 2
                    continue
                if dc == chr(36):
                    return None, "a here-document delimiter this hook cannot read"
                delim.append(dc)
                i += 1
            if not delim:
                return None, "a here-document with no delimiter"
            pending.append(("".join(delim), strip_tabs))
            continue
        if ch in WORD_SEPARATORS or ch in (" ", chr(9)) or ord(ch) < 32:
            if has:
                words.append("".join(cur))
            cur, has = [], False
            i += 1
            continue
        cur.append(ch)
        has = True
        i += 1
    if has:
        words.append("".join(cur))
    return words, None


def _bound_roots(text, cwd, extras=()):
    """The product roots the rosters are resolved from (R5/R7): (roots, None); or (None, reason)
    when the absolute-path discovery budget is exceeded (a truncated scan could silently drop the
    one protected spelling, so the hook denies instead) or when a store tree above a bound location
    cannot be read (_roots_above fails closed). Roots are taken at or above the session cwd (when
    there is one), above every absolute path spelled in `text`, and above every `extras` entry (a
    dequoted Bash word or payload string, tilde-expanded) that is absolute, so an absolute
    protected spelling is judged even when the session sits outside its product tree and a quoted
    root with spaces binds through its whole operand."""
    roots = []
    if isinstance(cwd, str) and os.path.isabs(cwd):
        got, reason = _roots_above(cwd)
        if reason is not None:
            return None, reason
        roots = list(got)
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
    if len(cands) > MAX_ABS_PATHS:
        return None, ("the command or payload spells %d absolute operands, over the %d-path roster "
                      "discovery budget, and this hook will not judge a truncated scan"
                      % (len(cands), MAX_ABS_PATHS))
    for cand in sorted(cands):
        got, reason = _roots_above(os.path.normpath(cand))
        if reason is not None:
            return None, reason
        for root in got:
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
    """R5, R6 and R8 for Bash; returns a deny reason or None (allow)."""
    if not isinstance(tool_input, dict) or not isinstance(tool_input.get("command"), str):
        return "the Bash payload carries no command string; failing closed (R6)"
    command = tool_input["command"]
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return ("the Bash payload carries no absolute session cwd, so the protected rosters cannot "
                "be resolved; failing closed (R6)")
    tokens = _pristine_tokens(command)
    if tokens and _is_sanctioned_opf(tokens, cwd):
        return None  # A1 holds even under a roster failure: the sanctioned repair path stays open
    words, reason = _loose_words(command)
    if reason is not None:
        return ("this Bash command cannot be read to the end (%s): the shell could run it "
                "differently than this hook read it, and a partially read command is never judged; "
                "failing closed (R6)" % (reason,))
    scan = command + chr(10) + chr(10).join(words)
    roots, reason = _bound_roots(command, cwd, words)
    if reason is not None:
        return reason + "; failing closed (R6)"
    frozen, views, reason = _rosters(roots)
    if reason is not None:
        return reason + "; failing closed (R6)"
    kind = _reference_kind(scan, cwd, ((frozen[0], frozen[1]), (views[0], views[1])))
    if kind is not None:
        return ("this Bash command references %s and is not a single plain invocation of the "
                "sanctioned writer (opf record or opf render): a lexical hook cannot prove any "
                "other referencing command read-only, so it is denied fail-closed (R5). %s; read "
                "protected files through the platform Read tool." % (kind, SANCTIONED))
    # The word-resolution pass (R5/R8): every dequoted word is judged as a resolved path the way a
    # file-tool target is. The R8 launcher exemption keeps the pack's own tools runnable: a plain
    # pristine python3 invocation exempts its SCRIPT operand from the guard alone (A1 already
    # governs the writer verbs), never from the store, frozen or view checks.
    exempt = frozenset()
    if tokens and not _ASSIGNMENT_RE.match(tokens[0]) and os.sep not in tokens[0] \
            and _PYTHON_RE.match(tokens[0]):
        rest = tokens[1:]
        while rest and _PYFLAGS_RE.match(rest[0]):
            rest = rest[1:]
        if rest:
            exempt = frozenset(_candidates(rest[0], cwd) or ())
    resolve_all = len(words) <= MAX_RESOLVED_WORDS
    seen = set()
    for word in words:
        if not resolve_all and not os.path.isabs(os.path.expanduser(word)):
            continue
        for cand in _candidates(word, cwd) or ():
            if cand in seen:
                continue
            seen.add(cand)
            if _store_rule(cand) is not None:
                return ("a word of this Bash command resolves into the %s store tree and the "
                        "command is not a single plain invocation of the sanctioned writer, so it "
                        "is denied fail-closed (R5). %s." % (WORKING, SANCTIONED))
            if cand in frozen[0]:
                return ("a word of this Bash command resolves to the plan-frozen old file %r: it "
                        "stays frozen, byte-identical, until its retirement is recorded (spec "
                        "14.1)." % (cand,))
            if cand in views[0]:
                return ("a word of this Bash command resolves to the declared view %r: views "
                        "change only through opf render (spec 5.8, 14.1)." % (cand,))
            if cand not in exempt:
                reason = _guard_rule(cand)
                if reason is not None:
                    return reason
            reason = _registration_rule(cand, roots)
            if reason is not None:
                return reason
    return None


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
    realpathed, control characters included: a path may legally carry them), to the store, a frozen
    path, a declared view or the pack's own files (R8); and denied cannot-evaluate when the
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
        # A resolvable spelling is JUDGED, never skipped: a path may legally carry control
        # characters (a newline-bearing symlink alias reaches the store like any other path). A
        # string longer than a platform path (PATH_MAX) cannot name a reachable file and stays in
        # the textual scan alone, as does a NUL-bearing spelling no OS call accepts.
        if 0 < len(s) <= MAX_PATH_CHARS:
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
            reason = _guard_rule(cand)
            if reason is not None:
                return reason
            reason = _registration_rule(cand, roots)
            if reason is not None:
                return reason
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
