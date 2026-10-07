# The orchestrator-integrity suite

Six controls over one substrate: a stop-work guard (Stop, TeammateIdle, and the scheduled-yield
tools), a record-drift gate, a background-dispatch truncation guard, an unattended-ask blocker, a
resume audit with a mutation barrier, and a mistakes register. One registry declares the adopter's
bindings; one pure decision core (decide_yield in scripts/aiqt_hooks.py) makes every yield judgement,
so a new yield path is covered by adding a binding, never by re-implementing judgement.

## The registry

A registry is `.aiqt/orchestration.local.json` (machine-local, never committed; whole-file
precedence) or `.aiqt/orchestration.json` (committed, adopter-authored). Relative paths in it resolve
against the repository root of the session cwd. All keys except `version` are optional; an undeclared
surface simply removes the probes that need it.

Where a registry is looked up (two scopes):

- Every component except the truncation guard (the stop guard, the scheduled-yield and
  TeammateIdle bindings, the unattended-ask blocker, the resume audit and barrier, the dispatch
  ledger, the prompt stamp, and the untracked wait-loop guard) looks only at the git-resolved
  toplevel of the session cwd. With no git toplevel, or where the registry loader reads the registry
  there as absent, each of them is inert. A toplevel the loader cannot examine (a regular file, a
  directory this process cannot search, or one holding a regular file named `.aiqt`) reads as bad,
  not absent.
- The truncation guard looks at the UNION of two places: every directory on the cwd's physical
  ancestor chain (a no-follow, descriptor-anchored walk that needs no git) and, where git resolves
  a toplevel for the cwd, that toplevel (which `core.worktree` can place off the ancestor chain).
  Before either lookup, in every session, it denies a `tool_name` that is missing, not a non-empty
  string, or carries a control character, and for a Bash call a `cwd` that is missing, not a
  non-empty string, contains a NUL, is not an existing path, is not a directory, or is a directory
  it cannot walk. A malformed `tool_input` is checked only after the lookup, so with no registry in
  scope (default mode) such a call is allowed.
- On the walk the NEAREST directory whose registry probe is not a clean not-present decides, and
  the walk stops there. A clean not-present is no `.aiqt` entry at all, or a real `.aiqt` directory
  holding neither registry name; so the walk passes a `.aiqt` directory without a registry name
  (the usual layout of an adopted repository) and continues upward. A stray `.aiqt` file (or any
  other entry it cannot evaluate) in a subdirectory shadows a confirmed registry above it, so
  registry-required mode denies a Bash call from below that subdirectory. The git toplevel is
  consulted only when every directory on the chain probes as a clean not-present.
  Inside `.aiqt` the first present registry name decides (`orchestration.local.json`, then
  `orchestration.json`): an `orchestration.local.json` that is a symlink or a directory is not
  confirmed even beside a regular `orchestration.json`.

What an absent registry means (two modes; they change only the truncation guard's scope decision and what
the doctor and the resume audit report and arm from it):

- DEFAULT (`AIQT_ORCH_REQUIRE_REGISTRY` unset or an explicit off value: `""`, `0`, `false`, `no`,
  `off`, ASCII case-insensitive, matched exactly with nothing stripped): with no registry in its
  scope the truncation guard is inert and allows every Bash call that passes those pre-scope
  checks. A `.aiqt` entry it cannot evaluate (a regular file or a symlink named `.aiqt`, or a
  `.aiqt` directory the guard's process lacks search (execute) permission on, such as mode `0600`
  or `0000` for a process those modes bind; a `.aiqt` of mode `0100` evaluates normally for its owner
  where `O_PATH` exists (Linux), because the `O_PATH` open needs no read permission, and for a process
  the mode bits do not bind (root, or one holding `CAP_DAC_READ_SEARCH` or `CAP_DAC_OVERRIDE`), but it
  cannot be evaluated by any other process that is not its owner (mode `0100` grants search to the
  owner only), nor by its owner where `O_PATH` is unavailable and the `O_RDONLY` fallback open also
  needs read permission; or a registry name that is not a regular file or cannot be examined) counts as present, so the
  guard stays active there. The git toplevel is consulted only when the whole chain probes as a
  clean not-present: one that exists but cannot be opened as a directory (it is a regular file, or
  the process may not reach it) keeps the guard active the same way, while one that does not exist
  (for example `core.worktree` naming a removed directory) holds no registry and reads as absent.
- REGISTRY-REQUIRED (`AIQT_ORCH_REQUIRE_REGISTRY` set to any other value, including a padded or
  garbled one): with no registry in its scope the truncation guard DENIES every Bash call that
  passes the pre-scope checks, and an entry it cannot evaluate also DENIES (a discovery fault is
  not a registry), as does a git toplevel that exists but cannot be opened as a directory (with
  its own reason). A git toplevel that does not exist reads as absent, so it DENIES as an absent
  registry does. A registry reachable only through the git toplevel reads as absent when git fails. A
  symlink at the first present registry name DENIES in this mode, although the registry loader the
  other components use follows it and accepts it; a symlinked `orchestration.json` beside a regular
  `orchestration.local.json` is never examined. Discovery checks presence and file type only: a
  regular registry file that is unreadable, invalid JSON, or empty satisfies this mode (the other
  components then read it as bad). The variable is otherwise read only where the guard's scope decision for a
  Bash call from the repository root is reported: by every `tools/orch_doctor.py` run, whose scope
  report differs by mode (printed with no registry at the root, a finding with one when the guard
  denies), and by the resume audit (the SessionStart hook and `tools/orch_doctor.py --resume-audit`,
  which write the same resume barrier). Where the registry loader reads the repository root's registry
  as absent, the resume audit stays silent and the doctor exits 2, in both modes, so every other component
  stays inert on an absent registry in both modes. With a registry present, the audit also arms the
  barrier (and SessionStart warns) when the truncation guard would deny a Bash call from the
  repository root: in BOTH modes when git resolves the root but the walk cannot be carried out there
  (for example a root directory this process can enter but not read), and in this mode also when that
  root's registry scope is absent or cannot be confirmed (for example a symlinked `orchestration.json`).
  The audit finds its root only through git, and returns silently in both modes before it reads the
  registry wherever it finds none: a session cwd that is missing, empty or not a string (no git call is
  made), or a session cwd for which git resolves no root, for any reason (an unreadable or broken git
  config, a dangling gitfile, a dubious-ownership refusal, no git binary, a timeout, or a session cwd
  this process cannot enter), or where git exits 0 but prints output that cannot be decoded (a root
  path that is not valid UTF-8, for example), is empty, or is not absolute. The truncation guard denies
  a Bash call whose cwd is missing, empty or not a string in every mode, before its scope check. For a
  Bash call from a non-empty string cwd where git resolves nothing, whether the guard's scope check denies it depends only on its own ancestor walk of
  that cwd and the mode, because its git leg resolves nothing either; its other checks still read the
  call itself (in scope, a foreground bare `&` is denied): with a regular registry on the walk the scope
  check does not deny a plain command in either mode, while a walk it cannot carry out denies in both.
  Git can still resolve a root this process cannot enter (through `core.worktree`, from a session cwd
  inside the repository's git directory). Where that root exists but this process cannot search it, or
  it is not a directory, the registry loader's `lstat` faults, the loader reports the registry bad, and
  the audit warns and arms the barrier in both modes (as it does for a regular file named `.aiqt` at
  any root); where that root does not exist, the loader reports the registry absent and the audit stays
  silent in both modes. Arming is best-effort, as is clearing, and both are atomic: each writer creates
  a temporary file beside the barrier file, flushes and fsyncs it, then renames it onto the barrier
  file with `os.replace`. Where the SessionStart audit cannot write the barrier (its state directory
  cannot be created, the temporary file cannot be created or written, or the rename fails, for example
  `XDG_STATE_HOME` naming a regular file, or a full disk), it removes the temporary file it created and
  ignores the error so that SessionStart is never wedged. The previous barrier file, armed or clear, or
  its absence, is then left byte-identical, so the PreToolUse barrier does not surface this run's
  findings; the session still sees the SessionStart warning naming them (where opening the forced-exit
  log fails other than as not found, because the state directory cannot be searched or is not a
  directory, as with that regular file, the warning also names the forced-exit log as unreadable). The
  PreToolUse barrier reads the barrier file only where the registry loader reads the registry ok, so
  for a registry it reads bad an armed barrier surfaces nothing there until the registry reads ok. It
  reads an absent barrier file as clear, and one that exists but cannot be read or parsed, or is not a
  well-formed barrier (a boolean `active` and a list of string `findings`), as armed: each mutation
  outside the allowlist then surfaces a note naming the file as unreadable (not once per arming, since
  there is no readable `warned` flag to record), and during the bake that note blocks nothing. It clears
  when `tools/orch_doctor.py --resume-audit` or the next SessionStart audit replaces the file, or when
  the user corrects or removes it (the state directory is on the allowlist); where the state directory
  cannot be searched the replace fails too, and the note persists until its permissions are restored.
  `tools/orch_doctor.py --resume-audit` does not ignore a write error: its run ends with the error, and
  the previous barrier file is left unchanged. The barrier does not record the
  mode, so the two writers agree only when they run with the same value: a doctor run without the
  variable can clear a barrier a registry-required SessionStart armed for a scope deny. Clearing it does
  not allow any Bash call: the barrier only warns, and the guard still denies on its own scope check.

The write-scope guard is not part of this suite but locates its declaration through this registry,
and it is not inert on an absent registry: it then reads the declaration from the default state
directory below, and a declaration there arms slice confinement.

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

## Platforms without hooks

The decision algorithm binds as an operating procedure: before any stop, idle wake, or drained
declaration, run `tools/orch_preflight.py` for the matching operation (`stop`, `idle`, or `drain`) and
act on its disposition table; the deterministic gates (record drift, mistakes register) still run and
are the enforced part on a hookless platform. The preflight is visibility only, not a blocking control,
and carries no enforceability-ledger row; this prose operating procedure is never advertised as
equivalent to a blocking hook.

## Honest limits (suite-level)

Machine-local state is local-only: CI gates cover committed surfaces and the gates' own self-tests; gates over machine-local records run at session checkpoints and in the local mirror. The controls below RAISE THE BAR against a forged stop or escape; they are not categorical, because the state directory they read (the escape sentinel's anchor, and the anti-shrinkage checkpoint with its init marker and eligibility memory) is one the assistant itself can write, as is a declared attestation register with its anchor (a separately-declared surface wherever it is located). Categorical forgery-resistance needs operator-owned, assistant-non-writable READ sources at the OS layer (the attestation register, the decision record, and the escape sentinel; the guard's own working channel, its denial counter, guard-events, the checkpoint and its marker, forced-exit.jsonl, and the validated snapshot, must stay assistant-writable or the guard cannot record and fails open), described in SYSTEM-HARDENING.md; what the pack enforces in code is the following.

Fabricated free-text blocker evidence remains mechanically unclosable only where no attestation register is declared. With one declared, external and foreign-lease evidence is verified against validated attestations that are re-anchored against the register's authority (its git merge-base state where the register is tracked, else a companion anchor file), and at yield the validated snapshot is re-bound to that authority AND its substantiated refs are re-derived from the register content itself, so a snapshot forged with fabricated refs substantiates none of them and a register swapped or gone stale between audit and yield holds the affected items (cannot-evaluate); only a fresh, approved row whose integrity pointer resolves substantiates a ref. This raises the bar over free-text evidence but is NOT categorically forgery-proof: the register is a separately-declared surface, and filesystem write to it, wherever it is declared, suffices to forge an attestation by appending a valid approved row (uncommitted; the git or anchor authority checks only the committed or anchored prefix), and that appended row substantiates its ref. The categorical closure is an operator-owned, assistant-non-writable register at the OS layer (SYSTEM-HARDENING.md entry 2). The remaining residuals are cadence (tamper misleads until the next audit or gate run) and relevance (a pointer that resolves can still be a misappropriated pretext; triage refutes it).

The stop guard's blocker substantiation is itself largely ACTOR-AUTHORABLE: the backlog `state`/`granted` fields, a matching pending-decision row, the dispatch ledger, a `not-before` value, and (where no attestation register is declared) free-text external evidence are all surfaces the assistant can write. The guard raises the bar (it rejects an unknown blocker kind and requires a substantiating record in the declared authoritative source) but cannot categorically enforce that a block is granted by an authority other than the assistant; the categorical closure is operator-owned, assistant-non-writable records at the OS layer (SYSTEM-HARDENING.md entry 2). Because of this the stop path FAILS CLOSED-CONTINUE, not open: an unconfirmable blocker, an unreadable proof source, or a non-enumerable backlog DENIES the stop (ignorance refuses the wind-down) rather than yielding with findings, the only release for a genuine block is the operator-owned escape sentinel, and the guard never converts an unconfirmable state into a clean close; the loop bound past which repeated denials become a single deliberate, recorded forced exit remains the bounded, marked exit.

The escape sentinel opens the clean-ALLOW channel only as an operator-created artefact: a regular file with a single hard link, reached without following any symlinked path component, resolved under the state-dir anchor (an absolute or '..' path outside it is rejected), owned by a uid other than this guard's, and not group- or other-writable; any other present file is ignored, recorded, and surfaced once at the next resume. Uid inequality proves not-self, never operator identity; component resolution is a best-effort lstat walk under the anchor, so a narrow window between the walk and the descriptor-bound stat remains.

The registry file is the confinement's trust root (a guardrail-config-integrity surface, review-gated where committed). The anti-shrinkage checkpoint's first window has no union to compare; an init marker distinguishes that genuine first window from a checkpoint deleted after a prior window (held as a possible reset), and the bound logs what it drops, never silently. The checkpoint also records each open id's eligibility (granted-and-open vs proposed/ungranted), so a demotion of a previously-actionable id to proposed or ungranted with no closed receipt is held (cannot-evaluate) rather than silently routed to the harmless proposed bucket; that eligibility memory lives in the same actor-writable state dir, raising the bar without being categorical. Deleting the marker and the checkpoint together, like deleting the turn state to restart the loop bound, is the same actor-writable-state residual. The loop bound remains a deliberate, exhaustible, marked exit, with each denial and each forced exit individually recorded in guard-events and an append-only forced-exit log that the next resume surfaces exactly once. Host clock control defeats every freshness check here (out of threat model). The roster of yield primitives is fixed at generation and re-reconciled when the toolchain changes.
