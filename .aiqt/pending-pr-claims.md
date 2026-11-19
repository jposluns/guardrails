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

A pull request reference is `#17`, `PR #17`, `PR#17`, `pull request #17`, `pr:17` (with `pr:#17`
accepted as a prose-only alias), `owner/repo#17` or a GitHub pull request URL (optionally ending in
`/files`, `/commits` or `/checks`). A Markdown link or autolink to a pull request URL counts as that URL
(`[#17](https://github.com/OWNER/REPO/pull/17)` binds OWNER/REPO#17); a link to any other URL counts as
its text. A bare number (`PR 17`) is not a reference. Coordinated lists (`#17, #18 and #19`, `#17/#18`)
bind every member. An issue reference (`issue #17`, `issue owner/repo#17`, `issues #17 and
owner/repo#18`, or a link to an `/issues/N` URL) is not a pull request reference: it is never read from
the pulls API and never binds. A link URL is matched with ASCII classes, so a pull request URL whose
fragment holds a non-ASCII letter or digit is not a pull request URL and the link counts as its text.

Units are complete: a Markdown paragraph or list item is joined across wrapped lines, and a TOML string
is decoded before it is read. Inside a unit, a claim binds within one clause; a clause ends at `.`
followed by a space, `;`, `:`, `!`, `?` or a spaced dash.

| Phrase | Result when the bound pull request is merged or closed |
| --- | --- |
Below, "is" stands for `is`, `are`, `remains`, `stays` or `still`, optionally followed by `still`,
`currently` or `now` (`#N is currently pending`).

| Phrase | Result when the bound pull request is merged or closed |
| --- | --- |
| `#N is pending`, `#N (pending)`, `pending PR #N`, `pending #N`, `pending merge of #N` | finding (exit 1) |
| `pending batch (#A, #B)` | finding for each stale member |
| `#N is open`, `#N (open)`, `#N (in progress)`, `open PR #N`, `open pull requests #A and #B` | finding |
| `#N is under review`, `#N is in review`, `#N is awaiting review` | finding |
| `#N is in progress` | finding |
| `In progress (#A, #B)`, `In progress: #N`, `Open (#N)`, `Pending: #N` at the start of a declared Status column cell | finding for each member that is not open |
| `waiting for/on #N to merge`, `awaiting merge of #N`, `until #N merges`, `once/after #N lands` | finding |
| `#N is not yet merged`, `#N is yet to be merged`, `#N has yet to merge`, `#N hasn't merged (yet)` | finding |
| `waiting on/for #N`, `awaiting #N`, `blocked on/by #N` | warning (a dependency may need interpretation) |
| `stacked on/atop #N`, `stacks on #N` | warning (ancestry can stay accurate after a merge) |
| `Open #N ...` at the start of a clause or after a comma (`To review it, open #N`) | warning (an instruction as likely as a claim) |
| `#N is unmerged` | nothing |

`open #N` after an instruction word (`leave open #N`, `keep open #N`) is not a claim. A lead phrase
(`waiting for`, `awaiting`, `until`, `once`, `blocked on`, `stacked on`, `pending`, `open`) directly
after a negation or a past auxiliary (`not waiting for #N to merge`, `no longer waiting on #N`, `isn't
waiting`, `were waiting for #N to merge`, `was stacked on #N`, `had been waiting`, `haven't been
waiting`; adverbs such as `still` are skipped) is not a current claim. Still current claims: present
perfect (`have been waiting for #N to merge`), a negator followed by `only` or `just` (`not only waiting
for #N to merge`), and a negator before `until`, `once` or `after` (`Not until #N merges can we ship`).
A phrase such as `the pending PR` with no named pull request is an ambiguous
binding: cannot-evaluate, even when a named claim binds in the same clause.

A Markdown table is read from its header and delimiter rows, with or without outer pipes.

Not read as current claims: fenced code blocks (a line such as ```` ```x``` ```` is inline code, not a
fence); indented code blocks (four columns of indent outside a paragraph or list); HTML comments within
one unit (`<!-- ... -->`); struck-through text (`~~PR #17 is pending~~`); a blockquote introduced by `Earlier note:` or
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
| `apply_record_draft.py --draft PATH --target REL` | judges the draft bytes read once and writes those same bytes only on exit 0, keeping the target's permission bits; a changed destination preimage, a symlinked parent directory or a parent that changed after it was opened withholds the write; a target that is not a regular file (a FIFO is refused without waiting on it) or cannot be read is exit 2 |
| `check_pending_pr_commit.sh` | runs `--staged --live`, prints the result, and always lets the commit continue; an incomplete evaluation is reported as not passed |

Exit 0: every bound claim was evaluated and none is contradicted (advisory warnings may remain). Exit 1:
one or more findings, evaluation otherwise complete. Exit 2: incomplete evaluation (definite findings are
still printed beside the unknowns) or a usage error. No candidates means only that no supported current
claim was found.

## Residuals

- The grammar is partial: an unfamiliar paraphrase, a pronoun, an undeclared record or a misleading
  history annotation can evade or confuse it. A date alone does not mark text historical. The
  self-test covers only the forms in its rule and fix rows; forms known not to be caught include a
  claim before its reference (`still pending: #17` outside a Status cell), a reference after other
  words in a Status cell (`Blocked, in progress (#17)`), a `pull/N` URL with a suffix other than
  `/files`, `/commits` or `/checks` (`/checks/123`), and a reference split inside a word across a
  line break. A reference in the text of a link to any other URL is read as plain text, so it binds to
  the surface's default repository.
- A blockquote with a historical lead-in is exempt as a whole, so a current claim later added inside
  it is not read. An `Earlier note:` lead-in applies only to the blockquote directly after it (a
  table, fence, indented block or heading between them ends it).
- An issue reference is never read, so a stale claim about an issue (`Issue #17 remains open` after it
  closed) is not detected. A `#N` with no `issue` word is always read as a pull request; when it names
  an issue, the pulls API answers 404 and the run is cannot-evaluate.
- An imperative `Open #N` at the start of a clause warns rather than finds, so `Open PR #17 needs
  review` written as a claim is a warning only. Negation and past tense are judged only from the word
  directly before a lead phrase (adverbs skipped), so a negation or past tense further back is not
  seen: `We were told to wait for #17 to merge` and `not sure whether we are waiting for #17 to merge`
  are still findings.
- An HTML comment that spans a blank line is not removed, so its text is read as record text.
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
