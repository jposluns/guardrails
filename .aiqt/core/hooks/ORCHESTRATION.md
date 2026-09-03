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
`escape-spoof.json` and `forced-exit.json` (each renamed with a `.surfaced` suffix once the resume
audit has raised it). The mode record carries a plain `Operating-mode: <text>` line; a mode
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
                        "ref": "...", "evidence": "...", "observed_at_utc": "..."}}]}
```

A nonzero exit, malformed JSON, a duplicate id, or an unknown version is ENUMERATOR_ERROR, never an
empty backlog; an empty `items` array is a valid enumeration, a parse failure never is. On the stop
path an enumerator error fails OPEN with findings; a new idle or wake scheduling call is DENIED on
cannot-evaluate, bounded by a three-denial cap. `tools/aei_backlog_md.py` is the generic reference
provider for a markdown-checkbox backlog.

## Platforms without hooks

The decision algorithm binds as an operating procedure: before any stop, idle wake, or drained
declaration, run `tools/orch_preflight.py` for the matching operation (`stop`, `idle`, or `drain`) and
act on its disposition table; the deterministic gates (record drift, mistakes register) still run and
are the enforced part on a hookless platform. The preflight is visibility only, not a blocking control,
and carries no enforceability-ledger row; this prose operating procedure is never advertised as
equivalent to a blocking hook.

## Honest limits (suite-level)

Machine-local state is local-only: CI gates cover committed surfaces and the gates' own self-tests;
gates over machine-local records run at session checkpoints and in the local mirror. Fabricated
free-text blocker evidence remains mechanically unclosable only where no attestation register is
declared; with one declared, external and foreign-lease evidence is verified against validated
attestations at audit cadence, and the residuals become authorship (the chain proves order and
integrity, never who appended), cadence (tamper misleads until the next audit or gate run), and
relevance (a pointer that resolves can still be a misappropriated pretext; triage refutes it). Uid
inequality on the escape sentinel proves not-self, never operator identity: any other-uid process
could plant a passing sentinel. The registry file is the confinement's trust root (a
guardrail-config-integrity surface, review-gated where committed). The anti-shrinkage checkpoint's
first window has no union to compare, and a machine-local, unversioned enumeration source keeps the
local-state disclosure above; the checkpoint bound logs what it drops, never silently. The loop
bound remains a deliberate, exhaustible, marked exit: deleting the turn state restarts it, with each
denial and each forced exit individually recorded in guard-events and the forced-exit artefact. Host
clock control defeats every freshness check here (out of threat model). The roster of yield
primitives is fixed at generation and re-reconciled when the toolchain changes.
