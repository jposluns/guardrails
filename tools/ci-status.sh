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
# This reads every paginated run from `actions/runs?head_sha=`, which needs only Actions: Read.
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

POLL_SECONDS=15
SETTLE_OBSERVATIONS=5
# The Actions runs API cannot prove that no later run will be created. Under --wait, require an
# unchanged, all-success run-ID set across five observations (60 seconds). A workflow created after
# that window, an event whose runs are delayed longer than the window, or a workflow suppressed by
# trigger, path, type, or commit-message rules is outside this mechanism's coverage. Report-once
# evaluates only its single observed snapshot. Deriving the exact expected set here would require the
# triggering event payload plus a complete GitHub workflow YAML, event-filter, and glob implementation;
# the SHA and repository files alone cannot answer that question for pull_request base branches or paths.

query() {
  # Status and conclusion come FIRST in every TSV row. jq's @tsv escaping keeps tabs, newlines, and
  # backslashes in display fields from becoming record delimiters, so a workflow name cannot move either
  # gating field. Name and URL remain display-only.
  # The no-run case (empty array) gets an explicit sentinel rather than a rendered "null", because
  # a real run's .status is nullable in the schema and must not be mistaken for "no run yet".
  # Pagination is part of the verdict: de-duplicate by run ID, then require the observed unique count to
  # equal the API's stable total_count. A malformed or racing snapshot is an API error, never green.
  gh api "repos/${REPO}/actions/runs?head_sha=${SHA}&per_page=100" --paginate --slurp \
    --jq '
      . as $pages
      | if (($pages | type) != "array") or (($pages | length) == 0) then
          error("malformed workflow-runs response")
        elif any($pages[];
            (type != "object")
            or ((.total_count | type) != "number")
            or (.total_count < 0)
            or ((.total_count | floor) != .total_count)
            or ((.workflow_runs | type) != "array")) then
          error("malformed workflow-runs response")
        else
          ([$pages[] | .workflow_runs[]]) as $all_runs
          | if any($all_runs[];
              ((.id | type) != "number")
              or (.id <= 0)
              or ((.id | floor) != .id)
              or ((.status != null)
                  and (((.status | type) != "string") or ((.status | length) == 0)))
              or ((.conclusion != null)
                  and (((.conclusion | type) != "string") or ((.conclusion | length) == 0)))
              or ((.name | type) != "string")
              or ((.name | length) == 0)
              or ((.html_url | type) != "string")
              or ((.html_url | length) == 0)) then
              error("malformed workflow run record")
            else
              ([$pages[].total_count] | unique) as $totals
              | ($all_runs | unique_by(.id) | sort_by(.id)) as $runs
              | if (($totals | length) != 1) or (($runs | length) != $totals[0]) then
                  error("inconsistent paginated workflow-runs snapshot")
                elif ($runs | length) == 0 then
                  "__NORUN__"
                else
                  $runs[]
                  | [(.status // "-"), (.conclusion // "-"), (.id | tostring), .name, .html_url]
                  | @tsv
                end
            end
        end' 2>&1
}

report() {
  local status="$1" concl="$2" name="$3" url="$4"
  printf '%s  %s: %s / %s\n' "$(date -u +%H:%M:%SZ)" "$name" "$status" "$concl"
  [ -n "${url:-}" ] && [ "$url" != "-" ] && printf '  %s\n' "$url"
}

SETTLE_FINGERPRINT=""
SETTLE_COUNT=0
last_summary="no workflow run registered"

while :; do
  lines="$(query)"
  query_rc=$?
  # No workflow run registered yet (empty array): the query emits an explicit sentinel. This is
  # briefly true right after a push, and is distinct from "in progress", from an error, and from a
  # real run whose .status happens to be null (which falls through to the unrecognized-status path).
  if [ "$lines" = "__NORUN__" ]; then
    SETTLE_FINGERPRINT=""
    SETTLE_COUNT=0
    last_summary="no workflow run registered"
    printf '%s  no workflow run registered for this commit yet\n' "$(date -u +%H:%M:%SZ)"
    [ "$WAIT" != "--wait" ] && exit 2
    if [ "$(date +%s)" -ge "$DEADLINE" ]; then
      echo "RESULT: TIMEOUT; no workflow run ever appeared for ${SHA}."
      echo "  Check that a workflow is triggered by this event and branch."
      exit 1
    fi
    sleep "$POLL_SECONDS"
    continue
  fi
  if [[ "$lines" == *"not accessible"* || "$lines" == *"Not Found"* || -z "$lines" ||
        "$query_rc" -ne 0 ]]; then
    echo "ERROR: could not read workflow runs for ${SHA} in ${REPO}"
    echo "  raw: ${lines}"
    exit 2
  fi

  run_ids=()
  failure_names=()
  failure_conclusions=()
  nonterminal_names=()
  unknown_statuses=()
  while IFS=$'\t' read -r status concl id name url; do
    report "$status" "$concl" "$name" "$url"
    run_ids+=("$id")
    case "$status" in
      completed)
        if [ "$concl" != "success" ]; then
          failure_names+=("$name")
          failure_conclusions+=("$concl")
        fi
        ;;
      queued|in_progress|waiting|requested|pending|action_required)
        nonterminal_names+=("${name} (${status})")
        ;;
      *)
        unknown_statuses+=("$status")
        ;;
    esac
  done <<<"$lines"

  if [ "${#failure_names[@]}" -ne 0 ]; then
    echo "RESULT: CI concluded with non-success workflow runs:"
    for ((i = 0; i < ${#failure_names[@]}; i++)); do
      printf '  %s: %s\n' "${failure_names[i]}" "${failure_conclusions[i]}"
    done
    exit 1
  fi

  if [ "${#unknown_statuses[@]}" -ne 0 ]; then
    SETTLE_FINGERPRINT=""
    SETTLE_COUNT=0
    last_summary="unrecognized run status '${unknown_statuses[0]}'"
    if [ "$WAIT" != "--wait" ]; then
      echo "ERROR: unrecognized run status '${unknown_statuses[0]}' for ${SHA} in ${REPO}"
      echo "  raw: ${lines}"
      exit 2
    fi
  elif [ "${#nonterminal_names[@]}" -ne 0 ]; then
    SETTLE_FINGERPRINT=""
    SETTLE_COUNT=0
    last_summary="${#nonterminal_names[@]} workflow run(s) not terminal"
    for item in "${nonterminal_names[@]}"; do
      last_summary+="; ${item}"
    done
    if [ "$WAIT" != "--wait" ]; then
      echo "RESULT: ${#nonterminal_names[@]} workflow run(s) not terminal; use --wait to gate on completion."
      exit 1
    fi
  else
    if [ "$WAIT" != "--wait" ]; then
      exit 0
    fi
    fingerprint="$(IFS=,; printf '%s' "${run_ids[*]}")"
    if [ "$fingerprint" = "$SETTLE_FINGERPRINT" ]; then
      SETTLE_COUNT=$((SETTLE_COUNT + 1))
    else
      SETTLE_FINGERPRINT="$fingerprint"
      SETTLE_COUNT=1
    fi
    last_summary="all ${#run_ids[@]} workflow run(s) successful; settle observation ${SETTLE_COUNT}/${SETTLE_OBSERVATIONS}"
    if [ "$SETTLE_COUNT" -ge "$SETTLE_OBSERVATIONS" ]; then
      exit 0
    fi
    echo "RESULT: ${last_summary}; waiting for late-created runs."
  fi

  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    echo "RESULT: TIMEOUT after ${CI_STATUS_TIMEOUT:-900}s; ${last_summary}."
    exit 1
  fi
  sleep "$POLL_SECONDS"
done
