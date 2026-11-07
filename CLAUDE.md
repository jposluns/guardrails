# CLAUDE.md: AIQT Guardrails

**Version 0.2.25** (this file carries its own version, independent of the pack's SemVer release
version; bump it on every substantive change to this file).

This repository AUTHORS the portable AIQT Guardrails pack and the aiqt.ai site, and it dogfoods the
pack's own rules. It is not a corpus. This file is the operating governance for the orchestrator that
works here; the maintainer's private work method, account references, and host-local paths live in a
machine-local `CLAUDE.local.md` that is never committed here.

The portable rules this repo operates under are indexed in `.claude/RULES-INDEX.md` and carried in full under `.claude/rules/`
(generated from `.aiqt/core/rules/`), which loads into a Claude Code session automatically. The rest of
this file is the repo-specific operating governance that sits on top of those rules.

**Apex principle (highest precedence):** (Accuracy = Integrity = Quality = Trust) > Progress > Speed >
Cost. The four facets are one non-negotiable top tier; a gain in progress, speed, or cost never justifies
a loss on it. Its full rule is the first entry in the rule index (`.claude/RULES-INDEX.md`).

<!-- RULES-INDEX:BEGIN (generated) -->
## AIQT rule index

The rule corpus is indexed in [.claude/RULES-INDEX.md](.claude/RULES-INDEX.md), grouped by facet in AIQT priority order, and the full rule text loads from `.claude/rules/`. Both are generated from `.aiqt/core/rules/` (by `tools/gen_claude.py` and `tools/gen_rules.py`) and drift-gated in CI; do not hand-edit this block or the index file.
<!-- RULES-INDEX:END -->

## Project identity and product

AIQT Guardrails is a portable governance pack for AI coding assistants, published to aiqt.ai under the
Apache License 2.0 (except vendored third-party material, which remains under its own terms). This repo is its sole author going forward; `grc_library` is the frozen dogfood adopter with
a provenance pin. The pack ships a portable core plus generated platform adapters, versioned in SemVer
(first public release 1.0.5), with a per-file UTC Date for currency.

## Operating decisions (repo-specific)

These are this repo's operating specifics. The portable rules they rest on are in the rule index (`.claude/RULES-INDEX.md`); only
what is particular to this repo (and has no source rule) lives here.

- **Confirmed defects are recorded.** Every confirmed defect gets a row in the operational
  `open-findings` register the moment it is confirmed, and leaves only via FIXED, ROUTED, REFUTED, or
  ACCEPTED. Severity is graded after the fix decision.
- **Verification floor.** This repo's standing floor is TRIPLE-family (a Claude-family, a GPT/Codex-
  family, and a Gemini-family verifier), for interoperability and platform neutrality; the only
  sanctioned reduction is a family's genuine unavailability (an outage or an exhausted rate limit, not
  merely a budget choice), noted and re-run when it returns. A further family or independent pass is
  reserved for critical changes. Quick, purely-bookkeeping changes need no standing verifier; the
  mechanical gates suffice.
- **Sole orchestrator.** ONE orchestrator is the sole writer and merge authority for this repo.
- **Change tracking.** The public `CHANGELOG.md` carries user-facing release notes per release,
  generated from `changelog.toml` and drift-gated; every change is recorded in detail in the operational
  record.

## Versioning and publication discipline

The pack uses SemVer, single-sourced in `changelog.toml` (the `version` on the latest `[[release]]`); the
root `VERSION` file is generated from it and drift-gated. The release-delta gate
(`tools/check_release_delta.py`) computes the minimum required bump over the governance surface. Releases
ship with a per-file manifest and a published ROOT digest, and those hashes are also published
independently on posluns.dev. By validating them you can identify whether the file you downloaded is the
same one we intended you to have.

## Gates and CI

CI runs a `Quality` workflow of deterministic gates: a project secret scan plus gitleaks, a
leak-denylist check, an en/em dash check, an internal-link check, a site-integrity check, roadmap,
changelog, rules, agents, and CLAUDE.md drift checks (generated files must match their sources), a
version format and single-source check, a rule-placement check, a standards-mappings check, an
adopter-conformance suite, an enforceability-ledger drift gate with a bidirectional roster-reconciliation check, and a standards-currency self-test. `tools/run_all_checks.sh` is the local mirror. Read CI status with `tools/ci-status.sh` (which
reads `actions/runs`, needing Actions: Read; under `--wait` it also reads pull requests: `pulls`, for
which a private repository's token also needs Pull requests: Read, and `commits/<sha>/pulls`, expected
to need the same (not verified live); see the script's header), never `gh pr checks` (a fine-grained token cannot read
the Checks API) and never `commits/<sha>/status` (which always reads pending). The script also offers
`--match-tree [--head <rev>] [--require-job-success <job name>]...`, which succeeds only on a completed
successful run of `.github/workflows/quality.yml` whose head commit's TREE equals the queried commit's
tree (each run's tree is read from `git/commits/<sha>`, expected to need Contents: Read, not verified
live), with optional per-job success requirements: every job of each name given concluded success in that
run, so a skipped job never counts, and it refuses the tree when any completed run of that file on a commit
with the same tree, within its read window, concluded anything but success, since a later pass never
erases an earlier failure. That window is up to 5 listing pages, all read in full, with trees read for the
newest 20 distinct head commits of completed runs of that file (when no successful run of that file is
listed, no tree is read and the read refuses): a non-success run on one of those commits with the same
tree refuses wherever it is listed, a run on any further commit is not compared, and no successful run
listed after the first such run is accepted, so no accepted run is older than one not compared (the
success and no-match result lines state the window). The gate roster grows
toward the full pack roster (portability, dogfood and adapter parity, version monotonicity).

Quality runs on a pull request only when a label is added to it: `quality.yml` triggers on the `labeled`
pull-request type alone, and `tools/check_ci_parity.py` holds its types to exactly `[labeled]`. The merge
lane applies the `merge-train` label to the train pull request after each push it makes there; adding a
label the pull request already carries fires no `labeled` event (GitHub's behaviour, not verified live
here), so to run again on the same head it removes the label and adds it back. A member pull request's
open, push, reopen, retarget or title/body edit schedules no Quality run, so a member head gets no Quality
run at all; `msg-leaks.yml` still runs on every pull request into main and every push to main. Since the
maintainer's decision of 2026-10-27, a member head's evidence is its named suites plus every
generated-file `--check` and the manifest check, run locally before its push, a process rule, not enforced
per head; the full roster is first proven on the train tree by the train pull request's Quality run.
Whenever the full `tools/run_all_checks.sh` runs, it FAILS when gitleaks is not on PATH or reports another
version than the `Secrets (gitleaks)` step of `quality.yml` pins (`tools/check_gitleaks_pin.py`; its live
leg is local-only, and CI runs its self-test). Behind the trigger, as a second layer, the eleven heavy jobs
(`opf-tooling-a`, `opf-tooling-b`, `opf-tooling-c`, `selftest-execution`, the `git-fixture-env-core` canary,
the five host jobs and `byte-canon-matrix`) carry one pinned job-level `if:` and run only on a push and on a pull
request whose head is a `merge-train-*` branch of this repository; every other job runs in every run. A
label added to any other pull request into main starts a run whose heavy jobs are skipped, so its
`Quality gates` fails by design, and a change merges only by riding a train: a squash merge of an
up-to-date, labelled `merge-train-*` pull request, whose run is the full suite (or the site fast
lane below for a site-only train, the one skip `Quality gates` accepts), and the push to `main`
runs the full suite again. A newer run of the same pull request cancels its superseded run; a push run has a
concurrency group of its own and is never cancelled. `tools/check_ci_parity.py` and
`tools/check_python_host_pin.py` accept exactly the pinned `concurrency` block and `if:` line.

The site fast lane (maintainer's recorded decision, 2026-11-03). Eligibility: the heavy jobs wait
for a `scope` job (every other job runs in parallel with it), whose first step after checkout and
Python setup, before any step that runs the pull request's own code, extracts and runs the base
tree's copy of `tools/ci_site_scope.py` (`git --no-replace-objects show HEAD^1:...` into the
runner's temporary directory; a base with no readable copy decides false). The decision is true
only when the run is a pull request whose head is a `merge-train-*` branch of this repository,
the checked-out HEAD is the documented two-parent test merge (HEAD's raw commit object, read
with `git --no-replace-objects cat-file commit HEAD`, must record exactly two parents, the second
equal to the event's head SHA; GitHub-documented behaviour, not verified live here), and every
path it changes, read with replacement objects disabled against `HEAD^1` (the first parent that
object records), is in the site allowlist: under `site/` or `opf/site/`, or exactly one of the
three files a site-only change regenerates (`.aiqt/manifest.toml`, `.aiqt/release/root.txt`,
`.aiqt/release/announce-snippet.txt`), except six excluded site-tree pages: the five OPF homes
contract pages `opf/site/adopt.html`, `opf/site/adopt.md`, `opf/site/core-files.html`,
`opf/site/disclosure.html` and `opf/site/record-indexes.html`, and `opf/site/tooling.html`.
A HEAD with any other parent count, a git failure, or an unparseable commit header decides
false. The decision reads HEAD's raw parents and ignores both `info/grafts` and replace refs;
the extraction (`git --no-replace-objects show HEAD^1:...`) disables replace refs only, and git
resolves its `HEAD^1` through a grafts file, so under one the extracted revision and the
revision the diff is read against can differ; a fresh CI checkout has no grafts file. Running
the base tree's copy first means an ordinary train cannot repoint the decision by editing the tool or by acting in an earlier step of that job; a
same-repository train can still edit the workflow itself, which no pull_request workflow covers,
so this ordering is merge-lane discipline, not isolation from a hostile train.

Checks kept for a site-only train: every always-run job still runs. The five OPF homes contract
pages stay excluded from the allowlist: the live gate of `opf/tools/check_opf_homes.py`, which
reads them, now runs in the always-run `tree-gates` job, so for that live gate the exclusions are
a deliberate conservative margin, but its self-test also reads those pages live and runs only in
skippable jobs, so for the self-test they are still load-bearing; `opf/site/tooling.html` stays
excluded as a deliberate conservative margin even though `tools/check_opf_declaration.py`, which
reads it and decodes every `.md` and `.html` file in the tree, also runs (live gate and
self-test) in the always-run `site` job; and
`tools/check_python_floor.py` (which reads `site/development.html`, `site/install.html`,
`opf/site/adopt.md` and `opf/site/adopt.html`) runs live in the always-run registry job in every
run. What the fast lane deliberately gives up for a site-only train is every check in the eleven
heavy jobs named above: every step of those jobs is skipped. Of their commands, only the
child-status probe (`opf/tools/_child_status.py`), the opf-declaration `selftest-execution`
suite, and `tools/check_byte_canon.py` (live gate and self-test) also run, as the same command,
in an always-run job. Every other command in those jobs runs nowhere else in CI on such a train,
among them the OPF tooling self-tests of the three `opf-tooling-*` jobs (for example
`check_opf_homes.py --self-test`, `check_opf_doctor.py --self-test`, `check_opf_drift.py
--self-test` and `check_opf_record.py --self-test`), the source-only init gate
`opf/tools/check_opf_init.py` (a fixture suite in `opf-tooling-b`), the `opf.py` self-test,
`tools/check_selftest_execution.py --self-test` and the other `selftest-execution` suites, every
git-fixture-env suite, and `tools/check_python_host_pin.py` beyond the registry job's
`--jobs-only` run. Those jobs no longer hold the whole-repository live gates: the live gates of
`check_opf_homes.py`, `check_opf_prompt_pack.py`, `check_opf_doctor.py`,
`tools/check_opf_enforce_platforms.py`, `check_opf_drift.py` and `check_opf_init_contract.py`,
`tools/check_entry_guard.py` and `tools/check_child_status_coverage.py` run in the always-run
`tree-gates` job in every run, and the opf-declaration live gate runs in the always-run `site`
job. It also
gives up the skipped jobs' host, configuration and platform repetitions: the host-patch jobs'
exact host Python, the config lanes' hostile git configurations, and `byte-canon-matrix`'s
platforms.

Failure behaviour: `Quality gates` accepts a skipped heavy job only with an eligible true
decision: the scope job wrote `site_only=true` and the run is such a merge-train pull request
(the gates step re-checks the event, head ref and head repository before accepting a skip). On a
true decision the heavy jobs skip (their pinned `if:` reads the scope output). The decision
classifies the train's aggregate change, every path changed over the base, not the pull requests
it carries; nothing routes pull requests to trains, so a train that mixes a site-only pull
request with any other change takes the full lane. Everything else is
fail-closed: an empty changed-path list, any path outside the allowlist, a rename out of those
trees, a HEAD that is not the two-parent test merge, or any error reading the diff decides false.
On a non-train or fork head the decision is also false, and there the heavy jobs are skipped by
their own `if:` and `Quality gates` FAILS: such a run is red, never a fast pass, and it does not
run the full suite. A heavy job that is skipped, fails, is cancelled, or reports no result
outside a true decision still fails `Quality gates`. Adding a NEW site file regenerates
`.gitattributes`, which is outside the allowlist, so such a change takes the full suite. A push
to `main` NEVER takes the fast lane: on a push the scope job writes `site_only=false` without
reading any diff, so a push runs everything, also after a site-only train merges; that holds only
while the scope job itself succeeds, and a failed scope job turns the run red through `Quality
gates` (never into a pass). `tools/check_ci_parity.py` pins the scope job's whole body
(`SCOPE_JOB`, the step order included), the heavy jobs' `if:` line and the gates step, so none of
this wiring can drift unreviewed; that pin is a consistency check within this reviewed
repository, not an authorization (the maintainer's recorded decision is the authorization).
Every `actions/checkout` step in `.github/workflows` sets `persist-credentials: false`, so the job token
stays out of the checkout's git config (no job pushes or fetches after its checkout);
`tools/check_ci_parity.py` reads each workflow file there as parsed YAML and refuses a checkout step
without it; a workflow outside that reader's conservative YAML subset is cannot-evaluate (exit 2),
never skipped.
`tools/ci-status.sh` requires a completed successful run of `.github/workflows/quality.yml` on every
read, with or without `--require-base` and `--wait`, so a head with none (a member head) reads as missing
(exit 1), never as passing. It also refuses a commit with any non-success run, a cancelled one included,
so a cancelled run on a live head (a label added while its run is in progress, the `merge-train` label
re-applied included, or a push back to an earlier head) needs a new head commit, labelled again.

The maintainer authorized on 2026-10-25 what member heads no longer get from CI; each still runs once, on
the train head: every Quality job. Since the maintainer's decision of 2026-10-27, the train pull
request's Quality run on the identical tree, not the local mirror, is the standing full evidence for the
gates in token parity with `tools/run_all_checks.sh` (gitleaks included) and the host-patch suites;
neither a member head's local subset nor the train's fast pre-push subset runs the full roster, and
nothing in this repository enforces either subset. The fast pre-push subset is a check run on the
maintainer's host before a train is pushed and is defined outside this repository; the train pull
request's Quality run is the evidence. Whenever the full local runner runs, its local
gitleaks-pin leg stays its own requirement. The CI-only pull-request legs
(branch-root and version monotonicity against the base), the hostile `core.autocrlf` byte-canon legs on
ubuntu and macOS, setup-python's resolution of `3.14` on the runner, evaluation of the merge ref against
current main in a clean clone, and CI evidence for each intermediate member commit have no member-head
substitute.

CI runs two Python legs. Five required jobs in `.github/workflows/quality.yml` run on the exact patch
release the maintainer host runs, named in `.aiqt/python-host.toml`, on `runs-on: ubuntu-24.04`:
`opf-host-patch` runs the OPF tooling self-test, and `git-fixture-env-core-host-patch`,
`git-fixture-env-config-combined-host-patch`, `git-fixture-env-config-malformed-host-patch` and
`git-fixture-env-config-system-host-patch` each run one of the four git-fixture-env suites, so the four
host suites run in parallel and take about as long as the longest of them rather than their sum. These
host jobs are the ONLY CI run of the OPF tooling self-test and of the combined, malformed and system
git-fixture-env suites; `git-fixture-env-core` stays on `3.14` as the one canary of those suites. Every
other job there that sets up Python pins `3.14` (the Quality gates job sets up none); with `check-latest`
off, setup-python may resolve that pin to a 3.14 already on the runner rather than the newest patch release.
Every other job in `quality.yml` stays required, `opf-tooling-a`, `opf-tooling-b` and `opf-tooling-c`
(every OPF gate and direct self-test on `3.14`, split by a local timing that stopped each step at 20
seconds; live balance unconfirmed) among them. Under the maintainer's 2026-10-27 authorization this reverses the earlier decision that
the host jobs' CI time was a cost accepted for running those suites twice: the `3.14` copies of the OPF
tooling self-test and of the three config suites are gone, and the local runner's
`opf.py --self-test-group` lines have no CI counterpart (CI runs the whole self-test once, on the host
patch). An edit to a pull request's title or body starts no Quality run. A passing local suite on the host
patch then predicts a required CI leg; a failure on the `3.14` canary alone is diagnostic evidence of a
patch-release difference, not proof that the change is innocent. The newest-patch early warning (which
once caught a change that passed every local suite and failed only in CI on a newer patch release) now
rests on the `git-fixture-env-core` canary and on the `3.14` setup of every non-host job; for the OPF
tooling self-test and the three config suites it is covered nowhere. The runner image is pinned beside the patch: on 2026-10-12 the
setup-python versions manifest listed Linux x64 builds of 3.14.4 for Ubuntu 22.04 and 24.04 only, so
`ubuntu-latest` moving to an image without one would fail every host job, and moving them off ubuntu-24.04
(or its retirement) needs a host patch with a build for the new image. `tools/check_python_host_pin.py`
holds the workflow's top-level keys (only `name`, `on`, `permissions`, the pinned `concurrency` block and
`jobs`) and each host job whole (no job key but `name`, `runs-on`, `steps` and the pinned `if:`, that
runner, the checkout step as written, one setup-python pin, the gate itself as the next step, the
job's suite) and fails when the running interpreter is another release, so `tools/run_all_checks.sh` FAILS
on any patch release other than the one the file names. When the host interpreter changes, update
`python-host-patch` in `.aiqt/python-host.toml` and the `python-version` pin of every host job in
`quality.yml` together, in one change.

## Git and writing conventions

- Commits are authored `Jeff Posluns <jeff@posluns.ca>`, with NO AI author, committer, or co-author
  trailer.
- Oxford English; prefer `-ize`/`-ization`. **Never use en dashes or em dashes**; use hyphens, commas,
  colons, semicolons, or parentheses. Sentence-case headings. Console messages to the maintainer carry
  a `[YYYY-MM-DDTHH:MMZ]` UTC prefix and never render a wall of diff lines.

## Scope note

This file is a generated-adapter governance file: a hand-authored repo-specific layer wrapping one small
generated block that points to the generated rule index (`.claude/RULES-INDEX.md`, produced by `tools/gen_claude.py` from `.aiqt/core/rules/` and
drift-gated in CI, so it cannot diverge from the pack). The full rule text loads from `.claude/rules/`;
this file adds only what is particular to operating this repo. It will grow toward the full published-pack
governance (the complete gate/hook roster, the SemVer and publication discipline in full) as those land.
