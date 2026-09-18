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

run_gate() {
  local name="$1"; shift
  echo "--- ${name} ---"
  if "$@"; then :; else failed=1; fi
  echo
}

run_gate "opf-tooling-selftest"        python3 -I -B "$here/opf.py" --self-test
run_gate "opf-drift-selftest"          python3 -I -B "$here/check_opf_drift.py" --self-test
run_gate "opf-doctor-selftest"         python3 -I -B "$here/check_opf_doctor.py" --self-test
run_gate "opf-init-selftest"           python3 -I -B "$here/check_opf_init.py" --self-test
run_gate "opf-upgrade-selftest"        python3 -I -B "$here/check_opf_upgrade.py" --self-test
run_gate "opf-import-selftest"         python3 -I -B "$here/check_opf_import.py" --self-test
run_gate "commonmark-headings-selftest" python3 -I -B "$here/selftest_commonmark_headings.py"
run_gate "commonmark-conformance"      python3 -I -B "$here/selftest_commonmark_conformance.py"

if [ "$failed" -ne 0 ]; then
  echo "OPF STANDALONE SUBSET: FAILED"
  exit 1
fi
echo "OPF STANDALONE SUBSET: OK"
exit 0
