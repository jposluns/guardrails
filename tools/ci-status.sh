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
# runs whose head_sha is exactly this commit. Both need only Actions: Read. Under --wait, THE
# PULL-REQUEST PROBE below also reads the repository's open pull requests (`pulls`) and, when none at
# this head into the base reads MERGEABLE, the pull requests associated with this commit
# (`commits/<sha>/pulls`): on a public repository those are public data, expected to need no further
# permission (not verified live); a private repository's token also needs Pull requests: Read, or
# every --wait exits 2.
#
# Every invocation requires a completed successful run of the workflow file
# .github/workflows/quality.yml on the commit (see THE REQUIRED WORKFLOW below), except --match-tree,
# which requires one on a commit with the same TREE and refuses that tree when any completed run of
# that file on it, within the window THE TREE MATCH below states, concluded otherwise.
#
# Usage:
#   tools/ci-status.sh                              # current HEAD, report once
#   tools/ci-status.sh <sha>                        # a given commit, report once
#   tools/ci-status.sh <sha> --wait                 # poll until terminal, bounded and fail-loud,
#                                                   # refusing a head that, with no merge into the
#                                                   # base shown in its associated pull requests,
#                                                   # has no open pull request into it now
#                                                   # or only CONFLICTING ones (THE PULL-REQUEST PROBE)
#   tools/ci-status.sh <sha> --require-base main    # additionally trust only pull_request runs
#                                                   # recorded against base main, and require a
#                                                   # conforming completed successful run of the
#                                                   # workflow file .github/workflows/quality.yml (see
#                                                   # THE BASE-BRANCH REQUIREMENT below)
#   ... --require-base main --require-workflow PATH # the same, requiring the workflow whose file
#                                                   # is PATH instead of the default quality.yml
#   tools/ci-status.sh --match-tree [--head REV] [--require-job-success NAME]...
#                                                   # succeed only on a completed successful run of
#                                                   # .github/workflows/quality.yml whose head commit
#                                                   # has the TREE of REV (default HEAD), each NAME
#                                                   # concluded success in that run, and no completed
#                                                   # run of it on that tree concluded otherwise. The
#                                                   # window: up to 5 listing pages, all read in full,
#                                                   # with trees read for the newest 20 distinct head
#                                                   # commits; a non-success run on one of them with
#                                                   # that tree refuses wherever it is listed, a run
#                                                   # on a further commit is not compared, and no
#                                                   # successful run listed after the first such run
#                                                   # is accepted (THE TREE MATCH)
#
# Exit codes (--match-tree has its own, stated at THE TREE MATCH below): 0 success, 1 failed / timed out / not-yet-terminal on a one-shot check, a run that
# does not satisfy --require-base, or no completed successful run of the required workflow file
# (a conforming one under --require-base; report-once refuses at once; --wait refuses after its
# settle window), a --wait whose head, with no merge into the base shown in its associated pull
# requests, has no open pull request (still at the last of the probe's reads), only open pull
# requests into another base, or only open pull requests into the base that GitHub reports
# CONFLICTING (see THE PULL-REQUEST PROBE below; a head whose associated pull requests show a merge
# into the base is decided by its runs alone), 2 no run found, API error, a malformed run
# record (in one-shot and --wait mode alike), a missing, unusable or repeated --require-base or
# --require-workflow value, the repository cannot be determined (see THE REPOSITORY IS THE
# CHECKOUT'S OWN below), or a --wait whose pull-request probe is refused the pull requests, reads a
# malformed response, or at its bound still reads UNKNOWN, a pull request that changed between the
# listing and its own read, or a failed read (a response that is not valid JSON included). So each of
# those three refusals (exit 1) comes only from a read that also read the head's associated pull
# requests: where that associated read fails at every read, and in none of the PERMANENT forms, the
# probe makes all 12 reads (11 re-reads) and exits 2 (before the probe read the associated pull
# requests, each of the three was refused at once, exit 1).
# Report-once (no --wait) never returns 0 for a non-terminal run.
set -uo pipefail

# Resolve whatever rev was given (short SHA, branch, tag, HEAD~1) to a FULL 40-character
# SHA. The `head_sha=` filter matches only on the full SHA, so a short one silently returns
# zero runs and this script then reports "no workflow run registered" for a commit whose run
# actually succeeded. That fails closed, blocking a merge rather than permitting one, but it
# is still a misreport, so resolve the input rather than trusting it. Fail loudly when the
# rev does not resolve, because an unresolvable rev is an error, not an absent run.
# Accept the flags in any position, so `ci-status.sh --wait` means "HEAD, and wait" rather
# than treating the flag as a rev. The one non-flag argument is the rev; a second non-flag
# argument is refused (exit 2), never read last-wins, because a stray positional that resolves
# (a branch name) would silently change which commit is checked. Anything starting
# with a hyphen is a flag, except the one argument that follows --require-base or
# --require-workflow, which is its value. Each value is validated AS IT IS CONSUMED, before any
# git or API read: a missing, empty, whitespace-only or hyphen-led value is refused (exit 2),
# because an empty or whitespace-only value would silently mean "no requirement", the exact
# fail-open these options exist to prevent, and a hyphen-led one is a swallowed flag, not a
# name. A repeated --require-base or --require-workflow is also refused (exit 2), never read
# last-wins: under last-wins a forgotten earlier value would silently replace the requirement
# the caller believes is in force. --require-workflow without --require-base is refused too, so
# the workflow file a plain read requires is always quality.yml. --head and --require-job-success
# take a value the same way, validated as it is consumed; --head and --match-tree are refused when
# repeated, and so is a --require-job-success naming a job already named. --head and
# --require-job-success are accepted only with --match-tree, and --match-tree refuses a positional rev
# (its rev is taken through --head only), --wait, --require-base and --require-workflow, so no option
# is ever silently ignored.
SHA_IN="HEAD"
SHA_IN_SET=0
WAIT=""
REQUIRE_BASE=""
REQUIRE_BASE_SET=0
MATCH_TREE=0
HEAD_SET=0
REQUIRE_JOBS=()
# The workflow FILE whose completed successful run every invocation demands (a conforming one
# under --require-base), matched against each run's recorded workflow path (the runs API's .path),
# never its display name: any workflow can carry name: Quality, so a name match would let a
# same-named run from another file stand in. quality.yml is this repository's aggregated workflow,
# the one required status the merge lane reads.
REQUIRE_WORKFLOW=".github/workflows/quality.yml"
REQUIRE_WORKFLOW_SET=0
EXPECT_VALUE=""
for arg in "$@"; do
  if [ -n "$EXPECT_VALUE" ]; then
    option="$EXPECT_VALUE"
    EXPECT_VALUE=""
    label="branch name"
    case "$option" in
      --require-workflow) label="workflow path" ;;
      --head) label="rev" ;;
      --require-job-success) label="job name" ;;
    esac
    case "$arg" in
      -*)
        printf 'ERROR: %s needs a %s; a hyphen-led value is refused (a swallowed flag, not a name).\n' "$option" "$label" >&2
        exit 2
        ;;
      *[![:space:]]*) ;;
      *)
        printf 'ERROR: %s needs a %s; an empty or whitespace-only value is refused.\n' "$option" "$label" >&2
        exit 2
        ;;
    esac
    if [ "$option" = "--require-base" ]; then
      if [ "$REQUIRE_BASE_SET" -eq 1 ]; then
        printf 'ERROR: --require-base is given more than once; a repeated option is refused rather than read last-wins.\n' >&2
        exit 2
      fi
      REQUIRE_BASE="$arg"
      REQUIRE_BASE_SET=1
    elif [ "$option" = "--require-workflow" ]; then
      if [ "$REQUIRE_WORKFLOW_SET" -eq 1 ]; then
        printf 'ERROR: --require-workflow is given more than once; a repeated option is refused rather than read last-wins.\n' >&2
        exit 2
      fi
      REQUIRE_WORKFLOW="$arg"
      REQUIRE_WORKFLOW_SET=1
    elif [ "$option" = "--head" ]; then
      if [ "$HEAD_SET" -eq 1 ]; then
        printf 'ERROR: --head is given more than once; a repeated option is refused rather than read last-wins.\n' >&2
        exit 2
      fi
      SHA_IN="$arg"
      HEAD_SET=1
    else
      for job in "${REQUIRE_JOBS[@]}"; do
        if [ "$job" = "$arg" ]; then
          printf 'ERROR: --require-job-success names the same job more than once; a repeated requirement is refused.\n' >&2
          exit 2
        fi
      done
      REQUIRE_JOBS+=("$arg")
    fi
    continue
  fi
  case "$arg" in
    --wait) WAIT="--wait" ;;
    --require-base) EXPECT_VALUE="--require-base" ;;
    --require-workflow) EXPECT_VALUE="--require-workflow" ;;
    --match-tree)
      if [ "$MATCH_TREE" -eq 1 ]; then
        printf 'ERROR: --match-tree is given more than once; a repeated option is refused.\n' >&2
        exit 2
      fi
      MATCH_TREE=1
      ;;
    --head) EXPECT_VALUE="--head" ;;
    --require-job-success) EXPECT_VALUE="--require-job-success" ;;
    -*) printf 'ERROR: unknown option %s (only --wait, --require-base BRANCH, --require-workflow PATH, --match-tree, --head REV and --require-job-success NAME are supported).\n' "$arg" >&2; exit 2 ;;
    *)
      if [ "$SHA_IN_SET" -eq 1 ]; then
        printf 'ERROR: more than one rev was given; a repeated rev is refused rather than read last-wins.\n' >&2
        exit 2
      fi
      SHA_IN="$arg"
      SHA_IN_SET=1
      ;;
  esac
done
if [ -n "$EXPECT_VALUE" ]; then
  label="branch name"
  case "$EXPECT_VALUE" in
    --require-workflow) label="workflow path" ;;
    --head) label="rev" ;;
    --require-job-success) label="job name" ;;
  esac
  printf 'ERROR: %s needs a %s and none was given.\n' "$EXPECT_VALUE" "$label" >&2
  exit 2
fi
if [ "$MATCH_TREE" -eq 1 ]; then
  if [ "$SHA_IN_SET" -eq 1 ]; then
    printf 'ERROR: --match-tree takes its rev through --head only; a positional rev is refused.\n' >&2
    exit 2
  fi
  if [ -n "$WAIT" ] || [ "$REQUIRE_BASE_SET" -eq 1 ] || [ "$REQUIRE_WORKFLOW_SET" -eq 1 ]; then
    printf 'ERROR: --match-tree is a separate read; --wait, --require-base and --require-workflow are refused with it.\n' >&2
    exit 2
  fi
elif [ "$HEAD_SET" -eq 1 ] || [ "${#REQUIRE_JOBS[@]}" -ne 0 ]; then
  printf 'ERROR: --head and --require-job-success are accepted only with --match-tree.\n' >&2
  exit 2
fi
if [ "$REQUIRE_WORKFLOW_SET" -eq 1 ] && [ "$REQUIRE_BASE_SET" -ne 1 ]; then
  printf 'ERROR: --require-workflow is accepted only with --require-base; a read without --require-base always requires the workflow file .github/workflows/quality.yml.\n' >&2
  exit 2
fi
if ! SHA="$(git rev-parse --verify --quiet "${SHA_IN}^{commit}")"; then
  printf 'ERROR: %s does not resolve to a commit in this repository.\n' "${SHA_IN}" >&2
  exit 2
fi
if ! COMMIT_TIME="$(git log -1 --no-show-signature --format=%ct "$SHA" --)" ||
    ! [[ "$COMMIT_TIME" =~ ^[0-9]+$ ]]; then
  printf 'ERROR: cannot read the committer date of %s.\n' "$SHA" >&2
  exit 2
fi

# THE REPOSITORY IS THE CHECKOUT'S OWN. The runs read are those of the repository this checkout's origin
# remote names, never a built-in name: a copied, forked, or adopter checkout that read a fixed repository
# would report another repository's CI runs as its own. The origin URL is read with
# `git remote get-url --all origin` (so url.<base>.insteadOf rewriting applies, as it does to a fetch) and
# must match one of these forms exactly, with no other host, port, user name, credentials, path segment,
# or trailing slash:
#   https://github.com/OWNER/REPO    ssh://git@github.com/OWNER/REPO    git@github.com:OWNER/REPO
# each optionally ending in .git. OWNER is 1 to 39 ASCII letters, digits, and hyphens, starting and ending
# with a letter or digit; REPO is 1 to 100 ASCII letters, digits, '.', '_', and '-', other than '.' and
# '..'. git's output is read byte for byte: command substitution strips every trailing line feed, which
# would turn a URL ending in a line feed, or a valid URL followed by a newline-only URL, into one
# apparently valid URL, so a sentinel follows the output, git's exit status is kept, and exactly one
# final line feed (the last URL's record delimiter) is removed before validation. An origin that is absent or has no
# URL, that git cannot read (get-url exits other than 0 or 2), that has more than one URL or a URL holding
# a line feed (git's output cannot tell these two apart), or that does not match is cannot-evaluate (exit
# 2, before any API call), never a fallback; the message names which, and does not echo the URL, which may
# carry credentials. CI_STATUS_REPO, when set and nonempty, only confirms: it must equal the derived
# OWNER/REPO exactly (case included), or the script refuses with exit 2. That refusal names only the
# derived OWNER/REPO and never echoes CI_STATUS_REPO, which is unvalidated and may carry credentials or
# line breaks.
# WHAT THIS DOES NOT CLOSE. No variable of this script selects another repository, but the read follows
# the origin as git config resolves it: a url.<base>.insteadOf rewrite from any configuration git reads
# (system, global, repository, or worktree files, their includes, and the GIT_CONFIG_* environment
# variables such as GIT_CONFIG_COUNT, GIT_CONFIG_KEY_<n>, and GIT_CONFIG_VALUE_<n>) can map the origin to
# another accepted URL, whose repository's runs are then read. Whoever controls this checkout's git
# configuration or its environment holds that power, as they do over a fetch. RESIDUAL (disclosed, not
# closed): gh, not this script, picks the API host. No --hostname is passed, so GH_HOST or gh's own
# configuration may send the same OWNER/REPO request to another host (gh's behaviour, not verified here).
# AVAILABILITY COST (disclosed, accepted): a real checkout whose origin names this repository in a form
# the strict match refuses exits 2 and cannot use this script. Refused real-world forms include
# credential-bearing https (a token added by an insteadOf rewrite included), an ssh host alias
# (git@ALIAS:OWNER/REPO), ssh://git@ssh.github.com:443/OWNER/REPO, git://github.com/OWNER/REPO, and any
# GitHub Enterprise host.
# The character classes are spelled out, never ranges, so the match does not depend on the locale.
GITHUB_NAME_ALNUM='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
GITHUB_OWNER_RE="^[${GITHUB_NAME_ALNUM}]([${GITHUB_NAME_ALNUM}-]{0,37}[${GITHUB_NAME_ALNUM}])?\$"
GITHUB_REPO_RE="^[${GITHUB_NAME_ALNUM}._-]{1,100}\$"
# Print OWNER/REPO for an accepted origin URL; return 1, printing nothing, for any other string.
github_repo_from_url() {
  local url="$1" path owner name
  case "$url" in
    https://github.com/*) path="${url#https://github.com/}" ;;
    ssh://git@github.com/*) path="${url#ssh://git@github.com/}" ;;
    git@github.com:*) path="${url#git@github.com:}" ;;
    *) return 1 ;;
  esac
  path="${path%.git}"
  [[ "$path" == */* ]] || return 1
  owner="${path%%/*}"
  name="${path#*/}"
  [[ "$owner" =~ $GITHUB_OWNER_RE ]] || return 1
  [[ "$name" =~ $GITHUB_REPO_RE ]] || return 1
  [ "$name" != "." ] && [ "$name" != ".." ] || return 1
  printf '%s/%s\n' "$owner" "$name"
}
# Byte for byte, as stated above: the sentinel '.' keeps git's trailing line feeds through the command
# substitution, which exits with git's own status; then the sentinel and one final line feed are removed.
ORIGIN_URLS="$(git remote get-url --all origin; origin_rc=$?; printf '.'; exit "$origin_rc")"
origin_rc=$?
ORIGIN_URLS="${ORIGIN_URLS%.}"
ORIGIN_URLS="${ORIGIN_URLS%$'\n'}"
if [ "$origin_rc" -eq 2 ] || { [ "$origin_rc" -eq 0 ] && [ -z "$ORIGIN_URLS" ]; }; then
  printf 'ERROR: cannot evaluate: this checkout has no origin remote, or its origin remote has no URL, so the repository whose CI runs to read is unknown (no default repository is assumed).\n' >&2
  exit 2
elif [ "$origin_rc" -ne 0 ]; then
  printf 'ERROR: cannot evaluate: git could not read the origin remote URL (git remote get-url exited %s).\n' "$origin_rc" >&2
  exit 2
elif [[ "$ORIGIN_URLS" == *$'\n'* ]]; then
  printf 'ERROR: cannot evaluate: the origin remote has more than one URL, or a URL containing a line break, so the repository whose CI runs to read is ambiguous.\n' >&2
  exit 2
fi
if ! REPO="$(github_repo_from_url "$ORIGIN_URLS")"; then
  printf 'ERROR: cannot evaluate: the origin remote URL is not one of https://github.com/OWNER/REPO, ssh://git@github.com/OWNER/REPO or git@github.com:OWNER/REPO (each optionally ending in .git), so the repository whose CI runs to read is unknown.\n' >&2
  exit 2
fi
if [ -n "${CI_STATUS_REPO:-}" ] && [ "$CI_STATUS_REPO" != "$REPO" ]; then
  # CI_STATUS_REPO is unvalidated (it may carry credentials or line breaks): print only the validated REPO.
  printf "ERROR: CI_STATUS_REPO is set but does not equal %s, the repository this checkout's origin remote names (its value is not echoed); refusing to read another repository's CI runs.\n" "$REPO" >&2
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
# the head and no run moves within it (observed GitHub behaviour, not a documented contract; RESIDUAL at
# RETRY_SECONDS states what a moving run costs). Cost scales with the runs created
# since LOWER_BOUND: one request for the head_sha query plus one per page, typically one or two pages for
# a recent commit; a scan that has not reached the bound within SCAN_MAX_PAGES pages (5,000 runs) is an
# API error, never a verdict.
SCAN_MARGIN_SECONDS=86400
SCAN_MAX_PAGES=50
SCAN_FROM="$COMMIT_TIME"
[ "$NOW" -lt "$SCAN_FROM" ] && SCAN_FROM="$NOW"
LOWER_BOUND=$(( SCAN_FROM - SCAN_MARGIN_SECONDS ))
# One-shot mode re-reads once, after RETRY_SECONDS, when the sources disagree about a run, the
# listing shrank between pages, one source listed a run ID twice in any shape other than the page shift
# merged below, or an all-success read merged a page shift (each is the signature of a run changing
# state, being deleted, being created, or moving between requests). A disagreement that persists, or an
# all-success re-read that merged a page shift again, is reported as an API error (exit 2).
# PAGE SHIFT (merged, never a pass): a run created between two page reads of the unfiltered listing pushes
# every older run down one position, so the next page repeats the previous page's last run (page 1 holds
# runs[0:100] with total_count 150, page 2 holds runs[99:150] with total_count 151). That repeat is merged
# as one run when the repeated rows are the LEADING rows of the later page, identical in every field to
# the TRAILING rows of the page before it, and no more in number than the rise in total_count between
# those two reads. A repeat larger than the rise, a repeat in any other shape (within one page, or not
# leading and trailing), or a repeated row that differs in any field (status, conclusion, head SHA, name,
# URL, or creation time) stays an API error. WHY A MERGED READ NEVER PASSES: between two reads the rows
# shift down by the runs created, minus the runs deleted above the page boundary, plus the runs moved
# from below the boundary to above it, minus the runs moved from above it to below it; total_count rises
# by the runs created minus every run deleted. The repeat count minus the rise is therefore the runs
# moved up, minus the runs moved down, plus the runs deleted below the boundary. A run that moves up
# across the boundary is a run the scan skips. Under the ordering ASSUMPTION no run moves and a repeat
# within the rise skips none, but one run moving down offsets one moving up: the repeat then stays within
# the rise while the run that moved up goes unseen, and nothing in the listing tells the two cases apart.
# So a read that merged any repeat is INCOMPLETE. It still reports a failed or not-terminal run of this
# commit (exit 1: every run it shows was read), but it never exits 0. One-shot mode re-reads an
# all-success merged read once; under --wait such a read resets the settle count, as an API error does,
# and the poll continues. A pass therefore needs a read with no repeated row, which the script before the
# merge would also have passed: the merge adds no way to pass. A run created after the earlier page was
# read sits on a page already read and is not seen, as it would not be had it been created just after
# the scan.
# RESIDUAL (disclosed, not closed; it predates the merge): the counts see runs created and deleted, never
# runs moved. Between two page reads, a run that moves up across a page boundary is skipped, and a run
# that moves from above the boundary to below the last row the scan reads makes the scan skip the run
# then first below the boundary. When the moves and creations in that interval leave no repeated row (for
# example, a run moving up offset by one moving down, with no run created), every count reconciles and
# the read can pass with a run of this commit unseen. One-shot mode reads such a snapshot once; under
# --wait, the five settle observations narrow the residual but cannot close it.
# AVAILABILITY COST (disclosed, accepted): an all-success one-shot check whose read and re-read both merge
# a page shift exits 2 although nothing may have been lost (run it again, or use --wait); a run deleted
# below the boundary while another is created also makes the repeat exceed the rise, and is rejected
# although nothing was lost; the shift check trusts each page's total_count as the count at that read,
# the assumption stated at DELETION DURING THE SCAN below. The head_sha-filtered source is not merged: a
# repeat there stays an API error.
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

# THE REQUIRED WORKFLOW (every invocation). quality.yml runs on a pull request only when the pull
# request is labelled, so a head can carry nothing but its msg-leaks.yml run, which would otherwise
# read green with no Quality evidence at all. Every invocation, with or without --require-base and
# --wait, therefore requires at least one completed successful run whose workflow FILE is
# REQUIRE_WORKFLOW (quality.yml unless --require-base names another with --require-workflow), matched
# inside jq on the run's raw recorded .path, never its display name; under --require-base that run
# must also conform (see below). Absent one the head reads as missing, never as passing (exit 1,
# naming the workflow file): report-once refuses on its single snapshot, and --wait keeps polling
# within the settle window first, because a run can still be registered late on this head. In every
# mode each run of the commit must record its workflow path as a nonempty string: a run whose .path
# is missing, not a string, or empty is a malformed run record (exit 2, naming the run id, in
# one-shot and --wait mode alike), never skipped and never read as some other file, because whether
# it is the required workflow file cannot be evaluated.
#
# THE BASE-BRANCH REQUIREMENT (--require-base BRANCH). The merge lane trusts
# a green run for a merge into BRANCH only when that run evaluated its pull request against BRANCH: a
# run that passed while the PR targeted another branch says nothing about a merge into BRANCH, and
# quality.yml no longer carries `edited`, so a base retarget alone schedules no fresh run. Under this
# option a run counts only when its event is pull_request AND the pull request GitHub recorded ON THE
# RUN names BRANCH as its base (the runs API's pull_requests[].base.ref). That stored record, not the
# PR's current base read from the pulls API for the run's head SHA, is the authoritative source:
# GitHub fills workflow_run.pull_requests when the run is created and does not rewrite it on later PR
# edits (observed behaviour: a run created before its PR opens keeps an empty array forever, which a
# matched-by-head recomputation would backfill), so it names the base the run actually evaluated (its
# GITHUB_BASE_REF), while the PR's current base reflects a retarget made AFTER the run and would
# accept exactly the stale pre-retarget run this option exists to refuse. EVERY run of the commit
# must satisfy the requirement, not just one: skipping nonconforming runs instead would let a
# conforming green sibling (for example the msg-leaks.yml run a retarget does re-trigger) stand in
# for the missing pre-retarget Quality run. AND a conforming run set alone is not enough: after a
# retarget to main the only run on the head can be that msg-leaks.yml run (quality.yml filters
# pull_request on branches: [main] and does not list edited, so the retarget schedules no fresh
# Quality run), and that lone conforming sibling would otherwise read green with no Quality
# evidence at all. The option therefore ALSO requires at least one completed, successful,
# conforming run whose workflow FILE is the one --require-workflow names (default
# .github/workflows/quality.yml, the aggregated workflow whose status the merge lane reads),
# matched by the run's recorded workflow path (the runs API's .path), never by its display
# name: any workflow can carry name: Quality, so a name match would let a same-named run from
# another file stand in; absent such a run the result is unmet (exit 1, naming the missing
# workflow file): report-once refuses on its single snapshot, and --wait keeps polling within
# the settle window first, because a qualifying run can still be registered late on this head
# (re-applying the merge-train label schedules one). A run recorded against another
# base (a pre-retarget run), a run of another event (a push run), and a run whose base cannot be
# established (an empty pull_requests array, or an entry naming no base branch, which GitHub also
# leaves for a run created before its PR) are each reported by name and end the check with exit 1,
# in one-shot and --wait mode alike: a run's recorded base never changes, so no amount of waiting
# turns that read green. RECOVERY: when a nonconforming run (a pre-retarget run) is RETAINED
# on this head, re-applying the merge-train label does add a conforming run on the same head, but the
# retained run still fails the
# every-run rule, so only a refresh push, which moves the head SHA this script pins, earns a
# run set this option trusts; a MISSING run of the required workflow file beside an otherwise
# conforming set needs no new head: a qualifying run on this same head (which re-applying the
# merge-train label schedules) satisfies it. A record of this commit that under this option lacks a usable
# event or pull_requests field entirely is a malformed run record (exit 2, API error), in one-shot and
# --wait mode alike (a record without a usable path is one in every mode; see THE REQUIRED WORKFLOW):
# malformed evidence is a property of the stored record, which polling re-reads unchanged, not a
# retryable API condition.
# RESIDUAL (disclosed, not closed): that the stored array is never rewritten is observed GitHub
# behaviour, not a documented contract; if GitHub recomputed it from the PR's current state, a
# pre-retarget run could read as conforming. Server-side branch protection remains the backstop.
# RESIDUAL (disclosed, not closed): the required-workflow match reads the runs API's .path as the
# BARE repository-relative workflow file path (observed live 2026-10-13 on this repository's runs:
# exactly .github/workflows/quality.yml, with no ref suffix); that format is observed GitHub
# behaviour, not a documented contract. Should GitHub ever return another format (an @ref suffix,
# say), the exact raw-JSON comparison fails for every run, so every check, with or without
# --require-base, exits 1 and the merge lane is blocked: fail closed, never a pass.
# NOT CHECKED (disclosed): only each recorded entry's base.ref is read, never its base.repo or its
# head.sha, so an entry naming the required branch of another repository reads as conforming;
# whether GitHub ever records such an entry on a pull_request run is unverified. AVAILABILITY COST
# (disclosed, unverified): GitHub can leave pull_requests empty for a run created from a fork PR;
# such a run never satisfies the requirement, so under this option a fork PR may always be refused
# (exit 1). CALLER (disclosed): no production caller in this repository passes --require-base (its
# behaviour self-test exercises the option); the merge-lane
# invocation lives in the merge lane's own tooling, outside this tree.
#
# One jq program, two modes. "step" validates ONE raw page of the unfiltered listing, prints "stop" or
# "more", then the page reduced to the fields used here as one compact JSON line. "verdict" receives the
# head_sha query's pages and the reduced scan pages, re-checks every completeness condition, and emits
# the run rows. Status, conclusion, the base-requirement verdict (a fixed token: "-", "met" or
# "unmet") and the required-workflow verdict (a fixed token: "match" or "other", in every mode) come
# FIRST in every TSV row, then the base verdict's display detail, then the run's recorded workflow
# path (display-only; the read is refused before any row is emitted unless every run of the commit
# records its path as a nonempty string, so an empty or non-string path can neither shift later
# columns nor abort the row), then id, name and URL. The step
# mode keeps each run's path in every mode, so a run only the scan sees can still be the required one.
# The required-workflow match is computed INSIDE jq, on the run's raw JSON .path
# against the required path passed in as --arg require_workflow, and only the fixed token reaches
# Bash. An earlier revision compared Bash's @tsv-escaped path column with the unescaped required
# path, so two different paths that escape alike (a recorded real tab versus a requested literal
# backslash-t) read as equal, and identical paths holding a tab, newline, carriage return or
# backslash never matched. jq's @tsv escaping keeps tabs,
# newlines, and backslashes in display fields from becoming record delimiters, so a workflow name cannot
# move the gating fields. Name, URL and the path column remain display-only. The no-run case gets an explicit sentinel
# rather than a rendered "null", because a real run's .status is nullable in the schema and must not be
# mistaken for "no run yet".
#
# TWO SOURCES. The server-side head_sha filter alone is not a complete answer: on 2026-10-06 it returned
# zero runs for commit 0a0c0ca3 while the repository's run listing showed run 37498869615 for that exact
# head_sha as completed/success, an hour after the same filtered query had listed that run as
# in_progress. A failed run dropping out the same way would leave only its green siblings, so the scan
# runs on EVERY query, whatever the filtered query returned, and the two sources are merged by run ID.
# When the scan merged a page shift (see RETRY_SECONDS), the run rows are preceded by a "__SHIFTED__"
# line, which can never equal a run row because every run row holds tab separators.
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
# page reads in a way the rise in total_count does not explain, so some run may have gone unseen; a merged
# shift is never a pass either, as stated there); a
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
  or ((.html_url | length) == 0)
  or (($require_base != "")
      and (((.event | type) != "string")
           or ((.event | length) == 0)
           or ((.pull_requests | type) != "array")
           or any(.pull_requests[]; (type != "object") or ((.base | type) != "object"))));
def malformed_path:
  ((.path | type) != "string") or ((.path | length) == 0);
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
def projection:
  [.head_sha, .status, .conclusion, .name, .html_url, .path]
  + (if $require_base == "" then [] else [.event, [.pull_requests[] | .base.ref]] end);
def base_requirement:
  if $require_base == "" then ["-", "-"]
  elif .event != "pull_request" then
    ["unmet", "its event is " + (.event | tojson) + ", not pull_request"]
  elif (.pull_requests | length) == 0 then
    ["unmet", "no pull request is recorded on the run, so its base cannot be established"]
  elif any(.pull_requests[]; ((.base.ref | type) != "string") or ((.base.ref | length) == 0)) then
    ["unmet", "a pull request recorded on the run names no base branch, so its base cannot be established"]
  elif all(.pull_requests[]; .base.ref == $require_base) then
    ["met", "-"]
  else
    ["unmet", "its recorded base is " + ([.pull_requests[].base.ref] | unique | join(", "))]
  end;
def workflow_requirement:
  if .path == $require_workflow then "match" else "other" end;
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
                        | {id, head_sha, status, conclusion, name, html_url, created_at, path}
                          + (if $require_base == "" then {} else {event, pull_requests} end)]}
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
        elif any($filtered_all[], $scan_all[]; (.head_sha == $requested_sha) and malformed_path) then
          error("malformed workflow run record: run(s) "
                + ([$filtered_all[], $scan_all[]
                    | select((.head_sha == $requested_sha) and malformed_path) | .id]
                   | unique | map(tostring) | join(", "))
                + " of this commit record no workflow path (.path is missing, not a string, or"
                + " empty), so whether each is the required workflow file cannot be evaluated")
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
                  (if ($scan_kept | length) < ($scan_all | length) then "__SHIFTED__" else empty end),
                  ($runs[]
                   | .[0]
                   | [(.status // "-"), (.conclusion // "-")]
                     + base_requirement
                     + [workflow_requirement, .path,
                        (.id | tostring), .name, .html_url]
                   | @tsv)
                end
            end
        end
    end
end'

ci_jq() {
  jq -s -r --arg mode "$1" --arg requested_sha "$SHA" --argjson lower_bound "$LOWER_BOUND" \
    --argjson max_pages "$SCAN_MAX_PAGES" --arg require_base "$REQUIRE_BASE" \
    --arg require_workflow "$REQUIRE_WORKFLOW" "$CI_STATUS_JQ"
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

# THE PULL-REQUEST PROBE (--wait only). quality.yml runs on pull requests into main only, for the
# labeled type alone (after each push to a train pull request the merge lane removes the merge-train
# label and adds it again), so only an open, labelled pull request into main gets a Quality run,
# and GitHub's Actions documentation states that a pull_request workflow does not run while the pull
# request has a merge conflict (not verified live here). So under --wait, before EVERY poll, not only
# the first, the probe lists the repository's open pull requests and keeps those whose head SHA is
# exactly this commit. It refuses (exit 1, naming the cause, before that poll reads any run) when none
# of them targets PR_BASE (the --require-base value, else main; each pull request is named with its
# base) or GitHub reports every one into PR_BASE CONFLICTING, and when none is open at all (closed,
# never opened, or its branch moved to another head), but only on a read whose read of the associated
# pull requests (MERGED below) succeeds and shows no merge into PR_BASE with this head: the first two
# at once on such a read, the third only if it still holds at the last of its reads (RE-READS below).
# A refusal never rests on a failed associated read: a failure in a PERMANENT form below is
# cannot-evaluate (exit 2) at once, and any other is re-read, so an associated read that fails at
# every read ends the probe cannot-evaluate (exit 2) at its last read, whatever the listing and the
# pull requests' own reads showed. WHAT A REFUSAL ESTABLISHES: only the state the probe read, and so
# that no new pull_request run will start for this head while that state holds. A run that started
# earlier, while the pull request was open and mergeable, may still be running and may still finish;
# the refusal is chosen because the head cannot merge into PR_BASE through a pull request as it
# stands, not because no run can come. Every poll is probed because a head that is mergeable when the
# wait begins can turn CONFLICTING mid-wait when another pull request merges. One reported MERGEABLE
# lets the poll proceed. Report-once mode runs no probe: it waits on nothing and reports the runs as
# they are. The probe can only refuse a wait or stop probing, never produce a pass: a green verdict
# still rests on the runs alone.
# MERGED: when no open pull request at this head into PR_BASE reads MERGEABLE (none at this head is
# listed, each one at this head targets another base, or the own reads of those into PR_BASE show
# each CONFLICTING, UNKNOWN, or no longer open at this head into PR_BASE, as a listing read just
# after a merge can still show the merged pull request open), the probe also reads the pull requests
# GitHub associates with this commit (repos/OWNER/REPO/commits/SHA/pulls, paginated). One that is
# closed with a merge time (.merged_at, a timestamp of the form given under PERMANENT below), whose
# head SHA is this commit and whose base is PR_BASE, is read as a merge into PR_BASE with this head
# (auto-merge can merge one as soon as the checks pass, within the settle window), and the head is
# neither refused nor re-read: the probe says what that read showed, stops for the rest
# of the wait (a merge is final), and the runs decide as for any other wait, a green verdict (exit 0)
# only when they settle all successful. A pull request at this head closed without that merge, or
# merged into another base, and a merge that read does not show, keep the refusal or the re-read the
# other reads give. A read that finds a MERGEABLE pull request skips this read. GitHub's REST
# documentation states that for a commit on the default branch this read lists only the merged pull
# request that introduced it, and otherwise the merged and open pull requests associated with it (not
# verified live, nor how soon a merge appears there); a merge that introduced this commit through
# another pull request is not one with this head, so it keeps the refusal.
# RE-READS: UNKNOWN (GitHub has not finished computing mergeability), a pull request that changed
# between the listing and its own read, no open pull request at this head (a listing read just after
# a push can still show the pull request's old head), and a failed read other than the PERMANENT
# forms below are re-read: at most PR_PROBE_READS reads per probe (so at most 11 re-reads per probe),
# PR_PROBE_RETRY_SECONDS apart (at most 55 seconds of sleep per probe). The last read decides: no
# open pull request at this head there is the refusal above (exit 1); anything else still
# unresolved is cannot-evaluate (exit 2), never a wait. A response gh returned that jq cannot parse (not valid
# JSON) is a failed read, re-read, as the runs reads under --wait re-read one.
# PERMANENT (cannot-evaluate, exit 2, at once): a malformed response (valid JSON without the shape
# below), and a read gh reports in one of its own forms for a status no re-read changes. gh api
# writes an HTTP error to stderr as the line "gh: MESSAGE (HTTP STATUS)", MESSAGE being the error
# body's message, or "gh: HTTP STATUS" when the body carries none (gh's api command, cli/cli
# pkg/cmd/api/api.go; not verified live here). Only these whole lines are permanent ("..." standing
# for any text):
#   gh: Not Found (HTTP 404)          gh: HTTP 404
#   gh: Bad credentials (HTTP 401)    gh: Requires authentication (HTTP 401)    gh: HTTP 401
#   gh: Resource not accessible by ... (HTTP 403)
#   gh: Resource protected by organization SAML enforcement... (HTTP 403)
# Text that merely contains one of them, such as a 502 diagnostic naming "Not Found", "HTTP 404" or a
# URL for pulls/4041, is a failed read and re-read, and so is any other 403 (a rate limit).
# The REST pulls API states mergeability as .mergeable: true, false or null, null meaning GitHub has
# not finished computing it, and a read of the one pull request starts that computation (GitHub's
# REST documentation, not verified live here; the self-test stubs gh). The probe names the three
# values MERGEABLE, CONFLICTING and UNKNOWN, as GitHub's GraphQL API does; that false means a merge
# conflict is inferred from that documentation, not verified live. The listing carries no
# .mergeable, so each open pull request into PR_BASE at this head is read on its own on every probe
# read. Every listed, associated and read record must carry a positive integer number, a string
# state, a head SHA string and a nonempty base branch string; each single read a .mergeable of true,
# false or null; and each associated record a .merged_at of null or a merge time as GitHub writes
# one, an ISO 8601 UTC timestamp of the form YYYY-MM-DDTHH:MM:SSZ that reads back unchanged through
# jq's fromdate and todate (so a year of fewer than four digits is refused too). GitHub's REST
# documentation states that it returns every timestamp in UTC in that form (not verified live), so
# another string (no time, fractional seconds, an offset other than Z, even +00:00, or any other
# text) is a malformed associated record, never a merge. Each single
# read's response must be exactly one such object whose number is the number requested, checked on
# its own before the reads are combined: a record of another pull request, two records in one
# response, or an array is a malformed response, as is anything else that does not have this shape.
# The listings are offset paginated like the runs listing: a pull request a page shift skips can only
# make the probe re-read, refuse, or (a merge into PR_BASE it did read) leave the verdict to the runs;
# a poll proceeds only on a pull request GitHub reported MERGEABLE in its own read, or merged into
# PR_BASE in the associated read.
# PERMISSIONS: the runs reads need Actions: Read. On a public repository the pull requests the
# probe reads are public data, expected to be readable with no further permission (not verified
# live here). AVAILABILITY COST (disclosed): a private repository's token also needs Pull requests:
# Read, or the probe's read is refused and every --wait exits 2; the associated read is expected to
# need the same (not verified live), and where it is refused the probe exits 2 instead of refusing
# with exit 1. AVAILABILITY COST (disclosed, accepted): a commit with no open pull request whose CI
# comes from another event (a push to main, such as a merge commit) is refused under --wait although
# its push run would come; report-once still reads it. AVAILABILITY COST (disclosed, accepted): a
# pull_request run that started while the pull request was open and mergeable can still be running,
# and can still finish green, after the pull request closes without a merge into PR_BASE, is
# retargeted or turns CONFLICTING; the probe refuses that head anyway, because it cannot merge as it
# stands, so a wait that run could have satisfied is refused; report-once still reads it.
# AVAILABILITY COST (disclosed, accepted): a head with no open pull request is refused only at the
# last of the probe's reads: up to 55 seconds of sleep after the first, plus the 11 re-reads' own
# time, each re-read making two paginated API reads (the listing and the associated pull requests).
# Every probe read that finds no MERGEABLE pull request makes the associated read, so a CONFLICTING,
# UNKNOWN or changed read also costs that one paginated API read more.
# RESIDUAL (disclosed, not closed): neither the poll sleep nor the probe's re-reads consult
# CI_STATUS_TIMEOUT, so a wait can end up to about 70 seconds past its deadline (the POLL_SECONDS
# sleep of 15 seconds after the last deadline check, then up to 55 seconds of probe re-reads), plus
# the reads' own time.
PR_BASE="${REQUIRE_BASE:-main}"
PR_PROBE_READS=12
PR_PROBE_RETRY_SECONDS=5
# Set once a probe reads a merge into PR_BASE with this head; the probe then stops (see MERGED above).
PR_PROBE_MERGED=0
# "list" reads the slurped pages of the open pull-request listing and prints one TSV row: "none",
# "base" (with each open pull request at this head and its base), or "candidates" (the numbers of
# those into PR_BASE). "associated" reads the slurped pages of the pull requests associated with this
# commit and prints "merged" (with the pull requests closed with a merge time, at this head, into
# PR_BASE) or "unmerged". "record" reads ONE single read's response, refuses it unless it is exactly
# one well-formed record numbered $number, and prints that record as one compact JSON line. "merge"
# reads those checked records and prints "mergeable", "unknown" or "conflicting" (with the pull
# requests named), or "changed" when none is still open at this head into PR_BASE. The first column
# is always one of those fixed tokens; the second is display-only and @tsv-escaped, so a branch name
# cannot add a row or a column.
CI_STATUS_PR_JQ='
def malformed_pull:
  (type != "object")
  or ((.number | type) != "number")
  or (.number <= 0)
  or ((.number | floor) != .number)
  or ((.state | type) != "string")
  or ((.head | type) != "object")
  or ((.head.sha | type) != "string")
  or ((.base | type) != "object")
  or ((.base.ref | type) != "string")
  or ((.base.ref | length) == 0);
def malformed_mergeable:
  (has("mergeable") | not)
  or ((.mergeable != null) and ((.mergeable | type) != "boolean"));
# A merge time as GitHub writes one: the form YYYY-MM-DDTHH:MM:SSZ, and a value that reads back
# unchanged through the jq builtins fromdate and todate (2026-02-29 or an hour of 24 does not). Each
# clause refuses a value the other accepts: the form refuses 1-01-01T00:00:00Z, the way todate writes
# year 1, which reads back unchanged; the read back refuses a date or hour that does not exist.
def merge_time:
  (type == "string")
  and test("\\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\\z")
  and ((try (fromdate | todate) catch null) == .);
def malformed_merged_at:
  (has("merged_at") | not)
  or ((.merged_at != null) and (.merged_at | merge_time | not));
def malformed_listing:
  (length != 1) or ((.[0] | type) != "array") or ((.[0] | length) == 0)
  or any(.[0][]; type != "array");
def open_at_head: (.state == "open") and (.head.sha == $requested_sha);
def numbered: map("#" + (.number | tostring)) | join(", ");
if $mode == "list" then
  if malformed_listing or any(.[0][][]; malformed_pull) then
    error("malformed pull-request listing")
  else
    [.[0][][] | select(open_at_head)] as $open
    | [$open[] | select(.base.ref == $base)] as $on_base
    | if ($open | length) == 0 then ["none", "-"]
      elif ($on_base | length) == 0 then
        ["base", ([$open[] | "#" + (.number | tostring) + " into " + .base.ref] | join(", "))]
      else ["candidates", ([$on_base[] | .number] | unique | map(tostring) | join(" "))]
      end
    | @tsv
  end
elif $mode == "associated" then
  if malformed_listing or any(.[0][][]; malformed_pull or malformed_merged_at) then
    error("malformed associated pull-request listing")
  else
    [.[0][][]
     | select((.state == "closed") and (.merged_at != null)
              and (.head.sha == $requested_sha) and (.base.ref == $base))] as $merged
    | if ($merged | length) == 0 then ["unmerged", "-"]
      else ["merged", ($merged | unique_by(.number) | numbered)]
      end
    | @tsv
  end
elif $mode == "record" then
  if (length != 1) or (.[0] | malformed_pull or malformed_mergeable) or (.[0].number != $number) then
    error("malformed pull-request record")
  else
    .[0] | tojson
  end
elif $mode == "merge" then
  # Unreachable through jq output, so no self-test vector reaches it: merge reads only the records
  # "record" mode already checked and printed, one per listed candidate, and there is at least one.
  # Kept so that this mode refuses on its own whatever feeds it.
  if (length == 0) or any(.[]; malformed_pull or malformed_mergeable) then
    error("malformed pull-request record")
  else
    [.[] | select(open_at_head and (.base.ref == $base))] as $live
    | if ($live | length) == 0 then ["changed", "-"]
      elif any($live[]; .mergeable == true) then
        ["mergeable", ([$live[] | select(.mergeable == true)] | numbered)]
      elif any($live[]; .mergeable == null) then
        ["unknown", ([$live[] | select(.mergeable == null)] | numbered)]
      else ["conflicting", ($live | numbered)]
      end
    | @tsv
  end
else
  error("unknown mode")
end'

# $2, the requested pull-request number, is read only in "record" mode.
pr_jq() {
  jq -s -r --arg mode "$1" --argjson number "${2:-null}" --arg requested_sha "$SHA" \
    --arg base "$PR_BASE" "$CI_STATUS_PR_JQ"
}

# Labels a response gh returned that the jq program refused ($1 the response, $2 the read it
# answers, $3 why a parsable one is refused). Valid JSON (any number of JSON texts, none included)
# is a malformed pull-request response, PERMANENT. Text jq cannot parse is an unparsable one, a
# failed read that is re-read, as the runs reads under --wait re-read a parse error.
pr_refused() {
  if printf '%s\n' "$1" | jq -s 'empty' >/dev/null 2>&1; then
    echo "malformed pull-request response: $2${3:+ $3}"
  else
    echo "unparsable pull-request response: $2 is not valid JSON"
  fi
}

# One probe read: prints one TSV row from CI_STATUS_PR_JQ, or returns 1 with the raw gh or jq
# diagnostic and, for a response the jq program refused, the label pr_refused gives it.
pr_probe_read() {
  local listing listed outcome associated merged number record checked records=""
  {
    listing="$(gh api "repos/$REPO/pulls?state=open&per_page=100" --paginate --slurp)" || return 1
    if ! listed="$(printf '%s\n' "$listing" | pr_jq list)"; then
      pr_refused "$listing" "the open pull-request listing"
      return 1
    fi
    outcome="$listed"
    if [ "${listed%%$'\t'*}" = "candidates" ]; then
      for number in ${listed#*$'\t'}; do
        # jq prints a large number in exponent form (1E+300), which this refuses before it reaches a URL.
        if ! [[ "$number" =~ ^[123456789][0123456789]*$ ]]; then
          echo "unexpected pull-request probe output"
          return 1
        fi
        record="$(gh api "repos/$REPO/pulls/$number")" || return 1
        # Each response is checked on its own, before the reads are combined, so a record of another
        # pull request, or a second record in the same response, can never stand in for this one.
        if ! checked="$(printf '%s\n' "$record" | pr_jq record "$number")"; then
          pr_refused "$record" "the read of pull request #${number}" \
            "is not exactly one well-formed record of #${number}"
          return 1
        fi
        records+="$checked"$'\n'
      done
      if ! outcome="$(printf '%s' "$records" | pr_jq merge)"; then
        echo "malformed pull-request response: the pull-request reads"
        return 1
      fi
    fi
    if [ "${outcome%%$'\t'*}" != "mergeable" ]; then
      # No open pull request at this head into PR_BASE reads MERGEABLE (none at this head is listed,
      # each targets another base, or the own reads show each one into PR_BASE CONFLICTING, UNKNOWN,
      # or no longer open at this head into PR_BASE): was one with this head merged into PR_BASE?
      associated="$(gh api "repos/$REPO/commits/$SHA/pulls?per_page=100" --paginate --slurp)" ||
        return 1
      if ! merged="$(printf '%s\n' "$associated" | pr_jq associated)"; then
        pr_refused "$associated" "the pull requests associated with ${SHA}"
        return 1
      fi
      if [ "${merged%%$'\t'*}" = "merged" ]; then
        printf '%s\n' "$merged"
        return 0
      fi
    fi
    printf '%s\n' "$outcome"
  } 2>&1
}

# Returns 0 when a line of a failed probe read's diagnostic ($1) is PERMANENT (see THE PULL-REQUEST
# PROBE): this script's own malformed-response or unexpected-output label, or one of gh's own lines
# for a status no re-read changes. Each line is matched whole, so a form inside a longer line (a 502
# diagnostic naming Not Found, HTTP 404 or pulls/4041) does not match.
pr_probe_permanent() {
  local diagnostic_line
  while IFS= read -r diagnostic_line; do
    case "$diagnostic_line" in
      "malformed pull-request response: "*|"unexpected pull-request probe output"|\
      "gh: Not Found (HTTP 404)"|"gh: HTTP 404"|\
      "gh: Bad credentials (HTTP 401)"|"gh: Requires authentication (HTTP 401)"|"gh: HTTP 401"|\
      "gh: Resource not accessible by "*" (HTTP 403)"|\
      "gh: Resource protected by organization SAML enforcement"*" (HTTP 403)")
        return 0
        ;;
    esac
  done <<<"$1"
  return 1
}

# Returns only when the head may be waited on; every refusal exits here (see THE PULL-REQUEST PROBE).
pr_probe() {
  local read_no=0 line probe_rc token detail cause refusal
  while :; do
    read_no=$((read_no + 1))
    refusal=""
    line="$(pr_probe_read)"
    probe_rc=$?
    # A successful read prints the one row jq prints in every mode, so an empty line cannot come
    # from jq output and no self-test vector reaches that clause; it is kept as a failed read.
    if [ "$probe_rc" -ne 0 ] || [ -z "$line" ]; then
      if pr_probe_permanent "$line"; then
        echo "ERROR: cannot evaluate: could not read the pull requests of ${REPO} for ${SHA}, so whether this head has an open pull request into ${PR_BASE} that can merge is unknown; refusing to wait."
        echo "  raw: ${line}"
        exit 2
      fi
      cause="the pull-request read failed (raw: ${line})"
    else
      token="${line%%$'\t'*}"
      detail="${line#*$'\t'}"
      case "$token" in
        mergeable) return 0 ;;
        merged)
          PR_PROBE_MERGED=1
          echo "NOTE: the pull requests associated with ${SHA} show pull request ${detail} with this head merged into ${PR_BASE}; the pull-request probe stops for the rest of this wait, and the verdict rests on the runs alone, as they stand (the merge is not a pass)."
          return 0
          ;;
        none)
          cause="no open pull request has ${SHA} as its head, and the pull requests associated with ${SHA} show none with this head merged into ${PR_BASE} (a listing read just after a push can still show the old head)"
          refusal="RESULT: refusing to wait: no open pull request has ${SHA} as its head now, and the pull requests associated with ${SHA} show none with this head merged into ${PR_BASE} (a pull request for it may have closed or moved to another head, or none was opened), still after ${PR_PROBE_READS} reads ${PR_PROBE_RETRY_SECONDS}s apart, so no new pull_request run will start for it while that holds; a run that started earlier may still finish, but this head cannot merge through a pull request as it stands."
          ;;
        base)
          echo "RESULT: refusing to wait: no open pull request for ${SHA} targets ${PR_BASE} now (${detail}), and the pull requests associated with ${SHA} show none with this head merged into ${PR_BASE}, so no new pull_request run into ${PR_BASE} will start for it while that holds; a run that started earlier may still finish, but this head cannot merge into ${PR_BASE} through a pull request as it stands."
          exit 1
          ;;
        conflicting)
          echo "RESULT: refusing to wait: GitHub reports pull request ${detail} CONFLICTING with ${PR_BASE} now, and the pull requests associated with ${SHA} show none with this head merged into ${PR_BASE}, so no new pull_request run will start for it while that holds; a run that started earlier may still finish, but the pull request cannot merge as it stands. Resolve the conflict (a refresh push moves the head SHA)."
          exit 1
          ;;
        unknown) cause="GitHub reports the mergeability of pull request ${detail} as UNKNOWN (not yet computed)" ;;
        changed) cause="the open pull request into ${PR_BASE} for this head changed between the listing and its own read" ;;
        *)
          echo "ERROR: cannot evaluate: unexpected pull-request probe output for ${SHA} in ${REPO}"
          echo "  raw: ${line}"
          exit 2
          ;;
      esac
    fi
    if [ "$read_no" -ge "$PR_PROBE_READS" ]; then
      # The last read decides: no open pull request is the refusal, anything else cannot-evaluate.
      if [ -n "$refusal" ]; then
        echo "$refusal"
        exit 1
      fi
      echo "ERROR: cannot evaluate: ${cause}, still after ${PR_PROBE_READS} reads ${PR_PROBE_RETRY_SECONDS}s apart; refusing to wait on a head whose pull-request state cannot be established."
      exit 2
    fi
    echo "NOTE: ${cause}; re-reading (read ${read_no} of ${PR_PROBE_READS})."
    sleep "$PR_PROBE_RETRY_SECONDS"
  done
}

# THE TREE MATCH (--match-tree). A separate read, for a caller that would skip work a run already did:
# it asks whether a completed successful run of the workflow FILE REQUIRE_WORKFLOW
# (.github/workflows/quality.yml; --require-workflow is refused here) exists on a commit whose TREE
# equals the tree of the queried commit (--head REV, default HEAD), not on the same commit. REV is
# resolved locally to its commit and that commit's tree. Runs are read newest first from the
# unfiltered `actions/runs` listing the scan above reads, one page per request. A run is a candidate
# only when its recorded .path is exactly REQUIRE_WORKFLOW (never its display name, for the reason THE
# REQUIRED WORKFLOW states), its status is completed and its conclusion is success. A completed run of
# that file whose conclusion is anything else (failure, cancelled, timed_out, skipped, none, ...) is a
# veto: as on a commit, where any completed non-success run fails the read, a later pass never erases
# an earlier failure, so the tree of a veto's head commit fails the read even beside a successful run.
# Every page of the window below is read in full before any decision, and every veto on it whose head
# commit's tree was read is tested, since a veto can be older than every candidate.
# A head commit's tree is read from the Git Data API (`git/commits/<head_sha>`, its .tree.sha), not
# from this checkout, because a squashed train head may no longer be fetchable into it; the record must
# name the commit asked for. No commit is read when the window holds no candidate. A veto with the same
# tree refuses the read (exit 1, naming each such run), before any jobs listing is read. Otherwise a
# candidate on another tree is passed over, and on a candidate with the same tree each
# --require-job-success NAME is read from that run's jobs listing (`actions/runs/<id>/jobs`, the latest
# attempt): at least one job is named exactly NAME and every job so named is completed with conclusion
# success, so a run that skipped a job (conclusion skipped), or ran none of that name, never stands as
# that job's evidence. The newest candidate meeting every requirement, listed before every run on a
# further head commit (WINDOW below), passes (exit 0).
# Exit 1: a veto on the same tree within the window, or no candidate meets every requirement within it
# (no run, only runs that are not completed and successful, only runs of another workflow file, only
# runs on another tree, only same-tree runs without the named jobs' success, or only same-tree runs
# listed after a run on a further head commit). Exit 2: a read fails, or a page, a run record, a commit
# record or a jobs listing is malformed (including any record of the listing, not only a candidate's or
# a veto's), at once: a malformed record ends the read, never skipped.
# WINDOW (disclosed, and stated on the success line and the no-match line): the listing is read to
# its end or to MATCH_TREE_MAX_PAGES pages (500 runs), every page read in full; a run past the page
# bound is not seen. The trees of at most MATCH_TREE_MAX_COMMITS distinct head commits are read, those
# of the newest completed runs of the file (candidates and vetoes alike), and a run on a further head
# commit is not compared (it can neither pass nor refuse); the result line counts it. Resolution is per
# run, not a prefix of the listing: past the commit bound the veto scan goes on through every run read,
# so a veto on a commit whose tree is already known refuses wherever it is listed. The candidate scan
# stops at the first run listed on a further head commit, so no run that passes is older than a run
# that was not compared: a candidate listed after that run never stands, even on a commit whose tree is
# known, and the result line says so whenever such a run is listed. An unseen, uncompared or passed-over
# candidate can only refuse, but an unseen or uncompared veto cannot refuse either: a failed run past the
# page bound, one on a head commit past the commit bound (always listed after any run that passes), or
# one a page shift between page reads skips, does not stop a pass. A run a page shift repeats is read
# again (its commit tree once), and a run listed once as a candidate and once as a veto is a veto.
# NOT CHECKED (disclosed): a pull_request run tested the merge of its head into its base, not its head
# alone; the two trees are equal only when the base was an ancestor of the head when the run ran. The
# listing shows each run's latest attempt only, so a failed earlier attempt of a run later re-run to
# success is not seen (the read of a commit above has the same limit).
# PERMISSIONS: the runs and jobs reads need Actions: Read; the commit read is expected to need
# Contents: Read (not verified live, nor the Git Data API's response for a squashed head).
MATCH_TREE_MAX_PAGES=5
MATCH_TREE_MAX_COMMITS=20
# "runs" reads ONE raw page of the listing and prints "stop" or "more", then one TSV row (kind, run id,
# head SHA, conclusion) per run of the required file that is completed: kind "pass" for a candidate,
# "veto" for any other conclusion (the conclusion display-only, "-" for none). "commit" reads one Git Data API commit record and prints its tree. "jobs" reads
# the slurped pages of one run's jobs listing and prints one TSV row: "met", or "unmet" with each
# unmet NAME and the status/conclusion of each job so named (display-only, @tsv-escaped). The NAMES
# arrive as positional arguments ($ARGS.positional), never spliced into the program.
CI_STATUS_TREE_JQ='
def malformed_page:
  (type != "object")
  or ((.total_count | type) != "number")
  or (.total_count < 0)
  or ((.total_count | floor) != .total_count)
  or ((.workflow_runs | type) != "array")
  or ((.workflow_runs | length) > 100);
def object_name:
  (type == "string") and test("^([0-9a-f]{40}|[0-9a-f]{64})$");
def nullable_token($field):
  ($field != null) and ((($field | type) != "string") or (($field | length) == 0));
def malformed_run:
  (type != "object")
  or ((.id | type) != "number")
  or (.id <= 0)
  or ((.id | floor) != .id)
  or ((.head_sha | object_name) | not)
  or ((.path | type) != "string")
  or ((.path | length) == 0)
  or nullable_token(.status)
  or nullable_token(.conclusion);
def malformed_job:
  (type != "object")
  or ((.name | type) != "string")
  or ((.name | length) == 0)
  or ((.status | type) != "string")
  or ((.status | length) == 0)
  or nullable_token(.conclusion);
def malformed_jobs_page:
  (type != "object")
  or ((.total_count | type) != "number")
  or (.total_count < 0)
  or ((.total_count | floor) != .total_count)
  or ((.jobs | type) != "array")
  or ((.jobs | length) > 100)
  or any(.jobs[]; malformed_job);
if $mode == "runs" then
  if (length != 1) or (.[0] | malformed_page) then
    error("malformed workflow-runs response")
  elif any(.[0].workflow_runs[]; malformed_run) then
    error("malformed workflow run record")
  else
    .[0]
    | (if (.workflow_runs | length) < 100 then "stop" else "more" end),
      (.workflow_runs[]
       | select((.path == $require_workflow) and (.status == "completed"))
       | [(if .conclusion == "success" then "pass" else "veto" end), (.id | tostring), .head_sha,
          (.conclusion // "-")]
       | @tsv)
  end
elif $mode == "commit" then
  if (length != 1) or ((.[0] | type) != "object") or (.[0].sha != $commit_sha)
    or ((.[0].tree | type) != "object") or ((.[0].tree.sha | object_name) | not) then
    error("malformed commit record")
  else
    .[0].tree.sha
  end
elif $mode == "jobs" then
  if (length != 1) or ((.[0] | type) != "array") or ((.[0] | length) == 0)
    or any(.[0][]; malformed_jobs_page) then
    error("malformed jobs response")
  else
    [.[0][] | .jobs[]] as $jobs
    | ([.[0][].total_count] | unique) as $totals
    | if (($totals | length) != 1) or (($jobs | length) != $totals[0]) then
        error("malformed jobs response: incomplete jobs listing")
      else
        [$ARGS.positional[] as $name
         | [$jobs[] | select(.name == $name)] as $named
         | if ($named | length) == 0 then $name + ": no job of that name"
           elif all($named[]; (.status == "completed") and (.conclusion == "success")) then empty
           else $name + ": " + ([$named[] | .status + "/" + (.conclusion // "-")] | join(", "))
           end] as $unmet
        | (if ($unmet | length) == 0 then ["met", "-"] else ["unmet", ($unmet | join("; "))] end)
        | @tsv
      end
  end
else
  error("unknown mode")
end'

# $2, the commit asked for, is read only in "commit" mode.
tree_jq() {
  jq -s -r --arg mode "$1" --arg commit_sha "${2:-}" --arg require_workflow "$REQUIRE_WORKFLOW" \
    "$CI_STATUS_TREE_JQ" --args "${REQUIRE_JOBS[@]}"
}

match_tree_error() {
  echo "ERROR: cannot evaluate the tree match of ${SHA} in ${REPO}: $1"
  echo "  raw: $2"
  exit 2
}

# Every outcome exits here (see THE TREE MATCH above).
match_tree() {
  local tree raw step row rest kind run_id head_sha verdict page_no=1 window seen_runs=" "
  local i listing_end=0 candidates=0
  local -a rows=() kinds=() run_ids=() head_shas=() concls=() vetoed=() unmet=()
  local -A tree_of=() unresolved=()
  if ! tree="$(git rev-parse --verify --quiet "${SHA}^{tree}")"; then
    printf 'ERROR: cannot read the tree of %s.\n' "$SHA" >&2
    exit 2
  fi
  # The whole window is read first: a veto may be older than every candidate.
  while :; do
    raw="$(gh api "repos/$REPO/actions/runs?per_page=100&page=$page_no" 2>&1)" ||
      match_tree_error "the workflow-runs listing (page ${page_no}) could not be read" "$raw"
    step="$(printf '%s\n' "$raw" | tree_jq runs 2>&1)" ||
      match_tree_error "the workflow-runs listing (page ${page_no}) was refused" "$step"
    mapfile -t rows <<<"$step"
    for row in "${rows[@]:1}"; do
      kind="${row%%$'\t'*}"
      rest="${row#*$'\t'}"
      run_id="${rest%%$'\t'*}"
      rest="${rest#*$'\t'}"
      head_sha="${rest%%$'\t'*}"
      # jq printed these from validated fields; anything else is refused before it reaches a URL.
      if ! [[ "$kind" =~ ^(pass|veto)$ ]] || ! [[ "$run_id" =~ ^[123456789][0123456789]*$ ]] ||
          ! [[ "$head_sha" =~ ^([0123456789abcdef]{40}|[0123456789abcdef]{64})$ ]] ||
          [ "$rest" = "$head_sha" ]; then
        match_tree_error "unexpected workflow-runs listing output" "$row"
      fi
      kinds+=("$kind")
      run_ids+=("$run_id")
      head_shas+=("$head_sha")
      concls+=("${rest#*$'\t'}")
      [ "$kind" = "pass" ] && candidates=$((candidates + 1))
    done
    case "${rows[0]}" in
      stop)
        listing_end=1
        break
        ;;
      more) ;;
      *) match_tree_error "unexpected workflow-runs listing output" "${rows[0]}" ;;
    esac
    [ "$page_no" -ge "$MATCH_TREE_MAX_PAGES" ] && break
    page_no=$((page_no + 1))
  done
  # With no candidate the read refuses without reading a commit. Otherwise the head trees are read
  # newest first up to the commit bound, and the scan goes on past it: resolution is per run, so a run
  # whose own head commit's tree is already known keeps that tree wherever it is listed, and only a run
  # on a further head commit is left unresolved (counted by run id for the result line).
  if [ "$candidates" -ne 0 ]; then
    for ((i = 0; i < ${#run_ids[@]}; i++)); do
      head_sha="${head_shas[i]}"
      [ -z "${tree_of[$head_sha]+set}" ] || continue
      if [ "${#tree_of[@]}" -ge "$MATCH_TREE_MAX_COMMITS" ]; then
        unresolved[${run_ids[i]}]=1
        continue
      fi
      raw="$(gh api "repos/$REPO/git/commits/$head_sha" 2>&1)" ||
        match_tree_error "the commit ${head_sha} of run ${run_ids[i]} could not be read" "$raw"
      tree_of[$head_sha]="$(printf '%s\n' "$raw" | tree_jq commit "$head_sha" 2>&1)" ||
        match_tree_error "the commit record of ${head_sha} (run ${run_ids[i]}) was refused" \
          "${tree_of[$head_sha]}"
    done
  fi
  if [ "$listing_end" -eq 1 ]; then
    window="listing pages read, each in full: ${page_no} of at most ${MATCH_TREE_MAX_PAGES}, to the listing's end"
  else
    window="listing pages read, each in full: ${page_no} of at most ${MATCH_TREE_MAX_PAGES}, any later page unread"
  fi
  if [ "$candidates" -eq 0 ]; then
    window+="; head commits whose tree was read: none, as no completed successful run of that file is listed"
  else
    window+="; head commits whose tree was read: ${#tree_of[@]} of at most ${MATCH_TREE_MAX_COMMITS}, newest first"
    window+="; completed runs of that file on a further head commit, not compared: ${#unresolved[@]}"
    if [ "${#unresolved[@]}" -ne 0 ]; then
      window+=", and no successful run listed after the first of them is accepted"
    fi
  fi
  # A later pass never erases an earlier failure: a veto anywhere in the pages read whose head commit's
  # tree is known and is this tree refuses the read, and this scan ends before any success counts.
  for ((i = 0; i < ${#run_ids[@]}; i++)); do
    if [ "${kinds[i]}" = "veto" ] && [ "${tree_of[${head_shas[i]}]-}" = "$tree" ]; then
      vetoed+=("run ${run_ids[i]} on ${head_shas[i]}: ${concls[i]}")
    fi
  done
  if [ "${#vetoed[@]}" -ne 0 ]; then
    echo "RESULT: a completed run of the workflow file ${REQUIRE_WORKFLOW} on a commit with the tree ${tree} of ${SHA} did not conclude success, and a success on that tree never erases it:"
    for row in "${vetoed[@]}"; do
      printf '  %s\n' "$row"
    done
    exit 1
  fi
  # The candidates, newest first, each judged by its own head commit. The scan stops at the first run
  # listed on a further head commit, candidate or veto, so no run that passes is older than a run that
  # was not compared.
  for ((i = 0; i < ${#run_ids[@]}; i++)); do
    run_id="${run_ids[i]}"
    head_sha="${head_shas[i]}"
    [ -n "${tree_of[$head_sha]+set}" ] || break
    [ "${kinds[i]}" = "pass" ] || continue
    case "$seen_runs" in
      *" ${run_id} "*) continue ;;
    esac
    seen_runs+="${run_id} "
    [ "${tree_of[$head_sha]-}" = "$tree" ] || continue
    if [ "${#REQUIRE_JOBS[@]}" -ne 0 ]; then
      raw="$(gh api "repos/$REPO/actions/runs/$run_id/jobs?filter=latest&per_page=100" \
        --paginate --slurp 2>&1)" ||
        match_tree_error "the jobs of run ${run_id} could not be read" "$raw"
      verdict="$(printf '%s\n' "$raw" | tree_jq jobs 2>&1)" ||
        match_tree_error "the jobs listing of run ${run_id} was refused" "$verdict"
      case "${verdict%%$'\t'*}" in
        met) ;;
        unmet)
          unmet+=("run ${run_id} on ${head_sha}: ${verdict#*$'\t'}")
          continue
          ;;
        *) match_tree_error "unexpected jobs listing output" "$verdict" ;;
      esac
    fi
    echo "RESULT: completed successful run ${run_id} of the workflow file ${REQUIRE_WORKFLOW} on ${head_sha} has the tree ${tree} of ${SHA}${REQUIRE_JOBS[0]+, every required job concluded success}, and no completed run of that file concluded otherwise on a head commit whose tree was read and is that tree (window: ${window})."
    exit 0
  done
  echo "RESULT: no completed successful run of the workflow file ${REQUIRE_WORKFLOW} on a commit with the tree ${tree} of ${SHA}${REQUIRE_JOBS[0]+ with every required job concluded success} was found (window: ${window})."
  for row in "${unmet[@]}"; do
    printf '  %s\n' "$row"
  done
  exit 1
}

if [ "$MATCH_TREE" -eq 1 ]; then
  match_tree
fi

SETTLE_FINGERPRINT=""
SETTLE_COUNT=0
last_summary="no workflow run registered"
RETRIED=0

while :; do
  # THE PULL-REQUEST PROBE (see above): before every poll, because a head can turn CONFLICTING
  # mid-wait, until a probe reads this head merged into PR_BASE, which is final.
  if [ "$WAIT" = "--wait" ] && [ "$PR_PROBE_MERGED" -eq 0 ]; then
    pr_probe
  fi
  lines="$(query)"
  query_rc=$?
  # A failed query is an API error even if its output resembles a valid sentinel or run row.
  # Report-once fails with exit 2. Under --wait, ride through a TRANSIENT query failure until the
  # deadline, keeping API diagnostics separate from display fields in successful run rows; a
  # malformed run record or response is not transient and exits 2 in both modes (see below).
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
      # Malformed evidence (the jq program's own error()s) is a property of the stored record, not
      # a retryable API condition: polling re-reads the same record, so under --wait it exits 2
      # here too, instead of polling to a timeout that misreports malformed evidence as a
      # not-yet-terminal verdict. The conflicting/shrank/duplicate signatures above stay retryable:
      # each is the signature of the listing changing between requests, which a re-read can settle.
      *"malformed workflow run record"*|*"malformed workflow-runs response"*) exit 2 ;;
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
      echo "RESULT: TIMEOUT; the latest workflow-run read found no workflow run for ${SHA}."
      echo "  Check that a workflow is triggered by this event and branch."
      exit 1
    fi
    sleep "$POLL_SECONDS"
    continue
  fi

  # A read that merged a page shift may still report a failed or not-terminal run, but it is never a pass.
  shifted=0
  if [ "$(printf '%s\n' "$lines" | sed -n 1p)" = "__SHIFTED__" ]; then
    shifted=1
    lines="$(printf '%s\n' "$lines" | sed 1d)"
  fi

  settled=0
  require_workflow_met=0
  run_ids=()
  failure_names=()
  failure_conclusions=()
  nonterminal_names=()
  unknown_statuses=()
  base_unmet=()
  while IFS=$'\t' read -r status concl base_state base_detail wf_state path id name url; do
    report "$status" "$concl" "$name" "$url"
    run_ids+=("$id")
    # The gating tokens come from the jq program's fixed sets ("-", "met", "unmet" for the base
    # requirement; "match", "other" for the required workflow), never from a display field;
    # anything other than "met" (under --require-base) or "match" fails closed, so an unexpected
    # token never passes. base_detail is display-only (@tsv-escaped); path is display-only too:
    # the required-workflow match is wf_state, computed in jq on the run's raw JSON
    # .path (validated as a nonempty string in every mode), never on this escaped column; name stays
    # display-only.
    if [ -n "$REQUIRE_BASE" ] && [ "$base_state" != "met" ]; then
      base_unmet+=("${name}: ${base_detail}")
    fi
    if { [ -z "$REQUIRE_BASE" ] || [ "$base_state" = "met" ]; } && [ "$status" = "completed" ] \
        && [ "$concl" = "success" ] && [ "$wf_state" = "match" ]; then
      require_workflow_met=1
    fi
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

  # A run cancelled by the workflow's concurrency group is a completed non-success run: it fails this
  # head for good, and only a refresh push escapes it.
  if [ "${#failure_names[@]}" -ne 0 ]; then
    echo "RESULT: CI concluded with non-success workflow runs:"
    for ((i = 0; i < ${#failure_names[@]}; i++)); do
      printf '  %s: %s\n' "${failure_names[i]}" "${failure_conclusions[i]}"
    done
    exit 1
  fi

  # THE BASE-BRANCH REQUIREMENT (see above): an unmet run is refused by name with exit 1 in both
  # modes, because a run's recorded base never changes: this RETAINED nonconforming run fails the
  # every-run rule on this head forever, so only a refresh push, under a NEW head SHA, escapes it
  # (re-applying the merge-train label adds runs on the same head but cannot remove this one).
  if [ "${#base_unmet[@]}" -ne 0 ]; then
    echo "RESULT: run(s) do not satisfy --require-base ${REQUIRE_BASE} (a run counts only when its event is pull_request and the pull request recorded on the run names ${REQUIRE_BASE} as its base):"
    for item in "${base_unmet[@]}"; do
      printf '  %s\n' "$item"
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
  elif [ "$shifted" -eq 1 ]; then
    SETTLE_FINGERPRINT=""
    SETTLE_COUNT=0
    last_summary="the unfiltered listing shifted between page reads, so this all-success read is incomplete"
    if [ "$WAIT" != "--wait" ]; then
      if [ "$RETRIED" -eq 0 ]; then
        RETRIED=1
        echo "NOTE: ${last_summary}; re-reading once."
        sleep "$RETRY_SECONDS"
        continue
      fi
      echo "ERROR: ${last_summary} for ${SHA} in ${REPO}"
      exit 2
    fi
    echo "RESULT: ${last_summary}; reading again."
  else
    # THE REQUIRED WORKFLOW (see above), in every mode: a run set without a completed successful
    # run of the required workflow file (a conforming one under --require-base) is missing (exit 1),
    # but the two modes refuse at different times. Report-once refuses on its single snapshot.
    # Under --wait a qualifying run can still be REGISTERED LATE on this same head (re-applying
    # the merge-train label schedules one, and registration also lags
    # the event), so the poll keeps watching and refuses only once the run set has been stable
    # for the whole settle window and still holds no qualifying run. Only a RETAINED
    # nonconforming run (the base_unmet refusal above) needs a NEW head SHA to recover.
    missing_required=0
    if [ "$require_workflow_met" -ne 1 ]; then
      missing_required=1
    fi
    if [ -n "$REQUIRE_BASE" ]; then
      missing_message="RESULT: no completed successful run of the workflow file ${REQUIRE_WORKFLOW} satisfies --require-base ${REQUIRE_BASE}; a conforming run of that workflow file on this head is required for a green verdict (re-applying the merge-train label schedules one on the same head; a refresh push under a new head SHA earns one once the label is applied again)."
    else
      missing_message="RESULT: no completed successful run of the workflow file ${REQUIRE_WORKFLOW} on this head, so its Quality evidence is missing, not passing; a run of that workflow file on this head is required for a green verdict (re-applying the merge-train label schedules one on a pull-request head; a push to main schedules one for its head)."
    fi
    if [ "$WAIT" != "--wait" ]; then
      if [ "$missing_required" -eq 1 ]; then
        echo "$missing_message"
        exit 1
      fi
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
    if [ "$missing_required" -eq 1 ]; then
      last_summary+=" (no completed successful run of the workflow file ${REQUIRE_WORKFLOW} yet)"
    fi
    if [ "$SETTLE_COUNT" -ge "$SETTLE_OBSERVATIONS" ]; then
      if [ "$missing_required" -eq 1 ]; then
        echo "$missing_message"
        exit 1
      fi
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
