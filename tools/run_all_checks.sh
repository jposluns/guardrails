#!/usr/bin/env bash
# Run every quality gate, in the same order CI runs them.
# Never pipe this to a truncating sink: a masked exit code defeats the gate.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

# Never let a gate leave Python bytecode: a stray __pycache__/*.pyc in the shippable surface trips the
# portability gate as a non-portable file class, a spurious local FAIL a fresh CI checkout never sees.
# Suppress bytecode for every gate below, so THIS runner never creates one. A gate runner reports the
# tree, it does not mutate it: a stray cache left by another tool is a real dirty-tree signal the
# portability gate should surface. If it does, delete the SPECIFIC __pycache__ path the gate names (a
# scoped `rm -rf <that dir>`). Do NOT blanket `git clean` ignored paths, which would also delete
# unrelated local files (a stray .venv, .idea, or node_modules); and this runner never auto-sweeps.
export PYTHONDONTWRITEBYTECODE=1

failed=0
notrun=0

run_gate() {
  local name="$1"; shift
  echo "--- ${name} ---"
  if "$@"; then :; else failed=1; fi
  echo
}

run_gate "secrets"   python3 tools/check_secrets.py
run_gate "secrets-selftest" python3 tools/check_secrets.py --self-test

# gitleaks is a second, independent secret gate. If it is not installed locally it is
# reported NOT RUN rather than skipped silently: a gate that quietly does not run is
# indistinguishable from one that passed, which is the failure this wording prevents.
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
    failed=1
  fi
else
  echo "NOT RUN: gitleaks is not on PATH locally. CI still runs it, so this is a gap"
  echo "  in THIS run only, not in the pipeline. Install it to close the gap:"
  echo "  see the pinned version and checksum in .github/workflows/quality.yml"
  notrun=1
fi
echo
run_gate "leaks"     python3 tools/check_leaks.py
run_gate "msg-leaks-selftest" python3 tools/check_msg_leaks.py --self-test  # real scan needs CI event context
run_gate "portability-selftest" python3 tools/check_portability.py --self-test
run_gate "portability" python3 tools/check_portability.py
run_gate "dashes"    python3 tools/check_no_dashes.py
run_gate "links"     python3 tools/check_links.py
run_gate "site-selftest" python3 tools/check_site.py --self-test
run_gate "site"      python3 tools/check_site.py
run_gate "overclaim-selftest" python3 tools/check_overclaim.py --self-test
run_gate "overclaim" python3 tools/check_overclaim.py
run_gate "footer-selftest" python3 tools/check_footer.py --self-test
run_gate "footer" python3 tools/check_footer.py
run_gate "newtab-selftest" python3 tools/check_newtab.py --self-test
run_gate "newtab" python3 tools/check_newtab.py
run_gate "roadmap-drift"   python3 tools/gen_roadmap.py --check
run_gate "changelog-drift" python3 tools/gen_changelog.py --check
run_gate "versions"        python3 tools/check_versions.py
run_gate "version-monotonicity-selftest" python3 tools/check_version_monotonicity.py --self-test
run_gate "version-monotonicity" python3 tools/check_version_monotonicity.py
run_gate "release-delta-selftest" python3 tools/check_release_delta.py --self-test
run_gate "release-delta"          python3 tools/check_release_delta.py
run_gate "release-build-selftest" python3 tools/check_release_build.py --self-test
run_gate "release-build"          python3 tools/check_release_build.py
run_gate "clauses-selftest" python3 tools/check_clauses.py --self-test
run_gate "clauses"          python3 tools/check_clauses.py --genesis
# VER-CORE Section 12 step 6: migration engine, crosswalk tooling, archive (VC-6). The crash-injection
# self-test is the mandatory 9.3 gate; the live check_crosswalk leg reports NOT APPLICABLE in this repo
# (the pack is not an adopter install), self-tested first over synthetic trees.
run_gate "crosswalk-gen-selftest" python3 tools/gen_crosswalk.py --self-test
run_gate "crosswalk-schema-drift" python3 tools/gen_crosswalk.py --check
run_gate "crosswalk-selftest"     python3 tools/check_crosswalk.py --self-test
run_gate "crosswalk"              python3 tools/check_crosswalk.py
run_gate "migrate-crashinject"    python3 tools/migrate.py --self-test
run_gate "rules-selftest"  python3 tools/gen_rules.py --self-test
run_gate "artifact-checksums-selftest" python3 tools/check_artifact_checksums.py --self-test
run_gate "artifact-checksums"          python3 tools/check_artifact_checksums.py
run_gate "rules-drift"     python3 tools/gen_rules.py --check
run_gate "agents-drift"    python3 tools/gen_agents.py --check
run_gate "mappings-page-drift" python3 tools/gen_mappings.py --check
run_gate "reference-roster-selftest" python3 tools/gen_reference_facts.py --self-test
run_gate "reference-roster-drift" python3 tools/gen_reference_facts.py --check
run_gate "reference-facts-selftest" python3 tools/check_reference_facts.py --self-test
run_gate "reference-facts" python3 tools/check_reference_facts.py
run_gate "disclosure-selftest" python3 tools/gen_disclosure.py --self-test
run_gate "disclosure-drift"    python3 tools/gen_disclosure.py --check
run_gate "install-selftest" python3 tools/gen_install.py --self-test
run_gate "install-drift"    python3 tools/gen_install.py --check
run_gate "notice-drift"    python3 tools/gen_notice.py --check
run_gate "claude-drift"    python3 tools/gen_claude.py --check
run_gate "adapters-drift"  python3 tools/gen_adapters.py --check
run_gate "cursor-selftest"  python3 tools/gen_cursor.py --self-test
run_gate "cursor-drift"  python3 tools/gen_cursor.py --check
run_gate "hooks-selftest" python3 tools/gen_hooks.py --self-test
run_gate "secret-patterns-drift" python3 tools/gen_secret_patterns.py --check
run_gate "hooks-drift"    python3 tools/gen_hooks.py --check
run_gate "hooks-behaviour-selftest" env PYTHONDONTWRITEBYTECODE=1 python3 tools/selftest_aiqt_hooks.py
run_gate "skill-selftest"  python3 tools/gen_skill.py --self-test
run_gate "skill-drift"     python3 tools/gen_skill.py --check
run_gate "gensrc-registry-selftest" python3 tools/gen_gensrc.py --self-test
run_gate "gensrc-registry-drift" python3 tools/gen_gensrc.py --check
run_gate "gensrc-failclose-selftest" python3 tools/check_gensrc_failclose.py --self-test
run_gate "gensrc-failclose" python3 tools/check_gensrc_failclose.py
run_gate "enforceability-selftest" python3 tools/gen_enforceability.py --self-test
run_gate "enforceability-drift" python3 tools/gen_enforceability.py --check
run_gate "renderers-selftest"    python3 tools/gen_renderers.py --self-test
run_gate "renderers-drift"       python3 tools/gen_renderers.py --check
run_gate "manifest-gen-selftest" python3 tools/gen_manifest.py --self-test
run_gate "manifest-gen-drift"    python3 tools/gen_manifest.py --check
run_gate "manifest-selftest"     python3 tools/check_manifest.py --self-test
run_gate "manifest"              python3 tools/check_manifest.py
run_gate "byte-canon-selftest"   python3 tools/check_byte_canon.py --self-test
run_gate "byte-canon"            python3 tools/check_byte_canon.py
run_gate "clauses-manifest-sources" python3 tools/check_clauses.py --genesis --with-manifest
run_gate "placement"      python3 tools/check_rule_placement.py
run_gate "mappings"       python3 tools/check_mappings.py
run_gate "conformance-selftest" python3 tools/conformance.py --self-test
run_gate "conformance"    python3 tools/conformance.py --root .
run_gate "currency-selftest" python3 tools/check_standards_currency.py --self-test

if [ "$failed" -ne 0 ]; then
  echo "RESULT: FAIL"
  exit 1
fi
if [ "$notrun" -ne 0 ]; then
  echo "RESULT: PASS, but one or more gates did NOT RUN locally (see above)"
  exit 0
fi
echo "RESULT: PASS"
