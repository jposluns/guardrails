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

A declared surface that is missing, not a regular file, larger than 64 KiB (65,536 bytes; see Running
time), not UTF-8 or malformed (TOML that does not parse or nests deeper than its bound, see Running
time, a fenced block or HTML comment block never closed) is cannot-evaluate; a draft or staged blob over that size is too. Each file (a declared surface or
a draft) is opened once without following a symlink and without waiting on a FIFO, and its type is
checked on that open descriptor. In `--staged`, a declared surface whose index entry is not a regular
file (mode `100644` or `100755`; a symlink `120000` or a gitlink is refused), is unmerged or is absent
is cannot-evaluate. Undeclared files are not read.
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
accepted as a prose-only alias), `owner/repo#17` or a GitHub pull request URL (`https://` or `http://`,
optionally ending in `/files`, `/commits` or `/checks`, and optionally in `/`). These link forms to a
pull request URL count as that URL (`[#17](https://github.com/OWNER/REPO/pull/17)` binds
OWNER/REPO#17): an inline Markdown link, a reference link (`[#17][pr17]`, `[#17][]` or `[#17]`) whose
definition (`[pr17]: URL`) is a prose line of the same Markdown document (a definition inside a fenced
block, indented code or an HTML comment is never collected, so it cannot shadow the real one), an autolink (`<URL>`), and an HTML anchor
(`<a href="URL">#17</a>`) inside one unit. A link to any other URL counts as its text, and brackets
that form no link are dropped (`[#17] is pending` with no definition binds `#17`). A link reference
definition is not read as record text. A bare number (`PR 17`) is not a reference. Coordinated lists
(`#17, #18 and #19`, `#17/#18`, `#17 & #18`) bind every member. An issue reference (`issue #17`, `issue
owner/repo#17`, `issues #17 and owner/repo#18`, or a link to an `/issues/N` URL) is not a pull request
reference: it is never read from the pulls API and never binds. Only the plural `issues` takes a list,
so in `Closes issue #12, and #17 is still open` the `#17` is a pull request reference. A link URL is
matched with ASCII classes, so a pull request URL whose fragment holds a non-ASCII letter or digit is
not a pull request URL and the link counts as its text.

Units are complete: a Markdown paragraph or list item is joined across wrapped lines, and a TOML string
is decoded before it is read. Inside a unit, a claim binds within one clause; a clause ends at `.`
followed by a space, `;`, `:`, `!`, `?` or a spaced dash.

Below, "is" stands for `is`, `are`, `remains`, `stays`, `has been`, `have been`, `still` or
`currently`, each optionally followed by up to two of `still`, `currently`, `now`, `also` and `both`
(`#N is currently pending`, `#N has been open`, `#A and #B are both still open`, `#N still currently
open`).

| Phrase | Result when the bound pull request is merged or closed |
| --- | --- |
| `#N is pending`, `#N (pending)`, `pending PR #N`, `pending #N`, `pending merge of #N` | finding (exit 1) |
| `pending batch (#A, #B)` | finding for each stale member |
| `#N is open`, `#N (open)`, `#N (in progress)`, `open PR #N`, `open pull requests #A and #B` | finding |
| `#N is under review`, `#N is in review`, `#N is awaiting review` | finding |
| `#N is in progress` | finding |
| `In progress (#A, #B)`, `In progress: #N`, `In progress #N`, `Open (#N)`, `Pending: #N` at the start of a declared Status column cell | finding for each member that is not open |
| `waiting for/on #N to merge`, `awaiting merge of #N`, `until #N merges`, `once/after #N lands` | finding |
| `#N is not yet merged`, `#N is yet to be merged`, `#N has yet to merge`, `#N hasn't merged (yet)` | finding |
| `waiting on/for #N`, `awaiting #N`, `blocked on/by #N` | warning (a dependency may need interpretation) |
| `stacked on/atop #N`, `stacks on #N` | warning (ancestry can stay accurate after a merge) |
| `Open #N ...` at the start of a clause or after a comma (`To review it, open #N`) | warning (an instruction as likely as a claim) |
| `#N is unmerged` | nothing |

`open #N` after an instruction word (`leave open #N`, `keep open #N`) is not a claim.

**Cues downgrade; they never exempt.** A claim from the table above whose phrase or reference follows
a cue word is a warning, not a finding: when its pull request has merged or closed, the run prints a
`WARNING:` line naming the record location, the sentence and the qualified pull request, and exits 0.
It is never a silent pass, and an unreadable state is still cannot-evaluate. The cue words are a
negation (`not`, `never`, `no`, `no longer`, `n't`, `cannot`, `neither`, `nor`, `none`), a past or
past-perfect auxiliary (`was`, `were`, `had`, `did`, `'d`), a condition or question (`if`, `whether`,
`unless`, a clause opening with `Was #N`), and `previously`, `formerly`, `once`, `merged`, `closed` and
`landed`. They are read back from the phrase or reference over adverbs (`still`, `only`, `just`, ...),
auxiliaries (`is`, `have`, `been`, ...), contraction stems (`haven`, `isn`, ...) and the words `the`,
`all`, `any` and `of`, and the first other word ends the look-back. So `not waiting for #N to merge`,
`We have not only been waiting for #N to merge`, `We'd been waiting` (straight or curly apostrophe),
`We were not only waiting`, `Not until #N merges`, `Neither #M nor #N is still open`, `If #N is still
open` and `The previously pending PR #N` all warn. Only a claim with no cue is a finding: `We have
been waiting for #N to merge`, `It's been waiting`, `Do not merge until #N merges` (the look-back stops
at `merge`) and `We merged the fix and #N remains open` (it stops at `and`). A phrase such as `the
pending PR` with no named pull request is an ambiguous binding: cannot-evaluate, even when a named
claim binds in the same clause. After a cue (`There are no pending PRs`, `Merged all pending pull
requests`), the same phrase is a warning that names the sentence and says that no pull request was
read.

A Markdown table is read from its header and delimiter rows, with or without outer pipes. A header
cell wrapped in emphasis or a code span (`**Status**`) names that column, and a leading byte order mark
is dropped. Each header cell is also read as prose (`| PR #17 is pending | Status |` is a claim); it is
never a Status cell, and it keeps naming its column.

Markdown block context is decided once, line by line, before any line's text is read; every reader
(record units, link definitions, Status cells and the references they resolve) reads only the lines
it marks as prose. A fence opens or closes only at most three columns past its container's content
column (the left margin, or the innermost list item's content column, taken as the column the item's
text starts at); a backtick or tilde line four or more columns past it is indented code outside a
paragraph and paragraph text inside one. A line indented less than a list item's content column (a
lazy paragraph continuation aside) ends that item, and any fenced block or HTML comment block opened
inside it. A fence or an HTML comment block may open right after a list marker (``- ``` `` or
`1. <!--`, or after a run of markers such as ``- 1. ``` ``): it opens inside that item, at the item's
content column, so its closing line closes it and the item text after it is read. Indentation, list
marker spacing and blank lines count spaces and tabs only: any other whitespace (a no-break space, an
en space, a form feed) is text, so it never indents a line, never makes a line blank and never puts
a fence or a list marker after it. The text after the `-->` that closes an HTML comment (a block or an
inline comment across lines) is paragraph text as it stands: it is not indented afresh, so spaces or a
tab after `-->` never make it indented code, a fence or a list item. It is still scanned for a comment
it opens, as any prose line is: in `--> <!-- second` the lines up to the next `-->` are an inline
comment.

Not read as current claims: fenced code blocks, in a list item as outside one (a line such as
```` ```x``` ```` is inline code, not a fence); indented code blocks (four columns of indent past the
container's content column, outside a paragraph); HTML comments: one within a line (`<!-- ... -->`),
an HTML comment block opened by a line starting `<!--` and running to the line holding `-->` (across
blank lines; text after `-->` on that line is read), and an inline comment opened later in a line that
ends on a later line of the same paragraph (an unclosed comment block is cannot-evaluate; an inline
`<!--` with no `-->` before the paragraph ends is text); struck-through text (`~~PR #17 is pending~~`, or GFM
`~PR #17 is pending~` with flanking tildes, so `~5 min ... ~10 min` is not struck); a blockquote
introduced by `Earlier note:` or `Previously recorded:` (in the paragraph before it or its own first
words); a quoted span (curly or straight quotes, or a code span) directly after a historical lead-in such
as `Previously recorded:` or `The old note read`, where an apostrophe inside a word (`isn't`, `isn’t`)
does not end the span, and a code span is bounded as the code-span reading below bounds it (an opening
run of any number of backticks closed by the next run of the same length, shorter runs inside it
included; a run with no such closer quotes nothing, so the text after it is read); and wording that
matches no phrase in the table (`#17 was pending`, `#17 is no longer pending`). Only the quoted span is exempt: `Previously blocked; #17 remains pending` is still a
current claim. Quotation
marks and backticks elsewhere do not hide a claim (`PR` followed by a backticked `#17` and `is pending`
still binds). A claim inside any other blockquote is cannot-evaluate until it is given a historical
lead-in or moved out of the quote.

Delimiters inside code spans are inert. Each text is given one code-span reading before any
exemption is recognized (CommonMark backtick-run matching: a run of backticks opens a span that the
next run of exactly the same length closes; a run with no such closer is literal backticks). Every
exemption and link recognizer (HTML comments, strikethrough, historical lead-ins and their quoted
spans, issue references, inline, reference and HTML links) then runs only on the text outside code
spans, so a comment opener in one code span and its closer in another, tildes inside code spans, an
`issue` word or a lead-in inside a code span, or link brackets inside code spans exempt nothing, and
the claim between them is read. A code span directly after a historical lead-in is still that
lead-in's quoted span, and a real comment or strikethrough holding a code span is still exempt. The
text inside a code span stays available to claim matching as before (a backticked
`PR #17 is pending` is a claim), and the unevaluated-reference scan uses the same reading. One
consequence: a number written in a code span after `issue` (`issue` then a backticked `#12`) is
not an issue reference; it is read as a pull request. Angle brackets around a GitHub URL (an
autolink) are a spelling, not an exemption: they are dropped, keeping the URL, inside code spans as
outside them, and only after the formatting marks are gone, so a whole autolink in one code span, or
its `<` and `>` each in a code span of their own around the bare URL, still binds the reference to the
predicate after it (`is pending`), while link exemptions stay off inside code spans.

**Unevaluated references warn; they never pass silently.** Coverage is a set difference, so it does
not depend on knowing what dropped a reference. The check takes the pull request references of the
whole document, keyed by repository (or the surface default) and number, and removes those that reach
a record unit the claim matcher evaluates (after the unit's normalization). This is by design and
does not change: the warning is keyed by pull request, not by occurrence, so one evaluated mention of
a pull request suppresses the warning for every other dropped occurrence of that pull request in the
same file (a `See #17.` line read as prose silences a `PR #17 is pending` that a comment, a fence or a
reader gap dropped elsewhere in that file). Every reference left prints
a `WARNING:` line naming the file, the line and the reference, and saying it was not evaluated. When
the block context hid the line (a line of a fenced block, indented code, an HTML comment block or an
inline comment across lines, the part of a prose line a comment cuts away, or a link reference
definition), the warning also names that block kind. A hidden line is scanned whole, with the same
spellings, link forms and issue exclusions as record text; strikethrough, historical spans and
single-line comments inside it do not hide a reference. Any other line is scanned as record text is
read, so a reference dropped on the way to a unit (by a reader gap, a comment the joining erased, a
header the table reader skipped) still warns. In a `toml-strings` surface each line of each decoded
string is scanned the same way; comments are not read or scanned. The pull request state is not read,
so the warning does not depend on that state, and an unreadable state does not make it
cannot-evaluate. With no finding the run exits 0. `--check-inputs` prints the same warnings and counts
them. Only the documented exemptions are left out of the scan: a fenced block whose info string starts
with one of the example markers `example` or `history` (case-insensitive, for instance
```` ```example ````), a blockquote with a historical lead-in, and, inside a line read in full,
struck-through text, historical spans and single-line comments. Any other info string (`python`, `text`, `md`) is scanned.
Wording in an unevaluated line is not matched against the phrase table; the warning asks for a review,
it is not a finding.

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
| `check_pending_pr_claims.py --check-inputs` | offline: validates the declaration and every declared surface, counts candidates and prints and counts unevaluated-reference warnings; does not assess currency |
| `check_pending_pr_claims.py --all --live` | every declared surface, live state |
| `check_pending_pr_claims.py --draft PATH --target REL` | the complete proposed content PATH judged as declared surface REL, live state |
| `check_pending_pr_claims.py --staged --live` | the staged blob of every declared surface the index changes, read by the object id of its stage-0 index entry, live state; a staged entry that is not a regular file (a symlink, for example) or is unmerged is exit 2 |
| `apply_record_draft.py --draft PATH --target REL` | judges the draft bytes read once and writes those same bytes only on exit 0, keeping the target's permission bits; a changed destination preimage, a symlinked parent directory or a parent that changed after it was opened withholds the write; a target that is not a regular file (a FIFO is refused without waiting on it) or cannot be read is exit 2 |
| `check_pending_pr_commit.sh` | runs `--staged --live`, prints the result, and always lets the commit continue; an incomplete evaluation is reported as not passed |

Exit 0: every bound claim was evaluated and none is contradicted (advisory warnings may remain). Exit 1:
one or more findings, evaluation otherwise complete. Exit 2: incomplete evaluation (definite findings are
still printed beside the unknowns) or a usage error. No candidates means only that no supported current
claim was found.

## Running time

Running time is bounded in two layers. Neither covers reading pull request state, which has its own
bounds (State source).

1. Each step that reads a document is meant to run in time linear in its size. The self-test shows
   this only for the shapes its growth rows time: each row times the whole check at two sizes eight
   times apart and passes when the time grows at most sixteen times, and its red leg, with that shape's
   quadratic form put back, must grow more. The rows cover seventeen shapes, among them complete HTML
   anchors on one line and one long reference link definition used on one line, on many lines and in
   many paragraphs (a document's definitions are worked out once, in one table its units and scanned
   lines share). A shape no row times is not shown to be linear. Where a definition URL is still copied
   into the text (a URL holding a parenthesis, a square bracket or a backtick is copied into each use, and
   a URL the markdown-link step does not read whole, as in a reference link whose label ends in a closing
   parenthesis written after a link text in brackets, an opening parenthesis and a space, which that step
   reads as an inline link ending at that parenthesis, is given back in full), the copied
   text is read and quoted as written, so n uses of a URL of length L cost n * L. Those copies are
   bounded: a document whose reference links would copy more than eight characters of URL per byte of
   the document (its UTF-8 bytes, not its characters), plus 65,536, is cannot-evaluate (exit 2). Two growth rows reach that bound on each copy
   path, and their red legs (the bound removed) grow more.
2. Hard bounds on input, collection and report. A declared surface, draft or staged blob larger than 64 KiB is
   cannot-evaluate (exit 2), a defence in depth for a superlinear path no row has found. The bound was
   set from the real records the check reads: the largest declared surface at any commit of this
   repository was 24,834 bytes (`TODO.md`; `changelog.toml` at most 2,728 bytes), and 64 KiB is 2.6
   times that. The self-test fails when a declared surface grows past half the bound, so that the bound
   is measured again first. `apply_record_draft.py` reads the existing destination (for its preimage
   hash only) up to its own bound of 4 MiB, so an older record over 64 KiB can still be replaced by a
   draft within 64 KiB. Each finding, warning and cannot-evaluate line quotes its whole clause or line,
   so n references bound in one clause of length L (n claims, one claim naming n joined references, a
   Status cell included, or n uses of one reference link) would give n * L characters. The lines built
   for one document are bounded instead: past 4 Mi characters (`REPORT_MAX_CHARS`; 16 MiB at four
   bytes a character) the document is cannot-evaluate (exit 2), with a line naming the bound, and its
   later lines are not built. Self-test rows reach that bound through the draft entry point (a
   reference link to a long pull URL used 3,000 times in one claim, 15,145 bytes) and on hidden
   references, and the red leg (the bound removed) builds past it. On the worst report shapes then
   known at 64 KiB a whole run peaked at about 32 MiB resident on the host it was measured on
   (2026-11-20); before the report bound the same shapes needed more than 2 GiB. The report bound runs only after
   the candidates are collected, so collection is bounded too, by an allowance of 4 Mi characters
   (`COLLECT_MAX_CHARS`) per document. Past it the document is cannot-evaluate (exit 2), naming the
   bound, before the entry that would pass it is kept. The allowance charges these fields and no
   others: a record unit (a Markdown paragraph, list item, table cell or blockquote, or a TOML string
   value) 32 characters plus its location, not its text; a claim entry 32 plus its quoted clause and
   its phrase; an ambiguous-phrase entry 32 plus its quoted clause; an unevaluated-reference entry 32
   plus its location and its quoted line. A field the entry charged just before also held (the same
   object) is not counted again: every entry found on one line or clause shares one copy of its quoted
   text and location, built once, and counts it once. A string built anew for each unit is counted each
   time: a long header cell over many table rows (each cell's location names its header) or a long TOML
   key path over many string values reaches the bound, and such a document is now cannot-evaluate where
   it was evaluated before. The allowance counts characters in those fields; it does not measure or
   bound the memory of the process. It leaves out the input bytes and decoded text, the parsed TOML
   document, the units' texts, the reader's and tokenizer's temporaries and Python's object overhead
   (the 32 per entry is a nominal charge, not a measured size), and one character can take four bytes.
   No bound is set on the memory of the whole process; the peaks below were observed, not bounded. Self-test rows show one shared
   copy for 1,000 references on a line with leading white space and non-BMP text (the red leg, a copy
   per reference, holds 1,000), the bound reached when the copies are put back and on both long-location
   shapes (the red legs, unbounded, collect them), and, in a child process limited to 512 MiB of address
   space, the 52,907-byte draft of 9,000 such references on one fenced line ending with the report
   bound named. Measured on 2026-11-20 on the host it was run on, through the draft entry point with the
   report written to memory: that draft peaked at about 52 MiB resident (before the shared quotes, a
   1 GiB limit ended it with an uncaught MemoryError at 1,036,340 KiB resident), the same line in a TOML
   string at about 49 MiB (before, the same MemoryError), a long non-BMP TOML key path over 4,000
   hidden references at about 48 MiB (before, about 258 MiB), and a long non-BMP TOML key path over
   3,600 string values and a long non-BMP header cell over 8,900 rows, both now refused, at about 30
   and 31 MiB (before, evaluated, about 125 and 255 MiB). A path not yet found can need more.

Nesting is bounded as well, so any document within 64 KiB ends in exit 0, 1 or 2, never a traceback.
No walk over a document recurses: Markdown blocks, blockquotes and list items are read in loops over
the lines, and the walk over a parsed TOML document keeps its own stack and builds a value's location
only at that value. A TOML table or array nested more than 100 tables and arrays deep
(`TOML_MAX_DEPTH`; the top-level table is not counted) is cannot-evaluate (exit 2), naming the bound.
Before, a table header 5,000 tables deep (10,007 bytes) parsed and then ended the walk in an uncaught
RecursionError (exit 1).

The bound is enforced before the TOML reader runs. One linear scan of the document's text
(`_toml_nesting`) counts the depth the reader will build, following TOML 1.0's tables and arrays of
tables: a table header's parts, each one level, except that a part naming an array of tables is two
(the array and its last element), where an earlier array-of-tables header named that array and an
array-of-tables header's own last part always names one (header keys are read as the reader reads them,
so a quoted, escaped or spaced spelling of a name is the same name, and a new element forgets what the
earlier one held); a dotted key's parts before its last below its table, inside an inline table too; and
one level per inline array or table; strings (basic, literal and multi-line, with their escapes) and
comments are stepped over. It refuses at the first part past the bound, so the reader is never given a
key of more than 101 parts, a table or inline nesting more than 100 deep. Before, the scan counted only
the array of tables a header itself named, so 51 array-of-tables headers, each one part longer than the
one before (2,878 bytes), passed it and were parsed 102 deep before the walk refused them; now they are
refused before the reader. The reader builds the table or array of tables a header's key names before it
looks for the closing bracket or brackets, so a header whose closer is missing or partial is counted and
bounded as a closed one. Before, the scan read such a header by its syntax alone, losing the levels of
earlier arrays of tables and of the header's own array, so an array-of-tables header with one closing
bracket under an earlier array of tables (207 bytes) passed it and the reader built it 101 deep before
refusing it; now it is refused before the reader. For a document the reader refuses, the scan's depth is
never lower than the depth the reader builds before refusing it. The reader copies a dotted key's prefixes, quadratic in its
parts: before the scan, a dotted key of 4,000, 8,000 and 12,000 parts (8,003 to 24,003 bytes) peaked at
about 90, 278 and 592 MiB resident and 16,000 parts at about 1 GiB, and at 21,000 parts a 1 GiB address
space limit ended the read in a MemoryError; with the scan each ends naming the bound at about 28 MiB in
under 0.2 seconds, as do table headers of 8,000 and 21,000 parts, inline arrays up to 30,000 deep,
inline tables up to 5,000 deep and a dotted key of 21,000 parts with no value (measured on 2026-11-20 on the host it was run on,
through `--draft`). Documents within the bound, filled to 64 KiB with keys of 101 parts, keys under a
header 100 deep, array-of-tables headers 99 deep or inline arrays and tables 100 deep, peaked at about 28
to 33 MiB and 0.6 seconds, as before the scan. For a document the reader reads, the scan's depth is
the parsed document's, no lower and no higher, so it refuses no document the walk after the reader
would read and passes none the walk would refuse; the self-test compares the two over a generated
corpus, and over a generated corpus of malformed documents it compares the scan's depth with the depth
the reader builds before refusing each, measured by instrumenting the reader. The walk's own refusal and the `RecursionError` and `MemoryError` handling at the reader stay as
a defence in depth.

Self-test rows give deep table headers, array-of-tables headers, dotted keys, inline arrays and inline
tables (exit 2, naming the bound, in process and through `--draft`); show that the reported dotted key,
one with no value, and headers, dotted keys and inline nesting just past the bound are refused before
the reader is called (the red leg, the scan off, calls it); read a value exactly 100 deep with its whole
key path, a dotted key of 101 parts, a header and a dotted key that together reach the bound and an
array-of-tables element at the bound; read a document whose strings and comments are full of brackets,
braces, dots and quotes exactly as with the scan off; refuse before the reader the 2,878-byte
array-of-tables chain and a table header under an array of tables that together pass the bound, in
process and through `--draft` (the red leg, the scan counting only a header's own array of tables, calls
the reader), and read the chain one header shorter, at the bound; show that the instrumented reader
builds the 207-byte unclosed array-of-tables header 101 deep, and refuse it before the reader, in
process and through `--draft`, with the same header with no closer, at the end of the text and with its
closer split by a space, and a table header with no closer under the array (the red leg, the scan
reading an unclosed header by its syntax alone, calls the reader for each array-of-tables header); hand
the unclosed header one level shorter, at the bound, to the reader; show, over every sequence of up to
three table and array-of-tables headers of up to three parts from two names spelled in each of the
reader's ways, and over 4,000 seeded documents mixing headers, dotted keys, inline tables and nested
inline arrays, that the scan's depth is the parsed document's (the red leg finds a document it counts
too shallow); show, over every sequence of one or two of those headers whose last header is unclosed or
(an array-of-tables header) half closed and over the seeded documents each cut short or with one
character dropped, inserted or replaced, that the scan's depth is never lower than the depth the
instrumented reader builds before refusing a document (the red leg finds one it counts too shallow),
and that the instrumented depth is the parsed document's for each document the reader reads; end the reported 16,000-part dotted key through `--draft` in a child limited to
512 MiB of address space with the bound named; refuse one 101 deep; and read blockquotes and list items
nested in documents of up to about 60,000 bytes as one level is read (those rows pass on the walk this
replaced too: Markdown had no recursive walk). The Markdown reader has no shape bound beyond the size
bound: it reads lines in loops, and blockquotes, list markers and lists in blockquotes nested to 60,000
bytes ran in about 28 MiB and at most 0.6 seconds, before and after this change.

In-band marks. The check writes a few characters into a text as its own marks and reads them back by
their spelling: NUL (a reference link's URL placeholder), U+E000 and U+E001 (a pull request reference's
token), U+E002 (an escaped table pipe), U+E003 (an issue number's `#`) and U+E004 (code-span content as
an exemption recognizer sees it), together `IN_BAND_MARKS`. A document that carries any of them, raw or
decoded from a TOML escape in a string value or a key, is cannot-evaluate (exit 2), naming the
character, before any of it is read. Before, an eight-byte Markdown draft spelling a reference token,
or its 18-byte TOML escape, ended in an uncaught `IndexError` (exit 1), a token index of 5,000 digits in
an uncaught `ValueError`, and the other marks changed what was read. Other private-use characters are
text. Self-test rows refuse each mark raw in Markdown and as a TOML escape in a value and in a key
(through `--draft` too for the reference token and an oversized index), read a claim beside another
private-use character as without it, and fail when the checker's code spells a NUL or private-use
character `IN_BAND_MARKS` does not hold.

## Residuals

- A prose grammar cannot be complete, and this one is partial: an unfamiliar paraphrase, a pronoun,
  an undeclared record or a misleading history annotation can evade or confuse it. Fixing one wording
  can expose the next, so a pass is never proof that no stale claim exists. A date alone does not
  mark text historical. The self-test covers only the forms in its rule, fix and cue rows. Forms
  known not to be caught:
  - a claim before its reference (`still pending: #17` outside a Status cell);
  - words between a reference and its phrase (`PR #17, which fixes the parser, is still open`, `PR
    #17 (the parser fix) is still pending`, `Depends on #17, which is still open`);
  - wordings outside the table (`Release 1.2 (#17 pending)`, `PR #17 is awaiting merge`, `PR #17 is
    not merged yet`, `PR #17 awaits review`, `PR #17 is ready to merge`);
  - a reference after other words in a Status cell (`Blocked, in progress (#17)`);
  - a `pull/N` URL with a suffix other than `/files`, `/commits` or `/checks` (`/checks/123`);
  - a reference split inside a word across a line break.
  A reference in the text of a link to any other URL is read as plain text, so it binds to the
  surface's default repository. A reference link whose definition is in another file, any reference
  link in a TOML string, and an HTML anchor split across units are read as their text.
- Cues are read only from the words directly before a claim (adverbs, auxiliaries, contraction stems
  and `the`, `all`, `any` and `of` aside). A cue further back is not seen, so the claim stays a
  finding: `We were told to wait for #17 to merge` and `not sure whether we are waiting for #17 to
  merge` are findings. A cue word can also turn a current claim into a warning only, for example `We'd
  wait for #17 to merge` (`'d` read as `had`, though here it is `would`). A question is recognised
  only by a cue word (`Was #17 still open?`), not by its question mark, so `Is #17 still open?` is a
  finding.
- A blockquote with a historical lead-in is exempt as a whole, so a current claim later added inside
  it is not read. An `Earlier note:` lead-in applies only to the blockquote directly after it (a
  table, fence, indented block or heading between them ends it).
- An issue reference is never read, so a stale claim about an issue (`Issue #17 remains open` after it
  closed) is not detected. A `#N` with no `issue` word is always read as a pull request; when it names
  an issue, the pulls API answers 404 and the run is cannot-evaluate.
- An imperative `Open #N` at the start of a clause warns rather than finds, so `Open PR #17 needs
  review` written as a claim is a warning only.
- The block model is a subset of CommonMark. A line indented four or more columns past its container
  outside a paragraph is indented code and is not read, including after a list item it is not inside
  (one whose content column it does not reach): text laid out that way is not read, as CommonMark
  renders it as code. A line with deeper indentation after a list item's marker (`1.      text`) keeps
  the column its text starts at, not CommonMark's marker column plus one, so text wrapped under it is
  read. Because only spaces and tabs indent, a list marker after a form feed or a no-break space (a
  page break or spacing left by PDF extraction) is paragraph text, and lines indented under it four or
  more columns are indented code: not read as claims, though their references are scanned and warn.
  Blockquote containers are not modelled: a fence marker after `>` is quote text. An
  inline comment ends at the first later line holding `-->` before a blank line or a line that starts
  a fence, heading, blockquote or list item, so a `-->` inside a code span there ends it early. A
  table header is recognised from the raw text of the line after it, even when that line is itself
  indented code. A paragraph that an inline comment interrupts across lines is joined around it; a
  comment block between two lines ends the paragraph. The code-span reading is taken first and wins
  over every other inline construct: CommonMark lets whichever starts first win, so text such as
  `<!--` followed by a backtick, `-->` and a closing backtick is read here as an unclosed comment
  opener and a code span, not as a comment. Backslash escapes before backticks are not read. Code
  spans are read three ways: per record unit (the joined text the matcher evaluates); per paragraph
  when block classification looks for an inline comment opener on a paragraph or list item line (the
  text the paragraph's earlier lines keep, then that line and its later lines raw up to a blank line or
  a line that starts a fence, heading, blockquote, list item or HTML comment block), so a code span
  that wraps a `<!--` across lines keeps it inert and the claim inside it is read; and per scanned
  line, so a code span that wraps across lines is literal backticks in the line scan. A heading, a
  table row or a blockquote line is read for a comment opener on its own, so a code span wrapped across
  blockquote lines is not followed there, and the `-->` that ends an inline comment is still found on
  the raw later line (above). Block classification decides before any unit is read, and the paragraph
  reading counts later lines raw where the unit joins only the text left after comments are cut, so
  backticks inside a hidden comment can pair differently in the two readings: where block
  classification and the unit reading still differ, block classification can remove a claim before
  the unit is read. The removed part is scanned and warns, except where one evaluated mention of the
  same pull request in the file suppresses the warning (the keyed design below), and there a stale
  claim passes silently. Otherwise the unit reading decides what is evaluated and the line scan only
  adds warnings.
- The unevaluated-reference warning is keyed by reference, not by claim: one evaluated mention of a
  pull request suppresses the warning for its other dropped occurrences in the same file, so a dropped
  line naming a pull request that an evaluated unit of the same file also names raises no warning
  (`PR #17 merged.` above a plain fence holding `PR #17 is pending` is quiet; so is `See #17.` above a
  comment that hides `PR #17 is pending`). This is the documented design, not a defect; a claim-level
  guarantee would need evaluated occurrences tracked, not keys. A fence marked `example` or `history` is not
  scanned at all. A bare `#17` and a qualified `owner/repo#17` are different keys, so one dropped and
  the other read still warns. Lines are scanned one at a time, so a strikethrough, historical span,
  link or HTML anchor split across the lines of one paragraph is read as its text on each line and can
  warn although the joined unit exempts or resolves it. Comments in a `toml-strings` surface are not
  read or scanned. Struck-through text, historical spans and single-line comments inside a line read in
  full are grammar exemptions and are not scanned.
- The running-time bounds refuse rather than read: a record over 64 KiB, or one whose reference links
  would copy definition URLs past the copy bound (Running time), is cannot-evaluate (exit 2), and none
  of its claims is evaluated. A record whose report would pass the report bound is cannot-evaluate too,
  and its findings and warnings past the bound are not reported. The memory stated for the bounds is
  one measurement on one host.
- A named backlog item is not resolved to a pull request: only explicit references bind.
- State can change after it is read (a closed pull request can be reopened); the API read and the file
  write are not one transaction.
- `gh`'s handling of `--hostname` and its own configuration is `gh`'s behaviour and is not verified
  here. No live API read was made by the self-test: every lifecycle response in it is synthetic.
- The self-test drives the staged mode through an injected git reader, so its real `git diff --cached`,
  `git ls-files --stage` and `git cat-file` calls are not exercised there.
- Hooks can be absent or bypassed, and with no scheduled reconciliation a stale claim stays until a
  live run is made. A closed pull request does not prove deployment, acceptance or completion of the
  related backlog item; the check flags wording only.
