#!/usr/bin/env bash
# Report, and optionally wait for, the CI conclusion for a commit.
#
# WHY THIS EXISTS. `gh pr checks`, `gh pr status`, and `gh run watch` all resolve
# GraphQL `statusCheckRollup`, which reads the Checks API. A fine-grained personal
# access token CANNOT be granted Checks access: it is a standing GitHub limitation,
# not a missing setting, so those commands return
#   "Resource not accessible by personal access token"
# and no permission change fixes it. Only a GitHub App, or a classic token with the
# `repo` scope, can read Checks.
#
# DO NOT substitute `commits/<sha>/status`. GitHub Actions publishes through Check
# Runs, not the older Statuses API, so that endpoint returns `state: pending` with
# `total_count: 0` FOREVER, even on a commit whose workflow succeeded. Verified on
# 2026-08-08 against commit 7666cff, whose Quality run had concluded `success`.
# Anything treating it as the green signal hangs; anything inverting it merges on a lie.
#
# This reads `actions/runs?head_sha=`, which needs only Actions: Read.
#
# Usage:
#   tools/ci-status.sh                 # current HEAD, report once
#   tools/ci-status.sh <sha>           # a given commit, report once
#   tools/ci-status.sh <sha> --wait    # poll until terminal, bounded and fail-loud
#
# Exit codes: 0 success, 1 failed / timed out / not-yet-terminal on a one-shot check,
# 2 no run found or API error. Report-once (no --wait) never returns 0 for a non-terminal run.
set -uo pipefail

REPO="${CI_STATUS_REPO:-jposluns/guardrails}"
# Resolve whatever rev was given (short SHA, branch, tag, HEAD~1) to a FULL 40-character
# SHA. The `head_sha=` filter matches only on the full SHA, so a short one silently returns
# zero runs and this script then reports "no workflow run registered" for a commit whose run
# actually succeeded. That fails closed, blocking a merge rather than permitting one, but it
# is still a misreport, so resolve the input rather than trusting it. Fail loudly when the
# rev does not resolve, because an unresolvable rev is an error, not an absent run.
# Accept the flag in any position, so `ci-status.sh --wait` means "HEAD, and wait" rather
# than treating the flag as a rev. The first non-flag argument is the rev; anything starting
# with a hyphen is a flag.
SHA_IN="HEAD"
WAIT=""
for arg in "$@"; do
  case "$arg" in
    --wait) WAIT="--wait" ;;
    -*) printf 'ERROR: unknown option %s (only --wait is supported).\n' "$arg" >&2; exit 2 ;;
    *) SHA_IN="$arg" ;;
  esac
done
if ! SHA="$(git rev-parse --verify --quiet "${SHA_IN}^{commit}")"; then
  printf 'ERROR: %s does not resolve to a commit in this repository.\n' "${SHA_IN}" >&2
  exit 2
fi
DEADLINE=$(( $(date +%s) + ${CI_STATUS_TIMEOUT:-900} ))

query() {
  # Status and conclusion come FIRST, so a workflow whose NAME contains a "|" cannot shift the
  # machine-read fields (the delimiter-injection false-green: a run named "x|completed|success"
  # used to parse as status=completed). Name and url are free text and sit last, where an extra
  # "|" only affects display, never the gating decision.
  # The no-run case (empty array) gets an explicit sentinel rather than a rendered "null", because
  # a real run's .status is nullable in the schema and must not be mistaken for "no run yet".
  gh api "repos/${REPO}/actions/runs?head_sha=${SHA}" \
    --jq 'if (.workflow_runs | length) == 0 then "__NORUN__" else (.workflow_runs[0] | "\(.status)|\(.conclusion // "-")|\(.name)|\(.html_url)") end' 2>&1
}

report() {
  local line="$1"
  IFS='|' read -r status concl name url <<<"$line"
  printf '%s  %s: %s / %s\n' "$(date -u +%H:%M:%SZ)" "$name" "$status" "$concl"
  [ -n "${url:-}" ] && [ "$url" != "-" ] && printf '  %s\n' "$url"
}

while :; do
  line="$(query)"
  # No workflow run registered yet (empty array): the query emits an explicit sentinel. This is
  # briefly true right after a push, and is distinct from "in progress", from an error, and from a
  # real run whose .status happens to be null (which falls through to the unrecognized-status path).
  if [ "$line" = "__NORUN__" ]; then
    printf '%s  no workflow run registered for this commit yet\n' "$(date -u +%H:%M:%SZ)"
    [ "$WAIT" != "--wait" ] && exit 2
    if [ "$(date +%s)" -ge "$DEADLINE" ]; then
      echo "RESULT: TIMEOUT; no workflow run ever appeared for ${SHA}."
      echo "  Check that a workflow is triggered by this event and branch."
      exit 1
    fi
    sleep 15
    continue
  fi
  if [[ "$line" == *"not accessible"* || "$line" == *"Not Found"* || -z "$line" ]]; then
    echo "ERROR: could not read workflow runs for ${SHA} in ${REPO}"
    echo "  raw: ${line}"
    exit 2
  fi
  report "$line"
  status="$(cut -d'|' -f1 <<<"$line")"
  concl="$(cut -d'|' -f2 <<<"$line")"

  case "$status" in
    completed)
      [ "$concl" = "success" ] && exit 0
      echo "RESULT: CI concluded '${concl}', not success."
      exit 1
      ;;
    queued|in_progress|waiting|requested|pending|action_required)
      # A recognized NON-terminal status. Report-once must not read as success (exit 0 here
      # was the fail-open: a caller gating with `ci-status.sh $sha && merge` merged on pending).
      if [ "$WAIT" != "--wait" ]; then
        echo "RESULT: run not terminal (status: '${status}'); use --wait to gate on completion."
        exit 1
      fi
      ;;
    *)
      # Not a GitHub run status: an API error / rate-limit / unexpected text slipped past the
      # guard above, leaving cut with a non-status field. Report-once fails loud as an API error
      # (exit 2, not green). Under --wait this may be a transient blip, so ride through to the
      # next poll exactly as before; a persistent one ends at the deadline as a timeout (exit 1).
      if [ "$WAIT" != "--wait" ]; then
        echo "ERROR: unrecognized run status '${status}' for ${SHA} in ${REPO}"
        echo "  raw: ${line}"
        exit 2
      fi
      ;;
  esac

  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    echo "RESULT: TIMEOUT after ${CI_STATUS_TIMEOUT:-900}s; last status '${status}'."
    exit 1
  fi
  sleep 15
done
