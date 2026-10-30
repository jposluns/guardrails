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
# Name each failing gate as it happens and, when the run reaches the end, list the names again
# before the FAILED line, so a failing subset never needs a hand re-run to find which gate failed. A
# failure at or before the special-file precheck instead stops the run at a STOP line (exit 2) right
# after its GATE FAILED line.
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

# Child exit status first (see _child_status.py): under an inherited SIGCHLD ignore every gate below would
# read its failed children as passes, so the probe runs as the first gate and its exit 2 fails the subset.
run_gate "child-status"                python3 -I -B "$here/_child_status.py"

# SPECIAL-FILE-PRECHECK: ONE walk of the tree (the authoring repository when this is its opf/
# subtree, else the standalone opf/ tree; derived from this script's fixed location and confirmed by
# git when git can answer, never from an upward .git search) right after the child-status probe and
# BEFORE any other gate runs (the probe imports only the standard library under -I and reads no file of
# the tree but its own script); the precheck carries the inline child-status guard, so under an ignored
# SIGCHLD it refuses (exit 2) and the run stops here too; a special file, a
# symlink to one, an unresolvable symlink that git does not ignore, a symlink to a directory outside
# the root, git-ignored or not, or, while git can list the tracked paths, a git-ignored symlink to an
# in-root directory that is, lies inside or holds a directory the walk does not enter (the
# repository's own git dir unless the walk enters it, and the pruned root .git entry in front of
# it), is refused by name and the run STOPS here with exit 2, so no gate or self-test below can
# block on a plain read of a FIFO: a tree copy follows a directory link and reads every file under
# it with a plain blocking read except a FIFO, which shutil raises on before opening it, so a
# character device there can block the copy. Its link exemptions, exactly: a dangling symlink and a
# git-ignored unresolvable symlink pass (every read open of either fails at once; a write open
# through a dangling link creates its target instead); a symlink to a regular file passes (a plain
# read of a regular file on a local filesystem does not block; FUSE or network storage can); and,
# while git can list the tracked paths, any other git-ignored in-root directory link is not walked
# under its own path (its target is walked under the target's path). It runs as two run_gate steps,
# each followed by the STOP line, which ends the run there with exit 2 once any gate has failed (both
# steps are gate lines, so the first line after the probe that names Python is a gate line). The
# first step is the BOOTSTRAP, a shell test only: python3 would block LOADING the precheck script
# itself if that path were a FIFO, so a missing, symlinked or non-regular script fails this named step
# and the run stops before python3 touches it. The step runs the authoring repository's CI bootstrap
# test under sh -c (never python3), so a failure prints the same error line before GATE FAILED. A
# failed probe above already stops the run at the first STOP
# line. Residual: the interpreter binary, THIS runner script and the probe script above are read
# before the bootstrap step can run.
run_gate "special-file-precheck-bootstrap" sh -c '[ -f "$1" ] && ! [ -h "$1" ] || { echo "error: $1: not a regular non-symlink file; cannot run the special-file precheck; fail-closed" >&2; exit 1; }' sh "$here/_containment.py"
[ "$failed" -eq 0 ] || exit 2
run_gate "special-file-precheck"       python3 -I -B "$here/_containment.py" --precheck
[ "$failed" -eq 0 ] || exit 2

run_gate "child-status-selftest"       python3 -I -B "$here/_child_status.py" --self-test
run_gate "opf-homes-selftest"          python3 -I -B "$here/check_opf_homes.py" --self-test
run_gate "opf-homes-contract"          python3 -I -B "$here/check_opf_homes.py"
run_gate "opf-tooling-selftest"        python3 -I -B "$here/opf.py" --self-test
run_gate "opf-journal-direct-selftest" python3 -I -B "$here/_journal.py" --self-test
run_gate "opf-observe-direct-selftest" python3 -I -B "$here/_opf_observe.py" --self-test
run_gate "opf-drift-selftest"          python3 -I -B "$here/check_opf_drift.py" --self-test
run_gate "opf-doctor-selftest"         python3 -I -B "$here/check_opf_doctor.py" --self-test
run_gate "opf-init-selftest"           python3 -I -B "$here/check_opf_init.py" --self-test
run_gate "opf-init-contract-selftest"  python3 -I -B "$here/_opf_init_contract.py" --self-test
run_gate "opf-init-contract-check-selftest" python3 -I -B "$here/check_opf_init_contract.py" --self-test
run_gate "opf-adopt-selftest"          python3 -I -B "$here/_opf_adopt.py" --self-test
run_gate "opf-adopt-apply-selftest"    python3 -I -B "$here/_opf_adopt_apply.py" --self-test
run_gate "opf-adopt-state-selftest"    python3 -I -B "$here/_opf_adopt_state.py" --self-test
run_gate "opf-adopt-hook-selftest"     python3 -I -B "$here/_opf_adopt_hook.py" --self-test
run_gate "opf-adopt-complete-selftest" python3 -I -B "$here/_opf_adopt_complete.py" --self-test
run_gate "opf-import-batch-selftest"   python3 -I -B "$here/_opf_import_batch.py" --self-test
run_gate "opf-import-complete-selftest" python3 -I -B "$here/_opf_import_complete.py" --self-test
run_gate "opf-pack-manifest-selftest"  python3 -I -B "$here/_opf_pack_manifest.py" --self-test
run_gate "opf-adopt-observe-selftest"  python3 -I -B "$here/_opf_adopt_observe.py" --self-test
run_gate "opf-adopt-discover-selftest" python3 -I -B "$here/_opf_adopt_discover.py" --self-test
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
