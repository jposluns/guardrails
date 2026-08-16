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
echo "--- secrets (gitleaks) ---"
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
run_gate "dashes"    python3 tools/check_no_dashes.py
run_gate "links"     python3 tools/check_links.py
run_gate "site"      python3 tools/check_site.py
run_gate "roadmap-drift"   python3 tools/gen_roadmap.py --check
run_gate "changelog-drift" python3 tools/gen_changelog.py --check
run_gate "rules-drift"     python3 tools/gen_rules.py --check

if [ "$failed" -ne 0 ]; then
  echo "RESULT: FAIL"
  exit 1
fi
if [ "$notrun" -ne 0 ]; then
  echo "RESULT: PASS, but one or more gates did NOT RUN locally (see above)"
  exit 0
fi
echo "RESULT: PASS"
