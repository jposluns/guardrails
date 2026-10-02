#!/usr/bin/env bash
# The STANDALONE OPF gate-subset runner (OPF-SELF-CONTAIN). It runs only the OPF tooling's own gates,
# from wherever the opf/ subtree is rooted, so a standalone opf/ checkout (with NO AIQT tree reachable)
# can verify itself. The whole-pack runner is tools/run_all_checks.sh in the authoring repo; this is the
# self-contained subset the opf-standalone-closure gate exercises in physical isolation.
# Never pipe this to a truncating sink: a masked exit code defeats the gate.
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)" || exit 2

# Each gate launches isolated: `python3 -I -B <opf/tools>/<gate>.py`. `-I` (isolated mode) drops the
# script's own directory from sys.path so a tool-written sibling cannot shadow a stdlib import; `-B`
# suppresses bytecode. Paths are resolved from THIS script's own directory, never the CWD, so the runner
# works from any working directory and from a relocated or standalone opf/ tree.
export PYTHONDONTWRITEBYTECODE=1

failed=0
# Name each failing gate as it happens and list the names again before the FAILED line, so a failing
# subset never needs a hand re-run to find which gate failed.
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

run_gate "opf-homes-selftest"          python3 -I -B "$here/check_opf_homes.py" --self-test
run_gate "opf-homes-contract"          python3 -I -B "$here/check_opf_homes.py"
run_gate "opf-tooling-selftest"        python3 -I -B "$here/opf.py" --self-test
run_gate "opf-journal-direct-selftest" python3 -I -B "$here/_journal.py" --self-test
run_gate "opf-allocation-direct-selftest" python3 -I -B "$here/_opf_allocation.py" --self-test
run_gate "opf-observe-direct-selftest" python3 -I -B "$here/_opf_observe.py" --self-test
run_gate "opf-drift-selftest"          python3 -I -B "$here/check_opf_drift.py" --self-test
run_gate "opf-doctor-selftest"         python3 -I -B "$here/check_opf_doctor.py" --self-test
run_gate "opf-init-selftest"           python3 -I -B "$here/check_opf_init.py" --self-test
run_gate "opf-init-contract-selftest"  python3 -I -B "$here/_opf_init_contract.py" --self-test
run_gate "opf-init-contract-check-selftest" python3 -I -B "$here/check_opf_init_contract.py" --self-test
run_gate "opf-upgrade-selftest"        python3 -I -B "$here/check_opf_upgrade.py" --self-test
run_gate "opf-adopt-selftest"          python3 -I -B "$here/_opf_adopt.py" --self-test
run_gate "opf-adopt-apply-selftest"    python3 -I -B "$here/_opf_adopt_apply.py" --self-test
run_gate "opf-adopt-hook-selftest"     python3 -I -B "$here/_opf_adopt_hook.py" --self-test
run_gate "opf-pack-manifest-selftest"  python3 -I -B "$here/_opf_pack_manifest.py" --self-test
run_gate "opf-adopt-observe-selftest"  python3 -I -B "$here/_opf_adopt_observe.py" --self-test
run_gate "opf-prompt-pack-selftest"    python3 -I -B "$here/check_opf_prompt_pack.py" --self-test
run_gate "opf-prompt-pack"             python3 -I -B "$here/check_opf_prompt_pack.py"
run_gate "opf-oplock-selftest"         python3 -I -B "$here/_opf_oplock.py" --self-test
run_gate "opf-init-substrate-selftest" python3 -I -B "$here/_opf_init_substrate.py" --self-test
run_gate "opf-init-builders-selftest"  python3 -I -B "$here/_opf_init.py" --self-test
run_gate "opf-init-operation-selftest" python3 -I -B "$here/_opf_init_operation.py" --self-test
run_gate "opf-init-p0-selftest"        python3 -I -B "$here/check_opf_init_p0.py" --self-test --red-on-revert
run_gate "commonmark-headings-selftest" python3 -I -B "$here/selftest_commonmark_headings.py"
run_gate "commonmark-conformance"      python3 -I -B "$here/selftest_commonmark_conformance.py"

if [ "$failed" -ne 0 ]; then
  echo "FAILED GATES: ${failed_names}"
  echo "OPF STANDALONE SUBSET: FAILED"
  exit 1
fi
echo "OPF STANDALONE SUBSET: OK"
exit 0
