# QA-suite assurance config schema (`.aiqt/assurance.toml`)

`Date: 2026-09-04`

This documents the discovery seam every QA-suite audit reads through `tools/_qa_adapter.py`. It is the
ordinary discovery plane. It is NOT the authority plane for stop or independence enforcement: those read a
separate operator-installed configuration that this file cannot override.

## Resolution order (fail-loud)

An audit resolves its config in this order, and stops at the first that applies:

1. an explicit `--config PATH`;
2. the `AIQT_ASSURANCE_CONFIG` environment variable;
3. the nearest parent `.aiqt/assurance.toml`, walking up from the start directory;
4. narrow portable defaults (root `TODO.md` as a GFM task-list backlog, `tools/check_*.py` as the gate
   roster, the current git repo, the Quality workflow), with no inferred tracker, transcript, inbox, or
   decision authority.

A config pinned by step 1 or step 2 that is absent, unreadable, or malformed is a loud error, never a
silent fall-through to a lower step. Only when neither is set does resolution walk to steps 3 and 4.

## The result contract

Every discovered surface, and every audit verdict built on it, carries a status from a fixed four-value
set:

| status | meaning |
|---|---|
| `PASS` | the surface resolved, or the audit's property holds, on observed evidence |
| `FAIL` | the audit's property is observably violated |
| `UNVERIFIABLE` | the evidence needed to decide is missing, unreadable, or ambiguous |
| `SKIP` | an optional surface is disabled by config: a visible, declared no-op, never silent |

Two invariants make a false green unreachable: missing evidence can never read as `PASS` (an absent
surface resolves to `UNVERIFIABLE`), and an optional-disabled surface is `SKIP`, never `PASS` and never
mislabelled as a malformed-required fault. A required surface that is disabled in config is a distinct
configuration fault (`UNVERIFIABLE`, malformed), kept separate from an optional-disabled `SKIP`.

## Surface tables

Each `[surfaces.<name>]` table declares:

| key | meaning |
|---|---|
| `adapter` | the shipped adapter type that probes this surface (see below) |
| `required` | `true` if the surface must resolve; a missing required surface is `UNVERIFIABLE` |
| `enabled` | `false` disables an optional surface (`SKIP`); disabling a required surface is malformed |
| adapter-specific keys | e.g. `path`, `dir`, `pattern`, `protected-branch`, `remote-landing-provider` |

## Shipped adapter types

Adapters are shipped code selected by config; they return normalized records with provenance and import
no adopter Python. Each probes only whether the surface resolves to real, readable evidence.

| adapter | surface | key(s) |
|---|---|---|
| `gfm-task-list` | a GFM task-list backlog | `path` |
| `glob-roster` | a file roster (the gate roster) | `dir`, `pattern` |
| `git` | the current git repository | `protected-branch`, `remote-landing-provider` |
| `record-file` | a single record file (register, session state) | `path` |
| `transcript-inbox` | a session transcript / inbox surface | `path` |
| `held-source` | a held reference base for cited-claim checks | `path` |

## This-repo dogfood binding

The committed `.aiqt/assurance.toml` binds this repo: `backlog` to `TODO.md`, `gate_roster` to
`tools/check_*.py`, `git` to this repository with `main` protected. `register` and `session_state` are
declared but disabled, because the assurance records live in the orchestrator's private store rather than
this public checkout; the orchestrator enables and binds them locally, and CI (public-only) runs those
record audits orchestrator-side at resume and handoff. `session` and `reference` are optional and absent
by default. In the public checkout `backlog` therefore resolves `UNVERIFIABLE` (the backlog is not
committed here), which is the intended required-unavailable signal, while `gate_roster` and `git` resolve
`PASS`.
