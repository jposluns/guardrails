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
     collapses), so neither an existing symlink nor a symlink-then-dotdot spelling evades R1 at
     the time of the check (a re-link between the check and the write is a disclosed residual).
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
  R5 Bash writes. EVERY Bash command is first classified PROVABLY PLAIN or not by ONE strict
     classifier (D-DISCARD-SOUND-RULE; the shared plain-command specification, QA round 7), decided
     on the RAW command string before any lexing: (1) every character is printable ASCII (no tab,
     newline, carriage return, NUL or non-ASCII character, so no Unicode digit, homoglyph or
     invisible character); (2) no dollar sign, backquote, backslash, semicolon, ampersand, pipe,
     angle bracket, parenthesis, brace, square bracket, star, question mark, exclamation mark, hash
     or tilde appears outside a single-quoted span, and no assignment precedes the command word;
     (3) a single-quoted span is literal and a double-quoted span carries none of those characters,
     every quote terminated; (4) words are separated by spaces only, and the command word is a bare
     unquoted name or path that is not a shell, an interpreter, eval, exec, source, ".", env,
     command, builtin, xargs, nohup, timeout, sudo or any other wrapper or program on the explicit
     deny list (PLAIN_DENIED_COMMANDS). So no redirection, here-document, sequencing, substitution,
     expansion, glob, escape, line continuation or leading wrapper can sit in a plain command, and
     the hook's own lexer never has to read one. A plain command that still names a command to run
     on its own command line (an argument word that is a shell or interpreter name, or that carries
     a shell or interpreter command string or a leading exclamation mark, a git configuration
     override, a code-running git subcommand or a command-naming git option: _plain_runs_code) is
     judged as not plain. A PROVABLY PLAIN command
     takes the EXACT path check: the raw string and every dequoted word are scanned for the
     protected tokens (the .working store tree by any substring spelling, a session cwd inside a
     .working tree, and the frozen (R3) and declared-view (R4) paths matched with path boundaries),
     the judged spellings are every dequoted word, every spelling an argument word carries inside
     itself (round 8: a value glued to a short-option run, sort -oTODO.md or -o/abs/TODO.md, and the
     text after each delimiter inside a word, of=alias or --target-directory=/abs/x) and every value
     of an inherited git path variable (GIT_DIR, GIT_WORK_TREE and the others in GIT_PATH_ENV);
     every spelling is resolved against the session cwd AND against every directory another
     spelling names (git -C dir, --output-dir dir, an inherited GIT_WORK_TREE: a redirection of
     authority), to a fixed point; the rosters bind from the product roots above the session cwd,
     every absolute spelling and every resolved target; and every resolved target is judged exactly
     as a file-tool target would be (store, frozen, view, the pack own files (R8) and the
     registration (R8)). Round 9: every directory a spelling resolves to also receives the
     basename of every argument spelling as one more resolved target (cp X docs, cp -t docs X,
     install X docs and mv X /abs/docs write docs/<basename of X>); a command led by a removing,
     moving or re-permissioning word (CONTAINER_VERBS) or git rm, mv, clean, checkout or restore
     denies when a resolved operand (the cwd too, for git) is a directory HOLDING a protected
     path (a bound store tree, a frozen file, a view, a registration or the pack tree), and so
     does any command whose directory-plus-basename target is such a directory (cp -r src/docs
     .); a tilde-headed spelling is judged in BOTH readings, literal under the cwd (bash keeps a
     quoted tilde literal) and expanded; and an absolute GIT_TRACE* value is one more inherited
     spelling. Round 10 (D-RESCOPES-A) stops parsing toward completeness: every
     directory-plus-basename join is container-checked even when another word already resolves
     to it (no dedupe across the two checks, so a cp backup suffix or a second operand spelling
     the joined directory no longer hides it); the container verbs and git container forms are
     recognized at ANY word of the command, so a wrapper word in front (setarch x86_64 rm -r docs)
     keeps the rule; and git is read only through a small allowlisted option grammar
     (_git_grammar: the global options GIT_FLAG_GLOBALS and GIT_VALUE_GLOBALS, and for rm, mv,
     clean, checkout and restore the options listed in GIT_CONTAINER_OPTIONS), so an unlisted
     global option (--attr-source, --shallow-file), an unlisted option of a container subcommand
     (--pathspec-from-file, -p, -e, an abbreviated long option) and a pathspec magic word (any
     word after the subcommand opening with a colon: :(top), :/docs) make the command NOT plain,
     and it takes the coarse rule below (it denies when the session cwd or a literal word lies in
     a product root, and is allowed in a session bound to none). Past the derived-spelling, base
     or resolved-target budget the command
     DENIES cannot-evaluate. A command referencing a protected token is denied unless the WHOLE
     command is a single plain invocation of the sanctioned writer (allowance A1 below), the only
     allowance. A git command under an inherited code-valued git variable (GIT_CODE_ENV, other than
     an empty or known no-op or pager value) is judged as not plain. Any OTHER Bash command (one that
     is not provably plain) is judged COARSELY: it DENIES when the session working directory lies
     inside an OPF product root (a directory holding a .working entry), when the command text or an
     inherited git path value spells the .working store token ANYWHERE (inside a word, an option
     value or a quoted command string included), or when any literal word, any spelling derived
     inside a word or any inherited git path value, resolved against the cwd and every named
     directory as above, lies inside a product root or lands on the pack own tree (R8); otherwise it
     is allowed. A protected path such a command reaches with none of these (a variable, a
     substitution, an escape, or an interpreter own language spelling the path with no literal
     text) is the disclosed lexical-floor residual. Reference, not proven mutation, is the trigger:
     a lexical hook cannot prove a referencing command read-only, so it fails closed and names the
     allowed route.
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
     as an absent (empty) roster. Root discovery keeps the probe's errors (round 9): a directory
     on the way that refuses the search while the session's own user owns it (so the same command
     could unlock it first) is CANNOT-EVALUATE, never a directory holding no store; another
     user's unsearchable directory is absence, since nothing below it is reachable to this user.
     Roster files are opened without blocking (O_NONBLOCK where the
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
     scan), because the hook cannot prove such a tool read-only; a payload carrying no tool_input
     OBJECT at all, and a payload with no absolute session cwd (its relative strings cannot be
     resolved and no roster can be bound), are likewise denied cannot-evaluate, never read as
     naming nothing. A payload string longer than a
     platform path (PATH_MAX) cannot name a reachable file and is judged textually only. The known read-only
     built-ins (Read, Glob, Grep and the other names in READONLY_TOOLS) are allowed outright; tools
     that only launch further hooked tool calls (Task, Agent) are treated as read-only here because
     the launched calls are judged on their own. Skill and SlashCommand are NOT read-only-listed
     (their expansion may run shell lines this hook does not see), so each takes R7 (claude n2).
  R8 enforcement self-protection. A file-tool target, a resolved Bash word or a resolved payload
     string that lands inside the enforcement pack's own tree (this hook's opf/enforcement/
     directory or the writer's opf/tools/ directory, both resolved from the hook's own installed
     location) or on a bound product root's `.claude/settings.json` or `.claude/settings.local.json`
     (the hook registration) is denied: the gated tools must not be able to rewrite the gate, the
     writer or the registration in one call. A `python3 <script in the pack tree>` launch whose
     words pass rules 1 to 3 of the plain classifier stays allowed from outside every product root
     (the coarse rule exempts its launched script operand from R8: the pack's own tools must remain
     runnable; A1 alone governs the writer verbs). What R8 CANNOT protect is disclosed under
     RESIDUALS.

THE SINGLE PRISTINE ALLOWANCE for a Bash command that references a protected token (the allowance
machinery is itself attack surface, so the read-only command words and read-only git forms earlier
revisions allowed are REMOVED rather than patched; the over-refusal is disclosed below). The command
must pass rules 1 to 3 of the same provably plain classifier, with a bare command word (rule 4's word
split, without its deny list, so the python3 launcher form below can be read): printable ASCII only,
no metacharacter outside a single-quoted span, double-quoted spans free of them, every quote
terminated. So no second command, redirection, substitution or expansion can ride along, while a
sanctioned invocation may still QUOTE prose or a path that names a protected token (an `opf record`
title, an `opf render --root` operand with spaces or parentheses, single-quoted). A leading
VAR=value assignment is never a bare command word: an environment assignment changes what a program
does (GIT_EXTERNAL_DIFF and GIT_CONFIG_* make `git diff` execute an arbitrary writer), so an
assignment-bearing command is never the allowance.
  A1 the sanctioned writer, as a whole single plain invocation: `opf record ...` or `opf render ...`
     (the installed entry point as a bare word), or a bare python3 word (allowlisted interpreter flags
     only) running THE repository's own opf/tools/opf.py with verb record or render. The launched
     script is identified by realpath EQUALITY against the writer this hook ships beside (resolved
     from the hook's own installed location), never by a filename: a same-named opf.py anywhere else
     is not the writer. The script word resolves LITERALLY against the session cwd, as bash runs it
     (round 9): a tilde can reach it only inside single quotes, which bash keeps literal, so a
     tilde-headed script word is never the writer. opf's own write guard, lease and journal govern
     what the writer may do. This
     allowance also holds under an R6 roster failure, so the in-session repair path stays open.

RESIDUALS (spec 14.1 requires each disclosed; the pack's residual register (slice (d)) and the plan's
per-platform residual coverage carry the same list):
  - A Bash write the matcher cannot see: a protected path reaching the filesystem through a shell
    variable, glob, alias, function, cd-relative spelling that drops the token, command or process
    substitution, an interpreter one-liner, or any other spelling in which no protected token appears
    textually in the command string. R5 is a lexical floor, not a sandbox.
  - A plain command whose program runs code the command line does not name: a script or binary the
    session prepared earlier (./tool.py), a build, package or test runner not on the deny list
    reading its own recipe file, a git hook, alias, pager, filter or diff driver taken from
    repository or user configuration, a program configured through an environment variable the
    session inherited, an exported shell function shadowing the command word, and a program outside
    PLAIN_DENIED_COMMANDS that runs a command named in its own options. The deny list and the plain
    semantic check exclude the inline forms only; the configuration and the prepared file are
    same-user preparation.
  - A plain command whose operand CONTAINS a protected path rather than lying on it, outside the
    round-9 container rule: the exact check judges words that resolve INTO a protected path, the
    directory-plus-basename joins and, for CONTAINER_VERBS and git rm, mv, clean, checkout and
    restore (at any word of the command, round 10), a directory operand holding a protected path
    of a BOUND root. So a program outside those words acting on a directory's whole contents
    under a name no operand carries (an archive extraction or a sync into a parent directory,
    cp -r src/. docs, cp -rT src/docs docs), a git work-tree rewrite named by no path (git reset
    --hard, git stash, git switch, git merge or pull, and git checkout of a branch run from a
    directory holding no protected path), a git alias from configuration standing for a
    container subcommand (git co, same-user preparation), and a recursive remove of an ancestor
    of a product root that the session neither sits in nor binds (no product root above the cwd
    or any operand) reach a protected file with no protected token.
  - A relative protected spelling past the word budget: a provably plain command binds the rosters
    above every RESOLVED target (round 8), so a relative spelling that climbs into a product from
    outside it denies; past MAX_RESOLVED_WORDS words only absolute spellings resolve (the budget
    entry below), so such a relative spelling in a longer command binds nothing.
  - A protected file reached ONLY by its real path with the session outside its product: each
    roster entry carries its realpath spelling (a symlinked directory inside OR outside the
    product root included), so such a write denies whenever a roster is BOUND (a product root at
    or above the written target or the session cwd, each judged lexically and realpathed); but
    when the entry's real path lies outside every product root AND the session cwd binds no root,
    no roster is discovered and the real-path write passes. The writer refuses symlinked view
    destinations, the planner refuses symlinked sources, and the writer refuses a symlinked
    `.working` (which this hook likewise refuses to bind as a store, failing closed), so a valid
    store never carries such a layout; reaching it takes a prior re-layout outside these tools
    (same-user preparation).
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
  - The check is point-in-time: the hook resolves every path (realpath, roster discovery, the
    machine-store test) in its own process BEFORE the tool runs, so a same-user change made
    between the check and the tool's own open (a background job re-pointing a symlink, renaming a
    directory or rewriting a roster) is not seen (a check-to-use race; same-user preparation, as
    spec 14.1 accepts).
  - A protected path an exotic (not provably plain) command reaches with neither the session cwd
    nor any literal text inside the product root: a path spelled only through a shell variable, an
    alias or function, a command or process substitution, an escape the coarse scan does not decode
    (ANSI-C or locale quoting), or an interpreter own language, where no literal word, no spelling
    inside a word and no inherited git path value resolves into the product and the .working token
    is not spelled, is not seen. R5 is a lexical floor, not a sandbox.
  - An inherited environment the hook cannot see or does not model: the hook judges the git
    variables in ITS OWN environment (GIT_PATH_ENV values as spellings, GIT_CODE_ENV as code), so a
    variable the Bash tool sets that the hook process does not inherit, a non-git program's own
    configuration variable (PAGER, EDITOR, a build tool's), and git's user or system configuration
    files themselves (an alias or hook path inside them) stay the same-user-preparation residual
    named above. An absolute GIT_TRACE* value is judged as a spelling (round 9); a trace variable
    naming a descriptor or socket writes no named file.
  - An R7 payload string is judged WHOLE: the option-glued and delimiter-embedded derivation of R5
    applies to Bash words only, so an unknown tool string such as -o/abs/TODO.md binds no roster by
    its glued spelling (its textual token scan still applies once a roster is bound).
  - Platform hook-startup failures may fall through to the platform's normal permission flow.
  - Shell or interpreter wrapping of the platform itself is outside the hook's reach.
  - Over-approximation is the accepted cost of the fail-closed posture. A provably plain command
    that references any protected token outside A1 denies, read-only forms included (cat, grep or
    git diff of a store path; git add of a declared view TODO.md; git log of a plan-frozen
    LEGACY.md). A command that is NOT provably plain denies whenever the session working directory,
    or any literal path word, lies inside a product root, even when it touches nothing protected: so
    from a cwd inside a product root a parameter expansion (echo $HOME), a command substitution
    (gh pr create --body "$(...)"), an interpreter, wrapper or build tool (python3 script.py, make
    test, bash -c ..., env, sed, find, tar), an eval, a line continuation, an ANSI-C or locale
    quote, a glob, a redirection, any here-document (a quoted commit-message here-document
    included), a tab or non-ASCII character, a double-quoted dollar sign (opf record task "costs
    $5"; single-quote it), a git configuration override or config subcommand, an argument word that
    names a shell or interpreter (grep -rn python src; use the Grep tool), and an argument word
    carrying a shell or interpreter command string (a commit message beginning "sh -c" or "!")
    all deny; run them from outside the product tree or outside a hooked session, read
    protected files through the Read tool, and change the store through the opf CLI. R8 denies
    rewriting the pack own files and the per-product registration through the gated tools, and the
    word-resolution pass of a plain command denies a command that merely names a protected or
    pack-owned file as a resolvable argument. Round 8 widens both passes: a word whose glued option
    suffix or post-delimiter suffix happens to name a protected path (a prose word such as
    -xTODO.md, or key=LEGACY.md), a relative operand that names a protected path under ANY directory
    the same command names (ls docs STATUS.md where docs/STATUS.md is a view), any not-plain command
    that spells .working anywhere (grep .working from outside every product), a git command under a
    non-trivial inherited GIT_PAGER, GIT_EXTERNAL_DIFF or similar, and a plain command naming more
    than MAX_BASES directories, all deny. Round 9 widens them again: a remove, move or
    re-permission of ANY directory holding a protected path (mv notes.md docs where docs holds a
    view, chmod -R u+w . from a product root), git restore --staged or git rm --cached over such a
    directory, a directory operand beside a word whose basename names a protected file inside it,
    a quoted tilde spelling whose literal OR expanded reading is protected, and every path below a
    directory the session's own user made unsearchable, all deny. Round 10 widens them again, by
    name, from a product root or any directory holding a protected path (the git container forms
    judge the session cwd): everyday git checkout main, git checkout -b feature, git rm notes.txt,
    git mv notes.txt n2.txt, git restore notes.txt, git restore --staged notes.txt and git clean -n
    all deny; and from ANY directory inside a product root, git with a global option outside the
    allowlisted grammar (git -p log, git --bare status, git --exec-path, git --attr-source HEAD
    log, git --namespace x log), git rm, mv, clean, checkout or restore with an option outside
    their listed sets (git checkout -p, git restore -p, the abbreviated git restore --sour HEAD
    x, git clean -e pattern, git clean -i, git rm --pathspec-from-file=list, git checkout
    --orphan x), and any git command carrying a pathspec magic or colon-headed word (git
    add :/docs, git show :docs/x for an index blob, git log -- ':(exclude)x') all deny; so does a
    plain command naming a directory that holds a protected path beside a directory word that
    the first name could be joined to (ls docs ., where docs holds a view), and a plain command
    whose argument word names a removing, moving or re-permissioning program or git container
    form (grep -rn mv docs, or a wrapped git) over such a directory, the names matched like the
    deny list, version or variant suffix included (rm-old.txt counts as rm), and from inside a
    product root a plain command whose argument word names git (git-notes.md included) followed
    by an option outside the grammar (ls git -la). R6 denies every write under a
    root whose roster
    carries any unreadable or malformed entry, R3 keeps denying a frozen path even after its
    retirement is recorded, and a protected token inside prose (a commit message) still trips a
    plain command.
  - The word-resolution budget of a provably plain command (claude n1): past MAX_RESOLVED_WORDS
    dequoted words only the ABSOLUTE spellings are resolved as paths (absolute words and derived
    absolute spellings always resolve; the raw, word and derived-spelling token scan still covers
    every spelling), so a relative spelling that reaches a
    declared view or a frozen file only after normalization or symlink resolution, in a plain
    command past the budget, is not resolved (the store tree still denies by its .working
    component); a shorter command resolves it. This budget cliff is disclosed here.
  - The IMPORTED record series is NOT yet protected here: the leaves `worklog.imported.toml` and
    `<type>.imported.index.toml` DIRECTLY inside the machine store directory (exactly
    `.working/<machine>/<leaf>`, no other depth, where `<machine>` is a plain directory whose
    manifest.toml declares [opf] standard = "opf"; the same leaf in any other directory, an absent
    one included, denies) are exempt from R1 by name, because
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

import errno
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
# The embedded-spelling budgets (R5, round 8): an argument word can carry a path GLUED to an option
# (sort -oTODO.md, tar -C/abs/root) or INSIDE the word after a delimiter (of=/abs/x, -Wl,-Map,x,
# --target-directory=/abs/x, a command string carrying a redirection target), and a directory a word
# names can become the base another relative word is resolved against (git -C dir, curl --output-dir
# dir). Each derived spelling and each directory base is RESOLVED exactly; past either budget, or past
# MAX_JUDGED_TARGETS resolved candidates, the command DENIES cannot-evaluate (never a partial judgment).
MAX_DERIVED_SPELLINGS = 4096
MAX_BASES = 64
MAX_JUDGED_TARGETS = 16384

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
# The store-tree control subdirs (spec 4.2; spec 4.4 reserves the names imports, imported, archive,
# staging and journals at the store level): a first component after .working/ outside this set is a
# machine-store candidate, where the imported-series leaf exemption below may apply.
CONTROL_SUBDIRS = frozenset(("imports", "imported", "archive", "staging", "journals"))
# The imported-series leaves (spec 8.3) stay EXEMPT until the import writer ships (module docstring).
IMPORTED_LEAF_RE = re.compile(r"\A(worklog\.imported\.toml|[A-Za-z0-9_-]+\.imported\.index\.toml)\Z")

# The write-capable file tools and the payload field naming each one's target.
FILE_TOOL_TARGET = dict(Write="file_path", Edit="file_path", MultiEdit="file_path",
                        NotebookEdit="notebook_path")
# The known read-only built-ins (R7): allowed outright. Task and Agent only launch further tool calls,
# each judged by this hook on its own, so they sit here too. TodoWrite is NOT listed: it is
# write-capable on some platforms and this hook cannot prove its input names no file, so it takes
# R7's scan like every other unlisted tool. Every OTHER tool name takes R7's scan.
READONLY_TOOLS = frozenset(("Read", "Glob", "Grep", "LS", "NotebookRead", "WebFetch", "WebSearch",
                            "Task", "Agent", "ExitPlanMode", "AskUserQuestion",
                            "BashOutput", "TaskOutput", "KillShell", "KillBash"))

# The loose-lexer word separators (R5): ONLY the shell's own unquoted operator characters end a
# word (semicolon, ampersand, pipe, the two angle brackets, the two parentheses, backquote; space,
# tab and newline are handled in the lexer itself), so a quoted operand beside a redirection stays
# whole. Braces, carriage returns and the other control characters are NOT separators: the shell
# keeps them inside a word (a pathname may carry them literally), so this lexer keeps them too,
# and a word whose unquoted braces the shell would EXPAND is refused instead (_loose_words).
WORD_SEPARATORS = frozenset(chr(c) for c in (59, 38, 124, 60, 62, 40, 41, 96))
# The backslash-escapable set INSIDE double quotes (dollar, backquote, double quote, backslash,
# newline); before any other character a double-quoted backslash stays literal, as in the shell.
DQ_ESCAPABLE = frozenset(chr(c) for c in (36, 96, 34, 92, 10))
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
# dequoted-word and payload-string passes). A match glued to a preceding letter or digit (and/or, a
# URL path, but ALSO an option letter: sort -o/abs/x) neither binds nor counts against the discovery
# budget HERE; a Bash word's option-glued and delimiter-embedded absolute spellings are bound and
# resolved instead through _derived_spellings (R5), while an R7 payload string is judged whole (the
# disclosed R7 residual). More matches than MAX_ABS_PATHS denies, never truncates.
ABS_PATH_RE = re.compile(r"(?<![A-Za-z0-9])/[A-Za-z0-9_./@%+,=~^-]+")
_ASSIGNMENT_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*=")
# A short-option run at the head of a word (-oTODO.md, -xvC/abs, +o): every suffix after one or more of
# its letters may be the option's glued value, so each is a derived spelling (_derived_spellings).
_OPTION_GLUE_RE = re.compile(r"\A[-+]+([A-Za-z0-9]*)")
_PYTHON_RE = re.compile(r"\Apython(3(\.\d+)?)?\Z")
_PYFLAGS_RE = re.compile(r"\A-[IBEsuPb]+\Z")
# THE PROVABLY PLAIN CLASSIFIER (R5): the shared plain-command specification, decided on the raw
# command string before any lexing. Rule 1: every character is printable ASCII (0x20 to 0x7E; no tab,
# newline, carriage return, NUL or non-ASCII character, so no Unicode digit, homoglyph or invisible
# character). Rule 2: PLAIN_FORBIDDEN never appears outside a single-quoted span: dollar sign,
# backquote, backslash, semicolon, ampersand, pipe, the two angle brackets, the two parentheses, the
# two braces, the two square brackets, star, question mark, exclamation mark, hash and tilde, each
# built from its code point so none appears literally here. Rule 3: a single-quoted span is literal; a
# double-quoted span may carry no PLAIN_FORBIDDEN character; an unterminated quote is not plain.
# Rule 4: words are separated by spaces only; the command word is bare (PLAIN_COMMAND_RE, so no
# quote and no leading assignment) and is not on PLAIN_DENIED_COMMANDS.
PLAIN_FORBIDDEN = frozenset(chr(c) for c in (36, 96, 92, 59, 38, 124, 60, 62, 40, 41, 123, 125,
                                             91, 93, 42, 63, 33, 35, 126))
PLAIN_COMMAND_RE = re.compile(r"\A[A-Za-z0-9_./-]+\Z")
# Rule 4's explicit deny list (acceptable because rules 1 and 2 already exclude every expansion
# form): a command word naming a shell, an interpreter, a shell builtin or keyword that runs, loads
# or defers code, a wrapper that runs another command, or a program that runs a command or code
# named in its own arguments or options. Matched case-insensitively on the word's basename (a
# case-insensitive filesystem launches PYTHON3 as python3), exactly or with a version or variant
# suffix that starts with a digit, dot, underscore or dash (python3.12, perl5.36, node-18).
PLAIN_DENIED_COMMANDS = frozenset((
    "sh", "bash", "rbash", "dash", "ash", "zsh", "ksh", "mksh", "pdksh", "oksh", "yash", "posh",
    "csh", "tcsh", "fish", "nu", "elvish", "xonsh", "busybox", "toybox",
    ".", "eval", "exec", "source", "command", "builtin", "enable", "trap", "alias", "fc", "bind",
    "coproc", "time", "r",
    "env", "xargs", "nohup", "timeout", "sudo", "doas", "su", "sg", "newgrp", "runuser", "pkexec",
    "chroot", "nice", "ionice", "chrt", "taskset", "numactl", "prlimit", "setpriv", "capsh",
    "cgexec", "chpst", "setsid", "stdbuf", "unbuffer", "script", "flock", "watch", "parallel",
    "nsenter", "unshare", "firejail", "bwrap", "proot", "faketime", "strace", "ltrace", "gdb",
    "lldb", "valgrind", "catchsegv", "hyperfine", "entr", "nodemon", "systemd-run", "caffeinate",
    "daemonize", "start-stop-daemon", "dbus-launch", "dbus-run-session", "xvfb-run", "ssh-agent",
    "at", "batch", "crontab", "screen", "tmux", "docker", "podman",
    "find", "make", "gmake", "bmake", "ninja", "sed", "gsed", "awk", "gawk", "mawk", "nawk", "tar",
    "gtar", "bsdtar", "rsync", "zip", "ssh", "scp", "sftp", "vi", "vim", "nvim", "view", "ex", "ed",
    "emacs", "less", "more", "man",
    "python", "pythonw", "pypy", "perl", "ruby", "irb", "node", "nodejs", "deno", "bun", "php",
    "lua", "luajit", "tclsh", "wish", "expect", "rscript", "osascript", "pwsh", "powershell",
    "java", "jshell", "groovy", "julia", "guile", "racket", "sbcl", "ocaml", "swift", "npm", "npx",
    "yarn", "pnpm", "uv", "uvx", "pipx", "dotnet", "cargo", "go"))
_DENIED_STEM_RE = re.compile(r"\A([a-z]+)[0-9._-]")
# An argument word's runner spelling: a name with at most a version suffix (python3, python3.12),
# never a longer identifier (PYTHON_VERSION, sh-notes).
_RUNNER_WORD_RE = re.compile(r"\A([a-z]+)[0-9.]*\Z")
# The subset of the deny list that, at the head of a command string carried INSIDE a plain
# argument word (git -c alias.x=!cmd, --to-command=cmd), marks inline code: a plain command carrying
# such a word takes the coarse rule, never the exact one (the plain semantic check).
INLINE_RUNNERS = frozenset(("sh", "bash", "rbash", "dash", "ash", "zsh", "ksh", "mksh", "pdksh",
                            "oksh", "yash", "posh", "csh", "tcsh", "fish", "busybox", "toybox",
                            "eval", "exec", "source", "env", "xargs", "sudo", "su", "nohup",
                            "timeout", "command", "builtin", "python", "pythonw", "pypy", "perl",
                            "ruby", "node", "nodejs", "deno", "bun", "php", "lua", "luajit",
                            "tclsh", "expect", "osascript", "pwsh", "powershell", "awk", "gawk",
                            "mawk", "nawk"))
# The git forms that run a command or code named on the command line (the plain semantic check): a
# global configuration override before the subcommand (-c, --config-env: alias.x=!cmd, core.pager,
# core.sshCommand, core.hooksPath), --exec-path, a subcommand that writes code-running configuration
# or runs a command string, and the options that name a command to run (rebase -x and clone -u are
# matched on their own subcommands).
GIT_VALUE_GLOBALS = frozenset(("-C", "--git-dir", "--work-tree"))
# The SMALL allowlisted git option grammar (round 10, D-RESCOPES-A): the subcommand is recognized only
# past these global options (a value global as a separate word, or --git-dir=/--work-tree= glued);
# any other global option (git 2.53 accepts --attr-source <tree-ish> and --shallow-file <file> as
# separate words, and a value word would otherwise be read as the subcommand) makes the command NOT
# plain. A container subcommand (GIT_CONTAINER_OPTIONS) accepts only its listed short letters, valued
# short letters and exact long options before a lone --; and any word after the subcommand that opens
# with a colon (pathspec magic, :(top) or :/docs, which reaches the repository top) is not plain.
GIT_FLAG_GLOBALS = frozenset(("--no-pager", "-P", "--no-optional-locks", "--literal-pathspecs",
                              "--no-replace-objects", "--version"))
GIT_CONTAINER_OPTIONS = dict(
    rm=("rfnq", "", ("--force", "--dry-run", "--quiet", "--cached", "--ignore-unmatch"), ()),
    mv=("fnkv", "", ("--force", "--dry-run", "--verbose"), ()),
    clean=("dfnqxX", "", ("--force", "--dry-run", "--quiet"), ()),
    checkout=("fqtm", "bB", ("--force", "--quiet", "--detach", "--track", "--no-track", "--merge",
                             "--ours", "--theirs"), ()),
    restore=("SWqm", "s", ("--staged", "--worktree", "--quiet", "--merge", "--ours", "--theirs"),
             ("--source",)))
GIT_CODE_SUBCOMMANDS = frozenset(("config", "filter-branch", "bisect", "submodule", "difftool",
                                  "mergetool", "daemon", "instaweb", "send-email", "credential",
                                  "svn", "p4", "cvsimport", "archimport", "help", "web--browse"))
GIT_CODE_OPTIONS = ("--exec", "--upload-pack", "--receive-pack", "--extcmd", "--tool",
                    "--open-files-in-pager", "-O")
# The AMBIENT git environment (R5, round 8): the hook reads the environment the session launched it
# with, which the Bash tool's git inherits. A path-valued variable redirects where git reads and writes
# (the repository, the work tree, the index, the object store, a configuration file), so each value is
# judged as one more spelling of every Bash command (token-scanned, resolved, bound and used as a base);
# a code-valued variable names a command git runs, so a git command under one takes the coarse rule
# unless its value is empty or a single known no-op or pager word (GIT_EDITOR=true, GIT_PAGER=cat).
GIT_PATH_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG",
                "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM")
GIT_CODE_ENV = ("GIT_EXTERNAL_DIFF", "GIT_PAGER", "GIT_EDITOR", "GIT_SEQUENCE_EDITOR", "GIT_SSH",
                "GIT_SSH_COMMAND", "GIT_ASKPASS", "GIT_EXEC_PATH", "GIT_PROXY_COMMAND",
                "GIT_TEMPLATE_DIR", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT")
GIT_ENV_NOOPS = frozenset(("", "true", "cat", "less", "more", ":", "0"))
# The git trace variables (round 9): an ABSOLUTE value is a file git appends its trace to, creating
# it, so each such value is judged as one more spelling like a GIT_PATH_ENV value; any other value
# (1, true, a descriptor number, a socket address) writes no named file.
GIT_TRACE_PREFIX = "GIT_TRACE"
# The lstat errors that mean a `.working` probe found nothing (round 9): no such entry, a
# non-directory or over-long component, a symlink loop on the way. A permission error is NOT
# absence (_store_entry), and every other error is cannot-evaluate.
ABSENT_ERRNOS = frozenset((errno.ENOENT, errno.ENOTDIR, errno.ENAMETOOLONG, errno.ELOOP))
# The removing, moving and permission-changing command words (round 9): such a program applied to a
# DIRECTORY operand acts on everything inside it, so a plain command led by one of these (or git rm,
# mv, clean, checkout or restore) denies when a resolved operand is a directory that CONTAINS a
# protected path: the store tree of a bound root, a frozen file, a declared view, the registration or
# the pack own tree (_container_rule). Matched like the deny list, on the lowercased basename.
CONTAINER_VERBS = frozenset(("rm", "rmdir", "unlink", "mv", "shred", "srm", "wipe", "trash",
                             "trash-put", "chmod", "chown", "chgrp", "setfacl", "chattr"))
GIT_CONTAINER_SUBCOMMANDS = frozenset(GIT_CONTAINER_OPTIONS)

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


def _candidates(target, cwd, tilde="both"):
    """The absolute forms of `target` to judge: the lexically normalized path and the realpath of the
    ORIGINAL spelling. Resolving the original spelling first is load-bearing: in a spelling such as
    `link/../x`, the filesystem resolves the symlink BEFORE `..` climbs out of its destination, so a
    lexical collapse first (normpath dropping `link/..`) would judge a different file than the one the
    write reaches. normpath is applied only to the already-resolved result and to the lexical twin.
    A leading tilde has two readings and BOTH are judged (round 9, fail closed): expanded against HOME
    (a tool or program that expands it) and LITERAL, a `~` directory under the cwd (bash keeps a
    quoted tilde literal, and the plain classifier lets a tilde through only inside single quotes).
    `tilde` "expand" or "literal" judges one reading alone; with neither reading resolvable (a
    relative spelling and no absolute cwd) the result is None."""
    if tilde == "both" and target.startswith("~"):
        both = set(_candidates(target, cwd, "expand") or ())
        both.update(_candidates(target, cwd, "literal") or ())
        return sorted(both) or None
    if tilde != "literal":
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


def _is_machine_store(working, name):
    """True only when `working`/`name` is a machine store: a plain directory (never a symlink)
    whose manifest.toml is a readable regular file declaring [opf] standard = "opf" (spec 4.5).
    Anything else, an absent or unreadable manifest included, is not the machine store."""
    sub = os.path.join(working, name)
    if not os.path.isdir(sub) or os.path.islink(sub):
        return False
    doc, reason = _read_roster_toml(os.path.join(sub, "manifest.toml"), "the machine-store manifest")
    if reason is not None or not isinstance(doc, dict):
        return False
    base = doc.get(MANIFEST_STANDARD)
    return isinstance(base, dict) and base.get("standard") == MANIFEST_STANDARD


def _store_rule(candidate):
    """R1/R2 over one absolute candidate path: a deny reason, or None. The imported-series leaf
    exemption (module docstring) applies only directly inside THE machine store (a store subdir
    whose manifest declares the OPF standard), never inside another first-level directory."""
    comps = _components(candidate)
    if WORKING not in comps:
        return None
    index = comps.index(WORKING)
    after = comps[index + 1:]
    if len(after) >= 2 and (after[0], after[1]) == ADOPTION_ARCHIVE:
        return ("writes under %s/archive/adoption/<run-id>/ are denied: the adoption archive holds "
                "digest-bound preserved originals (spec 14.1). %s." % (WORKING, SANCTIONED))
    if (len(after) == 2 and after[0] not in CONTROL_SUBDIRS and IMPORTED_LEAF_RE.match(after[1])
            and _is_machine_store(os.sep + os.path.join(*comps[:index + 1]), after[0])):
        return None  # the imported-series machine-store leaves stay writer-less until the import writer ships
    if after and after[0] == "imported":
        return ("direct writes under %s/imported/ are denied: adoption and import evidence is written "
                "only by the opf writers and verified by the completion checks (spec 14.1, 14.2). "
                "%s." % (WORKING, SANCTIONED))
    return ("direct edits under %s/ are denied: OPF records, counters, ledgers, journals and staging "
            "change only through the sanctioned writer (spec 14.1). %s." % (WORKING, SANCTIONED))


def _search_blocker(entry):
    """For an entry whose lstat failed on a permission error: a reason when the directory refusing
    the search is one the session's own user can unlock (it owns it, or it is the superuser), or
    None when another user owns it (round 9). The blocker is the deepest ancestor of `entry` that
    can itself be examined; every examination error is a reason (fail closed)."""
    cur = os.path.dirname(entry)
    while True:
        try:
            st = os.stat(cur)
        except PermissionError:
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
            continue
        except OSError as exc:
            return "the directory %s on the path to %s cannot be examined (%r)" % (cur, entry, exc)
        euid = os.geteuid() if hasattr(os, "geteuid") else None
        if euid is not None and euid != 0 and st.st_uid != euid:
            return None
        return ("the directory %s is not searchable, but the session's own user can unlock it (it "
                "owns it), even within the same command, so whether a %s store tree lies below it "
                "cannot be read" % (cur, WORKING))
    return "no directory on the path to %s can be examined" % (entry,)


def _store_entry(entry):
    """One `.working` probe for _roots_above with the errors KEPT (round 9: os.path.isdir and
    os.path.lexists read a permission error as absence): "dir" for a plain directory, "absent" for a
    genuinely absent entry (no such file, a non-directory or over-long component, a symlink loop on
    the way) or one under another user's unsearchable directory, else a reason."""
    try:
        st = os.lstat(entry)
    except OSError as exc:
        if exc.errno in ABSENT_ERRNOS:
            return "absent"
        if isinstance(exc, PermissionError):
            reason = _search_blocker(entry)
            return "absent" if reason is None else reason
        return "the store entry %s cannot be examined (%r)" % (entry, exc)
    if stat.S_ISDIR(st.st_mode):
        return "dir"
    return ("the store entry %s exists but is not a plain directory (a symlinked, dangling or "
            "non-directory %s tree is a layout the sanctioned writer refuses, and this hook will not "
            "bind a store it cannot read as the writer would)" % (entry, WORKING))


def _roots_above(path):
    """Every product root at or above `path` (a directory holding a `.working` entry, nearest
    first): (roots, None), or (None, reason) when a `.working` entry EXISTS somewhere above but is
    not a plain directory (a SYMLINK, even to a directory: the writer refuses a symlinked
    `.working` with O_NOFOLLOW, so this hook refuses to bind one as a store; a dangling link; or a
    non-directory), or when a directory on the way refuses the search and the session's own user
    could unlock it (round 9: an unsearchable directory the same command can chmod first is never
    read as holding no store; another user's unsearchable directory is absence, since nothing
    below it is reachable to this user): a store tree that cannot be read as the writer would read
    it is cannot-evaluate, never an absent root (R6)."""
    roots = []
    cur = os.path.normpath(path)
    while True:
        if os.path.basename(cur) != WORKING:
            entry = os.path.join(cur, WORKING)
            state = _store_entry(entry)
            if state == "dir":
                roots.append(cur)
            elif state != "absent":
                return None, state
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
    so each roster entry contributes both spellings and a write through either one matches ONCE A
    ROSTER IS BOUND (a product root at or above the written target or the session cwd); the
    real-path-outside-every-root, cwd-outside-every-root case is a disclosed residual."""
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
    """R1-R4, R6 and R8 for the write-capable file tools; returns a deny reason or None (allow).
    Rosters bind from the product roots above each candidate target AND above the session cwd
    (each judged lexically and as the kernel resolves it), so a view or frozen file reached by its
    REAL path behind a symlinked directory, the symlink target outside the product root included,
    still denies while the session sits inside its product."""
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
    cwd_roots = []
    if isinstance(cwd, str) and os.path.isabs(cwd):
        spellings = [os.path.normpath(cwd)]
        try:
            resolved = os.path.normpath(os.path.realpath(cwd))
        except (OSError, ValueError):
            resolved = None
        if resolved is not None and resolved not in spellings:
            spellings.append(resolved)
        for spelling in spellings:
            got, reason = _roots_above(spelling)
            if reason is not None:
                return reason + "; failing closed (R6)"
            for root in got:
                if root not in cwd_roots:
                    cwd_roots.append(root)
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
        roots = list(roots)
        for root in cwd_roots:
            if root not in roots:
                roots.append(root)
        if not roots:
            continue
        reason = _registration_rule(cand, _registration_idents(roots))
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


def _command_names(word):
    """The names a command word is matched under (rule 4): its basename, lowercased, and the stem
    before a version or variant suffix (python3.12 -> python)."""
    base = os.path.basename(word).lower()
    stem = _DENIED_STEM_RE.match(base)
    return frozenset((base, stem.group(1))) if stem else frozenset((base,))


def _denied_command(word):
    """Rule 4's deny list over one command word (PLAIN_DENIED_COMMANDS): True when its basename names
    a shell, an interpreter, a code-running builtin or a wrapper, exactly or with a version or
    variant suffix, compared case-insensitively."""
    return bool(_command_names(word) & PLAIN_DENIED_COMMANDS)


def _plain_words(command):
    """Rules 1 to 3 of the provably plain specification and rule 4's word split, decided on the raw
    string before any lexing: the dequoted words when every character is printable ASCII, no
    PLAIN_FORBIDDEN character appears outside a single-quoted span (a double-quoted span carries none
    either), every quote is terminated, words are separated by spaces only and the command word is a
    bare unquoted PLAIN_COMMAND_RE name or path; else None. The deny list is NOT applied here: the
    sanctioned-writer allowance (A1) reads its python3 launcher form through these same words."""
    if any(not 0x20 <= ord(ch) <= 0x7E for ch in command):
        return None
    head = command.lstrip(" ").split(" ", 1)[0]
    if not PLAIN_COMMAND_RE.match(head):
        return None
    words, cur, has = [], [], False
    i, n = 0, len(command)
    while i < n:
        ch = command[i]
        if ch in (chr(39), chr(34)):
            end = command.find(ch, i + 1)
            if end < 0:
                return None
            span = command[i + 1:end]
            if ch == chr(34) and any(c in PLAIN_FORBIDDEN for c in span):
                return None
            cur.append(span)
            has, i = True, end + 1
            continue
        if ch in PLAIN_FORBIDDEN:
            return None
        if ch == " ":
            if has:
                words.append("".join(cur))
                cur, has = [], False
            i += 1
            continue
        cur.append(ch)
        has, i = True, i + 1
    if has:
        words.append("".join(cur))
    return words


def _provably_plain(command):
    """R5's PROVABLY PLAIN classifier (the shared specification, all four rules): the dequoted words
    of a provably plain command, or None when the command is not plain. Every command this returns
    None for is judged by _exotic_bash_rule, the coarse product-root check."""
    words = _plain_words(command)
    if not words or _denied_command(words[0]):
        return None
    return words


def _names_runner(word):
    """True when `word`, as a basename with at most a version suffix, is an INLINE_RUNNERS name."""
    spelled = _RUNNER_WORD_RE.match(os.path.basename(word).lower())
    return bool(spelled) and spelled.group(1) in INLINE_RUNNERS


def _plain_runs_code(words):
    """The plain semantic check (R5): a phrase naming why a provably plain command still runs a
    command or code it names on its own command line, or None. Such a command takes the coarse rule.
    An argument word that IS an INLINE_RUNNERS name (a wrapper outside the deny list, such as
    eatmydata python3 -c ..., runs it), and a word carrying a command string headed by such a name
    or by a leading exclamation mark (git alias.x=!cmd, --to-command=python3 -c ...), are inline
    code; so are git's configuration overrides, its code-running subcommands and its
    command-naming options, and (round 10) a git word anywhere on the line (git itself or git run
    by a wrapper) whose options fall outside the allowlisted git option grammar (_git_grammar)."""
    for word in words[1:]:
        if _names_runner(word):
            return "an argument word names a shell or interpreter another program may run"
        for piece in word.split("="):
            piece = piece.lstrip(" ")
            if piece.startswith(chr(33)) and piece.strip(chr(33) + " "):
                return "an argument word carries a shell-escape command string"
            head = piece.split(" ")
            if len(head) > 1 and _names_runner(head[0]):
                return "an argument word carries a shell or interpreter command string"
    for j, word in enumerate(words):
        if "git" in _command_names(word):
            reason = _git_runs_code(words[j:])
            if reason is not None:
                return reason
    return None


def _git_runs_code(words):
    """The git part of the plain semantic check over a git invocation `words` (words[0] names git,
    as the command word or behind a wrapper): a phrase, or None."""
    for name in GIT_CODE_ENV:
        value = os.environ.get(name)
        if value is not None and value.strip(" ") not in GIT_ENV_NOOPS:
            return "the inherited git environment variable %s names a command git may run" % (name,)
    if any(k.startswith("GIT_CONFIG_KEY_") for k in os.environ):
        return "an inherited GIT_CONFIG_KEY_* variable overrides git configuration"
    i = 1
    while i < len(words) and words[i].startswith("-"):
        opt = words[i]
        if (opt == "-c" or (opt.startswith("-c") and not opt.startswith("--"))
                or opt.startswith("--config-env") or opt.startswith("--exec-path")):
            return "a git configuration override or exec-path option can run a command"
        i += 2 if opt in GIT_VALUE_GLOBALS else 1
    i, reason = _git_grammar(words)
    if reason is not None:
        return reason
    if i is None:
        return None
    sub, rest = words[i], words[i + 1:]
    if sub in GIT_CODE_SUBCOMMANDS:
        return "the git subcommand %r runs or configures a command" % (sub,)
    for word in rest:
        if word.startswith(GIT_CODE_OPTIONS):
            return "the git option %r names a command to run" % (word,)
        short = word.startswith("-") and not word.startswith("--")
        if sub == "rebase" and short and "x" in word[1:]:
            return "git rebase -x runs a command"
        if sub == "clone" and short and "u" in word[1:]:
            return "git clone -u runs a command"
    return None


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


def _registration_idents(roots):
    """The protected registration identities over `roots` (R8): for each root and registration
    leaf, the lexical spelling AND the realpath of the registration entry (a registration that is
    itself a symlink is rewritable through its target, WHEREVER that target lies, so the real file
    is a protected identity too), each mapped to its (root, registration path)."""
    idents = {}
    for root in roots:
        for base in sorted(REGISTRATION_LEAVES):
            reg = os.path.normpath(os.path.join(root, ".claude", base))
            idents.setdefault(reg, (root, reg))
            try:
                real = os.path.normpath(os.path.realpath(reg))
            except (OSError, ValueError):
                continue
            idents.setdefault(real, (root, reg))
    return idents


def _registration_rule(candidate, idents):
    """R8 over one absolute candidate path against `idents` (_registration_idents): a deny reason
    when it is, by its spelled or its REAL path, a bound product root's hook registration
    (.claude/settings.json or .claude/settings.local.json), or None."""
    hit = idents.get(candidate)
    if hit is None:
        return None
    root, reg = hit
    return ("%r is the Claude Code settings registration %s of the product root %r (matched by its "
            "spelled or its real path): the hook registration cannot be rewritten through the tool "
            "calls it gates (R8). Edit the registration outside a hooked session."
            % (candidate, reg, root))


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
    # The script word resolves LITERALLY, as bash runs it (round 9): a tilde reaches a plain word
    # only inside single quotes, which bash keeps literal, so a tilde-headed word is never the
    # writer (expanding it against HOME would bless a planted `~` directory under the cwd).
    script = rest[0]
    if script.startswith("~"):
        return False
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


def _literal_words(command):
    """A tolerant, never-failing split of a command that is NOT provably plain into its literal
    path-word candidates (R5 coarse pass). Single quotes, double quotes and the dollar-prefixed
    ANSI-C / locale spans are stripped as whole-word quotes with NO escape decoding (an escaped
    protected spelling stays undecoded, a disclosed residual); a backslash keeps the next character
    and drops a line continuation; unquoted blanks and the shell operator characters split words; a
    dollar sign, backquote, parenthesis or brace is kept as an ordinary character (a substitution
    text splits into words on its own operators). The words are resolved as paths by
    _exotic_bash_rule, never dequoted for a token scan or an allowance."""
    words, cur, has = [], [], False
    i, n = 0, len(command)
    while i < n:
        ch = command[i]
        if ch == chr(39):
            end = command.find(chr(39), i + 1)
            if end < 0:
                cur.append(command[i + 1:])
                has = True
                break
            cur.append(command[i + 1:end])
            has, i = True, end + 1
            continue
        if ch == chr(34):
            i += 1
            while i < n and command[i] != chr(34):
                if command[i] == chr(92) and i + 1 < n and command[i + 1] in DQ_ESCAPABLE:
                    cur.append(command[i + 1])
                    i += 2
                    continue
                cur.append(command[i])
                i += 1
            has, i = True, i + 1
            continue
        if ch == chr(36) and command[i + 1:i + 2] in (chr(39), chr(34)):
            i += 1
            continue
        if ch == chr(92):
            if i + 1 < n and command[i + 1] != chr(10):
                cur.append(command[i + 1])
                has = True
            i += 2
            continue
        if ch in WORD_SEPARATORS or ch in (chr(32), chr(9), chr(10)):
            if has:
                words.append("".join(cur))
                cur, has = [], False
            i += 1
            continue
        cur.append(ch)
        has, i = True, i + 1
    if has:
        words.append("".join(cur))
    return words


def _derived_spellings(words):
    """The path spellings an argument word can carry besides its whole self (R5, round 8): (list,
    None), or (None, reason) past MAX_DERIVED_SPELLINGS. A word headed by a short-option run (-oX,
    -xvC/abs, +o) yields every suffix after one or more of the run's letters (a getopt-style option
    takes its value glued: sort -oTODO.md writes TODO.md); and every word yields the suffix after
    each character that is neither a path-word character nor a slash (of=/abs/x, -Wl,-Map,x,
    host:/abs/x, a quoted command string's redirection target). A suffix longer than a platform path
    cannot name a reachable file and is not derived."""
    out, seen = [], set()
    for word in words:
        starts = []
        m = _OPTION_GLUE_RE.match(word)
        if m:
            starts.extend(range(m.start(1) + 1, m.end(1) + 1))
        for i in range(1, len(word)):
            if word[i - 1] not in WORD_CHARS and word[i - 1] != os.sep:
                starts.append(i)
        for i in starts:
            sub = word[i:]
            if not sub or sub == word or len(sub) > MAX_PATH_CHARS or sub in seen:
                continue
            seen.add(sub)
            out.append(sub)
            if len(out) > MAX_DERIVED_SPELLINGS:
                return None, ("the command's words carry more than %d option-glued or embedded path "
                              "spellings, over the derived-spelling budget, so it cannot be fully "
                              "examined; failing closed (R6)" % (MAX_DERIVED_SPELLINGS,))
    return out, None


def _ambient_git_spellings():
    """The path values of the inherited git environment (GIT_PATH_ENV; a list-valued variable split
    on the path separator), and the absolute value of every GIT_TRACE* variable (round 9: git
    appends its trace to that file): each is judged as one more spelling of the Bash command (R5,
    round 8)."""
    out = []
    for name in GIT_PATH_ENV:
        value = os.environ.get(name)
        if not value:
            continue
        parts = value.split(os.pathsep) if name == "GIT_ALTERNATE_OBJECT_DIRECTORIES" else [value]
        out.extend(part for part in parts if part)
    for name in sorted(os.environ):
        value = os.environ[name]
        if name.startswith(GIT_TRACE_PREFIX) and value and os.path.isabs(value):
            out.append(value)
    return out


def _resolved_targets(spellings, cwd, relative=True):
    """Every absolute target the spellings can reach (R5, round 8): (candidates, None), or (None,
    reason) past MAX_BASES or MAX_JUDGED_TARGETS. A relative spelling is resolved against the session
    cwd AND against every directory another spelling resolves to (a redirection of authority: git -C
    dir, --work-tree=dir, curl --output-dir dir, an inherited GIT_WORK_TREE), to a fixed point, so a
    relative operand is judged where the program may actually open it. Each candidate is the
    lexical and the realpath spelling (_candidates), and a tilde-headed spelling is judged in BOTH
    readings, expanded and literal, against every base (round 9: bash keeps a quoted tilde literal,
    while a program may expand one in its own option value). With `relative` false (past the word
    budget) only absolute and tilde-headed spellings resolve."""
    bases, queue, cands, seen = [], [cwd], [], set()
    absolutes_done = False
    while queue:
        base = queue.pop(0)
        if base in bases:
            continue
        if len(bases) >= MAX_BASES:
            return None, ("the command names more than %d directories a relative operand may be "
                          "resolved against, over the base budget, so it cannot be fully examined; "
                          "failing closed (R6)" % (MAX_BASES,))
        bases.append(base)
        for spelling in spellings:
            if os.path.isabs(spelling):
                if absolutes_done:
                    continue
            elif not relative and not spelling.startswith("~"):
                continue
            for cand in _candidates(spelling, base) or ():
                if cand in seen:
                    continue
                seen.add(cand)
                if len(seen) > MAX_JUDGED_TARGETS:
                    return None, ("the command resolves to more than %d candidate targets, over the "
                                  "resolution budget, so it cannot be fully examined; failing closed "
                                  "(R6)" % (MAX_JUDGED_TARGETS,))
                cands.append(cand)
                if os.path.isdir(cand) and cand not in bases:
                    queue.append(cand)
        absolutes_done = True
    return cands, None


def _joined_targets(cands, spellings):
    """The targets a word reaches INSIDE a directory another word names (R5, round 9): cp X docs,
    cp -t docs X, install X docs, ln X docs and mv X /abs/docs each write docs/<basename of X>, so
    every resolved candidate that is a directory receives the basename of every argument spelling as
    one more resolved target (lexical and realpath): (targets, None), or (None, reason) past
    MAX_JUDGED_TARGETS. A join is kept even when it equals a resolved candidate (round 10: a
    dedupe against the candidates let one extra word spelling the joined directory, cp -r -S docs
    src/docs ., drop that directory from the container check)."""
    names = []
    for spelling in spellings:
        name = os.path.basename(os.path.normpath(spelling)) if spelling else ""
        if name and name not in (".", "..") and name not in names:
            names.append(name)
    out, seen = [], set()
    for directory in cands:
        if not names or not os.path.isdir(directory):
            continue
        for name in names:
            for cand in _candidates(os.path.join(directory, name), None, "literal") or ():
                if cand in seen:
                    continue
                seen.add(cand)
                if len(seen) > MAX_JUDGED_TARGETS:
                    return None, ("the command reaches more than %d candidate targets inside the "
                                  "directories it names, over the resolution budget, so it cannot "
                                  "be fully examined; failing closed (R6)" % (MAX_JUDGED_TARGETS,))
                out.append(cand)
    return out, None


def _git_grammar(words):
    """The allowlisted git option grammar (round 10) over a git invocation `words` (words[0] names
    git): (index of the subcommand word, None), (None, None) when no subcommand follows the global
    options, or (None, reason) when a word falls outside the grammar (an unlisted global option, an
    unlisted option of a container subcommand, a value option with no value, or a pathspec magic
    word), which makes the command NOT plain. The grammar is deliberately small: it never tries to
    model every git option, so an unrecognized spelling is refused, never guessed."""
    i = 1
    while i < len(words) and words[i].startswith("-"):
        opt = words[i]
        if opt in GIT_FLAG_GLOBALS:
            i += 1
        elif opt in GIT_VALUE_GLOBALS:
            if i + 1 >= len(words):
                return None, "the git global option %r carries no value" % (opt,)
            i += 2
        elif opt.startswith("--") and opt.split("=", 1)[0] in GIT_VALUE_GLOBALS:
            i += 1
        else:
            return None, ("the git global option %r is outside the recognized git option grammar"
                          % (opt,))
    if i >= len(words):
        return None, None
    sub = words[i]
    for word in words[i + 1:]:
        if word.startswith(":"):
            return None, ("the git word %r is a pathspec magic form (it can reach the repository "
                          "top)" % (word,))
    grammar = GIT_CONTAINER_OPTIONS.get(sub)
    if grammar is None:
        return i, None
    flags, valued, longs, valued_longs = grammar
    j = i + 1
    while j < len(words):
        word = words[j]
        j += 1
        if word == "--":
            break
        if not word.startswith("-") or word == "-":
            continue
        if word.startswith("--"):
            name = word.split("=", 1)[0]
            if word in longs:
                continue
            if name in valued_longs:
                if name == word:
                    j += 1
                continue
            return None, ("the git %s option %r is outside the recognized git option grammar"
                          % (sub, word))
        for k, letter in enumerate(word[1:]):
            if letter in valued:
                if k + 2 == len(word):
                    j += 1
                break
            if letter not in flags:
                return None, ("the git %s option %r is outside the recognized git option grammar"
                              % (sub, word))
    if j > len(words):
        return None, "a git %s option carries no value" % (sub,)
    return i, None


def _container_verb(words):
    """True when a plain command acts on a directory operand's whole contents (round 9): ANY word
    of it (round 10: the command word or one a wrapper runs, setarch x86_64 rm -r docs) naming a
    CONTAINER_VERBS program, or a git container form (_git_container)."""
    for word in words:
        if _command_names(word) & CONTAINER_VERBS:
            return True
    return _git_container(words)


def _git_container(words):
    """True when any word of a plain command names git and, by the allowlisted grammar, its
    subcommand is a GIT_CONTAINER_SUBCOMMANDS form (git itself or git behind a wrapper)."""
    for j, word in enumerate(words):
        if "git" in _command_names(word):
            i, reason = _git_grammar(words[j:])
            if reason is None and i is not None and words[j + i] in GIT_CONTAINER_SUBCOMMANDS:
                return True
    return False


def _container_rule(cand, protected):
    """R5/R8 container check (round 9): a deny reason when `cand` is a directory holding one of the
    `protected` absolute paths (a store tree, a frozen file, a declared view, a registration or a
    pack own directory) at any depth, or None."""
    if not os.path.isdir(cand):
        return None
    prefix = cand if cand.endswith(os.sep) else cand + os.sep
    for path in sorted(protected):
        if path.startswith(prefix):
            return ("a word of this Bash command resolves to the directory %r, which holds the "
                    "protected path %r, and the command removes, moves, re-permissions or "
                    "rewrites a directory operand's whole contents (or writes into it under a "
                    "protected name), so it is denied fail-closed (R5, R8). %s." % (cand, path,
                                                                                   SANCTIONED))
    return None


def _bound_roots(text, cwd, extras=()):
    """The product roots the rosters are resolved from (R5/R7): (roots, None); or (None, reason)
    when the absolute-path discovery budget is exceeded (a truncated scan could silently drop the
    one protected spelling, so the hook denies instead) or when a store tree above a bound location
    cannot be read (_roots_above fails closed). Roots are taken at or above the session cwd (when
    there is one), above every absolute path spelled in `text`, and above every `extras` entry (a
    dequoted Bash word or payload string, tilde-expanded) that is absolute, so an absolute
    protected spelling is judged even when the session sits outside its product tree and a quoted
    root with spaces binds through its whole operand. EVERY bound location is judged both as
    spelled (lexically normalized) and as the KERNEL would resolve it (realpath of the original
    spelling, each symlink resolved before any dot-dot collapses), so a link/../file spelling
    binds the roster of the product the write actually reaches, never only its lexical twin."""
    seeds = []
    if isinstance(cwd, str) and os.path.isabs(cwd):
        seeds.append(cwd)
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
        seeds.append(cand)
    spellings = []
    for seed in seeds:
        # realpath the ORIGINAL spelling (the kernel resolves each link BEFORE a dot-dot climbs
        # out of it); normpath only the lexical twin and the already-resolved result.
        norm = os.path.normpath(seed)
        if norm not in spellings:
            spellings.append(norm)
        try:
            resolved = os.path.normpath(os.path.realpath(seed))
        except (OSError, ValueError):
            continue
        if resolved not in spellings:
            spellings.append(resolved)
    roots = []
    for spelling in spellings:
        got, reason = _roots_above(spelling)
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
    """R5, R6 and R8 for Bash. The sanctioned writer (A1) allows first; a PROVABLY PLAIN command
    (_provably_plain) that runs no command or code named on its own command line (_plain_runs_code)
    takes the exact path check (_plain_bash_rule); every other command takes the coarse product-root
    check (_exotic_bash_rule)."""
    if not isinstance(tool_input, dict) or not isinstance(tool_input.get("command"), str):
        return "the Bash payload carries no command string; failing closed (R6)"
    command = tool_input["command"]
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return ("the Bash payload carries no absolute session cwd, so the protected rosters cannot "
                "be resolved; failing closed (R6)")
    tokens = _plain_words(command)
    if tokens and _is_sanctioned_opf(tokens, cwd):
        return None
    words = _provably_plain(command)
    if words is None or _plain_runs_code(words) is not None:
        return _exotic_bash_rule(command, cwd, tokens)
    return _plain_bash_rule(command, words, cwd)


def _cwd_product_roots(cwd):
    """The product roots at or above the session cwd (both as spelled and as realpathed):
    (roots, None), or (None, reason) when a store tree above the cwd cannot be read (fail closed)."""
    roots = []
    spellings = [os.path.normpath(cwd)]
    try:
        resolved = os.path.normpath(os.path.realpath(cwd))
    except (OSError, ValueError):
        resolved = None
    if resolved is not None and resolved not in spellings:
        spellings.append(resolved)
    for spelling in spellings:
        got, reason = _roots_above(spelling)
        if reason is not None:
            return None, reason
        for root in got:
            if root not in roots:
                roots.append(root)
    return roots, None


def _exotic_bash_rule(command, cwd, tokens):
    """R5/R8 coarse pass for a Bash command that is NOT provably plain (module docstring): deny
    when the session working directory lies inside an OPF product root (a directory holding a
    .working entry), when the command text or an inherited git path variable spells the .working
    store token anywhere (inside a word, a command string or an option value included), or when any
    resolved target lies inside a product root or lands on the enforcement pack own tree (R8). The
    targets are every literal word, every option-glued or delimiter-embedded spelling inside a word
    (_derived_spellings: of=/abs/x, --target-directory=/abs/x, -C/abs, the redirection target inside
    a quoted command string) and every inherited git path value, each resolved against the cwd and
    against every directory another of them names (_resolved_targets); otherwise allow. A protected
    path reached with none of these (a variable, a substitution, an escape or an interpreter own
    language spelling a path no literal text carries) is the disclosed lexical-floor residual."""
    roots, reason = _cwd_product_roots(cwd)
    if reason is not None:
        return reason + "; failing closed (R6)"
    if roots:
        return ("this Bash command is not provably plain, so a lexical hook cannot prove it "
                "read-only, and the session working directory lies inside the OPF product root %r "
                "(its store tree is protected); it is denied fail-closed (R5). %s."
                % (roots[0], SANCTIONED))
    ambient = _ambient_git_spellings()
    if WORKING in command or any(WORKING in value for value in ambient):
        return ("this Bash command is not provably plain and spells the %s store token (inside a "
                "word, a command string, an option value or an inherited git path variable), so a "
                "lexical hook cannot prove it leaves the store untouched; it is denied fail-closed "
                "(R5). %s." % (WORKING, SANCTIONED))
    exempt = frozenset()
    if tokens and not _ASSIGNMENT_RE.match(tokens[0]) and os.sep not in tokens[0] \
            and _PYTHON_RE.match(tokens[0]):
        rest = tokens[1:]
        while rest and _PYFLAGS_RE.match(rest[0]):
            rest = rest[1:]
        if rest:
            exempt = frozenset(_candidates(rest[0], cwd) or ())
    words = _literal_words(command)
    if len(words) > MAX_RESOLVED_WORDS:
        return ("this Bash command is not provably plain and names more than %d words, over the "
                "word-resolution budget, so it cannot be fully examined; failing closed (R6)"
                % (MAX_RESOLVED_WORDS,))
    derived, reason = _derived_spellings(words)
    if reason is not None:
        return reason
    cands, reason = _resolved_targets(words + derived + ambient, cwd)
    if reason is not None:
        return reason
    for cand in cands:
        got, reason = _roots_above(cand)
        if reason is not None:
            return reason + "; failing closed (R6)"
        if got:
            return ("this Bash command is not provably plain, so a lexical hook cannot prove it "
                    "read-only, and a path it names (%r) lies inside the OPF product root %r "
                    "(its store tree is protected); it is denied fail-closed (R5). %s."
                    % (cand, got[0], SANCTIONED))
        if cand not in exempt:
            reason = _guard_rule(cand)
            if reason is not None:
                return reason
    return None


def _plain_bash_rule(command, words, cwd):
    """R5, R6 and R8 for a PROVABLY PLAIN Bash command: the exact path check. The judged spellings
    are every dequoted word, every option-glued or delimiter-embedded spelling inside a word
    (_derived_spellings: sort -oTODO.md, of=alias, -o/abs/TODO.md) and every inherited git path value
    (_ambient_git_spellings). The raw string and every spelling are scanned for the protected tokens
    (boundary-matched); every spelling is resolved against the session cwd and against every
    directory another spelling names (_resolved_targets: git -C dir, --output-dir dir, an inherited
    GIT_WORK_TREE); the rosters bind from the product roots above the cwd, every absolute spelling
    AND every resolved target (so a relative spelling that climbs into a product from outside binds
    that product's rosters); and every resolved target is judged exactly as a file-tool target would
    be (store, frozen, view, the R8 guard and the registration). Only a single plain
    sanctioned-writer invocation (A1, already allowed) may reference a protected token."""
    derived, reason = _derived_spellings(words)
    if reason is not None:
        return reason
    spellings = words + derived + _ambient_git_spellings()
    scan = command + chr(10) + chr(10).join(spellings)
    resolve_all = len(words) <= MAX_RESOLVED_WORDS
    cands, reason = _resolved_targets(spellings, cwd, resolve_all)
    if reason is not None:
        return reason
    joined, reason = _joined_targets(cands, words[1:] + derived)
    if reason is not None:
        return reason
    container = _container_verb(words)
    if _git_container(words):
        # git clean (and the other git container forms) act below the cwd with no path operand.
        for cand in _candidates(cwd, None, "literal") or ():
            if cand not in cands:
                cands.append(cand)
    roots, reason = _bound_roots(command, cwd, spellings)
    if reason is not None:
        return reason + "; failing closed (R6)"
    for cand in cands + joined:
        got, reason = _roots_above(cand)
        if reason is not None:
            return reason + "; failing closed (R6)"
        for root in got:
            if root not in roots:
                roots.append(root)
    frozen, views, reason = _rosters(roots)
    if reason is not None:
        return reason + "; failing closed (R6)"
    reg_idents = _registration_idents(roots)
    kind = _reference_kind(scan, cwd, ((frozen[0], frozen[1]), (views[0], views[1])))
    if kind is not None:
        return ("this Bash command references %s and is not a single plain invocation of the "
                "sanctioned writer (opf record or opf render): a lexical hook cannot prove any "
                "other referencing command read-only, so it is denied fail-closed (R5). %s; read "
                "protected files through the platform Read tool." % (kind, SANCTIONED))
    protected = set(frozen[0]) | set(views[0]) | set(reg_idents) | set(_guarded_prefixes())
    protected.update(os.path.join(root, WORKING) for root in roots)
    for cand in (cands if container else []) + joined:
        reason = _container_rule(cand, protected)
        if reason is not None:
            return reason
    for cand in cands + joined:
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
        reason = _guard_rule(cand)
        if reason is not None:
            return reason
        reason = _registration_rule(cand, reg_idents)
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
    string-scan budget is exceeded (the hook never judges a partial scan). A payload carrying no
    tool_input object, and a payload with no absolute session cwd (its relative strings cannot be
    resolved and no roster can be bound), are denied cannot-evaluate, never read as naming
    nothing. A payload with neither a
    textual nor a resolvable protected reference is allowed (the disclosed residual). The known
    read-only built-ins are allowed outright."""
    if tool_name in READONLY_TOOLS:
        return None
    if not isinstance(tool_input, dict):
        return ("the tool %r is not one this hook knows to be read-only and its payload carries no "
                "tool_input object, so it cannot be examined at all; failing closed (R6, R7)"
                % (tool_name,))
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return ("the tool %r is not one this hook knows to be read-only and its payload carries no "
                "absolute session cwd, so its relative strings cannot be resolved and the "
                "protected rosters cannot be bound; failing closed (R6, R7)" % (tool_name,))
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
    reg_idents = _registration_idents(roots)
    kind = _reference_kind(text, cwd, ((frozen[0], frozen[1]), (views[0], views[1])))
    if kind is None:
        for cand in paths:
            reason = _guard_rule(cand)
            if reason is not None:
                return reason
            reason = _registration_rule(cand, reg_idents)
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
    if not isinstance(tool_name, str) or not tool_name:
        sys.stderr.write("opf-pretooluse-deny: cannot evaluate: the payload carries no non-empty "
                         "tool_name. Failing closed: the tool call is blocked.\n")
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
