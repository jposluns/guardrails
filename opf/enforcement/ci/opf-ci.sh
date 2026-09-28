#!/bin/sh
# OPF CI floor: the portable shell recipe (enforcement pack, spec 14.1: the pack MUST provide CI checks).
#
#   sh opf-ci.sh [ROOT]      ROOT defaults to the current directory (the checked-out revision)
#
# It runs two read-only checks over the checked-out revision, in order, and stops at the first failure:
#   1. opf doctor --require-store   store integrity; a repository with no OPF store exits 2, so a store
#                                    that was removed cannot pass CI vacuously
#   2. opf render --check           the declared views match their sources (1 means drift)
# Exit status is the failing step's own 0/1/2 (0 clean, 1 finding, 2 cannot-evaluate); 0 only when both
# pass. It writes nothing.
#
# Environment overrides:
#   OPF_PYTHON  the interpreter (default: python3); always launched isolated (-I -B)
#   OPF_TOOL    the path to opf.py (default: ../../tools/opf.py beside this file, the pack layout)
#
# Inert until adoption installs it; the CI receipt-identity comparison is a later release.
set -u
if [ "$#" -gt 1 ]; then
    echo "opf-ci: usage: sh opf-ci.sh [ROOT]" >&2
    exit 2
fi
root=${1:-.}
here=$(cd -- "$(dirname -- "$0")" && pwd) || exit 2
opf_python=${OPF_PYTHON:-python3}
opf_tool=${OPF_TOOL:-$here/../../tools/opf.py}
"$opf_python" -I -B "$opf_tool" doctor --require-store --root "$root" || exit $?
"$opf_python" -I -B "$opf_tool" render --check --root "$root"
