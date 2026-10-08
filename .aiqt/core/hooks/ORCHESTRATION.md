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
  directory, as with that regular file, the warning also names the forced-exit log as unreadable).
  That warning then also says the barrier was not persisted (this audit did not arm it) and asks for a
  manual record, instead of saying that a re-run clears the barrier, and names the remedy: remove a
  directory at the barrier path (neither audit can replace one), otherwise make the state directory
  creatable and writable. A clean audit that cannot clear the barrier adds no warning: a barrier it
  leaves armed still reads as armed, noted once per arming where its warned flag can be written, otherwise
  on each mutation outside the record surfaces (one already warned about stays silent), and
  a directory in its place reads as armed and is noted on each mutation outside the record surfaces. The
  PreToolUse barrier reads the barrier file only where the registry loader reads the registry ok, so
  for a registry it reads bad an armed barrier surfaces nothing there until the registry reads ok. It
  reads an absent barrier file (a dangling symlink included) as clear, and a symlink to a regular file
  as that file. It reads the barrier with a non-blocking open (a FIFO with no writer does not wait for
  one), an `fstat` that refuses anything not a regular file before any read (so a FIFO or a device such
  as `/dev/zero` is never read), and a read of at most 65537 bytes (the 65536-byte bound plus one byte
  that detects a longer file); a close error is read as a bad barrier, never passed to the dispatcher.
  Every writer stores at most 65536 bytes: a finding list too long for the bound is stored as its first
  findings (each cut to 4000 characters) plus one line counting the rest, so a written barrier always
  reads back armed and well-formed and its warned flag can be recorded. A barrier is well-formed only when it is
  a JSON object whose keys are exactly a boolean `active` and a list of string `findings` (both
  required; a missing `findings` is malformed, never read as an empty list), plus an optional boolean
  `warned` and an optional string `ts`. One that exists but is not a regular file (a FIFO, a device, a
  directory, or a symlink to any of these; a UNIX socket fails its open with `ENXIO` and reads as
  `OSError`), is larger than the bound, cannot be read, closed or parsed for any reason (including a
  `RecursionError` from deeply nested JSON), or is not well-formed,
  reads as armed: each mutation outside the allowlist then surfaces a note naming the file as
  unreadable or malformed with the reason (not once per arming, since there is no readable `warned`
  flag to record), during the bake that note blocks nothing, and a mutation on the allowlist is allowed
  after the same read. The reader does not bound everything: path lookup on a hung mount can stall the
  open (and the `lstat` and `realpath` calls) for any file type, a regular file on a stalled filesystem
  can stall the read, and opening a device node does whatever its driver does; the 10-second hook
  timeout bounds each such stall. A directory is named only where the `lstat` of the path and the
  `fstat` of what was opened agree on device and inode. It clears when
  `tools/orch_doctor.py --resume-audit` or the next SessionStart audit replaces the file (the rename
  replaces a FIFO, a device node or a symlink at the path and leaves a symlink's target intact), or
  when the user corrects or removes it (the state directory is on the allowlist); where the state
  directory cannot be searched the replace fails too, and the note persists until its permissions are
  restored. Neither audit can replace a directory at the barrier path itself (the rename fails with
  `IsADirectoryError`), so for a directory the note says to remove the directory, after which a clean
  audit clears the barrier; a symlink to a directory gets the generic note, since the rename replaces
  the link. A writer removes the temporary file it created when any later step fails
  (writing, closing or renaming it) and never removes a temporary name it did not create; a process
  killed between creating and renaming it leaves that temporary file beside the barrier file.
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
`escape-spoof.json` (renamed with a `.surfaced` suffix once the resume audit has raised it; each rename
replaces the earlier `.surfaced` file, a failed rename leaves it to be raised again, and a later sentinel
recorded before that audit overwrites it, so the lasting record of each ignored sentinel is its
`guard-events.jsonl` row of kind `escape-spoof`, where that append succeeded; a failed append is warned
about in the output of the hook that ignored the sentinel (its banner, or the block reason of a denied
Stop or TeammateIdle, which has no banner; a denied scheduling call carries it in its deny reason and its
banner), which asks for a manual record, and leaves no row) and the append-only `forced-exit.jsonl`
(every non-closed-disposition forced exit appended as its
own row, each normally raised once, at least once if recording that it was raised fails, tracked by a
companion `forced-exit-surfaced.json`). The whole-file JSON records (`turn-state.json`,
`backlog-checkpoint.json`, its `checkpoint-init.marker`, `attestations-validated.json` and
`forced-exit-surfaced.json`) are each saved by one writer: it opens the state directory once as a
directory descriptor (with `O_PATH` where the platform has it, so the open needs only search permission and
a write-and-search-only state directory saves; where `O_PATH` is absent the fallback open also needs read
permission on that directory) and binds every later step (the read of the target's permission bits, the
create, the rename and the cleanup removal) to it, so a symlink swapped in for that directory after the open
can neither make the writer publish foreign content under the target's name nor hide the writer's own
temporary file (the directory path itself is resolved once, at that open, a directory moved after the open
still receives the save under its new name, and the writing uid is not a boundary); it creates a temporary
file of its own beside the target (an exclusive create under a random name that never follows a symlink, so
a file or symlink already at a temporary-like name is never opened, written or removed), gives it the
existing target's permission bits (0600 for a new target), writes and fsyncs it, and renames it onto the
target. A reader
therefore sees the previous file or the new one, never a part of either, and a failed save leaves the
previous file (or its absence) in place. A failed save is named with its error in the hook's output, except
for the forced-exit surfaced set, whose failed advance raises those rows again at the next resume audit; a
temporary file whose removal after a failure also fails is left beside the target and named there too, by
the state directory path as given, which after a swap may no longer lead to it, and one left by a process
killed mid-save is removed by nothing. Concurrent saves never share a temporary file,
so one save cannot corrupt or remove another's, but the last rename wins: two hooks that read, modify and
save the same file at once can lose one update (the read-modify-write is not serialized), and each reports
its own save as succeeded. The save needs a writable state directory (and, where `O_PATH` is absent, a
readable one), so a writable `turn-state.json` in a
state directory the hook cannot write is not saved: a Stop or TeammateIdle deny there now fails open with
findings (the earlier in-place overwrite let it stand), and a scheduling deny says its counter was not
written. A symlink at the target is replaced, not written through, and the new file is owned by the writing
uid. The uid the hooks run as can replace any of these files itself, so this is crash and collision safety,
not a boundary against that uid. The mode record is a SHARED text file, read by ONE sound parser (never an incremental regex-plus-substring
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
recognized value: every outcome of reading the mode path other than not found or a decoded text (a mode path
holding a NUL character, or one that is not strict UTF-8 (a lone surrogate, which is also how a
surrogate-escaped non-UTF-8 name arrives), refused fail-closed before any open even where the OS could open
it; a file that cannot be opened or read, a socket included, whose non-blocking open itself fails (`ENXIO`
on Linux); one that is not a regular file, such as a FIFO, a device like `/dev/zero` or a directory, refused
by an fstat after the non-blocking open and before any read, so a FIFO with no writer does not wait; one
larger than 1048576 bytes, of which the reader reads at most one byte more; or one whose bytes are not valid UTF-8), each of which the ask guard's
deny names, a present `Operating-mode:` declaration whose
value is empty or does not begin with attended/unattended, a JSON-shaped marker (content beginning with `{`,
`[`, or `"`) that is malformed or partial (an unterminated string, trailing garbage, duplicate keys, or
nesting too deep to parse included), a present JSON value that parses but is not an object with exactly a single string `mode` key (a
scalar such as a number, boolean, or null, an array, an object with extra keys, or an object without a string
`mode`), or a marker whose value falls outside the `attended`/`unattended` family. It preserves the fail-open
(undeclared) answer only when NO marker is present: an undeclared mode path, a genuinely absent file (one
whose open reports not found; a missing file under a parent path that is a regular file reports not a
directory and fails closed, as any other unreadable file does), an empty
or whitespace-only file, or content that carries no `Operating-mode:` declaration line, does not parse as
JSON, and whose first non-whitespace character is not `{`, `[`, or `"` (ordinary prose: a sentence merely
mentioning attended or unattended, or one beginning with a number or word such as `42 items done` or `true
trailing`, is such prose and does not arm). Every other input fails closed, so every input reaches one of
the two answers; only a stall bounded by the hook timeout delays it (a path lookup on a hung mount, a read
from a stalled filesystem, or what a device driver's open does). The scope check of the stop, idle and
yield guards, the dispatch ledger and the prompt stamp reads the mode record through the same reader, and
every fail-closed result counts there as a declared mode, so scope is live: a read failure (a file not valid
UTF-8 or a path not strict UTF-8 included) and a read text whose declaration or JSON marker is malformed or
unrecognized alike. The escape sentinel (default
`<state_dir>/ESCAPE-ALLOW-YIELD`) is operator-owned by enforced acceptance, not convention: it is
honoured only as a regular file (never a symlink), owned by a uid other than the assistant's
effective uid, and not group- or other-writable. A present sentinel failing any condition is ignored
(the decision proceeds exactly as with no sentinel), logged to guard-events and `escape-spoof.json` (a
failed write is warned about in the hook's banner, or in the block reason of a denied Stop or
TeammateIdle, and the warning asks for a manual record), and normally raised
once by the next resume audit (see the state-directory list above for when it is raised again or not
at all). Where operator and assistant share one uid, no file either can create passes,
so the clean-ALLOW escape channel is unavailable there: recovery is a differently-owned sentinel
(for example root-owned), registry or mode maintenance by the operator, or the bounded, marked exit;
split-uid deployment is the recommended posture.

`companion_stores` is an optional list of ABSOLUTE paths to git repo TOPLEVELS beside the session
repo that the write-scope guard (wrtscp) treats as SANCTIONED cross-repo write targets: the most
common case is the sole orchestrator's own durable store, which is by design a SECOND git repo next
to the code repo. A guarded Write/Edit/MultiEdit whose target resolves, by EXACT repo-root match
(the target's own resolved git toplevel EQUALS a declared store root, never a prefix or substring),
into a declared store is ALLOWED and AUDITED (a `guard-events.jsonl` row, kind `wrtscp`, decision
`allow`; where that append fails, the write is still allowed with a note saying the row could not be
written) instead of denied as an out-of-repo aiming error. It is honoured only when well-formed:
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
  `working-tree` or `not-a-review` target is allowed with a note and a `guard-events.jsonl` row; where
  that row cannot be written, the note says so (the allow stands).
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
cannot be located (a git directory separated from the main worktree) withholds every Bash call,
whether or not git can run. When git cannot say where the main worktree is (a failed or timed-out
probe), the raw `.git` and `commondir` files are read instead. A linked worktree of a bare repository
(`core.bare` true) has no main worktree and is not withheld, and a failed probe where `.git` is a
directory is no linked worktree, so a session with no registry anywhere is not withheld. Setting
`core.bare` to true in a separated git directory's configuration would end that withholding. A registry without a binding never ends the search: the session
repository's registry, its main worktree's, and every registry on the cwd's ancestors are read,
nearest first, and the first binding decides, so a registry of `{"version": 1}` checked out, written
or nested below an orchestrated tree cannot hide its binding. This hook reads each registry file on
its own, so a local `.aiqt/orchestration.local.json` without a binding cannot hide a binding in the
committed `.aiqt/orchestration.json`. In a bound session, a plain command that names a registry file
or the `.aiqt` directory is refused unless its command word only reads (`cat`, `head`, `tail`, `wc`,
`ls`, `stat`, `grep`, `jq`, `cmp`, `diff`). A command that changes the registry without naming it (a git
command that writes or removes work-tree files, a checkout from a subtree, the removal of a parent
directory, a script) is not predicted. Instead, when the hook first sees a registry of the session
repository, or of its main worktree, bind review dispatch, it records the binding (its digest, the binding
and the registry paths) in hook-owned state outside the work tree:
`GIT_COMMON_DIR/aiqt/review-dispatch-binding/KEY.json` in the repository's resolved common git directory,
KEY the sha256 of the worktree's identity there (`.` for the main worktree, `worktrees/NAME` for a linked
one) and the registry's path relative to that worktree's top level, never an absolute path, so a renamed or
moved repository keeps its record. It is written without following a symbolic link, mode 0600 in directories
of mode 0700. The common git directory is resolved once per check; where git cannot name it, every Bash call
is withheld. No allowlisted git subcommand writes there. In a bound session, a plain command other than a
read is refused when one of its words, resolved against the cwd as written and with every symbolic link
followed, is the resolved common git directory or lies inside it, whatever its spelling: a separated git
directory not named `.git` is protected like `.git`. A directory holding it is refused only to a command
that can delete, move or recursively rewrite it: `rm` with `-r`, `-R` or `-d`, `rmdir`, `mv` of it (or
`mv --exchange` into it), `chmod` in any form (a mode alone can cut every path to the git directory),
`cp -r` or `cp -a` of it, a `cp`, `mv` or `ln` whose written path merges into it (`x/.`, `-T`) or
resolves into the git directory, `opf` naming it, and an option word the coreutils option tables do not
model. A dispatch command may name a directory holding the git directory (`--workdir .`); one naming the
git directory or a path inside it is refused. Writing a new file or directory into it (`cp x .`,
`touch ./f`, `mkdir d`, `ln -s t ./l`) is allowed, and `git add .` is not refused, since git writes its own
directory through no pathspec. Where git cannot resolve the session repository, the git directory the
raw `.git` and `commondir` files name is protected, and one they cannot locate refuses every command but a
read. Each check reads each registry once: the record comparison and the enforcement use that one read, and
an own registry with a record is enforced with the recorded binding, so a registry removed during a check
cannot end it. While a record exists, a registry that is missing, unreadable, without a binding, or binding
differently from the record withholds every dispatch as UNVERIFIABLE, naming the change and the record, and
other plain commands are still judged under the recorded binding. An operator either restores the recorded binding or, outside
the session, removes the record (`rm "$(git rev-parse --git-common-dir)/aiqt/review-dispatch-binding/KEY.json"`,
the full path given in the refusal), and the next check records the binding then in force. A record that
cannot be read withholds every Bash call, and a binding that cannot be recorded withholds every dispatch.
Not caught: a process that removes both the registry and the record (an operator, a non-Bash tool, or a
command that reaches the record without naming it, such as an alias of the git directory through a bind
mount), and a registry changed before the hook first saw it bind, since a repository where no check ran
while it was bound has no record; where git cannot resolve the session repository, no record is read. Ordinary git commands (`add`,
`commit`, `checkout`, `restore`, `reset`) are allowed whatever the registry's git state; `git clean`,
`git am` and `git apply` are off the allowlist. When git cannot resolve the session
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

The escape sentinel opens the clean-ALLOW channel only as an operator-created artefact: a regular file with a single hard link, reached without following any symlinked path component, resolved under the state-dir anchor (an absolute or '..' path outside it is rejected), owned by a uid other than this guard's, and not group- or other-writable; any other present file is ignored, recorded (a failed record write is warned about in the hook's banner, or in the block reason of a denied Stop or TeammateIdle), and normally raised once at the next resume. Uid inequality proves not-self, never operator identity; component resolution is a best-effort lstat walk under the anchor, so a narrow window between the walk and the descriptor-bound stat remains.

The registry file is the confinement's trust root (a guardrail-config-integrity surface, review-gated where committed). The anti-shrinkage checkpoint's first window has no union to compare; an init marker distinguishes that genuine first window from a checkpoint deleted after a prior window (held as a possible reset), and the bound logs what it drops to guard-events, where a failed append is warned about in the hook's output, so a drop is never silent. A checkpoint or init marker that cannot be written is named in the hook's output (the deny reason and banner of a scheduling call, the block reason of a denied Stop or TeammateIdle, or the banner or note of an allow) whether or not its guard-events row is written; an unwritten checkpoint does not record that window's items, so one that then vanishes or is demoted before a later checkpoint write succeeds is not held as such unless an earlier checkpoint records it, and an unwritten init marker holds that window as cannot-evaluate. The checkpoint also records each open id's eligibility (granted-and-open vs proposed/ungranted), so a demotion of a previously-actionable id to proposed or ungranted with no closed receipt is held (cannot-evaluate) rather than silently routed to the harmless proposed bucket; that eligibility memory lives in the same actor-writable state dir, raising the bar without being categorical. Deleting the marker and the checkpoint together, like deleting the turn state to restart the loop bound, is the same actor-writable-state residual. The loop bound remains a deliberate, exhaustible, marked exit. Each denial and each forced exit appends its own guard-events row, and each forced exit also a row in an append-only forced-exit log, whose rows the next resume normally raises once (at least once if recording that they were raised fails). Neither record is guaranteed: a failed append leaves no row, and the hook then adds a short recording-failure warning to its output (the block reason of a denied Stop or TeammateIdle, which has no banner; the deny reason and the banner of a denied scheduling call; the banner of a forced exit) while the decision stands, so the loss is reported, not silent. The guard-events row of an ALLOW released by the operator escape is the only record that the override was used, so a failed append there adds the same kind of warning (in the banner of a Stop or TeammateIdle, in the note of a scheduling call) while the ALLOW stands. The row of a clean ALLOW with no escape is the over-fire metric only and is best effort: a failed append there is not reported. Host clock control defeats every freshness check here (out of threat model). The roster of yield primitives is fixed at generation and re-reconciled when the toolchain changes.
