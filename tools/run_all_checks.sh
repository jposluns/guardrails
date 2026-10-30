#!/usr/bin/env bash
# Run every quality gate, in the same order CI runs them.
# Never pipe this to a truncating sink: a masked exit code defeats the gate.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

# Every gate below launches isolated: `python3 -I -B tools/<gate>.py`. `-I` (isolated mode) drops the
# script's own directory from sys.path so a tool-written sibling (a stray tools/os.py) cannot shadow a
# stdlib import and neuter a gate; the check_python_launcher_isolation gate enforces this across the
# roster. `-I` implies `-E`, which makes the export below ineffective for the gate itself, so `-B`
# carries the bytecode suppression per step. The export remains only for any non-isolated grandchild a
# gate may spawn (which does not inherit `-I`).
# The portability gate now enumerates the git-tracked shippable surface, so a stray ignored
# __pycache__/*.pyc artefact no longer trips it. PYTHONDONTWRITEBYTECODE=1 remains tree hygiene: a gate
# runner reports the tree without littering it. Do NOT blanket `git clean` ignored paths, which
# would also delete unrelated local files (a stray .venv, .idea, or node_modules); this runner never
# auto-sweeps.
export PYTHONDONTWRITEBYTECODE=1

failed=0
notrun=0
# Gate output does not carry the runner's gate label, so each failure is named as it happens and the
# names are listed again before RESULT: FAIL; a failing suite never needs a hand re-run to find it.
failed_names=""

run_gate() {
  local name="$1"; shift
  echo "--- ${name} ---"
  if "$@"; then :; else
    local rc=$?
    failed=1
    failed_names="${failed_names:+${failed_names}, }${name}"
    echo "GATE FAILED: ${name} (exit ${rc})"
  fi
  echo
}

# Child exit status first: with SIGCHLD ignored by whatever started this runner, every Python gate below
# would read its own failed children as passes. bash reads the probe's refusal correctly but cannot reset
# the inherited ignore for its children, so the probe is a gate member, not a reset: its exit 2 sets
# failed, and the run ends RESULT: FAIL whatever later gates print. check_child_status_coverage.py holds
# this line first.
run_gate "child-status" python3 -I -B opf/tools/_child_status.py
run_gate "child-status-selftest" python3 -I -B tools/check_selftest_execution.py --suite child-status-selftest
run_gate "child-status-coverage-selftest" python3 -I -B tools/check_selftest_execution.py --suite child-status-coverage-selftest
run_gate "child-status-coverage" python3 -I -B tools/check_child_status_coverage.py
run_gate "secrets"   python3 -I -B tools/check_secrets.py
run_gate "secrets-selftest" python3 -I -B tools/check_secrets.py --self-test

# gitleaks is a second, independent secret gate, and it is required whenever this full runner runs.
# A member head gets no Quality run: its evidence is its named suites plus every generated-file
# --check and the manifest check, and the train pull request's Quality run on the identical tree is
# the standing full evidence. If gitleaks is not installed locally its scan cannot run, and the
# gitleaks-pin gate below fails the run rather than letting it pass without the scan.
# A no-root user install (the recommended non-root path) lands in ~/.local/bin, which a
# non-login shell may not carry on PATH; fall back to it ONLY when the normal lookup fails,
# and append rather than prepend, so an on-PATH gitleaks keeps precedence and a user-local
# binary can never shadow it (nor shift resolution of the later gates).
echo "--- secrets (gitleaks) ---"
if ! command -v gitleaks >/dev/null 2>&1 && [ -n "${HOME:-}" ] && [ -x "$HOME/.local/bin/gitleaks" ]; then
  PATH="$PATH:$HOME/.local/bin"
fi
if command -v gitleaks >/dev/null 2>&1; then
  if gitleaks dir . --no-banner --redact --exit-code 1; then
    echo "PASS: gitleaks found no leaks"
  else
    gitleaks_rc=$?
    failed=1
    failed_names="${failed_names:+${failed_names}, }secrets (gitleaks)"
    echo "GATE FAILED: secrets (gitleaks) (exit ${gitleaks_rc})"
  fi
else
  echo "MISSING: gitleaks is not on PATH locally, so its scan cannot run here, and the"
  echo "  gitleaks-pin gate below fails this run. Install the pinned version to fix it:"
  echo "  see the pinned version and checksum in .github/workflows/quality.yml"
  notrun=1
fi
echo
# The pinned gitleaks is required locally: the gate fails the run when no gitleaks is on PATH (after the
# ~/.local/bin fallback above) or the one on PATH reports another version than the Secrets (gitleaks)
# step of quality.yml pins. CI runs only its self-test (a ci-parity exception): the CI step downloads
# and verifies the pinned binary itself.
run_gate "gitleaks-pin-selftest" python3 -I -B tools/check_gitleaks_pin.py --self-test
run_gate "gitleaks-pin" python3 -I -B tools/check_gitleaks_pin.py
run_gate "leaks"     python3 -I -B tools/check_leaks.py
run_gate "leaks-selftest" python3 -I -B tools/check_leaks.py --self-test
run_gate "msg-leaks-selftest" python3 -I -B tools/check_msg_leaks.py --self-test  # real scan needs CI event context
run_gate "portability-selftest" python3 -I -B tools/check_portability.py --self-test
run_gate "portability" python3 -I -B tools/check_portability.py
run_gate "derived-command-parameters-selftest" python3 -I -B tools/check_derived_command_parameters.py --self-test
run_gate "derived-command-parameters" python3 -I -B tools/check_derived_command_parameters.py
run_gate "relied-exit-pipes-selftest" python3 -I -B tools/check_relied_exit_pipes.py --self-test
run_gate "relied-exit-pipes" python3 -I -B tools/check_relied_exit_pipes.py
run_gate "dashes"    python3 -I -B tools/check_no_dashes.py
run_gate "dashes-selftest" python3 -I -B tools/check_no_dashes.py --self-test
run_gate "dashes-default-parity" python3 -I -B tools/check_no_dashes.py --default-parity
run_gate "links"     python3 -I -B tools/check_links.py
run_gate "site-selftest" python3 -I -B tools/check_site.py --self-test
run_gate "site"      python3 -I -B tools/check_site.py
run_gate "overclaim-selftest" python3 -I -B tools/check_overclaim.py --self-test
run_gate "overclaim" python3 -I -B tools/check_overclaim.py
run_gate "license-qual-selftest" python3 -I -B tools/check_license_qualification.py --self-test
run_gate "license-qual" python3 -I -B tools/check_license_qualification.py
run_gate "footer-selftest" python3 -I -B tools/check_footer.py --self-test
run_gate "footer" python3 -I -B tools/check_footer.py
# Repository-only assertion lives outside the shipped tooling.
run_gate "newtab-repo-contract" python3 -I -B .github/check_newtab_contract.py
run_gate "newtab-selftest" python3 -I -B tools/check_newtab.py --self-test
run_gate "newtab" python3 -I -B tools/check_newtab.py
run_gate "site-versions-selftest" python3 -I -B tools/check_site_versions.py --self-test
run_gate "site-versions" python3 -I -B tools/check_site_versions.py
run_gate "opf-render-selftest" python3 -I -B tools/selftest_opf_render.py
run_gate "opf-homes-selftest" python3 -I -B opf/tools/check_opf_homes.py --self-test
run_gate "opf-homes-contract" python3 -I -B opf/tools/check_opf_homes.py
# The OPF tooling self-test runs here as its named groups, a local-runner split; CI runs no group, only the
# whole self-test once. opf.py runs a group only while the groups hold every registered self-test exactly
# once. The whole run stays in the host-patch CI job (a ci-parity exception) and in opf/tools/run_all_checks.sh.
run_gate "opf-tooling-selftest-core" python3 -I -B opf/tools/opf.py --self-test-group core
run_gate "opf-tooling-selftest-adopt" python3 -I -B opf/tools/opf.py --self-test-group adopt
run_gate "opf-tooling-selftest-watchdog" python3 -I -B opf/tools/opf.py --self-test-group watchdog
run_gate "opf-journal-direct-selftest" python3 -I -B opf/tools/_journal.py --self-test
run_gate "opf-observe-direct-selftest" python3 -I -B opf/tools/_opf_observe.py --self-test
run_gate "opf-sync-direct-selftest" python3 -I -B opf/tools/_opf_sync.py --self-test
run_gate "opf-drift-selftest" python3 -I -B opf/tools/check_opf_drift.py --self-test
run_gate "opf-drift" python3 -I -B opf/tools/check_opf_drift.py
run_gate "opf-doctor-selftest" python3 -I -B opf/tools/check_opf_doctor.py --self-test
run_gate "opf-doctor" python3 -I -B opf/tools/check_opf_doctor.py
run_gate "opf-init-selftest" python3 -I -B opf/tools/check_opf_init.py --self-test
run_gate "opf-init" python3 -I -B opf/tools/check_opf_init.py
run_gate "opf-init-contract-selftest" python3 -I -B opf/tools/_opf_init_contract.py --self-test
run_gate "opf-init-contract-check-selftest" python3 -I -B opf/tools/check_opf_init_contract.py --self-test
run_gate "opf-init-contract-check" python3 -I -B opf/tools/check_opf_init_contract.py
run_gate "opf-record-selftest" python3 -I -B opf/tools/check_opf_record.py --self-test --red-on-revert
run_gate "opf-adopt-selftest" python3 -I -B opf/tools/_opf_adopt.py --self-test
run_gate "opf-adopt-apply-selftest" python3 -I -B opf/tools/_opf_adopt_apply.py --self-test
run_gate "opf-adopt-state-selftest" python3 -I -B opf/tools/_opf_adopt_state.py --self-test
run_gate "opf-adopt-hook-selftest" python3 -I -B opf/tools/_opf_adopt_hook.py --self-test
run_gate "opf-adopt-complete-selftest" python3 -I -B opf/tools/_opf_adopt_complete.py --self-test
run_gate "opf-import-batch-selftest" python3 -I -B opf/tools/_opf_import_batch.py --self-test
run_gate "opf-import-complete-selftest" python3 -I -B opf/tools/_opf_import_complete.py --self-test
run_gate "opf-pack-manifest-selftest" python3 -I -B opf/tools/_opf_pack_manifest.py --self-test
run_gate "opf-adopt-observe-selftest" python3 -I -B opf/tools/_opf_adopt_observe.py --self-test
run_gate "opf-adopt-discover-selftest" python3 -I -B opf/tools/_opf_adopt_discover.py --self-test
run_gate "opf-prompt-pack-selftest" python3 -I -B opf/tools/check_opf_prompt_pack.py --self-test
run_gate "opf-prompt-pack" python3 -I -B opf/tools/check_opf_prompt_pack.py
run_gate "opf-enforce-platforms-selftest" python3 -I -B tools/check_opf_enforce_platforms.py --self-test
run_gate "opf-enforce-platforms" python3 -I -B tools/check_opf_enforce_platforms.py
run_gate "opf-oplock-selftest" python3 -I -B opf/tools/_opf_oplock.py --self-test
run_gate "opf-init-substrate-selftest" python3 -I -B opf/tools/_opf_init_substrate.py --self-test
run_gate "opf-init-builders-selftest" python3 -I -B opf/tools/_opf_init.py --self-test
run_gate "opf-init-operation-selftest" python3 -I -B opf/tools/_opf_init_operation.py --self-test
run_gate "opf-init-p0-selftest" python3 -I -B opf/tools/check_opf_init_p0.py --self-test --red-on-revert
run_gate "opf-init-observe-selftest" python3 -I -B opf/tools/check_opf_init_observe.py --self-test --red-on-revert
run_gate "opf-standalone-closure-selftest-a" python3 -I -B tools/check_opf_standalone_closure.py --self-test --ci-job opf-closure-a
run_gate "opf-standalone-closure-selftest-b" python3 -I -B tools/check_opf_standalone_closure.py --self-test --ci-job opf-closure-b
run_gate "opf-standalone-closure-selftest-c" python3 -I -B tools/check_opf_standalone_closure.py --self-test --ci-job opf-closure-c
run_gate "opf-standalone-closure-selftest-d" python3 -I -B tools/check_opf_standalone_closure.py --self-test --ci-job opf-closure-d
run_gate "opf-standalone-closure-selftest-e" python3 -I -B tools/check_opf_standalone_closure.py --self-test --ci-job opf-closure-e
run_gate "opf-standalone-closure-a" python3 -I -B tools/check_opf_standalone_closure.py --ci-job opf-closure-a
run_gate "opf-standalone-closure-b" python3 -I -B tools/check_opf_standalone_closure.py --ci-job opf-closure-b
run_gate "opf-standalone-closure-c" python3 -I -B tools/check_opf_standalone_closure.py --ci-job opf-closure-c
run_gate "opf-standalone-closure-d" python3 -I -B tools/check_opf_standalone_closure.py --ci-job opf-closure-d
run_gate "opf-standalone-closure-e" python3 -I -B tools/check_opf_standalone_closure.py --ci-job opf-closure-e
run_gate "aiqt-corpus-selftest" python3 -I -B tools/selftest_aiqt_corpus.py
run_gate "roadmap-drift"   python3 -I -B tools/gen_roadmap.py --check
run_gate "changelog-drift" python3 -I -B tools/gen_changelog.py --check
run_gate "versions"        python3 -I -B tools/check_versions.py
run_gate "release-cut-selftest" python3 -I -B tools/check_release_cut.py --self-test --red-on-revert
run_gate "release-cut"          python3 -I -B tools/check_release_cut.py
run_gate "version-monotonicity-selftest" python3 -I -B tools/check_version_monotonicity.py --self-test
run_gate "version-monotonicity" python3 -I -B tools/check_version_monotonicity.py
run_gate "branch-root-selftest" python3 -I -B tools/check_branch_root.py --self-test
run_gate "branch-root"          python3 -I -B tools/check_branch_root.py --max-lag 200
run_gate "release-delta-selftest" python3 -I -B tools/check_release_delta.py --self-test
run_gate "release-delta"          python3 -I -B tools/check_release_delta.py
run_gate "release-build-selftest" python3 -I -B tools/check_release_build.py --self-test
run_gate "release-build"          python3 -I -B tools/check_release_build.py
run_gate "clauses-selftest" python3 -I -B tools/check_clauses.py --self-test
run_gate "clauses"          python3 -I -B tools/check_clauses.py --genesis
# VER-CORE Section 12 step 6: migration engine, crosswalk tooling, archive (VC-6). The crash-injection
# self-test is the mandatory 9.3 gate; the live check_crosswalk leg reports NOT APPLICABLE in this repo
# (the pack is not an adopter install), self-tested first over synthetic trees.
run_gate "crosswalk-gen-selftest" python3 -I -B tools/gen_crosswalk.py --self-test
run_gate "crosswalk-schema-drift" python3 -I -B tools/gen_crosswalk.py --check
run_gate "crosswalk-selftest"     python3 -I -B tools/check_crosswalk.py --self-test
run_gate "crosswalk"              python3 -I -B tools/check_crosswalk.py
run_gate "migrate-crashinject"    python3 -I -B tools/migrate.py --self-test
# VER-CORE Section 12 step 7 (VC-7): pin lifecycle (pin.py) and the core doctor (doctor.py). The
# self-tests exercise the plan-4.5 scenarios over synthetic installs; the live `doctor --root .` leg
# reports NOT APPLICABLE in this repo (the pack is not an adopter install). doctor is READ-ONLY.
run_gate "pin-selftest"           python3 -I -B tools/pin.py --self-test
run_gate "doctor-selftest"        python3 -I -B tools/doctor.py --self-test
run_gate "doctor"                 python3 -I -B tools/doctor.py --root .
run_gate "rules-selftest"  python3 -I -B tools/gen_rules.py --self-test
run_gate "artifact-checksums-selftest" python3 -I -B tools/check_artifact_checksums.py --self-test
run_gate "artifact-checksums"          python3 -I -B tools/check_artifact_checksums.py
run_gate "rules-drift"     python3 -I -B tools/gen_rules.py --check
run_gate "agents-selftest" python3 -I -B tools/gen_agents.py --self-test
run_gate "agents-drift"    python3 -I -B tools/gen_agents.py --check
run_gate "first-pin-demo-selftest" python3 -I -B tools/gen_first_pin_demo.py --self-test
run_gate "first-pin-demo-drift"    python3 -I -B tools/gen_first_pin_demo.py --check
run_gate "must-deliver-selftest" python3 -I -B tools/check_must_deliver.py --self-test
run_gate "must-deliver"          python3 -I -B tools/check_must_deliver.py
run_gate "mappings-page-selftest" python3 -I -B tools/gen_mappings.py --self-test
run_gate "mappings-page-drift" python3 -I -B tools/gen_mappings.py --check
run_gate "reference-roster-selftest" python3 -I -B tools/gen_reference_facts.py --self-test
run_gate "reference-roster-drift" python3 -I -B tools/gen_reference_facts.py --check
run_gate "reference-facts-selftest" python3 -I -B tools/check_reference_facts.py --self-test
run_gate "reference-facts" python3 -I -B tools/check_reference_facts.py
run_gate "disclosure-selftest" python3 -I -B tools/gen_disclosure.py --self-test
run_gate "disclosure-drift"    python3 -I -B tools/gen_disclosure.py --check
run_gate "install-selftest" python3 -I -B tools/gen_install.py --self-test
run_gate "install-drift"    python3 -I -B tools/gen_install.py --check
run_gate "install-page-selftest" python3 -I -B tools/check_install_page.py --self-test
run_gate "install-page" python3 -I -B tools/check_install_page.py
run_gate "sized-instructions-selftest" python3 -I -B tools/check_sized_instructions.py --self-test
run_gate "sized-instructions" python3 -I -B tools/check_sized_instructions.py
run_gate "instruction-budget-selftest" python3 -I -B tools/check_instruction_budget.py --self-test
run_gate "instruction-budget" python3 -I -B tools/check_instruction_budget.py
run_gate "brief-capability-selftest" python3 -I -B tools/check_brief_capability.py --self-test  # no brief in the repository to lint
run_gate "notice-drift"    python3 -I -B tools/gen_notice.py --check
run_gate "claude-selftest" python3 -I -B tools/gen_claude.py --self-test
run_gate "claude-drift"    python3 -I -B tools/gen_claude.py --check
run_gate "adapters-drift"  python3 -I -B tools/gen_adapters.py --check
run_gate "cursor-selftest"  python3 -I -B tools/gen_cursor.py --self-test
run_gate "cursor-drift"  python3 -I -B tools/gen_cursor.py --check
run_gate "worker-pack-selftest" python3 -I -B tools/gen_worker_pack.py --self-test
run_gate "worker-pack-drift" python3 -I -B tools/gen_worker_pack.py --check
run_gate "hooks-selftest" python3 -I -B tools/gen_hooks.py --self-test
run_gate "secret-patterns-selftest" python3 -I -B tools/gen_secret_patterns.py --self-test
run_gate "secret-patterns-drift" python3 -I -B tools/gen_secret_patterns.py --check
run_gate "hooks-drift"    python3 -I -B tools/gen_hooks.py --check
run_gate "hooks-behaviour-selftest" python3 -I -B tools/selftest_aiqt_hooks.py
run_gate "char-policy-selftest" python3 -I -B tools/gen_char_policy.py --self-test
run_gate "char-policy-drift" python3 -I -B tools/gen_char_policy.py --check
run_gate "hooks-preview-selftest" python3 -I -B tools/check_hooks_preview.py --self-test
run_gate "hooks-preview" python3 -I -B tools/check_hooks_preview.py
run_gate "selftest-execution-selftest" python3 -I -B tools/check_selftest_execution.py --self-test
run_gate "orch-behaviour-selftest" python3 -I -B tools/check_selftest_execution.py --suite orch-behaviour-selftest
run_gate "ci-status-behaviour-selftest" python3 -I -B tools/check_selftest_execution.py --suite ci-status-behaviour-selftest
run_gate "git-fixture-env-core" python3 -I -B tools/check_selftest_execution.py --suite git-fixture-env-core
run_gate "git-fixture-env-config-combined" python3 -I -B tools/check_selftest_execution.py --suite git-fixture-env-config-combined
run_gate "git-fixture-env-config-malformed" python3 -I -B tools/check_selftest_execution.py --suite git-fixture-env-config-malformed
run_gate "git-fixture-env-config-system" python3 -I -B tools/check_selftest_execution.py --suite git-fixture-env-config-system
run_gate "instruction-budget-behaviour-selftest" python3 -I -B tools/check_selftest_execution.py --suite instruction-budget-selftest
run_gate "brief-capability-behaviour-selftest" python3 -I -B tools/check_selftest_execution.py --suite brief-capability-selftest
run_gate "hook-latency-selftest" python3 -I -B tools/check_selftest_execution.py --suite hook-latency-selftest
run_gate "entry-guard-selftest" python3 -I -B tools/check_selftest_execution.py --suite entry-guard-selftest
run_gate "entry-guard" python3 -I -B tools/check_entry_guard.py
run_gate "verifier-delivery-selftest" python3 -I -B tools/check_selftest_execution.py --suite verifier-delivery-selftest
run_gate "record-drift-selftest" python3 -I -B tools/check_record_drift.py --self-test
run_gate "record-drift"          python3 -I -B tools/check_record_drift.py
run_gate "record-sections-selftest" python3 -I -B tools/check_record_sections.py --self-test
run_gate "mistakes-register-selftest" python3 -I -B tools/check_mistakes_register.py --self-test
run_gate "mistakes-register"          python3 -I -B tools/check_mistakes_register.py
run_gate "mistakes-register-append-selftest" python3 -I -B tools/orch_register.py --self-test
run_gate "aei-enumerator-selftest" python3 -I -B tools/aei_backlog_md.py --self-test
run_gate "skill-selftest"  python3 -I -B tools/gen_skill.py --self-test
run_gate "skill-drift"     python3 -I -B tools/gen_skill.py --check
run_gate "gensrc-registry-selftest" python3 -I -B tools/gen_gensrc.py --self-test
run_gate "gensrc-registry-drift" python3 -I -B tools/gen_gensrc.py --check
run_gate "gensrc-failclose-selftest" python3 -I -B tools/check_gensrc_failclose.py --self-test
run_gate "gensrc-failclose" python3 -I -B tools/check_gensrc_failclose.py
run_gate "enforceability-selftest" python3 -I -B tools/gen_enforceability.py --self-test
run_gate "enforceability-drift" python3 -I -B tools/gen_enforceability.py --check
run_gate "enforcement-register-selftest" python3 -I -B tools/gen_enforcement_register.py --self-test
run_gate "enforcement-register-drift" python3 -I -B tools/gen_enforcement_register.py --check
run_gate "launcher-isolation-selftest" python3 -I -B tools/check_python_launcher_isolation.py --self-test
run_gate "launcher-isolation" python3 -I -B tools/check_python_launcher_isolation.py
run_gate "python-floor-selftest" python3 -I -B tools/check_selftest_execution.py --suite python-floor-selftest
run_gate "python-floor" python3 -I -B tools/check_python_floor.py
# The host-patch pin: the CI host-patch jobs run their suites on the release .aiqt/python-host.toml
# names, and the live leg FAILS this run when this interpreter is another patch release (a run there does
# not predict those required CI legs); when the host interpreter changes, update that file and every
# host-job pin in .github/workflows/quality.yml together.
run_gate "python-host-pin-selftest" python3 -I -B tools/check_python_host_pin.py --self-test
run_gate "python-host-pin" python3 -I -B tools/check_python_host_pin.py
# The jobs-leg-only member mirrors the registry job's CI step, which judges the host-patch jobs from
# outside them (roster symmetry; ci-parity reconciles it).
run_gate "python-host-pin-jobs" python3 -I -B tools/check_python_host_pin.py --jobs-only
run_gate "renderers-selftest"    python3 -I -B tools/gen_renderers.py --self-test
run_gate "renderers-drift"       python3 -I -B tools/gen_renderers.py --check
run_gate "manifest-gen-selftest" python3 -I -B tools/gen_manifest.py --self-test
run_gate "manifest-gen-drift"    python3 -I -B tools/gen_manifest.py --check
run_gate "manifest-selftest"     python3 -I -B tools/check_manifest.py --self-test
run_gate "manifest"              python3 -I -B tools/check_manifest.py
run_gate "byte-canon-selftest"   python3 -I -B tools/check_byte_canon.py --self-test
run_gate "byte-canon"            python3 -I -B tools/check_byte_canon.py
run_gate "clauses-manifest-sources" python3 -I -B tools/check_clauses.py --genesis --with-manifest
run_gate "placement-selftest" python3 -I -B tools/check_selftest_execution.py --suite rule-placement-selftest
run_gate "placement"      python3 -I -B tools/check_rule_placement.py
run_gate "applies-selftest" python3 -I -B tools/check_applies.py --self-test
run_gate "applies"         python3 -I -B tools/check_applies.py
run_gate "mappings"       python3 -I -B tools/check_mappings.py
# Repository-only assertion lives outside the shipped tooling: it pins the corpus below and the whole permitted
# gate invocation in both runners (--config .aiqt/cited-standards.toml and nothing else, so an appended --root
# is a finding), and validates pack.toml with its owning validator (opf/tools/check_opf_prompt_pack.py).
run_gate "cited-standards-repo-contract-selftest" python3 -I -B .github/check_cited_standards_contract.py --self-test
run_gate "cited-standards-repo-contract" python3 -I -B .github/check_cited_standards_contract.py
run_gate "cited-standards-selftest" python3 -I -B tools/check_cited_standards.py --self-test
# This repository only: the reviewed corpus adds the OPF prompt-pack members to the rule corpus. The config and
# the two .github contract scripts are not shipped, so in a tree without them this gate, the newtab-repo-contract
# gate and the cited-standards-repo-contract gate lines all exit 2 (cannot evaluate) and this runner then ends
# RESULT: FAIL.
run_gate "cited-standards"          python3 -I -B tools/check_cited_standards.py --config .aiqt/cited-standards.toml
run_gate "conformance-selftest" python3 -I -B tools/conformance.py --self-test
run_gate "conformance"    python3 -I -B tools/conformance.py --root .
run_gate "commonmark-headings-selftest" python3 -I -B opf/tools/selftest_commonmark_headings.py
run_gate "commonmark-conformance" python3 -I -B opf/tools/selftest_commonmark_conformance.py
run_gate "currency-selftest" python3 -I -B tools/check_standards_currency.py --self-test
run_gate "cwe-importer-selftest" python3 -I -B tools/import_cwe.py --self-test

run_gate "ci-parity-selftest" python3 -I -B tools/check_ci_parity.py --self-test
run_gate "ci-parity"          python3 -I -B tools/check_ci_parity.py

# Git option-table drift: the self-test exercises the parser/comparator over fixtures (no git invoked);
# the live leg compares installed git branch/tag help spellings against the hook's static option literal
# and an independently maintained role catalog, failing on membership or write-role drift.
run_gate "git-option-table-selftest" python3 -I -B tools/check_git_option_table.py --self-test
run_gate "git-option-table" python3 -I -B tools/check_git_option_table.py

# Dangerous-API AST lint (plan item 6): unsafe deserialization, disabled TLS verification, and
# shell or code execution sinks in the repo's own Python, with a reviewed in-gate allowlist; an
# unreadable or unparseable file is cannot-evaluate.
run_gate "dangerous-api-selftest" python3 -I -B tools/check_dangerous_api.py --self-test
run_gate "dangerous-api" python3 -I -B tools/check_dangerous_api.py

# QA-suite foundation: the discovery-seam / result-contract adapter, the trivial reference audit that
# proves the harness end to end, and the internal-name leak gate. Their self-tests are gating (a broken
# harness is a real failure); the internal-name scan is gating too.
run_gate "qa-adapter-selftest"      python3 -I -B tools/_qa_adapter.py --self-test
run_gate "audit-reference-selftest" python3 -I -B tools/audit_reference.py --self-test
run_gate "internal-names-selftest"  python3 -I -B tools/check_selftest_execution.py --suite internal-names-selftest
run_gate "internal-names"           python3 -I -B tools/check_internal_names.py

# ADVISORY QA digest, run under run_gate. It runs the reference audit and prints a compact digest that
# distinguishes a required-unavailable surface from an optional-disabled one. audit_reference.py --digest
# always exits 0 on a finding, so an advisory FINDING never gates; only a tool CRASH (unexpected non-zero
# exit) fails the build, which is the correct behaviour for a broken advisory tool.
run_gate "qa-advisory-digest"       python3 -I -B tools/audit_reference.py --digest

if [ "$failed" -ne 0 ]; then
  echo "FAILED GATES: ${failed_names}"
  echo "RESULT: FAIL"
  exit 1
fi
# The gitleaks scan did not run (bash found no gitleaks) while every gate passed, which needs the
# gitleaks-pin gate's own PATH lookup to have found one: the run still fails, as that gate requires,
# never passes with the scan not run.
if [ "$notrun" -ne 0 ]; then
  echo "RESULT: FAIL: the gitleaks scan did NOT RUN locally (see above)"
  exit 1
fi
echo "RESULT: PASS"
