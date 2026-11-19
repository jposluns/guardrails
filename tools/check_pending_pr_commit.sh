#!/bin/sh
# Advisory commit adapter for the stale pending-claim check (.aiqt/pending-pr-claims.md).
#
# Runs tools/check_pending_pr_claims.py --staged --live, which judges the staged blob (complete
# content, unchanged context included) of every declared record surface the index changes, and shows
# its findings. ADVISORY BY POLICY: the commit always continues (exit 0), but an incomplete evaluation
# is never reported as passed. A pre-commit hook may call this script; no hook in this repository is
# wired to it by this change.
root=$(git rev-parse --show-toplevel) && cd "$root" || {
  echo "INCOMPLETE: pending-claim check NOT evaluated (the repository root could not be found); this is not a pass. Advisory: the commit continues." >&2
  exit 0
}
python3 -I -B tools/check_pending_pr_claims.py --staged --live
rc=$?
case "$rc" in
  0) ;;
  1) echo "ADVISORY: the staged records above say a merged or closed pull request is still pending or open. Advisory: the commit continues; correct the record." >&2 ;;
  *) echo "INCOMPLETE: pending-claim evaluation did not complete (exit $rc); this is not a pass. Advisory: the commit continues." >&2 ;;
esac
exit 0
