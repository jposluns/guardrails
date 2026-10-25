# The orchestrator-integrity suite

Seven controls over one substrate: a stop-work guard (Stop, TeammateIdle, and the scheduled-yield
tools), a record-drift gate, a background-dispatch truncation guard, an unattended-ask blocker, a
resume audit with a mutation barrier, a lease-heartbeat keeper (PostToolUse and Stop, warn-only),
and a mistakes register. One registry declares the adopter's
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
  "lease": {"path": "path", "max_age_hours": 24, "holder_is_session_id": true,
            "heartbeat": {"mode": "warn", "format": "lines", "holder_field": "holder",
                          "field": "heartbeat_utc", "state_field": "state",
                          "active_values": ["active"], "refresh_after_minutes": 10,
                          "warn_after_minutes": 30, "lock_path": "path"}},
  "state_dir": "path",
  "yield_tools": ["ScheduleWakeup", "CronCreate"],
  "dispatch_tools": [],
  "review_dispatch": {"commands": ["orch-dispatch"], "brief_option": "--brief",
                      "labels": {"target": "Review-target:", "revision": "Reviewed-revision:",
                                 "path": "Review-path:", "repo": "Review-repo:",
                                 "branch": "Review-branch:", "draft": "Review-draft:",
                                 "skip": "Review-skip:"},
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
`backlog-checkpoint.json`, `attestations-validated.json`, `lease-heartbeat.json` (the
lease-heartbeat keeper's past-holder memory), and, when their events occur,
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
`forced-exit-surfaced.json`; NOT `lease-heartbeat.json`, whose keeper has its OWN writer,
described under "The lease heartbeat" below: that writer binds to the holder's held
state-directory descriptor, opened `O_RDONLY|O_DIRECTORY|O_NOFOLLOW`, so it needs read and
search permission on the state directory and refuses a symlink at the directory's own name; it
creates its exclusive temporary with mode 0600 and does NOT carry over the target's permission
bits; it REFUSES a non-regular, multi-link or lease-aliased replace target rather than
replacing a symlink; and it unlinks its own temporary only while the name still carries the
inode it created, disclosing one whose identity was never learned) are each saved by one writer: it opens the state directory once as a
directory descriptor (with `O_PATH` where the platform has it, so the open needs no permission on that
directory itself, only search permission on its ancestors, while the later stat, create, rename and cleanup
need search permission on it, the last three write permission too, and a write-and-search-only state
directory saves; where `O_PATH` is absent the fallback open also needs read permission on that directory)
and binds every later step (the read of the target's permission bits, the
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
its own save as succeeded. The save needs a writable and searchable state directory (and, where `O_PATH`
is absent, a readable one), so a writable `turn-state.json` in a
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
whose value begins with the `unattended` token arms the ask blocker's unattended deny; a recognized
attended-family mode does not disarm it: for a session within the live scope (the yield guard's gate, so
a co-located worker a holder-scoped lease does not name never inherits the global backlog; under such a
lease the judgement is off for every session where no holder can be confirmed, a lease file that cannot
be stat'ed, and, for a non-empty lease the session-id and holder-token checks run on (one classified
fresh or cannot-evaluate under a declared `max_age_hours`, or, with no horizon declared, every non-empty
lease whatever its mtime), a lease that cannot be read, a call that carries no session id or a lease
that names no session, while every other lease shape, an empty lease, a lease that declares no path (an
absent, empty or non-string path), a lease stale past a declared `max_age_hours` or a lease future-dated
past the tolerated clock skew under a declared usable `max_age_hours` (an unusable one is cannot-evaluate
whatever the mtime, so the holder check runs), skips the holder check and a live mode record then keeps
every session in scope, an id-less caller included, the judgement staying on there, erring toward
denying), where the
registry declares a backlog enumerator, a blocking question is judged against the same fresh enumeration
the yield guard uses (ctx kind `ask`, no wake-naming requirement, counted by its own
`ask_denials`/`ask_basis` pair in `turn-state.json`, which genuine human input resets exactly as it
resets the schedule pair) and is denied while actionable items are neither dispatched (waiting on a live
tracked task) nor blocked by a human decision recorded in the pending-decisions record; the deny names the
items and the two exits (dispatch them, or record them as blocked by this decision, then re-issue the
question) and is never cap-relieved, since both exits stay available (a backlog holding both an actionable
item and a cannot-evaluate item keeps that deny, which then also names the held items, so held work never
buys relief past work the enumeration still classifies as actionable; a proof-source failure that holds
every otherwise-actionable item, such as one schema-invalid dispatch-ledger line, an unreadable or
undeclared pending-decisions record, or a declared attestation register whose validated snapshot is
absent or stale, or an enumeration failure that hides items, such as a duplicate item id, leaves
nothing classified actionable, so the cap can still relieve such a shape; each relief attempts a
forced_unresolved record and a guard-events row, a failed write is reported in the released message,
and the relief still proceeds); only the cannot-evaluate denies (an
enumerator error, a declared non-object enumerator, or an unreadable item proof source) carry the schedule
path's three-denial cap on an unchanged basis, and only while no item is actionable, and the ask blocker's
manifest residue carries the full
contract and its residuals (a mode change after the question is asked, work the enumerator does not list,
and question tools other than AskUserQuestion stay out of its view). The reader fails CLOSED to the
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
unrecognized alike. A DECLARED lease `max_age_hours` that is not usable (a boolean, zero, negative, NaN,
infinite or a non-number) on a lease that would otherwise read live is a scope CANNOT-EVALUATE, never
unlimited freshness and never a silent guards-off: the stop, idle and yield guards route it into their
existing cannot-evaluate deny path (the loop bound, the schedule cap and the operator escape still
release), while the dispatch ledger and the prompt stamp keep recording and surface it as a note; a live
mode record outranks it, and a session the lease does not name stays a non-holder under it. This is a
CHANGE for adopters: the scope check previously read a `max_age_hours` of zero, a negative number, a
non-number such as the string `"24"`, `false` or NaN as no horizon at all (the lease never went stale),
`true` as a one-hour horizon and infinity as a horizon never reached; with such a value the stop, idle
and yield guards now deny through that cannot-evaluate path until it is corrected to a positive finite
number of hours. The escape sentinel (default
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
writes it, its first row with `--bootstrap`, which establishes the anchor later appends require,
and `tools/check_mistakes_register.py` gates it). It is validated at audit cadence (the
resume audit and `tools/orch_doctor.py --resume-audit`), never synchronously at yield time: the
validation pass writes `attestations-validated.json`, and the stop guard classifies an external or
foreign-lease blocker as blocked only when its ref is covered by a fresh validated row (the
evidence-freshness check still applies). Undeclared means the free-text evidence path stands,
byte-identical to prior behaviour; declared but with no readable fresh snapshot holds the affected
items (cannot-evaluate), never blocked and never actionable.

## The lease heartbeat

The incident class this keeper exists for: a live session's lease heartbeat goes stale because
refresh is manual, after which the scope check reads the lease as dead and every lease-scoped guard
silently goes out of scope. Two warn-only controls, `orch-lease-heartbeat-post` (PostToolUse,
matcher `.*`) and `orch-lease-heartbeat-stop` (Stop), run one core (two controls because the
generator requires a distinct handler per control). Both are inert unless the registry's `lease`
declares `holder_is_session_id` and a valid `heartbeat` block (see the registry schema above); the
registry is the confinement's trust root, so the opt-in, and the wider `refresh` mode, is an
operator act there, and an invalid block never writes, never falls back to another mode, and is
surfaced (a Stop warning and a resume-audit finding; never a guard-events row or a memory save:
no descriptor proof exists before the first open, and a bookkeeping writer with no held
descriptor withholds).

- Declaration. `mode` is `warn` (never writes the lease; bookkeeping rows and saves still
  run, through the same held-descriptor discipline as every other bookkeeping write) or
  `refresh`. `format` is `lines` (one `key: value`
  per PHYSICAL line, which ends only at a line feed, a carriage return directly before it being part
  of the line ending, so a Unicode separator such as U+2028, a form feed or a lone carriage return
  inside a value never starts a new line, and a declared field's value carrying a carriage return
  is a cannot-evaluate: a lease whose lines end with a lone carriage return is ONE physical line,
  so it is a cannot-evaluate, never a holder read as foreign; prose and other lines are allowed, so
  the lease may share a file with the mode record) or `json` (a top-level object, duplicate keys
  rejected). `holder_field`
  names the holder;
  `field` (optional) names the heartbeat timestamp, a UTC time ending `Z` or `+00:00`; `state_field`
  and `active_values` (together or not at all) gate on a lease state; `refresh_after_minutes` <
  `warn_after_minutes`, and `warn_after_minutes` stays at least two clock skews (600 s) under
  `max_age_hours`; the declared lease path and `lock_path` must be USABLE path values, judged on
  the strings alone BEFORE any path resolution (an embedded NUL, or an empty component from a
  doubled, leading or trailing separator, is the named invalid-declaration refusal on Stop,
  never a late resolution fault or a silent allow); `lock_path` (optional) names an existing flock
  file taken BEFORE the lease is read and held across the whole read-prove-write span, so
  every proof holds inside its critical section (busy: that event refreshes nothing; a later
  one does); the lock must be a STABLE, SEPARATE coordination file NO writer of
  this plugin ever replaces, so a `lock_path` that names, or resolves to the same identity as,
  the declared lease or ANY file a whole-file writer of this plugin replaces (the past-holder
  memory, the guard-events log, every `_orch_write_json_atomic` target such as
  `turn-state.json` and `backlog-checkpoint.json`, and anything else inside the state
  directory) is refused by ONE shared read-only validation, run on paths and destinations
  resolved from the authoritative root at declaration validation, at the resume audit and at
  the entry check (resolved path, COMPONENT-aware resolved-path containment in the state
  directory, so a state directory of `/` contains every lock and a sibling that merely shares
  the directory's textual prefix is not inside it, parent
  identity and inode, so an alternate or relative spelling that resolves to a refused target
  refuses too; a stat fault other than absence, or a containment comparison fault, is the
  named refusal, and nothing is created),
  and AGAIN by
  descriptor identity under the held lock, where every state-directory entry is also swept by
  fstat identity against the held lock descriptor (flock attaches to the open file description, so a
  keeper whose own save or refresh replaced its lock file would leave the next cooperating
  keeper locking the replacement while this one still holds the orphaned inode, voiding the
  critical section); once held, the lease is RE-OPENED under it, so a cooperating takeover
  between the first open and the lock reads as "lease taken" or "lease lost", never as a
  cannot-evaluate, and a flock fault other than busy (ENOLCK, EINVAL...) is a named refusal
  surfaced on Stop with the fault named exactly ONCE per turn end (a due refresh's banner
  names the fault and carries the bookkeeping consequence, with the deferral dropped rather
  than rendered a second time), never a silent skip.
- Holder proof. The payload `session_id` must EXACTLY equal the one parsed holder field: a
  whole-token presence test is never used, because a decoy line such as `previous_holder: <sid>`
  passes it. The lease must be a single-link regular file owned by the effective uid, at most
  64 KiB, strict UTF-8, reached by a component-wise no-follow descriptor walk (`..` refused, a
  symlink at any component refuses, ancestors included).
- Decision. The lease age is the OLDER of the file mtime age and the declared heartbeat field's
  age, so bumping only the mtime cannot hide a stale recorded liveness value. Inside
  `refresh_after_minutes` nothing happens (idempotent). Every event first pays the
  git-toplevel resolution (one subprocess), a whole-registry read (the registry file is
  read to EOF; that read is not byte-bounded, and this keeper does not change it; the keeper's
  bookkeeping destinations are derived once from that same read, never by a second read), and
  the lease/bookkeeping separation proof (path resolutions of the lease and of both bookkeeping
  destinations, one stat of the lease, up to one stat per destination and up to two
  parent-directory stats; an absent state directory is created here, so the parent-identity leg
  always compares real identities) before any lease check, and where `lock_path` is declared
  the shared lock validation runs TWICE per event before the quick path (once inside the
  declaration validation and once at the entry check, after the separation proof may have
  created the state directory; each run is up to ten path resolutions and up to eleven stats:
  the lock, the lease, both bookkeeping destinations, the five whole-file-writer targets, the
  state directory and the lock's parent directory); an event that reaches the lease open
  re-proves the separation on the opened descriptor (one fstat plus up to one stat per
  destination, one state-directory open and two fstats); on PostToolUse a healthy lease then
  adds one
  `lstat` where no `field` is declared
  (the quick mtime path, which a declared `field` disables: a fresh mtime cannot prove the
  field's own recorded time fresh, so there the bounded walk-and-read runs on each call).
  In `refresh` mode a due refresh touches the mtime (no declared `field`: a clock read on
  the open descriptor, no content change, and no permission bit changed, so the touch still runs
  on a lease carrying a setuid, setgid or sticky bit) or rewrites ONLY the declared field's value
  (the field write alone is refused, with nothing written, for a lease carrying a setuid, setgid
  or sticky bit, which a replace cannot carry over faithfully; otherwise an exclusive
  temporary file beside the lease, given the lease's group before its first byte (on Linux a
  new file starts in the creating process's effective group, or in the directory's group where
  the directory is setgid or the filesystem is mounted with grpid, so it need not be the
  lease's; a failure to set it is the named no-write refusal, with nothing published, so where
  it always fails, for example for a process without CAP_CHOWN whose effective and
  supplementary groups do not include the lease's group, writing where a new file does not
  start in that group, or on a filesystem that refuses the change, EVERY field refresh is
  refused: Stop warns of that refusal only while the lease is still eligible for a refresh,
  and once the lease is inside two clock skews of its staleness horizon Stop warns instead that
  it is too close to renew, then, once stale, that it has lapsed), the original permission bits
  in full (a failure to set them is the same named no-write refusal), fsync,
  an identity re-check against the proof fstat, a descriptor-anchored replace, a directory fsync
  whose failure is a write that HAPPENED but may not be durable, never plain success: reported
  as the Stop warning, or on PostToolUse as the result's error text, with an attempted
  guard-events row (best-effort: a failed append is reported through the withheld-bookkeeping
  warning instead), and
  a post-replace verification whose failure is reported as a write that HAPPENED, on both
  events alike (the Stop warning, or on PostToolUse the result's error text, with an attempted
  guard-events row); the failure
  cleanup runs from a finally for ANY exception class and unlinks the temporary name only when
  one stat just before the unlink finds it still carrying the inode the write created, so an
  entry whose ownership is unestablished at that stat is never removed (the unlink itself is by
  name, and POSIX offers no unlink-if-inode: an entry swapped onto the name between that stat and
  the unlink is removed in its place, a window of the same-user concurrency class below), and a
  temporary that may remain (a failed unlink, an identity never learned, a
  cleanup stat fault) is disclosed by name (save for the residuals below): in the refusal, or
  in the withheld report while an
  exception of any other class is propagating, the channel chosen by that propagation and
  never by whether a failure was named, so a named refusal followed by an interrupting close
  fault still discloses through the withheld report, after the named failure; a fault of any
  other class raised inside the cleanup itself, at its stat or its unlink, names the temporary
  in the withheld report before it propagates (with the same residuals), even when that
  fault's own text cannot be
  rendered (the fixed disclosure is formatted before the temporary is created and recorded
  before that text is rendered, and the rendered text replaces only that very record, bound
  by its position and identity, never by its value), and the first fault wins, except that a
  stop raised there, while rendering that fault's text, or while that record is built,
  recorded or replaced (an interrupt or an exit, not an `Exception`), propagates ahead of an
  `Exception` already propagating and never displaces a stop already propagating; where that
  record cannot be built (a fault of either kind while building it), a preformatted record
  naming the temporary is recorded in its place, and where the refusal's detail cannot be
  built because of an ordinary `Exception`, the refusal carries the preformatted fallback
  TEXT naming the temporary in its place (a stop raised there is the detail residual below,
  and then no refusal is returned). Disclosed
  residuals: a stop raised while a withheld record naming the temporary is itself appended,
  before that append has taken effect, propagates, ordered as above, WITHOUT the disclosure
  (nothing is recorded, and no
  preformatted record can be, because recording one needs the very append that stopped), and
  so does a stop raised while the refusal's detail is built (no fault already propagating:
  the refusal is never returned); this
  order holds for faults raised inside those guards; memory exhaustion so
  severe that a fixed-size step fails (a call, the append that records the entry) can lose
  the record, and a stop delivered as a signal at an instant outside the guards leaves the
  cleanup at that point, unordered). The new
  value is the event's own clock read, never a recalled value, keeping the old value's `Z` or
  `+00:00` suffix; the `json` form replaces only the declared field's value text in place and
  never re-serializes the object (which would rewrite other members: `1e400` as `Infinity`, a long
  decimal as its shortest float, non-ASCII text as escapes), and it refuses the write, with nothing
  written, unless the re-parsed result carries the same members in the same order, every other
  member's value text byte-identical and its parsed value exactly equal.
- Never. It never creates, seizes, revives or releases a lease, and never renews a LAPSED one
  (refreshing a live, proven-held lease in the declared refresh mode is the feature): an
  absent lease is never
  created (a past holder is told "lease lost"), a foreign holder is never touched (a past holder is
  told "lease taken", anyone else sees silence), a non-active state is never revived, a stale lease
  is never renewed ("lapsed: re-acquire through the acquire step"), a future-dated time is left in
  place as a record of the clock skew or tampering, a lease inside two clock skews of its staleness horizon is too close to renew
  safely, and a git-tracked lease (one outside the repository work tree cannot be tracked
  there, so the tracking gate passes it: outside-ness is decided on RESOLVED paths, never a
  lexical spelling) or a protected path (a `.git`, `.aiqt` or `.claude`
  component, `plugin/`, anywhere inside the state directory's or another protected
  directory's subtree, judged by ancestor-identity containment and never by enumerating a
  directory's immediate children, or an inode alias of a registry file) is refused IN BOTH
  refresh forms, the mtime touch included (there the mtime IS the recorded liveness value, so
  a touch changes lease semantics even with no content byte changed); a
  protection check that cannot be evaluated refuses too. The keeper's bookkeeping is
  descriptor-bound at the WRITE itself: both destinations (the past-holder memory and the
  guard-events log) are resolved ONCE per event from the same registry read and carried, with
  the HELD lease descriptor the separation proof was made on, the lease parent-directory
  descriptor it was opened under, and a state-directory descriptor opened ONCE
  (`O_DIRECTORY|O_NOFOLLOW`) when the proof passed (building the holder is refused outright
  when the state directory and the lease's parent are the same directory by fstat identity),
  as ONE per-event holder that
  EVERY bookkeeping writer consumes (the row append, the event append, the memory save, the
  tracking backoff save and the crash recorder: none re-reads anything, so a registry re-read
  failing or changing mid-event can no longer redirect any writer onto a destination the proof
  never examined, and none writes without the holder's held descriptor). They are checked
  DISTINCT from the declared lease at the entry (by resolved path, by inode, and by parent
  identity: a lease directly in the state directory is refused outright, so an alias spelling
  realpath cannot fold can never let a first append create the lease's own file; a lease path
  that IS the state directory or an ANCESTOR of it is refused BEFORE that directory would be
  created, with nothing created; otherwise an absent state directory is created first,
  idempotently under concurrent creation, so the parent-identity leg always has a real identity
  to compare, and a creation fault is the named cannot-evaluate whose remediation names the
  state-record fault while a faulted lease access names the lease path, each chosen from the
  fault itself, never from a substring of the detail) and AGAIN AT EVERY WRITE: each append
  opens its destination by NAME relative to the held state-directory descriptor (`dir_fd`)
  with `O_WRONLY|O_APPEND|O_CREAT|O_NOFOLLOW|O_NONBLOCK|O_NOCTTY` (an absent log is created; a
  planted symlink is refused, never followed; a FIFO, with or without a reader, returns
  promptly and is refused on the descriptor's own stat, never a blocked hook), fstats the very
  descriptor it writes through, and writes only a regular file with exactly one hard link
  whose identity differs from the held lease descriptor's AND from an lstat of the lease name
  taken relative to the held lease parent-directory descriptor AFTER the destination fstat;
  the memory and backoff saves create their temporary file exclusively, check the temporary
  DESCRIPTOR before its first byte (a regular file with exactly one hard link, distinct from
  the held lease descriptor and from a fresh lstat of the lease name, so a temporary renamed
  onto the lease name, hard-linked or unlinked before the first write is refused with nothing
  written through the descriptor) and replace relative to
  that same held state-directory descriptor on both sides (`src_dir_fd` and `dst_dir_fd`),
  after the same identity checks on the replace target and an identity re-check of the
  temporary NAME immediately before the replace (a re-pointed temporary name publishes
  nothing, and the failure cleanup unlinks the temporary name only when one stat just before
  the unlink finds it still carrying the inode the save created, with the same by-name window as
  the field write; a temporary that may remain is named in the withheld report, an
  interrupted cleanup included, before any fault propagates (save for the residuals below),
  even when that fault's own text
  cannot be rendered (the fixed disclosure is formatted before the temporary is created and
  recorded before that text is rendered, and the rendered text replaces only that very
  record), and the first fault wins, except that a stop raised in the cleanup, while
  rendering its fault's text, or while that record is built, recorded or replaced,
  propagates ahead of an `Exception` already propagating and never displaces a stop already
  propagating, with the same disclosed residuals as the field write: a stop raised while a
  withheld record naming the temporary is itself appended, before that append has taken
  effect, propagates, ordered the same way, without the disclosure; memory
  exhaustion at a fixed-size step can lose the record; and a stop delivered as a signal
  outside those guards leaves the cleanup at that point, unordered); a refused or FAILED write is withheld
  and reported through the hook result (the Stop banner, or the result's error text, which the
  dispatcher writes to stderr), never into a file. The write authorization IS the held descriptor: opening the lease, taking the
  declared lock and re-opening under it each EMPTY the holder, only the separation re-proof on
  the descriptor then in hand (at the first open, and again on the re-open under a declared
  lock) fills it, and cleanup empties it BEFORE any descriptor or lock is released (each
  terminal cleanup attempts EVERY remaining close, the lock's last, even when an earlier close
  raises any exception class, never retries the faulted one, and then propagates the FIRST
  such fault), so a branch
  holding no proven descriptor writes and records NOTHING (the
  BUSY- and FAULTED-lock branches, the no-holder branches that still run the decision table,
  record their bookkeeping refusal as a deferral surfaced on Stop ONLY, at most once per turn
  end, the faulted-lock deferral NAMING the fault, rendered once (a due refresh's Stop banner
  names the fault and the deferral is dropped), never a report on every PostToolUse call, and
  a deferral made on a PostToolUse event is DROPPED with that event's holder, so a core fault
  deferred there surfaces only where the Stop event reproduces it, a disclosed narrowing;
  every OTHER no-holder refusal or core fault rides the hook result of the event it happened
  on, PostToolUse included, as the result's error text, surfaced AS the named fault, never
  folded into a lock deferral): the entry
  path before the first open, an invalid declaration, a failed first open, a failed under-lock
  re-open, a busy or faulted lock, a crash before the proof or after cleanup, and an unknown or
  unusable lease path all withhold every row and save (a refusal never records into the refused
  lease; the hook result is the only surfacing: the Stop warning, joined there by any deferral
  or withheld report, or the result's error text on the event the refusal happened, a
  disclosed narrowing of the guard-events
  record on exactly those branches), while a core fault inside the judged table records its one
  crash row from within the critical section, before descriptors or lock are released, on the
  branches where the proven descriptor is held. Every
  other cannot-evaluate is a named no-write warning on Stop plus an attempted (best-effort)
  guard-events row, never a
  write; a refusal that persists unchanged is recorded once per state change,
  never once per call, and its git tracking probe is backed off until the lease identity
  changes or the backoff elapses.
- Fail direction. Neither binding ever exits 2, blocks or asks: each returns exit 0, and only a
  stop raised inside the core (an interrupt or an exit, not an `Exception`) propagates past the
  binding, where it can end the hook process with another non-zero status, never 2: each
  binding re-raises an exit whose status would be 2 (an integer code congruent to 2 modulo 256
  within the signed 64-bit range the interpreter converts it through, so 258 as well) as an exit
  with status 1, and every other stop propagates unchanged; PostToolUse surfaces no
  message (a withheld or failed bookkeeping write, a written-unverified refresh and a
  written-not-durable refresh ride the result's error text to the dispatcher's stderr); the
  bindings catch the core's own faults (a
  fault inside the judged table becomes one guard-events row ONLY on the branches where the
  proven descriptor is held,
  recorded while it is held; on a busy or faulted lock the table runs with no held descriptor,
  so a fault there records no row and joins the Stop-only deferrals, dropped with a
  PostToolUse event's holder; a fault outside the table records no row and is surfaced AS the
  named core fault through the result of its own event; none becomes a per-tool-call banner);
  and the
  keeper never changes another guard's decision (a refresh only keeps a fresh lease fresh, so its
  order against the stop guard on Stop does not matter).
- Residuals. A session that makes no tool call and ends no turn produces no heartbeat (staleness is
  then the correct outcome; a long foreground tool call delays the refresh by its own length). Two
  processes sharing one `session_id` both refresh, undetected. The declared lock is taken
  before the lease is read, so every writer that takes that lock is serialized against the
  whole read-prove-write span; a writer that IGNORES the lock, or any writer where none is
  declared, can still race the re-check-to-replace window, where an unlinked lease is
  revived by the replace or a swapped-in acquire is overwritten: the identity re-check
  narrows that window (it cannot remove it), and the post-replace verification does not report
  either case (it reads the replace's own content, which carries this session's refreshed
  value, so the revived lease or the overwritten acquire reads as a clean refresh; it can
  report a change that lands between the replace and its own re-read, or a verification fault,
  where the refreshed lease cannot be re-opened, re-read or re-parsed, and it can undo
  nothing). Bash can still rewrite
  the lease or the registry. The past-holder memory
  (`lease-heartbeat.json`) and the guard-events append are best-effort, and every bookkeeping
  open, lstat, exclusive temporary create and replace runs RELATIVE to the held
  state-directory and lease parent-directory descriptors, with the saves' temporary descriptor
  checked before its first byte, the temporary name re-checked immediately before the
  replace, and the failure cleanup unlinking the temporary only when one stat just before the
  unlink finds its name still carrying the inode the save created, running from a finally for
  ANY exception class (one whose
  identity was never learned, because the pre-write stat itself faulted, is disclosed in the
  report and never unlinked, and a cleanup stat fault other than confirmed absence also never
  unlinks and discloses the name and the fault, save for the residuals stated with the memory
  save above: a stop raised while that withheld record is itself appended, before the append
  has taken effect, propagates without it, memory exhaustion at a fixed-size step can lose
  it, and a stop delivered as a signal outside the guards leaves the cleanup unordered; the
  field write's own residuals, the stop raised while its refusal detail is built included,
  are stated with it above). The DISCLOSED residual of
  the bookkeeping is the SAME-USER CONCURRENCY CLASS, stated
  here once and completely (it subsumes every narrower residual sentence of earlier rounds): a
  concurrently running process of the same user can rename, re-point, link or replace ANY entry
  between ANY check and its use (the state directory, the lease's parent directory, the lease,
  a bookkeeping destination, a temporary file), so a row can land at a declared path (the suite
  keeps a disclosed-residual WITNESS vector that re-points the lease's parent directory at the
  state directory between the destination open and the identity read and shows the row landing
  at the declared lease path) or a bookkeeping write can be lost, and the failure cleanup of a
  field write or a save, whose unlink is by NAME after one identity stat because POSIX offers no
  unlink-if-inode, removes an entry swapped onto the temporary name between that stat and the
  unlink in its place, leaving the moved temporary undisclosed (the suite keeps a WITNESS vector
  for this window too); the checks cover ACCIDENTAL
  mis-targeting and a stale or replaced lease, and they do not guarantee a boundary against a
  same-user concurrent actor, which needs OS isolation (distinct users, or a state directory
  and lease directory private to this keeper). A refused append can still have CREATED an empty destination file (the
  `O_CREAT` side effect of the append open); no row byte lands anywhere.
  Extended attributes and
  ACLs are not carried over by a field write, and the final parent directory needs read permission
  for the fsync. A fresh heartbeat proves only liveness, never progress: it must never count as
  activity for yield decisions. Whether the platform fires `.*` PostToolUse matchers everywhere,
  and whether `session_id` is stable across resume, compaction and subagents, are deployment checks
  before enabling `refresh` (ship `warn` first, read the guard-events rows, then widen).

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
- `labels` names the five brief labels, and optionally two more, `draft` and `skip`, declared
  together or not at all, which turn on the worker draft census below. They must be distinct.
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
- `Review-draft:` (optional, only where the binding declares the `draft` label) is the absolute path
  of the worker draft the commit applies, once.
- `Review-skip:` lines (only with the `skip` label) each name one drafted path deliberately left out
  of the commit, kept exactly as written.

For a `revision` target, the hook checks the following in order. The first failure decides.

1. The pin resolves to exactly that commit. A tag object id is not accepted.
2. The authority prints the same commit id.
3. Every parent named in the raw commit is present.
4. Where the binding declares the draft labels, the worker draft census below passes: `Review-skip:`
   lines in a brief with no `Review-draft:` line are refused, and where the brief names a draft, the
   paths the draft's headers name, less the skipped ones, join the declared paths.
5. The commit's changed set equals the declared paths. The base is the sole parent, the first parent
   of a merge, or the empty tree for a root commit.
6. No declared path is staged against the pin, and the working tree matches the pin at every
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

### The worker draft census

A hand-written `Review-path:` list is checked against the commit, so a drafted file that the apply
left out, and that the list leaves out too, shows no divergence. Where the binding declares the
`draft` and `skip` labels, a revision brief can name the worker draft the commit applies, and the
declared review set is then built from the draft's own diff headers, less the paths the brief
records as skipped, together with any `Review-path:` lines (for paths the commit changes beyond the
draft). A drafted path that is neither committed nor recorded as skipped is refused, and the refusal
names it as drafted. The hook reads the draft once, by its absolute path, under `max_brief_bytes`,
split on LF only, as `git apply` reads a patch. Unlike a brief, a draft under `/proc` or `/dev` (such
as `/dev/shm`) is not withheld, since the census is made from what the hook itself reads there.

The draft is a git diff (`git diff`, `git diff --cached` or `git format-patch` output). The census
reads it conservatively, as the diff output git writes by default, so some forms that `git apply`
accepts but git does not write are refused rather than read: a localized no-newline marker, a context
marker that only added lines follow, an added-line marker that more added lines follow, and a line
beginning with a backslash, other than git's exact marker, right after a hunk's counted lines or
their marker. Not every such form is refused: an added-line marker that a removed line follows and an
empty line read as context are read. After a hunk's counted lines, git's exact no-newline marker is
read as the marker; any other line beginning with a backslash there is refused; a hunk header, a
`diff --git` line or a `---` line naming a path goes on to the next hunk or section; a `diff --cc`
or `diff --combined` line is refused as a combined diff; a line that begins as another diff header is
refused as outside a file section; a `Content-Transfer-Encoding:` line naming an encoding other than
7bit or 8bit is refused as a transfer-encoded mailbox part; a `Submodule` line that shows a
submodule's own changes inline (as `--submodule=log` or `--submodule=diff` writes it) is refused as a
submodule shown inline; and any other line is read as prose. Whatever line follows it, each hunk is
consumed by the line counts in its own header, so no path is hidden.
A file section opens at a `diff --git` line, or at a `---` line naming a path, which a `+++` line
and a hunk header must follow. Each hunk is consumed by the line counts in its own header, so an
added line that reads `++ b/x` (shown as `+++ b/x`) is content, not a header. Git's exact
`\ No newline at end of file` marker is read only where git writes one, after the last line of each
side of the file it marks. A marker after a removed line marks the old side, after an added line the
new side, and after a context line both. So a marker is read only where the hunk header's count for
each side it marks is met, and only in the section's last hunk. A line after a hunk's counted lines
and its marker is not part of the hunk: as `git apply` reads it, it is prose unless it opens a section
or begins as a diff header or a marker does, so a hunk with extra lines after its counted ones is not
refused for them. The exception is a line right after the counted lines, or right after their
marker, that begins with a backslash, with or without a space after it: git writes nothing there but
its exact marker, so any other such line is refused. A section completes with a hunk, a binary
marker (`Binary files ... differ` or `GIT binary patch`), or a creation, deletion, mode change,
rename or copy header. A section names at most one old path (its `---`, `rename from` and
`copy from` headers) and one new path (its `+++`, `rename to` and `copy to` headers), and its
`diff --git` line names exactly those, `a/` then `b/`, as git writes them from one pair. A rename
names both of its paths (`rename from` and `rename to`), a copy only its destination (`copy to`), a
deletion or a creation the one path that is not `/dev/null`, and a section with no path header (a
mode change, an empty file, a binary change) the one path its `diff --git` line names twice. C-quoted
names are decoded, and a name is read up to a tab.

Outside a section, a line is prose and is skipped unless it begins as a diff header or a no-newline
marker does. Prose covers what git writes around its sections (the mailbox headers and the commit
message, the `---` separator, alone or with trailing blanks, the diffstat and its summary, the `-- `
signature and the version line after it, and a binary patch's encoded lines) and blank or indented
lines. A line that begins as a diff header or a marker does is not prose, wherever it stands (after a
section, at the end, or before prose), since a section cut short or mangled would otherwise drop out
of the census unseen: a `+++` line, a hunk header, an extended header such as `index` or
`rename from` (including the older `rename old` and `rename new`), a binary marker, a line beginning
with a backslash and a space, and a `diff -` line other than `diff --git` (besides it, git writes
only `diff --cc` and `diff --combined`, for a merge, which are refused as combined diffs, so
`diff -ruN` output is refused too). A `---` line naming a path always opens a section.

Among a hunk's counted lines, `git apply` reads a line beginning with a backslash and a space as a
no-newline marker when it is at least eleven bytes long (counted in bytes before the line feed, so a
short line of non-ASCII text can reach it) and rejects a shorter one as a corrupt patch; right after
a hunk's counted lines it accepts any such line, reading one of eleven bytes or more as a marker and
passing over a shorter one. The census refuses every such line other than git's exact marker in both
places.

- More than one `Review-draft:` line, a relative draft path, a draft that cannot be read (missing, a
  symlink, not a regular file, over the cap, not UTF-8, a NUL), an empty `Review-skip:` value, and a
  draft that names no file in a diff header are withheld as `UNVERIFIABLE:`, with a reason naming the
  draft and the cause.
- A draft the census cannot read is withheld as `UNVERIFIABLE:`, with a reason naming the cause: a
  combined diff (`diff --cc` or `diff --combined`), a header path without git's default `a/` or `b/`
  prefix in its place (written with `--no-prefix`, `diff.noprefix`, `diff.mnemonicPrefix` or custom
  prefixes, or by `git diff -R`, which swaps them), a `---` line naming a path with no `+++` line after
  it (the `---` line of a context-format `diff -c` section among them), a `+++` line with no hunk after
  it, a section that ends with no hunk, binary marker, or creation, deletion, mode, rename or copy
  header (a draft cut short inside a section), a rename, copy or mode header without its pair, a
  `diff --git` line naming two paths with no rename or copy header, a section whose `---`, `+++`,
  rename or copy headers name two old or two new paths, or other paths than its `diff --git` line
  names (a mangled or hand-edited section, which is refused rather than counted by one of its
  headers), an unreadable quoted name, a hunk header that cannot be read, a hunk that ends before its
  header's counts are met or holds more lines for one side than its header counts while the other
  side's count is not yet met, a hunk line beginning with a backslash that is not git's exact
  `\ No newline at end of file` marker after a context, removed or added line, a marker before the
  last line its hunk header counts for a side it marks (such as a marker after a removed line that
  another removed line follows, or one after a context line in the middle of a hunk, as `git diff -w`,
  `-b` or `--ignore-space-at-eol` can write), a hunk after a marker in its section, any other line
  beginning with a backslash right after a hunk's counted lines or their marker, and a diff header or
  marker line outside a section. Where a `diff --git` line names its paths under `b/` and then `a/`, as
  `git diff -R` writes it, in a section with no `---` line (a mode change, a binary change, an empty
  file, a pure rename or a pure copy), the reason names that swap; a section with a `---` line, as most
  `git diff -R` output has, is refused first with the reason that its `---` or `+++` line names no
  path under git's prefix for its side.
- A header or hunk line that ends in a carriage return before its LF is withheld as `UNVERIFIABLE:`,
  with a reason naming CRLF, since a header's names would end in the return. This covers a draft
  saved with CRLF line ends, which must be written again with LF line ends, and a draft holding a
  hunk of a file whose own lines end in CRLF, which is withheld however it is saved.
- A mailbox part whose `Content-Transfer-Encoding` is other than `7bit` or `8bit` (base64,
  quoted-printable) is withheld as `UNVERIFIABLE:`, since `git am` decodes and applies the file
  headers it holds, which a line reader cannot see.
- A submodule shown inline by `git diff --submodule=log` or `--submodule=diff` (a
  `Submodule <path> <old>..<new>` line, whose own changes follow under the submodule's path) is
  withheld as `UNVERIFIABLE:`, with a reason naming `--submodule=short`, the default, which shows the
  submodule as its one gitlink section.
- A `Review-skip:` line that names a path the draft does not name, or a path the commit changes, is
  refused, and so are `Review-skip:` lines in a brief with no `Review-draft:` line.

The census applies to a `revision` target only. It reads paths, not content: a draft that names a file
only in prose, a partial apply inside one file (a hunk dropped from a file the commit still changes),
and a section indented from column 0 (which GNU `patch` reads and `git apply` does not), which is read
as prose, are not caught, and neither is an apply whose brief does not name its draft. The census
reads the draft the brief names, and nothing proves that draft is the one the commit applied: a
trimmed copy, or a draft cut short between two complete sections, reads as a complete draft of fewer
files. A commit message line in a mailbox draft that begins as a diff header or a no-newline marker
does (such as one starting `index ` or `\ `), that reads as an inline submodule header (such as
`Submodule sub 1234567..89abcde: bumped`), or that reads as a `Content-Transfer-Encoding:` header with
a value other than `7bit` or `8bit` is refused with the rest, which can withhold a correct draft:
telling a message line from a part header or a section's line needs the mailbox structure, which a
draft can mangle, so the census refuses such a line wherever it stands.

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
- A path the change needs that is missing from both the commit and the declaration is caught only
  where the brief names the worker draft that drafts it; the census reads the draft's diff headers,
  so a file the draft names only in prose and a partial apply inside one file are not caught. The
  census reads the draft the brief names, which is not proven to be the draft the commit applied.
  A draft with CRLF line ends, or one holding a hunk of a CRLF file, is withheld as `UNVERIFIABLE:`
  rather than censused.

## Review holds

The optional review-hold markers turn on the review workdir hold hook (`review-workdir-hold`, rule
rvwpin): once a review is dispatched against a worktree, that worktree is not modified until the
review returns or is abandoned. The markers are adopter-provided: the pack ships the hook, the
dispatcher writes the holds. Without a marker directory the hook is inert (a silent allow), so
adopters who write no markers see nothing, as long as the payload carries an absolute `cwd`: a
`cwd` that is missing, relative, carries a NUL byte or names no directory that exists hides the
session's repository, so every git
command outside the read-only grammar, and an absent command, is withheld as `UNVERIFIABLE`
(fail closed), in the standalone preflight as in the composed hook. A consulted directory whose
git identity probe cannot answer (git could not be run, timed out, ran past the decision deadline,
exited nonzero or on a signal with anything but git's specific not-a-git-repository answer, a
dubious-ownership fatal included, or exited 0 with a reply that is not EXACTLY the expected two
newline-terminated path
lines with an absolute first line, empty, one line, carrying extra lines, missing its final
newline, a relative first line or an echoed option, or answered a `core.worktree` lookup with
neither the set nor the unset reply or ambiguously: an empty value, a value missing git's final
newline, or the unset exit carrying output; a configured `core.worktree` is otherwise kept
byte-exact apart from that final newline) fails closed the same
way: the probe failure is never
read as "not a
repository", which would hide a live hold there, while git's specific answer that a directory is
in no repository, matched at the start of one of the probe's own C-locale stderr lines (never as
a substring a path in the message could echo),
stays the inert allow. A hold-set candidate path is resolved PHYSICALLY first (`os.path.realpath`,
so a `..` component and every symlink are applied the way the kernel would apply them, never
stripped textually) and one that names no existing directory is then consulted
at its NEAREST EXISTING ancestor, so a directory the command itself creates first, a `..`
spelling through it, and a write
operand's file path, still read the markers of the repository each lands in; a candidate whose
existence cannot be read at all is probed itself and the probe failure fails closed, and a
PROCESS-RELATIVE candidate in any structural position (an operand, an option value, a `-C`, `cd`
or `pushd` target, a `--git-dir` or `--work-tree` value, a `GIT_` assignment, an `ln` operand:
`/proc/self/...`, `/proc/<pid>/...`, `/dev/fd/...`, judged on its literal components with a run
of leading slashes collapsed first, so `//proc` is read as `/proc`) cannot be resolved by the
hook at all, since its meaning depends on the process that opens it, so it counts as held and
the command is withheld outright (the read-only grammar still passes such a read). An `ln`
segment carrying any option word other than `-s`, `-r`, `-sr`, `-rs`, `--symbolic` or
`--relative` (another cluster such as `-sf`, `-t`/`--target-directory`, `-T`, `-n` or an
unknown option) leaves where that link lands or points unmodelled, so while a hold is live a
git command in the same Bash call is withheld. Its operands are still consulted under every
reading the modelled forms make at once (the target at the directory the segment runs from and
at the link's directory, and the link recorded for the rewrite even over an existing directory,
since `-n` and `-T` read a link name that points to a directory as the name itself, not as a
directory to enter), so a hold any of those readings reaches is seen; with no live hold in any consulted repository the decision is the inert allow, unchanged
for adopters who write no markers. Create the link in a separate command first. A git too
old to answer the probe's options echoes them back
on exit 0, so it fails closed the same way (it was previously read as inert). A marker-directory
path that exists but does not lead to a directory (a dangling `review-holds` or `aiqt` symlink)
is malformed marker state, never absence. There is no
host-wide view of
holds: the hook reads only the markers of the repositories a command names or sits in.

One marker file per live review lives in `GIT_COMMON_DIR/aiqt/review-holds/<dispatch-id>.json`,
beside the hook-owned `review-dispatch-binding/` records. Every linked worktree of a repository
shares that directory, so the hook finds a hold from the command's target alone and needs no
orchestration registry. The marker is a JSON object owned by the hook's user with exactly these
fields:

- `version`: 1.
- `workdir`: the held worktree's absolute top level.
- `head`: the full lowercase object id the worktree was at when the review was dispatched.
- `dispatch_id`: a non-empty identifier for the review.
- `created_utc` and `expires_utc`: ISO-8601 UTC timestamps. `expires_utc` is required: an expired
  marker is ignored with a note, so a crashed dispatcher cannot pin a worktree forever. No maximum
  lifetime is enforced, so a marker with a far-future expiry pins its worktree until the dispatcher
  removes it; keep the horizon short and refresh it for a long review.

The adopter's steps:

1. The dispatch tool writes the marker atomically (write a temporary file elsewhere in
   `GIT_COMMON_DIR/aiqt/`, then rename it into `review-holds/`) before the reviewer starts, and
   removes it when the review returns or is abandoned. Never write the temporary file inside
   `review-holds/` itself: every entry there is read as a marker, so a partial or oddly named file
   reads as malformed state and withholds git commands outside the read-only grammar
   repository-wide until it is renamed into place or removed.
2. The resync or fast-forward tool runs the preflight before it moves any worktree: pipe the
   PreToolUse payload (`tool_name` `Bash`, an absolute `cwd`, and `tool_input.command`) to
   `aiqt_hooks.py review_workdir_hold`, and withhold the move on exit 2 or a printed `deny`
   decision; or read the markers itself.
3. Dispatch each review on a dedicated worktree per review, as the review dispatch binding's limits
   above already advise.

While a live marker pins a worktree, the rule is one-plain-git-or-fail-closed, judged from the RAW
command text, so shell grammar is never modelled construct by construct. Every command is judged
against its HOLD SET: the repositories of the session working directory, of every literal path
the raw text names after `-C`, `--git-dir`, `--work-tree` (the `=` and the separated form), `cd`,
`pushd` or a `GIT_` assignment, with one level of surrounding quotes stripped (quoted `sh -c`
bodies, heredoc bodies and option values included), of every other literal ABSOLUTE path any
segment's tokens carry (a bare operand, the payload after the first `=` of any token, or an
attached short-option value such as `-o/abs/path`), of every operand of an `ln` segment (the
name of a link the command creates resolved against the directory the segment runs from; a
relative target of a plain symbolic link, `ln -s` without `-r`, resolved against the link's own
directory, where the kernel resolves it, and that of a hard link or an `ln -r`, `ln -sr` or
`--relative` link resolved against the directory the segment runs from, where `ln` opens it;
an operand no literal text resolves leaves the created link's destination unknown and is
withheld while a hold is live; and a two-operand link recorded so that a later path stepping
through it is also consulted where the link will point, one level, matched on the path's text),
and of every literal ABSOLUTE path ANYWHERE in the raw
text (so a held path inside a quoted `bash -c`/`sh -c`/`eval` body or a heredoc body still
surfaces its hold; a run of leading slashes is collapsed to one, a quoted span that is one whole
absolute path is read whole with its spaces, and a path glued to a word, an attached
short-option value in raw text included, is left to the token scan), every candidate resolved
PHYSICALLY first and one that names no
existing directory then
consulted at its nearest existing ancestor, and each expanded
through its configured `core.worktree` (a repository-file `core.worktree` adds the configured
worktree's repository to the set, so the expansion also reaches a mutation hidden behind a wrapper
or override; a lookup that cannot answer, and a configured worktree that is process-relative,
fail closed). The operand widening is additive only: a write operand is not a directory move and
never becomes a resolution base, so an over-read can only add repositories whose holds are
consulted. While ANY repository in that set has a live hold, ANY command whose raw text carries
a git word (the word `git`, the git of a `git-<name>` executable, the git ending an interpreter
path such as `/usr/bin/git`), anywhere, inside quotes, substitutions, heredoc bodies and option
values included, is denied (a git segment carrying a program-running form with a plain reason, see
PROGRAM-RUNNING FORMS below; anything else with the reason prefixed `UNVERIFIABLE:`), EXCEPT one
plain git segment:
exactly one segment, no wrapper or reserved word before git (a bare `command`, `exec`, `builtin`,
`env` or `sudo` prefix is peeled by the shared peel), the command word the literal word `git`, and
no `$(`, backquote or heredoc operator anywhere in the raw text. That one plain segment passes
only when it is the CLOSED LITERAL READ-ONLY GRAMMAR: no leading assignment of any kind (a
`GIT_CONFIG_*`, `GIT_EXTERNAL_DIFF` or any other inline override can make a listed read verb run
a program or re-point its repository), the literal word `git` first, no git global option at all
except a literal `-C` directory (`-c`/`--config-env`, `--git-dir`/`--work-tree`, `--exec-path`,
`-P`/`--no-pager` and every other global spelling drop the pass), a subcommand on the ENUMERATED
read-only allowlist in a non-mutating form (a verb outside the allowlist, `read-tree`,
`checkout-index`, `bisect` or `sparse-checkout` included, is treated as mutating, and
`git config edit` is a write), and only options from the verb's CLOSED per-verb read-only set,
which names nothing that writes a file (`--output`), runs a program named in the command or in
configuration (`-O`/`--open-files-in-pager`, `--upload-pack`, `--ext-diff`, `--textconv`,
`--show-signature`, `tag -v`, the `--format`/`--pretty`/`--sort` families, whose signature
placeholders and atoms can run the signing program) or edits configuration. Read-only git so
judged is never withheld by a hold; everything else is judged against the hold set, and also
passes when its target is CERTAIN, resolves outside every held worktree, belongs to a repository
with NO live hold, and carries no program-running option, no unmodelled global option, no
relative `-C`/`--git-dir`/`--work-tree`/`cd`/`GIT_`-assignment operand anywhere in the raw text
and no directory step its target does not account for, whatever that step names: its
only directory moves literal `-C` operands resolved physically (`os.path.realpath`), no
`--git-dir`/`--work-tree` or `-c`/`--config-env` override, no `GIT_` variable assigned, exported
or unset in the command, none of the inherited variables GIT_DIR, GIT_WORK_TREE, GIT_COMMON_DIR,
GIT_INDEX_FILE, GIT_OBJECT_DIRECTORY, GIT_ALTERNATE_OBJECT_DIRECTORIES, GIT_QUARANTINE_PATH,
GIT_NAMESPACE, GIT_REPLACE_REF_BASE, GIT_GRAFT_FILE or GIT_SHALLOW_FILE set in the hook's own
environment (exactly that list: another inherited variable, the `GIT_CONFIG_*` family included,
does not block certainty, a disclosed limit), and the resolved repository's configuration setting
no `core.worktree`. A certain target that is a held worktree is denied; identity is compared by
git's absolute git directory on physically resolved paths, never by path string, so a symlinked
alias of the held worktree (`-C link/..` included) is the same worktree and a sibling path that
merely shares a prefix is not. A certain target whose repository TOP LEVEL (`rev-parse
--show-toplevel`, never the `-C` directory alone) ENCLOSES a held worktree on disk
(the held worktree nested under the target's tree, as with worktrees kept in a subdirectory of
the main checkout) is denied too: a whole-tree mutation of the enclosing tree (`git clean -ffdx`
removes a nested worktree the way the denied `worktree remove` would, and run from a
subdirectory a `:/` or `..` pathspec still sweeps the whole tree) can delete or rewrite the held
directory, so every command outside the read-only grammar there is withheld, a safe over-deny
for the forms that cannot reach it, and a target whose top level git cannot report (a bare
repository, an unreadable tree) fails closed as `UNVERIFIABLE` while a hold is live. `git
fetch`, `git pull`, `git push` and `git send-pack` whose certain target is any worktree of a
repository with a live hold are denied outright: an explicit refspec reaches any ref of that
repository, the held branch included, `--update-head-ok` lifts git's own checked-out-branch
refusal, and `send-pack` is push's plumbing, limited only by the receiving side's own refusal,
which configuration can relax. `git worktree remove` and `git worktree move` of the held worktree
are denied by name shorthand or exact path BEFORE any path resolution (git prefers the
registered-worktree name over an ordinary directory carrying the same name) and by resolved
identity after it, and `git update-ref` or `git symbolic-ref` writes of the held worktree's HEAD,
of its cross-worktree ref paths (`worktrees/<id>/HEAD`, `main-worktree/HEAD`) or of any ref in its
symbolic HEAD chain (the checked-out branch included; a delete in any accepted spelling, `--del`,
clustered `-qd` and clusters whose `m` consumes a reason included) are denied from any worktree of
the repository. `update-ref` dereferences a symbolic ref by default, so the written ref is
resolved through its own symbolic chain in the target repository and a different chain that
converges on the held branch is denied too; `--no-deref` writes the named ref itself and is judged
on that name alone. A `worktree remove`/`move` operand that resolves to no worktree, an update-ref
or symbolic-ref option cluster whose written operand cannot be determined, an update-ref batch,
and a written ref whose symbolic chain git cannot be asked about are denied as
`UNVERIFIABLE:` while a hold is live. STATED OVER-DENIAL: while a hold-set repository is held, a
wrapped or quoted read-only git (`timeout 5 git status`, `sh -c 'git worktree list'`, `env X=1 git
worktree list`) is DENIED, not passed: the raw-text rule classifies no shell bodies, so only the
one plain git segment earns the read-only pass, and any other text carrying a git word, however
read-only or data-only, fails closed until the hold ends. The closed grammar over-refuses the
same way on a held or uncertain target: a read with ANY leading assignment (`X=1 git -C <held>
status`), with any global option other than a literal `-C` (`git -P`, `git --no-pager`,
`git --literal-pathspecs`), or with a read option the verb's closed set does not name (an
unmodelled flag, an attached-value short such as `-U5` or `-n5`, a pickaxe `-S`/`-G` spelling) is
withheld while the hold is live, as is every mutation whose target sits anywhere under a tree
that encloses a held worktree (a scoped `git clean` in a subdirectory beside the nested hold
included) and every certain-target command whose repository top level cannot be read (a bare
repository). While the TARGET'S OWN repository has a live hold, EVERY git segment outside the
read-only grammar is withheld (as `UNVERIFIABLE`, or denied by the program gate below when it
carries a program-running form), whether or not the raw text names the held path:
a sibling worktree shares the repository's refs, objects and configuration, and a program-running
option (`--upload-pack`, grep's `-O`, `rebase -x`, `submodule foreach`, `difftool -x`) reaches
the held worktree without naming it (`git worktree remove held`, `git update-ref` of the held
branch), shapes this rule does not model case by case; the stated over-refusal is that a plain
mutation in a sibling worktree that cannot reach the held one (`git -C <sibling> commit`, a
`worktree remove` of an unheld sibling, an `update-ref` of an unrelated ref) is withheld too
until the hold ends. PROGRAM-RUNNING FORMS: while ANY hold the rule can see is live (a hold in the
hold set, the session's repository or the target's repository), every git segment that carries a
form running a program or shell text is DENIED with a plain reason, whatever its target, and the
rule does not try to resolve where that program runs or which worktree it reaches. The forms are:
any leading assignment (`GIT_EDITOR`, `GIT_SSH_COMMAND`, the `GIT_CONFIG_*` family, `EDITOR` and
`PAGER` can name a program, so every assignment counts); any global option other than a literal
`-C` (`-c` with an `alias.*` `!` value or a `core.*` program setting, `--config-env`,
`--exec-path`, `-p`); the curated program-option model (`--upload-pack`, `--receive-pack`,
`--exec`, `rebase -x` with the value attached in any spelling, `grep -O`, `difftool -x` and
`--extcmd`, `submodule foreach`, `bisect run`, `filter-branch`, the `send-email` command options,
signing and signature verification options, a signature placeholder in a format or sort value,
`clone -u`, `-c` and `--config`, a merge `--strategy`, `--patch` and `--interactive`,
`--trailer`, a `TRANSPORT::ADDRESS` URL, `--help`); the subcommands `mergetool`, `difftool`,
`instaweb`, `web--browse`, `hook`, `shell`, `daemon`, `credential`, `send-email`, `imap-send`,
`svn`, `verify-commit` and `verify-tag`; `for-each-repo`, which runs a git command in EVERY
repository a configuration key lists (a held one included, and an allowed command can write that
key first), and `maintenance`, which writes that very list (`register`, `unregister`) and runs a
platform scheduler over it (`start`, `stop`); the connection services `upload-pack`,
`receive-pack`, `upload-archive` and `http-backend`, each of which runs in the repository its
operand or request names (`receive-pack` runs that repository's hooks); every `config` form
outside the read-only grammar (a
write can install a `!` alias or a `core.*` program setting that a later git runs); `clone` and
`init` with `--template` (the template's hooks, which clone runs at once through post-checkout);
and `help` with `--web`, `--info` or `--man` (`-w`, `-i`, `-m`, a viewer). FILE-WRITING PATH
OPTIONS: while ANY hold the rule can see is live, a git segment carrying an option that writes a
file or tree at an arbitrary path operand is DENIED the same way, whatever the operand names:
`checkout-index --prefix` (it re-roots the whole checkout under any prefix), the `--output` of
the diff and log families (`diff`, `log`, `show`, `whatchanged`, `stash`, the
`diff-files`/`diff-index`/`diff-tree` plumbing, `range-diff`, `rev-list`, `shortlog` and
`reflog`), `archive -o`/`--output`, `format-patch
-o`/`--output-directory`, `bugreport` and `diagnose` `-o`/`--output-directory`, `index-pack -o`,
`fast-import --export-marks` and
`--export-pack-edges`, `fast-export --export-marks`, `init` and `clone --separate-git-dir`,
`credential-store --file`, and `mailsplit -o`; and so are the two options that ADMIT write paths
carried in the command's INPUT rather than in an operand, `apply --unsafe-paths` (the patch
content can name paths outside the target tree, which git itself refuses without the option) and
`fast-import --allow-unsafe-features` (the stream's own `export-marks`/`export-pack-edges`
features name any path, which git refuses without it), since those paths travel in data no argv
scan reads: a content-carried write path the hook cannot read as data counts as unknown and is
refused while any hold the rule can see is live, and from a session outside every held repository
where the command's text names NO held path at all, the hold stays unseen and the admission
option is not refused there, a disclosed residual below. A write operand is a path, not a
directory move, so where it lands is not resolved:
from a command whose resolved target is an
unrelated unheld repository it can still write into the held worktree. HELD-PATH OPERANDS: while
ANY hold the rule can see is live, a git segment carrying a literal ABSOLUTE option value or
operand that resolves physically to a held worktree or strictly inside one is DENIED too,
WHATEVER the word means to its verb: a file-writing option outside the enumerated tables, or a
path-writing positional operand, can land a write at that path, and telling it from a read
spelling would mean re-implementing each verb's grammar, so every absolute held-path word is
refused, a stated over-refusal for the spellings that only read (a command in the closed
read-only grammar passed before this gate and is unaffected); a `-C` operand into the held
worktree is the direct held-worktree mutation and carries the direct hold reason rather than
this gate's over-refusal text. Every such literal absolute path also JOINS THE HOLD SET (resolved
physically first, a path
that does not exist yet consulted at its nearest existing ancestor), as does every literal
absolute path anywhere in the raw text (a quoted shell body or heredoc body included), so the
hold such a path names is
seen, and the gates above fire, from a session outside every held repository too. The EXACT
residual of the
file-writing class is therefore: a file-writing option outside the enumerated tables, or a
path-writing positional operand, whose operand is RELATIVE (it resolves against a directory this
rule cannot fix) or is built by an expansion (`$`, a backquote, a glob or brace character, a
leading `~`); a shell redirection; and a write path carried ONLY in patch or stream content
(`apply --unsafe-paths`, a `fast-import` stream) from a session outside every held repository
whose command text names no held path, since the hook does not read that content; each of those
spellings can still write into a held
worktree from an allowed command, and they stay in the file-edit residual in the limits below.
STATED OVER-REFUSAL:
while some hold is live, an unrelated `git -C <unheld repository> rebase -x 'make' HEAD` is
refused, as are a harmless leading assignment (`LC_ALL=C git -C <unheld repository> commit`), a
`git config user.name` write in an unrelated repository, and a `git -C <unheld repository> diff
--output=<file inside that repository>`, until the hold ends. With no hold the
rule can see, each of these forms is allowed. While a hold is live in a repository the command
reaches only through the session or the raw text and the target is an unrelated unheld
repository, a segment with no program-running form is still withheld when its raw text carries
ANY directory step (`-C`,
`--git-dir`, `--work-tree`, `cd`, `pushd` or a `GIT_` assignment, inside an option value
included) that its resolved target does not account for, WHATEVER that step names: a step in an
option value is applied from a directory this rule cannot fix, so the command is withheld even
where the step names an unheld directory, a stated over-refusal. A RELATIVE path after `-C`,
`--git-dir`, `--work-tree`, `cd`,
`pushd` or a `GIT_` assignment anywhere in the raw text resolves against whatever directory runs
it, which this rule cannot fix, so it is tried against the session cwd AND every directory the
command walks to (the resolved target and every ABSOLUTE raw-text step before the operand in the
text included, so a `cd` (its option words such as `-P`, `-L` or `--` skipped) or `-C` inside an
option value is a base for the relative step after it) AND every textual ANCESTOR of each of
those directories, each landing repository joining the hold set,
and the command is withheld while any reachable hold is live; this over-refuses every relative
`-C` spelling while a hold is live, an option value that is no directory included (`git commit -C
HEAD` and a relative `-C` step naming the resolved target itself are withheld the same way). The
widening is additive and best effort, NOT a model of where a program runs: a symlinked `-C` hides
the physical top level `rebase -x` runs from, a `submodule foreach` program runs in each
submodule below the walked directory, text order is not run order, and a relative step is never
a base for the operand after it, so a hold reachable only through a symlinked `-C`, a submodule
or two relative steps in a row (unless the first lands on a textual ancestor) may stay out of the
hold set. From a session inside a held repository, or with any other hold the rule can see, the
program gate refuses such a command anyway; otherwise it is the allow residual below. The
decision
weighs every hold the command can reach, the UNION of the hold set, the session repository's holds
and the target repository's holds deduplicated by marker path, so a session whose own cwd path is
opaque (a glob or brace character drops it from the hold set) still has its live holds judged. A
hold known ONLY because the session sits in a held repository does NOT withhold a one-plain-git
segment whose certain target is an unrelated unheld repository when that segment carries no
program-running form, so a session in a review-held repository can still commit in an unrelated
one. Each withholding is a safe over-refusal
taken instead of under-blocking; none fires with no live hold the rule can see (the fail-closed
`cwd` and malformed-marker refusals aside). ALLOW RESIDUAL: there is no host-wide view of holds,
so the rule sees only the holds of the repositories in the hold set, the session's repository and
the target's repository. A program-running form whose program reaches a held worktree only
through a path the hold set does not resolve (`git -C <link into a subdirectory> rebase -x 'git
-C ../held reset --hard'`, `git -C <unheld> submodule foreach 'git -C ../../held reset --hard'`,
`git -C <unheld> rebase -x 'cd a/b && git -C ../../../held reset --hard'`) is ALLOWED from a
session outside every held repository when no other hold is seen; the program gate refuses it
only while some hold is seen. The hold set is built
from literal text, so an operand only an expansion resolves (`git -C "$DIR"`, a leading `~`, a
glob) names no repository; from a session outside every held repository such a command stays
allowed even where the expansion would land in a held worktree, and a git reached through an
opaque program name (a wrapper script, an alias) carries no git word at all. The raw-text scan
reads the `-C`, `--git-dir`/`--work-tree`, `cd`/`pushd` (an option word such as `-P`, `-L`
or `--` skipped before the operand) and `GIT_`-assignment spellings, and EVERY literal absolute
path anywhere in the raw text whose preceding character does not belong to a word; the token
scan adds a
path where it is a whole literal absolute token, `=`-payload or attached short-option
value, and the `ln` scan adds a link's operands: a
directory still named another way (a quote-spliced operand such as `-C
"<prefix>"<rest>`, whose raw-text match is only the fragment inside the quotes, a RELATIVE path
inside a quoted body that no `-C`/`cd` spelling introduces, or an attached short-option value
such as `-o<held>/f` that appears only inside a quoted body, where the raw-text scan reads no
path glued to a word) joins no hold set or joins it at the wrong directory, so from a session
outside
every held
repository such a command stays allowed even where it reaches a held worktree, and so does a
PROCESS-RELATIVE path that appears only inside such a body from a session outside every held
repository (seen only by the raw-text scan, it withholds while a hold is live rather than
outright); from a session
inside one, or with any other hold the rule can see, the program gate, the one-plain-git rule or
the process-relative withhold refuses the
form that carries such a value. A symlink that already exists on disk and points through a
process-relative magic link is resolved by the hook's own process when a candidate traverses it,
which can differ from git's resolution, the same class of residual. An UNQUOTED path containing
spaces in the raw text is truncated at the space (only a quoted span is read whole), the same
residual. A link created by a LATER command or by another tool is not modelled, and the rewrite
that follows a link the same command creates is one level, in text order, and matched on the
path's text, so each of these steps reaches the directory the link points to without that
directory's holds being read: a step through a chain of two created links, or a loop running a
later segment first; a step that spells the link's path another way (`.`, `..` or another link
in it); a step through a link created INSIDE a directory that already exists (a link is
recorded under its own name only); a step whose path the command already
named earlier; and a step
whose rewritten path is process-relative, which is dropped from the hold set rather than
refused (a link to `/` and then `-C <link>/proc/self/cwd/...`). From a session outside every
held repository such a command stays allowed even where it reaches a held worktree. A program-running spelling outside the gate's lists (an alias or
a `git-<name>` program on PATH, a hook or a program the configuration already names) is the same
residual: the gate is bounded by its enumeration, not a certified absence of programs. An
unreadable or
malformed marker directory, in any consulted repository and including a `review-holds` path that
exists but is not a directory, denies git-word commands other than the one plain read-only form
(fail closed); non-git commands pass, and a dry-run spelling of a mutating verb (`git clean -n`,
`git commit --dry-run`) is read as mutating, a safe over-deny. When a live marker's recorded
`head` differs from the held worktree's current HEAD, the hook surfaces a drift note (the
in-flight review is invalidated; re-dispatch per rvwpin.4) on each check without denying, which
catches a move made outside the session after the fact.

Limits:

- Movers the hook cannot see: a resync from cron, a terminal, another session without the plugin, a
  Codex or Gemini worker, or a non-Bash tool. These are caught only after the fact by the drift
  note, and only on the next hooked Bash call in a session; they are prevented only where the
  adopter's tool runs the preflight above.
- Indirect git is not seen: an alias, a shell function, a script, or git configured to run programs
  (a `post-checkout` hook, `core.fsmonitor`). A relative `cd` resolved through `CDPATH` can land in
  a repository the hold set does not name. The closed per-verb option sets drop
  `--ext-diff`/`--textconv`/`-O`, but a read verb still runs any program git's configuration already
  wires to its DEFAULT form (`diff.external` and textconv for `diff`, `log -p`, `show` and `blame`,
  a configured pager) with no option at all, since the rule judges argv, not configuration.
- A read verb the grammar passes may still write git's on-disk index: `git status` (and other reads)
  refresh the held worktree's index stat cache in the background, a metadata-only write the grammar
  treats as read-only because it changes no tracked content.
- The held BRANCH is pinned against the ref writes named above, against
  `fetch`/`pull`/`push`/`send-pack` anywhere in the held repository, and against every other
  non-read-only git bound to any worktree of the held repository (the repository-wide
  withholding above), but a push from
  another repository naming this one by URL or path operand can still move it from a session
  outside every held repository: an absolute path operand now joins the hold set, but a hold
  reached only that way does not withhold a certain unrelated target with no unaccounted
  directory step, and a URL or relative operand joins nothing (a push operand naming the held
  worktree's own path is refused by the held-path gate); it surfaces
  after the fact as the drift note.
- File edits inside a held worktree (the Write and Edit tools, `sed -i`, `cp`) are covered by the
  rule but not by this hook; extending the write-scope hook is a separate follow-on. A shell
  redirection (`>`, `>>`) is stripped from the one plain git segment before the grammar reads it, so
  a read verb redirected into a file inside the held worktree writes that file outside the grammar's
  view, the same file-edit residual. The enumerated file-writing path OPTIONS above are refused
  while a hold is live whatever their operand, any git segment whose literal ABSOLUTE option
  value or operand resolves into a held worktree is refused too (the held-path gate), and every
  such literal absolute path joins the hold set (resolved physically, a missing path at its
  nearest existing
  ancestor), from a quoted shell body or heredoc body too, so a
  file-writing option outside the tables and a path-writing POSITIONAL operand (`worktree add`,
  the `clone` and `init` directory operands, `bundle create`, `pack-objects`' base name,
  `merge-file`, `mailinfo`) are caught exactly where the operand is a literal absolute path into
  the held worktree, from a session outside every held repository included; the same spellings
  with a RELATIVE or expansion-built operand cannot be told
  from a ref or pathspec
  without re-implementing each verb's grammar, so those forms stay in this file-edit residual,
  and so does a write path carried only in patch or stream content from a session outside every
  held repository whose command text names no held path (`apply --unsafe-paths` with a patch
  whose content walks into the held worktree, a `fast-import` stream's own marks features): the
  hook does not read that content, and with no hold visible the admission options above are not
  refused.
- Marker accuracy is the adopter's: a missing marker, an early removal, a wrong `workdir`, or a
  review that outlasts `expires_utc` are adopter errors the hook cannot judge.
- A race between the check and the command running remains.
- The marker directory can be written by the assistant, so a determined actor can remove a hold;
  the same suite-level limit the honest-limits section below discloses for the pack's other
  machine-local state.
- Safe over-denies (a verb outside the enumerated
  read-only allowlist however read-only its form, a read verb with any leading assignment, with
  any global option other than a literal `-C`, or with an option its closed per-verb read-only
  set does not name, every git segment outside the read-only grammar bound to ANY worktree of a
  held repository (`git branch -f`, `git switch --ignore-other-worktrees` and every plain sibling
  mutation included),
  `fetch`/`pull`/`push`/`send-pack` targeting any worktree of a held
  repository, every command outside the read-only grammar whose target repository's top level
  encloses a held worktree (a subdirectory target included) or cannot be read at all, every
  program-running form of the program gate while any hold the rule can see is live, however
  unrelated its certain target (an unrelated `rebase -x`, a harmless leading assignment such as
  `LC_ALL=C` and a `git config` write in an unrelated repository included), every file-writing
  path option of the enumeration above while any hold the rule can see is live, however unrelated
  its certain target and wherever its operand lands, every git segment carrying a literal
  absolute path that resolves into a held worktree, whatever the word means to its verb (a read
  spelling outside the read-only grammar included), every git command
  outside the read-only grammar, and an absent command, when the payload's `cwd` is missing,
  relative, carries a NUL byte or names no directory that exists, even where no marker directory
  exists, the same commands when any consulted directory's git identity probe cannot answer
  (git could not be run, timed out, ran past the decision deadline, exited nonzero or on a
  signal with anything but git's specific not-a-git-repository answer, or replied unreadably on
  exit 0, a git too old to know the probe's options included: the
  holds there are exactly what cannot be read, so this fires even where no live hold is known),
  a RELATIVE `-C`,
  `--git-dir`, `--work-tree`, `cd` or
  `GIT_`-assignment operand anywhere in the raw text while any reachable hold is live (`git
  commit -C HEAD` included), any directory step the resolved target does not account for while
  any reachable hold is live, whatever the step names (a `cd /safe/dir` inside an option value,
  a held-path spelling in a commit message), a
  repository-level write such as
  `tag` targeted into
  the held worktree, a path that literally contains `$`, a backquote, a glob or brace character or
  a leading `~`, a PROCESS-RELATIVE path in any structural position, whatever the marker state
  (and one seen only in the raw text while any reachable hold is live), an `ln` operand no
  literal text resolves while any reachable hold is live, an `ln` option word other than `-s`,
  `-r`, `-sr`, `-rs`, `--symbolic` or `--relative` (`-sf`, `-t` and `-T` included) beside any
  git word while any reachable hold is live, a `core.worktree` lookup that cannot
  answer, in any consulted repository and whatever the marker state (the configured worktree's
  holds are exactly what cannot be read), a held or unrelated absolute path mentioned only as
  data in a body or message making its repository's holds visible (one more consulted
  repository, never a loosening), a `cd` in a pipeline or subshell read as making the working
  directory unknown, a
  `cd` destination that cannot be entered read as the directory it started from, and a worktree
  name or override the hook cannot resolve while a hold is live) are taken instead of
  under-blocking. A held worktree nested under a worktree of a DIFFERENT repository is protected
  only where the command's hold set names the held repository (the session cwd or a literal
  operand): a mutation bound to the enclosing foreign repository alone reads no marker of the
  nested one, a disclosed residual of per-repository marker discovery. The `review-holds`
  directory itself is resolved through the filesystem, so a
  symlinked directory that leads to a real directory is followed; a dangling `review-holds` or
  `aiqt` symlink reads as malformed marker state (fail closed), never as absence, and only the
  directory's entries are opened without following links.

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
