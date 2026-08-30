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
  "staleness": {"external_hours": 24, "task_hours": 24},
  "escape": {"path": "path"}
}
```

`state_dir` defaults to `${XDG_STATE_HOME:-$HOME/.local/state}/aiqt-guardrails/orch/<repo-key>/`
(repo-key is a digest of the root path). The machine-written state there is `dispatch-ledger.jsonl`,
`guard-events.jsonl`, `turn-state.json`, `resume-barrier.json`, and `pending-asks.jsonl`. The mode
record carries a plain `Operating-mode: <text>` line; a mode containing `unattended` arms the ask
blocker. The escape artefact (default `<state_dir>/ESCAPE-ALLOW-YIELD`) is operator-owned by
convention: its presence allows every yield and is logged to guard-events.

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
blocker evidence is not mechanically closable here; the register makes patterns visible. The
roster of yield primitives is fixed at generation and re-reconciled when the toolchain changes.
