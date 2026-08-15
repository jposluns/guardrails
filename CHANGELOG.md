# Changelog: AIQT Guardrails

Every substantive change carries an entry: what changed, when, and why, with the version bump.

## 2026-08-15, Version 0.1.0

Repository scaffolding established so the guardrails orchestrator can operate under the AIQT
disciplines from its first session.

### Added
- `CLAUDE.md`: the operating governance (the AIQT apex rule, evidence and verification, anything
  wrong fixed first, dual-family QA, the worker/orchestrator model, records and session lifecycle,
  authorization, the gate roster, and git and writing conventions). A starter that grows toward the
  full published-pack governance.
- `tools/`: the quality gates (`check_secrets.py`, `check_no_dashes.py`, `check_links.py`,
  `check_changelog.py`), the local mirror `run_all_checks.sh`, and `ci-status.sh` (reads CI via
  `actions/runs`, since a fine-grained token cannot read the Checks API).
- `.github/workflows/quality.yml`: the `Quality` CI workflow running the gates on every pull request
  and on `main`, with gitleaks pinned by version and checksum.

### Why
The orchestrator has visibility only into this repo and its local store, so it could not build its own
scaffolding from the design sources it cannot see. The lab_infra custodian built this from those
sources so the orchestrator launches into an environment set up to succeed. Operational state and the
maintainer's private governance live in a local-only store, never in this public repo.

### Verification
- The gates pass on this repository, run unpiped locally before the pull request.
