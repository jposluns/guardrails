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
     classifier (D-DISCARD-SOUND-RULE; the shared plain-command specification, QA round 7, revised
     2026-10-06), decided on the RAW command string before any lexing: (1) every character is
     printable ASCII (no tab, newline, carriage return, NUL or non-ASCII character, so no Unicode
     digit, homoglyph or invisible character); (2) no dollar sign, backquote, backslash, semicolon,
     ampersand, pipe, angle bracket, parenthesis, brace, square bracket, star, question mark,
     exclamation mark, hash or tilde appears outside a single-quoted span, and no assignment
     precedes the command word; (3) a single-quoted span carries no dollar sign, backquote, square
     bracket or backslash (a builtin such as printf -v or test -v re-evaluates a quoted array
     subscript), a double-quoted span carries none of the rule-2 characters, and every quote is
     terminated; (4) words are separated by spaces only, and the command word is a bare unquoted
     name, or an absolute path whose directory is exactly /usr/bin, /bin, /usr/local/bin or
     /usr/sbin, whose basename is on the allowlist PLAIN_ALLOWED_COMMANDS: git and opf (which this
     hook judges by its own semantic check) and programs that cannot run another program (ls, cat,
     grep, cp, mv, rm and the others listed). So no redirection, here-document, sequencing,
     substitution, expansion, glob, escape, line continuation, wrapper, interpreter, unlisted
     program or dashed git builtin (git-rm, /usr/lib/git-core/git-checkout) can lead a plain
     command, and the hook's own lexer never has to read one. A plain git or opf command that still
     names a command to run on its own command line (an argument word that is a shell or
     interpreter name, or that carries a shell or interpreter command string or a leading
     exclamation mark, a git configuration override, a code-running git subcommand, a
     command-naming git option, or a git global option outside the allowlisted grammar:
     _plain_runs_code) is judged as not plain; an argument word of any other allowlisted program
     is data, never a command that program runs. A PROVABLY PLAIN command
     takes the EXACT path check: the raw string and every dequoted word are scanned for the
     protected tokens (the .working store tree by any substring spelling, a session cwd inside a
     .working tree, and the frozen (R3) and declared-view (R4) paths matched with path boundaries),
     the judged spellings are every dequoted word, every spelling an argument word carries inside
     itself (round 8: a value glued to a short-option run, ls -ITODO.md or -I/abs/TODO.md, and the
     text after each delimiter inside a word, of=alias or --target-directory=/abs/x) and every value
     of an inherited git path variable (GIT_DIR, GIT_WORK_TREE and the others in GIT_PATH_ENV);
     every spelling is resolved against the session cwd AND against every directory another
     spelling names (git -C dir, --output-dir dir, an inherited GIT_WORK_TREE: a redirection of
     authority), to a fixed point; the rosters bind from the product roots above the session cwd,
     every absolute spelling and every resolved target; and every resolved target is judged exactly
     as a file-tool target would be (store, frozen, view, the pack own files (R8) and the
     registration (R8)). Round 9: for a cp, mv or ln command word (JOIN_WRITERS), every
     directory a spelling resolves to also receives the basename of every argument spelling as one
     more resolved target (cp X docs, cp -t docs X and mv X /abs/docs write docs/<basename of X>);
     a command led by rm, rmdir, mv or chmod (CONTAINER_VERBS, matched on the command word alone)
     denies when a resolved operand is a directory HOLDING a protected path (a bound store tree, a
     frozen file, a view, a registration or the pack tree), and so does a cp, mv or ln whose
     directory-plus-basename target is such a directory (cp -r src/docs .); a tilde-headed
     spelling is judged in BOTH readings, literal under the cwd (bash keeps a quoted tilde
     literal) and expanded; and an absolute GIT_TRACE* value is one more inherited
     spelling. Round 10 (D-RESCOPES-A) stops parsing toward completeness: every
     directory-plus-basename join is container-checked even when another word already resolves
     to it (no dedupe across the two checks, so a cp backup suffix or a second operand spelling
     the joined directory no longer hides it); and git is read only through a small allowlisted
     global-option grammar (_git_grammar: GIT_FLAG_GLOBALS and GIT_VALUE_GLOBALS), so an unlisted
     global option (--attr-source, --shallow-file) makes the command NOT plain, and it takes the
     coarse rule below (it denies when the session cwd or a literal word lies in a product root,
     and is allowed in a session bound to none). Round 11 (the git work-tree rule): a plain git
     command whose subcommand rewrites the working tree or the index (GIT_WORKTREE_SUBCOMMANDS:
     checkout, restore, reset, clean, stash, switch, merge, pull, rebase, cherry-pick, revert, am,
     apply, rm, mv, read-tree, checkout-index and worktree; round 13 adds sparse-checkout, bisect,
     submodule other than its status and summary forms, update-index, merge-recursive,
     merge-resolve, merge-octopus, merge-subtree, merge-index, merge-one-file, filter-branch,
     rerere and quiltimport; round 21 inverts the list: every public git 2.53 command outside
     the reviewed GIT_NO_WORKTREE_WRITE set, which names a reason per member) DENIES whenever it
     acts in a bound
     product (a product root at or above the session cwd, an absolute spelling or a resolved
     operand, git -C and --work-tree values included), whatever its pathspec spelling, because git
     expands a glob ('../*') or pathspec magic (:(top), :/docs) itself; a dry run of git rm, mv
     or clean read through a strict grammar (GIT_DRY_RUN_GRAMMAR: only listed option letters and
     exact long options, -n or --dry-run among them) writes nothing and is exempt; outside every
     bound product such a command denies when the session cwd, a directory it names or the
     repository top above either holds the pack own tree (R8); a member that runs code (bisect,
     submodule other than its read forms, filter-branch) is not plain and takes the coarse rule
     below, which applies the same repository-top check (round 14: to every not-plain command,
     below). Every other git subcommand
     (commit, add, push, status, log, diff, show, fetch and the rest) is judged by the exact path
     check alone, so it is allowed unless it references or resolves to a protected path. The
     dashed builtin forms (git-checkout, /usr/lib/git-core/git-rm) are off the allowlist and take
     the coarse rule. Round 14: a cp, mv or ln command word carrying ANY backup option (-b, -S,
     --backup or --suffix, bare, valued or abbreviated, or a short-option cluster holding b or S;
     _backup_option) DENIES with a named reason when it acts in a bound product, because the
     backup renames an existing destination to that destination plus a suffix no word spells
     (cp --backup=simple --suffix=.md notes.txt docs/STATUS overwrites docs/STATUS.md); install,
     which takes the same options, is not plain, and the coarse rule names the same reason when
     it denies one; ln also joins every argument basename into the session cwd (ln with one
     operand links it there under its basename); git submodule status and summary (after any -q
     or --quiet) are plain read forms, never a code-running subcommand; and the coarse rule
     reads the git work-tree subcommand over EVERY literal word after any word naming git, and
     over every dashed git-<name> builtin word (_git_worktree_words), so a global option outside
     the grammar (git -c core.abbrev=7 checkout -- .) or a second command (git checkout -- .;
     true) no longer hides it from the repository-top check. Round 15: that not-plain read
     exempts no dry run and no submodule read form (the literal words carry no command boundary,
     so git rm -rf .; echo -n would read the later -n as a dry run of git rm); the dry-run grammar
     and the read forms exempt only a plain single git invocation; and git help whose every option
     word prints (GIT_HELP_PRINT_OPTIONS: -a, -g, -c and their long forms, among others) is no
     longer a code-running subcommand, while -w, --web, -i, --info and every other option word
     keep it not plain. Past the derived-spelling, base
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
split, without its allowlist, so the python3 launcher form below can be read): printable ASCII only,
no metacharacter outside a single-quoted span, double-quoted spans free of them, single-quoted spans
free of a dollar sign, backquote, square bracket or backslash, every quote terminated. So no second
command, redirection, substitution or expansion can ride along, while a sanctioned invocation may
still QUOTE prose or a path that names a protected token (an `opf record` title, an `opf render
--root` operand with spaces or parentheses, single-quoted). A leading
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
    variable, glob, alias, function, command or process substitution, an interpreter one-liner, or
    any other spelling in which no protected token appears textually in the command string. This
    includes a directory change whose destination no word spells (cd -, a bare cd to HOME, a CDPATH
    lookup, a pushd or popd stack entry) followed, in the same command, by a relative spelling that
    reaches a protected path only from that destination: such a command is not plain, so it denies
    when the session cwd lies inside a product root, but run from outside every product root with
    no literal word inside one (cd - and rm -rf docs) it is allowed. A directory change whose
    destination IS spelled (cd /abs/product, cd ../product/docs) binds that product and denies.
    R5 is a lexical floor, not a sandbox.
  - A plain command whose program runs code the command line does not name: a git hook, alias,
    pager, filter, diff or merge driver, credential helper or fsmonitor taken from repository or
    user configuration (a configuration alias standing for a work-tree rewrite, git co for git
    checkout, and a commit or merge hook that writes the work tree included: git aliases and
    configuration-driven writes are disclosed here by name, not chased), a program configured
    through an environment variable the session inherited, an exported shell function or alias
    shadowing an allowlisted command word, and a same-named program planted on PATH ahead of an
    allowlisted command word. The allowlist and the plain semantic check exclude the inline forms
    only; the configuration, the function and the planted program are same-user preparation.
    Round 15 names one more: a plain git help naming a page (git help log) shows it in the
    configured format, man and its pager by default, or the viewer the help.format, man.viewer
    and help.browser configuration names; only the explicit viewer options (-w, --web, -i, --info
    and every other option outside GIT_HELP_PRINT_OPTIONS) keep it not plain.
  - A plain command whose operand CONTAINS a protected path rather than lying on it, outside the
    container rule: the exact check judges words that resolve INTO a protected path, the
    directory-plus-basename joins of cp, mv and ln and, for rm, rmdir, mv and chmod, a directory
    operand holding a protected path of a BOUND root. So a copy of a directory's whole contents
    under a name no operand carries (cp -r src/. docs, cp -rT src/docs docs), a git subcommand
    outside GIT_WORKTREE_SUBCOMMANDS that still writes the index (the index writers git add, git
    stage and git commit -a, which record the work tree's own content, are in the round-21
    GIT_NO_WORKTREE_WRITE set and judged by the exact path check alone; svn, p4, cvsimport,
    archimport and cvsexportcommit, documented to write a work tree, are not installed with git
    2.53 here, so they are outside GIT_PUBLIC_SUBCOMMANDS and never plain), a
    not-plain command, run from a repository whose top holds the pack own tree and binding no
    product, whose git command word or work-tree subcommand no literal word spells (a variable,
    substitution, alias or function supplies it: g=git; $g checkout -- .), and a
    work-tree rewrite or a recursive remove reaching a product root that the session neither sits
    in nor binds (no product root above the cwd or any operand: git reset --hard, git stash or git
    rm -r '*' run from a repository top or a sibling directory above or beside the product root,
    rm -r of such an ancestor) reach a protected file with no protected token.
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
  - Destinations a command forms without spelling them (round 14 scope). A backup option outside
    every bound product is judged by the exact path check alone: its backup lands beside a
    destination that is itself judged, so it reaches the pack own tree only through a
    destination inside that tree, which R8 already denies, and a rename never replaces a
    non-empty directory. SIMPLE_BACKUP_SUFFIX and VERSION_CONTROL only name a backup that an
    option already asks for. The other forms of the allowlisted programs that write a
    destination no operand spells literally were checked and are handled: cp, mv and ln -t and
    --target-directory (the round-9 joins), cp --parents and ln with one operand (the round-14
    cwd join); ln -r and ln -s change only the link text, and cp -T, mv -T, ln -T and mv
    --exchange write only named operands. install (-D, -t, a backup option) is off the
    allowlist and takes the coarse rule, so outside every bound product it is judged by that
    rule alone. Round 16 corrects the cp --parents claim: cp --parents /abs/src.txt docs writes
    docs/abs/src.txt (the source's WHOLE spelling under the directory, leading slashes dropped;
    GNU coreutils 9.7 confirmed), which neither the absolute resolution nor the basename join
    reached. A cp, mv or ln carrying --parents (or an abbreviation of it) now joins EVERY
    argument spelling, absolute and relative, whole, under every directory the command names
    (_parents_targets), each join judged exactly and by the container check; in a plain command
    past the word budget, where a relative spelling no longer resolves, such a command denies
    with a named reason, and the coarse rule judges the same joins for its product-root check.
    mv and ln on the test host reject --parents (each exits 1) and are joined the same way.
    Round 16 also covers the git subcommands that write a file whose name they construct or an
    option names (GIT_OUTPUT_WRITERS below and _git_output_reason): such a file landing at or
    under a product root, or in the pack own tree, denies with a named reason. Round 17 withdraws
    the round-16 standard-output and output-location exemptions, which were decided by option
    membership (git format-patch -1 HEAD --stdout --no-stdout and git format-patch -1 HEAD
    --subject-prefix --stdout write the patch file; git pack-objects --stdout --no-stdout pack
    writes the pack): in a bound product a subcommand that constructs a file name (format-patch,
    bugreport, diagnose, unpack-file, pack-objects, index-pack, bundle, clone, mailsplit) denies
    whatever its options, and any other output-writing subcommand (diff, log, show, archive and
    the rest of GIT_OUTPUT_WRITERS) denies when an output option word appears at all, wherever its value
    points; a lone -- does not end that scan. This is a disclosed over-refusal: git format-patch
    --stdout, git pack-objects --stdout, git bundle create - HEAD, git bundle verify, git
    format-patch -o /elsewhere, git log --output=/elsewhere/x and git log -- --output=x are
    refused from a product root; run them from outside every product root (redirecting standard
    output there). Only the exact three words git <subcommand> -h, which print usage and write
    nothing, are exempt; any other word sequence with -h (git clone -h x, git format-patch -h
    --stdout) takes the normal rule. A git configuration value naming an output location
    (format.outputDirectory) is the configuration residual named above. Round 22 scans EVERY git
    subcommand for an output option word (git blame, annotate and pickaxe inherit --output from
    the diff options): a GIT_OUTPUT_OPTIONS name or any abbreviation of one (_git_output_option),
    judged by the option name alone, never by the option git itself resolves that word to on
    the subcommand at hand. This is a disclosed over-refusal: an abbreviation of --output (or of
    another GIT_OUTPUT_OPTIONS name) on a subcommand where git resolves it to a different option
    is still read as an output option and refused, in a bound product whatever follows it (git
    branch --o, which git 2.53 reads as --omit-empty and which writes no file). Only the prefixes
    of the exact git 2.53 options in GIT_OUTPUT_SHADOWS (--index, --filter, --expire) are
    excluded outside GIT_OUTPUT_WRITERS; no further per-subcommand or global exemption is made,
    since one would widen what the output check must prove. Spell such an option in full (git
    branch --omit-empty is no output option word and is allowed); running it from outside every
    product root is no workaround, since the plain git branch --o still denies there (its option
    '--o' carries no value).
  - Platform hook-startup failures may fall through to the platform's normal permission flow.
  - Shell or interpreter wrapping of the platform itself is outside the hook's reach.
  - Over-approximation is the accepted cost of the fail-closed posture. A provably plain command
    that references any protected token outside A1 denies, read-only forms included (cat, grep or
    git diff of a store path; git add of a declared view TODO.md; git log of a plan-frozen
    LEGACY.md). A command that is NOT provably plain denies whenever the session working directory,
    or any literal path word, lies inside a product root, even when it touches nothing protected: so
    from a cwd inside a product root a parameter expansion (echo $HOME), a command substitution
    (gh pr create --body "$(...)"), any program off the rule-4 allowlist (python3 script.py, make
    test, bash -c ..., env, sed, find, tar, sort, jq, curl, gh, pytest, ./tool, a dashed git builtin
    such as git-log), an eval, a line continuation, an ANSI-C or locale quote, a glob, a
    redirection, any here-document (a quoted commit-message here-document included), a tab or
    non-ASCII character, a dollar sign, backquote, square bracket or backslash even inside single
    quotes (opf record task 'costs $5'), a git configuration override or config subcommand, and a
    git argument word carrying a shell or interpreter command string (a commit message beginning
    "sh -c" or "!") all deny; run them from outside the product tree or outside a hooked session,
    read protected files through the Read tool, and change the store through the opf CLI. R8
    denies rewriting the pack own files and the per-product registration through the gated tools,
    and the word-resolution pass of a plain command denies a command that merely names a protected
    or pack-owned file as a resolvable argument. Round 8 widens both passes: a word whose glued
    option suffix or post-delimiter suffix happens to name a protected path (a prose word such as
    -xTODO.md, or key=LEGACY.md), a relative operand that names a protected path under ANY
    directory the same command names (ls docs STATUS.md where docs/STATUS.md is a view), any
    not-plain command that spells .working anywhere (grep .working from outside every product), a
    git command under a non-trivial inherited GIT_PAGER, GIT_EXTERNAL_DIFF or similar, and a plain
    command naming more than MAX_BASES directories, all deny. Round 9 widens them again: a remove,
    move or re-permission of ANY directory holding a protected path (mv notes.md docs where docs
    holds a view, chmod -R u+w . from a product root), a cp, mv or ln directory operand beside a
    word whose basename names a protected file inside it, a quoted tilde spelling whose literal
    OR expanded reading is protected, and every path below a directory the session's own user
    made unsearchable, all deny. Round 10 named some refusals that predate it (git checkout main
    and git clean -n already denied from a product root before round 10) and added the git
    global-option grammar. Round 11 states the git refusals by subcommand: in a bound product
    every git subcommand in GIT_WORKTREE_SUBCOMMANDS denies, even where it writes only an
    unprotected file or only .git, or writes nothing (git checkout main, git checkout -b feature,
    git switch main, git restore notes.txt, git restore --staged notes.txt, git rm notes.txt, git
    mv notes.txt n2.txt, git stash list, git worktree list, git reset --soft HEAD; round 13: git
    sparse-checkout list, git bisect log, git rerere status, git update-index --refresh and a
    bare git submodule, which git reads as status), and so does a
    git rm, mv or clean dry run outside the strict dry-run grammar (git clean -e x -n, git rm
    --dry -r x); outside every bound product such a subcommand run in a repository whose tree
    holds the pack own files (R8) denies from any directory of that repository; and a git global
    option outside the allowlisted grammar (git -p log, git --bare status, git --exec-path, git
    --attr-source HEAD log, git --namespace x log) is not plain and denies from a product root.
    Round 11 also withdraws the round-10 over-refusals that wrote nothing: ls docs ., du -sh docs
    ., git log -- docs ., grep -rn mv docs, cat rm-old.txt docs, ls git -la, grep git -r src,
    grep -rn python src, git clean -n, git rm -n notes.txt, git add :/docs and git show :docs/x
    are allowed when they reference no protected path. Round 14: in a bound product every cp,
    mv or ln carrying a backup option denies, even where the backup lands on nothing protected
    (cp -b notes.txt n2.txt); ln joins every argument basename into the session cwd, so ln -s
    /elsewhere/TODO.md links/TODO.md from a product root whose TODO.md is a view denies; and at a
    location whose repository top holds the pack own tree, a not-plain command carrying a git
    word and any later work-tree subcommand name, even as data (git log; echo reset), denies.
    Round 14 withdraws the round-13 refusal of git submodule status and git submodule --quiet
    summary in a bound product. Round 15: at a location whose repository top holds the pack own
    tree, a not-plain command carrying a git word and a later work-tree subcommand word denies
    even where that subcommand is a dry run or a submodule read form (git -c core.abbrev=7 rm -n
    x, git -c a.b=c submodule status, git status | grep -n x where no work-tree word appears
    stays allowed); and git help -m, --man, a --no- viewer negation, an abbreviated option or a
    short-option cluster (git help -av) is not plain and denies from a product root. Round 15
    withdraws the refusal of git help forms that only print (git --no-pager help -a, git help
    -g, git help --config, git help log). Round 16: a cp, mv or ln carrying --parents joins
    every argument spelling under every named directory, so a dot spelling (cp --parents
    notes.txt . from a product root) or a destination spelled to climb back to itself joins as
    that directory and denies by the container check; in a bound product or naming a product
    path, a git subcommand that writes a file whose name it constructs or an option names
    denies when the cwd, any directory the command names, or the option value lies at or under
    a product root (git format-patch -1 HEAD, git diff --output=out.patch, git bugreport), git
    clone denies whenever the cwd or any word lies in one (git clone url /elsewhere/x from a
    product root included), and git bundle create with an option outside its recognized
    grammar denies. Round 17: in a bound product every such constructing subcommand denies
    whatever its options (git format-patch -1 --stdout, git pack-objects --stdout and git bundle
    create - HEAD included), and an output option denies wherever its value points (git
    format-patch -1 -o /elsewhere, git log --output=/elsewhere/x); only git <subcommand> -h,
    exactly those three words, is exempt. R6 denies every write under a
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
decision or silent allow) and 2 (blocking error) only, the floor guard's below included. These statuses hold
even when a standard stream
cannot be written or flushed: every stderr diagnostic is best-effort (write, then flush, each failure
swallowed, so the EXIT CODE carries the decision), a deny decision whose stdout write or flush fails exits
2 instead of 0 (a lost deny blocks, never allows), and the hook leaves through os._exit after flushing
both streams best-effort, so the interpreter-exit flush of a std stream cannot replace a blocking exit 2
with the interpreter's own non-blocking status (a full device or a broken pipe once ended these paths with
status 120, and a closed descriptor 2 with status 1, each of which waves the tool call through).

PYTHON FLOOR: this hook requires Python 3.14 or newer, the pack floor that .aiqt/core/python-floor.toml
states and tools/check_python_floor.py enforces; this file is a guarded-surfaces entry there. The guard at
the top of this file is the gate's canonical CLI form with refusal exit 2, not its nonblocking (exit 1)
form and not the aiqt_hooks.py hook form: this hook serves PreToolUse only, where exit 1 is a non-blocking
error that lets the tool call proceed, while exit 2 blocks it and feeds standard error back to Claude. So
an interpreter older than Python 3.14 that can start the hook reads no input, writes one line beginning
`error: pretooluse_deny.py requires Python 3.14 or newer` to standard error (a best-effort write: the
exit does not depend on it) and exits 2: the tool call is
blocked (cannot evaluate), under the output condition above. An older interpreter that cannot start the
hook (one that cannot compile this file, or a launch that fails before the guard runs) never reaches the
guard and fails with its own error first; that exit status is not set by this hook, and an exit other than
2 lets the call proceed, so register the hook with an interpreter at or above the floor (the registration
above names python3; point it at a 3.14 or newer interpreter where python3 is older).
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: pretooluse_deny.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

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
# THE PROVABLY PLAIN CLASSIFIER (R5): the shared plain-command specification (revised 2026-10-06),
# decided on the raw command string before any lexing. Rule 1: every character is printable ASCII
# (0x20 to 0x7E; no tab, newline, carriage return, NUL or non-ASCII character, so no Unicode digit,
# homoglyph or invisible character). Rule 2: PLAIN_FORBIDDEN never appears outside a single-quoted
# span: dollar sign, backquote, backslash, semicolon, ampersand, pipe, the two angle brackets, the two
# parentheses, the two braces, the two square brackets, star, question mark, exclamation mark, hash
# and tilde, each built from its code point so none appears literally here. Rule 3: a single-quoted
# span may carry no PLAIN_SQ_FORBIDDEN character (dollar sign, backquote, the two square brackets,
# backslash: a builtin such as printf -v or test -v re-evaluates a quoted array subscript, so
# 'a[$(cmd)]' runs cmd); a double-quoted span may carry no PLAIN_FORBIDDEN character; an
# unterminated quote is not plain. Rule 4: words are separated by spaces only; the command word is
# bare (PLAIN_COMMAND_RE, so no quote and no leading assignment) and its basename is on the
# PLAIN_ALLOWED_COMMANDS allowlist, spelled as a bare name or as an absolute path whose directory is
# exactly one of PLAIN_COMMAND_DIRS (any other path, ./ls or /tmp/x/ls, is not plain).
PLAIN_FORBIDDEN = frozenset(chr(c) for c in (36, 96, 92, 59, 38, 124, 60, 62, 40, 41, 123, 125,
                                             91, 93, 42, 63, 33, 35, 126))
PLAIN_SQ_FORBIDDEN = frozenset(chr(c) for c in (36, 96, 91, 93, 92))
PLAIN_COMMAND_RE = re.compile(r"\A[A-Za-z0-9_./-]+\Z")
# Rule 4's allowlist (the shared specification: a deny list of interpreter names was bypassed by a
# versioned interpreter path). git and opf CAN run other programs, so each is judged by this hook's
# own semantic check (_plain_runs_code, the git work-tree rule, A1); every other listed program
# cannot run another program, so an argument word of it is data, never a command it runs. Every
# other command word (a shell, an interpreter at any version, a wrapper, sed, find, tar, make, sort,
# a dashed git builtin such as git-rm or /usr/lib/git-core/git-checkout) is NOT plain and takes the
# coarse rule. Allowlist membership never makes a command safe by itself.
PLAIN_ALLOWED_COMMANDS = frozenset((
    "git", "opf", "ls", "cat", "echo", "printf", "pwd", "true", "false", "test", "head", "tail",
    "wc", "grep", "egrep", "fgrep", "diff", "cmp", "stat", "du", "df", "date", "basename",
    "dirname", "realpath", "readlink", "uniq", "cut", "tr", "mkdir", "rmdir", "touch", "cp", "mv",
    "rm", "ln", "chmod"))
PLAIN_COMMAND_DIRS = frozenset(("/usr/bin", "/bin", "/usr/local/bin", "/usr/sbin"))
# The allowlisted programs that can run another program: only these have their argument words read
# for a command or code they may run (_plain_runs_code).
PLAIN_CODE_RUNNERS = frozenset(("git", "opf"))
# An argument word's runner spelling: a name with at most a version suffix (python3, python3.12),
# never a longer identifier (PYTHON_VERSION, sh-notes).
_RUNNER_WORD_RE = re.compile(r"\A([a-z]+)[0-9.]*\Z")
# The shell and interpreter names that, at the head of a command string carried INSIDE a plain
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
# The SMALL allowlisted git global-option grammar (round 10, D-RESCOPES-A): the subcommand is
# recognized only past these global options (a value global as a separate word, or --git-dir= and
# --work-tree= glued); any other global option (git 2.53 accepts --attr-source <tree-ish> and
# --shallow-file <file> as separate words, and a value word would otherwise be read as the
# subcommand) makes the command NOT plain.
GIT_FLAG_GLOBALS = frozenset(("--no-pager", "-P", "--no-optional-locks", "--literal-pathspecs",
                              "--no-replace-objects", "--version"))
# The git subcommands that rewrite the working tree or the index (round 11): in a BOUND product (a
# product root at or above the session cwd, an absolute spelling or a resolved operand) a plain git
# command naming one of these denies whatever its pathspec form (a glob such as '../*', pathspec
# magic, no operand at all), since git expands the pathspec itself; outside every bound product it
# denies when the cwd, a directory it names or the repository top above either holds the pack own
# tree (R8). Every other git subcommand is judged by the exact path check alone.
# Round 13 adds sparse-checkout (set or reapply removes every file outside the sparse cone),
# bisect (start, good, bad, skip and reset check out a commit), submodule (update, deinit, foreach,
# absorbgitdirs, add and set-url write the work tree; only status and summary read, see
# GIT_SUBMODULE_READ), update-index (writes any index entry), the merge strategy backends
# merge-recursive, merge-resolve, merge-octopus and merge-subtree, merge-index and merge-one-file,
# filter-branch (checks the rewritten HEAD out), rerere (writes a recorded resolution into a
# conflicted file) and quiltimport (applies patches to the work tree). bisect, submodule and
# filter-branch run code and so are never plain: the coarse rule applies the same unbound
# repository-top check to them (_exotic_bash_rule).
# Round 20 adds subtree, with NO read form: in the git 2.53 git-subtree script add, merge and pull
# (and split or push with --rejoin, a word read anywhere before a lone --) run git read-tree,
# git checkout or git merge on the work tree, and split without --rejoin still deletes and
# recreates $GIT_DIR/subtree-cache/<pid>, writes commit objects and, with -b, updates a branch, so
# every git subtree form denies in a bound product (git subtree -h included: disclosed).
# Round 21 derives the set by exclusion (GIT_WORKTREE_SUBCOMMANDS, after GIT_PUBLIC_SUBCOMMANDS
# below): every public command outside the reviewed GIT_NO_WORKTREE_WRITE set.
# The read forms of git submodule (round 13): the first word after any -q or --quiet is exactly one
# of these. Every other form (the bare command and --cached included) is a work-tree rewrite.
GIT_SUBMODULE_READ = frozenset(("status", "summary"))
# The dry-run grammar (round 11): git rm, mv or clean whose every option word is one of these short
# letters or exact long options, at least one of them the dry run (-n, --dry-run), writes nothing
# and is judged like any other git subcommand. Any other option (a valued one, an abbreviation, a
# --no- negation) leaves the subcommand a work-tree rewrite.
GIT_DRY_RUN_GRAMMAR = dict(
    rm=("rfqn", ("--dry-run", "--force", "--quiet", "--cached", "--ignore-unmatch")),
    mv=("fkvn", ("--dry-run", "--force", "--verbose")),
    clean=("dfqxXn", ("--dry-run", "--force", "--quiet")))
GIT_CODE_SUBCOMMANDS = frozenset(("config", "filter-branch", "bisect", "submodule", "difftool",
                                  "mergetool", "daemon", "instaweb", "send-email", "credential",
                                  "svn", "p4", "cvsimport", "archimport", "web--browse",
                                  "for-each-repo", "remote-ext"))
# The public git commands (round 19, PD-427-GIT-SCOPE): every name git 2.53 lists with
# git --list-cmds=main that carries no "--", frozen here so the verdict never depends on the
# installed build or on PATH. A subcommand word that is an INTERNAL helper (a name carrying "--":
# submodule--helper, whose foreach runs the command its words name, checkout--worker,
# credential-cache--daemon, difftool--helper, fsmonitor--daemon, mergetool--lib,
# sh-i18n--envsubst, upload-archive--writer, web--browse and the retired bisect--helper and
# rebase--helper) or a name outside this set (an alias, a git-<name> program found on PATH, a
# command another git build ships) runs code the hook cannot read, so it is never plain
# (_git_unlisted) and a not-plain command carrying one takes the unbound repository-top check
# (_git_worktree_words). Round 19 also adds for-each-repo (runs the git command its words name
# in every configured repository) and remote-ext (runs the command its address names) to
# GIT_CODE_SUBCOMMANDS.
GIT_PUBLIC_SUBCOMMANDS = frozenset((
    "add", "am", "annotate", "apply", "archive", "backfill", "bisect", "blame", "branch",
    "bugreport", "bundle", "cat-file", "check-attr", "check-ignore", "check-mailmap",
    "check-ref-format", "checkout", "checkout-index", "cherry", "cherry-pick", "clean", "clone",
    "column", "commit", "commit-graph", "commit-tree", "config", "count-objects", "credential",
    "credential-cache", "credential-store", "daemon", "describe", "diagnose", "diff", "diff-files",
    "diff-index", "diff-pairs", "diff-tree", "difftool", "fast-export", "fast-import", "fetch",
    "fetch-pack", "filter-branch", "fmt-merge-msg", "for-each-ref", "for-each-repo",
    "format-patch", "fsck", "fsck-objects", "gc", "get-tar-commit-id", "grep", "hash-object",
    "help", "hook", "http-backend", "http-fetch", "http-push", "imap-send", "index-pack", "init",
    "init-db", "instaweb", "interpret-trailers", "last-modified", "log", "ls-files", "ls-remote",
    "ls-tree", "mailinfo", "mailsplit", "maintenance", "merge", "merge-base", "merge-file",
    "merge-index", "merge-octopus", "merge-one-file", "merge-ours", "merge-recursive",
    "merge-recursive-ours", "merge-recursive-theirs", "merge-resolve", "merge-subtree",
    "merge-tree", "mergetool", "mktag", "mktree", "multi-pack-index", "mv", "name-rev", "notes",
    "pack-objects", "pack-redundant", "pack-refs", "patch-id", "pickaxe", "prune", "prune-packed",
    "pull", "push", "quiltimport", "range-diff", "read-tree", "rebase", "receive-pack", "reflog",
    "refs", "remote", "remote-ext", "remote-fd", "remote-ftp", "remote-ftps", "remote-http",
    "remote-https", "repack", "replace", "replay", "repo", "request-pull", "rerere", "reset",
    "restore", "rev-list", "rev-parse", "revert", "rm", "send-pack", "shell", "shortlog", "show",
    "show-branch", "show-index", "show-ref", "sparse-checkout", "stage", "stash", "status",
    "stripspace", "submodule", "subtree", "switch", "symbolic-ref", "tag", "unpack-file",
    "unpack-objects", "update-index", "update-ref", "update-server-info", "upload-archive",
    "upload-pack", "var", "verify-commit", "verify-pack", "verify-tag", "version", "whatchanged",
    "worktree", "write-tree"))
# Round 21 (CLASSIFY BY EXCLUSION): a hand-kept writer list missed subtree (round 20) and then
# merge-recursive-ours and merge-recursive-theirs, so the classification is inverted. Every public
# git 2.53 command (GIT_PUBLIC_SUBCOMMANDS, read from git --list-cmds=main) is a work-tree writer
# unless it is in GIT_NO_WORKTREE_WRITE, the set REVIEWED as never writing a work-tree file (an
# index, object, ref or configuration write inside the git directory is not a work-tree write; a
# file an output option names, and the file a GIT_GENERATED_WRITERS member constructs, is judged
# by the output rule, _git_output_reason, which denies it in a bound product and wherever it lands
# in a product root or the pack own tree). A name that could not be classified with evidence is
# left out, so it denies (over-refusal only). Each member names
# its reason; the hook, editor, pager and remote push-to-checkout behaviour that configuration
# selects is the disclosed configuration residual.
GIT_NO_WORKTREE_WRITE = frozenset((
    "add",  # writes the index and objects from the work tree's own content
    "annotate",  # reads history and prints
    "archive",  # writes standard output; its -o or --output file is the output rule's
    "backfill",  # fetches missing blobs into the object store
    "blame",  # reads history and prints
    "bugreport",  # writes only the file the output rule computes (GIT_GENERATED_WRITERS)
    "bundle",  # writes only the file the output rule computes (GIT_GENERATED_WRITERS)
    "branch",  # writes refs and configuration (refuses to force the checked-out branch)
    "cat-file",  # reads objects and prints
    "check-attr",  # reads attributes and prints
    "check-ignore",  # reads ignore rules and prints
    "check-mailmap",  # reads the mailmap and prints
    "check-ref-format",  # checks a name and prints
    "clone",  # writes only the file the output rule computes (GIT_GENERATED_WRITERS)
    "cherry",  # reads history and prints
    "column",  # formats standard input to standard output
    "commit",  # writes objects, refs and the index (commit -a and a pathspec commit)
    "commit-graph",  # writes the object directory; --object-dir is the output rule's
    "commit-tree",  # writes one commit object
    "count-objects",  # reads the object store and prints
    "describe",  # reads refs and prints (--dirty refreshes index stat data only)
    "diagnose",  # writes only the file the output rule computes (GIT_GENERATED_WRITERS)
    "diff",  # prints; its --output file is the output rule's
    "diff-files",  # prints; its --output file is the output rule's
    "diff-index",  # prints; its --output file is the output rule's
    "diff-pairs",  # reads standard input and prints; --output is the output rule's
    "diff-tree",  # prints; its --output file is the output rule's
    "fast-export",  # writes standard output; --export-marks is the output rule's
    "fetch",  # writes objects, refs and FETCH_HEAD inside the git directory
    "fetch-pack",  # writes objects and prints refs
    "fmt-merge-msg",  # reads standard input or a named file and prints
    "for-each-ref",  # reads refs and prints
    "format-patch",  # writes only the file the output rule computes (GIT_GENERATED_WRITERS)
    "fsck",  # reads the object store (--lost-found writes inside the git directory)
    "fsck-objects",  # the fsck alias
    "gc",  # repacks and prunes inside the git directory
    "get-tar-commit-id",  # reads standard input and prints
    "grep",  # reads and prints; its pager option is the command-naming rule's
    "hash-object",  # reads the named file, writes objects (-w)
    "help",  # prints; its viewer options are the help rule's
    "imap-send",  # sends standard input to an IMAP folder
    "index-pack",  # writes only the file the output rule computes (GIT_GENERATED_WRITERS)
    "last-modified",  # reads history and prints
    "log",  # prints; its --output file is the output rule's
    "ls-files",  # reads the index and prints
    "ls-remote",  # reads remote refs and prints
    "ls-tree",  # reads a tree and prints
    "mailsplit",  # writes only the file the output rule computes (GIT_GENERATED_WRITERS)
    "merge-base",  # reads history and prints a commit
    "merge-tree",  # prints, or writes tree objects (--write-tree); never the index or work tree
    "mktag",  # writes one tag object
    "mktree",  # writes one tree object
    "multi-pack-index",  # writes the object directory; --object-dir is the output rule's
    "name-rev",  # reads refs and prints
    "notes",  # writes notes refs and objects (notes merge works inside the git directory)
    "pack-objects",  # writes only the file the output rule computes (GIT_GENERATED_WRITERS)
    "pack-redundant",  # reads packs and prints
    "pack-refs",  # writes packed-refs
    "patch-id",  # reads standard input and prints
    "pickaxe",  # the blame alias (git 2.53 git pickaxe -h prints blame usage)
    "prune",  # removes unreachable objects
    "prune-packed",  # removes loose objects already packed
    "push",  # updates remote refs; a receiving repository's push-to-checkout is its configuration
    "range-diff",  # prints; its --output file is the output rule's
    "reflog",  # reads or expires reflogs inside the git directory
    "refs",  # migrates or verifies the ref store
    "remote",  # writes configuration and refs (remote update fetches)
    "repack",  # writes packs; --expire-to and --filter-to are the output rule's
    "replace",  # writes replace refs and objects
    "replay",  # writes objects and prints or updates refs; documented not to touch the work tree
    "repo",  # reads repository information and prints
    "request-pull",  # reads history and prints
    "rev-list",  # reads history and prints
    "rev-parse",  # reads names and prints
    "send-pack",  # the push transport: updates remote refs
    "shortlog",  # reads history and prints
    "show",  # prints; its --output file is the output rule's
    "show-branch",  # reads refs and prints
    "show-index",  # reads a pack index on standard input and prints
    "show-ref",  # reads refs and prints
    "stage",  # the add alias: writes the index and objects
    "status",  # reads the work tree and prints (refreshes index stat data only)
    "stripspace",  # filters standard input to standard output
    "symbolic-ref",  # writes a symbolic ref (HEAD moves; the work tree is untouched)
    "tag",  # writes tag refs and objects
    "unpack-file",  # writes only the file the output rule computes (GIT_GENERATED_WRITERS)
    "unpack-objects",  # writes loose objects from a pack on standard input
    "update-ref",  # writes one ref
    "update-server-info",  # writes info/refs and objects/info/packs inside the git directory
    "upload-archive",  # serves an archive on standard output
    "upload-pack",  # serves objects on standard output
    "var",  # prints a git variable
    "verify-commit",  # checks a signature and prints
    "verify-pack",  # reads a pack and prints
    "verify-tag",  # checks a signature and prints
    "version",  # prints the version
    "whatchanged",  # prints; its --output file is the output rule's
    "write-tree"))  # writes tree objects from the index
# The git subcommands that rewrite the working tree, or may and are not proved not to (round 21):
# every public git 2.53 command outside GIT_NO_WORKTREE_WRITE. Besides the names earlier rounds
# listed (checkout, restore, reset, clean, stash, switch, merge, pull, rebase, cherry-pick, revert,
# am, apply, rm, mv, read-tree, checkout-index, worktree, sparse-checkout, bisect, submodule,
# update-index, merge-recursive, merge-resolve, merge-octopus, merge-subtree, merge-index,
# merge-one-file, filter-branch, rerere, quiltimport and subtree) this now holds merge-ours,
# merge-recursive-ours, merge-recursive-theirs and merge-file; the writers of a file an operand
# names that no output rule computes (init, init-db, interpret-trailers --in-place, mailinfo,
# credential-store) and fast-import (an input stream may name a marks file); the commands that
# run a configured or named program (config, credential, credential-cache, daemon, difftool,
# for-each-repo, hook, instaweb, maintenance, mergetool,
# remote-ext); and the server and transport helpers not proved here (http-backend, http-fetch,
# http-push, receive-pack, remote-fd, remote-ftp, remote-ftps, remote-http, remote-https, shell).
GIT_WORKTREE_SUBCOMMANDS = GIT_PUBLIC_SUBCOMMANDS - GIT_NO_WORKTREE_WRITE
# The git global options that take their value as the NEXT word (git 2.53 git.c), skipped when the
# not-plain read looks for the subcommand word after a git word (_git_worktree_words).
GIT_SEPARATE_VALUE_GLOBALS = frozenset(("-C", "-c", "--git-dir", "--work-tree", "--namespace",
                                        "--super-prefix", "--attr-source"))
# The valued options of git grep (round 19, read from the git 2.53 git grep -h output): a short
# letter here takes the rest of its cluster or, when it ends the cluster, the next word as its
# value (git grep -eFOO and git grep -e -O name a pattern, never an option), and a long option
# here spelled exactly, with no =, takes the next word. A lone -- ends grep's options.
GIT_GREP_VALUED_SHORT = "efABCm"
GIT_GREP_VALUED_LONG = frozenset(("--max-depth", "--context", "--before-context",
                                  "--after-context", "--threads", "--max-count"))
# The git help forms that only print (round 15, PD-427-GIT-SCOPE): git help whose every option word
# is exactly one of these (a lone -- ends the options; every other word is a page or command name)
# lists commands, guides, interfaces or configuration names, or shows a named page in the
# configured format, and is judged by the exact path check like any other git subcommand. Any other
# option word (-w or --web and -i or --info, which launch a viewer, -m or --man, a --no- viewer
# negation, an abbreviation, a short-option cluster) runs a viewer program or is not proved not to,
# so the command is not plain. The viewer a named page launches by default (man and its pager, or
# the help.format, man.viewer and help.browser configuration) is the disclosed configuration
# residual.
GIT_HELP_PRINT_OPTIONS = frozenset((
    "-a", "--all", "-g", "--guides", "-c", "--config", "-v", "--verbose", "--no-verbose",
    "--external-commands", "--no-external-commands", "--aliases", "--no-aliases",
    "--user-interfaces", "--developer-interfaces"))
# The command-naming git options, scoped by subcommand (round 18; each scope read from the git 2.53
# -h output of every builtin and run against a scratch helper script): the option names a program
# git runs only under the subcommands listed, so -O on diff, diff-files, diff-index, diff-tree,
# diff-pairs, log, show and range-diff (an ordering file git reads) is not one. --exec runs a
# program for archive (with --remote), push, send-pack and rebase, and for ls-remote and fetch-pack
# (hidden in -h, run by git 2.53); --upload-pack for clone, fetch, fetch-pack, ls-remote and pull;
# --receive-pack for push and send-pack; --extcmd and --tool for difftool and mergetool (also
# GIT_CODE_SUBCOMMANDS); --open-files-in-pager and its short form -O only for grep. A word matches
# when it begins with the option (a glued =value or -O value included) or, for a long option, when
# its name before any = is an abbreviation git accepts (git fetch --upload-p=cmd and git grep
# --open=cmd run cmd); a grep short-option cluster carrying O (git grep -iOcmd) matches too. Round
# 19: only grep's OPTION words are read (_git_option_words): the value of a valued grep option
# (GIT_GREP_VALUED_SHORT, GIT_GREP_VALUED_LONG) and every word after a lone -- are not options,
# so git grep -eFOO and git grep -- -O allow while git grep -iO and -iOcmd deny. An alias is
# outside GIT_PUBLIC_SUBCOMMANDS and so never plain.
GIT_CODE_OPTIONS = dict((
    ("--exec", frozenset(("archive", "push", "send-pack", "rebase", "ls-remote", "fetch-pack"))),
    ("--upload-pack", frozenset(("clone", "fetch", "fetch-pack", "ls-remote", "pull"))),
    ("--receive-pack", frozenset(("push", "send-pack"))),
    ("--extcmd", frozenset(("difftool",))),
    ("--tool", frozenset(("difftool", "mergetool"))),
    ("--open-files-in-pager", frozenset(("grep",))),
    ("-O", frozenset(("grep",)))))
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
# DIRECTORY operand acts on everything inside it, so a plain command led by one of these denies when
# a resolved operand is a directory that CONTAINS a protected path: the store tree of a bound root, a
# frozen file, a declared view, the registration or the pack own tree (_container_rule). Round 11:
# only the command word is read, matched exactly on its basename (an allowlisted command word is
# never a wrapper, so no argument word is a program it runs); every other remover or mover
# (unlink, shred, chown, util-linux or Perl rename) is off the rule-4 allowlist and NOT plain.
CONTAINER_VERBS = frozenset(("rm", "rmdir", "mv", "chmod"))
# The plain programs that write INTO a directory operand under another operand's basename (cp X docs,
# cp -t docs X, mv X /abs/docs, ln X docs): only their directory-plus-basename joins are judged
# (_joined_targets); a read-only program naming a directory beside a file (ls docs .) joins nothing.
JOIN_WRITERS = frozenset(("cp", "mv", "ln"))
# The cp option that writes each source's WHOLE spelling under the destination directory (round 16:
# cp --parents /abs/src.txt docs writes docs/abs/src.txt), matched with any abbreviation of at least
# one letter (_parents_option), so every argument spelling is joined whole (_parents_targets).
PARENTS_OPTION = "--parents"
# The git subcommands that write a file whose name they CONSTRUCT or an option names (round 16; each
# checked against its git 2.53 -h output): format-patch (NNNN-subject.patch, the cover letter),
# bugreport and diagnose (a suffixed report name) write into the cwd unless an output directory is
# named; unpack-file (round 18) writes a random .merge_file_XXXXXX into the work-tree top (the cwd
# or above it) and takes no output option; pack-objects (<base-name>-<hash>.pack, .idx, .rev)
# and index-pack (<pack>.idx, .rev, .keep) write beside a named base or pack; bundle create writes
# its first operand; clone creates a
# directory named after the repository, or its operand; mailsplit writes numbered files into its -o
# directory. Round 17: these GIT_GENERATED_WRITERS deny in a bound product whatever their options
# (no --stdout or output-location exemption: git format-patch -1 HEAD --stdout --no-stdout and
# --subject-prefix --stdout write the patch file, so membership of --stdout proves nothing), and
# only the exact three words git <subcommand> -h (git's own usage-only form) are exempt. The
# valued options of GIT_OUTPUT_OPTIONS (any abbreviation) and, for the GIT_SHORT_O_OUTPUT
# subcommands, -o name an output file or directory: --output on diff, diff-files,
# diff-index, diff-tree, diff-pairs, log, show, whatchanged, range-diff and format-patch, -o or
# --output on archive, --output-directory on format-patch, bugreport and diagnose, --export-marks on
# fast-export and fast-import, --index-output on read-tree, --expire-to and --filter-to on gc and
# repack, --object-dir on commit-graph and multi-pack-index. Every other git 2.53 builtin was read
# from its -h output: an operand or option it writes is spelled whole (interpret-trailers
# --in-place, merge-file, credential-store --file, init) and takes the exact path check, or it
# writes only inside the repository's own git directory (fetch, hash-object -w, notes, fsck,
# update-server-info, maintenance), or it reads the file it names (-F, --file, -O, --contents,
# --pathspec-from-file, --import-marks, --ignore-revs-file).
GIT_CWD_WRITERS = frozenset(("format-patch", "bugreport", "diagnose", "unpack-file"))
GIT_BASE_WRITERS = frozenset(("pack-objects", "index-pack"))
GIT_SHORT_O_OUTPUT = frozenset(("archive", "bugreport", "diagnose", "format-patch", "index-pack",
                                "mailsplit"))
GIT_OUTPUT_OPTIONS = ("--output", "--output-directory", "--export-marks", "--index-output",
                      "--expire-to", "--filter-to", "--object-dir")
# Round 22 (QA round 19): an output option is a write in EVERY git subcommand, not only in
# GIT_OUTPUT_WRITERS. --output is one of the diff options that every history and diff reader
# inherits (git blame --output=docs/STATUS.md free and the annotate and pickaxe forms truncated a
# declared view; -h lists no inherited option), so a word that is a GIT_OUTPUT_OPTIONS name or an
# abbreviation of one (--o, --outp=x) is judged as an output option whatever the subcommand
# (_git_output_option). Outside GIT_OUTPUT_WRITERS an abbreviation that is also a prefix of a
# GIT_OUTPUT_SHADOWS name is not: those are exact git 2.53 options of other builtins (git grep
# --index, git fetch --filter, git reflog expire --expire), which git matches exactly before
# any abbreviation. Every git 2.53 builtin -h was scanned for an option naming a file or
# directory to write: the only such options are the GIT_OUTPUT_OPTIONS names, the short -o of
# GIT_SHORT_O_OUTPUT and the operands GIT_GENERATED_WRITERS construct; the inherited diff,
# revision and pretty option sets add --output alone (--output-indicator-* take a character).
# An output value is resolved against the session cwd, every directory the command names AND the
# repository top above each (git resolves --output from the top), after lexical normalization.
# An abbreviation that git resolves to a different option of the subcommand (git branch --o is
# --omit-empty) is still judged an output option: the disclosed over-refusal under RESIDUALS.
GIT_OUTPUT_SHADOWS = ("--index", "--filter", "--expire")
GIT_GENERATED_WRITERS = GIT_CWD_WRITERS | GIT_BASE_WRITERS | frozenset(("bundle", "clone",
                                                                       "mailsplit"))
GIT_OUTPUT_WRITERS = GIT_GENERATED_WRITERS | GIT_SHORT_O_OUTPUT | frozenset((
    "diff", "diff-files", "diff-index", "diff-tree", "diff-pairs", "log", "show", "whatchanged",
    "range-diff", "fast-export", "fast-import", "read-tree", "gc", "repack", "commit-graph",
    "multi-pack-index"))
# The option words git bundle create accepts before its file operand (git 2.53 -h; --version takes
# its value glued); any other option word there leaves the file operand uncomputed (deny).
GIT_BUNDLE_CREATE_FLAGS = frozenset(("-q", "--quiet", "--no-quiet", "--progress", "--no-progress",
                                     "--all-progress", "--all-progress-implied"))
# The backup-making writers (round 14): a backup option of cp, mv or ln (install takes the same
# options but is off the rule-4 allowlist and takes the coarse rule) renames an EXISTING destination
# to that destination plus a suffix before writing, so cp --backup=simple --suffix=.md notes.txt
# docs/STATUS overwrites docs/STATUS.md, a path no word spells. Such a command denies in a bound
# product, whatever the suffix (_backup_option): every backup option counts, -b, -S, --backup with or
# without a value, --suffix with or without a value, an abbreviation of either long option, and a
# short-option cluster carrying b or S anywhere (a glued value included, fail closed).
BACKUP_WRITERS = frozenset(("cp", "mv", "ln", "install"))
BACKUP_LONG_OPTIONS = ("--backup", "--suffix")
BACKUP_SHORT_LETTERS = frozenset(("b", "S"))
# The payload-key spelling of a path-like file-tool field (round 11): a write-capable file tool whose
# tool_input carries such a key anywhere other than its one evaluated target field is denied, since
# this hook judges only that field.
PATHLIKE_KEY_RE = re.compile(r"path|file|dir|target|dest|notebook|uri|url", re.IGNORECASE)

SANCTIONED = ("OPF content changes only through the sanctioned writer: run the opf CLI (opf record, "
              "opf render, and the other opf verbs), or make the change outside the store's scope")


class Unreadable(Exception):
    """An envelope-level payload this hook cannot read at all (mapped to exit 2, a blocking error)."""


def _stderr_note(line):
    """Best-effort stderr diagnostic: write the line and flush, swallowing every failure (a closed
    descriptor 2 leaves sys.stderr None; a full device or a broken pipe raises OSError), so the EXIT
    CODE, never this line, carries the decision. Before this helper, a failing stderr write on a
    fail-closed path ended the run with the interpreter's own status (1 or 120); on PreToolUse any
    status other than 2 is non-blocking, so the call this hook meant to block went ahead."""
    try:
        sys.stderr.write(line)
        sys.stderr.flush()
    except BaseException:
        pass


def _exit_now(code):
    """The one exit door: flush both std streams best-effort, then leave through os._exit, which skips
    the interpreter-exit flush of the std streams (a stream that buffered a failed write raises again in
    that flush and the interpreter exits 120, a status the platform treats as non-blocking, which would
    turn a blocking exit 2 into an allow). Everything this hook writes is flushed at its write site, so
    os._exit discards nothing."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except BaseException:
            pass
    os._exit(code)


def _emit_deny(reason):
    """Write the structured deny decision to stdout and flush it. A decision that cannot be both
    written and flushed never provably reached the platform, and exit 0 without it reads as a silent
    allow, so that failure returns the blocking exit 2 instead (a lost deny blocks, never allows)."""
    decision = dict(hookSpecificOutput=dict(hookEventName="PreToolUse",
                                            permissionDecision="deny",
                                            permissionDecisionReason="opf-pretooluse-deny: " + reason))
    try:
        sys.stdout.write(json.dumps(decision) + "\n")
        sys.stdout.flush()
    except BaseException as exc:
        _stderr_note("opf-pretooluse-deny: the deny decision could not be written to standard output "
                     "(%r). Failing closed: the tool call is blocked.\n" % (exc,))
        return 2
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


def _pathlike_keys(tool_input, field):
    """Every key of a file-tool payload, at any depth (inside the MultiEdit edits array included),
    that spells a path-like field (PATHLIKE_KEY_RE) other than the top-level evaluated target
    `field`; past MAX_PAYLOAD_STRINGS entries the overflow itself is reported (fail closed)."""
    out, stack, seen = [], [(tool_input, 0)], 0
    while stack:
        value, depth = stack.pop()
        seen += 1
        if seen > MAX_PAYLOAD_STRINGS:
            return out + ["(more than %d payload entries)" % (MAX_PAYLOAD_STRINGS,)]
        if isinstance(value, dict):
            for key, sub in value.items():
                if isinstance(key, str) and not (depth == 0 and key == field) \
                        and PATHLIKE_KEY_RE.search(key):
                    out.append(key)
                stack.append((sub, depth + 1))
        elif isinstance(value, list):
            stack.extend((sub, depth + 1) for sub in value)
    return out


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
    stray = _pathlike_keys(tool_input, field)
    if stray:
        return ("the %s payload carries the path-like field %r besides its evaluated target field "
                "%s, and this hook judges only that field, so it is denied fail-closed (R6)"
                % (tool_name, stray[0], field))
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


def _plain_command_word(word):
    """Rule 4's allowlist over one command word: True when its basename is on PLAIN_ALLOWED_COMMANDS
    (compared exactly), spelled bare or as an absolute path whose directory is exactly one of
    PLAIN_COMMAND_DIRS."""
    if os.sep not in word:
        return word in PLAIN_ALLOWED_COMMANDS
    head, base = os.path.split(word)
    return head in PLAIN_COMMAND_DIRS and base in PLAIN_ALLOWED_COMMANDS


def _plain_words(command):
    """Rules 1 to 3 of the provably plain specification and rule 4's word split, decided on the raw
    string before any lexing: the dequoted words when every character is printable ASCII, no
    PLAIN_FORBIDDEN character appears outside a single-quoted span (a double-quoted span carries none
    either, a single-quoted span no PLAIN_SQ_FORBIDDEN character), every quote is terminated, words
    are separated by spaces only and the command word is a bare unquoted PLAIN_COMMAND_RE name or
    path; else None. The allowlist is NOT applied here: the
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
            if ch == chr(39) and any(c in PLAIN_SQ_FORBIDDEN for c in span):
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
    if not words or not _plain_command_word(words[0]):
        return None
    return words


def _names_runner(word):
    """True when `word`, as a basename with at most a version suffix, is an INLINE_RUNNERS name."""
    spelled = _RUNNER_WORD_RE.match(os.path.basename(word).lower())
    return bool(spelled) and spelled.group(1) in INLINE_RUNNERS


def _plain_runs_code(words):
    """The plain semantic check (R5): a phrase naming why a provably plain command still runs a
    command or code it names on its own command line, or None. Such a command takes the coarse rule.
    Only a PLAIN_CODE_RUNNERS command word (git, opf) can run another program (round 11: every other
    allowlisted program cannot, so its argument words are data). For those, an argument word that IS
    an INLINE_RUNNERS name, and a word carrying a command string headed by such a name or by a
    leading exclamation mark (git alias.x=!cmd), are inline code; so are git's configuration
    overrides, its code-running subcommands, its command-naming options and a global option outside
    the allowlisted git grammar (_git_grammar)."""
    if os.path.basename(words[0]) not in PLAIN_CODE_RUNNERS:
        return None
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
    if os.path.basename(words[0]) == "git":
        return _git_runs_code(words)
    return None


def _git_runs_code(words):
    """The git part of the plain semantic check over a git invocation `words` (words[0] is the git
    command word): a phrase, or None."""
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
    if sub == "help":
        option = _git_help_viewer(rest)
        if option is not None:
            return "git help %r may launch a manual viewer" % (option,)
    if sub in GIT_CODE_SUBCOMMANDS and not _git_submodule_read(sub, rest):
        return "the git subcommand %r runs or configures a command" % (sub,)
    if _git_unlisted(sub):
        return ("the git subcommand %r is an internal helper or is not a public git 2.53 command, "
                "so it may run a command" % (sub,))
    for word in _git_option_words(sub, rest):
        if _git_code_option(sub, word):
            return "the git option %r names a command to run" % (word,)
        short = word.startswith("-") and not word.startswith("--")
        if sub == "rebase" and short and "x" in word[1:]:
            return "git rebase -x runs a command"
        if sub == "clone" and short and "u" in word[1:]:
            return "git clone -u runs a command"
    return None


def _git_unlisted(sub):
    """True when the git subcommand word `sub` is an internal helper (a name carrying "--") or is
    outside GIT_PUBLIC_SUBCOMMANDS (round 19): git may run code for it that the hook cannot read."""
    return "--" in sub or sub not in GIT_PUBLIC_SUBCOMMANDS


def _git_option_words(sub, rest):
    """The option words among `rest`, the words after git subcommand `sub`, that the command-naming
    scan reads (round 19). For grep: words up to a lone --, without the value of a valued option
    (GIT_GREP_VALUED_SHORT, GIT_GREP_VALUED_LONG) and without operands; a short cluster is cut
    before its valued letter, whose rest is the value (-ieO keeps -i), and after an O, whose rest
    is the pager. Every other subcommand: every word (fail closed)."""
    if sub != "grep":
        return list(rest)
    found, skip = [], False
    for word in rest:
        if skip:
            skip = False
            continue
        if word == "--":
            break
        if not word.startswith("-") or word == "-":
            continue
        if word.startswith("--"):
            found.append(word)
            skip = word in GIT_GREP_VALUED_LONG
            continue
        for at in range(1, len(word)):
            if word[at] == "O":
                found.append(word[:at + 1])
                break
            if word[at] in GIT_GREP_VALUED_SHORT:
                if at > 1:
                    found.append(word[:at])
                skip = at == len(word) - 1
                break
        else:
            found.append(word)
    return found


def _git_code_option(sub, word):
    """True when the argument word `word` of git subcommand `sub` is a command-naming option in
    that subcommand's scope (GIT_CODE_OPTIONS, round 18): it begins with the option, it is an
    abbreviation of a long option (its name before any = is at least one letter past the dashes),
    or it is a short-option cluster carrying the letter of a short option."""
    name = word.partition("=")[0]
    for option, scope in GIT_CODE_OPTIONS.items():
        if sub not in scope:
            continue
        if word.startswith(option):
            return True
        if option.startswith("--"):
            if len(name) > 2 and option.startswith(name):
                return True
        elif word.startswith("-") and not word.startswith("--") and option[1:] in word[1:]:
            return True
    return False


def _git_help_viewer(rest):
    """The first option word of git help (round 15) outside GIT_HELP_PRINT_OPTIONS, before a lone
    --, or None when every option word is a listed print form: -w, --web, -i, --info and every
    unlisted option (fail closed) may launch a viewer."""
    for word in rest:
        if word == "--":
            return None
        if word.startswith("-") and word not in GIT_HELP_PRINT_OPTIONS:
            return word
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


def _parents_option(words):
    """The first --parents option word among `words` (round 16), bare or abbreviated to any prefix
    of at least one letter, or None. A lone -- does not end the scan (a file operand spelled like
    the option only adds joins)."""
    for word in words:
        name = word.split("=", 1)[0]
        if name.startswith("--") and len(name) > 2 and PARENTS_OPTION.startswith(name):
            return word
    return None


def _parents_targets(cands, spellings):
    """The targets cp --parents writes (round 16): every resolved candidate that is a directory
    receives every argument spelling WHOLE (its leading slashes dropped, never normalized first, so
    a dot-dot inside it climbs from the directory as the kernel resolves it) as one more resolved
    target (lexical and realpath): (targets, None), or (None, reason) past MAX_JUDGED_TARGETS."""
    tails = []
    for spelling in spellings:
        tail = spelling.lstrip(os.sep) if spelling else ""
        if tail and tail not in tails:
            tails.append(tail)
    out, seen = [], set()
    for directory in cands:
        if not tails or not os.path.isdir(directory):
            continue
        for tail in tails:
            for cand in _candidates(os.path.join(directory, tail), None, "literal") or ():
                if cand in seen:
                    continue
                seen.add(cand)
                if len(seen) > MAX_JUDGED_TARGETS:
                    return None, ("the command reaches more than %d candidate targets under the "
                                  "directories it names (--parents), over the resolution budget, "
                                  "so it cannot be fully examined; failing closed (R6)"
                                  % (MAX_JUDGED_TARGETS,))
                out.append(cand)
    return out, None


def _git_grammar(words):
    """The allowlisted git global-option grammar (round 10) over a git invocation `words` (words[0]
    is git): (index of the subcommand word, None), (None, None) when no subcommand follows the
    global options, or (None, reason) when a global option falls outside the grammar or a value
    global carries no value, which makes the command NOT plain. The grammar is deliberately small:
    it never tries to model every git option, so an unrecognized spelling is refused, never
    guessed."""
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
    return i, None


def _backup_option(words):
    """The first backup option word among `words` (the words after a BACKUP_WRITERS command word),
    or None (round 14): -b or -S in any short-option cluster, and --backup or --suffix, bare,
    valued or abbreviated to any unique-or-not prefix of at least one letter. A lone -- does NOT
    end the scan (a valued option may take it as its value, cp -t -- --backup x, so the words
    after it can still be options): a file operand spelled like a backup option only over-refuses."""
    for word in words:
        if word.startswith("--"):
            name = word.split("=", 1)[0]
            if len(name) > 2 and any(full.startswith(name) for full in BACKUP_LONG_OPTIONS):
                return word
        elif word.startswith("-") and any(ch in BACKUP_SHORT_LETTERS for ch in word[1:]):
            return word
    return None


def _backup_reason(words, root):
    """The round-14 backup deny reason when a BACKUP_WRITERS word among `words` is followed by a
    backup option (_backup_option) and the command acts in the bound product root `root`, else
    None. The exact path check calls it only when the plain command word is such a writer (an
    allowlisted program never runs another); the coarse rule passes every literal word."""
    if root is None:
        return None
    for k, word in enumerate(words):
        if os.path.basename(word) not in BACKUP_WRITERS:
            continue
        option = _backup_option(words[k + 1:])
        if option is not None:
            return ("%s carries the backup option %r, which renames an existing destination to "
                    "that destination plus a backup suffix (cp --backup=simple --suffix=.md notes.txt "
                    "docs/STATUS overwrites docs/STATUS.md), a target no word of the command spells, "
                    "and this command acts in the bound OPF product root %r, so it is denied "
                    "fail-closed (R5). %s." % (os.path.basename(word), option, root, SANCTIONED))
    return None


def _git_output_spots(sub, rest, cwd, cands):
    """Where git `sub` with the words `rest` after it writes a file whose name it constructs or an
    option names (round 16, GIT_OUTPUT_WRITERS): (spots, named, None), each spot an absolute path
    at or under which such a file lands and `named` true when an output option word appears, or
    (None, True, reason) when the location cannot be computed. A relative
    location is resolved against the cwd and every directory the command names (an
    over-approximation: git -C and --work-tree move it), and a cwd-defaulted location is every one
    of those directories. Round 17: no --stdout or bundle - form is exempt, and a lone -- does NOT
    end the scan (a valued option may take it as its value: git fast-export --refspec --
    --export-marks=mk HEAD writes mk), so a word after it spelled like an output option only
    over-refuses. Round 22: every subcommand is scanned for output option words
    (_git_output_option), and every base also contributes the repository top above it
    (_git_top), where git resolves an output option's value; a value that resolves against no
    base cannot be computed (deny)."""
    bases = []
    for base in [cwd] + [c for c in cands if os.path.isdir(c)]:
        top = _git_top(base) if isinstance(base, str) and os.path.isabs(base) else None
        for spot in (base, top):
            if spot is not None and spot not in bases:
                bases.append(spot)
    values, operands, k = [], [], 0
    while k < len(rest):
        word = rest[k]
        name, eq, glued = word.partition("=")
        long_output = _git_output_option(name, sub in GIT_OUTPUT_WRITERS)
        short_output = (sub in GIT_SHORT_O_OUTPUT and word.startswith("-")
                        and not word.startswith("--") and "o" in word[1:])
        if long_output or short_output:
            tail = glued if long_output else word[word.index("o", 1) + 1:]
            if (eq if long_output else tail):
                values.append(tail)
            elif k + 1 < len(rest):
                k += 1
                values.append(rest[k])
            else:
                return None, True, "its option %r carries no value" % (word,)
        elif not word.startswith("-") or word == "-":
            operands.append(word)
        k += 1
    files, spots = list(values), []
    if sub in GIT_CWD_WRITERS and not values:
        spots.extend(bases)
    if sub in GIT_BASE_WRITERS:
        for word in operands:
            for base in bases:
                spots.extend(os.path.dirname(c) for c in _candidates(word, base, "literal") or ())
    if sub == "bundle" and rest and rest[0] == "create":
        for word in rest[1:]:
            if word in GIT_BUNDLE_CREATE_FLAGS or word.startswith("--version="):
                continue
            if word.startswith("-") and word != "-":
                return None, True, ("its create option %r is outside the recognized grammar, so "
                                    "the file operand cannot be computed" % (word,))
            files.append(word)
            break
    if sub == "clone":
        spots.extend(bases)
        spots.extend(cands)
    for value in files:
        got = []
        for base in bases:
            got.extend(_candidates(value, base, "literal") or ())
        if not got:
            return None, bool(values), ("its output location %r resolves against no directory"
                                        % (value,))
        spots.extend(got)
    return spots, bool(values), None


def _git_output_option(name, scoped):
    """True when the option name `name` (a word before any =) is a git output option (round 22):
    a GIT_OUTPUT_OPTIONS name or an abbreviation of one of at least one letter. For a subcommand
    outside GIT_OUTPUT_WRITERS (`scoped` false) an abbreviation that is also a prefix of a
    GIT_OUTPUT_SHADOWS name is not (git grep --index matches the exact --index first)."""
    if not name.startswith("--") or len(name) <= 2:
        return False
    if not any(full.startswith(name) for full in GIT_OUTPUT_OPTIONS):
        return False
    return scoped or not any(shadow.startswith(name) for shadow in GIT_OUTPUT_SHADOWS)


def _git_spots_reason(sub, spots):
    """The deny reason for the first of `spots` (absolute output locations of git `sub`) at or
    under a product root (_roots_above) or inside the pack own tree (R8), or None."""
    seen = set()
    for spot in spots:
        if spot in seen:
            continue
        seen.add(spot)
        got, reason = _roots_above(spot)
        if reason is not None:
            return reason + "; failing closed (R6)"
        where = got[0] if got else None
        if where is None and _guard_rule(spot) is not None:
            where = "the enforcement pack own tree"
        if where is not None:
            return ("git %s writes a file whose name it constructs or an option names (a "
                    "generated patch, report or pack name, or an output option's value) at %r, "
                    "inside %r, so it is denied whatever name it constructs (R5, R8); run it "
                    "from outside every product root and the pack own tree. %s."
                    % (sub, spot, where, SANCTIONED))
    return None


def _git_output_reason(words, cwd, cands, roots):
    """The round-16 deny reason for a plain git command whose subcommand writes a file whose name
    it constructs or an option names (_git_output_spots), or None: such a file landing at or under
    a product root (judged with _roots_above, so a root is found whether or not it is already
    bound) or inside the pack own tree (R8) denies, as does a location that cannot be computed.
    Round 17 removes every standard-output and output-location exemption in a bound product (the
    bound roots `roots`): a GIT_GENERATED_WRITERS subcommand denies whatever its options, and any
    other GIT_OUTPUT_WRITERS subcommand denies when it carries an output option word at all,
    wherever its value points. Only the exact words git <subcommand> -h (usage only; git itself
    treats just that two-argument form as a help request) are exempt. Round 22: EVERY subcommand
    is scanned for output option words (git blame --output=x writes x), so in a bound product any
    git subcommand carrying one denies, and elsewhere its value is judged from the cwd, every
    directory the command names and the repository top above each."""
    if os.path.basename(words[0]) != "git":
        return None
    i, reason = _git_grammar(words)
    if reason is not None or i is None:
        return None
    sub = words[i]
    if len(words) == 3 and words[0] == "git" and i == 1 and words[2] == "-h":
        return None
    spots, named, reason = _git_output_spots(sub, words[i + 1:], cwd, cands)
    if roots and (sub in GIT_GENERATED_WRITERS or named):
        return ("git %s writes a file whose name it constructs or an option names (a generated "
                "patch, report, pack, bundle or clone, or an output option's value), and this "
                "command acts in the bound OPF product root %r, so it is denied whatever its "
                "options: no --stdout, output-location or other option is parsed into an "
                "exemption (a later option can negate it or consume it as a value) (R5, R8); run "
                "it from outside every product root, redirecting its standard output there. %s."
                % (sub, roots[0], SANCTIONED))
    if reason is not None:
        return ("git %s writes a file whose name it constructs or an option names, and %s; it is "
                "denied fail-closed (R5, R6). %s." % (sub, reason, SANCTIONED))
    return _git_spots_reason(sub, spots)


def _git_output_words_reason(words, cwd, cands):
    """The round-22 output check for a NOT plain command outside every product root, or None:
    over the literal words after the first git word, every output option word (_git_output_option,
    scoped when a GIT_OUTPUT_WRITERS name appears there) is judged where git may write its value
    (_git_output_spots: the cwd, every directory the command names and the repository top above
    each); a value landing in a product root or the pack own tree, or resolving nowhere, denies."""
    heads = [k for k, word in enumerate(words) if os.path.basename(word) == "git"]
    if not heads:
        return None
    rest = words[heads[0] + 1:]
    sub = next((word for word in rest if word in GIT_OUTPUT_WRITERS), "git")
    spots, _named, reason = _git_output_spots(sub, rest, cwd, cands)
    if reason is not None:
        return ("git %s writes a file whose name it constructs or an option names, and %s; it is "
                "denied fail-closed (R5, R6). %s." % (sub, reason, SANCTIONED))
    return _git_spots_reason(sub, spots)


def _git_submodule_read(sub, rest):
    """True when git `sub` with the words `rest` after it is a read form of git submodule (round
    13; round 14 shares it with the plain semantic check, so a read form is plain): the first word
    after any -q or --quiet is a GIT_SUBMODULE_READ name."""
    if sub != "submodule":
        return False
    while rest and rest[0] in ("-q", "--quiet"):
        rest = rest[1:]
    return bool(rest) and rest[0] in GIT_SUBMODULE_READ


def _git_dry_run(sub, rest):
    """True when git `sub` (rm, mv or clean) with the words `rest` after it is a dry run by the
    GIT_DRY_RUN_GRAMMAR: every option word before a lone -- is a listed short-letter run or an
    exact listed long option, and one of them is -n or --dry-run. Any other option word (a valued
    or unlisted letter, an abbreviation, a --no- negation) is not a dry run (fail closed)."""
    grammar = GIT_DRY_RUN_GRAMMAR.get(sub)
    if grammar is None:
        return False
    flags, longs = grammar
    dry = False
    for word in rest:
        if word == "--":
            break
        if not word.startswith("-") or word == "-":
            continue
        if word.startswith("--"):
            if word not in longs:
                return False
            dry = dry or word == "--dry-run"
            continue
        for letter in word[1:]:
            if letter not in flags:
                return False
        dry = dry or "n" in word[1:]
    return dry


def _git_worktree_sub(words):
    """The work-tree or index rewriting subcommand of a plain git command (round 11), or None:
    words[0] is git, the subcommand found by the global grammar is a GIT_WORKTREE_SUBCOMMANDS
    name, and the command is not a dry run (_git_dry_run)."""
    if os.path.basename(words[0]) != "git":
        return None
    i, reason = _git_grammar(words)
    if reason is not None or i is None:
        return None
    sub, rest = words[i], words[i + 1:]
    if sub not in GIT_WORKTREE_SUBCOMMANDS or _git_dry_run(sub, rest):
        return None
    if _git_submodule_read(sub, rest):
        return None
    return sub


def _git_worktree_words(words):
    """The work-tree or index rewriting git subcommands a NOT plain command may run (round 14),
    read over its literal words (_literal_words) without trusting any segmentation or any global
    option grammar: after the first word whose basename is git, EVERY later word that is a
    GIT_WORKTREE_SUBCOMMANDS name (git -c core.abbrev=7 checkout -- ., git status; git checkout
    -- ., true; git reset --hard), and every dashed builtin word whose basename is git-<name> for
    such a name (/usr/lib/git-core/git-checkout). A later word that is such a name only as data
    (git log; echo reset) counts too: the over-refusal is disclosed. Round 15: NO dry-run or
    read-form exemption applies here. The literal words carry no command boundary, so a later
    command's words would be read as the subcommand's own arguments (git rm -rf .; echo -n read as
    a dry run of git rm); the dry-run grammar (_git_dry_run) and the submodule read forms
    (_git_submodule_read) exempt only a plain single git invocation (_git_worktree_sub), whose own
    argument list the plain classifier proved. Round 19: an internal helper word (a name carrying
    "--", bare or as a git-<name> basename) counts wherever it stands after a git word, and so does
    the word in subcommand position after a git word (past its option words and the value of a
    GIT_SEPARATE_VALUE_GLOBALS option) when it is not a public git 2.53 command (_git_unlisted):
    git submodule--helper foreach and an alias run code the hook cannot read. A word after git
    that is data (echo git notes.txt) counts too: the over-refusal is disclosed."""
    found, seen_git, head, skip = [], False, False, False
    for word in words:
        base = os.path.basename(word)
        if base == "git":
            seen_git, head, skip = True, True, False
            continue
        at_head = False
        if skip:
            skip = False
        elif head and word.startswith("-"):
            skip = word in GIT_SEPARATE_VALUE_GLOBALS
        elif head:
            at_head, head = True, False
        if base.startswith("git-") and (base[4:] in GIT_WORKTREE_SUBCOMMANDS or "--" in base[4:]):
            sub = base[4:]
        elif seen_git and (word in GIT_WORKTREE_SUBCOMMANDS
                           or (not word.startswith("-") and "--" in word)
                           or (at_head and _git_unlisted(word))):
            sub = word
        else:
            continue
        if sub not in found:
            found.append(sub)
    return found


def _git_unbound_rule(cwd, cands, protected):
    """The unbound half of the git work-tree rule (round 11; round 13 shares it with the coarse
    rule): a deny reason when the session cwd, a directory among the resolved `cands`, or the
    repository top above either is a container of a `protected` path (the pack own tree, R8),
    or None."""
    bases = []
    for base in (_candidates(cwd, None, "literal") or []) + [c for c in cands if os.path.isdir(c)]:
        for spot in (base, _git_top(base)):
            if spot is not None and spot not in bases:
                bases.append(spot)
    for base in bases:
        reason = _container_rule(base, protected)
        if reason is not None:
            return reason
    return None


def _git_top(path):
    """The nearest directory at or above the absolute directory `path` holding a .git entry (the
    repository top git acts on from there), or None."""
    cur = path
    while True:
        if os.path.lexists(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


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
    words = _literal_words(command)
    if roots:
        backup = _backup_reason(words, roots[0])
        if backup is not None:
            return backup
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
    if _parents_option(words) is not None and any(
            os.path.basename(word) in JOIN_WRITERS for word in words):
        # Round 16: the --parents joins (_parents_targets) take the same product-root check.
        more, reason = _parents_targets(cands, words + derived)
        if reason is not None:
            return reason
        cands = cands + more
    for cand in cands:
        got, reason = _roots_above(cand)
        if reason is not None:
            return reason + "; failing closed (R6)"
        if got:
            backup = _backup_reason(words, got[0])
            if backup is not None:
                return backup
            return ("this Bash command is not provably plain, so a lexical hook cannot prove it "
                    "read-only, and a path it names (%r) lies inside the OPF product root %r "
                    "(its store tree is protected); it is denied fail-closed (R5). %s."
                    % (cand, got[0], SANCTIONED))
        if cand not in exempt:
            reason = _guard_rule(cand)
            if reason is not None:
                return reason
    # Round 13: a git work-tree subcommand that runs code (bisect, submodule, filter-branch) or
    # carries another not-plain trait takes the unbound repository-top check of the git work-tree
    # rule (R8). Round 14: the subcommand is read over EVERY literal word after a git word
    # (_git_worktree_words), so a global option outside the grammar (git -c x=y checkout -- .) and
    # a second command (git checkout -- .; true) no longer hide it.
    # Round 22: an output option word after a git word is judged from the repository top too.
    reason = _git_output_words_reason(words, cwd, cands)
    if reason is not None:
        return reason
    if _git_worktree_words(words):
        return _git_unbound_rule(cwd, cands, set(_guarded_prefixes()))
    return None


def _plain_bash_rule(command, words, cwd):
    """R5, R6 and R8 for a PROVABLY PLAIN Bash command: the exact path check. The judged spellings
    are every dequoted word, every option-glued or delimiter-embedded spelling inside a word
    (_derived_spellings: ls -ITODO.md, of=alias, -I/abs/TODO.md) and every inherited git path value
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
    program = os.path.basename(words[0])
    joined, cwd_joined = [], []
    if program in JOIN_WRITERS:
        joined, reason = _joined_targets(cands, words[1:] + derived)
        if reason is not None:
            return reason
        parents = _parents_option(words[1:])
        if parents is not None:
            # Round 16: --parents writes each source's whole spelling under the directory.
            if not resolve_all:
                return ("%s carries %r, which writes each source's whole spelling under the "
                        "destination directory (cp --parents /abs/src.txt docs writes "
                        "docs/abs/src.txt), and the command names more than %d words, past the "
                        "word budget where a relative spelling no longer resolves, so the "
                        "destinations cannot be computed; it is denied fail-closed (R5, R6). %s."
                        % (program, parents, MAX_RESOLVED_WORDS, SANCTIONED))
            more, reason = _parents_targets(cands, words[1:] + derived)
            if reason is not None:
                return reason
            joined = joined + more
    if program == "ln":
        # Round 14: ln with one operand links it into the CURRENT directory under its basename
        # (ln -f notes/STATUS.md from docs replaces docs/STATUS.md), a target no word spells.
        cwd_joined, reason = _joined_targets(_candidates(cwd, None, "literal") or [],
                                             words[1:] + derived)
        if reason is not None:
            return reason
    container = program in CONTAINER_VERBS
    git_sub = _git_worktree_sub(words)
    roots, reason = _bound_roots(command, cwd, spellings)
    if reason is not None:
        return reason + "; failing closed (R6)"
    for cand in cands + joined + cwd_joined:
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
    if git_sub is not None and roots:
        return ("git %s rewrites the working tree or the index, and this command acts in the bound "
                "OPF product root %r, so it is denied whatever its pathspec spelling (git expands "
                "a glob or pathspec magic itself) (R5). %s." % (git_sub, roots[0], SANCTIONED))
    reason = _git_output_reason(words, cwd, cands, roots)
    if reason is not None:
        return reason
    protected = set(frozen[0]) | set(views[0]) | set(reg_idents) | set(_guarded_prefixes())
    protected.update(os.path.join(root, WORKING) for root in roots)
    if git_sub is not None:
        # Unbound: the pack own tree (R8) under the cwd, a directory the command names, or the
        # repository top above either is still a container git rewrites.
        reason = _git_unbound_rule(cwd, cands, protected)
        if reason is not None:
            return reason
    for cand in (cands if container else []) + joined:
        reason = _container_rule(cand, protected)
        if reason is not None:
            return reason
    for cand in cands + joined + cwd_joined:
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
    if program in BACKUP_WRITERS:
        # Round 14: checked last, so a backup command that the exact check already denies keeps
        # that more specific reason.
        return _backup_reason(words, roots[0] if roots else None)
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
        _stderr_note("opf-pretooluse-deny: cannot evaluate: %s. Failing closed: the tool call is "
                     "blocked (spec 14.1 denial posture).\n" % (exc,))
        return 2
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        _stderr_note("opf-pretooluse-deny: cannot evaluate: the payload carries no non-empty "
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
        _code = main()
    except BaseException as exc:  # noqa: BLE001  fail-closed backstop: never a silent allow on a crash
        _stderr_note("opf-pretooluse-deny: cannot evaluate: unexpected error (%r). Failing "
                     "closed: the tool call is blocked.\n" % (exc,))
        _code = 2
    _exit_now(_code)
