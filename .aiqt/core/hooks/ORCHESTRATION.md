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
  "mistakes_register": "path",
  "attestations": "path",
  "staleness": {"external_hours": 24, "task_hours": 24},
  "escape": {"path": "path"}
}
```

`state_dir` defaults to `${XDG_STATE_HOME:-$HOME/.local/state}/aiqt-guardrails/orch/<repo-key>/`
(repo-key is a digest of the root path). The machine-written state there is `dispatch-ledger.jsonl`,
`guard-events.jsonl`, `turn-state.json`, `resume-barrier.json`, `pending-asks.jsonl`,
`backlog-checkpoint.json`, `attestations-validated.json`, and, when their events occur,
`escape-spoof.json` (renamed with a `.surfaced` suffix once the resume audit has raised it) and the
append-only `forced-exit.jsonl` (every non-closed-disposition forced exit appended as its own row,
each surfaced exactly once, tracked by a companion `forced-exit-surfaced.json`). The mode record carries a plain `Operating-mode: <text>` line; a mode
containing `unattended` arms the ask blocker. The escape sentinel (default
`<state_dir>/ESCAPE-ALLOW-YIELD`) is operator-owned by enforced acceptance, not convention: it is
honoured only as a regular file (never a symlink), owned by a uid other than the assistant's
effective uid, and not group- or other-writable. A present sentinel failing any condition is ignored
(the decision proceeds exactly as with no sentinel), logged to guard-events, and surfaced once by
the next resume audit. Where operator and assistant share one uid, no file either can create passes,
so the clean-ALLOW escape channel is unavailable there: recovery is a differently-owned sentinel
(for example root-owned), registry or mode maintenance by the operator, or the bounded, marked exit;
split-uid deployment is the recommended posture.

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
                        "ref": "...", "evidence": "...", "observed_at_utc": "..."},
            "close": {"reason": "fixed|no-fix-needed|not-buildable|maintainer-deferred",
                      "ref": "...", "paths": ["..."]}}]}
```

Under AEI v2 a `closed` item MUST carry a `close` object (a `reason` in the closed vocabulary and a
non-blank `ref`). A `fixed` close is terminal ONLY when the cited commit is BOUND TO THE ITEM BY
CONTENT: its changed set intersects the item's declared close `paths` (the repo-relative path(s) the fix
touched). This binding is by content alone: an item id merely named in a commit message is
assistant-writable text, not evidence the fix is in the commit, so it never makes a close terminal. A
`fixed` close that declares NO `paths` therefore cannot be strong-verified and HOLDS (cannot-evaluate),
as does an unavailable git rail; a real-but-unrelated commit, or one whose declared paths the commit did
not touch, stays actionable; resolving to some commit is not enough.

ADOPTER REQUIREMENT (close `paths`): to have a `fixed` close read as a TERMINAL disposition (rather than
held or found unjustified), the enumeration MUST declare the item's close `paths`, and the cited commit
must actually touch them. BOTH surfaces can express `paths`: a JSON AEI enumeration carries them in the
close object, and the committed-backlog markdown grammar (`tools/aei_backlog_md.py`) carries them as a
trailing `:: paths=<comma-separated repo-relative paths>` on the fixed close, the same close.paths field
the JSON surface and the liveness join read. The reconciliation resolves to exactly three cases, identical
at the yield boundary and at the committed-state liveness join (`tools/check_liveness_joins.py`): a `fixed`
close that declares `paths` whose cited commit's changed set INTERSECTS them is TERMINAL; a `fixed` close
that declares NO `paths` HOLDS (cannot-evaluate, unreconcilable), because the commit cannot be bound to the
item by content and the close cannot be strong-verified; and a `fixed` close that declares `paths` whose
cited commit does NOT touch them, or whose ref resolves to no commit, is an UNJUSTIFIED FINDING that stays
ACTIONABLE (the fix did not land, so the item keeps being worked), never a hold. An unavailable git rail
HOLDS every `fixed` close (cannot-evaluate). The distinction is material: a held close and an actionable
unjustified finding both keep working the item safely, but only a hold is cannot-evaluate while an
unjustified finding stays in the actionable set, and an actionable wait-denial can reach cap relief where a
cannot-evaluate cannot. Declaring accurate `paths` that the fix actually touched is what lets a genuine fix
count as terminal rather than hold or read as unjustified.

A nonzero exit, malformed JSON, a duplicate id, or an unknown version is ENUMERATOR_ERROR, never an
empty backlog; an empty `items` array is a valid enumeration, a parse failure never is. On the stop
path (FIX 1) an enumerator error or any cannot-evaluate FAILS CLOSED-CONTINUE: the stop is DENIED
(ignorance refuses the wind-down), releasable by the operator-owned escape sentinel OR by the
guard-owned loop bound (past which it becomes a deliberate, recorded forced exit), and never a clean
close; a new idle or wake scheduling call is likewise DENIED on cannot-evaluate, bounded by a
three-denial cap. `tools/aei_backlog_md.py` is the generic reference
provider for a markdown-checkbox backlog.

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

The registry file is the confinement's trust root (a guardrail-config-integrity surface, review-gated where committed). The anti-shrinkage checkpoint's first window has no union to compare; an init marker distinguishes that genuine first window from a checkpoint deleted after a prior window (held as a possible reset), and the bound logs what it drops, never silently. A `proposed` or ungranted item is ASSISTANT-WRITABLE label text. Under AEI v2 the classifier treats an ORDINARY proposal as ACTIONABLE (a self-authored proposal is not a grant and licenses no wind-down; its preparatory pipeline is buildable work): it never removes buildable work from the actionable set even on a first observation. The one exception is a maintainer/provenance-confirmed park: a proposed item carrying a human-decision blocker parks at its final step ONLY when its blocker ref is covered by a validated, ownership-passing DEFERRALS register row keyed (item, ref), the same maintainer-controlled rail the maintainer-deferred close requires and one the assistant cannot self-author. A self-authored proposal plus a self-authored pending-decisions label never parks: the pending-decisions surface is assistant-writable, so it substantiates no proposed park (F-331). Only that maintainer-controlled deferrals provenance honours the GD-127 recorded-wind-down park rather than forcing the item to prep. Under AEI v1 (the shipped compatibility contract) a proposed or ungranted item keeps the byte-identical NON-COMPELLING `proposed` bucket, unchanged in bucket and decision; the stricter proposed-is-actionable treatment is a v2 behaviour, so a v1 adopter's classification is preserved exactly. A present-but-unreadable orchestration registry is a cannot-evaluate DISTINCT from an absent one: the stop and teammate-idle family DENIES the wind-down fail-closed (never treating unreadable config as absence), and the operator-owned escape sentinel, resolved independently of the unreadable registry, is the release. The checkpoint additionally records each open id's eligibility (granted-and-open vs proposed/ungranted), so a cross-window demotion of a previously-actionable id to proposed or ungranted with no closed receipt is also held (cannot-evaluate); that eligibility memory lives in the same actor-writable state dir, raising the bar without being categorical. Deleting the marker and the checkpoint together, like deleting the turn state to restart the loop bound, is the same actor-writable-state residual. The loop bound remains a deliberate, exhaustible, marked exit, with each denial and each forced exit individually recorded in guard-events and an append-only forced-exit log that the next resume surfaces exactly once. Host clock control defeats every freshness check here (out of threat model). The roster of yield primitives is fixed at generation and re-reconciled when the toolchain changes.
