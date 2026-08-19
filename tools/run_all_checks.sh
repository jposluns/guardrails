#!/usr/bin/env bash
# Run every quality gate, in the same order CI runs them.
# Never pipe this to a truncating sink: a masked exit code defeats the gate.
set -uo pipefail
cd "$(dirname "$0")/.."

failed=0
notrun=0

run_gate() {
  local name="$1"; shift
  echo "--- ${name} ---"
  if "$@"; then :; else failed=1; fi
  echo
}

run_gate "secrets"   python3 tools/check_secrets.py

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
run_gate "portability-selftest" python3 tools/check_portability.py --self-test
run_gate "portability" python3 tools/check_portability.py
run_gate "dashes"    python3 tools/check_no_dashes.py
run_gate "links"     python3 tools/check_links.py
run_gate "site"      python3 tools/check_site.py
run_gate "roadmap-drift"   python3 tools/gen_roadmap.py --check
run_gate "changelog-drift" python3 tools/gen_changelog.py --check
run_gate "versions"        python3 tools/check_versions.py
run_gate "version-monotonicity-selftest" python3 tools/check_version_monotonicity.py --self-test
run_gate "version-monotonicity" python3 tools/check_version_monotonicity.py
run_gate "rules-drift"     python3 tools/gen_rules.py --check
run_gate "agents-drift"    python3 tools/gen_agents.py --check
run_gate "mappings-page-drift" python3 tools/gen_mappings.py --check
run_gate "notice-drift"    python3 tools/gen_notice.py --check
run_gate "claude-drift"    python3 tools/gen_claude.py --check
run_gate "adapters-drift"  python3 tools/gen_adapters.py --check
run_gate "cursor-drift"  python3 tools/gen_cursor.py --check
run_gate "hooks-selftest" python3 tools/gen_hooks.py --self-test
run_gate "hooks-drift"    python3 tools/gen_hooks.py --check
run_gate "hooks-behaviour-selftest" python3 tools/selftest_aiqt_hooks.py
run_gate "skill-drift"     python3 tools/gen_skill.py --check
run_gate "skill-selftest"  python3 tools/gen_skill.py --self-test
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
