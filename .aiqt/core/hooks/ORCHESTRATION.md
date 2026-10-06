# The orchestrator-integrity suite

Six controls over one substrate: a stop-work guard (Stop, TeammateIdle, and the scheduled-yield
tools), a record-drift gate, a background-dispatch truncation guard, an unattended-ask blocker, a
resume audit with a mutation barrier, and a mistakes register. One registry declares the adopter's
bindings; one pure decision core (decide_yield in scripts/aiqt_hooks.py) makes every yield judgement,
so a new yield path is covered by adding a binding, never by re-implementing judgement.

## The registry

The suite is INERT unless a registry is present: `.aiqt/orchestration.local.json` (machine-local,
never committed; whole-file precedence) or `.aiqt/orchestration.json` (committed, adopter-authored),
resolved at the repository root of the session cwd. Relative paths resolve against that root. All
keys except `version` are optional; an undeclared surface simply removes the probes that need it.

```json
{
  "version": 1,
  "enumerator": {"argv": ["python3", "tools/aei_backlog_md.py", "--backlog", "BACKLOG.md", "--aei"],
                 "timeout": 60},
  "record": {"findings": "path", "pending_decisions": "path", "handoff": "path"},
  "truth": {"changelog": "changelog.toml", "merged_pr_history": true},
  "mode": {"path": "path"},
  "lease": {"path": "path", "max_age_hours": 24},
  "state_dir": "path",
  "yield_tools": ["ScheduleWakeup", "CronCreate"],
  "dispatch_tools": [],
  "review_dispatch": {"commands": ["orch-dispatch"], "brief_option": "--brief",
                      "labels": {"target": "Review-target:", "revision": "Reviewed-revision:",
                                 "path": "Review-path:", "repo": "Review-repo:",
                                 "branch": "Review-branch:"},
                      "authority": {"argv": ["python3", "tools/task_revision.py"], "timeout": 5},
                      "max_brief_bytes": 1048576},
  "mistakes_register": "path",
  "attestations": "path",
  "staleness": {"external_hours": 24, "task_hours": 24},
  "escape": {"path": "path"},
  "companion_stores": ["/abs/path/to/a/store/repo/root"]
}
```

`state_dir` defaults to `${XDG_STATE_HOME:-$HOME/.local/state}/aiqt-guardrails/orch/<repo-key>/`
(repo-key is a digest of the root path). The machine-written state there is `dispatch-ledger.jsonl`,
`guard-events.jsonl`, `turn-state.json`, `resume-barrier.json`, `pending-asks.jsonl`,
`backlog-checkpoint.json`, `attestations-validated.json`, and, when their events occur,
`escape-spoof.json` (renamed with a `.surfaced` suffix once the resume audit has raised it) and the
append-only `forced-exit.jsonl` (every non-closed-disposition forced exit appended as its own row,
each surfaced exactly once, tracked by a companion `forced-exit-surfaced.json`). The mode record is a SHARED text file, read by ONE sound parser (never an incremental regex-plus-substring
scan), and is recognized in EITHER of two shapes: a plain `Operating-mode: <text>` declaration line, or a JSON
object with exactly a top-level string `mode` key (`{"mode": "attended"}` / `{"mode": "unattended"}`). A
leading byte-order mark is tolerated in both shapes (stripped once before any check). The `Operating-mode:`
declaration is parsed on its OWN physical line (the value is the rest of that line only and never crosses a
newline; leading horizontal whitespace is tolerated consistently), and its value must BEGIN with `attended`
or `unattended` (compound annotations such as `attended (ipad); continuous mode` or `unattended; continuous`
are allowed) or it fails closed; the match is word-anchored at the start, never a substring, so `disattended`
and `not-attended` do not match. A JSON marker must be exactly `{"mode": "<attended|unattended...>"}` (parsed
strict-exact with a duplicate-key-rejecting hook and no extra keys) or it fails closed. A recognized mode
whose value begins with the `unattended` token arms the ask blocker. The reader fails CLOSED to the
guards-armed (`unattended`) posture, never silently disarming, when a marker IS present but cannot yield a
recognized value: a present-but-unreadable or non-UTF-8 file, a present `Operating-mode:` declaration whose
value is empty or does not begin with attended/unattended, a JSON-shaped marker (content beginning with `{`,
`[`, or `"`) that is malformed or partial (an unterminated string, trailing garbage, or duplicate keys
included), a present JSON value that parses but is not an object with exactly a single string `mode` key (a
scalar such as a number, boolean, or null, an array, an object with extra keys, or an object without a string
`mode`), or a marker whose value falls outside the `attended`/`unattended` family. It preserves the fail-open
(undeclared) answer only when NO marker is present: an undeclared mode path, a genuinely absent file, an empty
or whitespace-only file, or content that carries no `Operating-mode:` declaration line, does not parse as
JSON, and whose first non-whitespace character is not `{`, `[`, or `"` (ordinary prose: a sentence merely
mentioning attended or unattended, or one beginning with a number or word such as `42 items done` or `true
trailing`, is such prose and does not arm). The escape sentinel (default
`<state_dir>/ESCAPE-ALLOW-YIELD`) is operator-owned by enforced acceptance, not convention: it is
honoured only as a regular file (never a symlink), owned by a uid other than the assistant's
effective uid, and not group- or other-writable. A present sentinel failing any condition is ignored
(the decision proceeds exactly as with no sentinel), logged to guard-events, and surfaced once by
the next resume audit. Where operator and assistant share one uid, no file either can create passes,
so the clean-ALLOW escape channel is unavailable there: recovery is a differently-owned sentinel
(for example root-owned), registry or mode maintenance by the operator, or the bounded, marked exit;
split-uid deployment is the recommended posture.

`companion_stores` is an optional list of ABSOLUTE paths to git repo TOPLEVELS beside the session
repo that the write-scope guard (wrtscp) treats as SANCTIONED cross-repo write targets: the most
common case is the sole orchestrator's own durable store, which is by design a SECOND git repo next
to the code repo. A guarded Write/Edit/MultiEdit whose target resolves, by EXACT repo-root match
(the target's own resolved git toplevel EQUALS a declared store root, never a prefix or substring),
into a declared store is ALLOWED and AUDITED (a `guard-events.jsonl` row, kind `wrtscp`, decision
`allow`) instead of denied as an out-of-repo aiming error. It is honoured only when well-formed:
each entry must be a non-empty absolute string resolving to a real git toplevel that IS the declared
root itself; a malformed, unresolvable, non-repo, or non-root entry is fail-closed (dropped, so the
cross-repo write still denies), and a bad entry can only ever remove a would-be allow, never open a
hole. Declaring a store does not let a write escape into a repo nested inside it (a nested repo's
toplevel differs) nor defeat the frozen-floor or nested-in-session denials. Like every registry key
it is adopter/harness-controlled config the guard READS; the guard never self-widens, and integrity
of the registry file itself is the harness's to hold.

Declaring a companion store is a GUARDRAIL-CONFIGURATION change, not a routine edit: `companion_stores`
widens where the write-scope guard permits covered writes to land (a cross-repo scope widening), so it
is governed by the SAME human-authorization discipline as any other scope widening (the
guardrail-config-integrity rule) and is set by the operator/harness on the registry surface, never by
the constrained actor. To enforce that, the write-scope guard freezes the machine-local registry
`.aiqt/orchestration.local.json` against the actor's OWN covered writes: a guarded Write/Edit/MultiEdit
targeting it is DENIED in both the armed and un-armed regimes and independent of the committed frozen
floor, so the actor cannot self-declare a companion-store (or yield-tool) widening through a covered
tool. This is defence in depth on the covered-tool path; a Bash-mediated write (a redirect, `sed -i`)
remains the disclosed write-scope residual, closed only by an OS-level write sandbox. A `companion_stores`
declaration takes effect only inside a VERSION-1 registry object: a registry whose `version` key is
missing or not `1` is read as bad (a cannot-evaluate), so a declaration that omits `"version": 1` yields
no stores and every cross-repo write FAILS CLOSED (denies) rather than the declaration silently taking
effect.

A declared `attestations` register is a chained, append-only row file (AT-N ids; the
mistakes-register machinery under a different prefix: `tools/orch_register.py append --prefix AT`
writes it and `tools/check_mistakes_register.py` gates it). It is validated at audit cadence (the
resume audit and `tools/orch_doctor.py --resume-audit`), never synchronously at yield time: the
validation pass writes `attestations-validated.json`, and the stop guard classifies an external or
foreign-lease blocker as blocked only when its ref is covered by a fresh validated row (the
evidence-freshness check still applies). Undeclared means the free-text evidence path stands,
byte-identical to prior behaviour; declared but with no readable fresh snapshot holds the affected
items (cannot-evaluate), never blocked and never actionable.

## The adopter enumeration interface (AEI), protocol v1

The registry names a fixed argv array, never a shell string. The provider enumerates the REAL
backlog source itself; it must not accept an item list or a completeness assertion from the agent.
Output on stdout:

```json
{"version": 1, "generated_at_utc": "...",
 "source": {"locator": "...", "revision": "...", "observed_at_utc": "..."},
 "items": [{"id": "...", "title": "...", "state": "open|closed|proposed", "granted": true,
            "blocker": {"kind": "tracked-task|human-decision|external|foreign-lease|not-before",
                        "ref": "...", "evidence": "...", "observed_at_utc": "..."}}]}
```

A nonzero exit, malformed JSON, a duplicate id, or an unknown version is ENUMERATOR_ERROR, never an
empty backlog; an empty `items` array is a valid enumeration, a parse failure never is. On the stop
path (FIX 1) an enumerator error or any cannot-evaluate FAILS CLOSED-CONTINUE: the stop is DENIED
(ignorance refuses the wind-down), releasable by the operator-owned escape sentinel OR by the
guard-owned loop bound (past which it becomes a deliberate, recorded forced exit), and never a clean
close; a new idle or wake scheduling call is likewise DENIED on cannot-evaluate, bounded by a
three-denial cap. `tools/aei_backlog_md.py` is the generic reference provider for a markdown-checkbox
backlog. Its grammar is the dash-bullet checkbox line (`- <ID> [ |.|o|O|x|BLOCKED] <title>`, with an
optional `:: blocker:<kind>=<ref>` clause and a trailing `:: proposed`); any other line is prose. A
backlog that yields ZERO items is a cannot-evaluate ENUMERATOR_ERROR (exit 3, no JSON emitted), NOT an
empty enumeration, unless it is a valid affirmed-empty backlog: a file whose every non-blank line, split
on physical newlines, is a column-0 sentinel line `<!-- aei: empty backlog -->` (one or more sentinel
lines allowed; blank lines allowed). Any other non-blank line,
including a heading or a comment carrying content (a markdown table, an alternate bullet, a fenced
example, indented code, prose, or any other unrecognized format), is operative content and yields
cannot-evaluate (exit 3) WHETHER OR NOT a sentinel is present, and an unaffirmed empty file fails closed,
so an unrecognized backlog can never read as a drained actionable set and a sentinel can never mask
unrecognized work. Emptiness is affirmed by the sentinel (which tolerates internal whitespace, not a
hyphen, and must sit at column 0), never inferred from absence; the empty `items` array is a valid
enumeration only when produced from that affirmation, every non-blank line a column-0 sentinel line.
The input is decoded as UTF-8 strictly: invalid UTF-8 is a cannot-evaluate (exit 3, no JSON), never
silently replaced and its line dropped. Any nonphysical line-boundary or separator control character
(VT, FF, FS, GS, RS, NEL, and the LINE and PARAGRAPH SEPARATOR) makes the whole input a cannot-evaluate
(exit 3), because such a character is a line boundary to Unicode but not a physical newline, so it could
otherwise smuggle a second item onto one physical line or embed content inside a sentinel; the enumerator
rejects it rather than guess. Lines are otherwise split on physical newlines only (CR, LF, CRLF).

## The review dispatch binding

The optional `review_dispatch` key turns on the review dispatch hook (`review-dispatch-pin`, rule
vfxcmt). Without it the hook does nothing. The key is checked strictly: an unknown key, a missing
`commands`, `brief_option`, `labels` or `authority`, or a value of the wrong type makes the binding
malformed.

- `commands` lists the basenames of the commands that dispatch a review.
- `brief_option` is the option that names the brief file, as `--brief PATH` or `--brief=PATH`.
- `labels` names the five brief labels. They must be distinct.
- `authority.argv` is a fixed argv, never a shell string. The hook runs it from the repository root
  with the brief's absolute path appended and every `GIT_*` variable removed from its environment, as
  for the hook's own git reads, and it must print exactly one full commit id: the
  authoritative task revision. `authority.timeout` is in seconds, from 1 to 8 (default 5). The whole
  check, git reads and authority included, runs within 8 seconds, under the 10-second hook timeout;
  when that budget runs out the dispatch is withheld as `UNVERIFIABLE:`. The check runs in a worker
  thread that the hook waits for only until the budget ends, so a read blocked in the kernel cannot
  hold the hook. Every file read also checks the budget before each read, and a result reached after
  the budget has run out is never an allow.
- `max_brief_bytes` caps the brief size (default 1048576).

The hook checks only a provably plain dispatch, decided on the raw characters before any lexing by
the shared plain-command specification. A command is plain only when every character is printable
ASCII (no tab, newline, NUL or non-ASCII character); none of `$`, backquote, backslash, `;`, `&`,
`|`, `<`, `>`, `(`, `)`, `{`, `}`, `[`, `]`, `*`, `?`, `!`, `#` or `~` appears outside a single-quoted
segment; a double-quoted segment holds none of them; no quote is left open; words are separated by
spaces only; and the command word is a bare name or path, unquoted, that is not a shell, an
interpreter, `eval`, `exec`, `source`, `.`, `env`, `command`, `builtin`, `xargs`, `nohup`, `timeout`,
`sudo` or another wrapper that runs a command. A plain dispatch is therefore the only command of the
call: no operator, no redirection (not even `</dev/null`) and no second command. Its command word is
a declared command (a path to it counts), compared without regard to case. The brief must be the last
argument, given once as `--brief PATH` or `--brief=PATH`, with no `--` before it; a two-character
brief option is never accepted in its `-b=PATH` form, which a getopt parser reads as the value
`=PATH`. A parser the hook does not know may also take a brief from an alias or a grouped short
option, so a brief given last is the one a last-wins parser keeps. A second brief, including an
abbreviation such as `--brie PATH`, is refused. A relative brief path resolves against the session
cwd; a path under `/proc` or `/dev` is withheld as `UNVERIFIABLE:`, since it can name a different file
in the hook's process (a sandbox may give each process its own `/dev/shm`). The brief is opened once, without blocking and without following a symlink,
and must be a regular file; one leading byte-order mark is dropped.

In a session that a binding scopes, every Bash call must be one plain command. A command that is not
plain is withheld as `UNVERIFIABLE:`, whatever it names, with a message that says how to split the work
into plain calls and write the dispatch plainly: a dispatcher name assembled at run time (a variable
joined to text, a glob, command output) is not in the raw text, so no name check can clear a command
that is not plain. A plain command that names a declared command other than as its command word (a
`git -c` alias that runs it, say), compared without regard to case, is withheld too. A plain command
that names no declared command is allowed.

The hook reads labels only from the brief, only at column 0, and only as the exact label followed by
one space. The value is the rest of that line. The brief must be UTF-8 with no NUL and no line
boundary other than a physical newline.

- `Review-target:` is required, once. It must be `revision`, `working-tree` or `not-a-review`. A
  `working-tree` or `not-a-review` target is allowed with a note and a `guard-events.jsonl` row.
- `Reviewed-revision:` is required, once, for a `revision` target. It must be the full lowercase
  commit id, at the length of the repository's object format. A short id, a branch name or `HEAD`
  is refused.
- `Review-path:` lines list the review set, one path per line, each kept exactly as written.
- `Review-repo:` (optional) is the absolute path of the repository top level the review reads. It
  defaults to the session repository root.
- `Review-branch:` (optional) is informational. If it does not resolve to the pin, the dispatch is
  allowed with a note.

For a `revision` target, the hook checks the following in order. The first failure decides.

1. The pin resolves to exactly that commit. A tag object id is not accepted.
2. The authority prints the same commit id.
3. Every parent named in the raw commit is present.
4. The commit's changed set equals the declared paths. The base is the sole parent, the first parent
   of a merge, or the empty tree for a root commit.
5. No declared path is staged against the pin, and the working tree matches the pin at every
   declared path. The working tree is compared by content: the hook hashes each declared file, or
   reads each symlink target, and compares the result and the file mode with the pin's tree entry.
   An assume-unchanged or skip-worktree flag, `core.ignoreStat`, the stat cache, an fsmonitor answer
   or a configured filter therefore cannot report a changed file as clean. A path the pin deletes
   must be absent. A checked-out submodule must be at the pinned commit. With `core.fileMode`
   false, a file that differs from the pin only in its executable bit is clean, as git reads it.

Every git read disables replacement refs, grafts, pathspec magic, the commit-graph cache, the
fsmonitor, the untracked cache and submodule recursion, and takes no optional locks. The changed set
and the staged state are read with `--ignore-submodules=none`, so a `.gitmodules` `ignore` value
cannot hide a submodule change. A file the pin replaces with a directory holding the pin's own
entries counts as clean.

The hook refuses with a deny that names the reason. When it cannot evaluate, it denies with the
reason prefixed `UNVERIFIABLE:`, so that outcome stays distinct. Cannot-evaluate cases include a
duplicate label, an unreadable brief, a failed or timed-out git probe, an exhausted time budget, and
an authority that fails or prints anything other than one commit id. A linked worktree is scoped
by its main worktree's registry, found through git or, when git cannot run (a broken shared
configuration), through the raw `.git` and `commondir` files. A linked worktree whose main worktree
cannot be located (a git directory separated from the main worktree) withholds every Bash call. When
git cannot say where the main worktree is (a failed or timed-out probe), the raw `.git` and
`commondir` files are read instead. A registry without a binding never ends the search: the session
repository's registry, its main worktree's, and every registry on the cwd's ancestors are read,
nearest first, and the first binding decides, so a registry of `{"version": 1}` checked out, written
or nested below an orchestrated tree cannot hide its binding. When git cannot resolve the session
repository (a broken configuration, a refused ownership check, a deleted cwd), or resolves one with
no binding, the hook looks for the registry on the cwd's ancestors, so a `core.worktree` setting that
moves the top level cannot turn the check off. If git cannot resolve the repository, or resolves one
that is not the registry's own (a nested repository, a moved top level), and a binding is found,
every dispatch is withheld as `UNVERIFIABLE:`. The authority runs from the top level of the worktree
whose registry binds the dispatch, so a relative `authority.argv` is always the registry's own. A
dispatch is withheld as `UNVERIFIABLE:` when the environment sets a variable that moves the
repository, index, object store or history git reads (`GIT_DIR`, `GIT_WORK_TREE`, `GIT_COMMON_DIR`,
`GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_QUARANTINE_PATH`,
`GIT_NAMESPACE`, `GIT_REPLACE_REF_BASE`, `GIT_GRAFT_FILE`, `GIT_SHALLOW_FILE`): the hook's reads ignore
it, but the dispatch inherits it. A malformed binding, a registry that cannot be read (including a
FIFO, a device or a symlink in its place), or an ancestor search that cannot be made withholds every
Bash call, foreground or background, until an operator repairs it.

Limits:

- A dispatch through an undeclared command, an alias, a function, a script, a nested shell string or
  a non-Bash tool is not seen.
- The hook checks the declaration and the repository, not what the worker reads. The worker must
  read from a checkout of the pinned revision, so point `Review-repo:` at a worktree checked out
  there.
- A misdeclared target bypasses the check.
- The authority's own correctness is the adopter's concern.
- Delivery acceptance is not checked.
- A plain command whose program runs an argument or its own configuration as shell text (a `git -c`
  alias, a pager or editor setting, a package script, a makefile) can assemble a dispatcher name the
  hook does not see; only a name written in full in the command is found.
- The dispatcher's own options other than the brief option (a `--family` or a working directory, say)
  are its own; the hook checks the brief and the repository, not what those options select.
- Variables that configure git without moving the repository (`GIT_CONFIG_*`, `GIT_CONFIG_PARAMETERS`)
  are not refused: the hook's own reads drop them, but the verifier inherits them.
- Many commands are refused although they dispatch nothing: in a session a binding scopes, every
  command that is not plain (a pipeline, `&&`, a redirection, a variable, a glob, a wrapper), and any
  plain command that mentions a declared command name outside one plain dispatch, such as
  `grep orch-dispatch` or a commit message that names it. Run each command as a call of its own, and
  redirect output another way.
- Uncommitted edits inside a checked-out submodule whose HEAD is at the pinned commit are not read. A declared file whose checkout was converted (line
  endings or a filter) differs by content from the pin and is refused.

## Platforms without hooks

The decision algorithm binds as an operating procedure: before any stop, idle wake, or drained
declaration, run `tools/orch_preflight.py` for the matching operation (`stop`, `idle`, or `drain`) and
act on its disposition table; the deterministic gates (record drift, mistakes register) still run and
are the enforced part on a hookless platform. The preflight is visibility only, not a blocking control,
and carries no enforceability-ledger row; this prose operating procedure is never advertised as
equivalent to a blocking hook.

A dispatcher without hooks can run the review dispatch check as a preflight: pipe the PreToolUse
payload (`tool_name` `Bash`, `cwd`, and `tool_input.command`) to `aiqt_hooks.py review_dispatch_pin`,
and withhold the dispatch on exit 2 or on a printed `deny` decision.

## Honest limits (suite-level)

Machine-local state is local-only: CI gates cover committed surfaces and the gates' own self-tests; gates over machine-local records run at session checkpoints and in the local mirror. The controls below RAISE THE BAR against a forged stop or escape; they are not categorical, because the state directory they read (the escape sentinel's anchor, and the anti-shrinkage checkpoint with its init marker and eligibility memory) is one the assistant itself can write, as is a declared attestation register with its anchor (a separately-declared surface wherever it is located). Categorical forgery-resistance needs operator-owned, assistant-non-writable READ sources at the OS layer (the attestation register, the decision record, and the escape sentinel; the guard's own working channel, its denial counter, guard-events, the checkpoint and its marker, forced-exit.jsonl, and the validated snapshot, must stay assistant-writable or the guard cannot record and fails open), described in SYSTEM-HARDENING.md; what the pack enforces in code is the following.

Fabricated free-text blocker evidence remains mechanically unclosable only where no attestation register is declared. With one declared, external and foreign-lease evidence is verified against validated attestations that are re-anchored against the register's authority (its git merge-base state where the register is tracked, else a companion anchor file), and at yield the validated snapshot is re-bound to that authority AND its substantiated refs are re-derived from the register content itself, so a snapshot forged with fabricated refs substantiates none of them and a register swapped or gone stale between audit and yield holds the affected items (cannot-evaluate); only a fresh, approved row whose integrity pointer resolves substantiates a ref. This raises the bar over free-text evidence but is NOT categorically forgery-proof: the register is a separately-declared surface, and filesystem write to it, wherever it is declared, suffices to forge an attestation by appending a valid approved row (uncommitted; the git or anchor authority checks only the committed or anchored prefix), and that appended row substantiates its ref. The categorical closure is an operator-owned, assistant-non-writable register at the OS layer (SYSTEM-HARDENING.md entry 2). The remaining residuals are cadence (tamper misleads until the next audit or gate run) and relevance (a pointer that resolves can still be a misappropriated pretext; triage refutes it).

The stop guard's blocker substantiation is itself largely ACTOR-AUTHORABLE: the backlog `state`/`granted` fields, a matching pending-decision row, the dispatch ledger, a `not-before` value, and (where no attestation register is declared) free-text external evidence are all surfaces the assistant can write. The guard raises the bar (it rejects an unknown blocker kind and requires a substantiating record in the declared authoritative source) but cannot categorically enforce that a block is granted by an authority other than the assistant; the categorical closure is operator-owned, assistant-non-writable records at the OS layer (SYSTEM-HARDENING.md entry 2). Because of this the stop path FAILS CLOSED-CONTINUE, not open: an unconfirmable blocker, an unreadable proof source, or a non-enumerable backlog DENIES the stop (ignorance refuses the wind-down) rather than yielding with findings, the only release for a genuine block is the operator-owned escape sentinel, and the guard never converts an unconfirmable state into a clean close; the loop bound past which repeated denials become a single deliberate, recorded forced exit remains the bounded, marked exit.

The escape sentinel opens the clean-ALLOW channel only as an operator-created artefact: a regular file with a single hard link, reached without following any symlinked path component, resolved under the state-dir anchor (an absolute or '..' path outside it is rejected), owned by a uid other than this guard's, and not group- or other-writable; any other present file is ignored, recorded, and surfaced once at the next resume. Uid inequality proves not-self, never operator identity; component resolution is a best-effort lstat walk under the anchor, so a narrow window between the walk and the descriptor-bound stat remains.

The registry file is the confinement's trust root (a guardrail-config-integrity surface, review-gated where committed). The anti-shrinkage checkpoint's first window has no union to compare; an init marker distinguishes that genuine first window from a checkpoint deleted after a prior window (held as a possible reset), and the bound logs what it drops, never silently. The checkpoint also records each open id's eligibility (granted-and-open vs proposed/ungranted), so a demotion of a previously-actionable id to proposed or ungranted with no closed receipt is held (cannot-evaluate) rather than silently routed to the harmless proposed bucket; that eligibility memory lives in the same actor-writable state dir, raising the bar without being categorical. Deleting the marker and the checkpoint together, like deleting the turn state to restart the loop bound, is the same actor-writable-state residual. The loop bound remains a deliberate, exhaustible, marked exit, with each denial and each forced exit individually recorded in guard-events and an append-only forced-exit log that the next resume surfaces exactly once. Host clock control defeats every freshness check here (out of threat model). The roster of yield primitives is fixed at generation and re-reconciled when the toolchain changes.
