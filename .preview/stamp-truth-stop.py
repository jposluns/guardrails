#!/usr/bin/env python3
"""Stop hook: block a turn whose timestamp claims or "Session elapsed" footer disagree with the clock.

THREAT MODEL. An ACCIDENTAL-DRIFT discipline guard: it catches the model composing times instead of reading
the clock and presenting them in its NORMAL status format. It is NOT an adversarial boundary: an evasion that
needs deliberately unusual presentation (an unusual zone, a code span, a quote line, a schedule word placed
before the time) is a disclosed residual, not a defect. It fails OPEN on malformed input.

Motivated by an assistant composing console stamps and "Session elapsed HH:MM" footers from its own sense of
time instead of reading the clock, drifting up to 4h40m ahead (timestamp-from-clock;
claims-rest-on-observation). Companion of clock-inject.py, which feeds the real clock into context; this hook
is the check.

TIMESTAMP CLAIM. Anywhere in assistant prose, bracketed or not:
    YYYY-MM-DD[T or space]HH:MM(:SS(.f{1,9})?)? ZONE
ZONE is required: Z (attached or after one space); a numeric offset +HH, +HHMM, or +HH:MM, or with -,
attached or after one space; or, after one space, UTC, GMT, or a letter abbreviation of 2 to 6 capitals.
Z, UTC, and GMT are UTC; an offset is applied as written; a letter abbreviation counts only when it is the
process local zone's abbreviation at that wall time (both DST readings are tried; if both match, the earlier
instant is taken). A time with no zone, or with an abbreviation that is not the local zone, is not a claim
in prose (it is ignored). The zone grammar and the scheduling keyword list are duplicated verbatim in
future-stamp-write.py (python3 -I forbids a sibling import); a self-test asserts they are identical.

EXEMPT TEXT. Lines inside a matched fenced code block (``` or ~~~, closed by a fence of the same character
at least as long; an UNCLOSED fence is not code, so its lines are checked), blockquote lines (starting `>`
after optional whitespace), and inline code spans (a backtick run closed by the next run of the same length
on the same line; an unclosed run opens nothing). For the AHEAD rule only, a NON-leading claim is also exempt
when a scheduling keyword IMMEDIATELY precedes it: the keyword ends at most SCHED_GAP_TOKENS (3) word tokens
before the claim (a word token holds a letter or digit, so `:`, `[`, or `**` do not count), as in "next run
at T", "due T", "deadline: T", "scheduled for T", "until T", "not before T", "by T", "valid through T".
Keywords (case-insensitive, whole words; see below): due, deadline, expir, until, next, scheduled, not before,
not-before, eta, planned, "target date", "target:", `by ` with its trailing space, and (round 24, the
validity words) through and valid. Each matches as a WHOLE word, optionally plural (round 24: "validated",
"throughput", "duet", or "etag" exempt nothing), except the stem `expir`, which also covers expires, expiry,
and expiration. A keyword never exempts a line-leading status stamp, a keyword AFTER a
claim does not exempt it, and a keyword further back does not.

RULES, against the message's reference time REF:
  1. AHEAD: every timestamp claim outside exempt text must be no more than 1 minute after REF.
  2. BEHIND: a STATUS STAMP is a claim that is the first token of its line (after optional whitespace, `*` or
     `_` emphasis, `[`, and one list bullet `-`, `+`, `*`, or `N.`/`N)` followed by whitespace), outside code
     and blockquotes. The message's HEADER stamp, its FIRST status stamp, CLAIMS the current time only when it
     is in the bracketed console form (a `[` immediately before the stamp, as in `[2026-09-23 13:45:02 EDT]`);
     such a header must be no more than 10 minutes before REF (30 minutes for the final message, which can be
     long in generation). An unbracketed first status stamp is a historical date, never checked for BEHIND
     (round 24: "2020-01-01T00:00Z service launched" is truthful history). Later status stamps
     (a bulleted history list under the header) are not checked for BEHIND. Every status stamp is checked for
     AHEAD whatever keywords its line carries. Quoted history mid-line is never checked for BEHIND. A claim
     that cannot be converted (an invalid date such as 2020-02-30, or a letter abbreviation that is not the local
     zone at that wall time) cannot be evaluated and FAILS OPEN, status stamp or not (round 26).
  3. ELAPSED FOOTER: the LAST elapsed footer in the message that lies outside fenced code, blockquote lines,
     and inline code spans is checked, wherever it sits (a completion marker, a `---`, or a quote line after
     it does not displace it). Two forms are footers, and the last occurrence of EITHER form wins: "Session
     elapsed HH:MM", case-insensitive, with up to eight spaces, tabs, `*`, `_`, `:`, `-`, or en or em dash
     characters between the words and before the number; and the parenthetical "(session: Xh Ym)", also
     "(session: Xh)" or "(session: Ym)", case-insensitive, 1 to 4 digits per number, optional spaces or tabs
     after the colon, between the parts, and before the `)`, and nothing else inside the parentheses. Its
     tolerance is ASYMMETRIC like the stamps' (fabrication runs ahead): it must be no more than 2 minutes
     AHEAD of (REF - lease start), and no more than 10 minutes BEHIND it (30 minutes for the final message,
     the same long-generation allowance as its status stamp); with no known lease start it is not checked.
     Earlier occurrences (a corrected value earlier on the same line, a worker footer quoted above) are
     ignored.
Every check is per claim in its own try, in integer microseconds (no datetime bound can overflow); a failure
on one claim or entry is skipped and never discards violations already found. Each line is scanned once
(the status prefix and keyword positions are computed once per line; a literal is converted once per
message), so the check is linear in the message.

REF. For a transcript entry, its own recorded `timestamp` (UTC; a naive value is read as UTC); an entry
without a parseable timestamp is skipped, never compared against now. For the payload's
last_assistant_message: the timestamp of the last collected transcript assistant entry when its text matches
(that entry is then not checked twice), else now.

WHICH MESSAGES. The transcript is read backwards; assistant entries (not isMeta, not isSidechain) are
collected until the most recent genuine user message or the RECOVERY BOUNDARY, whichever is later, plus the
last_assistant_message. Recovery boundary: a private per-session state file records OFFSET, the byte offset
just past the last COMPLETE transcript record evaluated (a trailing record with no newline yet that does not
parse is unfinished, and OFFSET never passes it, so it is evaluated once complete), with a SHA-256 of up to
256 bytes before OFFSET. The next Stop (with stop_hook_active or not) evaluates only records starting at or
after OFFSET. The file is written when this hook blocks, and on a pass once a state file exists or was
unavailable. LATE-ARRIVAL EXCEPTION: at a block, when the blocked final message is NOT yet in the transcript,
its SHA-256 is recorded as pending; a later record with that text is skipped ONCE (the pending entry is consumed) because it arrived
after OFFSET only through transcript lag. Pending entries are dropped at any new genuine user message and
when the transcript is reset (shorter than OFFSET, or the bytes before OFFSET changed), which also discards
OFFSET. A blocked final message already in the transcript creates no exception. The boundary is never
inferred from message text. State dir: $XDG_RUNTIME_DIR/clock-truth when XDG_RUNTIME_DIR is absolute, else
/dev/shm/clock-truth-<euid>, created 0700 and used only when it is a real directory owned by this user with
no group or other permission bits; round 30: each transcript has its own SUBDIRECTORY there, named by the
SHA-256 of transcript_path plus `.d`, created 0700 and opened no-follow relative to the state dir, held to the
same private-directory test, and holding the one state file `state`, opened no-follow and
written via an exclusive temp file and an atomic rename. ABSENT state (a usable dir with no file for this
transcript) means the whole turn is evaluated. UNAVAILABLE state (the dir or the transcript's subdirectory
cannot be created or is not private; the file cannot be opened, is not a regular own-uid file, cannot be
read, or is malformed) and
UNPERSISTABLE state (the boundary could not be written at a block) mean the boundary is unknown: only the
FINAL message (last_assistant_message, else the last collected transcript assistant entry) is checked, never
older messages, because re-checking already-reported messages would re-block every Stop and wedge the actor.
A pass rewrites an unavailable state so it recovers. State I/O never crashes.
STOP_HOOK_ACTIVE (the corrective loop after a block): violations only in messages older than the final one
never block; a bad FINAL message still blocks, and then older violations are reported with it. So a corrected
final message always passes.
BLOCK CAP (defence in depth against a loop the model cannot satisfy, for example a hook defect): when
stop_hook_active is true AND this Stop would block, the consecutive block cycles of this hook immediately
preceding it are the LARGER of two counts (round 24). (i) The hook's own recovery-state counter: the state
file (see WHICH MESSAGES) also records `blocks`, set to 1 by a block outside the corrective loop, incremented
by each further block under stop_hook_active, kept at a capped allow AND at a pass under stop_hook_active
(round 27), set to the cap at a fail-open allow and at a pass under stop_hook_active whose counter was
unreadable (so the rest of that corrective loop stays capped), and reset to 0 ONLY by a Stop without
stop_hook_active. While stop_hook_active is true the counter NEVER decreases, whatever the transcript shows
(round 26) and whatever the final message is (round 27): a transcript reset or rewrite, or a user-role entry
read as a genuine user message, resets the boundary and the late-arrival exception but never the counter,
and a clean pass inside the loop keeps it (in round 26 a clean pass reset it, so alternating with ANOTHER Stop
hook that rejected the clean message, 1, 0, 1, 0, let this hook block every other Stop without limit). So
within one continuous stop_hook_active run this hook blocks at most BLOCK_CAP times, for any interleaving of
other hooks' feedback, transcript contents, and clean or violating messages (the argument is at the counter
code in _evaluate()). Round 28: that bound assumes the Stops for one transcript are SERIALIZED (two concurrent
evaluations, one loading the counter and saving it back after the other raised it, re-armed it every time),
so the whole load, evaluate, and save runs under an EXCLUSIVE, NON-BLOCKING lock. Round 29: the lock is an
fcntl.flock on the private state DIRECTORY itself, opened once per evaluation (no-follow, O_DIRECTORY) and
required to be an own-uid directory with no group or other permission bits, and EVERY state-file read and
write of that evaluation is made relative to that same directory descriptor, so the lock and the data are
bound to one directory inode that no single-file unlink can replace (round 28 locked a separate `.lock` file,
and deleting only that file mid-evaluation let a second evaluation lock a new inode and interleave, re-arming
the counter; there is no `.lock` file now). A directory removed or renamed away and recreated mid-evaluation
takes the first evaluation's save with it (into the orphaned directory, or nowhere), while the next Stop finds
the new directory's state ABSENT, which under stop_hook_active fails open. A Stop that finds the lock held by
another evaluation FAILS OPEN: it is allowed with its claims unchecked and a one-line warning, and it reads
and writes no state, so the counter is unchanged. A Stop that cannot lock for another reason (the directory
cannot be opened or is not private, no fcntl, or a failed flock) writes NO state, as for an unpersistable
state: only its final message can block, and under stop_hook_active nothing blocks (each violation fails open
with a warning), so the counter is unchanged. Round 30: the locked directory is the transcript's OWN state
subdirectory (see WHICH MESSAGES), not the whole state dir, so only Stops of the SAME transcript contend
(round 29's per-user directory lock was held for a whole evaluation, which scans up to 64 MiB of transcript,
so every other session's Stop that overlapped it passed unchecked); the directory-inode binding above holds
for the subdirectory unchanged. PRUNING: a Stop that creates its transcript's subdirectory then, after
releasing its own lock, removes stale subdirectories so their number stays bounded: it lists the names in the
state dir (one directory read, no per-entry call), sorts the subdirectory-shaped ones, and examines at most
4096 of them in that sorted order, starting just after a persisted CURSOR name and wrapping around (round 31:
round 30 examined the first 4096 entries in directory order on every prune, so an eligible subdirectory listed
beyond them was never examined; the cursor makes successive prunes advance through the whole set), and removes
at most 32 subdirectories, each only when its name has the subdirectory
shape, it opens no-follow as a private own-uid directory, a NON-BLOCKING exclusive flock on it succeeds (so
one an evaluation holds is never touched), and, checked with that lock held, it has not been written for 7
days; its entries are unlinked relative to its descriptor and it is removed only while its name still refers
to the locked inode. The cursor is the file `prune-cursor` in the state dir (opened no-follow relative to the
state dir's descriptor, a regular own-uid file holding one subdirectory name; anything else reads as no
cursor, so the scan starts at the first name; round 32: it is WRITTEN only through an exclusively created
regular temporary file renamed over it, never by opening the existing entry, so a FIFO or other special file
planted there cannot block the Stop): it records the last name examined when a prune stops short of
the whole sorted set (the 4096-name window or the 32-removal batch), and it is removed once a prune has
examined every name. So successive prunes advance through the whole sorted set: a prune examines its whole
window unless 32 removals end it first, and the next prune resumes where it stopped, so every eligible
subdirectory is eventually examined and removed (a prune that removes fewer than 32 examines 4096 names, or
every name when there are fewer); the subdirectories that remain are those of transcripts evaluated in the last 7 days, those an evaluation held
locked when they were examined, and eligible ones the cursor has not reached yet, each reached by a later
prune (a prune runs only when a Stop creates a new transcript's subdirectory, so a backlog drains only as new
transcripts arrive). Two concurrent prunes can each write the cursor, the later write winning; a name either
skips is examined when the cursor next wraps round to it.
Under stop_hook_active each save also re-reads the stored counter and never
writes a lower value, and a block whose write would not raise the stored counter fails open. It is written
even when the transcript is unreadable (the boundary is then kept as loaded). (ii) The transcript count: walking back at
most 8 MiB and 512 records from the transcript tail, each record that is this hook's own block feedback is a
cycle. ONLY a user-role entry whose first
text starts with the host's "Stop hook feedback" prefix AND carries this hook's block-reason prefix is such a
marker; a `system` record, a tool result, or any other shape is never one, whatever text it quotes. Duplicate
markers, adjacent or separated only by metadata (isMeta or isSidechain entries, tool results, other record
types), count once: only a non-isMeta, non-isSidechain assistant entry separates two cycles. Assistant
entries, tool results, isMeta and isSidechain entries, and other record types are passed over, and any other
user entry (a genuine user message, a notification) ends the run. Cycles must also be TIGHT: more than 10
assistant entries (each text, thinking, or tool-use entry counts, an isMeta one included, the conservative
direction) between the transcript end and the newest marker, or between two markers, ends the run there, so
a long stretch of ordinary work after old blocks resets the count while a short corrective continuation (a
few tool calls) does not. The transcript count is ESTABLISHED only when its walk ends at a genuine user
entry, at the start of the file, or at the cap; an unreadable transcript, a counting gap, or the record or
byte bound leaves it unestablished. At 3 or more the Stop is ALLOWED instead, with a warning that says the cap
was hit and names the unresolved claims. The own counter is the REQUIRED brake: whenever the counter is
unusable under stop_hook_active (no usable private state dir, no transcript_path to key it, a state file that
cannot be read, or a block that cannot be written to it), the hook could not establish whether it is looping,
so the Stop FAILS OPEN at once with a one-line warning naming the first unresolved claim, WHATEVER the
transcript count says (the transcript count alone is never the only brake). An ABSENT state file in a usable
private dir is an established count of 0 only OUTSIDE the corrective loop. Under stop_hook_active it is an
UNKNOWN count (round 26): this hook writes the file at every block it makes and never deletes it while it is
in use (round 30: it prunes only a subdirectory unwritten for 7 days, see PRUNING), so a file
missing mid-loop was removed by something else (a tmp cleaner deleting it between Stops, or its directory
removed or replaced after the evaluation opened it) or pruned after 7 idle days,
and the Stop FAILS OPEN at once as for an unusable counter,
never restarting the count at 0; that closes the round-25 residual in which such a deletion, or a transcript
reset or a synthetic user-role entry resetting the counter, let every corrective Stop block. Each warning is delivered to the user as a top-level {"systemMessage": ...} on stdout (the
hooks reference's common JSON output field); the same text also goes to stderr, which on exit 0 reaches only
the host's debug log, so stderr is diagnostic logging, not a user-visible channel.

Elapsed resolution (same as clock-inject.py). Lease file = env AIQT_LEASE_FILE when set to a non-empty value
(a legacy spelling is accepted as a fallback, see _cfg); otherwise there is NO lease, elapsed is unknown, and
the footer is not checked. No lease is derived from the project directory or the cwd. Start (the lease code
is shared verbatim with clock-inject.py; a self-test
in each asserts the copies are identical): a lease FIELD line is `Name: value`, `**Name:** value`, or either
after a `-` or `*` list bullet, with optional surrounding whitespace. ONLY the FIRST Active-session field is
read. (a) A value <label>-YYYYMMDDTHHMMSSZ, where <label> is 1 to 32 characters of [A-Za-z0-9] (for example
`sess-` or `S88-`), gives that time (an impossible time is unknown, no fallback). (b) `none` (any case) or an
empty value is an INACTIVE lease: unknown, and neither Session-start-UTC nor the transcript is consulted. (c)
Any other value is an active id carrying no time (for example `sess-2026-09-23-opus55-r1`): the start is the
FIRST Session-start-UTC field (`YYYY-MM-DDTHH:MM:SSZ` or `YYYYMMDDTHHMMSSZ`) of the lease's header section,
the file up to the first markdown heading after the Active-session field. (d) Else the EARLIEST parseable
top-level `timestamp` among the complete records in the first 256 KiB of the payload's transcript_path (read
non-blocking, regular files only). (e) Else unknown. No Active-session field is unknown with no fallback. See
clock-inject.py for the full statement.

File reads. The lease and transcript are opened non-blocking and must be regular files (a FIFO or device is
treated as unreadable). The lease read is capped at 1 MiB. The transcript is read backwards in 64 KiB chunks,
at most 64 MiB, each record at most 4 MiB (a longer record is skipped unparsed); every scan is linear.

Contract: pass = no stdout, exit 0; block = top-level {"decision": "block", "reason": ...} on stdout, exit 0
(the Claude Code hooks reference, Stop decision control, specifies top-level decision and reason for Stop);
capped or fail-open allow (see BLOCK CAP) = top-level {"systemMessage": ...} on stdout (the user-visible
warning), the same text on stderr as diagnostic logging only (on exit 0 the host sends stderr to its debug
log), exit 0. Fail-OPEN silently on unparseable hook input or any internal error outside the per-claim
isolation, including an argument, stdin, or JSON error before the payload is evaluated: a DISCIPLINE guard,
not a security boundary. The payload is read as BYTES and parsed by json.loads, so its decoding does not
depend on the process locale. An error writing the output (a closed or full stdout) also fails open (round
24): it is swallowed and the hook exits 0; if the stream cannot even be pointed at /dev/null, the hook ends
at once with os._exit(0), so no exit-time flush can fail it. Skipped entirely: a subordinate worker process,
detected as env AIQT_HOOKS_WORKER=1 (legacy spellings are also accepted, see _is_worker), and a payload
carrying agent_id (a subagent's stop). The worker skip writes one warning line to stderr (round 24; never to
stdout, so worker output is not distorted, and on exit 0 stderr reaches only the host's debug log, so the
skip is logged, not shown).

RESIDUAL COVERAGE. MISSES: a time without an explicit zone, a
12-hour or natural-language time, a compact sess- id, an epoch number, and a relative claim ("started 3
hours ago") are not checked; an unzoned local time is not a claim at all, so it is never compared as local
wall time or as UTC, and a fabricated unzoned local time passes (write the zone). A fabricated future time placed mid-line within three word tokens after a
scheduling keyword passes, including everyday prose such as "the next phase at T" or "fixed by Jeff at T"
(the line-leading status stamp is always checked); the validity words are prefixes like the others, so
only a whole word (or its plural) exempts: "validated" or "throughput" does not; "expir" alone is a stem. A fabricated stamp or footer inside a code fence, a `>`
quote line, or an inline code span passes. An unquoted worker footer pasted AFTER the real footer is the one
checked (quote worker output in a fence or a `>` line). A stale footer that lags true elapsed by up to the
BEHIND window (10 minutes, 30 for the final message) passes. FALSE POSITIVES: prose that happens to put the
words "session elapsed" before an HH:MM outside code, for example "the total session elapsed: 14:30 across
all workers", is read as the footer when it is the last such occurrence and blocks on a mismatch; remedy:
put it in a `code span` or a `>` line. Likewise any "(session: 30m)"-shaped parenthetical in prose (a
timeout, say) is read as the footer when it is the last footer-shaped occurrence; same remedy. A footer
written in any other shape (`(session 2h 5m)`, `(2h 5m session)`, `(session: 2h 5m, 1 compaction)`) is not
recognized and not checked. AHEAD applies to EVERY zoned claim
(that breadth is the drift catch), so a genuine FUTURE time mentioned mid-line without an immediately
preceding keyword blocks, for example "the eclipse occurs 2027-08-02T18:00Z" ("valid through 2027-..." passes
since round 24); remedy: precede it with a keyword ("expires 2027-...", "until 2027-...") or put it in a
`code span`. A correction that repeats the rejected future value in plain prose re-blocks; quote it in a
`code span` or a `>` line, or omit it. A genuinely scheduled time written as the FIRST token of a line blocks
(put a label such as "Next run:" before it). Only a BRACKETED first line-leading stamp claims the current
time: an unbracketed stale header ("2026-09-23 10:00 EDT starting phase 3") is read as history and is not
checked for BEHIND (a MISS; AHEAD still applies), while a BRACKETED historical stamp that is the message's
FIRST line-leading stamp (a bracketed history list with no header stamp above it) is read as the header and
blocks when older than the BEHIND tolerance (start the message with a current stamp, drop the brackets, or
move the time mid-line or into a blockquote); a stale header stamp placed below an earlier line-leading
history stamp is not checked for BEHIND. Under
stop_hook_active a fabricated stamp in an intermediate message of the corrective continuation (not the
final message) passes, as does any older message when the recovery state is unavailable or cannot be
written. Inline code-span detection is
per line and approximate (an unclosed backtick run hides later spans on that line, so their claims are
checked, failing toward checking). A letter abbreviation is trusted as local when it matches the process
zone's abbreviation; a foreign zone sharing that abbreviation is not distinguished, and a claim with a foreign
abbreviation or an invalid date is not checked anywhere, a line-leading status stamp included (round 26: it
fails open, so a fabricated `[2026-09-24 09:00 PDT]` header in an Eastern-zone process is a MISS). Genuine-user detection is heuristic on entry shape and a fixed list of
notification prefixes. The recovery boundary lives in volatile per-user storage: it is lost on reboot (the
state is then absent and the whole current turn is evaluated once more). Two OVERLAPPING Stops for one
transcript (round 28): the one that finds the lock held is allowed UNCHECKED with a warning (the fail-open
direction), so a violation in it passes. The window is one whole evaluation, which grows with the transcript
scanned: milliseconds on a short turn, but about 1.2 s on a 49 MiB turn and 1.6 s on a 63 MiB one, near the
64 MiB scan cap (measured round 30 on the development host). Round 30: the lock is the transcript's own state
subdirectory, so only overlapping Stops of the SAME transcript contend (round 29's lock was the whole per-user
state directory, so a Stop of any other session of the same user overlapping that window was also allowed
unchecked). Per-transcript subdirectories are pruned only after 7 days unwritten (see PRUNING), so the state
of a transcript idle that long is gone: its next Stop finds it ABSENT (outside the corrective loop the whole
turn is evaluated; inside it the Stop fails open once). A state file that round 29 or earlier left directly
in the state dir (a flat `<sha>` file, or round 28's `.lock` file) is neither read nor removed by round 30
(volatile storage clears it at reboot), so a corrective loop running across the upgrade finds its state
ABSENT and fails open once. A same-uid process
that rewrites the state file directly while holding no lock, or swaps a state directory holding a lower count
into place, is outside the threat model (it can set the counter to any value anyway). Where flock is
unavailable (no fcntl, or a filesystem that refuses it), no state is ever written, so, as with an unpersistable state, only final messages are
checked and every violation under stop_hook_active is allowed with a warning. A record over 4 MiB and anything beyond
64 MiB back are not scanned. Only the host clock is authoritative, so a wrong host clock is enforced
faithfully. The elapsed check is only as right as the lease (a stale, wrong-clock, or another session's
lease yields a false block or a false pass; so does a stale Session-start-UTC value, trusted as written).
The transcript fallback measures from the transcript's FIRST entry, which for a resumed or continued
session, or a transcript older than the current lease, predates the lease: it OVERSTATES elapsed, so a
correct lease-relative footer can then read as BEHIND and block (remedy: record Session-start-UTC in the
lease, or a timed id); a transcript whose first 256 KiB holds no complete timestamped record leaves the
footer unchecked. Subagent output is not checked. BLOCK CAP: after 3
consecutive block cycles a still-wrong final message PASSES with only a warning (by design: an unsatisfiable
hook must not wedge the actor), and when the count cannot be established at all it PASSES at once with a
one-line warning (the fail-open direction: a possible loop is never risked for an unverifiable count). The
cap counts blocks per continuous stop_hook_active run, not per violation (round 27): a corrected message that
passes here but is rejected by another Stop hook keeps the loop running, and a NEW violation later in that
same loop gets only the blocks the run has left, possibly none (then a capped allow with a warning), until a
Stop without stop_hook_active starts a new count. The cap exists ONLY as defence in depth against a loop the model cannot
satisfy; it is not how a violation is normally resolved. The marker record shape (a user-role entry whose
text starts "Stop hook feedback" and carries this hook's block-reason prefix) is INFERRED from the host's
documented Stop feedback and has NOT been observed in a live transcript; if the host records Stop feedback in
any other shape (a `system` record, an isMeta-only record, a different prefix) the transcript count never
reaches the cap, and the cap then rests on the hook's own recovery-state counter alone (round 24). A feedback record beyond the 8 MiB or
512-record tail is not counted; an isMeta user entry between cycles does not end the run, while any other
user entry does; a corrective continuation longer than 10 assistant entries is not counted as a consecutive
cycle by the transcript count (the own recovery-state counter still counts it). The counter lives in the
same volatile per-user state as the boundary; with that state unusable, or absent, a Stop under
stop_hook_active is allowed with a warning (fail open), so a violation in the corrective loop then goes
unenforced. That includes a corrective loop this hook did not start (another Stop hook blocked) in a session
where this hook has never yet written its state for the transcript (round 26: the absence cannot be told
apart from a deletion, so it fails open rather than risk a loop). The capped
warning reaches
the user only through systemMessage, so its visibility depends on the host honouring that field; the stderr
copy is diagnostic logging only (on exit 0 the host sends stderr to its debug log, not to the user).

Self-test: python3 -I -S -B stamp-truth-stop.py --self-test
Run beside its sibling hooks, the self-test also checks that the code shared verbatim with them is identical.
Run alone (a single-hook install), those sibling-parity checks are SKIPPED, not passed, each naming the absent
sibling; with env AIQT_HOOKS_REQUIRE_SIBLINGS=1 an absent sibling FAILS them instead (for a repository gate). A
sibling that is present but unreadable fails them either way.
"""

import bisect
import datetime
import hashlib
import json
import os
import re
import stat
import sys
import time

try:
    import fcntl
except ImportError:  # no flock on this platform: locking is unavailable, so every Stop fails open
    fcntl = None

UTC = datetime.timezone.utc
US = 1_000_000
MINUTE = 60 * US
AHEAD_US = 1 * MINUTE
BEHIND_US = 10 * MINUTE
FINAL_BEHIND_US = 30 * MINUTE
ELAPSED_AHEAD_US = 2 * MINUTE  # footer AHEAD tolerance; its BEHIND tolerance is the message's `behind`
LEASE_MAX_BYTES = 1 << 20
STATE_MAX_BYTES = 1 << 16
MAX_SCAN_BYTES = 64 << 20
MAX_RECORD_BYTES = 4 << 20
CHUNK = 1 << 16
TAIL_BYTES = 256
MAX_PENDING = 8
MAX_REPORTED = 20
BLOCK_PREFIX = "Clock-truth check (stamp-truth-stop hook) failed"
FEEDBACK_PREFIX = "Stop hook feedback"
BLOCK_CAP = 3  # consecutive prior block cycles after which a stop_hook_active Stop allows with a warning
CAP_SCAN_BYTES = 8 << 20
CAP_SCAN_RECORDS = 512
CAP_GAP_ASSISTANT = 10  # max assistant entries between counted cycles (and after the newest)
_EPOCH = datetime.datetime(1970, 1, 1)
_EPOCH_UTC = _EPOCH.replace(tzinfo=UTC)
_ONE_US = datetime.timedelta(microseconds=1)

# Duplicated verbatim in future-stamp-write.py; the self-test asserts the two copies are identical.
SCHED_KEYWORDS = ("due", "deadline", "expir", "until", "next", "scheduled", "not before", "not-before", "eta",
                  "planned", "target date", "target:", "by ", "through", "valid")
SCHED_GAP_TOKENS = 3
TIME_GRAMMAR = r"(?<!\d)(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,9}))?)?"
ZONE_GRAMMAR = r"(?:[ ]?(Z)|[ ]?([+-]\d{2}(?::?\d{2})?)|[ ](UTC|GMT|[A-Z]{2,6}))(?![A-Za-z0-9])"

# Round 24: a keyword matches as a WHOLE word (an optional plural `s`, then no further letter or digit), so
# "validated", "throughput", "duet", or "etag" exempt nothing; only a listed STEM matches as a prefix ("expir"
# covers expires, expiry, expiration).
SCHED_STEMS = ("expir",)
_SCHED_RE = re.compile(r"(?<![a-z0-9])(?:" + "|".join(
    re.escape(k) + (r"[a-z]*" if k in SCHED_STEMS else r"(?:s(?![a-z0-9]))?(?![a-z0-9])" if k[-1].isalnum() else "")
    for k in SCHED_KEYWORDS) + ")", re.IGNORECASE)  # a keyword ending in a space or `:` already ends a word
_TOKEN_RE = re.compile(r"\S+")
_WORDCH_RE = re.compile(r"[A-Za-z0-9]")
_CLAIM_RE = re.compile(TIME_GRAMMAR + ZONE_GRAMMAR)
_BTICK_RE = re.compile(r"`+")
_SESS_RE = re.compile(r"[A-Za-z0-9]{1,32}-(\d{8}T\d{6}Z)")
_FOOT_RE = re.compile(r"(?<![A-Za-z0-9])session[ \t*_:\-\u2013\u2014]{1,8}elapsed[ \t*_:\-\u2013\u2014]{0,8}"
                      r"(\d{1,4}):([0-5]\d)(?!\d)", re.IGNORECASE)
# the parenthetical footer form: (session: Xh Ym), (session: Xh), or (session: Ym)
_SFOOT_RE = re.compile(r"\(session:[ \t]*(?:(\d{1,4})h(?:[ \t]*(\d{1,4})m)?|(\d{1,4})m)[ \t]*\)", re.IGNORECASE)
_FENCE_RE = re.compile(r"`{3,}|~{3,}")
_BULLET_RE = re.compile(r"(?:[-+*]|\d{1,9}[.)])(?=[ \t])")
_NOT_GENUINE_PREFIXES = ("<task-notification>", "<system-reminder>", "[SYSTEM NOTIFICATION",
                         "<local-command-", FEEDBACK_PREFIX, "<user-prompt-submit-hook>")


def _cfg(name, env=None):
    """AIQT_<name> primary; the legacy ORCH_<name> spelling is accepted as a fallback."""
    env = os.environ if env is None else env
    v = env.get("AIQT_" + name)
    return v if v is not None else env.get("ORCH_" + name)


def _is_worker(env=None):
    """True in a subordinate worker process: AIQT_HOOKS_WORKER=1. Kept identical across the three hooks
    (python3 -I forbids a sibling import)."""
    env = os.environ if env is None else env
    if env.get("AIQT_HOOKS_WORKER") == "1":
        return True
    return env.get("ORCH_WORKER") == "1" or "ORCH_VERIFY_OWNER" in env  # legacy spellings

def _sibling_or_skip(name, env=None):
    """Self-test helper, kept identical across the three hooks: the path of sibling hook `name` beside this file.
    A genuinely absent sibling (os.lstat raises FileNotFoundError, nothing broader) SKIPS the calling test with a
    message naming it, so a single-hook install self-tests clean; with env AIQT_HOOKS_REQUIRE_SIBLINGS=1 the
    absence FAILS the test instead, so a repository gate never skips parity silently. Any other error (an
    unreadable directory, say) propagates, and a sibling that exists but cannot be loaded fails when it is read,
    so only a genuine absence ever skips."""
    import unittest
    env = os.environ if env is None else env
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    try:
        os.lstat(path)
    except FileNotFoundError:
        if env.get("AIQT_HOOKS_REQUIRE_SIBLINGS") == "1":
            raise AssertionError(f"sibling hook {name} is absent ({path}) and AIQT_HOOKS_REQUIRE_SIBLINGS=1 "
                                 "requires it") from None
        raise unittest.SkipTest(f"sibling hook {name} is absent (a standalone install); set "
                                "AIQT_HOOKS_REQUIRE_SIBLINGS=1 to require it") from None
    return path


# ---- lease / elapsed (kept identical to clock-inject.py; python3 -I forbids a sibling import) ----

def read_regular(path, limit):
    """Bytes (at most `limit`) of a REGULAR file, or None. Opened non-blocking so a FIFO cannot stall us."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOCTTY", 0) | getattr(os, "O_CLOEXEC", 0))
    except (OSError, TypeError, ValueError):
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        chunks, got = [], 0
        while got < limit:
            b = os.read(fd, min(1 << 16, limit - got))
            if not b:
                break
            chunks.append(b)
            got += len(b)
        return b"".join(chunks)
    except OSError:
        return None
    finally:
        os.close(fd)


def lease_file():
    """Return the configured lease file path (env AIQT_LEASE_FILE, see _cfg), or None when none is set."""
    env = _cfg("LEASE_FILE")
    if env:
        return env
    return None


_LEASE_FIELD_RE = re.compile(r"[ \t]*(?:[-*][ \t]+)?(\*\*)?(Active-session|Session-start-UTC):(?(1)\*\*)(.*)")
_START_VALUE_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z|\d{8}T\d{6}Z")
_HEADING_RE = re.compile(r"[ \t]{0,3}#{1,6}(?:[ \t]|$)")
TRANSCRIPT_PREFIX_BYTES = 256 << 10


def _utc_field(value):
    """Aware UTC datetime for a lease time value, YYYYMMDDTHHMMSSZ or YYYY-MM-DDTHH:MM:SSZ, else None."""
    if not isinstance(value, str) or not _START_VALUE_RE.fullmatch(value):
        return None
    try:
        return datetime.datetime.strptime(value.replace("-", "").replace(":", ""), "%Y%m%dT%H%M%SZ").replace(
            tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def transcript_start(path):
    """Earliest parseable top-level `timestamp` (UTC; a naive value is read as UTC) among the complete JSONL
    records in the first TRANSCRIPT_PREFIX_BYTES of a REGULAR transcript file, else None. Same read posture as
    the lease (non-blocking open, regular files only, bounded read). A record the bound may have cut is not
    parsed, and only a record holding the bytes `"timestamp"` is parsed at all."""
    if not isinstance(path, str) or not path:
        return None
    data = read_regular(path, TRANSCRIPT_PREFIX_BYTES)
    if not data:
        return None
    recs = data.split(b"\n")
    if len(data) >= TRANSCRIPT_PREFIX_BYTES:
        recs.pop()  # the last piece may be cut by the bound
    best = None
    for rec in recs:
        if b'"timestamp"' not in rec:
            continue
        try:
            entry = json.loads(rec)
            ts = entry.get("timestamp") if isinstance(entry, dict) else None
            if not isinstance(ts, str):
                continue
            dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            dt = dt.replace(tzinfo=datetime.timezone.utc) if dt.tzinfo is None else dt.astimezone(
                datetime.timezone.utc)
        except (ValueError, TypeError, OverflowError, RecursionError):
            continue
        if best is None or dt < best:
            best = dt
    return best


def lease_start(path, transcript=None):
    """UTC session start, or None. Only the FIRST Active-session field (`Active-session:`, `**Active-session:**`,
    or either after a `-` or `*` bullet) is read. Precedence: (a) its id's time when the value is
    <label>-YYYYMMDDTHHMMSSZ; (b) when the id carries no time, the FIRST Session-start-UTC field (same forms)
    of the lease's header section (the file up to the first heading after the Active-session field); (c)
    else the earliest timestamp of `transcript`, when given; (d) else None. An inactive lease (`none`, any
    case, or an empty value), a well-formed id with an impossible time, no Active-session field, or an
    unreadable lease is None with no fallback."""
    if not path:
        return None
    data = read_regular(path, LEASE_MAX_BYTES)
    if data is None:
        return None
    active, start = False, None  # start: the text of the first Session-start-UTC value seen
    for line in data.decode("utf-8", "replace").splitlines():
        if active and _HEADING_RE.match(line):
            break  # the header section ends at the first heading after the Active-session field
        m = _LEASE_FIELD_RE.match(line)
        if not m:
            continue
        value = m.group(3).strip()
        if m.group(2) == "Session-start-UTC":
            if start is None:
                start = value
                if active:
                    break
            continue
        if active:
            continue  # only the FIRST Active-session field is read
        if not value or value.lower() == "none":
            return None  # inactive: no elapsed, and no fallback
        sess = _SESS_RE.fullmatch(value)
        if sess:
            return _utc_field(sess.group(1))
        active = True  # an active id that carries no time
        if start is not None:
            break
    if not active:
        return None
    got = _utc_field(start)
    return got if got is not None else transcript_start(transcript)


def fmt_elapsed_us(us):
    if us < 0:
        return None
    secs = us // US
    return f"{secs // 3600:02d}:{(secs % 3600) // 60:02d}"


# ---- time arithmetic (integer microseconds since the epoch) ----

def to_us(dt):
    """Epoch microseconds for an aware datetime (exact integer arithmetic, no overflow)."""
    return (dt - _EPOCH_UTC) // _ONE_US


def fmt_us(us):
    try:
        return (_EPOCH_UTC + datetime.timedelta(microseconds=us)).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, ValueError):
        return f"epoch{us // US:+d}s"


def _naive_us(y, mo, d, hh, mi, ss, us):
    return (datetime.datetime(y, mo, d, hh, mi, ss, us) - _EPOCH) // _ONE_US


def _local_us(y, mo, d, hh, mi, ss, abbr):
    """Epoch microseconds for a local wall time whose zone abbreviation is `abbr`, or None."""
    found = []
    for isdst in (0, 1):  # an explicit isdst makes mktime deterministic (no dependence on earlier calls)
        try:
            ts = time.mktime((y, mo, d, hh, mi, ss, 0, 0, isdst))
            lt = time.localtime(ts)
        except (OverflowError, ValueError, OSError):
            continue
        if (lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, lt.tm_min, lt.tm_sec) == (y, mo, d, hh, mi, ss) \
                and lt.tm_zone == abbr:
            found.append(int(ts))
    return min(found) * US if found else None


def claim_us(m):
    """Epoch microseconds for a matched claim, or None (invalid fields, or an abbreviation not local then)."""
    try:
        y, mo, d, hh, mi = (int(m.group(i)) for i in range(1, 6))
        ss = int(m.group(6) or 0)
        us = int(((m.group(7) or "") + "000000")[:6])
        z, off, abbr = m.group(8), m.group(9), m.group(10)
        if z or abbr in ("UTC", "GMT"):
            return _naive_us(y, mo, d, hh, mi, ss, us)
        if off:
            digits = off[1:].replace(":", "")
            hours, mins = int(digits[:2]), int(digits[2:] or 0)
            if hours > 18 or mins > 59:
                return None
            base, delta = _naive_us(y, mo, d, hh, mi, ss, us), (hours * 3600 + mins * 60) * US
            return base - delta if off[0] == "+" else base + delta
        loc = _local_us(y, mo, d, hh, mi, ss, abbr)
        return None if loc is None else loc + us
    except (ValueError, OverflowError, OSError, TypeError):
        return None


# ---- the contract ----

def _code_lines(lines):
    """Indexes of lines inside a MATCHED fenced code block (fence lines included); an unclosed fence is text."""
    code, open_i, fch, flen = set(), None, "", 0
    for i, ln in enumerate(lines):
        s = ln.strip(" \t")
        if open_i is None:
            m = _FENCE_RE.match(s)
            if m and not (s[0] == "`" and "`" in s[m.end():]):
                open_i, fch, flen = i, s[0], m.end()
        elif s and len(s) >= flen and s == fch * len(s):
            code.update(range(open_i, i + 1))
            open_i = None
    return code


def sched_exempter(line):
    """A predicate pos -> True when a scheduling keyword ends at most SCHED_GAP_TOKENS word tokens before
    position `pos` of `line` (a word token holds a letter or digit; punctuation-only tokens such as `:` or `[`,
    and the token containing `pos`, are not counted). Linear: keyword ends and tokens are found once per line.
    Duplicated verbatim in the sibling hook; the self-test asserts the two copies are identical."""
    ends = [k.end() for k in _SCHED_RE.finditer(line)]
    if not ends:
        return lambda pos: False
    toks = [(t.start(), t.end()) for t in _TOKEN_RE.finditer(line) if _WORDCH_RE.search(t.group())]
    starts, tends = [s for s, _ in toks], [e for _, e in toks]

    def exempt(pos):
        k = bisect.bisect_right(ends, pos) - 1
        if k < 0:
            return False
        between = bisect.bisect_right(tends, pos) - bisect.bisect_left(starts, ends[k])
        return between <= SCHED_GAP_TOKENS
    return exempt


def _lead_end(line):
    """Index where the permitted status-stamp prefix of `line` ends: whitespace, one bullet, then `*`, `_`,
    `[`, and whitespace. A claim starting exactly there is a line-leading status stamp."""
    i, n = 0, len(line)
    while i < n and line[i] in " \t":
        i += 1
    b = _BULLET_RE.match(line, i)
    if b:
        i = b.end()
    while i < n and line[i] in " \t*_[":
        i += 1
    return i


def _code_spans(line):
    """Sorted [start, end) inline code spans on one line: a backtick run closed by the next run of the same
    length (runs of other lengths inside are content); an unclosed run opens nothing. Linear."""
    spans, open_len, open_start = [], 0, 0
    for r in _BTICK_RE.finditer(line):
        n = r.end() - r.start()
        if not open_len:
            open_len, open_start = n, r.start()
        elif n == open_len:
            spans.append((open_start, r.end()))
            open_len = 0
    return spans


def _in_spans(spans, pos):
    k = bisect.bisect_right(spans, (pos, float("inf"))) - 1
    return k >= 0 and pos < spans[k][1]


def _minutes(off_us):
    mins = round(off_us / MINUTE)
    return f"{abs(mins)} min {'AHEAD' if mins > 0 else 'BEHIND'}"


def _check_claim(m, status, sched, ref, behind, where, cache=None, header=None):
    """A violation string for one claim, or None. `status`: the claim is a line-leading status stamp.
    `sched`: a scheduling keyword immediately precedes it (never set for a status stamp). `header`: the claim
    is the message's HEADER stamp, its first line-leading stamp, the only one checked for BEHIND (None means
    the same as `status`). `cache` maps a literal to its converted value so a repeated literal is converted
    once."""
    header = status if header is None else header
    literal = m.group(0)
    if cache is not None and literal in cache:
        got = cache[literal]
    else:
        got = claim_us(m)
        if cache is not None:
            cache[literal] = got
    if got is None:
        # round 26: a claim that cannot be converted (an invalid date such as 2020-02-30, or a letter abbreviation
        # that is not the local zone at that wall time) cannot be evaluated, so it FAILS OPEN, status stamp or not
        return None
    diff = got - ref
    if diff > AHEAD_US and (status or not sched):
        return f"timestamp {literal} in {where}: {_minutes(diff)} of when it was written ({fmt_us(ref)})"
    if status and header and -diff > behind:
        return f"status stamp {literal} in {where}: {_minutes(diff)} of when it was written ({fmt_us(ref)})"
    return None


def _footer_minutes(m):
    """Claimed elapsed minutes of a footer match of either form (_FOOT_RE or _SFOOT_RE)."""
    if m.re is _FOOT_RE:
        return int(m.group(1)) * 60 + int(m.group(2))
    return int(m.group(1) or 0) * 60 + int(m.group(2) or m.group(3) or 0)


def check_message(text, ref, start, where, behind):
    """Violation strings for one assistant message written at `ref` (epoch us); `start` is epoch us or None."""
    bad, seen, cache = [], set(), {}
    lines = text.splitlines()
    code = _code_lines(lines)
    foot, foot_at = None, (-1, -1)  # the last elapsed footer (either form) outside code and quotes
    header_seen = False  # the first line-leading stamp (the header) is the only one checked for BEHIND
    for i, line in enumerate(lines):
        if i in code or line.lstrip().startswith(">"):
            continue
        spans = _code_spans(line) if "`" in line else []
        lead = exempt = None
        for m in _CLAIM_RE.finditer(line):
            try:
                if spans and _in_spans(spans, m.start()):
                    continue
                if lead is None:
                    lead = _lead_end(line)
                status = m.start() == lead
                # round 24: only a header in the bracketed console form CLAIMS the current time; an unbracketed
                # leading date (a history line such as "2020-01-01T00:00Z service launched") is never BEHIND
                header = status and not header_seen and line[m.start() - 1:m.start()] == "["
                header_seen = header_seen or status
                sched = False
                if not status:
                    if exempt is None:
                        exempt = sched_exempter(line)
                    sched = exempt(m.start())
                v = _check_claim(m, status, sched, ref, behind, where, cache, header)
            except Exception:
                continue  # isolate one claim
            if v and v not in seen:
                seen.add(v)
                bad.append(v)
        for rx in (_FOOT_RE, _SFOOT_RE):
            for m in rx.finditer(line):
                if not (spans and _in_spans(spans, m.start())) and (i, m.start()) > foot_at:
                    foot, foot_at = m, (i, m.start())
    if start is not None and foot is not None:
        true_el = ref - start
        try:
            off = _footer_minutes(foot) * MINUTE - true_el
            if true_el >= 0 and (off > ELAPSED_AHEAD_US or -off > behind):
                bad.append(f"\"{' '.join(foot.group(0).split())}\" (last elapsed footer in {where}): true elapsed "
                           f"when written was {fmt_elapsed_us(true_el)} ({_minutes(off)})")
        except Exception:
            pass
    return bad


# ---- transcript ----

def _reverse_records(fd, size, floor=0):
    """Yield (start_offset, record) last-first for non-empty records starting at or after `floor`. Linear:
    pieces of a record are kept as a list and joined once; a record over MAX_RECORD_BYTES is not accumulated
    and yields None in place of its bytes."""
    pos, lower = size, max(floor, size - MAX_SCAN_BYTES, 0)
    tail, tail_len, over = [], 0, False
    while pos > lower:
        n = min(CHUNK, pos - lower)
        pos -= n
        chunk = os.pread(fd, n, pos)
        if len(chunk) != n:
            raise OSError("short read")
        j = n
        while True:
            k = chunk.rfind(b"\n", 0, j)
            piece = chunk[k + 1:j]
            if not over:
                tail.append(piece)
                tail_len += len(piece)
                if tail_len > MAX_RECORD_BYTES:
                    over, tail = True, []
            if k < 0:
                break
            if over:
                yield pos + k + 1, None
            elif tail_len:
                yield pos + k + 1, b"".join(reversed(tail))
            tail, tail_len, over, j = [], 0, False, k
    if pos == lower and (lower == 0 or lower == floor):
        if over:
            yield pos, None
        elif tail_len:
            yield pos, b"".join(reversed(tail))


def _entry_texts(content):
    if isinstance(content, str):
        return [content]
    out = []
    if isinstance(content, list):
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "text" and isinstance(blk.get("text"), str):
                out.append(blk["text"])
    return out


def _content(entry):
    msg = entry.get("message")
    return msg.get("content") if isinstance(msg, dict) else None


def _has_tool_result(content):
    return isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result"
                                             for b in content)


def _is_genuine_user(entry):
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):
        return False
    content = _content(entry)
    if _has_tool_result(content):
        return False
    texts = _entry_texts(content)
    if not texts:
        return False
    return not texts[0].lstrip().startswith(_NOT_GENUINE_PREFIXES)


def _parse_ts(value):
    """Epoch us from a transcript timestamp; a naive value is UTC (the transcript format); else None."""
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return (dt - _EPOCH) // _ONE_US
        return (dt.replace(tzinfo=None) - _EPOCH) // _ONE_US - dt.utcoffset() // _ONE_US
    except (ValueError, OverflowError, TypeError):
        return None


def _tail_sha(fd, offset):
    """SHA-256 of up to TAIL_BYTES of the transcript just before `offset` (None at offset 0)."""
    if offset <= 0:
        return None
    n = min(TAIL_BYTES, offset)
    b = os.pread(fd, n, offset - n)
    if len(b) != n:
        raise OSError("short read")
    return hashlib.sha256(b).hexdigest()


def turn_messages(path, floor=0, floor_tail=None):
    """This turn's assistant text after byte offset `floor`, as a dict:
    msgs: [(text, written_at_us_or_None)] oldest first, after the later of the last genuine user message and
          `floor`;
    size: the transcript size, or None when it is unreadable or not a regular file;
    end:  the offset just past the last COMPLETE record (an unfinished trailing record, one with no newline
          yet that does not parse, is never passed, so it is read again once complete);
    tail: the SHA-256 of the bytes just before `end`;
    reset: True when `floor` was unusable (beyond the end, or the bytes before it changed), so the whole turn
          was read from the start;
    user: True when a genuine user message was met after the floor."""
    out = {"msgs": [], "size": None, "end": 0, "tail": None, "reset": False, "user": False}
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOCTTY", 0) | getattr(os, "O_CLOEXEC", 0))
    except (OSError, TypeError, ValueError):
        return out
    msgs = []
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return out
        size = st.st_size
        if floor and (floor > size or floor_tail is None or _tail_sha(fd, floor) != floor_tail):
            floor, out["reset"] = 0, True
        terminated = size == 0 or os.pread(fd, 1, size - 1) == b"\n"
        end, first = (size if terminated else floor), True
        for off, raw in _reverse_records(fd, size, floor):
            trailing, first = first and not terminated, False
            try:
                entry = json.loads(raw) if raw is not None else None
            except Exception:
                entry = None  # malformed or unfinished; isolated
            if trailing and isinstance(entry, dict):
                end = size  # a parseable record lacking only its newline is complete
            elif trailing:
                end = off  # unfinished: stop the boundary before it
            if not isinstance(entry, dict):
                continue
            try:
                if _is_genuine_user(entry):
                    out["user"] = True
                    break
                if entry.get("type") == "assistant" and not entry.get("isSidechain") and not entry.get("isMeta"):
                    texts = _entry_texts(_content(entry))
                    if texts:
                        msgs.append(("\n".join(texts), _parse_ts(entry.get("timestamp"))))
            except Exception:
                continue  # isolate a malformed entry
        out["size"], out["end"], out["tail"] = size, end, _tail_sha(fd, end)
    except OSError:
        out["size"] = None  # keep whatever was collected; no boundary can be trusted
    finally:
        os.close(fd)
    msgs.reverse()
    out["msgs"] = msgs
    return out


def _is_block_marker(entry):
    """True when a transcript record is this hook's own block feedback: ONLY a user-role entry whose first text
    starts with the host's Stop-feedback prefix (FEEDBACK_PREFIX) and carries BLOCK_PREFIX. Any other record
    type (a `system` record included) is never a marker, whatever it quotes: the fail-safe direction, since a
    missed marker only lets blocking continue. The shape is inferred from the host's documented Stop feedback,
    not observed live (see BLOCK CAP)."""
    if entry.get("type") != "user" or _has_tool_result(_content(entry)):
        return False
    msg = entry.get("message")
    if isinstance(msg, dict) and msg.get("role", "user") != "user":
        return False
    texts = _entry_texts(_content(entry))
    return bool(texts) and texts[0].lstrip().startswith(FEEDBACK_PREFIX) and any(BLOCK_PREFIX in t for t in texts)


def prior_block_cycles(path, cap=BLOCK_CAP):
    """How many consecutive block cycles of this hook immediately precede now, counted backwards from the end
    of the transcript and stopping at `cap`. Memory-free: no state, only this hook's own block-reason marker
    in the transcript tail (at most CAP_SCAN_BYTES and CAP_SCAN_RECORDS records). A cycle is one marker
    record (see _is_block_marker: user-role Stop feedback only, never a `system` record); duplicate markers
    count once unless a non-isMeta, non-isSidechain assistant entry separates them (an isMeta assistant entry
    does not separate cycles, as in turn_messages); assistant entries, tool results, isMeta and isSidechain
    entries, and other record types are passed over; any other user entry (a genuine user message, a
    notification) ends the run. Cycles are consecutive only when TIGHT: more than CAP_GAP_ASSISTANT assistant
    entries (an isMeta one included, the conservative direction) between the end of the transcript and the
    newest marker, or between two markers, ends the run there, so a long stretch of ordinary work after old
    blocks resets the count (a corrective continuation that runs a few tools still counts). Every rule errs
    toward UNDER-counting, which fails safe (blocking continues). 0 when the transcript is unreadable."""
    return block_cycles(path, cap)[0]


def block_cycles(path, cap=BLOCK_CAP):
    """(count, established) for prior_block_cycles. `established` is False when the count could not be
    established (round 24): the transcript is unreadable or not a regular file, the walk ended at a counting
    gap (more than CAP_GAP_ASSISTANT assistant entries) or at the record or byte bound before reaching `cap`, a
    genuine user entry, or the start of the file."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOCTTY", 0) | getattr(os, "O_CLOEXEC", 0))
    except (OSError, TypeError, ValueError):
        return 0, False
    count, grouped, gap, established = 0, False, 0, False
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return 0, False
        lower = max(0, st.st_size - CAP_SCAN_BYTES)
        established = lower == 0  # a walk that runs out reached the start of the file only when unbounded
        for k, (_off, raw) in enumerate(_reverse_records(fd, st.st_size, lower)):
            if count >= cap:
                established = True
                break
            if k >= CAP_SCAN_RECORDS:
                established = False
                break
            try:
                entry = json.loads(raw) if raw is not None else None
            except Exception:
                entry = None
            if not isinstance(entry, dict) or entry.get("isSidechain"):
                continue
            if entry.get("type") == "assistant":
                if not entry.get("isMeta"):
                    grouped = False  # only a real assistant turn separates two cycles
                gap += 1  # an isMeta entry still counts toward the gap: the under-counting direction
                if gap > CAP_GAP_ASSISTANT:
                    established = False  # a counting gap (round 24: the count is then not established)
                    break  # a long stretch without a block: the run of consecutive cycles ends here
            elif _is_block_marker(entry):
                if not grouped:
                    count, grouped = count + 1, True
                gap = 0
            elif (entry.get("type") == "user" and not entry.get("isMeta")
                  and not _has_tool_result(_content(entry))):
                established = True
                break  # a genuine user message or a notification: not a consecutive block cycle
    except OSError:
        established = False
    finally:
        os.close(fd)
    return min(count, cap), established or count >= cap


# ---- recovery-boundary state ----

def _sha(text):
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def default_state_dir():
    x = os.environ.get("XDG_RUNTIME_DIR")
    if x and os.path.isabs(x):
        return os.path.join(x, "clock-truth")
    return f"/dev/shm/clock-truth-{os.geteuid()}"


STATE_FILE = "state"  # round 30: the state file's name inside its per-transcript subdirectory
PRUNE_AGE_NS = 7 * 86400 * 10 ** 9  # round 30: a per-transcript subdirectory unwritten this long may be pruned
PRUNE_SCAN = 4096  # round 30: at most this many subdirectory names are examined per prune
PRUNE_BATCH = 32  # round 30: at most this many subdirectories are removed per prune
PRUNE_CURSOR = "prune-cursor"  # round 31: the file in the state root naming where the next prune resumes


def state_subdir(tp):
    """Round 30: the name of the per-transcript state SUBDIRECTORY for transcript path `tp`, inside the private
    state root: the SHA-256 of the path plus `.d` (the suffix keeps it apart from the flat `<sha>` state files
    that rounds 29 and earlier left directly in the root, which are neither read nor removed)."""
    return _sha(tp) + ".d"


def _is_state_subdir(name):
    return name.endswith(".d") and _is_hash(name[:-2])


def _check_private(fd):
    """Raise OSError (closing `fd`) unless the OPENED object is a directory owned by this user with no group or
    other permission bits."""
    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077:
            raise OSError("state dir is not private")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _open_private_dir(path, create=True, dir_fd=None):
    """A descriptor for `path` as a private (0700-or-stricter, own-uid, real) directory, or raise OSError. It is
    created 0700 when missing (unless `create` is False), opened no-follow with O_DIRECTORY, and checked on the
    OPENED object (round 29), so every later read and write made relative to the descriptor stays in the
    directory that was checked. Round 30: with `dir_fd` the name is created and opened RELATIVE to that
    descriptor (the per-transcript subdirectory inside the private root)."""
    if create:
        try:
            os.mkdir(path, 0o700, dir_fd=dir_fd)
        except FileExistsError:
            pass
    return _check_private(os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
                                  | getattr(os, "O_CLOEXEC", 0), dir_fd=dir_fd))


def _open_state_dir(sdir, tp, create=True):
    """Round 30: a descriptor for the private per-transcript subdirectory of `tp` inside the private state root
    `sdir`, created (both, 0700, no-follow) when missing unless `create` is False, or raise OSError (a missing
    subdirectory with `create` False raises FileNotFoundError)."""
    root = _open_private_dir(sdir)
    try:
        return _open_private_dir(state_subdir(tp), create, root)
    finally:
        os.close(root)


def _is_hash(h):
    return isinstance(h, str) and len(h) == 64 and all(c in "0123456789abcdef" for c in h)


def load_state(sdir, tp, extra=None, dfd=None):
    """(offset, tail hash or None, [pending late-arrival hashes], ok). ok is True with (0, None, []) when the
    state is genuinely ABSENT (a usable private dir holding no file for this transcript); ok is False with
    (0, None, []) when the state is UNAVAILABLE: the dir cannot be created or is not private, or the file
    cannot be opened, is not a regular own-uid file, cannot be read, or is malformed. When `extra` (a dict) is
    given and ok is True, extra["blocks"] receives the consecutive-block counter (0 when absent; round 24), and
    an absent state also sets extra["absent"] (round 26: under stop_hook_active the caller reads absence as an
    unknown count, not 0). Round 29: with `dfd` (the evaluation's locked state-dir descriptor) the file is
    opened RELATIVE to it, never through `sdir`; without it the dir is opened and checked for this call. Round
    30: `dfd` is the transcript's own state SUBDIRECTORY (see state_subdir), holding the file STATE_FILE; without
    it, a missing subdirectory is an ABSENT state (it is not created by a read)."""
    unavailable = (0, None, [], False)
    own = dfd is None

    def absent():
        if extra is not None:
            extra["blocks"], extra["absent"] = 0, True  # round 26: the caller decides what absence means
        return 0, None, [], True
    try:
        if own:
            dfd = _open_state_dir(sdir, tp, create=False)
    except FileNotFoundError:
        return absent()
    except (OSError, TypeError, ValueError):
        return unavailable
    try:
        try:
            fd = os.open(STATE_FILE, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                         dir_fd=dfd)
        except FileNotFoundError:
            return absent()
        except (OSError, TypeError, ValueError):
            return unavailable
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid():
                return unavailable
            obj = json.loads(os.read(fd, STATE_MAX_BYTES))
            off, tail, pending = obj.get("offset"), obj.get("tail"), obj.get("pending")
            blocks = obj.get("blocks", 0)  # absent in a state written before round 24: no blocks counted
            if obj.get("v") != 2 or type(off) is not int or off < 0 or not isinstance(pending, list) \
                    or not (tail is None or _is_hash(tail)) or (off > 0) != (tail is not None) \
                    or not all(_is_hash(h) for h in pending) or type(blocks) is not int or blocks < 0:
                return unavailable
            if extra is not None:
                extra["blocks"] = blocks
            return off, tail, pending[-MAX_PENDING:], True
        except (OSError, ValueError, AttributeError):
            return unavailable
        finally:
            os.close(fd)
    finally:
        if own:
            os.close(dfd)


def save_state(sdir, tp, offset, tail, pending, blocks=0, dfd=None):
    """Record the boundary, pending late-arrival hashes, and the consecutive-block counter. Returns True only
    when the state file was written and atomically renamed into place; any failure returns False (never
    raises). Round 29: with `dfd` the temp file, the rename, and any cleanup are all RELATIVE to that
    descriptor (os.replace with src_dir_fd and dst_dir_fd), so the write lands in the directory the caller
    locked; without it the dir is opened and checked for this call. Round 30: `dfd` is the transcript's own
    state SUBDIRECTORY; without it the subdirectory is created when missing."""
    tmp, own = None, dfd is None
    try:
        if own:
            dfd = _open_state_dir(sdir, tp)
    except Exception:
        return False
    try:
        tmp = f"{STATE_FILE}.{os.getpid()}.{os.urandom(4).hex()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK
                     | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=dfd)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):  # round 32: never write a non-regular file
                raise OSError("state temporary is not a regular file")
            os.write(fd, json.dumps({"v": 2, "offset": offset, "tail": tail,
                                     "pending": list(pending)[-MAX_PENDING:], "blocks": blocks}).encode())
        finally:
            os.close(fd)
        os.replace(tmp, STATE_FILE, src_dir_fd=dfd, dst_dir_fd=dfd)
        tmp = None
        return True
    except Exception:
        return False
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
        if own:
            os.close(dfd)


def lock_state(sdir, tp, info=None):
    """Round 29: take the EXCLUSIVE, NON-BLOCKING evaluation lock, an fcntl.flock on a private state DIRECTORY
    itself, opened once (no-follow, O_DIRECTORY) and checked on the opened object to be an own-uid directory
    with no group or other permission bits. Round 30: the locked directory is the transcript's OWN state
    subdirectory (state_subdir(tp), created 0700 and opened no-follow RELATIVE to the private root), so only
    Stops of the SAME transcript contend. Returns (dfd, status): "held" (dfd holds the lock until it is
    closed; every state read and write of the evaluation is made relative to it); "busy" (another evaluation
    holds the lock; dfd is None); "unavailable" (the dir is usable but flock is missing on this platform or
    failed: dfd is open for READS only, and the caller must write nothing); or "nodir" (the root or the
    subdirectory cannot be created or opened, or is not private: no state can be read or written; dfd is
    None). When `info` (a dict) is given, info["created"] is True when this call created the subdirectory.
    The caller closes a returned dfd. Never raises. (Round 28 locked a separate `.lock` file, whose deletion
    let a second evaluation lock a new inode: a directory cannot be replaced by a single-file unlink.)"""
    try:
        root = _open_private_dir(sdir)
        try:
            name = state_subdir(tp)
            try:
                os.mkdir(name, 0o700, dir_fd=root)
                if info is not None:
                    info["created"] = True
            except FileExistsError:
                pass
            dfd = _open_private_dir(name, False, root)
        finally:
            os.close(root)
    except (OSError, TypeError, ValueError):
        return None, "nodir"
    if fcntl is None:
        return dfd, "unavailable"
    try:
        fcntl.flock(dfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return dfd, "held"
    except BlockingIOError:
        os.close(dfd)
        return None, "busy"
    except Exception:
        return dfd, "unavailable"


def _read_prune_cursor(root):
    """Round 31: the subdirectory name stored in PRUNE_CURSOR (opened no-follow relative to the state root's
    descriptor `root`; it must be a regular own-uid file holding one state_subdir-shaped name), else None."""
    try:
        fd = os.open(PRUNE_CURSOR, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                     dir_fd=root)
    except (OSError, TypeError, ValueError):
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid():
            return None
        name = os.read(fd, 128).decode("ascii").strip()
        return name if _is_state_subdir(name) else None
    except (OSError, ValueError):
        return None
    finally:
        os.close(fd)


def _write_prune_cursor(root, name):
    """Round 31: record `name` in PRUNE_CURSOR (relative to `root`), or remove the cursor when `name` is None.
    Round 32: the existing PRUNE_CURSOR is NEVER opened for writing (a FIFO planted there blocked round 31's
    write-only open forever, hanging the Stop). The name is written to an EXCLUSIVELY created regular temporary
    file in the same directory (O_CREAT|O_EXCL|O_NOFOLLOW|O_NONBLOCK, checked regular on the opened object) and
    atomically renamed over PRUNE_CURSOR (os.replace relative to `root`), which replaces whatever entry was
    there without opening it; the temporary file is removed on any failure. A failed write only makes a later
    prune start from the first name. Never raises."""
    tmp = None
    try:
        if name is None:
            os.unlink(PRUNE_CURSOR, dir_fd=root)
            return
        tmp = f"{PRUNE_CURSOR}.{os.getpid()}.{os.urandom(4).hex()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK
                     | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=root)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError("prune cursor temporary is not a regular file")
            os.write(fd, name.encode("ascii"))
        finally:
            os.close(fd)
        os.replace(tmp, PRUNE_CURSOR, src_dir_fd=root, dst_dir_fd=root)
        tmp = None
    except Exception:
        pass
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp, dir_fd=root)
            except Exception:
                pass


def prune_state(sdir, keep=None, now_ns=None):
    """Round 30: remove per-transcript state subdirectories left by transcripts no longer in use, so their
    number stays bounded. Lists the private root `sdir` (opened no-follow, never created), sorts its
    state_subdir-shaped names (less `keep`), and examines at most PRUNE_SCAN of them, starting just after the
    persisted cursor name (round 31, PRUNE_CURSOR) and wrapping around, so successive prunes advance through the
    whole sorted set (round 30 examined the first PRUNE_SCAN entries in directory order every time, starving any
    listed beyond them). It removes at most PRUNE_BATCH subdirectories, each only when ALL of these hold: it
    opens no-follow as a private own-uid directory; a NON-BLOCKING exclusive flock on it SUCCEEDS (a
    subdirectory an evaluation holds is never touched); and, checked after the lock is taken, it has not been
    written for PRUNE_AGE_NS (its mtime, which every save's atomic rename updates). Its regular entries are
    unlinked relative to its descriptor, and it is removed by name only while that name still refers to the
    locked inode. The cursor then records the last name examined, or is removed when every name was examined.
    Returns the number removed. Never raises."""
    removed = 0
    if fcntl is None:
        return 0
    try:
        root = _open_private_dir(sdir, create=False)
    except Exception:
        return 0
    try:
        now_ns = time.time_ns() if now_ns is None else now_ns
        with os.scandir(root) as it:
            names = sorted(entry.name for entry in it if entry.name != keep and _is_state_subdir(entry.name))
        cursor = _read_prune_cursor(root)
        start = bisect.bisect_right(names, cursor) if cursor is not None else 0
        window = (names[start:] + names[:start])[:PRUNE_SCAN]  # sorted order from the cursor, wrapping around
        last = None  # the last name examined
        for name in window:
            if removed >= PRUNE_BATCH:
                break
            last = name
            try:
                cfd = _open_private_dir(name, False, root)
            except Exception:
                continue
            try:
                try:
                    fcntl.flock(cfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except Exception:
                    continue  # held by an evaluation (or unlockable): never removed
                st = os.fstat(cfd)
                if now_ns - st.st_mtime_ns < PRUNE_AGE_NS:
                    continue
                for child in os.listdir(cfd):
                    try:
                        os.unlink(child, dir_fd=cfd)
                    except OSError:
                        pass
                cur = os.stat(name, dir_fd=root, follow_symlinks=False)
                if (cur.st_dev, cur.st_ino) == (st.st_dev, st.st_ino):
                    os.rmdir(name, dir_fd=root)
                    removed += 1
            except Exception:
                continue
            finally:
                os.close(cfd)
        # round 31: resume after the last name examined, or start afresh once every name has been examined
        whole = len(names) <= PRUNE_SCAN and last == (window[-1] if window else None)
        _write_prune_cursor(root, None if whole else last)
    except Exception:
        pass
    finally:
        os.close(root)
    return removed


def evaluate(payload, now, start, sdir=None, notes=None):
    """Return a block reason string, or None to pass (see _evaluate). Round 28: the WHOLE load, evaluate, and
    save runs under an exclusive non-blocking lock (lock_state; round 29: on a state directory itself, with
    every state read and write relative to the locked directory descriptor; round 30: on the transcript's own
    state subdirectory), so two concurrent Stops can never interleave a load of the recovery state with
    another's save. A Stop that finds the lock HELD by another
    evaluation FAILS OPEN: it is allowed unchecked with a one-line warning appended to `notes`, and it reads and
    writes no state, so the counter is unchanged. A Stop that cannot lock for any other reason (locking
    unavailable, or no usable private state dir) is evaluated but WRITES NO STATE, exactly as for an
    unpersistable state: only its final message can block, and under stop_hook_active nothing can block (a
    block there must record its raised counter, and none is recorded, so every violation fails open with a
    warning), so the counter is unchanged and no loop can form. With no transcript_path there is nothing to key
    a state file and no state is ever read or written. Round 30: a Stop that CREATED its transcript's
    subdirectory then prunes stale ones (prune_state), after its own lock is released."""
    tp = payload.get("transcript_path")
    tp = tp if isinstance(tp, str) and tp else None
    sdir = sdir or default_state_dir()
    if not tp:
        return _evaluate(payload, now, start, notes, None, False)
    info = {}
    dfd, status = lock_state(sdir, tp, info)
    if status == "busy":
        if notes is not None:
            notes.append(f"WARNING: stamp-truth-stop did not evaluate this Stop (another evaluation holds the "
                         f"state lock), so it is ALLOWED with its clock claims UNCHECKED and its "
                         f"block counter unchanged; the real clock now reads UTC "
                         f"[{now.strftime('%Y-%m-%dT%H:%M:%SZ')}].")
        return None
    try:
        return _evaluate(payload, now, start, notes, dfd, status == "held")
    finally:
        if dfd is not None:
            os.close(dfd)
        if info.get("created"):
            prune_state(sdir, state_subdir(tp))


def _evaluate(payload, now, start, notes, dfd, persist):
    """Return a block reason string, or None to pass. `now` and `start` are aware datetimes (start may be None).
    When the block cap turns a block into an allow, the warning is appended to `notes` (a list) if given.
    Called by evaluate() with the lock held (round 28). Round 29: `dfd` is the locked state-dir descriptor (round
    30: the transcript's own state subdirectory) and
    every state read and write is made relative to it (None: no usable private state dir, so the state is
    UNAVAILABLE); `persist` False (the lock could not be taken: locking unavailable, or no usable private state
    dir) means no state is written by this call."""
    bad, seen, over = [], set(), [0]  # a bounded report list; `seen` dedupes in O(1); `over` counts the rest

    def add(vs):
        for v in vs:
            if v in seen:
                continue
            seen.add(v)
            if len(bad) < MAX_REPORTED:
                bad.append(v)
            else:
                over[0] += 1

    now_us = to_us(now)
    start_us = to_us(start) if start is not None else None
    lam = payload.get("last_assistant_message")
    lam = lam if isinstance(lam, str) and lam.strip() else None
    tp = payload.get("transcript_path")
    tp = tp if isinstance(tp, str) and tp else None
    extra = {}  # receives the consecutive-block counter when the state is readable (round 24)
    if not tp:
        offset, tail, pending, state_ok = 0, None, [], True
    elif dfd is None:
        offset, tail, pending, state_ok = 0, None, [], False  # no usable private state dir: UNAVAILABLE
    else:
        offset, tail, pending, state_ok = load_state(None, tp, extra, dfd)
    blocks = extra.get("blocks")  # None: the counter is unavailable (no transcript_path, or unusable state)
    active = payload.get("stop_hook_active") is True
    if active and extra.get("absent"):
        # round 26: this hook writes the state at the block that starts a corrective loop, so an ABSENT state under
        # stop_hook_active (deleted between Stops, or its directory removed mid-call) is an UNKNOWN count, never 0
        blocks = None
    had_state = bool(offset or pending or blocks)
    t = turn_messages(tp, offset, tail) if tp else {"msgs": [], "size": None}
    msgs = t["msgs"]
    if blocks is not None and not active:
        # only a Stop outside the corrective loop starts the count again; under stop_hook_active the counter is
        # PRESERVED whatever the transcript shows (round 26: a transcript rewrite that resets the boundary, or a
        # user-role entry read as a genuine user message, reset it and let a loop block without limit)
        blocks = 0
    if t.get("reset") or t.get("user"):
        pending = []  # a reset transcript or a new genuine user message invalidates the late-arrival exception
    remaining = list(pending)
    lam_in_transcript = lam is not None and any(text.strip() == lam.strip() for text, _ in msgs)
    # the FINAL message: last_assistant_message, else the last collected transcript assistant entry
    final, final_ref = lam, now_us
    if lam is not None and msgs and msgs[-1][0].strip() == lam.strip():
        if msgs[-1][1] is not None:
            final_ref = msgs[-1][1]
        msgs = msgs[:-1]  # checked below as the final message
    elif lam is None and msgs:
        (final, final_ref), msgs = msgs[-1], msgs[:-1]
        h = _sha(final.strip())
        if h in remaining:
            remaining.remove(h)  # the already-reported final message arriving late: skipped once, consumed
            final = None
        elif final_ref is None:
            final = None  # no trustworthy written_at
    final_bad, earlier_bad = [], []
    if final is not None:
        try:
            final_bad = check_message(final, final_ref, start_us, "your final message", FINAL_BEHIND_US)
        except Exception:
            pass
    # Older messages are checked only when the recovery state could be READ: with it unavailable the boundary
    # is unknown, and re-checking already-reported messages would re-block every Stop and wedge the actor.
    n = len(msgs) if state_ok else 0
    for i, (text, ref) in enumerate(msgs[:n], 1):
        h = _sha(text.strip())
        if h in remaining:
            remaining.remove(h)  # the already-reported final message arriving late: skipped once, consumed
            continue
        if ref is None:
            continue  # no trustworthy written_at
        try:
            earlier_bad.extend(check_message(text, ref, start_us, f"earlier message {i} of {n} this turn",
                                             BEHIND_US))
        except Exception:
            continue
    if active and not final_bad:
        earlier_bad = []  # the corrective loop: a correct final message is never re-blocked for older ones
    readable = bool(tp) and t["size"] is not None
    if not readable:
        earlier_bad = []  # no readable transcript size: no boundary can be persisted
    # BLOCK CAP (round 24): under stop_hook_active the prior consecutive blocks are the larger of this hook's
    # OWN counter in the recovery state and the transcript marker count; when neither can be established the
    # Stop FAILS OPEN with a one-line warning, and at BLOCK_CAP it is allowed with the cap warning
    cap, tknown = None, False  # cap None: block normally; "cap" or "open": allow with a warning instead
    if (final_bad or earlier_bad) and active:
        tcount, tknown = block_cycles(tp) if tp else (0, False)
        known = [c for c in (blocks, tcount if tknown else None) if c is not None]
        # the transcript count is never the only brake: without a usable own counter, fail open (round 24)
        cap = "open" if blocks is None else "cap" if max(known) >= BLOCK_CAP else None
    will_block = bool(final_bad or earlier_bad) and cap is None
    # The counter this Stop records (round 27: while stop_hook_active holds it NEVER decreases).
    #   block: loaded + 1. A block under stop_hook_active happens only with a loaded integer counter below the
    #          cap (an unknown one gives cap "open", a count at the cap gives "cap").
    #   fail-open allow ("open"): BLOCK_CAP (round 26), so the rest of that corrective loop stays capped.
    #   capped allow ("cap"), or a PASS, under stop_hook_active: the loaded counter, unchanged (round 27: a clean
    #          pass inside the loop used to reset it to 0, so alternating with another Stop hook that rejected
    #          the clean message let this hook block every other Stop without limit); an UNKNOWN counter (the
    #          state unreadable, so none was loaded) is recorded as BLOCK_CAP, capped, never as 0.
    #   any Stop without stop_hook_active: 0 then (+1 if it blocks); the only reset, since it starts a new turn.
    # CONCURRENCY (round 28). The argument below reads the file's successive values as a SEQUENCE of Stops, each
    # loading the counter and then saving what it computed from that load. That holds only if no two evaluations
    # for one transcript interleave between one's load and its save: round 27 assumed serialized Stops, and two
    # concurrent evaluations broke it (a clean Stop A loads 1 and pauses; a violating Stop B loads 1, saves 2,
    # and blocks; A saves 1; repeated, B blocked every time). The assumption is now DISCHARGED by the lock:
    # evaluate() runs this whole function (load, evaluate, save) under an exclusive flock, taken NON-BLOCKING.
    # SYNCHRONIZATION IDENTITY (round 29), per transcript (round 30): the lock is held on the inode of the
    # transcript's OWN private state SUBDIRECTORY (state_subdir(tp), inside the private state root, created
    # 0700 and opened no-follow relative to the root), through the descriptor `dfd` opened once per evaluation,
    # and EVERY state-file read (the load, and save()'s re-read) and write (the temp file and the atomic
    # os.replace, both relative to dfd) of this evaluation goes through that same descriptor, so the lock and
    # the data it guards are bound to ONE directory inode, and the Stops that contend for it are exactly the
    # Stops of that one transcript, the set this argument is about (round 29's per-USER directory made other
    # sessions' Stops contend too). Round 28 locked a
    # separate `.lock` file, and deleting only that file between A's final re-read and its save let B create and
    # lock a new inode, record 2, and block, after which A saved 1 (codex round 28: eight blocks in one run, the
    # counter going 1, 2, 1 each time, with no actor writing state or bypassing flock). A single-file unlink
    # cannot replace a directory: deleting the state file while A holds the lock leaves B finding the lock HELD;
    # removing the subdirectory or the root (rm -r) makes A's save fail (nothing can be created in a removed
    # directory); and renaming the subdirectory or the root away and recreating it lands A's save in the
    # ORPHANED directory, harmlessly, while B, in the new directory, finds the state ABSENT, an unknown count
    # that fails open and records BLOCK_CAP, so the counter the next Stop reads never moves down.
    # PRUNING (round 30): prune_state() removes a subdirectory only while IT holds that subdirectory's lock (a
    # non-blocking flock that fails whenever an evaluation holds it) and only when the subdirectory has gone
    # unwritten for PRUNE_AGE_NS, so a removal is an external deletion between Stops, the case the BOUND below covers
    # (the next load is None: fail open, recording BLOCK_CAP); a Stop that opened the subdirectory just before
    # the pruner removed it locks the removed inode, finds the state ABSENT, and cannot save (the rm -r case).
    # A Stop that finds the lock held reads and writes
    # nothing and allows; a Stop that cannot lock for another reason runs with `persist` False and writes
    # nothing, so under stop_hook_active it cannot block (a block needs a recorded raise, below). Neither can
    # move the counter or block inside the loop. So the Stops that write the state file are serialized, and each
    # one's load and save are adjacent in the sequence the argument reads. As defence in
    # depth against a writer that ignores the (advisory) lock, save() below also re-reads the stored counter
    # under stop_hook_active and never writes a value lower than it, and a block whose write would not RAISE
    # the stored counter is not counted as recorded (it fails open instead). RESIDUAL: a same-uid process that
    # rewrites the state file directly while holding no lock (or that swaps a directory holding a LOWER valid
    # count back into place) is outside the threat model: it can set the counter to any value anyway, so the
    # lock gives no protection against it and claims none.
    # BOUND (round 27). Take one continuous run of Stops with stop_hook_active true for one transcript_path (so
    # one state file). The state file is written only by save_state(), called through save() below with
    # `new_blocks` (or, round 28, the higher stored value), and never deleted by this hook while in use (round
    # 30: prune_state() deletes only a subdirectory it has locked and that has gone unwritten for PRUNE_AGE_NS,
    # which this argument treats exactly as an external deletion); every write is an
    # atomic rename of a complete v2 file. Let
    # c be the counter a Stop LOADS (None when absent or unreadable) and w what it writes. By the cases above,
    # within the run w >= c whenever c is an integer (block c + 1, cap or pass c, open BLOCK_CAP >= c since no
    # write ever exceeds BLOCK_CAP: a block writes c + 1 only when c < BLOCK_CAP), and w = BLOCK_CAP whenever
    # c is None. So, reading the file's successive values across the run, the counter is non-decreasing and
    # bounded by BLOCK_CAP. A Stop that BLOCKS under stop_hook_active must have loaded an integer c < BLOCK_CAP
    # and SAVED c + 1 (a failed save turns it into a fail-open allow below), so each such block raises the
    # recorded counter by exactly 1; a non-decreasing integer in [0, BLOCK_CAP] can be raised by 1 at most
    # BLOCK_CAP times, so the run holds at most BLOCK_CAP blocks (at most BLOCK_CAP - 1 when the Stop that
    # started it was this hook's own block, which recorded 1). Nothing in this argument reads the transcript
    # or another hook's output: other hooks' feedback records, a transcript rewrite or reset, a user-role entry
    # read as genuine, and clean or violating messages in any order change only the transcript count and the
    # boundary, neither of which can lower the counter or make a Stop block without the own counter below the
    # cap. An external deletion or corruption of the state file makes the next load None, which fails open
    # (a violation) or records BLOCK_CAP or nothing (a pass), never a lower integer. The residual is a writer
    # OTHER than this hook replacing the file (or the directory) with a valid lower count (the same uid,
    # deliberately), or writing it while ignoring the lock: outside the threat model (see CONCURRENCY).
    if will_block:
        new_blocks = (blocks or 0) + 1
    elif cap == "open" or (active and blocks is None):
        new_blocks = BLOCK_CAP
    elif active:
        new_blocks = blocks  # a capped allow or a pass inside the corrective loop: preserved (round 27)
    else:
        new_blocks = 0  # outside the corrective loop (blocks was already reset to 0 above)

    def save(off, tl, pend):
        """Write the state with `new_blocks`; True only when it was written and, for a block, the write RAISED the
        stored counter. Round 28: under stop_hook_active the stored counter is re-read first (the lock is held)
        and a lower value is never written over it; a block whose write would not raise it (another writer got
        there first) returns False, so the block fails open as an unrecorded one. Round 29: the re-read and the
        write are both relative to the locked directory descriptor."""
        if not persist or dfd is None:
            return False
        nb, raised = new_blocks, True
        if active:
            cur = {}
            ok = load_state(None, tp, cur, dfd)[3]
            stored = cur.get("blocks") if ok and not cur.get("absent") else None
            if stored is not None:
                nb, raised = max(new_blocks, stored), stored < new_blocks
        return save_state(None, tp, off, tl, pend, nb, dfd) and (raised or not will_block)

    saved = False  # whether this Stop's counter reached the recovery state
    if readable:
        if final_bad or earlier_bad:
            late = [_sha(lam.strip())] if lam is not None and not lam_in_transcript else []
            saved = save(t["end"], t["tail"], remaining + late)
            if not saved:
                earlier_bad = []  # the boundary cannot be PERSISTED: only the final message may block
        elif had_state or not state_ok:
            # advance, or repair an unreadable state; round 27: the counter is written too (preserved under
            # stop_hook_active, BLOCK_CAP when it was unknown), never reset to 0 inside the corrective loop
            save(t["end"], t["tail"], remaining)
    elif tp and (blocks is not None or cap == "open") and (will_block or had_state or cap == "open"):
        saved = save(offset, tail, pending)  # keep the boundary, record the counter
    if will_block and active and not saved:
        cap = "open"  # this block could not be recorded in the own counter, so the next Stop could not cap
    add(final_bad)
    add(earlier_bad)
    if not bad:
        return None
    shown = bad + ([f"... and {over[0]} more distinct violation(s) not listed"] if over[0] else [])
    if cap == "open":
        # round 24: the hook's own consecutive-block counter is unusable (no usable recovery state, or a block
        # that could not be recorded in it), so a loop cannot be ruled out: fail OPEN, loudly, whatever the
        # transcript count says
        if notes is not None:
            notes.append(f"WARNING: stamp-truth-stop could not establish its consecutive block count under "
                         f"stop_hook_active, so this Stop is ALLOWED with {len(bad) + over[0]} clock claim(s) "
                         f"UNRESOLVED (first: {bad[0]}); the real clock now reads UTC "
                         f"[{now.strftime('%Y-%m-%dT%H:%M:%SZ')}].".replace("\n", " "))
        return None
    if cap == "cap":
        # defence in depth against a corrective loop the model cannot satisfy (a hook defect, say): allow,
        # loudly, instead of blocking a fourth consecutive time
        if notes is not None:
            notes.append(f"WARNING: stamp-truth-stop block cap hit ({BLOCK_CAP} consecutive blocks already this "
                         "turn); this Stop is ALLOWED with these clock claims UNRESOLVED:\n- " + "\n- ".join(shown)
                         + f"\nThe real clock now reads UTC [{now.strftime('%Y-%m-%dT%H:%M:%SZ')}]. Correct them "
                         "in your next message; if the hook itself is wrong, report it.")
        return None
    local = now.astimezone()
    el = fmt_elapsed_us(now_us - start_us) if start_us is not None else None
    return (BLOCK_PREFIX + ": these clock claims do not match the real clock.\n- " + "\n- ".join(shown) +
            f"\nTRUE values now: local [{local.strftime('%Y-%m-%d %H:%M:%S')} {local.tzname()}], "
            f"UTC [{now.strftime('%Y-%m-%dT%H:%M:%SZ')}], session elapsed "
            f"{el if el else 'unknown (no lease start: see the hook docstring, Elapsed resolution)'}.\n"
            "Checked: every date-time with an explicit zone in your prose must not be ahead of when it was "
            "written; a [bracketed] stamp that starts the first status line must also be recent (an unbracketed "
            "leading date is history); the LAST elapsed footer (\"Session elapsed "
            "HH:MM\" or \"(session: Xh Ym)\") outside code and quotes must match the lease. Code fences, `>` quote lines, and `code spans` are "
            "exempt. A genuine FUTURE time mid-line (a deadline, an expiry, a planned run) is allowed only when a "
            "schedule or validity word immediately precedes it (due, deadline, expires, until, through, valid, "
            "next run at, scheduled for, not before, by, ...) or when it is in a `code span`; a line-leading "
            "stamp is always checked for being ahead. To "
            "quote the rejected value in a correction, put it in a `code span` or a `>` line, or leave it out. "
            "Re-read the clock with `date` and `date -u` (never compose a time from memory or context), then send "
            "a short corrected message with the correct stamp and, as its last footer, the correct elapsed value. "
            "Messages before this block are not re-checked.")


def _emit_line(text, stream=None):
    """Write one line to `stream` (default stdout) and flush it. An output error fails OPEN (round 24): it is
    swallowed, and the stream's descriptor is pointed at /dev/null so the interpreter's exit flush cannot fail
    either, so the hook still exits 0. If that rescue fails too, the process ends at once with os._exit(0)
    (no flush is retried), since any later write or exit flush could fail the hook."""
    s = sys.stdout if stream is None else stream
    try:
        print(text, file=s, flush=True)
        return True
    except Exception:
        try:
            fd = os.open(os.devnull, os.O_WRONLY | os.O_NONBLOCK)  # round 32: no blocking open anywhere
            try:
                os.dup2(fd, s.fileno())
            finally:
                os.close(fd)
        except Exception:
            os._exit(0)
        return False


def main(argv):
    try:
        self_test = len(argv) > 1 and argv[1] == "--self-test"
    except Exception:
        return 0  # an unusable argv fails open: exit 0 at once, evaluating nothing
    if self_test:
        return _self_test()
    try:
        if _is_worker():
            # round 24: the skip is no longer silent (stderr only, so worker output is never distorted)
            _emit_line("stamp-truth-stop: skipped, worker marker present (AIQT_HOOKS_WORKER=1 or a legacy spelling)",
                       sys.stderr)
            return 0
        buf = getattr(sys.stdin, "buffer", None)  # bytes; a text stream (the self-test) has no buffer
        payload = json.loads(buf.read() if buf is not None else sys.stdin.read())
        if not isinstance(payload, dict) or payload.get("agent_id"):
            return 0
        tp = payload.get("transcript_path") if isinstance(payload.get("transcript_path"), str) else None
        now = datetime.datetime.now(UTC)
        notes = []
        reason = evaluate(payload, now, lease_start(lease_file(), tp), notes=notes)
    except Exception:
        return 0  # fail-open silently
    if reason:
        _emit_line(json.dumps({"decision": "block", "reason": reason}))
    elif notes:
        _emit_line(json.dumps({"systemMessage": notes[0]}))  # the capped allow: visible to the user
        _emit_line(notes[0], sys.stderr)
    return 0


def _self_test():
    import importlib.util
    import inspect
    import io
    import shutil
    import subprocess
    import tempfile
    import threading
    import unittest

    def iso(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def lstamp(dt):
        loc = dt.astimezone()
        return f"[{loc.strftime('%Y-%m-%d %H:%M:%S')} {loc.tzname()}]"

    def ustamp(dt):
        return f"[{dt.strftime('%Y-%m-%dT%H:%M:%SZ')}]"

    def run_main(payload_text, env=None, argv=("stamp-truth-stop.py",)):
        old_in, old_out, old_env = sys.stdin, sys.stdout, dict(os.environ)
        sys.stdin = io.StringIO(payload_text) if isinstance(payload_text, str) else payload_text
        sys.stdout = io.StringIO()
        try:
            for k in ("AIQT_HOOKS_WORKER", "ORCH_WORKER", "ORCH_VERIFY_OWNER", "CLAUDE_PROJECT_DIR"):
                os.environ.pop(k, None)
            os.environ.update(env or {})
            rc = main(list(argv) if isinstance(argv, tuple) else argv)
            return rc, sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = old_in, old_out
            os.environ.clear()
            os.environ.update(old_env)

    def in_subprocess(expr, timeout):
        """Run `expr` (with module m loaded) in a fresh interpreter; return stdout, raising on timeout."""
        code = ("import importlib.util as u,datetime,os,time;os.environ['TZ']='EST5EDT,M3.2.0,M11.1.0';time.tzset();"
                "s=u.spec_from_file_location('m',%r);m=u.module_from_spec(s);s.loader.exec_module(m);"
                "UTC=datetime.timezone.utc;print(%s)" % (os.path.abspath(__file__), expr))
        return subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True,
                              timeout=timeout).stdout.strip()

    MIN = datetime.timedelta(minutes=1)

    class T(unittest.TestCase):
        def setUp(self):
            # Pin the zone (test hermeticity): a POSIX TZ string needs no tzdata on the host.
            self._env = {k: os.environ.get(k) for k in ("TZ", "AIQT_LEASE_FILE", "ORCH_LEASE_FILE",
                                                         "XDG_RUNTIME_DIR", "CLAUDE_PROJECT_DIR")}
            for k in ("AIQT_LEASE_FILE", "ORCH_LEASE_FILE", "CLAUDE_PROJECT_DIR"):
                os.environ.pop(k, None)
            os.environ["TZ"] = "EST5EDT,M3.2.0,M11.1.0"
            time.tzset()
            base = "/dev/shm" if os.path.isdir("/dev/shm") else None
            self.tmp = tempfile.mkdtemp(prefix="clk.", dir=base)
            os.environ["XDG_RUNTIME_DIR"] = self.tmp
            self.sdir = os.path.join(self.tmp, "state")
            self.tr = os.path.join(self.tmp, "t.jsonl")
            self.now = datetime.datetime(2026, 9, 23, 17, 45, 0, tzinfo=UTC)
            self.start = datetime.datetime(2026, 9, 23, 14, 18, 0, tzinfo=UTC)  # elapsed 03:27

        def tearDown(self):
            shutil.rmtree(self.tmp, ignore_errors=True)
            for k, v in self._env.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
            time.tzset()

        def write(self, entries, mode="w"):
            if mode == "w":  # a fresh transcript path: a real transcript is append-only
                self.tr = os.path.join(self.tmp, f"t{time.monotonic_ns()}.jsonl")
            with open(self.tr, mode) as f:
                for e in entries:
                    f.write((e if isinstance(e, str) else json.dumps(e)) + "\n")

        def user(self, text, ts=None, **extra):
            e = {"type": "user", "timestamp": iso(ts or self.now), "message": {"role": "user", "content": text}}
            e.update(extra)
            return e

        def asst(self, text, ts=None, **extra):
            e = {"type": "assistant", "timestamp": iso(ts or self.now),
                 "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}
            e.update(extra)
            return e

        def block_entry(self):
            return {"type": "user", "timestamp": iso(self.now),
                    "message": {"role": "user", "content": "Stop hook feedback:\n" + BLOCK_PREFIX + ": ..."}}

        def ev(self, entries=None, sdir=None, now=None, **extra):
            if entries is not None:
                self.write(entries)
            payload = {"transcript_path": self.tr, "hook_event_name": "Stop", "stop_hook_active": False}
            payload.update(extra)
            return evaluate(payload, now or self.now, self.start, sdir or self.sdir)

        def final(self, text, now=None):
            return evaluate({"last_assistant_message": text}, now or self.now, self.start, self.sdir)

        def seed(self, blocks=0, sdir=None):
            # the recovery state an earlier Stop of this hook leaves for the transcript (round 26: under
            # stop_hook_active an ABSENT state is an unknown count and fails open, so a test of the corrective
            # loop's other rules starts from the state the loop's own first block would have written)
            self.assertTrue(save_state(sdir or self.sdir, self.tr, 0, None, [], blocks))

        def key(self, tr=None, sdir=None, make=False):
            # round 30: the state FILE of a transcript, inside its own private state subdirectory (made 0700 when
            # `make`, with a private root)
            sub = os.path.join(sdir or self.sdir, state_subdir(tr or self.tr))
            if make:
                os.makedirs(sub, 0o700, exist_ok=True)
                os.chmod(os.path.dirname(sub), 0o700)
                os.chmod(sub, 0o700)
            return os.path.join(sub, STATE_FILE)

        def state_files(self, sdir):
            # round 30: every existing per-transcript state file under the state root `sdir`
            out = []
            for name in os.listdir(sdir) if os.path.isdir(sdir) else ():
                p = os.path.join(sdir, name, STATE_FILE)
                if os.path.isfile(p):
                    out.append(p)
            return out

        # -- AHEAD: every zoned claim anywhere --
        def test_correct_local_stamp_passes(self):
            self.assertIsNone(self.ev([self.user("go"), self.asst(f"{lstamp(self.now)} done.")]))

        def test_correct_utc_stamp_and_elapsed_pass(self):
            txt = f"{ustamp(self.now)} merged.\nSession elapsed 03:27, 0 compactions"
            self.assertIsNone(self.ev([self.user("go"), self.asst(txt)]))

        def test_stamp_30_min_ahead_blocks(self):
            r = self.ev([self.user("go"), self.asst(f"{lstamp(self.now + 30 * MIN)} x")])
            self.assertIn("30 min AHEAD", r)
            self.assertTrue(r.startswith(BLOCK_PREFIX))
            self.assertIn("TRUE values now: local [2026-09-23 13:45:00 EDT], UTC [2026-09-23T17:45:00Z]", r)

        def test_status_label_future_blocks(self):
            # finding (codex r2): 'Status: [future]' passed because only a message-leading stamp was checked
            self.assertIn("280 min AHEAD", self.final("Status: [2026-09-23T22:25Z] done."))

        def test_unbracketed_local_ahead_blocks(self):
            # finding (claude r2): an unbracketed console stamp was never checked
            self.assertIn("280 min AHEAD", self.final("2026-09-23 18:25 EDT starting phase 3"))
            self.assertIn("280 min AHEAD", self.final("progress at 2026-09-23 18:25:00 EDT, fine"))
            self.assertIn("280 min AHEAD", self.final("2026-09-23 18:25:02 EDT | 2026-09-23T22:25:02Z | x"))

        def test_stamp_after_other_text_blocks(self):
            # finding (gemini r2): any text before the stamp evaded the leading-only check
            self.assertIn("300 min AHEAD", self.final("<thinking>checking clock</thinking>\n"
                                                      "[2026-09-23 22:45:00 UTC] Status update."))

        def test_long_emphasis_prefix_blocks(self):
            # finding (codex r2): a 128-character window hid a stamp after a long permitted prefix
            self.assertIn("AHEAD", self.final("***" + " " * 128 + "[2026-09-23T22:25Z]"))

        def test_numeric_and_spaced_offsets(self):
            self.assertIsNone(self.final("[2026-09-23 22:45 +05] ok"))
            self.assertIsNone(self.final("[2026-09-23 23:15 +05:30] ok"))
            self.assertIn("300 min AHEAD", self.final("[2026-09-23 13:45 -09] x"))
            self.assertIn("300 min AHEAD", self.final("at 2026-09-23T13:45-0900 x"))
            os.environ["TZ"] = "<+05>-5"
            time.tzset()
            self.assertIsNone(self.final(f"{lstamp(self.now)} ok"))

        def test_fractional_seconds(self):
            self.assertIn("280 min AHEAD", self.final("[2026-09-23T22:25:00.000Z] x"))
            self.assertIsNone(self.final("[2026-09-23T17:44:59.123456789Z] x"))

        def test_zoneless_and_foreign_abbreviation_mid_line_ignored(self):
            self.assertIsNone(self.final("the job will run 2026-09-23 22:25 and 2026-09-24T01:00"))
            self.assertIsNone(self.final("their office shows 2026-09-23 22:25 PDT"))

        def test_status_foreign_abbreviation_fails_open(self):
            # round 26 (finding 3): a status stamp whose abbreviation is not local was UNVERIFIABLE and blocked; a
            # claim that cannot be converted cannot be evaluated, so it fails open (a disclosed MISS)
            self.assertIsNone(self.final("[2026-09-23 13:45:00 PDT] x"))
            self.assertIsNone(self.final("[2026-09-24 09:00 PDT] deploy scheduled"))

        def test_dst_fold_deterministic(self):
            # 2026-11-01 01:30 occurs twice in EST5EDT: EDT = 05:30Z, EST = 06:30Z
            t = datetime.datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
            self.assertIsNone(self.final("[2026-11-01 01:30 EDT] a", t))
            self.assertIn("60 min AHEAD", self.final("[2026-11-01 01:30 EST] a", t))

        # -- exemptions --
        def test_quoted_past_stamp_mid_line_passes(self):
            txt = (f"{lstamp(self.now)} The incident log said \"[2026-09-22T17:45:00Z] failed\" and "
                   "2026-09-20 10:00 UTC earlier.")
            self.assertIsNone(self.final(txt))

        def test_scheduled_future_with_keyword_passes(self):
            for txt in ("The next run is [2026-09-24T17:45:00Z].", "Deadline set for 2026-12-25 09:00:00 EST.",
                        "Retry not before 2026-09-24 01:00 UTC", "ETA 2026-09-24T00:00Z"):
                self.assertIsNone(self.final(txt), txt)

        def test_keyword_never_exempts_leading_status_stamp(self):
            # residual 1 closed: a keyword on the line no longer exempts the line-leading status stamp
            self.assertIn("280 min AHEAD", self.final("[2026-09-23T22:25Z] merged. Next: QA"))
            self.assertIn("280 min AHEAD", self.final("- **2026-09-23 18:25 EDT** deadline met, next up"))
            self.assertIn("BEHIND", self.final("[2026-09-23T16:00Z] Next: QA"))

        def test_keyword_exempts_only_following_non_leading_claim(self):
            now = ustamp(self.now)
            self.assertIsNone(self.final(f"{now} merged; next run [2026-09-24T01:00Z] as planned"))
            self.assertIn("2026-09-24T01:00Z in your final message: 435 min AHEAD",
                          self.final(f"{now} merged; [2026-09-24T01:00Z] retry by then"))
            self.assertIn("AHEAD", self.final("Summary: 2026-09-24T01:00Z done, next QA"))

        def test_fence_and_blockquote_exempt(self):
            txt = "x\n```\n[2099-01-01T00:00Z] in code\n```\n> [2099-01-01T00:00Z] quoted\n  > 2099-01-01 00:00 UTC"
            self.assertIsNone(self.final(txt))
            self.assertIsNone(self.final("x\n~~~~\n2099-01-01T00:00Z\n~~~~~\ny"))

        def test_unclosed_fence_is_checked(self):
            self.assertIn("AHEAD", self.final("x\n```\n[2099-01-01T00:00Z] unclosed"))
            self.assertIn("AHEAD", self.final("```x\n````\n2099-01-01T00:00Z after mismatched fence\n```\n"))

        def test_inline_code_span_exempt(self):
            # round 3 remedy: a code span is the documented way to quote a future or rejected value
            self.assertIsNone(self.final("example `2099-01-01T00:00Z` shape"))
            self.assertIsNone(self.final("example ``a ` 2099-01-01T00:00Z`` shape"))
            self.assertIn("AHEAD", self.final("unclosed `2099-01-01T00:00Z shape"))
            self.assertIn("AHEAD", self.final("`x` then 2099-01-01T00:00Z outside"))

        # -- BEHIND: status stamps only --
        def test_status_stamp_behind_blocks(self):
            r = self.ev([self.user("go"), self.asst(f"{ustamp(self.now - 20 * MIN)} x"), self.asst("done")])
            self.assertIn("20 min BEHIND", r)
            self.assertIn("20 min BEHIND", self.ev([self.user("go"), self.asst(f"- **{ustamp(self.now - 20 * MIN)}**"),
                                                    self.asst("done")]))

        def test_bullet_history_line_behind_blocks(self):
            # round 24 (item 2): this WAS a documented false positive (an unbracketed history stamp that is the
            # message's first line-leading stamp was read as its header and checked for BEHIND). Only a header in
            # the bracketed console form claims the current time now, so the history line passes; a BRACKETED
            # first line-leading history stamp is still read as the header (disclosed)
            self.assertIsNone(self.final("History:\n- 2026-09-22 10:00 UTC incident began\nok"))
            self.assertIsNone(self.final("History: the incident began 2026-09-22 10:00 UTC.\nok"))
            self.assertIn("BEHIND", self.final("History:\n- [2026-09-22 10:00 UTC] incident began\nok"))

        def test_r7_history_bullet_after_header_passes(self):
            # peer finding (MEDIUM): a history bullet after a correct header stamp blocked as ~1800 min BEHIND
            hdr = ustamp(self.now)
            self.assertIsNone(self.final(f"{hdr} status.\n- [2026-09-22 14:00Z] CI failed.\nok"))
            self.assertIsNone(self.final(f"{lstamp(self.now)} x\n1. 2026-09-20 10:00 UTC opened\n"
                                         "2. **2026-09-21 10:00 UTC** closed"))
            self.assertIsNone(self.ev([self.user("go"), self.asst(f"{hdr} a\n- [2026-09-22 14:00Z] old"),
                                       self.asst("done")]))
            # the header stamp itself is still checked for BEHIND, and AHEAD still applies to every stamp
            self.assertIn("35 min BEHIND", self.final(f"{ustamp(self.now - 35 * MIN)} x\n- [2026-09-22 14:00Z] y"))
            r = self.final(f"{hdr} x\n- [2026-09-23T20:00Z] fabricated history line")
            self.assertIn("2026-09-23T20:00Z in your final message: 135 min AHEAD", r)
            # a keyword never exempts a later line-leading stamp from AHEAD either
            self.assertIn("AHEAD", self.final(f"{hdr} x\n- [2026-09-23T20:00Z] next run"))
            # each message has its own header
            self.assertIn("20 min BEHIND", self.ev([self.user("go"), self.asst(f"{hdr} a"),
                                                    self.asst(f"{ustamp(self.now - 20 * MIN)} b"),
                                                    self.asst("done")]))

        def test_final_message_behind_tolerance_30(self):
            # finding (gemini r2): a long generation made a correct final status stamp read as BEHIND
            self.assertIsNone(self.final(f"{ustamp(self.now - 12 * MIN)} started long"))
            self.assertIn("35 min BEHIND", self.final(f"{ustamp(self.now - 35 * MIN)} stale"))

        # -- elapsed footer: last non-empty line only --
        def test_wrong_footer_on_last_line_blocks(self):
            for txt in ("status\nSession elapsed 08:07, 1 compaction", "x\nSession elapsed: 08:07", "x\n**Session elapsed** 08:07",
                        "x\n_Session elapsed_: 08:07\n\n", "x\nSession elapsed \u2014 08:07", "x\nsession - elapsed \u2013 08:07"):
                self.assertIn("true elapsed when written was 03:27", self.final(txt), repr(txt))
            self.assertIsNone(self.final("x\n**Session elapsed:** 03:28"))

        def test_footer_quoted_mid_message_passes(self):
            # findings (claude/gemini r2): a worker's footer, or the corrected wrong value, re-blocked
            txt = (f"{lstamp(self.now)} Worker done:\nSession elapsed 05:12\nCorrection: I wrote Session elapsed "
                   "08:07 earlier.\nSession elapsed 03:27")
            self.assertIsNone(self.final(txt))
            self.assertIsNone(self.final("x\n> Session elapsed 05:12"))
            self.assertIsNone(self.final("x\n```\nSession elapsed 05:12\n```\nSession elapsed 03:27\n> Session elapsed 05:12"))
            self.assertIsNone(self.final("x `Session elapsed 05:12` quoted"))

        def test_footer_tolerance_asymmetric(self):
            # finding (claude r4): a flat 2-minute footer tolerance false-blocked a long final wrap-up whose
            # status stamp (same provenance) was tolerated to 30 minutes
            txt = f"{ustamp(self.now)} done.\nSession elapsed 03:27"
            for gap in (3, 7, 25, 30):
                self.assertIsNone(self.final(txt, now=self.now + gap * MIN), gap)
            r = self.final(txt, now=self.now + 35 * MIN)
            self.assertIn("(last elapsed footer in your final message)", r)
            self.assertIn("35 min BEHIND", r)
            # AHEAD stays strict at 2 minutes, final message or not
            self.assertIsNone(self.final("x\nSession elapsed 03:29"))
            self.assertIn("3 min AHEAD", self.final("x\nSession elapsed 03:30"))
            # an earlier message keeps the 10-minute BEHIND window
            ref = to_us(self.now)
            self.assertEqual(check_message("Session elapsed 03:17", ref, to_us(self.start), "x", BEHIND_US), [])
            self.assertIn("11 min BEHIND",
                          check_message("Session elapsed 03:16", ref, to_us(self.start), "x", BEHIND_US)[0])
            self.assertIn("3 min AHEAD",
                          check_message("Session elapsed 03:30", ref, to_us(self.start), "x", BEHIND_US)[0])

        def test_footer_prose_collision_disclosed(self):
            # disclosed false positive: prose naming "session elapsed" before an HH:MM reads as the footer
            self.assertIsNotNone(self.final("The total session elapsed: 14:30 across all workers today."))
            self.assertIsNone(self.final("The total `session elapsed: 14:30` across all workers today."))

        def test_elapsed_unknown_lease_skipped(self):
            self.assertIsNone(evaluate({"last_assistant_message": "Session elapsed 99:00"}, self.now, None, self.sdir))

        # -- reference time --
        def test_final_message_ref_from_matching_entry(self):
            # finding (codex r2): the final message was always compared against now
            t = self.now - 20 * MIN
            ok = f"{ustamp(t)} ok\nSession elapsed 03:07"
            self.assertIsNone(self.ev([self.user("go", t), self.asst(ok, t)], last_assistant_message=ok))
            fab = f"{ustamp(self.now)} fabricated"
            self.assertIn("20 min AHEAD", self.ev([self.user("go", t), self.asst(fab, t)], last_assistant_message=fab))

        def test_entry_timestamp_is_the_reference(self):
            then = self.now - 30 * MIN
            self.assertIsNone(self.ev([self.user("go", then), self.asst(f"{lstamp(then)} early", then),
                                       self.asst(f"{lstamp(self.now)} late")]))

        def test_naive_transcript_timestamp_is_utc(self):
            then = datetime.datetime(2026, 9, 23, 17, 5, tzinfo=UTC)
            e = self.asst(f"{lstamp(then + 40 * MIN)} early but fabricated")
            e["timestamp"] = "2026-09-23T17:05:00"
            self.assertIn("40 min AHEAD", self.ev([self.user("go", then), e, self.asst(f"{lstamp(self.now)} late")]))

        def test_unparseable_transcript_timestamp_skipped_not_now(self):
            e = self.asst(f"{lstamp(self.now - 40 * MIN)} early")
            e["timestamp"] = "yesterday-ish"
            self.assertIsNone(self.ev([self.user("go"), e]))

        # -- isolation --
        def test_overflow_entry_does_not_hide_final(self):
            # finding (codex r2): ref + AHEAD overflowed on a 9999 entry and discarded the final violation
            e = self.asst("[2026-09-23T17:45Z] earlier", ts=None)
            e["timestamp"] = "9999-12-31T23:59:59Z"
            r = self.ev([self.user("go"), e], last_assistant_message="[2099-01-01T00:00Z] final")
            self.assertIn("in your final message", r)

        def test_claim_compare_is_overflow_safe(self):
            # the comparison itself is integer arithmetic: a 9999 reference cannot overflow it
            ref = to_us(datetime.datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC))
            line = "[9999-12-31T23:59Z] x"
            v = _check_claim(_CLAIM_RE.search(line), True, False, ref, BEHIND_US, "x")
            self.assertIsNone(v)
            line = "[2026-09-23T17:45Z] x"
            self.assertIn("BEHIND", _check_claim(_CLAIM_RE.search(line), True, False, ref, BEHIND_US, "x"))

        def test_year_one_local_zone_no_crash(self):
            # finding (codex r2): [0001-01-01 00:00 JST] under TZ=JST-9 raised in the conversion
            os.environ["TZ"] = "JST-9"
            time.tzset()
            r = self.final("[0001-01-01 00:00 JST] x\n[2099-01-01T00:00Z] y")
            self.assertIn("2099-01-01T00:00Z", r)
            # an invalid claim (month 13) is isolated and never discards a violation on the same message
            self.assertIn("2099-01-01T00:00Z", self.final("[2026-13-01T00:00Z] bad\n[2099-01-01T00:00Z] y"))

        def test_malformed_entries_isolated(self):
            ents = [self.user("go"), self.asst("[2099-01-01T00:00Z] x"), {"type": "assistant", "message": 42},
                    "{not json", "[1,2]"]
            self.assertIsNotNone(self.ev(ents))

        def test_pathological_inputs_linear(self):
            out = in_subprocess("(m.check_message('prose '+'`'*200000+'\\n'+'2026-09-23 '*100000+'\\n'+' '*200000+'x'"
                                "+'[2099-01-01T00:00Z]',0,None,'x',0), m.check_message('Session elapsed'*20000+"
                                "'\\n'+'Session '+'-'*200000,0,0,'x',0))", timeout=5)
            self.assertIn("AHEAD", out)

        # -- which messages --
        def test_only_after_last_genuine_user(self):
            old_bad = self.asst(f"{lstamp(self.now + 3 * 60 * MIN)} old turn")
            notif = self.user("<task-notification>done</task-notification>")
            tool_result = {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}}
            ents = [old_bad, self.user("next"), self.asst("[2026-09-23T17:44Z] a"), notif, tool_result,
                    self.asst(f"{lstamp(self.now)} b")]
            self.assertIsNone(self.ev(ents))
            ents[2] = self.asst("[2026-09-23T19:00Z] a")
            self.assertIsNotNone(self.ev(ents))

        def test_quoted_block_text_is_not_a_boundary(self):
            # finding (codex r2): a notification quoting the block prefix was taken as this hook's block
            ents = [self.user("go"), self.asst("[2026-09-23T22:25Z] bad"),
                    self.user(f"<task-notification>QA tested {BLOCK_PREFIX}</task-notification>", isMeta=True),
                    self.asst("Done.")]
            self.assertIn("280 min AHEAD", self.ev(ents, last_assistant_message="Done."))

        def test_stop_hook_active_checks_only_the_final_message(self):
            # peer finding (HIGH): under stop_hook_active an older message alone never re-blocks
            ents = [self.user("go"), self.asst("[2026-09-23T22:25:00Z] x"), self.asst("Done.")]
            self.assertIn("280 min AHEAD", self.ev(ents, last_assistant_message="Done."))
            self.write(ents)
            self.seed()
            self.assertIsNone(self.ev(stop_hook_active=True, last_assistant_message="Done."))
            r = self.ev(stop_hook_active=True, last_assistant_message="[2026-09-23T22:30:00Z] Done.")
            self.assertIn("in your final message: 285 min AHEAD", r)  # a bad FINAL message still blocks
            self.assertIn("earlier message", r)  # and, blocking anyway, the older one is reported too

        def test_recovery_boundary_via_state_file(self):
            bad_text = f"{lstamp(self.now + 40 * MIN)} wrong"
            ents = [self.user("go"), self.asst(bad_text)]
            self.assertTrue(self.ev(ents, last_assistant_message=bad_text).startswith(BLOCK_PREFIX))
            # one state file (round 29: the evaluation lock is the directory itself, so no `.lock` file; round 30:
            # in the transcript's own private subdirectory)
            self.assertEqual(os.listdir(self.sdir), [state_subdir(self.tr)])
            self.assertEqual(os.listdir(os.path.dirname(self.key())), [STATE_FILE])
            self.assertEqual(stat.S_IMODE(os.lstat(self.sdir).st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(os.lstat(os.path.dirname(self.key())).st_mode), 0o700)
            corrected = (f"{lstamp(self.now)} Correction: I previously wrote a wrong stamp.\n"
                         "Session elapsed 03:27")
            self.write([self.block_entry(), self.asst(corrected)], "a")
            self.assertIsNone(self.ev(stop_hook_active=True, last_assistant_message=corrected))
            # a fresh fabrication in an intermediate message is caught without stop_hook_active; under it only
            # the final message can block (disclosed residual), and the boundary still advances past it
            self.write([self.asst("[2026-09-23T20:00Z] again")], "a")
            self.assertIsNone(self.ev(stop_hook_active=True, last_assistant_message="fine"))
            self.write([self.asst("[2026-09-23T20:30Z] and again")], "a")
            self.assertIn("165 min AHEAD", self.ev(last_assistant_message="fine"))
            self.assertIn("165 min AHEAD", self.ev(stop_hook_active=True,
                                                   last_assistant_message="[2026-09-23T20:30Z] and again"))

        def test_lagging_blocked_final_not_rechecked(self):
            bad_text = "[2026-09-23T20:00Z] wrong"
            self.assertIsNotNone(self.ev([self.user("go")], last_assistant_message=bad_text))
            self.write([self.asst(bad_text), self.block_entry(), self.asst(f"{lstamp(self.now)} fixed")], "a")
            self.assertIsNone(self.ev(last_assistant_message=f"{lstamp(self.now)} fixed"))

        def test_state_unusable_checks_final_only(self):
            # peer finding (HIGH): an unusable state re-checked the whole turn, re-blocking every Stop
            os.mkdir(self.sdir, 0o755)
            os.chmod(self.sdir, 0o755)
            self.assertEqual(load_state(self.sdir, self.tr)[3], False)
            bad_text = "[2026-09-23T20:00Z] wrong"
            self.assertIsNotNone(self.ev([self.user("go"), self.asst(bad_text)], last_assistant_message=bad_text))
            self.write([self.block_entry(), self.asst("fixed")], "a")
            self.assertIsNone(self.ev(last_assistant_message="fixed"))
            self.assertIsNone(self.ev(stop_hook_active=True, last_assistant_message="fixed"))
            # round 24 (A): with the own counter unusable, a bad final message under stop_hook_active FAILS OPEN
            # with a warning (it blocked before: the transcript count was the only brake)
            notes = []
            self.assertIsNone(evaluate({"transcript_path": self.tr, "stop_hook_active": True,
                                        "last_assistant_message": bad_text}, self.now, self.start, self.sdir, notes))
            self.assertIn("135 min AHEAD", notes[0])
            self.assertIn("135 min AHEAD", self.ev(last_assistant_message=bad_text))  # outside the loop: blocks
            self.assertEqual(os.listdir(self.sdir), [])

        def test_r7_state_unreadable_never_wedges(self):
            # peer repro: an older future stamp, a correct final message, and a state dir raising
            # PermissionError gave three consecutive BLOCKs (two with stop_hook_active)
            locked = os.path.join(self.tmp, "locked")
            os.mkdir(locked, 0o700)
            os.chmod(locked, 0)
            sdir = os.path.join(locked, "state")
            try:
                if os.access(locked, os.R_OK | os.X_OK):
                    self.skipTest("privileges bypass mode 0")
                self.assertEqual(load_state(sdir, self.tr), (0, None, [], False))
                good = f"{ustamp(self.now)} corrected."
                self.write([self.user("go"), self.asst("[2026-09-23T22:25:00Z] early bad"), self.asst(good)])
                for k, active in enumerate((False, True, True)):
                    self.assertIsNone(self.ev(sdir=sdir, stop_hook_active=active, last_assistant_message=good))
                    # two feedback cycles only: a third would reach the round-8 block cap
                    self.write(([self.block_entry()] if k < 2 else []) + [self.asst(good)], "a")
                # round 24 (A): the state is unusable, so under stop_hook_active this fails open (was a block);
                # outside the loop the bad final message still blocks
                self.assertIsNone(self.ev(sdir=sdir, stop_hook_active=True,
                                          last_assistant_message="[2026-09-23T20:00Z] x"))
                self.assertIn("in your final message", self.ev(sdir=sdir,
                                                               last_assistant_message="[2026-09-23T20:00Z] x"))
            finally:
                os.chmod(locked, 0o700)

        # -- round 8: block cap (peer request, worker-harness) --
        def cap_ev(self, entries, active=True, final="[2026-09-23T20:00Z] still wrong"):
            self.write(entries)
            if active:
                self.seed()  # an own counter of 0, so the transcript marker count decides (round 26)
            notes = []
            payload = {"transcript_path": self.tr, "hook_event_name": "Stop", "stop_hook_active": active,
                       "last_assistant_message": final}
            return evaluate(payload, self.now, self.start, self.sdir, notes), notes

        def cycles(self, n, bad="[2026-09-23T20:00Z] still wrong"):
            out = [self.user("go"), self.asst(bad)]
            for _ in range(n):
                out += [self.block_entry(), self.asst(bad)]
            return out

        def test_r8_block_cap_allows_with_warning(self):
            r, notes = self.cap_ev(self.cycles(3))
            self.assertIsNone(r)
            self.assertEqual(len(notes), 1)
            self.assertIn("block cap hit", notes[0])
            self.assertIn("in your final message: 135 min AHEAD", notes[0])  # names the unresolved claim
            self.assertEqual(prior_block_cycles(self.tr), 3)
            r, notes = self.cap_ev(self.cycles(5))
            self.assertIsNone(r)
            self.assertTrue(notes)

        def test_r8_block_cap_uncapped_still_blocks(self):
            for entries, active in ((self.cycles(2), True),  # under the cap
                                    (self.cycles(3), False),  # the cap applies only under stop_hook_active
                                    # a genuine user message ends the run of consecutive cycles
                                    (self.cycles(2) + [self.user("try again"), self.asst("x"), self.block_entry(),
                                                       self.asst("y")], True)):
                r, notes = self.cap_ev(entries, active)
                self.assertTrue(r and r.startswith(BLOCK_PREFIX), (len(entries), active))
                self.assertEqual(notes, [])
            # a quoted prefix in a tool result or an isMeta notification is not a block cycle; tool-use
            # entries inside a cycle do not break the run
            quoted = [self.user("go"), self.asst("a"),
                      self.user(f"<task-notification>QA tested {BLOCK_PREFIX}</task-notification>", isMeta=True),
                      self.asst("b"),
                      self.user([{"type": "tool_result", "content": f"Stop hook feedback:\n{BLOCK_PREFIX}"}]),
                      self.asst("c"), self.block_entry(), self.asst("d")]
            self.write(quoted)
            self.assertEqual(prior_block_cycles(self.tr), 1)
            # a duplicate feedback entry, with no assistant between, is one cycle
            dup = self.cycles(2) + [self.block_entry(), self.block_entry(), self.asst("e")]
            self.write(dup)
            self.assertEqual(prior_block_cycles(self.tr), 3)
            self.assertEqual(prior_block_cycles(os.path.join(self.tmp, "missing.jsonl")), 0)

        def test_r8_block_cap_main_output(self):
            self.write(self.cycles(3))
            self.seed(sdir=default_state_dir())  # main() keys the state under XDG_RUNTIME_DIR (round 26)
            p = json.dumps({"transcript_path": self.tr, "hook_event_name": "Stop", "stop_hook_active": True,
                            "last_assistant_message": "[2099-01-01T00:00Z] still wrong"})
            old_err, sys.stderr = sys.stderr, io.StringIO()
            try:
                rc, out = run_main(p)
                err = sys.stderr.getvalue()
            finally:
                sys.stderr = old_err
            obj = json.loads(out)
            self.assertEqual(rc, 0)
            self.assertNotIn("decision", obj)
            self.assertIn("block cap hit", obj["systemMessage"])
            self.assertIn("block cap hit", err)

        def test_r8_block_cap_scan_bounded(self):
            # a long run of non-marker records never makes the count scan unbounded
            self.write(self.cycles(3)[:2] + [self.asst("filler")] * 20000 + self.cycles(3)[2:])
            t0 = time.monotonic()
            self.assertEqual(prior_block_cycles(self.tr), 3)
            self.write(self.cycles(3) + [self.asst("filler")] * (CAP_SCAN_RECORDS + 10))
            self.assertEqual(prior_block_cycles(self.tr), 0)  # the markers lie beyond the record bound
            self.assertLess(time.monotonic() - t0, 2.0)

        def test_r9_block_cap_needs_tight_cycles(self):
            # round 9 finding 3 (MED): old blocks followed by a long successful stretch still counted, so the
            # first bad final message after it was allowed; cycles now count only when tight
            def tool_cycle():
                return [{"type": "assistant", "message": {"role": "assistant", "content": [
                            {"type": "tool_use", "id": "t", "name": "Bash", "input": {}}]}},
                        self.user([{"type": "tool_result", "tool_use_id": "t", "content": "ok"}])]
            stale = self.cycles(3)
            for _ in range(100):
                stale += tool_cycle()
            self.write(stale)
            self.assertEqual(prior_block_cycles(self.tr), 0)
            r, notes = self.cap_ev(stale)
            self.assertTrue(r and r.startswith(BLOCK_PREFIX))
            self.assertEqual(notes, [])
            # at the gap bound the run still counts; one past it ends the run (cycles() ends on an assistant)
            self.write(self.cycles(3) + [self.asst("w")] * (CAP_GAP_ASSISTANT - 1))
            self.assertEqual(prior_block_cycles(self.tr), 3)
            self.write(self.cycles(3) + [self.asst("w")] * CAP_GAP_ASSISTANT)
            self.assertEqual(prior_block_cycles(self.tr), 0)
            # a long stretch BETWEEN old and new markers ends the run at the stretch
            self.write(self.cycles(2) + [self.asst("w")] * (CAP_GAP_ASSISTANT + 5) + [self.block_entry(),
                                                                                      self.asst("bad")])
            self.assertEqual(prior_block_cycles(self.tr), 1)
            # a corrective continuation that runs a few tools (reading the clock, say) is still a tight cycle
            tight = [self.user("go"), self.asst("bad")]
            for _ in range(3):
                tight += [self.block_entry()] + tool_cycle() + tool_cycle() + [self.asst("still bad")]
            r, notes = self.cap_ev(tight)
            self.assertIsNone(r)
            self.assertIn("block cap hit", notes[0])

        def test_r10_block_cap_only_undercounts(self):
            # codex round-8 [UNVERIFIABLE] overcounts: both now count lower and do NOT cap
            final = "[2099-01-01T00:00Z] complete"
            sysrec = {"type": "system", "content": BLOCK_PREFIX + ": x"}
            # case 1: two cycles, then an isMeta assistant entry and a duplicate feedback record: codex's exact
            # system-record form, and a user-feedback duplicate separated from its original only by the isMeta
            for ents0 in (self.cycles(2) + [self.asst("meta", isMeta=True), sysrec],
                          self.cycles(2)[:-1] + [self.asst("meta", isMeta=True), self.block_entry()]):
                dup = ents0[-1]
                ents = ents0 + [self.asst(final)]
                self.write(ents)
                self.assertEqual(prior_block_cycles(self.tr), 2, dup)
                r, notes = self.cap_ev(ents, final=final)
                self.assertTrue(r and r.startswith(BLOCK_PREFIX), dup)
                self.assertEqual(notes, [])
            # case 2: system records merely quoting BLOCK_PREFIX are never markers
            ents = [self.user("go"), self.asst("x"), sysrec, self.asst("x"), sysrec, self.asst("x"), sysrec,
                    self.asst(final)]
            self.write(ents)
            self.assertEqual(prior_block_cycles(self.tr), 0)
            r, notes = self.cap_ev(ents, final=final)
            self.assertTrue(r and r.startswith(BLOCK_PREFIX))
            self.assertEqual(notes, [])
            # duplicates separated only by metadata collapse; a non-user-role entry is not a marker
            meta = [self.user("m", isMeta=True), self.asst("meta", isMeta=True), sysrec]
            self.write(self.cycles(2) + [self.block_entry()] + meta + [self.block_entry(), self.asst("z")])
            self.assertEqual(prior_block_cycles(self.tr), 3)
            self.write(self.cycles(2) + [self.block_entry()] + meta + meta + [self.block_entry(), self.asst("z")])
            self.assertEqual(prior_block_cycles(self.tr), 3)
            odd = self.block_entry()
            odd["message"]["role"] = "assistant"
            self.write(self.cycles(2) + [odd, self.asst("z")])
            self.assertEqual(prior_block_cycles(self.tr), 0)  # not a marker, so a user entry that ends the run
            # the disclosure (item 3d): inferred shape, fail-safe never-fires, defence in depth only
            doc = " ".join(__doc__.split())
            for s in ("NOT been observed in a live transcript", "the transcript count never reaches the cap",
                      "ONLY as defence in depth"):
                self.assertIn(s, doc)

        def test_r10_stderr_is_not_a_visible_fallback(self):
            # codex round-8 [L]: on exit 0 stderr reaches only the debug log; systemMessage is the visible channel
            doc = " ".join(__doc__.split())
            self.assertNotIn("stderr is the fallback", doc)
            self.assertIn("stderr is diagnostic logging", doc)
            self.assertIn("the stderr copy is diagnostic logging only", doc)

        def test_r7_state_absent_is_not_unavailable(self):
            self.assertEqual(load_state(self.sdir, self.tr), (0, None, [], True))
            ents = [self.user("go"), self.asst("[2026-09-23T22:25:00Z] x"), self.asst("Done.")]
            self.assertIn("earlier message", self.ev(ents, last_assistant_message="Done."))

        def test_r7_state_unpersistable_drops_older_messages(self):
            # the state reads as absent but cannot be written: the boundary cannot advance, so only the final
            # message may block (else the next Stop re-reads and re-blocks the same older message)
            os.mkdir(self.sdir, 0o500)
            try:
                if os.access(self.sdir, os.W_OK):
                    self.skipTest("privileges bypass mode 0500")
                ents = [self.user("go"), self.asst("[2026-09-23T22:25:00Z] x"), self.asst("Done.")]
                self.assertIsNone(self.ev(ents, last_assistant_message="Done."))
                self.assertIn("in your final message", self.ev(last_assistant_message="[2026-09-23T20:00Z] y"))
                self.write([self.block_entry(), self.asst("[2026-09-23T20:00Z] y"), self.asst("ok")], "a")
                self.assertIsNone(self.ev(last_assistant_message="ok"))
            finally:
                os.chmod(self.sdir, 0o700)

        def test_r7_malformed_state_is_unavailable_then_repaired(self):
            self.write([self.user("go"), self.asst("[2026-09-23T22:25:00Z] x"), self.asst("Done.")])
            key = self.key(make=True)
            with open(key, "w") as f:
                f.write("{not json")
            self.assertEqual(load_state(self.sdir, self.tr)[3], False)
            self.assertIsNone(self.ev(last_assistant_message="Done."))  # final only
            off, _tail, _pending, ok = load_state(self.sdir, self.tr)
            self.assertEqual((ok, off), (True, os.path.getsize(self.tr)))  # rewritten valid on the pass

        def test_state_offset_past_end_ignored(self):
            bad_text = "[2026-09-23T20:00Z] wrong"
            self.assertIsNotNone(self.ev([self.user("go"), self.asst("pad " * 200), self.asst(bad_text)],
                                         last_assistant_message=bad_text))
            with open(self.tr, "w") as f:  # the same transcript path replaced by a shorter transcript
                f.write(json.dumps(self.user("go")) + "\n" + json.dumps(self.asst("[2026-09-23T20:30Z] new")) + "\n")
            self.assertIn("165 min AHEAD", self.ev(last_assistant_message="ok"))

        def test_state_symlink_not_followed(self):
            self.write([self.user("go"), self.asst("[2099-01-01T00:00Z] x")])
            key = self.key(make=True)
            target = os.path.join(self.tmp, "elsewhere.json")
            size = os.path.getsize(self.tr)
            with open(self.tr, "rb") as f:
                tail = hashlib.sha256(f.read()[-TAIL_BYTES:]).hexdigest()
            with open(target, "w") as f:  # a planted VALID state that, if followed, would hide the whole transcript
                f.write(json.dumps({"v": 2, "offset": size, "tail": tail, "pending": []}))
            shutil.copy(target, self.key(sdir=os.path.join(self.tmp, "real"), make=True))
            self.assertIsNone(self.ev(sdir=os.path.join(self.tmp, "real")))  # the planted state is valid if read
            os.symlink(target, key)
            self.assertIsNotNone(self.ev())

        def test_assistant_meta_entry_skipped(self):
            self.assertIsNone(self.ev([self.user("go"), self.asst("[2099-01-01T00:00Z] x", isMeta=True)]))

        def test_missing_transcript_still_checks_final_message(self):
            rc, out = run_main(json.dumps({"transcript_path": os.path.join(self.tmp, "missing.jsonl"),
                                           "last_assistant_message": "[2099-01-01T00:00Z] x"}))
            obj = json.loads(out)
            self.assertEqual((rc, obj["decision"]), (0, "block"))

        def test_subagent_stop_skipped(self):
            p = {"agent_id": "a1", "last_assistant_message": "[2099-01-01T00:00Z] x"}
            self.assertEqual(run_main(json.dumps(p)), (0, ""))

        def test_worker_kill_switch_and_garbage_silent(self):
            p = json.dumps({"last_assistant_message": "[2099-01-01T00:00Z] x"})
            self.assertEqual(run_main(p, {"AIQT_HOOKS_WORKER": "1"}), (0, ""))
            self.assertEqual(run_main(p, {"ORCH_WORKER": "1"}), (0, ""))
            self.assertEqual(run_main("not json"), (0, ""))

        def test_worker_detected_by_verify_owner(self):
            # legacy spelling: a launcher that exports ORCH_VERIFY_OWNER (any value, even empty) marks a worker
            p = json.dumps({"last_assistant_message": "[2099-01-01T00:00Z] x"})
            self.assertEqual(json.loads(run_main(p)[1])["decision"], "block")
            for value in ("x", ""):
                self.assertEqual(run_main(p, {"ORCH_VERIFY_OWNER": value}), (0, ""))
            self.assertTrue(_is_worker({"ORCH_VERIFY_OWNER": ""}))
            self.assertTrue(_is_worker({"ORCH_WORKER": "1"}))
            self.assertFalse(_is_worker({"ORCH_WORKER": "0"}))
            self.assertFalse(_is_worker({}))

        def test_aiqt_hooks_worker(self):
            p = json.dumps({"last_assistant_message": "[2099-01-01T00:00Z] x"})
            self.assertTrue(_is_worker({"AIQT_HOOKS_WORKER": "1"}))
            for value in ("0", "", "true", "yes", " 1"):
                self.assertFalse(_is_worker({"AIQT_HOOKS_WORKER": value}), value)
                self.assertEqual(json.loads(run_main(p, {"AIQT_HOOKS_WORKER": value})[1])["decision"], "block")
            self.assertTrue(_is_worker({"AIQT_HOOKS_WORKER": "0", "ORCH_VERIFY_OWNER": ""}))

        def test_cfg_precedence(self):
            self.assertEqual(_cfg("X", {"AIQT_X": "a", "ORCH_X": "o"}), "a")
            self.assertEqual(_cfg("X", {"AIQT_X": "", "ORCH_X": "o"}), "")
            self.assertEqual(_cfg("X", {"ORCH_X": "o"}), "o")
            self.assertIsNone(_cfg("X", {}))

        def test_lease_label_portability(self):
            lease = os.path.join(self.tmp, "lease.md")
            want = datetime.datetime(2026, 9, 23, 14, 17, 10, tzinfo=UTC)
            for label in ("sess", "S88", "a", "A" * 32):
                with open(lease, "w") as f:
                    f.write(f"Active-session: {label}-20260923T141710Z\n")
                self.assertEqual(lease_start(lease), want, label)
            for bad in ("A" * 33 + "-20260923T141710Z", "-20260923T141710Z", "S_88-20260923T141710Z",
                        "S88 20260923T141710Z", "S88-20260923T141710"):
                with open(lease, "w") as f:
                    f.write(f"Active-session: {bad}\nActive-session: sess-20200101T000000Z\n")
                self.assertIsNone(lease_start(lease), bad)

        def test_lease_discovery_precedence(self):
            # only an explicit env lease is used: AIQT_ beats the legacy ORCH_ spelling, and the project
            # directory never supplies one
            proj = os.path.join(self.tmp, "proj")
            for sub in (".working", ".aiqt", "private"):
                os.makedirs(os.path.join(proj, sub))
                for name in ("lease.md", "session-lease.md"):
                    with open(os.path.join(proj, sub, name), "w") as f:
                        f.write("Active-session: S88-20000101T000000Z\n")
            own = os.path.join(self.tmp, "own.md")
            with open(own, "w") as f:
                f.write("Active-session: S88-20260923T141710Z\n")
            os.environ["CLAUDE_PROJECT_DIR"] = proj
            self.assertIsNone(lease_file())
            os.environ["ORCH_LEASE_FILE"] = own + ".legacy"
            self.assertEqual(lease_file(), own + ".legacy")
            os.environ["AIQT_LEASE_FILE"] = own
            self.assertEqual(lease_file(), own)
            self.assertEqual(lease_start(lease_file()), datetime.datetime(2026, 9, 23, 14, 17, 10, tzinfo=UTC))
            os.environ["AIQT_LEASE_FILE"] = ""
            self.assertIsNone(lease_file())  # a set-but-empty AIQT_ value still beats ORCH_: no lease

        def test_fifo_transcript_and_lease_do_not_block(self):
            fifo = os.path.join(self.tmp, "fifo")
            os.mkfifo(fifo)
            out = in_subprocess("(m.lease_start(%r), m.evaluate({'transcript_path':%r,'last_assistant_message':"
                                "'[2099-01-01T00:00Z] x'}, datetime.datetime.now(UTC), None, %r) is not None)"
                                % (fifo, fifo, self.sdir), timeout=5)
            self.assertEqual(out, "(None, True)")

        def test_inactive_lease_never_reads_history(self):
            lease = os.path.join(self.tmp, "lease.md")
            with open(lease, "w") as f:
                f.write("Active-session: none\n## History\nActive-session: sess-20200101T000000Z\n")
            self.assertIsNone(lease_start(lease))

        def test_huge_single_record_is_fast(self):
            with open(self.tr, "w") as f:
                f.write(json.dumps(self.user("go")) + "\n")
                f.write(json.dumps({"type": "user", "message": {"content": [
                    {"type": "tool_result", "content": "x" * (32 << 20)}]}}) + "\n")
                f.write(json.dumps(self.asst("[2099-01-01T00:00Z] x")) + "\n")
            t0 = time.monotonic()
            r = evaluate({"transcript_path": self.tr}, self.now, self.start, self.sdir)
            self.assertLess(time.monotonic() - t0, 2.0)
            self.assertIsNotNone(r)

        def test_reverse_records_exact_with_offsets(self):
            lines = [b"a" * n for n in (0, 1, CHUNK - 1, CHUNK, CHUNK + 1, 3 * CHUNK + 7, 5)]
            data = b"\n".join(lines)
            with open(self.tr, "wb") as f:
                f.write(data)
            fd = os.open(self.tr, os.O_RDONLY)
            try:
                got = list(_reverse_records(fd, len(data)))
                floor = len(b"\n".join(lines[:4])) + 1
                tail = list(_reverse_records(fd, len(data), floor))
            finally:
                os.close(fd)
            self.assertEqual([r for _, r in got], [ln for ln in reversed(lines) if ln])
            for off, rec in got:
                self.assertEqual(data[off:off + len(rec)], rec)
            self.assertEqual([r for _, r in tail], [ln for ln in reversed(lines[4:]) if ln])

        # -- round 4 --
        def test_r4_blocked_text_already_in_transcript_creates_no_exception(self):
            # finding (codex r3 M1): the blocked hash was a persistent exemption across a new user boundary
            bad_text = "[2026-09-23T22:25Z] done."
            self.assertIsNotNone(self.ev([self.user("go"), self.asst(bad_text)], last_assistant_message=bad_text))
            self.write([self.block_entry(), self.asst(f"{lstamp(self.now)} fixed"), self.user("next task"),
                        self.asst(bad_text), self.asst("Done.")], "a")
            self.assertIn("280 min AHEAD", self.ev(last_assistant_message="Done."))
            # the same within one turn (no user boundary): an in-transcript blocked text is no exception
            self.assertIsNotNone(self.ev([self.user("go"), self.asst(bad_text)], last_assistant_message=bad_text))
            self.write([self.block_entry(), self.asst(bad_text), self.asst("Done.")], "a")
            self.assertIn("280 min AHEAD", self.ev(last_assistant_message="Done."))

        def test_r4_pending_consumed_once(self):
            bad_text = "[2026-09-23T20:00Z] wrong"
            self.assertIsNotNone(self.ev([self.user("go")], last_assistant_message=bad_text))  # lagging: pending
            self.write([self.asst(bad_text), self.block_entry(), self.asst("fixed")], "a")
            self.assertIsNone(self.ev(last_assistant_message="fixed"))  # consumed here
            self.write([self.asst(bad_text), self.asst("again fine")], "a")
            self.assertIn("135 min AHEAD", self.ev(last_assistant_message="again fine"))

        def test_r4_pending_dropped_at_genuine_user(self):
            bad_text = "[2026-09-23T20:00Z] wrong"
            self.assertIsNotNone(self.ev([self.user("go")], last_assistant_message=bad_text))
            self.write([self.user("new question"), self.asst(bad_text), self.asst("ok")], "a")
            self.assertIn("135 min AHEAD", self.ev(last_assistant_message="ok"))

        def test_r4_truncation_resets_boundary_and_pending(self):
            # finding (codex r3 M1): a shrunk transcript kept the hash exemption
            bad_text = "[2026-09-23T22:25Z] done."
            self.assertIsNotNone(self.ev([self.user("go"), self.asst("pad " * 300)], last_assistant_message=bad_text))
            with open(self.tr, "w") as f:
                f.write(json.dumps(self.user("go")) + "\n" + json.dumps(self.asst(bad_text)) + "\n")
            self.assertIn("280 min AHEAD", self.ev(last_assistant_message="Done."))

        def test_r4_rewritten_prefix_resets_boundary(self):
            self.assertIsNotNone(self.ev([self.user("go"), self.asst("[2026-09-23T20:00Z] a")],
                                         last_assistant_message="[2026-09-23T20:00Z] a"))
            with open(self.tr, "w") as f:  # same path, different and LONGER content: the tail hash differs
                f.write(json.dumps(self.user("go")) + "\n" + json.dumps(self.asst("[2026-09-23T20:30Z] " + "b" * 400))
                        + "\n" + json.dumps(self.asst("ok")) + "\n")
            self.assertIn("165 min AHEAD", self.ev(last_assistant_message="ok"))

        def test_r4_unfinished_trailing_record_rechecked(self):
            # finding (codex r3 m4): the sampled size acknowledged a half-written record unchecked
            rec = json.dumps(self.asst("[2026-09-23T23:00Z] DIFFERENT"))
            self.write([self.user("go")])
            with open(self.tr, "a") as f:
                f.write(rec[:40])
            self.assertIsNotNone(self.ev(last_assistant_message="[2026-09-23T22:25Z] fabricated"))
            with open(self.tr, "a") as f:
                f.write(rec[40:] + "\n" + json.dumps(self.asst(f"{lstamp(self.now)} ok")) + "\n")
            self.assertIn("315 min AHEAD", self.ev(last_assistant_message=f"{lstamp(self.now)} ok"))

        def test_r4_parseable_trailing_record_is_complete(self):
            self.write([self.user("go")])
            with open(self.tr, "a") as f:
                f.write(json.dumps(self.asst("[2026-09-23T23:00Z] x")))  # no newline yet, but whole
            self.assertIsNotNone(self.ev(last_assistant_message="[2026-09-23T22:25Z] y"))
            with open(self.tr, "a") as f:
                f.write("\n" + json.dumps(self.asst(f"{lstamp(self.now)} ok")) + "\n")
            self.assertIsNone(self.ev(last_assistant_message=f"{lstamp(self.now)} ok"))

        def test_r4_footer_last_occurrence_outside_code(self):
            # findings (codex r3 M3, gemini r3 M1, claude r3 m1): a footer not on the last line was unchecked
            for txt in ("Status.\nSession elapsed 08:07\nWORKER_STATUS: COMPLETE",
                        f"{ustamp(self.now)} ok\nSession elapsed 08:07\n---", "Session elapsed 99:00\n> done",
                        "Session elapsed 08:07\n```\nlog\n```"):
                self.assertIn("true elapsed when written was 03:27", self.final(txt), repr(txt))
            # disclosed: an unquoted worker footer pasted after the real one is the one checked
            self.assertIn("05:12", self.final("x\nSession elapsed 03:27\nWorker said: Session elapsed 05:12"))

        def test_r4_one_line_corrections_pass(self):
            # finding (codex r3 m): truthful corrections re-blocked
            self.assertIsNone(self.final("Correction: I wrote Session elapsed 08:07. Correct is Session elapsed 03:27."))
            self.assertIsNone(self.final("Correction: I previously wrote `[2026-09-23T22:25Z]`, which was wrong. "
                                         "Correct: [2026-09-23T17:45Z]."))
            self.assertIn("AHEAD", self.final("Correction: I previously wrote [2026-09-23T22:25Z], which was wrong."))

        def test_r4_keyword_must_immediately_precede(self):
            # finding (claude r3 m2): an everyday or distant keyword exempted a composed future stamp
            now = lstamp(self.now)
            for txt in (f"{now} pushed to target branch; ran at 2026-09-23 22:25 EDT",
                        f"{now} the next thing I did was finish the long build at 2026-09-23T22:25Z",
                        f"{now} due to load we finished at 2026-09-23T22:25Z"):
                self.assertIn("AHEAD", self.final(txt), txt)
            for txt in (f"{now} next run at 2026-09-24T01:00Z", f"{now} due 2026-09-24T01:00Z",
                        f"{now} deadline: **[2026-09-24T01:00Z]**", f"{now} scheduled for 2026-09-24T01:00Z",
                        f"{now} held until 2026-09-24T01:00Z", f"{now} not before 2026-09-24T01:00Z",
                        f"{now} done by 2026-09-24T01:00Z", f"{now} target date: 2026-09-24T01:00Z",
                        f"{now} Target: 2026-09-24T01:00Z", f"{now} it expires on 2026-09-24T01:00Z"):
                self.assertIsNone(self.final(txt), txt)

        def test_r4_future_fact_false_positive_and_remedy(self):
            # finding (claude r3 M): AHEAD on every zoned claim blocks a future fact; disclosed, with a remedy
            # round 24 (item 2): "valid" and "through" are schedule/validity words now, so the original example
            # passes; a future fact with no such word before it still blocks, with the same remedy text
            self.assertIsNone(self.final(f"{lstamp(self.now)} The TLS cert is valid through 2027-01-01T00:00:00Z."))
            r = self.final(f"{lstamp(self.now)} The eclipse occurs 2027-08-02T18:00:00Z.")
            self.assertIn("AHEAD", r)
            self.assertIn("`code span`", r)
            self.assertIn("immediately precedes", r)
            self.assertIsNone(self.final(f"{lstamp(self.now)} The TLS cert is valid through `2027-01-01T00:00:00Z`."))
            self.assertIsNone(self.final(f"{lstamp(self.now)} The TLS cert expires 2027-01-01T00:00:00Z."))

        def test_r4_repeated_stamps_linear(self):
            # finding (codex r3 m5): each claim sliced the growing line prefix (quadratic)
            line = "2026-09-23T17:45Z " * 100000
            t0 = time.monotonic()
            self.assertEqual(check_message(line, to_us(self.now), None, "x", BEHIND_US), [])
            self.assertLess(time.monotonic() - t0, 1.0)
            t0 = time.monotonic()
            r = check_message("next " + "2099-01-01T00:00Z x " * 50000, to_us(self.now), None, "x", BEHIND_US)
            self.assertLess(time.monotonic() - t0, 1.0)
            self.assertEqual(len(r), 1)  # deduplicated

        def test_r5_many_distinct_violations_linear_and_bounded(self):
            # finding (codex r4): add() scanned the growing violation list (quadratic in distinct violations)
            lits = [f"2099-{1 + i % 12:02d}-{1 + (i // 12) % 28:02d}T{(i // 336) % 24:02d}:{(i // 8064) % 60:02d}Z"
                    for i in range(40000)]
            self.assertEqual(len(set(lits)), 40000)
            t0 = time.monotonic()
            r = self.final(" ".join(lits))
            self.assertLess(time.monotonic() - t0, 1.0)
            self.assertEqual(r.count(" in your final message: "), MAX_REPORTED)
            self.assertIn(f"... and {40000 - MAX_REPORTED} more distinct violation(s) not listed", r)

        def test_r4_threat_model_stated(self):
            self.assertIn("THREAT MODEL", __doc__.split("\n\n")[1])

        # -- round 13 (peer report): lease start sources and the (session: Xh Ym) footer --
        def lease(self, text):
            p = os.path.join(self.tmp, "lease.md")
            with open(p, "w") as f:
                f.write(text)
            return p

        def test_r13_lease_field_forms(self):
            want = datetime.datetime(2026, 9, 23, 14, 17, 10, tzinfo=UTC)
            for line in ("Active-session: sess-20260923T141710Z", "**Active-session:** sess-20260923T141710Z",
                         "- Active-session: sess-20260923T141710Z", "* Active-session: sess-20260923T141710Z",
                         "  - **Active-session:**   sess-20260923T141710Z  ", "\t**Active-session:**sess-20260923T141710Z"):
                self.assertEqual(lease_start(self.lease(f"# Lease\n\n{line}\n")), want, line)
            # an unrecognized form is not a field: the first RECOGNIZED field is the one read
            for line in ("+ Active-session: sess-20200101T000000Z", "**Active-session: sess-20200101T000000Z",
                         "Active-session** sess-20200101T000000Z", "active-session: sess-20200101T000000Z"):
                self.assertEqual(lease_start(self.lease(f"{line}\nActive-session: sess-20260923T141710Z\n")), want,
                                 line)
            # still only the FIRST recognized field, whatever its form
            self.assertIsNone(lease_start(self.lease("**Active-session:** none\n- Active-session: sess-20260923T141710Z\n")))

        def test_r13_date_only_id_and_session_start(self):
            want = datetime.datetime(2026, 9, 23, 14, 18, 0, tzinfo=UTC)
            ids = "# Lease\n\n**Active-session:** sess-2026-09-23-opus55-r1\n\n**Status:** active\n"
            self.assertIsNone(lease_start(self.lease(ids)))  # the round-12 symptom, with no other source
            for field in ("**Session-start-UTC:** 2026-09-23T14:18:00Z", "Session-start-UTC: 20260923T141800Z",
                          "- Session-start-UTC: 2026-09-23T14:18:00Z"):
                self.assertEqual(lease_start(self.lease(ids + "\n" + field + "\n")), want, field)
                self.assertEqual(lease_start(self.lease(field + "\n" + ids)), want, field)  # above the id too
            # a timed id beats Session-start-UTC; the FIRST Session-start-UTC field only
            self.assertEqual(lease_start(self.lease("Active-session: S88-20260923T141710Z\n"
                                                    "Session-start-UTC: 2026-09-23T10:00:00Z\n")),
                             datetime.datetime(2026, 9, 23, 14, 17, 10, tzinfo=UTC))
            self.assertEqual(lease_start(self.lease(ids + "Session-start-UTC: 2026-09-23T14:18:00Z\n"
                                                    "Session-start-UTC: 2026-09-23T01:00:00Z\n")), want)
            # a Session-start-UTC below the next heading (a history section) is not this lease's start
            self.assertIsNone(lease_start(self.lease(ids + "\n## History\nSession-start-UTC: 2026-09-20T01:00:00Z\n")))
            # malformed values are not a start (no zone, a zone offset, an impossible date)
            for bad in ("2026-09-23T14:18:00", "2026-09-23 14:18:00Z", "2026-09-23T14:18:00+00:00",
                        "2026-13-23T14:18:00Z", "soon"):
                self.assertIsNone(lease_start(self.lease(ids + f"Session-start-UTC: {bad}\n")), bad)

        def test_r13_transcript_fallback(self):
            ids = "**Active-session:** sess-2026-09-23-opus55-r1\n"
            t1, t2 = self.start, self.start + 5 * MIN
            self.write([{"type": "summary", "summary": "s"}, "{broken", self.user("later", t2),
                        {"type": "system", "timestamp": "not a time"}, self.user("go", t1),
                        {"type": "x", "message": {"timestamp": "2000-01-01T00:00:00Z"}}])  # nested: not top-level
            self.assertEqual(lease_start(self.lease(ids), self.tr), t1)  # the EARLIEST top-level timestamp
            self.assertEqual(transcript_start(self.tr), t1)
            # Session-start-UTC outranks the transcript; a malformed one falls through to it
            self.assertEqual(lease_start(self.lease(ids + "Session-start-UTC: 2026-09-23T15:00:00Z\n"), self.tr),
                             datetime.datetime(2026, 9, 23, 15, 0, tzinfo=UTC))
            self.assertEqual(lease_start(self.lease(ids + "Session-start-UTC: later\n"), self.tr), t1)
            # a timed id never consults the transcript
            self.assertEqual(lease_start(self.lease("Active-session: sess-20260923T160000Z\n"), self.tr),
                             datetime.datetime(2026, 9, 23, 16, 0, tzinfo=UTC))
            # bounded: a record the 256 KiB bound cuts is not parsed, so a huge first record hides its timestamp
            self.write([self.user("x" * TRANSCRIPT_PREFIX_BYTES, t1), self.user("go", t2)])
            self.assertIsNone(transcript_start(self.tr))
            self.write([self.user("go", t2)])
            with open(self.tr, "a") as f:
                f.write(json.dumps(self.user("partial", t1)))  # complete but newline-less trailing record
            self.assertEqual(transcript_start(self.tr), t1)
            for bad in (None, "", os.path.join(self.tmp, "absent.jsonl"), self.tmp, 42):
                self.assertIsNone(transcript_start(bad), bad)
            # end to end through main(): the id-only lease plus the payload transcript gives the footer a start
            # main() reads the real clock, so the transcript start is taken relative to it
            self.write([self.user("go", datetime.datetime.now(UTC) - datetime.timedelta(hours=2, minutes=5))])
            p = json.dumps({"transcript_path": self.tr, "last_assistant_message": "done (session: 9h 0m)"})
            rc, out = run_main(p, {"AIQT_LEASE_FILE": self.lease(ids)})
            r = json.loads(out)["reason"]
            self.assertIn("\"(session: 9h 0m)\" (last elapsed footer in your final message)", r)
            self.assertIn("session elapsed 02:0", r)

        def test_r13_fifo_transcript_does_not_block(self):
            fifo = os.path.join(self.tmp, "tfifo")
            os.mkfifo(fifo)
            lease = self.lease("**Active-session:** sess-2026-09-23-opus55-r1\n")
            self.assertEqual(in_subprocess("(m.transcript_start(%r), m.lease_start(%r, %r))" % (fifo, lease, fifo),
                                           timeout=5), "(None, None)")

        def test_r13_inactive_lease_stays_unknown(self):
            self.write([self.user("go", self.start)])
            for head in ("Active-session: none", "**Active-session:** none", "- Active-session: NONE",
                         "Active-session:", "**Active-session:**   "):
                text = f"{head}\nSession-start-UTC: 2026-09-23T14:18:00Z\nActive-session: sess-20200101T000000Z\n"
                self.assertIsNone(lease_start(self.lease(text), self.tr), head)
            # no Active-session field at all: unknown, and the transcript is not consulted
            self.assertIsNone(lease_start(self.lease("# notes\nSession-start-UTC: 2026-09-23T14:18:00Z\n"), self.tr))
            self.assertIsNone(lease_start(None, self.tr))
            self.assertIsNone(lease_start(os.path.join(self.tmp, "absent.md"), self.tr))

        def test_r13_session_footer_checked(self):
            # start 14:18Z, now 17:45Z: true elapsed 03:27
            for ok in ("done (session: 3h 27m)", "done (Session: 3h27m)", "done (session:3h 29m )", "(session: 207m)",
                       "x\n(session: 3h 27m)\n---", "x (session: 3h 27m)\n> (session: 9h 0m)",
                       "x (session: 3h 27m) `(session: 9h 0m)`", "x (session: 3h 27m)\n```\n(session: 9h 0m)\n```"):
                self.assertIsNone(self.final(ok), ok)
            r = self.final("done (session: 2h 5m)")
            self.assertIn("\"(session: 2h 5m)\" (last elapsed footer in your final message): true elapsed when "
                          "written was 03:27 (82 min BEHIND)", r)
            self.assertIn("3 min AHEAD", self.final("done (session: 3h 30m)"))
            self.assertIn("33 min AHEAD", self.final("done (session: 4h)"))
            self.assertIn("147 min BEHIND", self.final("done (session: 60m)"))
            # the same tolerances as the HH:MM form: BEHIND 10 min for an earlier message, 30 for the final
            ref, st = to_us(self.now), to_us(self.start)
            self.assertEqual(check_message("(session: 3h 17m)", ref, st, "x", BEHIND_US), [])
            self.assertIn("11 min BEHIND", check_message("(session: 3h 16m)", ref, st, "x", BEHIND_US)[0])
            self.assertIn("27 min BEHIND", check_message("(session: 3h)", ref, st, "x", BEHIND_US)[0])
            self.assertIsNone(self.final("done (session: 3h)"))  # 27 min BEHIND is inside the final 30
            # not footers: other shapes are not recognized (disclosed)
            for other in ("(session 9h 0m)", "(session: 9h 0m, 1 compaction)", "(9h 0m session)", "(session: 9x)"):
                self.assertIsNone(self.final("done " + other), other)
            self.assertIsNone(evaluate({"last_assistant_message": "(session: 99h)"}, self.now, None, self.sdir))

        def test_r13_last_footer_of_either_form_wins(self):
            for ok in ("Session elapsed 09:00\nlater (session: 3h 27m)", "(session: 9h 0m)\nSession elapsed 03:27",
                       "Session elapsed 09:00 then (session: 3h 27m)", "(session: 9h 0m) then Session elapsed 03:27"):
                self.assertIsNone(self.final(ok), ok)
            r = self.final("Session elapsed 03:27\nlater (session: 9h 0m)")
            self.assertIn("\"(session: 9h 0m)\" (last elapsed footer", r)
            self.assertNotIn("\"Session elapsed 03:27\"", r)
            r = self.final("(session: 3h 27m) and Session elapsed 09:00")
            self.assertIn("\"Session elapsed 09:00\" (last elapsed footer", r)
            self.assertEqual(r.count("(last elapsed footer"), 1)

        def test_r13_session_footer_scan_linear(self):
            out = in_subprocess("m.check_message('(session: '*100000+'\\n'+'(session: 1'*100000, 0, 0, 'x', 0)",
                                timeout=5)
            self.assertEqual(out, "[]")

        def test_r13_shared_lease_code_identical_to_clock_inject(self):
            sib = _sibling_or_skip("clock-inject.py")
            spec = importlib.util.spec_from_file_location("ci_sibling", sib)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for name in ("read_regular", "lease_file", "_utc_field", "transcript_start", "lease_start", "_is_worker",
                     "_cfg", "_sibling_or_skip"):
                self.assertEqual(inspect.getsource(getattr(mod, name)), inspect.getsource(globals()[name]), name)
            for name in ("_SESS_RE", "_LEASE_FIELD_RE", "_START_VALUE_RE", "_HEADING_RE"):
                self.assertEqual((getattr(mod, name).pattern, getattr(mod, name).flags),
                                 (globals()[name].pattern, globals()[name].flags), name)
            self.assertEqual((mod.LEASE_MAX_BYTES, mod.TRANSCRIPT_PREFIX_BYTES),
                             (LEASE_MAX_BYTES, TRANSCRIPT_PREFIX_BYTES))

        def test_shared_grammar_identical_to_sibling(self):
            sib = _sibling_or_skip("future-stamp-write.py")
            spec = importlib.util.spec_from_file_location("fsw_sibling", sib)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self.assertEqual(mod.SCHED_KEYWORDS, SCHED_KEYWORDS)
            self.assertEqual(mod.SCHED_GAP_TOKENS, SCHED_GAP_TOKENS)
            self.assertEqual((mod.TIME_GRAMMAR, mod.ZONE_GRAMMAR), (TIME_GRAMMAR, ZONE_GRAMMAR))
            self.assertEqual((mod._SCHED_RE.pattern, mod._SCHED_RE.flags), (_SCHED_RE.pattern, _SCHED_RE.flags))
            self.assertEqual(mod.SCHED_STEMS, SCHED_STEMS)
            self.assertEqual((mod._TOKEN_RE.pattern, mod._WORDCH_RE.pattern), (_TOKEN_RE.pattern, _WORDCH_RE.pattern))
            self.assertEqual(inspect.getsource(mod.sched_exempter), inspect.getsource(sched_exempter))
            # round 13: the code and quote exemption helpers are shared verbatim too
            for name in ("_code_lines", "_code_spans", "_in_spans"):
                self.assertEqual(inspect.getsource(getattr(mod, name)), inspect.getsource(globals()[name]), name)
            for name in ("_BTICK_RE", "_FENCE_RE"):
                self.assertEqual((getattr(mod, name).pattern, getattr(mod, name).flags),
                                 (globals()[name].pattern, globals()[name].flags), name)
            # the configuration and kill-switch helpers are shared verbatim across the three hooks
            for name in ("_cfg", "_is_worker", "_sibling_or_skip"):
                self.assertEqual(inspect.getsource(getattr(mod, name)), inspect.getsource(globals()[name]), name)


        # -- round 24 (field validation of round 12) --
        def test_r24_item1_cap_from_own_counter_five_calls(self):
            # item 1 (BLOCKING): with stop_hook_active, an unreadable transcript_path, and a future-stamped final
            # message, five consecutive Stops each blocked (the transcript count was 0 and never capped). The
            # hook's own recovery-state counter now caps it: the first may block, blocking stops within the cap
            # round 26: the loop starts, as in the host, with a Stop outside it (whose block writes the state); an
            # absent state under stop_hook_active is now an unknown count and fails open
            missing = os.path.join(self.tmp, "missing-transcript.jsonl")
            lease = {"AIQT_LEASE_FILE": os.path.join(self.tmp, "no-lease.md")}  # never the host's real lease
            outs = []
            for active in (False, True, True, True, True):
                p = json.dumps({"hook_event_name": "Stop", "stop_hook_active": active, "transcript_path": missing,
                                "last_assistant_message": "[2099-01-01T00:00Z] done"})
                outs.append(json.loads(run_main(p, lease)[1] or "{}"))
            kinds = ["block" if o.get("decision") == "block" else "warn" if "systemMessage" in o else "silent"
                     for o in outs]
            self.assertEqual(kinds[0], "block", kinds)
            self.assertLessEqual(kinds.count("block"), BLOCK_CAP, kinds)
            self.assertEqual(kinds[BLOCK_CAP:], ["warn"] * (5 - BLOCK_CAP), kinds)
            self.assertIn("block cap hit", outs[-1]["systemMessage"])
            # a Stop outside the corrective loop starts the count again, and so blocks
            p0 = json.dumps({"hook_event_name": "Stop", "stop_hook_active": False, "transcript_path": missing,
                             "last_assistant_message": "[2099-01-01T00:00Z] done"})
            self.assertEqual(json.loads(run_main(p0, lease)[1]).get("decision"), "block")

        def test_r24_item1_unestablishable_count_fails_open(self):
            # item 1: no usable recovery state (a non-private dir) AND a count the transcript cannot establish
            # (unreadable, or a counting gap): FAIL OPEN at once, with a one-line warning
            os.mkdir(self.sdir, 0o755)
            os.chmod(self.sdir, 0o755)
            gap = [self.user("go")] + [self.asst("working")] * (CAP_GAP_ASSISTANT + 2)
            self.write(gap)
            for tp in (os.path.join(self.tmp, "missing.jsonl"), self.tr):
                notes = []
                r = evaluate({"transcript_path": tp, "stop_hook_active": True,
                              "last_assistant_message": "[2099-01-01T00:00Z] x"}, self.now, self.start, self.sdir, notes)
                self.assertIsNone(r, tp)
                self.assertEqual(len(notes), 1, tp)
                self.assertNotIn("\n", notes[0])
                self.assertIn("could not establish its consecutive block count", notes[0])
                self.assertIn("UNRESOLVED", notes[0])
            # outside the corrective loop nothing changes: the violation blocks
            self.assertTrue(evaluate({"transcript_path": self.tr, "last_assistant_message": "[2099-01-01T00:00Z] x"},
                                     self.now, self.start, self.sdir).startswith(BLOCK_PREFIX))
            self.assertEqual(block_cycles(self.tr), (0, False))
            self.assertEqual(block_cycles(os.path.join(self.tmp, "missing.jsonl")), (0, False))
            self.write(self.cycles(1))
            self.assertEqual(block_cycles(self.tr), (1, True))  # ended by the genuine user message
            # a block that cannot be RECORDED (the state dir's parent is missing, so the write fails) with an
            # unestablished transcript count would leave the next Stop unable to cap: it fails open at once
            nodir = os.path.join(self.tmp, "absent-parent", "state")
            notes = []
            self.assertIsNone(evaluate({"transcript_path": os.path.join(self.tmp, "missing.jsonl"),
                                        "stop_hook_active": True, "last_assistant_message": "[2099-01-01T00:00Z] x"},
                                       self.now, self.start, nodir, notes))
            self.assertIn("could not establish", notes[0])

        def test_r24_item2_historical_and_validity_prose_passes(self):
            # item 2 (BLOCKING): truthful prose blocked: a leading historical date line (read as the header and
            # checked for BEHIND) and a future expiry after "valid through" (no keyword then)
            for ok in ("2020-01-01T00:00Z service launched", "The certificate is valid through 2099-01-01T00:00Z.",
                       "Token valid until 2099-01-01T00:00Z", "- 2020-01-01T00:00Z launch\n- 2021-06-01T00:00Z v2",
                       "Cert valid: 2099-01-01T00:00Z", "Maintenance runs through 2099-01-01T00:00Z."):
                self.assertIsNone(self.final(ok), ok)
            # a claim of the CURRENT time is still checked: a stale bracketed header, a wrong footer, and any
            # future stamp with no schedule or validity word before it (line-leading, or mid-line)
            self.assertIn("35 min BEHIND", self.final(f"{ustamp(self.now - 35 * MIN)} status"))
            self.assertIn("true elapsed", self.final("x\nSession elapsed 09:00"))
            self.assertIn("AHEAD", self.final("2099-01-01T00:00Z service launched"))
            self.assertIn("AHEAD", self.final("launched 2099-01-01T00:00Z"))
            self.assertIn("AHEAD", self.final("[2099-01-01T00:00Z] valid through"))  # a keyword never exempts a status stamp
            self.assertIn("through", SCHED_KEYWORDS)
            self.assertIn("valid", SCHED_KEYWORDS)

        def test_r24_worker_skip_warns_and_output_error_fails_open(self):
            # residuals: a worker marker silenced the hook with no warning, and an output error escaped the
            # fail-open handlers (the exit flush failed with status 120)
            old_err, sys.stderr = sys.stderr, io.StringIO()
            try:
                rc, out = run_main(json.dumps({"last_assistant_message": "[2099-01-01T00:00Z] x"}),
                                   {"AIQT_HOOKS_WORKER": "1"})
                err = sys.stderr.getvalue()
            finally:
                sys.stderr = old_err
            self.assertEqual((rc, out), (0, ""))
            self.assertEqual(err.count("\n"), 1)
            self.assertIn("skipped, worker marker present", err)
            if not os.path.exists("/dev/full"):
                self.skipTest("/dev/full absent")
            env = {k: v for k, v in os.environ.items() if k not in ("AIQT_HOOKS_WORKER", "ORCH_WORKER",
                                                               "ORCH_VERIFY_OWNER")}
            env["AIQT_LEASE_FILE"] = os.path.join(self.tmp, "no-lease.md")  # never the host's real lease
            with open("/dev/full", "w") as full:
                p = subprocess.run([sys.executable, "-I", "-B", os.path.abspath(__file__)], stdout=full,
                                   stderr=subprocess.PIPE, text=True, env=env, timeout=30,
                                   input=json.dumps({"last_assistant_message": "[2099-01-01T00:00Z] x"}))
            self.assertEqual(p.returncode, 0, p.stderr)

        # -- generic port: bytes payload, a failed output rescue, fail-open before evaluation --
        def test_payload_read_as_bytes(self):
            # a UTF-8 payload behind a text layer that cannot decode it: only a bytes read parses it
            msg = "[2099-01-01T00:00Z] x " + chr(0xe9) + chr(0x2603)
            raw = json.dumps({"last_assistant_message": msg}, ensure_ascii=False).encode("utf-8")
            rc, out = run_main(io.TextIOWrapper(io.BytesIO(raw), encoding="ascii"))
            self.assertEqual((rc, json.loads(out)["decision"]), (0, "block"))
            bad = io.TextIOWrapper(io.BytesIO(b"\xff\xfe{"), encoding="ascii")  # undecodable: fails open
            self.assertEqual(run_main(bad), (0, ""))

        def test_emit_rescue_failure_exits_0(self):
            code = ("import importlib.util as u, io;s=u.spec_from_file_location('m',%r);m=u.module_from_spec(s);"
                    "s.loader.exec_module(m);print('before', flush=True);b=io.StringIO();b.close();"
                    "m._emit_line('x', b);print('after', flush=True)" % os.path.abspath(__file__))
            r = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=30)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "before\n", ""))

        def test_fail_open_before_evaluation(self):
            class Unreadable:
                def read(self, *a):
                    raise OSError("unreadable stdin")
            for stdin in (Unreadable(), "[1, 2]", '"text"', "null", ""):
                self.assertEqual(run_main(stdin), (0, ""), stdin)
            p = json.dumps({"last_assistant_message": "[2099-01-01T00:00Z] x"})
            for argv in (None, 7):  # argv that cannot be inspected fails open: exit 0, silent, nothing evaluated
                self.assertEqual(run_main(p, argv=argv), (0, ""), argv)
            rc, out = run_main(p, argv=[None, 3])  # inspectable, merely not --self-test: evaluated as a hook
            self.assertEqual((rc, json.loads(out)["decision"]), (0, "block"))

        def test_r24a_unusable_counter_fails_open_on_first_call(self):
            # follow-up (A): no usable state, stop_hook_active, and a transcript whose count reads 0 (established,
            # ended by a genuine user entry) blocked before; the transcript count is never the only brake
            os.mkdir(self.sdir, 0o755)
            os.chmod(self.sdir, 0o755)
            self.write([self.user("go"), self.asst("[2099-01-01T00:00Z] x")])
            notes = []
            r = evaluate({"transcript_path": self.tr, "stop_hook_active": True,
                          "last_assistant_message": "[2099-01-01T00:00Z] x"}, self.now, self.start, self.sdir, notes)
            self.assertIsNone(r)
            self.assertEqual(block_cycles(self.tr), (0, True))  # the transcript count read an established 0
            self.assertEqual(len(notes), 1)
            self.assertNotIn("\n", notes[0])
            self.assertIn("could not establish", notes[0])
            # no transcript_path at all (nothing to key the counter): also fails open under the loop
            notes = []
            self.assertIsNone(evaluate({"stop_hook_active": True, "last_assistant_message": "[2099-01-01T00:00Z] x"},
                                       self.now, self.start, self.sdir, notes))
            self.assertTrue(notes)
            # a USABLE state keeps the cap behaviour: block, block, block, then capped allow
            sdir = os.path.join(self.tmp, "ok-state")
            kinds = []
            for active in (False, True, True, True):
                notes = []
                r = evaluate({"transcript_path": self.tr, "stop_hook_active": active,
                              "last_assistant_message": "[2099-01-01T00:00Z] x"}, self.now, self.start, sdir, notes)
                kinds.append("block" if r else "cap" if notes and "block cap hit" in notes[0] else "other")
            self.assertEqual(kinds, ["block", "block", "block", "cap"])

        def test_r24b_keywords_match_whole_words(self):
            # follow-up (B): a keyword was a PREFIX, so "validated", "throughput", "duet", or "etag" exempted a
            # fabricated time after it
            for bad in ("validated 2099-01-01T00:00Z", "throughput 2099-01-01T00:00Z", "duet 2099-01-01T00:00Z",
                        "etag 2099-01-01T00:00Z", "nextcloud 2099-01-01T00:00Z", "untilx 2099-01-01T00:00Z",
                        "plannedness 2099-01-01T00:00Z", "scheduledx 2099-01-01T00:00Z"):
                self.assertIn("AHEAD", self.final(bad) or "", bad)
            for ok in ("valid 2099-01-01T00:00Z", "through 2099-01-01T00:00Z", "due 2099-01-01T00:00Z",
                       "ETA 2099-01-01T00:00Z", "next_run: 2099-01-01T00:00Z", "deadlines: 2099-01-01T00:00Z",
                       "expires 2099-01-01T00:00Z", "expiry 2099-01-01T00:00Z", "expiration 2099-01-01T00:00Z",
                       "Valid-through 2099-01-01T00:00Z", "planned for 2099-01-01T00:00Z"):
                self.assertIsNone(self.final(ok), ok)

        def test_r24_disclosures(self):
            doc = " ".join(__doc__.split())
            for s in ("bracketed console form", "compared as local wall time", "valid", "through",
                      "could not establish", "own recovery-state counter"):
                self.assertIn(s, doc)

        # -- round 25 (claude-fable-5 expensive QA of round 24) --
        def test_r25_item1_deletion_residual_closed(self):
            # finding 1 (LOW, round 25) disclosed an externally deleted state file as leaving the cap unreachable;
            # round 26 closes it (an absent state under stop_hook_active is an unknown count and fails open)
            doc = " ".join(__doc__.split())
            for s in ("the block cap may never be reached", "bounded only by the host's own stop_hook_active handling"):
                self.assertNotIn(s, doc)
            for s in ("never deletes it", "an UNKNOWN count (round 26)", "closes the round-25 residual"):
                self.assertIn(s, doc)
            # with a `system`-record feedback shape (no transcript marker) and the state deleted, the loop fails open
            self.write([self.user("go"), self.asst("x"),
                        {"type": "system", "content": "Stop hook feedback: " + BLOCK_PREFIX}, self.asst("y")])
            self.assertEqual(block_cycles(self.tr), (0, True))
            extra = {}
            self.assertEqual(load_state(self.sdir, self.tr, extra), (0, None, [], True))
            self.assertEqual(extra, {"blocks": 0, "absent": True})
            notes = []
            self.assertIsNone(evaluate({"transcript_path": self.tr, "stop_hook_active": True,
                                        "last_assistant_message": "[2099-01-01T00:00Z] y"},
                                       self.now, self.start, self.sdir, notes))
            self.assertIn("could not establish", notes[0])

        # -- round 26 (codex gpt-6-astra high QA of round 25) --
        def loop7(self, cond, sdir=None):
            """Seven violating Stops (one outside the corrective loop, then six under stop_hook_active) with
            `cond` applied before each corrective Stop; returns the outcome kinds. The hook never writes the
            transcript: its bytes are compared around every Stop."""
            sdir = sdir or os.path.join(tempfile.mkdtemp(dir=self.tmp), "state")
            bad = "[2026-09-23T20:00Z] still wrong"
            self.write([self.user("go"), self.asst(bad)])
            real_load, kinds = load_state, []
            for k in range(7):
                if k:
                    if cond == "rewrite":  # (a) a rewritten transcript resets the boundary (t["reset"])
                        with open(self.tr, "w") as f:
                            f.write(json.dumps(self.user("go" + "!" * k)) + "\n" + json.dumps(self.asst(bad)) + "\n")
                    elif cond == "hookfeedback":  # (b) a synthetic user-role entry read as a genuine user message
                        self.write([self.user("Hook feedback: " + BLOCK_PREFIX), self.asst(bad)], "a")
                    elif cond in ("delete", "delete-once") and (cond == "delete" or k == 2):  # (c)
                        for p in self.state_files(sdir):  # round 30: in the per-transcript subdirectory
                            os.unlink(p)
                    elif cond in ("dirrace",):  # (d) the dir removed after the evaluation opened (and locked) it
                        calls = []

                        def racing(*a, calls=calls, **kw):
                            # round 29: the evaluation's first load_state reads through the locked descriptor;
                            # the directory is removed just before it, so the load and the save meet a removed
                            # directory (the save cannot create a file there) and the next Stop recreates it
                            calls.append(a)
                            if len(calls) == 1:
                                shutil.rmtree(sdir)
                            return real_load(*a, **kw)
                        globals()["load_state"] = racing
                    if cond in ("control", "delete", "delete-once", "dirrace"):
                        self.write([self.asst(bad)], "a")
                with open(self.tr, "rb") as f:
                    before = f.read()
                notes = []
                try:
                    r = evaluate({"transcript_path": self.tr, "stop_hook_active": k > 0,
                                  "last_assistant_message": bad}, self.now, self.start, sdir, notes)
                finally:
                    globals()["load_state"] = real_load
                with open(self.tr, "rb") as f:
                    self.assertEqual(f.read(), before, (cond, k))  # no transcript write
                kinds.append("block" if r else "cap" if notes and "block cap hit" in notes[0]
                             else "open" if notes and "could not establish" in notes[0] else "other")
            # at most one state file: no second counter, and (round 29) no `.lock` file; a directory the dirrace
            # removed during the last Stop is not recreated by that Stop's save
            names = os.listdir(sdir) if os.path.isdir(sdir) else []
            self.assertEqual(names, [] if cond == "dirrace" else [state_subdir(self.tr)], cond)
            return kinds

        def test_r26_item1_recovery_counter_survives_loop_resets(self):
            # finding 1 (HIGH): a transcript rewrite, a synthetic "Hook feedback:" user entry, a deleted state file,
            # and a state dir removed mid-call each reset the counter, so 7 violating Stops gave 7 blocks
            self.assertEqual(self.loop7("control"), ["block"] * 3 + ["cap"] * 4)
            for cond in ("rewrite", "hookfeedback"):  # the counter is preserved while stop_hook_active holds
                self.assertEqual(self.loop7(cond), ["block"] * 3 + ["cap"] * 4, cond)
            for cond in ("delete", "dirrace"):  # an absent state mid-loop is unknown: fail open at once
                self.assertEqual(self.loop7(cond), ["block"] + ["open"] * 6, cond)
            # a single deletion mid-loop cannot re-arm the cap: the fail-open allow records the counter at the cap
            kinds = self.loop7("delete-once")
            self.assertEqual(kinds, ["block", "block", "open", "cap", "cap", "cap", "cap"])
            for cond in ("control", "rewrite", "hookfeedback", "delete", "delete-once", "dirrace"):
                self.assertLessEqual(self.loop7(cond).count("block"), BLOCK_CAP, cond)
            # outside the corrective loop an absent state is still an established 0: the violation blocks
            self.write([self.user("go"), self.asst("[2026-09-23T20:00Z] x")])
            self.assertTrue(self.ev(last_assistant_message="[2026-09-23T20:00Z] x").startswith(BLOCK_PREFIX))

        def test_r26_item3_unconvertible_claim_fails_open(self):
            # finding 3 (MED): an unbracketed historical claim with a non-local abbreviation, or an invalid date,
            # was UNVERIFIABLE and blocked (misreporting UTC as a non-local abbreviation for 2020-02-30)
            for txt in ("2020-01-01 00:00 PST service launched", "2020-02-30 00:00 UTC service launched",
                        "[2020-02-30 00:00 UTC] x", "- 2020-01-01 00:00 PST launch", "[2026-13-01T00:00Z] y"):
                self.assertIsNone(self.final(txt), txt)
            # no diagnostic survives to be wrong: an unconvertible claim never appears in a block reason, and a
            # convertible violation beside it still blocks
            r = self.final("[2020-02-30 00:00 UTC] x\n[2020-01-01 00:00 PST] y\n2099-01-01T00:00Z z")
            self.assertIn("2099-01-01T00:00Z in your final message", r)
            for s in ("2020-02-30", "PST", "UNVERIFIABLE"):
                self.assertNotIn(s, r)
            self.assertNotIn("UNVERIFIABLE and blocks", " ".join(__doc__.split()))

        # -- round 27 (codex gpt-6-astra high QA of round 26) --
        def other_hook_feedback(self):
            # ANOTHER Stop hook's block feedback: a user-role Stop-feedback record without this hook's prefix
            return self.user("Stop hook feedback:\nstatus-header hook: your final message lacks a status header")

        def alternating(self, tp, sdir, n=16):
            """One Stop outside the corrective loop, then n - 1 under stop_hook_active, alternating a future-stamped
            heartbeat and `Done.`, which another Stop hook rejects; returns (kinds, counters)."""
            bad = "[2026-09-23T20:00Z] heartbeat"
            kinds, counters = [], []
            for k in range(n):
                final = bad if k % 2 == 0 else "Done."
                if k and os.path.exists(tp):
                    self.write([self.asst(final)], "a")
                notes = []
                r = evaluate({"transcript_path": tp, "stop_hook_active": k > 0, "last_assistant_message": final},
                             self.now, self.start, sdir, notes)
                kinds.append("block" if r else "cap" if notes and "block cap hit" in notes[0]
                             else "open" if notes else "pass")
                extra = {}
                load_state(sdir, tp, extra)
                counters.append(extra.get("blocks"))
                if os.path.exists(tp):
                    self.write([self.block_entry() if r else self.other_hook_feedback()], "a")
            return kinds, counters

        def test_r27_clean_pass_in_loop_keeps_counter_alternating_hook(self):
            # finding (HIGH): a clean Stop inside the corrective loop reset the counter, so alternating with another
            # Stop hook that rejected the clean message gave counter 1, 0, 1, 0 and eight blocks; the counter is
            # now preserved across every Stop while stop_hook_active holds
            self.write([self.user("go"), self.asst("[2026-09-23T20:00Z] heartbeat")])
            kinds, counters = self.alternating(self.tr, self.sdir)
            self.assertLessEqual(kinds.count("block"), BLOCK_CAP, kinds)
            self.assertLessEqual(kinds[1:].count("block"), BLOCK_CAP, kinds)
            self.assertEqual(kinds, ["block", "pass", "block", "pass", "block"] + ["pass", "cap"] * 5 + ["pass"],
                             kinds)
            self.assertEqual(counters[0], 1)
            self.assertEqual(counters[1:], sorted(counters[1:]), counters)  # never decreases inside the loop
            self.assertEqual(counters[-1], BLOCK_CAP)
            # the unreadable-transcript branch (the counter written with the boundary kept) preserves it too
            missing = os.path.join(self.tmp, "missing.jsonl")
            kinds, counters = self.alternating(missing, os.path.join(self.tmp, "state-missing"), 17)
            self.assertLessEqual(kinds.count("block"), BLOCK_CAP, kinds)
            self.assertEqual(counters[1:], sorted(counters[1:]), counters)
            # a Stop outside the loop starts a new count: the next violation blocks again
            self.assertTrue(self.ev(last_assistant_message="[2026-09-23T20:00Z] x").startswith(BLOCK_PREFIX))

        def test_r27_unknown_counter_pass_in_loop_records_cap(self):
            # an UNREADABLE counter met by a clean pass under stop_hook_active is repaired AT the cap, never at 0
            self.write([self.user("go"), self.asst("[2026-09-23T20:00Z] x")])
            self.assertIsNotNone(self.ev(last_assistant_message="[2026-09-23T20:00Z] x"))
            key = self.key()
            with open(key, "w") as f:
                f.write("{not json")
            self.write([self.block_entry(), self.asst("Done.")], "a")
            self.assertIsNone(self.ev(stop_hook_active=True, last_assistant_message="Done."))
            extra = {}
            self.assertTrue(load_state(self.sdir, self.tr, extra)[3])
            self.assertEqual(extra["blocks"], BLOCK_CAP)
            notes = []
            self.write([self.other_hook_feedback(), self.asst("[2026-09-23T20:00Z] y")], "a")
            self.assertIsNone(evaluate({"transcript_path": self.tr, "stop_hook_active": True,
                                        "last_assistant_message": "[2026-09-23T20:00Z] y"}, self.now, self.start,
                                       self.sdir, notes))
            self.assertIn("block cap hit", notes[0])
            doc = " ".join(__doc__.split())
            for s in ("reset to 0 ONLY by a Stop without stop_hook_active", "NEVER decreases",
                      "at most BLOCK_CAP times", "gets only the blocks the run has left"):
                self.assertIn(s, doc)
            self.assertNotIn("reset to 0 only by a pass", doc)

        def test_r27_randomized_interleavings_bounded(self):
            # the bound, re-derived: within one continuous stop_hook_active run this hook blocks at most BLOCK_CAP
            # times for ANY interleaving of other hooks' feedback, transcript contents, and clean or violating
            # messages; 600 seeded random runs, each checked Stop by Stop
            import random
            rng = random.Random(27)
            finals = ("[2026-09-23T20:00Z] heartbeat", "Done.", f"{ustamp(self.now)} fixed.",
                      "work at 2026-09-23T21:00Z", "x\nSession elapsed 09:00", f"{ustamp(self.now - 40 * MIN)} old",
                      None)
            events = ("other", "own", "work", "meta", "user", "rewrite", "delete", "corrupt", "none")
            worst = 0
            for run in range(600):
                sdir = os.path.join(self.tmp, f"rs{run}")
                missing = rng.random() < 0.1
                self.write([self.user("go"), self.asst(rng.choice(finals[:-1]))])
                tp = os.path.join(self.tmp, f"missing{run}.jsonl") if missing else self.tr
                if rng.random() < 0.5:
                    self.assertTrue(save_state(sdir, tp, 0, None, [], rng.randrange(BLOCK_CAP + 1)))
                blocks, trace, prev, touched = 0, [], None, False
                for k in range(rng.randrange(2, 18)):
                    final = rng.choice(finals)
                    if k and not missing:
                        for ev in rng.sample(events, rng.randrange(1, 3)):
                            if ev == "other":
                                self.write([self.other_hook_feedback()], "a")
                            elif ev == "own":
                                self.write([self.block_entry()], "a")
                            elif ev == "work":
                                self.write([self.asst("working")] * rng.randrange(1, 13), "a")
                            elif ev == "meta":
                                self.write([self.user("m", isMeta=True), self.asst("meta", isMeta=True)], "a")
                            elif ev == "user":  # a user-role entry the hook reads as a genuine user message
                                self.write([self.user("Hook feedback: " + BLOCK_PREFIX)], "a")
                            elif ev == "rewrite":  # the transcript rewritten under the same path
                                with open(tp, "w") as f:
                                    f.write(json.dumps(self.user("go" + "!" * k)) + "\n")
                            elif ev in ("delete", "corrupt") and os.path.isdir(sdir):  # external state damage
                                touched = True
                                for p in self.state_files(sdir):  # round 30: in the per-transcript subdirectories
                                    if ev == "delete":
                                        os.unlink(p)
                                    else:
                                        with open(p, "w") as f:
                                            f.write("{bad")
                        if final is not None:
                            self.write([self.asst(final)], "a")
                    payload = {"transcript_path": tp, "stop_hook_active": k > 0}
                    if final is not None or missing:
                        payload["last_assistant_message"] = final or "Done."
                    notes = []
                    r = evaluate(payload, self.now, self.start, sdir, notes)
                    trace.append((k, final, "block" if r else notes[0][:40] if notes else "pass"))
                    blocks += bool(r)
                    if r and not missing:
                        self.write([self.block_entry()], "a")
                    extra = {}
                    ok = load_state(sdir, tp, extra)[3]
                    cur = extra.get("blocks") if ok and not extra.get("absent") else None
                    if k and cur is not None and prev is not None and not touched:
                        self.assertGreaterEqual(cur, prev, (run, trace))  # never decreases inside the loop
                    prev, touched = (cur if k else None), False
                    self.assertLessEqual(blocks, BLOCK_CAP, (run, trace))
                worst = max(worst, blocks)
            self.assertEqual(worst, BLOCK_CAP)  # the bound is reached, so the runs exercise it

        # -- round 28 (codex gpt-6-astra high QA of round 27) --
        def test_r28_concurrent_stops_cannot_rearm_counter(self):
            # finding (codex MED): the bound assumed serialized Stops; two concurrent evaluations for one transcript
            # (a clean Stop A loads 1 and pauses before saving; a violating Stop B loads 1, saves 2, and blocks; A
            # saves 1; repeated) gave eight blocks; the whole load, evaluate, and save now runs under a per-transcript
            # exclusive NON-BLOCKING lock, and a Stop that cannot take it fails open with the counter unchanged
            bad = "[2026-09-23T20:00Z] heartbeat"
            self.write([self.user("go"), self.asst(bad)])
            self.assertTrue(self.ev(last_assistant_message=bad).startswith(BLOCK_PREFIX))  # counter 1
            real_save, paused, resume = save_state, threading.Event(), threading.Event()

            def parking_save(*a, **k):  # A parks after loading the counter, before saving it
                if threading.current_thread().name == "A":
                    paused.set()
                    resume.wait(10)
                return real_save(*a, **k)

            def counter():
                extra = {}
                load_state(self.sdir, self.tr, extra)
                return extra.get("blocks")

            b_kinds, counters = [], []
            globals()["save_state"] = parking_save
            try:
                for _ in range(8):
                    paused.clear()
                    resume.clear()
                    out = {"notes": []}

                    def run_a(out=out):
                        out["r"] = evaluate({"transcript_path": self.tr, "stop_hook_active": True,
                                             "last_assistant_message": "Done."}, self.now, self.start, self.sdir,
                                            out["notes"])
                    a = threading.Thread(name="A", target=run_a)
                    a.start()
                    try:
                        self.assertTrue(paused.wait(10))  # A has loaded the counter (and holds the lock)
                        notes = []
                        r = evaluate({"transcript_path": self.tr, "stop_hook_active": True,
                                      "last_assistant_message": bad}, self.now, self.start, self.sdir, notes)
                        b_kinds.append("block" if r else notes[0] if notes else "pass")
                    finally:
                        resume.set()
                        a.join(10)
                    self.assertFalse(a.is_alive())
                    self.assertEqual((out.get("r"), out["notes"]), (None, []))  # A's clean pass, unaffected
                    counters.append(counter())
            finally:
                globals()["save_state"] = real_save
            self.assertEqual(sum(k == "block" for k in b_kinds), 0, b_kinds)
            for k in b_kinds:
                self.assertIn("another evaluation holds the state lock", k)
                self.assertIn("block counter unchanged", k)
            self.assertEqual(counters, [1] * 8)
            # serialized Stops after the overlap still count up to the cap and stop blocking
            kinds = []
            for _ in range(3):
                notes = []
                r = evaluate({"transcript_path": self.tr, "stop_hook_active": True, "last_assistant_message": bad},
                             self.now, self.start, self.sdir, notes)
                kinds.append("block" if r else "cap" if notes and "block cap hit" in notes[0] else "other")
            self.assertEqual(kinds, ["block", "block", "cap"])
            # across PROCESSES too: while another process holds the lock (round 29: on the state DIRECTORY), a
            # Stop fails open and writes nothing
            key = self.key()
            lockp = os.path.dirname(key)  # round 30: the transcript's own state subdirectory
            with open(key, "rb") as f:
                before = f.read()
            holder = subprocess.Popen([sys.executable, "-I", "-B", "-c",
                                       "import fcntl, os, sys\n"
                                       "fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)\n"
                                       "fcntl.flock(fd, fcntl.LOCK_EX)\n"
                                       "print('held', flush=True)\n"
                                       "sys.stdin.readline()\n", lockp],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(holder.stdout.readline().strip(), "held")
                for active in (False, True):
                    notes = []
                    self.assertIsNone(evaluate({"transcript_path": self.tr, "stop_hook_active": active,
                                                "last_assistant_message": bad}, self.now, self.start, self.sdir,
                                               notes))
                    self.assertIn("holds the state lock", notes[0])
                with open(key, "rb") as f:
                    self.assertEqual(f.read(), before)  # no state change, the counter included
            finally:
                holder.stdin.close()
                holder.wait(10)
                holder.stdout.close()
            self.assertTrue(self.ev(last_assistant_message=bad).startswith(BLOCK_PREFIX))  # the lock is released

        def test_r28_lock_unavailable_writes_no_state(self):
            # locking unavailable (round 29: no fcntl, or a flock the filesystem refuses, on a usable private state
            # dir): the Stop writes no state, so under stop_hook_active it cannot block (it fails open with a
            # warning) and outside the loop only its final message can block, as for an unpersistable state
            # (test_r7_state_unpersistable_...)
            import errno
            bad = "[2026-09-23T20:00Z] heartbeat"
            self.write([self.user("go"), self.asst(bad)])
            self.seed(1)
            key = self.key()
            with open(key, "rb") as f:
                before = f.read()
            real_fcntl = globals().get("fcntl")

            class RefusingFcntl:  # a filesystem that refuses flock
                LOCK_EX, LOCK_NB = getattr(real_fcntl, "LOCK_EX", 2), getattr(real_fcntl, "LOCK_NB", 4)

                @staticmethod
                def flock(fd, op):
                    raise OSError(errno.ENOLCK, "no locks available")
            for how in ("nofcntl", "refused"):
                globals()["fcntl"] = None if how == "nofcntl" else RefusingFcntl
                try:
                    notes = []
                    self.assertIsNone(evaluate({"transcript_path": self.tr, "stop_hook_active": True,
                                                "last_assistant_message": bad}, self.now, self.start, self.sdir,
                                               notes), how)
                    self.assertIn("could not establish", notes[0], how)
                    self.assertTrue(evaluate({"transcript_path": self.tr, "last_assistant_message": bad}, self.now,
                                             self.start, self.sdir).startswith(BLOCK_PREFIX), how)
                    dfd, status = lock_state(self.sdir, self.tr)
                    try:
                        self.assertEqual(status, "unavailable", how)
                        self.assertIsNotNone(dfd, how)  # open for reads only
                    finally:
                        if dfd is not None:
                            os.close(dfd)
                finally:
                    globals()["fcntl"] = real_fcntl
                with open(key, "rb") as f:
                    self.assertEqual(f.read(), before, how)
                self.assertEqual(os.listdir(self.sdir), [state_subdir(self.tr)], how)
            # with no usable private state dir (not private; a symlink to a private dir, which is not followed; a
            # regular file) there is nothing to serialize and no state can be read or written: outside the loop a
            # bad final message still blocks, and inside it the Stop fails open as before
            public = os.path.join(self.tmp, "public")
            os.mkdir(public, 0o755)
            os.chmod(public, 0o755)
            link = os.path.join(self.tmp, "link")
            os.symlink(self.sdir, link)
            plain = os.path.join(self.tmp, "plain")
            with open(plain, "w") as f:
                f.write("x")
            for sdir in (public, link, plain):
                self.assertEqual(lock_state(sdir, self.tr), (None, "nodir"), sdir)
                self.assertTrue(evaluate({"transcript_path": self.tr, "last_assistant_message": bad}, self.now,
                                         self.start, sdir).startswith(BLOCK_PREFIX), sdir)
                notes = []
                self.assertIsNone(evaluate({"transcript_path": self.tr, "stop_hook_active": True,
                                            "last_assistant_message": bad}, self.now, self.start, sdir, notes), sdir)
                self.assertIn("could not establish", notes[0], sdir)
            self.assertEqual(os.listdir(public), [])
            with open(key, "rb") as f:
                self.assertEqual(f.read(), before)  # the symlinked dir was not followed

        def test_r28_save_never_lowers_counter_inside_loop(self):
            # defence in depth against a writer that ignores the (advisory) lock: under stop_hook_active the save
            # re-reads the stored counter and never writes a lower value, and a block whose write would not RAISE
            # the stored counter is not recorded, so it fails open
            bad = "[2026-09-23T20:00Z] heartbeat"
            real_tm = turn_messages

            def raising(*a, **k):  # between this Stop's load and its save, another writer records 2
                self.assertTrue(save_state(self.sdir, self.tr, 0, None, [], 2))
                return real_tm(*a, **k)

            def counter():
                extra = {}
                load_state(self.sdir, self.tr, extra)
                return extra.get("blocks")

            for final, expect in (("Done.", "pass"), (bad, "open")):
                self.write([self.user("go"), self.asst(bad)])
                self.seed(1)
                notes = []
                globals()["turn_messages"] = raising
                try:
                    r = evaluate({"transcript_path": self.tr, "stop_hook_active": True,
                                  "last_assistant_message": final}, self.now, self.start, self.sdir, notes)
                finally:
                    globals()["turn_messages"] = real_tm
                kind = "block" if r else "open" if notes and "could not establish" in notes[0] else "pass"
                self.assertEqual(kind, expect, final)
                self.assertEqual(counter(), 2, final)  # the other writer's higher value, never the loaded 1
            # outside the corrective loop the count legitimately restarts: no re-read, the Stop's own value is written
            self.write([self.user("go"), self.asst("Done.")])
            self.seed(2)
            self.assertIsNone(self.ev(last_assistant_message="Done."))
            self.assertEqual(counter(), 0)

        def test_r28_disclosures(self):
            doc = " ".join(__doc__.split())
            for s in ("Round 28: that bound assumes the Stops for one transcript are SERIALIZED", "NON-BLOCKING lock",
                      "Two OVERLAPPING Stops", "no state is ever written"):
                self.assertIn(s, doc)
            src = inspect.getsource(_evaluate)
            for s in ("CONCURRENCY (round 28)", "DISCHARGED by the lock", "never writes a value lower"):
                self.assertIn(s, " ".join(src.split()))

        # -- round 29 (codex gpt-6-astra high QA of round 28) --
        def overlap_disturbed(self, mode, n=8, serial=8):
            """The codex round-28 regression: an initial block records 1; then `n` times a clean Stop A
            (stop_hook_active) takes the lock, loads and RE-READS the counter, and parks at a barrier just before
            its save (inside save_state, which save() reaches only after its final re-read); `mode` disturbs the
            synchronization or the state meanwhile ("unlink-lock": delete only the *.lock file(s), round 28's
            attack; "rename-dir": rename the state dir away and recreate it empty; "delete-state": delete the
            state file; round 30, "rename-subdir": rename the transcript's state subdirectory away and recreate
            it empty; "rmtree-subdir": remove that subdirectory); a violating Stop B (stop_hook_active) runs to
            completion; A resumes and saves. Then
            `serial` more violating Stops. Returns (B kinds, serial kinds, the counter after each B, blocks in
            the stop_hook_active run)."""
            bad = "[2026-09-23T20:00Z] heartbeat"
            self.write([self.user("go"), self.asst(bad)])
            self.assertTrue(self.ev(last_assistant_message=bad).startswith(BLOCK_PREFIX))  # counter 1
            real_save, paused, resume = save_state, threading.Event(), threading.Event()

            def parking_save(*a, **k):
                if threading.current_thread().name == "A":
                    paused.set()
                    resume.wait(10)
                return real_save(*a, **k)

            def counter():
                extra = {}
                ok = load_state(self.sdir, self.tr, extra)[3]
                return extra.get("blocks") if ok and not extra.get("absent") else None

            def kind(r, notes):
                return ("block" if r else "busy" if notes and "holds the state lock" in notes[0]
                        else "cap" if notes and "block cap hit" in notes[0]
                        else "open" if notes and "could not establish" in notes[0] else "pass")
            b_kinds, s_kinds, counters, active_blocks = [], [], [], 0
            globals()["save_state"] = parking_save
            try:
                for i in range(n):
                    paused.clear()
                    resume.clear()
                    out = {"notes": []}

                    def run_a(out=out):
                        out["r"] = evaluate({"transcript_path": self.tr, "stop_hook_active": True,
                                             "last_assistant_message": "Done."}, self.now, self.start, self.sdir,
                                            out["notes"])
                    a = threading.Thread(name="A", target=run_a)
                    a.start()
                    try:
                        self.assertTrue(paused.wait(10), (mode, i))  # A is past its final re-read
                        if mode == "unlink-lock":
                            for x in os.listdir(self.sdir):
                                if x.endswith(".lock"):
                                    os.unlink(os.path.join(self.sdir, x))
                        elif mode == "rename-dir":
                            os.rename(self.sdir, f"{self.sdir}.old{i}")
                            os.mkdir(self.sdir, 0o700)
                        elif mode == "delete-state":
                            os.unlink(self.key())
                        elif mode == "rename-subdir":
                            sub = os.path.dirname(self.key())
                            os.rename(sub, f"{sub}.old{i}")
                            os.mkdir(sub, 0o700)
                        elif mode == "rmtree-subdir":
                            shutil.rmtree(os.path.dirname(self.key()))
                        notes = []
                        r = evaluate({"transcript_path": self.tr, "stop_hook_active": True,
                                      "last_assistant_message": bad}, self.now, self.start, self.sdir, notes)
                        b_kinds.append(kind(r, notes))
                        active_blocks += bool(r)
                    finally:
                        resume.set()
                        a.join(10)
                    self.assertFalse(a.is_alive())
                    self.assertEqual((out.get("r"), out["notes"]), (None, []), (mode, i))  # A's clean pass
                    counters.append(counter())
            finally:
                globals()["save_state"] = real_save
            for _ in range(serial):
                notes = []
                r = evaluate({"transcript_path": self.tr, "stop_hook_active": True, "last_assistant_message": bad},
                             self.now, self.start, self.sdir, notes)
                s_kinds.append(kind(r, notes))
                active_blocks += bool(r)
            return b_kinds, s_kinds, counters, active_blocks

        def test_r29_disturbed_overlap_stays_bounded(self):
            # finding (codex HIGH): deleting ONLY round 28's `.lock` file while A was parked after its final
            # counter re-read let B lock a new inode, record 2, and block; A then saved 1; repeated, eight blocks in
            # one run (counter 1, 2, 1, ...). Round 29 locks the state DIRECTORY and does every state read and
            # write through that one locked descriptor, so no single-file unlink replaces the lock identity (round
            # 30: the locked directory is the transcript's own state subdirectory, disturbed here too)
            for mode in ("unlink-lock", "rename-dir", "delete-state", "rename-subdir", "rmtree-subdir"):
                b_kinds, s_kinds, counters, active_blocks = self.overlap_disturbed(mode)
                trace = (mode, b_kinds, s_kinds, counters)
                # 1 + 8 overlapping pairs (16 Stops) + 8 serial Stops: the run's blocks stay within the cap (the
                # initial block recorded 1, so at most BLOCK_CAP - 1 more)
                self.assertLessEqual(active_blocks, BLOCK_CAP - 1, trace)
                self.assertEqual(counters, sorted(counters), trace)  # never decreases inside the loop
                self.assertFalse([x for x in os.listdir(self.sdir) if x.endswith(".lock")], trace)  # no lock file
                if mode in ("rename-dir", "rename-subdir", "rmtree-subdir"):
                    # B meets the NEW directory's absent state: an unknown count, so it fails open and records the
                    # cap; A's save lands in the orphaned directory, harmlessly (or, removed, nowhere)
                    self.assertEqual(b_kinds, ["open"] * 8, trace)
                    self.assertEqual(counters, [BLOCK_CAP] * 8, trace)
                    self.assertEqual(s_kinds, ["cap"] * 8, trace)
                    if mode == "rename-dir":
                        extra = {}
                        self.assertTrue(load_state(f"{self.sdir}.old0", self.tr, extra)[3])
                        self.assertEqual(extra["blocks"], 1)  # A's value, written into the orphan
                    elif mode == "rename-subdir":
                        with open(os.path.join(f"{os.path.dirname(self.key())}.old0", STATE_FILE)) as f:
                            self.assertEqual(json.load(f)["blocks"], 1)  # A's value, written into the orphan
                else:
                    # the directory lock is still HELD by A: B fails open unchecked, the counter unchanged at 1
                    self.assertEqual(b_kinds, ["busy"] * 8, trace)
                    self.assertEqual(counters, [1] * 8, trace)
                    self.assertEqual(s_kinds, ["block"] * (BLOCK_CAP - 1) + ["cap"] * (8 - BLOCK_CAP + 1), trace)
                self.tearDown()
                self.setUp()

        def test_r29_dir_removed_mid_evaluation_save_fails_safe(self):
            # rm -r of the locked directory while A is parked: A's save cannot create a file in a removed directory
            # (it fails, never raising), and the next Stop recreates the dir, finds the state ABSENT, and under
            # stop_hook_active fails open and records the cap
            bad = "[2026-09-23T20:00Z] heartbeat"
            self.write([self.user("go"), self.asst(bad)])
            self.assertTrue(self.ev(last_assistant_message=bad).startswith(BLOCK_PREFIX))
            real_save, paused, resume, got = save_state, threading.Event(), threading.Event(), {}

            def parking_save(*a, **k):
                paused.set()
                resume.wait(10)
                got["saved"] = real_save(*a, **k)
                return got["saved"]
            globals()["save_state"] = parking_save
            a = threading.Thread(target=lambda: got.setdefault("r", evaluate(
                {"transcript_path": self.tr, "stop_hook_active": True, "last_assistant_message": bad},
                self.now, self.start, self.sdir, got.setdefault("notes", []))))
            try:
                a.start()
                self.assertTrue(paused.wait(10))
                shutil.rmtree(self.sdir)
            finally:
                resume.set()
                a.join(10)
                globals()["save_state"] = real_save
            self.assertIs(got["saved"], False)
            self.assertIsNone(got["r"])  # its block could not be recorded, so it failed open
            self.assertIn("could not establish", got["notes"][0])
            notes = []
            self.assertIsNone(evaluate({"transcript_path": self.tr, "stop_hook_active": True,
                                        "last_assistant_message": bad}, self.now, self.start, self.sdir, notes))
            self.assertIn("could not establish", notes[0])
            extra = {}
            self.assertTrue(load_state(self.sdir, self.tr, extra)[3])
            self.assertEqual(extra["blocks"], BLOCK_CAP)

        def test_r29_state_io_is_relative_to_the_locked_dir(self):
            # every state read and write of an evaluation goes through the one locked directory descriptor: with
            # the lock held, the path is renamed away and a DIFFERENT private directory put in its place; the load
            # and the save still reach the locked (original) directory, and nothing is written to the new one
            # (round 30: the locked directory is the transcript's own state subdirectory; both the subdirectory and
            # the whole root are moved and replaced)
            self.write([self.user("go"), self.asst("[2026-09-23T20:00Z] x")])
            for moved in ("subdir", "root"):
                self.seed(1)
                sub = os.path.dirname(self.key())
                path = sub if moved == "subdir" else self.sdir
                dfd, status = lock_state(self.sdir, self.tr)
                try:
                    self.assertEqual(status, "held", moved)
                    os.rename(path, path + ".moved")
                    os.mkdir(path, 0o700)
                    extra = {}
                    self.assertEqual(load_state(None, self.tr, extra, dfd)[3], True, moved)
                    self.assertEqual(extra, {"blocks": 1}, moved)
                    self.assertTrue(save_state(None, self.tr, 0, None, [], 2, dfd), moved)
                    self.assertEqual(os.listdir(path), [], moved)
                    msub = sub + ".moved" if moved == "subdir" else os.path.join(self.sdir + ".moved",
                                                                                  state_subdir(self.tr))
                    self.assertEqual(os.listdir(msub), [STATE_FILE], moved)
                    # the lock is on the directory INODE: the moved directory is still held, the new one is free
                    probe = os.open(msub, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        with self.assertRaises(BlockingIOError):
                            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    finally:
                        os.close(probe)
                    other, st2 = lock_state(self.sdir, self.tr)
                    self.assertEqual(st2, "held", moved)
                    os.close(other)
                finally:
                    os.close(dfd)
                shutil.rmtree(path + ".moved")
                shutil.rmtree(self.sdir)

        def test_r29_disclosures(self):
            doc = " ".join(__doc__.split())
            for s in ("fcntl.flock on the private state DIRECTORY itself", "no single-file unlink can replace",
                      "there is no `.lock` file now", "rewrites the state file directly while holding no lock"):
                self.assertIn(s, doc)
            # round 30: the per-user contention the round-29 lock introduced is gone (see test_r30_disclosures)
            self.assertNotIn("DIFFERENT sessions of the same user also contend", doc)
            src = " ".join(inspect.getsource(_evaluate).split())
            for s in ("SYNCHRONIZATION IDENTITY (round 29)", "ORPHANED directory", "while holding no lock",
                      "outside the threat model"):
                self.assertIn(s, src)
            self.assertNotIn('".lock"', inspect.getsource(lock_state))

        # -- round 30 (claude-fable-5 QA of round 29) --
        def test_r30_other_transcripts_are_not_serialized(self):
            # finding (claude LOW): round 29's lock was the per-USER state directory, held for a whole evaluation
            # (about 1.2 s on a 49 MiB transcript), so every OTHER session's Stop overlapping it was allowed
            # unchecked; the lock is now the transcript's own state subdirectory, so only the same transcript's
            # Stops contend
            bad = "[2026-09-23T20:00Z] heartbeat"
            self.write([self.user("go"), self.asst(bad)])
            tr1 = self.tr
            self.write([self.user("go"), self.asst(bad)])
            tr2 = self.tr
            real_save, paused, resume = save_state, threading.Event(), threading.Event()

            def parking_save(*a, **k):  # A (transcript 1) parks inside its evaluation, holding its lock
                if threading.current_thread().name == "A":
                    paused.set()
                    resume.wait(10)
                return real_save(*a, **k)
            globals()["save_state"] = parking_save
            out = {"notes": []}
            a = threading.Thread(name="A", target=lambda: out.setdefault("r", evaluate(
                {"transcript_path": tr1, "last_assistant_message": bad}, self.now, self.start, self.sdir,
                out["notes"])))
            try:
                a.start()
                self.assertTrue(paused.wait(10))
                # a Stop of ANOTHER transcript is evaluated (it blocks), never skipped as busy
                notes = []
                r = evaluate({"transcript_path": tr2, "last_assistant_message": bad}, self.now, self.start,
                             self.sdir, notes)
                self.assertTrue(r and r.startswith(BLOCK_PREFIX), (r, notes))
                self.assertEqual(notes, [])
                # a Stop of the SAME transcript still finds the lock held and fails open unchecked
                notes = []
                self.assertIsNone(evaluate({"transcript_path": tr1, "last_assistant_message": bad}, self.now,
                                           self.start, self.sdir, notes))
                self.assertIn("holds the state lock", notes[0])
            finally:
                resume.set()
                a.join(10)
                globals()["save_state"] = real_save
            self.assertFalse(a.is_alive())
            self.assertTrue(out["r"].startswith(BLOCK_PREFIX))
            # across processes: a holder of the state ROOT directory's flock (the round-29 lock identity) no longer
            # serializes anything, while a holder of a transcript's subdirectory serializes that transcript only
            for held, busy in ((self.sdir, ()), (os.path.dirname(self.key(tr1)), (tr1,))):
                holder = subprocess.Popen([sys.executable, "-I", "-B", "-c",
                                           "import fcntl, os, sys\n"
                                           "fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)\n"
                                           "fcntl.flock(fd, fcntl.LOCK_EX)\n"
                                           "print('held', flush=True)\n"
                                           "sys.stdin.readline()\n", held],
                                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
                try:
                    self.assertEqual(holder.stdout.readline().strip(), "held")
                    for tp in (tr1, tr2):
                        notes = []
                        r = evaluate({"transcript_path": tp, "last_assistant_message": bad}, self.now, self.start,
                                     self.sdir, notes)
                        if tp in busy:
                            self.assertIsNone(r, (held, tp))
                            self.assertIn("holds the state lock", notes[0])
                        else:
                            self.assertTrue(r and r.startswith(BLOCK_PREFIX), (held, tp, r, notes))
                finally:
                    holder.stdin.close()
                    holder.wait(10)
                    holder.stdout.close()

        def test_r30_prune_state_policy(self):
            # the per-transcript subdirectories are bounded by pruning: a Stop that CREATES its subdirectory then
            # removes (at most PRUNE_BATCH of) the subdirectories unwritten for PRUNE_AGE_NS, never one an
            # evaluation holds locked, never a name without the subdirectory shape, never through a symlink
            bad = "[2026-09-23T20:00Z] heartbeat"
            old_s = (time.time_ns() - PRUNE_AGE_NS) / 1e9 - 3600
            trs = {}
            for name in ("old", "locked", "recent"):
                self.write([self.user("go"), self.asst(bad)])
                trs[name] = self.tr
                self.assertTrue(self.ev(last_assistant_message=bad).startswith(BLOCK_PREFIX), name)
            subs = {name: os.path.dirname(self.key(tp)) for name, tp in trs.items()}
            self.assertEqual(sorted(os.listdir(self.sdir)), sorted(os.path.basename(s) for s in subs.values()))
            for name in ("old", "locked"):
                os.utime(subs[name], (old_s, old_s))
            other = os.path.join(self.sdir, "keepme")  # a name without the subdirectory shape
            os.mkdir(other, 0o700)
            os.utime(other, (old_s, old_s))
            target = os.path.join(self.tmp, "elsewhere")  # a subdirectory-shaped SYMLINK is never followed
            os.mkdir(target, 0o700)
            os.utime(target, (old_s, old_s))
            link = os.path.join(self.sdir, _sha("link") + ".d")
            os.symlink(target, link)
            # a Stop on an EXISTING transcript creates nothing, so it prunes nothing
            self.write([self.asst("Done.")], "a")
            self.tr = trs["recent"]
            self.assertIsNone(self.ev(last_assistant_message="Done."))
            self.assertTrue(os.path.isdir(subs["old"]))
            held, status = lock_state(self.sdir, trs["locked"])  # an evaluation holding the old subdirectory
            try:
                self.assertEqual(status, "held")
                self.write([self.user("go"), self.asst("Done.")])  # a NEW transcript: its Stop prunes
                self.assertIsNone(self.ev(last_assistant_message="Done."))
            finally:
                os.close(held)
            names = os.listdir(self.sdir)
            self.assertNotIn(os.path.basename(subs["old"]), names)  # unlocked and 7+ days unwritten: removed
            for kept in (subs["locked"], subs["recent"], other, link):
                self.assertIn(os.path.basename(kept), names)
            self.assertIn(state_subdir(self.tr), names)
            self.assertTrue(os.path.isdir(target))
            self.assertTrue(os.path.isfile(self.key(trs["locked"])))
            # the pruned transcript's state is gone: outside the loop it is evaluated afresh, and inside a
            # corrective loop its Stop fails open (an absent state is an unknown count), never blocks
            notes = []
            self.assertIsNone(evaluate({"transcript_path": trs["old"], "stop_hook_active": True,
                                        "last_assistant_message": bad}, self.now, self.start, self.sdir, notes))
            self.assertIn("could not establish", notes[0])
            # that Stop re-created the pruned transcript's subdirectory, so it pruned too: the old subdirectory
            # that was locked before, no longer held, is removed now
            self.assertFalse(os.path.exists(subs["locked"]))
            self.assertTrue(os.path.isdir(subs["old"]))
            # the batch bound: at most PRUNE_BATCH removals per prune
            for i in range(PRUNE_BATCH + 5):
                d = os.path.join(self.sdir, _sha(f"bulk{i}") + ".d")
                os.mkdir(d, 0o700)
                with open(os.path.join(d, STATE_FILE), "w") as f:
                    f.write("{}")
                os.utime(d, (old_s, old_s))
            self.assertEqual(prune_state(self.sdir), PRUNE_BATCH)
            self.assertEqual(prune_state(self.sdir), 5)  # the rest
            self.assertEqual(prune_state(self.sdir), 0)
            self.assertEqual(prune_state(os.path.join(self.tmp, "no-such-root")), 0)  # never creates the root
            self.assertFalse(os.path.exists(os.path.join(self.tmp, "no-such-root")))

        def test_r30_disclosures(self):
            doc = " ".join(__doc__.split())
            for s in ("only Stops of the SAME transcript contend", "about 1.2 s on a 49 MiB turn", "PRUNING",
                      "a NON-BLOCKING exclusive flock on it succeeds", "it has not been written for 7 days",
                      "neither read nor removed by round 30", "named by the SHA-256 of transcript_path plus `.d`"):
                self.assertIn(s, doc)
            self.assertNotIn("the window is one evaluation, milliseconds", doc)
            src = " ".join(inspect.getsource(_evaluate).split())
            for s in ("per transcript (round 30)", "PRUNING (round 30)", "treats exactly as an external deletion"):
                self.assertIn(s, src)
            self.assertEqual((PRUNE_AGE_NS, PRUNE_SCAN, PRUNE_BATCH), (7 * 86400 * 10 ** 9, 4096, 32))

        # -- round 31 (codex gpt-6-astra high QA of round 30) --
        def test_r31_prune_cursor_reaches_every_subdirectory(self):
            # finding (codex LOW): every prune examined the same first PRUNE_SCAN entries in directory order, so an
            # eligible stale subdirectory listed beyond them was never removed; a persisted cursor over the sorted
            # names now advances each prune. The stale one is created between two runs of PRUNE_SCAN recent ones,
            # so it lies beyond the first PRUNE_SCAN entries whichever way the filesystem orders creation
            os.makedirs(self.sdir, 0o700)
            os.chmod(self.sdir, 0o700)
            old_s = (time.time_ns() - PRUNE_AGE_NS) / 1e9 - 3600
            recent = [_sha(f"recent{i}") + ".d" for i in range(2 * PRUNE_SCAN)]
            stale = _sha("stale") + ".d"
            for name in recent[:PRUNE_SCAN] + [stale] + recent[PRUNE_SCAN:]:
                os.mkdir(os.path.join(self.sdir, name), 0o700)
            os.utime(os.path.join(self.sdir, stale), (old_s, old_s))
            with os.scandir(self.sdir) as it:
                order = [e.name for e in it]
            self.assertGreaterEqual(order.index(stale), PRUNE_SCAN)  # beyond a directory-order window
            removed = [prune_state(self.sdir) for _ in range(3)]
            self.assertEqual(sum(removed), 1, removed)
            names = set(os.listdir(self.sdir))
            self.assertNotIn(stale, names)
            self.assertTrue(set(recent) <= names)  # nothing recent is removed
            # the cursor persists between prunes and is dropped once a prune has examined every name
            self.assertEqual(prune_state(self.sdir), 0)
            cur = os.path.join(self.sdir, PRUNE_CURSOR)
            self.assertTrue(os.path.isfile(cur))  # 2 * PRUNE_SCAN names: a prune never covers them all
            def read_cursor():
                fd = os.open(self.sdir, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    return _read_prune_cursor(fd)
                finally:
                    os.close(fd)
            self.assertTrue(_is_state_subdir(read_cursor()))
            for junk in ("not a name", "../" + stale):
                with open(cur, "w") as f:
                    f.write(junk)
                self.assertIsNone(read_cursor())
            os.unlink(cur)
            with open(os.path.join(self.tmp, "planted"), "w") as f:
                f.write(stale)
            os.symlink(os.path.join(self.tmp, "planted"), cur)  # a symlinked cursor is never followed
            self.assertIsNone(read_cursor())
            os.unlink(cur)
            small = os.path.join(self.tmp, "small")
            os.mkdir(small, 0o700)
            for i in range(3):
                os.mkdir(os.path.join(small, _sha(f"s{i}") + ".d"), 0o700)
            self.assertEqual(prune_state(small), 0)
            self.assertNotIn(PRUNE_CURSOR, os.listdir(small))  # a prune that examined every name keeps no cursor
            doc = " ".join(__doc__.split())
            for s in ("starting just after a persisted CURSOR name and wrapping around", "every eligible subdirectory "
                      "is eventually examined and removed", "a backlog drains only as new transcripts arrive"):
                self.assertIn(s, doc)
            self.assertNotIn("which each later new transcript continues to remove", doc)

        # -- round 32 (codex gpt-6-astra high QA of round 31) --
        def test_r32_fifo_at_prune_cursor_never_hangs(self):
            # finding (codex HIGH): a FIFO planted at prune-cursor blocked round 31's write-only open forever, so
            # a Stop that pruned past a whole window hung. The cursor is now written only through an exclusively
            # created regular temporary renamed over it. Run in a SUBPROCESS with a hard deadline, so a regression
            # fails the test instead of hanging the suite. A FIFO at the state file, and a directory at the
            # cursor, are exercised too; no temporary file is ever left behind.
            child = (
                "import datetime, hashlib, importlib.util, json, os, stat, sys, time\n"
                "spec = importlib.util.spec_from_file_location('sts32', sys.argv[1])\n"
                "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
                "tmp = sys.argv[2]; sdir = os.path.join(tmp, 'state'); os.mkdir(sdir, 0o700)\n"
                "m.PRUNE_SCAN = 8  # a prune that cannot cover every name, so it writes the cursor\n"
                "for i in range(m.PRUNE_SCAN + 1):\n"
                "    os.mkdir(os.path.join(sdir, hashlib.sha256(f'r{i}'.encode()).hexdigest() + '.d'), 0o700)\n"
                "cur = os.path.join(sdir, m.PRUNE_CURSOR); os.mkfifo(cur, 0o600)\n"
                "tr = os.path.join(tmp, 't.jsonl')\n"
                "with open(tr, 'w') as f:\n"
                "    f.write(json.dumps({'type': 'user', 'message': {'role': 'user', 'content': 'go'}}) + '\\n')\n"
                "now = datetime.datetime.now(datetime.timezone.utc)\n"
                "r = m.evaluate({'transcript_path': tr, 'last_assistant_message': 'Done.'}, now, now, sdir, [])\n"
                "assert r is None, r\n"
                "assert stat.S_ISREG(os.lstat(cur).st_mode), 'the cursor was not replaced by a regular file'\n"
                "fd = os.open(sdir, os.O_RDONLY | os.O_DIRECTORY)\n"
                "assert m._is_state_subdir(m._read_prune_cursor(fd))\n"
                "os.unlink(cur); os.mkfifo(cur, 0o600)\n"
                "m._write_prune_cursor(fd, 'x' * 64 + '.d')\n"
                "assert stat.S_ISREG(os.lstat(cur).st_mode)\n"
                "os.unlink(cur); os.mkdir(cur, 0o700)  # a directory at the cursor: the rename fails, fail open\n"
                "m._write_prune_cursor(fd, 'x' * 64 + '.d')\n"
                "assert stat.S_ISDIR(os.lstat(cur).st_mode)\n"
                "sub = os.path.join(sdir, m.state_subdir(tr)); st = os.path.join(sub, m.STATE_FILE)\n"
                "if os.path.lexists(st):\n"
                "    os.unlink(st)\n"
                "os.mkfifo(st, 0o600)  # a FIFO at the state file: read and save never block\n"
                "assert m.load_state(sdir, tr)[3] is False\n"
                "assert m.save_state(sdir, tr, 0, None, [], 0) is True\n"
                "assert stat.S_ISREG(os.lstat(st).st_mode)\n"
                "left = [n for d in (sdir, sub) for n in os.listdir(d) if n.endswith('.tmp')]\n"
                "assert not left, left\n"
                "print('R32-OK')\n")
            p = subprocess.run([sys.executable, "-I", "-B", "-c", child, os.path.abspath(__file__), self.tmp],
                               capture_output=True, text=True, timeout=30)
            self.assertEqual((p.returncode, p.stdout.strip()), (0, "R32-OK"), p.stderr[-2000:])
            src = " ".join(inspect.getsource(_write_prune_cursor).split())
            self.assertIn("os.O_EXCL", src)
            self.assertIn("os.replace(tmp, PRUNE_CURSOR", src)
            self.assertNotIn("O_TRUNC", src)
            self.assertIn("never by opening the existing entry", " ".join(__doc__.split()))

        # -- sibling parity on a single-hook install --
        PARITY_TESTS = ("test_r13_shared_lease_code_identical_to_clock_inject",
                        "test_shared_grammar_identical_to_sibling")
        PARITY_SIBLINGS = ("clock-inject.py", "future-stamp-write.py")

        def _parity_in_copy(self, siblings, value, dangling=False):
            """Copy this file (and `siblings`, found beside it) into a fresh directory, run ONLY the copy's
            sibling-parity tests in a child interpreter with AIQT_HOOKS_REQUIRE_SIBLINGS set to `value` (None:
            unset, whatever the caller has), and return ([rc, run, skipped, failures, errors], child stderr).
            With `dangling`, each sibling is a symlink to a missing target: it EXISTS but cannot be read."""
            base = "/dev/shm" if os.path.isdir("/dev/shm") else None
            d = tempfile.mkdtemp(prefix="sib.", dir=base)
            try:
                me = os.path.join(d, os.path.basename(os.path.abspath(__file__)))
                shutil.copyfile(os.path.abspath(__file__), me)
                for sib in siblings:
                    shutil.copyfile(_sibling_or_skip(sib), os.path.join(d, sib))
                if dangling:
                    for sib in self.PARITY_SIBLINGS:
                        os.symlink(os.path.join(d, "no-such-target"), os.path.join(d, sib))
                env = {k: v for k, v in os.environ.items() if k != "AIQT_HOOKS_REQUIRE_SIBLINGS"}
                if value is not None:
                    env["AIQT_HOOKS_REQUIRE_SIBLINGS"] = value
                code = ("import importlib.util as u, json, unittest\n"
                        "s = u.spec_from_file_location('m', %r)\n"
                        "m = u.module_from_spec(s)\n"
                        "s.loader.exec_module(m)\n"
                        "names = %r\n"
                        "unittest.TestLoader.loadTestsFromTestCase = lambda self, tc: unittest.TestSuite("
                        "tc(n) for n in names)\n"
                        "box, run = [], unittest.TextTestRunner.run\n"
                        "unittest.TextTestRunner.run = lambda self, t: box.append(run(self, t)) or box[-1]\n"
                        "rc = m._self_test()\n"
                        "r = box[0]\n"
                        "print(json.dumps([rc, r.testsRun, len(r.skipped), len(r.failures), len(r.errors)]))\n"
                        ) % (me, self.PARITY_TESTS)
                p = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code], env=env, capture_output=True,
                                   text=True, timeout=120)
                self.assertTrue(p.stdout.strip(), p.stderr)
                return json.loads(p.stdout.strip().splitlines()[-1]), p.stderr
            finally:
                shutil.rmtree(d, ignore_errors=True)

        def test_sibling_parity_skips_alone_and_fails_when_required(self):
            # a single-hook install: each sibling-parity test is SKIPPED (not passed) with a message naming the
            # absent sibling, unless AIQT_HOOKS_REQUIRE_SIBLINGS=1, when the same absence FAILS it
            n = len(self.PARITY_TESTS)
            for value in (None, "0", ""):
                got, err = self._parity_in_copy((), value)
                self.assertEqual(got, [0, n, n, 0, 0], (value, err))
                for sib in self.PARITY_SIBLINGS:
                    self.assertIn(f"sibling hook {sib} is absent (a standalone install)", err)
            got, err = self._parity_in_copy((), "1")
            self.assertEqual(got, [1, n, 0, n, 0], err)
            self.assertIn("AIQT_HOOKS_REQUIRE_SIBLINGS=1 requires it", err)
            # a sibling that EXISTS but cannot be read fails (never skips), with or without the variable
            for value in (None, "1"):
                got, err = self._parity_in_copy((), value, dangling=True)
                self.assertEqual((got[0], got[1], got[2], got[3] + got[4]), (1, n, 0, n), (value, err))

        def test_sibling_parity_runs_and_passes_with_siblings_present(self):
            # with every sibling beside the copy the parity tests RUN and pass, whatever the variable says
            n = len(self.PARITY_TESTS)
            for value in (None, "1"):
                got, err = self._parity_in_copy(self.PARITY_SIBLINGS, value)
                self.assertEqual(got, [0, n, 0, 0, 0], (value, err))

    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestLoader().loadTestsFromTestCase(T))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
