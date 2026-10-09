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
# This reads every paginated run from `actions/runs?head_sha=` AND the newest pages of the unfiltered
# `actions/runs` listing back to a lower bound derived from the commit's committer date, keeping the
# runs whose head_sha is exactly this commit. Both need only Actions: Read.
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
if ! COMMIT_TIME="$(git log -1 --no-show-signature --format=%ct "$SHA" --)" ||
    ! [[ "$COMMIT_TIME" =~ ^[0-9]+$ ]]; then
  printf 'ERROR: cannot read the committer date of %s.\n' "$SHA" >&2
  exit 2
fi
NOW="$(date +%s)"
DEADLINE=$(( NOW + ${CI_STATUS_TIMEOUT:-900} ))

# BOUNDED SCAN. The unfiltered listing is read newest first, one page per request, and paging stops at
# the first page that is short (the end of the listing) or holds only runs created before LOWER_BOUND.
# A run for this commit is created after the commit is pushed, so after the commit object exists; its
# created_at (GitHub's clock) can precede the committer date only when the committer's clock ran ahead.
# LOWER_BOUND is therefore min(committer date, now) minus SCAN_MARGIN_SECONDS. The clamp to now
# neutralises a committer date in the future; it can only lower the bound, so the clamp never raises the
# bound above the committer-date bound; its effect depends on the checking machine's clock, which, if
# ahead, can leave a future committer date unclamped. WHAT THE 24-HOUR MARGIN COVERS: a committer clock
# ahead of GitHub's by at most 24 hours, whatever the cause. A clock behind is always covered, since it
# only lowers the bound. A wrong time zone setting on a clock showing the right local time shifts the
# committer date by the difference of two UTC offsets, which span UTC-12 to UTC+14, so up to 26 hours
# either way: an error of up to 24 hours ahead is covered, one of 24 to 26 hours ahead is NOT. ORDERING
# JITTER: the stop rule ends the scan at a page whose runs were ALL created before LOWER_BOUND, so a run
# of this commit is missed only if the listing places it after a full page of 100 runs each created
# before LOWER_BOUND; with an accurate committer clock each of those runs was created at least 24 hours
# before it (at least 24 hours minus the clock error when the clock is ahead). Jitter on that scale is
# NOT covered. RESIDUAL (disclosed, not closed): a committer clock ahead by more than 24 hours (including
# the 24 to 26 hour wrong time zone case), still ahead when the commit was made, can place this commit's
# runs below LOWER_BOUND; they are then visible only to the head_sha query, which is exactly the parent
# script's coverage. ASSUMPTION: the listing is ordered newest first by creation, so a new run enters at
# the head (observed GitHub behaviour, not a documented contract). Cost scales with the runs created
# since LOWER_BOUND: one request for the head_sha query plus one per page, typically one or two pages for
# a recent commit; a scan that has not reached the bound within SCAN_MAX_PAGES pages (5,000 runs) is an
# API error, never a verdict.
SCAN_MARGIN_SECONDS=86400
SCAN_MAX_PAGES=50
SCAN_FROM="$COMMIT_TIME"
[ "$NOW" -lt "$SCAN_FROM" ] && SCAN_FROM="$NOW"
LOWER_BOUND=$(( SCAN_FROM - SCAN_MARGIN_SECONDS ))
# One-shot mode re-reads once, after RETRY_SECONDS, when the sources disagree about a run, the
# listing shrank between pages, or one source listed a run ID twice in any shape other than the page
# shift merged below (each is the signature of a run changing state, being deleted, being created, or
# moving between requests); a disagreement that persists is reported as an API error (exit 2).
# PAGE SHIFT (merged): a run created between two page reads of the unfiltered listing pushes every older
# run down one position, so the next page repeats the previous page's last run (page 1 holds runs[0:100]
# with total_count 150, page 2 holds runs[99:150] with total_count 151). That repeat is merged as one run
# when the repeated rows are the LEADING rows of the later page, identical in every field to the TRAILING
# rows of the page before it, and no more in number than the rise in total_count between those two
# reads. WHY THE RISE BOUNDS IT: under the ordering ASSUMPTION at SCAN_MARGIN_SECONDS (a new run enters at
# the head), between two reads the rows shift down by the runs created, minus the runs deleted above the
# page boundary, plus the runs moved from below the boundary to above it; total_count rises by the runs
# created minus every run deleted. The repeat count is at most the rise only when no run moved across
# the boundary and none was deleted below it, and a run that moves up across the boundary is exactly the
# run the scan would otherwise lose, so a merged repeat never hides a run that existed at the first
# read. A run created after the earlier page was read sits on a page already read and is not seen, as
# it would not be had it been created just after the scan. A repeat larger than the rise, a repeat in
# any other shape (within one page, or not leading and trailing), or a repeated row that differs in any
# field (status, conclusion, head SHA, name, URL, or creation time) stays an API error.
# AVAILABILITY COST (disclosed, accepted): a run deleted below the boundary while another is created
# also makes the repeat exceed the rise, and is rejected although nothing was lost; the shift check
# trusts each page's total_count as the count at that read, the assumption stated at DELETION DURING
# THE SCAN below. The head_sha-filtered source is not merged: a repeat there stays an API error.
RETRY_SECONDS=5

POLL_SECONDS=15
SETTLE_OBSERVATIONS=5
# The Actions runs API uses non-atomic offset pagination. A same-count replacement while pages are
# fetched can therefore present a stale-consistent, all-green snapshot: for example, a completed run
# can disappear from an earlier page while a new pending run moves onto a page already fetched. Under
# --wait, requiring an unchanged, all-success run-ID set across five observations (60 seconds) narrows
# that race window but cannot eliminate it. Server-side branch protection remains the backstop.
#
# The API also cannot prove that no later run will be created. A workflow created after the settle
# window, an event whose runs are delayed longer than the window, or a workflow suppressed by trigger,
# path, type, or commit-message rules is outside this mechanism's coverage. Report-once evaluates only
# its single observed snapshot. Deriving the exact expected set here would require the triggering event
# payload plus a complete GitHub workflow YAML, event-filter, and glob implementation; the SHA and
# repository files alone cannot answer that question for pull_request base branches or paths.

# One jq program, two modes. "step" validates ONE raw page of the unfiltered listing, prints "stop" or
# "more", then the page reduced to the fields used here as one compact JSON line. "verdict" receives the
# head_sha query's pages and the reduced scan pages, re-checks every completeness condition, and emits
# the run rows. Status and conclusion come FIRST in every TSV row. jq's @tsv escaping keeps tabs,
# newlines, and backslashes in display fields from becoming record delimiters, so a workflow name cannot
# move either gating field. Name and URL remain display-only. The no-run case gets an explicit sentinel
# rather than a rendered "null", because a real run's .status is nullable in the schema and must not be
# mistaken for "no run yet".
#
# TWO SOURCES. The server-side head_sha filter alone is not a complete answer: on 2026-10-06 it returned
# zero runs for commit 0a0c0ca3 while the repository's run listing showed run 37498869615 for that exact
# head_sha as completed/success, an hour after the same filtered query had listed that run as
# in_progress. A failed run dropping out the same way would leave only its green siblings, so the scan
# runs on EVERY query, whatever the filtered query returned, and the two sources are merged by run ID.
#
# Fail closed (API error, never "no run" and never a verdict) when: either source fails; a page or any
# record in either source is malformed (every scan record's id, head_sha, and created_at are validated
# before it counts toward completeness or results, created_at must be a calendar-valid UTC time that
# formats back to the same string, and a matching scan record is validated as fully as a filtered one);
# the scan did not end on a terminal page within SCAN_MAX_PAGES; a page other than the last is not full;
# any scan page's total_count is below the number of records up to and including that page, whether the
# page is full or short and whatever ended the scan; the listing's total_count fell between two pages;
# the sources, or two pages, disagree about a run ID's head_sha (checked across ALL records, BEFORE
# selecting this commit's runs); two records of this commit's run disagree about status, conclusion,
# name, or URL; the head_sha-filtered source lists a run ID twice; the unfiltered listing repeats a run
# ID in any shape other than the page shift merged at RETRY_SECONDS (offset pagination shifted between
# page reads in a way the rise in total_count does not explain, so some run may have gone unseen); a
# scan that reached the end of the listing (a short last page) read a number of rows, merged repeats
# included, other than that page's total_count (each merged repeat stands for one run created at the
# head after the earlier page was read); or the filtered source's unique count differs from its
# total_count.
#
# DELETION DURING THE SCAN. Offset pagination skips a run only when the runs ahead of the page boundary
# shift up, which needs more deletions ahead of the boundary than insertions; new runs enter at the
# head, so every skip makes total_count fall between the two page reads. Rejecting a falling
# total_count therefore closes that skip, ASSUMING each page's total_count is the untruncated count of
# the listing at that read (unverified live; GitHub documents a 1,000-result cap only for filtered
# listings). A total_count that is itself truncated would defeat this check and the short-last-page
# check alike. A same-count replacement within the filtered source, described above, stays open.
CI_STATUS_JQ='
def malformed_page:
  (type != "object")
  or ((.total_count | type) != "number")
  or (.total_count < 0)
  or ((.total_count | floor) != .total_count)
  or ((.workflow_runs | type) != "array")
  or ((.workflow_runs | length) > 100);
def malformed_pages:
  (type != "array") or (length == 0) or any(.[]; malformed_page);
def malformed_id:
  (type != "object")
  or ((.id | type) != "number")
  or (.id <= 0)
  or ((.id | floor) != .id)
  or ((.head_sha | type) != "string")
  or ((.head_sha | test("^([0-9a-f]{40}|[0-9a-f]{64})$")) | not);
def malformed_scan_record:
  malformed_id
  or ((.created_at | type) != "string")
  or ((.created_at | try (fromdateiso8601 | todateiso8601) catch null) != .created_at);
def malformed_run:
  malformed_id
  or (.head_sha != $requested_sha)
  or ((.status != null)
      and (((.status | type) != "string") or ((.status | length) == 0)))
  or ((.conclusion != null)
      and (((.conclusion | type) != "string") or ((.conclusion | length) == 0)))
  or ((.name | type) != "string")
  or ((.name | length) == 0)
  or ((.html_url | type) != "string")
  or ((.html_url | length) == 0);
def terminal_page:
  ((.workflow_runs | length) < 100)
  or all(.workflow_runs[]; (.created_at | fromdateiso8601) < $lower_bound);
def unique_id_count: map(.id) | unique | length;
def shift_repeat_count($previous):
  [.workflow_runs[] | .id as $id | select(any($previous.workflow_runs[]; .id == $id))] | length;
def explained_shift($previous):
  shift_repeat_count($previous) as $repeats
  | ($repeats == 0)
    or (($repeats <= (.total_count - $previous.total_count))
        and (.workflow_runs[:$repeats] == $previous.workflow_runs[-$repeats:]));
def scan_page_overrun($index):
  .total_count < (100 * $index + (.workflow_runs | length));
def projection: [.head_sha, .status, .conclusion, .name, .html_url];
if $mode == "step" then
  if (length != 1) or (.[0] | malformed_page) then
    error("malformed workflow-runs response")
  elif any(.[0].workflow_runs[]; malformed_scan_record) then
    error("malformed workflow run record")
  else
    .[0]
    | (if terminal_page then "stop" else "more" end),
      ({total_count,
        workflow_runs: [.workflow_runs[]
                        | {id, head_sha, status, conclusion, name, html_url, created_at}]}
       | tojson)
  end
elif $mode != "verdict" then
  error("unknown mode")
elif (length != 2) then
  error("malformed workflow-runs response")
else
  .[0] as $filtered_pages
  | .[1] as $scan_pages
  | if ($filtered_pages | malformed_pages) or ($scan_pages | malformed_pages) then
      error("malformed workflow-runs response")
    else
      ([$filtered_pages[] | .workflow_runs[]]) as $filtered_all
      | ([$scan_pages[] | .workflow_runs[]]) as $scan_all
      | ($scan_pages | length) as $page_count
      | ($scan_pages[-1]) as $last
      | if any($filtered_all[]; malformed_run)
          or any($scan_all[]; malformed_scan_record)
          or any($scan_all[]; (.head_sha == $requested_sha) and malformed_run) then
          error("malformed workflow run record")
        elif $page_count > $max_pages then
          error("unfiltered workflow-runs listing not bounded within the page limit")
        elif any($scan_pages[:-1][]; (.workflow_runs | length) != 100)
          or (($last | terminal_page) | not)
          or any(range(0; $page_count); . as $index | $scan_pages[$index] | scan_page_overrun($index)) then
          error("incomplete unfiltered workflow-runs listing")
        elif any(range(1; $page_count);
            $scan_pages[.].total_count < $scan_pages[. - 1].total_count) then
          error("workflow-runs listing shrank during the scan")
        else
          ([$scan_pages[0].workflow_runs[]]
           + [range(1; $page_count) as $index
              | $scan_pages[$index]
              | shift_repeat_count($scan_pages[$index - 1]) as $repeats
              | .workflow_runs[$repeats:][]]) as $scan_kept
          | (($filtered_all + $scan_all) | sort_by(.id) | group_by(.id)) as $by_id
          | if any($by_id[]; (([.[].head_sha] | unique | length) > 1)) then
              error("conflicting duplicate workflow-run records")
            else
              [$by_id[] | select(.[0].head_sha == $requested_sha)] as $runs
              | ([$filtered_pages[].total_count] | unique) as $totals
              | if any($runs[]; ((map(projection) | unique | length) > 1)) then
                  error("conflicting duplicate workflow-run records")
                elif ($filtered_all | unique_id_count) != ($filtered_all | length) then
                  error("duplicate run id within the head_sha-filtered listing")
                elif any(range(1; $page_count);
                    . as $index | $scan_pages[$index] | explained_shift($scan_pages[$index - 1]) | not)
                  or (($scan_kept | unique_id_count) != ($scan_kept | length)) then
                  error("duplicate run id within the unfiltered listing")
                elif (($last.workflow_runs | length) < 100)
                  and ($last.total_count != ($scan_all | length)) then
                  error("incomplete unfiltered workflow-runs listing")
                elif (($totals | length) != 1)
                  or (($filtered_all | unique_id_count) != $totals[0]) then
                  error("inconsistent paginated workflow-runs snapshot")
                elif ($runs | length) == 0 then
                  "__NORUN__"
                else
                  $runs[]
                  | .[0]
                  | [(.status // "-"), (.conclusion // "-"), (.id | tostring), .name, .html_url]
                  | @tsv
                end
            end
        end
    end
end'

ci_jq() {
  jq -s -r --arg mode "$1" --arg requested_sha "$SHA" --argjson lower_bound "$LOWER_BOUND" \
    --argjson max_pages "$SCAN_MAX_PAGES" "$CI_STATUS_JQ"
}

query() {
  local filtered raw step decision view page_no=1 scan_json=""
  {
    filtered="$(gh api "repos/$REPO/actions/runs?head_sha=$SHA&per_page=100" --paginate --slurp)" ||
      return 1
    while :; do
      if [ "$page_no" -gt "$SCAN_MAX_PAGES" ]; then
        echo "unfiltered workflow-runs listing not bounded within $SCAN_MAX_PAGES pages"
        return 1
      fi
      raw="$(gh api "repos/$REPO/actions/runs?per_page=100&page=$page_no")" || return 1
      step="$(printf '%s\n' "$raw" | ci_jq step)" || return 1
      decision="$(printf '%s\n' "$step" | sed -n 1p)"
      view="$(printf '%s\n' "$step" | sed -n 2p)"
      # Each view is one compact object from jq, so joining views with commas stays one JSON array.
      [ -n "$scan_json" ] && scan_json+=","
      scan_json+="$view"
      case "$decision" in
        stop) break ;;
        more) page_no=$((page_no + 1)) ;;
        *) echo "unexpected scan step output"; return 1 ;;
      esac
    done
    printf '%s\n[%s]\n' "$filtered" "$scan_json" | ci_jq verdict
  } 2>&1
}

report() {
  local status="$1" concl="$2" name="$3" url="$4"
  printf '%s  %s: %s / %s\n' "$(date -u +%H:%M:%SZ)" "$name" "$status" "$concl"
  [ -n "${url:-}" ] && [ "$url" != "-" ] && printf '  %s\n' "$url"
}

SETTLE_FINGERPRINT=""
SETTLE_COUNT=0
last_summary="no workflow run registered"
RETRIED=0

while :; do
  lines="$(query)"
  query_rc=$?
  # A failed query is an API error even if its output resembles a valid sentinel or run row.
  # Report-once fails with exit 2. Under --wait, ride through a transient query failure until the
  # deadline, keeping API diagnostics separate from display fields in successful run rows.
  if [ "$query_rc" -ne 0 ] || [ -z "$lines" ]; then
    case "$lines" in
      *"conflicting duplicate workflow-run records"*|*"workflow-runs listing shrank during the scan"*|\
      *"duplicate run id within the"*)
        if [ "$WAIT" != "--wait" ] && [ "$RETRIED" -eq 0 ]; then
          RETRIED=1
          echo "NOTE: workflow-run records changed between requests; re-reading once."
          echo "  raw: $lines"
          sleep "$RETRY_SECONDS"
          continue
        fi
        ;;
    esac
    SETTLE_FINGERPRINT=""
    SETTLE_COUNT=0
    last_summary="workflow-runs API query failed"
    echo "ERROR: could not read workflow runs for ${SHA} in ${REPO}"
    echo "  raw: ${lines}"
    case "$lines" in
      *"Resource not accessible"*|*"Not Found"*|*"404"*) exit 2 ;;
    esac
    [ "$WAIT" != "--wait" ] && exit 2
    if [ "$(date +%s)" -ge "$DEADLINE" ]; then
      echo "RESULT: TIMEOUT after ${CI_STATUS_TIMEOUT:-900}s; ${last_summary}."
      exit 1
    fi
    sleep "$POLL_SECONDS"
    continue
  fi

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

  settled=0
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
    last_summary="all ${#run_ids[@]} observed workflow run(s) successful; settle observation ${SETTLE_COUNT}/${SETTLE_OBSERVATIONS}"
    if [ "$SETTLE_COUNT" -ge "$SETTLE_OBSERVATIONS" ]; then
      settled=1
    else
      echo "RESULT: ${last_summary}; waiting for late-created runs."
    fi
  fi

  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    echo "RESULT: TIMEOUT after ${CI_STATUS_TIMEOUT:-900}s; ${last_summary}."
    exit 1
  fi
  [ "$settled" -eq 1 ] && exit 0
  sleep "$POLL_SECONDS"
done
