# Stale pending-claim check

`tools/check_pending_pr_claims.py` flags a record sentence (release text, backlog rows, release notes)
that says a named pull request is pending, open, under review, stacked on or waited for, while that
pull request has merged or closed. When the pull request's state cannot be read, the result is
cannot-evaluate (exit 2), never a pass.

## Status of the wiring

- **Scheduled reconciliation is not yet wired.** No workflow runs `--all --live` on a schedule or on
  manual dispatch; a new CI workflow for it is held for the maintainer. Until it is added, live
  reconciliation runs only when someone invokes it.
- The Quality workflow and `tools/run_all_checks.sh` run only the offline modes: the execution-reconciled
  self-test (`tools/check_selftest_execution.py --suite pending-pr-claims-selftest`) and
  `--check-inputs`. Neither reads pull request state, so a green Quality run says nothing about whether
  a record is current.
- `tools/apply_record_draft.py` (guarded draft application) and `tools/check_pending_pr_commit.sh`
  (advisory commit adapter) exist as tools. No private draft applier, pre-commit hook or post-train
  step in this repository calls them yet, so host enforcement is unverified.

## Declared surfaces

`.aiqt/pending-pr-claims.toml` (schema version 1) declares every surface the check reads:

| Path | Format | Role |
| --- | --- | --- |
| `changelog.toml` | `toml-strings`: every decoded string value is a unit; comments are not read | source (`CHANGELOG.md` is generated from it) |
| `TODO.md` | `markdown`, with `Status` as the status column | generated view of the maintainer's private backlog; a correction belongs in that source |

A declared surface that is missing, not a regular file, larger than 4 MiB, not UTF-8 or malformed (TOML
that does not parse, a fenced block never closed) is cannot-evaluate. Undeclared files are not read.
Private backlog and release-note sources need their own authorized declaration and invocation; none is
declared here.

`repository = "origin"` binds a bare `#N` to the repository named by the checkout's origin remote,
accepted only as `https://github.com/OWNER/REPO`, `ssh://git@github.com/OWNER/REPO` or
`git@github.com:OWNER/REPO` (optionally ending in `.git`), the same strict forms `tools/ci-status.sh`
accepts. An optional `expected-repository` must then equal it. An explicit `owner/repo#N` or a
`https://github.com/OWNER/REPO/pull/N` URL selects its own repository and never falls back to the local
one.

## Grammar

A pull request reference is `#17`, `PR #17`, `pull request #17`, `pr:17` (with `pr:#17` accepted as a
prose-only alias), `owner/repo#17` or a GitHub pull request URL. A bare number (`PR 17`) is not a
reference. Coordinated lists (`#17, #18 and #19`) bind every member.

Units are complete: a Markdown paragraph or list item is joined across wrapped lines, and a TOML string
is decoded before it is read. Inside a unit, a claim binds within one clause; a clause ends at `.`
followed by a space, `;`, `:`, `!`, `?` or a spaced dash.

| Phrase | Result when the bound pull request is merged or closed |
| --- | --- |
| `#N is/remains/still pending`, `#N (pending)`, `pending PR #N`, `pending #N`, `pending merge of #N` | finding (exit 1) |
| `pending batch (#A, #B)` | finding for each stale member |
| `#N is/remains/still open`, `open PR #N`, `open pull requests #A and #B` | finding |
| `#N is under review`, `PR #N is in review` | finding |
| `In progress (#A, #B)` in the declared Status column | finding for each member that is not open |
| `waiting for/on #N to merge`, `awaiting merge of #N`, `until #N merges`, `once/after #N lands` | finding |
| `#N is not yet merged` | finding |
| `waiting on/for #N`, `awaiting #N`, `blocked on/by #N` | warning (a dependency may need interpretation) |
| `stacked on/atop #N`, `stacks on #N` | warning (ancestry can stay accurate after a merge) |
| `#N is unmerged` | nothing |

`open #N` after an instruction word (`leave open #N`, `keep open #N`) is not a claim. A phrase such as
`the pending PR` with no named pull request is an ambiguous binding: cannot-evaluate.

Not read as current claims: fenced code blocks; a blockquote introduced by `Earlier note:` or
`Previously recorded:` (in the paragraph before it or its own first words); a quoted span (curly or
straight quotes, or backticks) directly after a historical lead-in such as `Previously recorded:` or
`The old note read`; past tense (`#17 was pending`); negation (`#17 is no longer pending`). Only the
quoted span is exempt: `Previously blocked; #17 remains pending` is still a current claim. Quotation
marks and backticks elsewhere do not hide a claim (`PR` followed by a backticked `#17` and `is pending`
still binds). A claim inside any other blockquote is cannot-evaluate until it is given a historical
lead-in or moved out of the quote.

## State source

`tools/_pr_state.py` runs, as an argument list with no shell,
`gh api --hostname github.com -H "Accept: application/vnd.github+json" repos/OWNER/REPO/pulls/N`, with
`gh` found on the child environment's `PATH` and `GH_HOST` and `GH_REPO` dropped from that environment. One JSON object is accepted only when its
`number` is exactly the requested integer, `base.repo.full_name` and `html_url` name the requested
repository and number, `state` is `open` or `closed`, `merged_at` is present and null or a
`YYYY-MM-DDTHH:MM:SSZ` timestamp, and any `merged` flag agrees with `merged_at`.

| Response | Classification |
| --- | --- |
| open, null merge time | OPEN |
| closed, merge time | MERGED |
| closed, null merge time | CLOSED |
| anything missing, contradictory (open with a merge time), mismatched or malformed | cannot evaluate |

A missing `gh`, a launch failure, a nonzero exit (401, 403 and 404 included; a 404 is not evidence of
closure), a signal, a timeout and an exhausted bound are cannot-evaluate. State is never inferred from
commit subjects, changelog references, open-list omission, CI results or mergeability. Reads are
deduplicated by repository and number within one run and never cached across runs. Bounds (declared in
`[limits]`): 100 unique pull requests, 10 seconds per call, 120 seconds overall, one retry of a timeout
or an HTTP 5xx error inside the deadline. Diagnostics name the record location, the matched clause, the
qualified pull request, its classification and the retrieval time; they never echo `gh`'s error stream
or the origin URL.

## Modes and exit codes

| Command | What it does |
| --- | --- |
| `check_pending_pr_claims.py --check-inputs` | offline: validates the declaration and every declared surface and counts candidates; does not assess currency |
| `check_pending_pr_claims.py --all --live` | every declared surface, live state |
| `check_pending_pr_claims.py --draft PATH --target REL` | the complete proposed content PATH judged as declared surface REL, live state |
| `check_pending_pr_claims.py --staged --live` | the staged blob of every declared surface the index changes, live state |
| `apply_record_draft.py --draft PATH --target REL` | judges the draft bytes read once and writes those same bytes only on exit 0; a changed destination preimage withholds the write |
| `check_pending_pr_commit.sh` | runs `--staged --live`, prints the result, and always lets the commit continue; an incomplete evaluation is reported as not passed |

Exit 0: every bound claim was evaluated and none is contradicted (advisory warnings may remain). Exit 1:
one or more findings, evaluation otherwise complete. Exit 2: incomplete evaluation (definite findings are
still printed beside the unknowns) or a usage error. No candidates means only that no supported current
claim was found.

## Residuals

- The grammar is partial: an unfamiliar paraphrase, a pronoun, an undeclared record or a misleading
  history annotation can evade or confuse it. A date alone does not mark text historical.
- A named backlog item is not resolved to a pull request: only explicit references bind.
- State can change after it is read (a closed pull request can be reopened); the API read and the file
  write are not one transaction.
- `gh`'s handling of `--hostname` and its own configuration is `gh`'s behaviour and is not verified
  here. No live API read was made by the self-test: every lifecycle response in it is synthetic.
- The self-test drives the staged mode through an injected git reader, so its real `git diff --cached`
  and `git cat-file` calls are not exercised there.
- Hooks can be absent or bypassed, and with no scheduled reconciliation a stale claim stays until a
  live run is made. A closed pull request does not prove deployment, acceptance or completion of the
  related backlog item; the check flags wording only.
