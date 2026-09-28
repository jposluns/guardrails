#!/bin/sh
# OPF CI floor: the portable shell recipe (enforcement pack, spec 1.3.0 14.1: the pack MUST provide CI checks).
#
#   sh opf-ci.sh [ROOT]      ROOT defaults to the current directory (the checked-out revision)
#
# It runs two read-only checks over the checked-out revision, in order, and stops at the first failure:
#   1. opf doctor --require-store   store integrity; a repository with no OPF store exits 2, so a store
#                                    that was removed cannot pass CI vacuously
#   2. opf render --check           the declared views match their sources (1 means drift)
# Exit status is 0/1/2 only (0 clean, 1 finding, 2 cannot-evaluate): a failing step exits with its own
# 0/1/2, forwarded unchanged, and ANY other child status (a launch failure such as the shell's own 127
# command-not-found or 126 not-executable, or a signal death) is normalized to 2, mirroring the
# check_opf_doctor clamp, so an out-of-vocabulary status is never read as a verdict. 0 only when both
# steps pass. It writes nothing.
#
# Environment overrides:
#   OPF_PYTHON  the interpreter (default: python3); always launched isolated (-I -B)
#   OPF_TOOL    the path to opf.py (default: ../../tools/opf.py beside this file, the pack layout)
#
# Inert until adoption installs it; the CI receipt-identity comparison is a later release (U25).
set -u
if [ "$#" -gt 1 ]; then
    echo "opf-ci: usage: sh opf-ci.sh [ROOT]" >&2
    exit 2
fi
root=${1:-.}
# CDPATH is cleared for this one command: a CDPATH match makes `cd` PRINT the resolved directory, which
# would corrupt `here` with a second output line (the portable idiom).
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 2
opf_python=${OPF_PYTHON:-python3}
opf_tool=${OPF_TOOL:-$here/../../tools/opf.py}
# Run one check step and normalize its status to the recipe vocabulary: the step's own 0/1/2 is
# returned unchanged, and any other status becomes 2 (cannot-evaluate).
run_step() {
    "$opf_python" -I -B "$opf_tool" "$@"
    rc=$?
    case "$rc" in
        0|1|2) return "$rc" ;;
    esac
    return 2
}
run_step doctor --require-store --root "$root" || exit $?
run_step render --check --root "$root"
