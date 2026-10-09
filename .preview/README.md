# Hooks preview

This directory is a preview channel for AIQT Guardrails hooks that are not yet part of the pack's plugin.
Each hook is one self-contained Python file that you can download, check, test, and switch on in Claude
Code by hand. This page is written so that you can hand it to your AI coding assistant and ask it to
install a hook for you: every step below is a command it can run, and every check tells it when to stop.

Ten hooks are published here, each listed with its checksum and link in the integrity table below.
A hook without a row in that table is not available here, and the install steps do not apply to it.

One document linked from this page is not a hook: [the OPF implementation prompt](../opf/spec/OPF-IMPLEMENTATION-PROMPT.md)
is a prompt you can give to an AI coding assistant so that it implements OPFiles natively in its own
project, from the specification. It has no row in the integrity table, and the install steps below do
not apply to it.

## What these hooks are

The three clock hooks, `clock-inject.py`, `stamp-truth-stop.py`, and `future-stamp-write.py`, back the
rule that a current timestamp is read from the clock, never recalled or guessed
([the rule text](../.claude/rules/aiqt/10-ACCUR-timestamp-from-clock.md)). `constraint-reread.py` backs the
rule that a standing constraint persists across context loss
([the rule text](../.claude/rules/aiqt/10-TRUST-standing-constraints-persist.md)), and `rerun-pass-check.py`
backs the rule that a rerun pass does not erase an earlier failure
([the rule text](../.claude/rules/aiqt/10-INTEG-rerun-pass-is-still-failure.md)); both are linked in the
[enforcement register](../ENFORCEMENT.md). The other hooks guard completion
records, background polling loops, existing working-record files, and commands that select processes by a
pattern, and `char-policy-write.py` applies your repository's own character policy to file writes; it backs
no rule in the pack and claims none. Each one is a discipline guard against accidental drift, not a
security boundary, and each one fails open: if the hook hits an error or input it cannot evaluate, it gets
out of the way rather than blocking your work. Two exceptions block instead: an interpreter older than
Python 3.14, described with the launch line below, and, on the six `PreToolUse` hooks, a deny decision that
cannot be written to standard output and flushed (a full device, a closed pipe), where the hook exits 2
rather than let a decided deny dissolve into a silent allow (a lost deny blocks, never allows; a lost
advisory note still allows with exit 0). Each file states
what it does not catch in a section headed `RESIDUAL COVERAGE` in its opening docstring; that section is
the authority, and the summary further down this page only points to it.

- **`clock-inject.py`** reads the real clock after each tool call and adds one short line to the
  assistant's context, in the form `CLOCK (read by hook, authoritative): <local time> | <UTC time>`,
  followed by the session's elapsed time when a lease is configured. The freshest time value in context
  is then a real reading, not one the model composed. It informs; it blocks nothing. Events:
  `PostToolUse` (after a tool call that succeeded) and `PostToolUseFailure` (after one that started and
  failed); register both to cover both outcomes.
- **`stamp-truth-stop.py`** checks each finished turn. When the assistant's prose carries a timestamp with
  an explicit zone that lies ahead of the clock, or an elapsed-time footer that disagrees with the
  configured lease, it blocks the stop and says why, so the assistant corrects the value before handing
  back. Text in code spans, fenced code blocks, and `>` quote lines is not checked, and a time that a
  scheduling word (such as `until`, `due`, or `next run at`) directly precedes is treated as a scheduled
  time, not a reading. Event: `Stop`.
- **`future-stamp-write.py`** denies a write that would record a future-dated timestamp into a folder you
  have declared as holding working records. An observed-time entry (a heartbeat, a recorded-at value) has
  to come from the clock, so one dated in the future was composed. A scheduled value (a deadline, a next
  run) is legitimately in the future and is allowed. It checks file writes and edits, and shell commands
  that write into those folders, including those made by a helper session started inside your session.
  Event: `PreToolUse`, matcher `Write|Edit|MultiEdit|Bash`.
- **`ungated-record.py`** denies a shell command that can write a completion claim after a test gate
  fails. For example, `pytest; echo PASS >> report` writes PASS even on failure; use
  `pytest && echo PASS >> report` so the record depends on success. It follows recognized test runners
  and literal completion words written by `echo`, `printf`, `cat`, or `tee` to a literal file path
  outside `/dev`. It checks the command before it runs; it does not run the tests or verify their results.
  Event: `PreToolUse`, matcher `Bash`.
- **`unbounded-wait.py`** denies a background `while` or `until` loop that sleeps between polls and
  carries no counter or deadline marker. Background means the tool's `run_in_background` is true or
  the loop, or a compound command around it (a group, subshell, `if`, or loop), is launched with `&`.
  It names a bounded rewrite, and can also point out a missing parent directory for a literal path
  being polled. A `break` alone is not a bound, and a `timeout` around only the probe or sleep does
  not bound the loop.
  Event: `PreToolUse`, matcher `Bash`.
- **`record-remove-check.py`** denies a shell command that would remove, truncate, or replace an
  existing, non-empty working-record file under a configured store folder. It checks the filesystem
  before the command runs and names the file and its size. It covers `rm` (including recursive
  removal), truncating redirections, plain two-operand `cp` and `mv` onto a file, `truncate -s 0`,
  and `tee` without options. It also checks helper-session calls.
  Event: `PreToolUse`, matcher `Bash`.
- **`char-policy-write.py`** denies a file write that would add a character your repository's character
  policy forbids, in a file that policy covers. The policy is the data file `.aiqt/char-policy.json` at the
  repository root, the one the pack's CI gate `tools/check_no_dashes.py` reads: it lists the characters, a
  name for each, optional advice, and the folders, file suffixes and files in scope. The hook names no
  character of its own; this repository's policy forbids the en dash and the em dash in Markdown and in the
  hook files. An edit is denied only when it adds more of a character than it removes, so a file that
  already holds one can still be edited, and a whole-file write is compared with the file it replaces. The
  reason names each character by its code point (`U+` and four hex digits) and quotes the policy's advice.
  A policy file that cannot be used, or an existing file it cannot read, gets a note and the write goes
  ahead; the CI gate still checks the file. Once its root is set, every other call it cannot evaluate (a
  payload it cannot read in full within 2 seconds as strict UTF-8 JSON without `NaN`, `Infinity` or a key
  repeated in one object, a field
  of the wrong type, a path it cannot resolve, such as one through a symbolic link loop, a root that is not
  an absolute path, does not exist or is not a directory, a launch with an unknown command-line argument,
  or an internal error) also goes ahead with a note naming the reason. It checks the form of a call's
  `file_path`, then the fields the tool needs, and only then resolves the path, compares it with the root
  and reads the policy file, so a malformed call gets that note wherever it points and whether or not the
  root holds a policy file. Once its root is set, it stays silent only for a tool other than the three it
  checks, and for a well-formed call that it checked and found clean, that targets a file outside the root
  or outside the policy's scope, or whose root holds no policy file; while its root is unset or empty, it
  is silent for every call. It does not skip worker processes.
  Event: `PreToolUse`, matcher `Write|Edit|MultiEdit`.
- **`pattern-self-match.py`** denies a shell command that would stop or wait on processes chosen by
  `pgrep -f` or `pkill -f` when the pattern is plain text in the command itself. The shell that runs the
  command carries that text in its own command line, so `pkill -f worker/` signals that shell, and
  `while pgrep -f worker/ >/dev/null; do sleep 2; done` never finishes by itself. It backs the rule that an
  action binds to its explicit target, not to ambient context
  ([the rule text](../.claude/rules/aiqt/10-INTEG-explicit-binding-over-ambient-context.md)); its reason
  points to stopping work by the process id or group recorded at launch. It denies only a closed set of
  shapes, listed in its docstring. A command outside that set that names a process matcher (`pgrep`,
  `pkill`, `killall`, `pidof`, or `grep` beside `ps`) whose pattern is text the command holds is allowed
  with a note (a `systemMessage`, with no permission decision); it never asks. It also checks
  helper-session calls.
  Event: `PreToolUse`, matcher `Bash`.
- **`constraint-reread.py`** reminds the assistant of standing constraints after a context compaction and
  refuses the turn end while no re-read entry is recorded, up to a loop cap after which the stop is allowed
  (while it can keep its state: if its state lock cannot be taken at all, it reminds of a compaction once
  and refuses no turn end, as its limits below say); it cannot make the assistant honour them. When Claude Code reports a compaction, it records the time and reminds the assistant of the constraints your durable
  record lists, on every prompt, until the record holds a `Constraints-reread:` entry dated after the
  compaction; meanwhile it refuses each turn end, at most three times in a row before it allows the stop
  with a warning, so a turn end is not held past the cap, unless the host sends `stop_hook_active` false
  on a turn end that continues a refusal. The count is kept in its state
  file, so the cap holds when the `stop_hook_active` input field is absent or true; it restarts only at
  the next prompt you submit (`UserPromptSubmit`), a new compaction, or a turn end whose
  `stop_hook_active` is explicitly false, which the hook trusts as a new turn (so the cap does not hold
  against a host that sends false on a turn end that continues a refusal). If the hook's state location
  cannot be examined, it keeps reminding but says that no entry can clear the reminder, and it does not
  refuse the turn end. It detects a compaction by the platform's own markers: the `SessionStart` input field
  `source` with the value `compact`, and the `PreCompact` event, both described in the
  [Claude Code hooks reference](https://code.claude.com/docs/en/hooks). Events: `SessionStart`,
  `PreCompact` (optional), `UserPromptSubmit`, and `Stop`.
- **`rerun-pass-check.py`** keeps an earlier failure in view after a rerun passes. After a CI rerun call
  that succeeds (`gh run rerun`, `glab ci retry`), or a test or check command that failed and then passed
  with the same words, each as written with its quoting, and no recorded change between (a certain rerun;
  a check whose words hold an expansion, such as `$LINENO` or `tests/test_*.py`, is never compared), it
  adds a note to the assistant's context; at turn end it refuses, at most twice in a row, a final
  message that calls a pass conclusive without naming the earlier failure. The count is kept in its
  state file, so the cap holds when the `stop_hook_active` input field is absent or true; it restarts
  only at the next prompt you submit (`UserPromptSubmit`), a final message that names a disclosure word,
  or a turn end whose `stop_hook_active` is explicitly false, which the hook trusts as a new turn (so the
  cap does not hold against a host that sends false on a turn end that continues a refusal). A state
  file that is missing, unreadable or malformed gives an unknown count, never 0. While the state file
  cannot be read or is malformed, a turn end whose final message calls a pass conclusive, or that has no
  final message, is allowed with a warning that the hook's state file could not be read. Once it is
  missing, or a tool call has written back a lost or unreadable one, until one of those restarts a
  conclusive turn end is allowed with a warning instead of refused only while a rerun seen since is
  outstanding; any other is silent, and a lost record of earlier reruns is silent (a missed refusal).
  Events: `PostToolUse` and
  `PostToolUseFailure` (matcher `Bash|Write|Edit|MultiEdit|NotebookEdit`), `UserPromptSubmit`, and
  `Stop`. Its shell reading
  is exact only for a small closed grammar (listed in its docstring) and for commands of at most 8192
  characters. Inside it, a CI rerun is missed only when its words come into existence when the command
  runs, or bash runs `gh` under another name (an expansion that has a value, a tilde expansion such as
  `~`, `~+` or `~-`, which bash expands though the hook reads `~` as a plain character, a pathname
  expansion, an alias, a shell function, a hashed command name from `hash -p`, or a command string handed
  to another program), and a simple command that names `gh`, `run` and `rerun` in order without running
  them is noted anyway (a false note, and a false refusal when its call succeeds). It reads an exit
  status only from an integer exit-code field of the tool response, where one is present, and that status
  is the whole call's: a later command, `|| true`, a background `&`, a `!` or an `if` or `while`
  condition can hide a `gh` call the server refused, with or without `pipefail` set, and so can a pipe
  under default bash options, so `gh run rerun 7 2>&1 | tail -5` exits 0 and is kept as certain (a false
  refusal); with `pipefail` set, that pipe exits nonzero and gets only the uncertain note. The note for a
  CI rerun call that is not reported as failed says only that and asserts neither a rerun nor an earlier
  failure; the refusal asks the assistant to state any earlier failure, and neither it nor the warning
  given when a refusal is capped asserts a CI rerun (of CI, each says only that a command naming a CI
  rerun was not reported as failed). An uncertain
  rerun gets a note and is never kept, so it never brings a refusal: every command outside the grammar
  (including `echo "$(date)"`, a here-document, and any longer command), a possible CI rerun by
  construction because the hook could not parse it, even when it fails; and a CI rerun call that fails,
  since the rerun may or may not have started. That note says that a CI rerun cannot be ruled out and
  asserts no rerun and no earlier check failure. A blank command gets no note. This is a deliberate cost:
  an off-grammar command that reruns nothing, such as a here-document commit, gets a false note, and a
  real CI rerun written outside the grammar, a failed CI rerun call that did start a rerun, or an
  off-grammar check that fails and then passes gets only its note, with no refusal. A state file written
  by an earlier revision of the hook is discarded, so none of its flags arms a refusal, and one of the
  current revision holding a flag of a kind it never keeps is malformed. Read-only is
  decided only inside the grammar: `env` with any argument, an assignment prefix, a command word holding
  an expansion, and any output redirection whose target is not the unquoted word `/dev/null` count as a
  change; an input redirection and a descriptor duplication or close (`<f`, `>&2`, `>&-`) write nothing.

## Integrity

Every published hook file is listed below with its SHA-256 checksum and a link to the file itself. The
files are served from this repository's main branch; for a raw download, use
`https://raw.githubusercontent.com/jposluns/guardrails/main/.preview/<file>`. The same values are in
[SHA256SUMS](SHA256SUMS), which lists bare file names in the format `sha256sum -c` reads.

| File | SHA-256 | Link |
|---|---|---|
| `char-policy-write.py` | `f022b91e16dbeec2f1ebe0f2841784296b6c5cc077dff369b10d64bd9756e4a9` | [char-policy-write.py](char-policy-write.py) |
| `clock-inject.py` | `e43a1df6603ddc9220bba2a89667f103f7cec124cc912b75415158eb39f6007d` | [clock-inject.py](clock-inject.py) |
| `constraint-reread.py` | `dcf1481b4bcea890663592e8b58ebb4596672f41c6e3489d5c30ff3567f414b0` | [constraint-reread.py](constraint-reread.py) |
| `future-stamp-write.py` | `c24c667eadb8c3395f5bc3dbe5ca40e538acc580121914d49a86ad2930327701` | [future-stamp-write.py](future-stamp-write.py) |
| `pattern-self-match.py` | `0c4a4244fb17920a8647d3d4845e3aafc5dc13ec983297178f464a3c687c6c6a` | [pattern-self-match.py](pattern-self-match.py) |
| `record-remove-check.py` | `24decb293ed1ed4355507e06e93035b1155a0d3aaa131f7cd78651a72dab9543` | [record-remove-check.py](record-remove-check.py) |
| `rerun-pass-check.py` | `e1c74fe3cea9912cd00dae7f95c4537979fea5d60055c3d08c46eb76bec0dd6e` | [rerun-pass-check.py](rerun-pass-check.py) |
| `stamp-truth-stop.py` | `d71423d0e4277e26dcc7ba56d119d0ebf31fc88e8379f8e6ba51aa24c8977f89` | [stamp-truth-stop.py](stamp-truth-stop.py) |
| `unbounded-wait.py` | `f84af2ed0158d6259bc933690f4292abd5187947e6b55db3b2ff54825f7de4e0` | [unbounded-wait.py](unbounded-wait.py) |
| `ungated-record.py` | `2cbfab2cfda66887c5f8db50d07466fded4bbbfdab39efd56197eee4ed41e3d0` | [ungated-record.py](ungated-record.py) |

What the checksum does and does not prove:

- The checksum is what proves the download is the file this page describes. The checksum, the
  SHA256SUMS file, and the hook itself are all served from this one repository, so a matching checksum
  proves the file arrived intact and matches this page. It does not prove the repository itself is
  uncompromised; for that, compare against a copy of this page you obtained independently, such as one
  from an earlier visit.
- Download from `jposluns/guardrails` only. The repository's quality gate checks that each row links to
  its own file, but not where a raw download URL points, so confirm the owner and repository by eye.

How the files are served: each file is served from the main branch as it stands, so a download always
gets the current version, and the checksum on this page changes in the same change as the file. To
install, download the file from its link (for a raw download, the main-branch URL above), check its
SHA-256 against this page, and run its self-test. The quality gate checks on every change that the table,
SHA256SUMS, and the files agree.

## Installing a hook

These steps are for an AI coding assistant to carry out, one hook at a time. Replace `<file>` and
`<checksum>` with the values from that hook's row in the integrity table. Stop at the first step that
fails and report it; do not work around a failed check.

1. Create the hooks directory and download the hook from the main branch:

   ```sh
   mkdir -p ~/.claude/hooks
   curl -fsSL 'https://raw.githubusercontent.com/jposluns/guardrails/main/.preview/<file>' -o ~/.claude/hooks/<file>
   ```

2. Check the checksum. The command prints `<file>: OK` only when the file matches the value on this page:

   ```sh
   (cd ~/.claude/hooks && echo '<checksum>  <file>' | sha256sum -c -)
   ```

   On macOS, which has no `sha256sum` by default, use `shasum -a 256 -c -` in its place; it prints the
   same `<file>: OK`.

   If it prints anything other than `<file>: OK`, delete the downloaded file and stop. A mismatch means
   the file is not the one this page describes.

3. Check that `python3 --version` reports at least Python 3.14, which the hooks require; if it does not,
   stop. Then run the hook's own self-test and require it to pass:

   ```sh
   python3 -I -S -B ~/.claude/hooks/<file> --self-test
   ```

   A nonzero exit or a reported failure means stop; do not switch the hook on. Some self-tests compare
   shared code with reference hook files. When those files are absent, the comparisons report skipped;
   the notes below identify the unpublished references for the hooks that carry them. Skipped is expected;
   failed is not.

4. Switch the hook on by adding an entry to the `hooks` section of Claude Code's `settings.json` (the
   user file `~/.claude/settings.json`, or a project's `.claude/settings.json`). If `settings.json`
   already has a `hooks` section, add to it rather than replacing it, and keep every existing entry and
   permission. If an event such as `PreToolUse` already has an array, append the selected entries to that
   array; do not add a second key with the same event name. Register each hook once: if a later version
   of the pack's plugin provides the same hook, remove this entry so it does not run twice.

   Use this launch line for each of the ten hooks:

   ```sh
   /bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B "/ABSOLUTE/PATH/TO/<file>"'
   ```

   - `-I` stops a stray file beside the hook, or a Python environment variable, from changing how Python
     loads it; `-S` skips site packages, which these hooks do not use; `-B` writes no bytecode cache.
   - The `[ -d ... ]` tests are a launch guard: if any standard stream is a directory, Python would fail
     before the hook's own code could fail open, so the guard skips the hook instead.
   - For `ungated-record.py`, `unbounded-wait.py`, `record-remove-check.py`, and `pattern-self-match.py`,
     use this guard in place of their docstrings' `REGISTRATION` line, which tests only stdin. This guard
     has a stricter launch
     condition: it also skips directory stdout or stderr. When none of the streams is a directory, it
     runs the same `python3 -I -S -B` command with stdin unchanged. The three clock hooks,
     `constraint-reread.py`, `rerun-pass-check.py`, and `char-policy-write.py` do not define a
     `REGISTRATION` constant; use this same guard for them.
   - Use the absolute path to the downloaded file. It sits inside double quotes, so a path with spaces
     works; the path must not contain `"`, `'`, `$`, a backtick, or a backslash. The hooks need `python3`
     on the `PATH` that Claude Code runs hook commands with. The hooks require Python 3.14 or newer.
     Step 3 checks the installing shell's `python3`, which can differ from the one on Claude Code's
     `PATH`; each hook also checks its own interpreter when it starts. On an older interpreter that can
     start the hook, each hook reads no input, writes one line beginning
     `error: <file> requires Python 3.14 or newer` to standard error, and exits. The write and its
     flush are best-effort: the exit status does not depend on them (each hook exits without the
     interpreter's own exit-time stream flush, so a stream whose flush fails cannot replace the
     exit), and the refusal holds even when standard error is unavailable or failing.
     Claude Code reads the exit by event: for `clock-inject.py` (`PostToolUse`,
     `PostToolUseFailure`) the exit is 2 and the tool has already run, so the line only reaches the
     assistant and nothing is blocked; for the six `PreToolUse` hooks the exit is 2 and every matching
     tool call is denied; for `stamp-truth-stop.py` (`Stop`), `constraint-reread.py` (`SessionStart`,
     `PreCompact`, `UserPromptSubmit`, and `Stop`), and `rerun-pass-check.py` (`PostToolUse`,
     `PostToolUseFailure`, `UserPromptSubmit`, and `Stop`) the exit is 1, a non-blocking error, so no reminder or note is
     added and every stop goes ahead unchecked (exit 2 would block the stop, and the hook's own block cap
     would never run, since the hook stops before its loop guard runs). An interpreter that cannot start
     the hook fails before its guard runs, with Python's own error instead of that line: an interpreter
     that predates the `-I` option rejects it and exits 2, and one that accepts `-I` but predates
     f-strings cannot compile the three clock hooks, which use them, and exits 1. Exit 1 is a
     non-blocking error on every event, so a `PreToolUse` hook then allows every tool call unchecked;
     exit 2 on `Stop` blocks the stop, and the hook's own block cap never runs, and exit 2 on
     `UserPromptSubmit` blocks the prompt. If you see any of these errors, upgrade Python or remove the
     hook's entry.
   - In JSON, each `"` inside the command is written `\"`, as in the entries below. The `timeout` value is
     the most seconds Claude Code lets one run of the hook take.

   This combined example shows the ten hooks. Copy only entries for hooks you have downloaded,
   checked, and tested. `clock-inject.py` needs both `PostToolUse` and `PostToolUseFailure`, with no
   matcher (all tools); `stamp-truth-stop.py` uses `Stop`, with no matcher. On `PreToolUse`,
   `future-stamp-write.py` matches file writes and shell commands, `char-policy-write.py` matches file writes
   only, and the other four match `Bash`.
   `constraint-reread.py` uses `SessionStart` (matcher `compact`), `PreCompact`, `UserPromptSubmit`, and
   `Stop`; `rerun-pass-check.py` uses `PostToolUse` and `PostToolUseFailure` (matcher
   `Bash|Write|Edit|MultiEdit|NotebookEdit`), `UserPromptSubmit`, and `Stop`.

   ```json
   {
     "hooks": {
       "PostToolUse": [
         { "hooks": [ { "type": "command", "timeout": 10, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/clock-inject.py\"'" } ] },
         { "matcher": "Bash|Write|Edit|MultiEdit|NotebookEdit", "hooks": [ { "type": "command", "timeout": 30, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/rerun-pass-check.py\"'" } ] }
       ],
       "PostToolUseFailure": [
         { "hooks": [ { "type": "command", "timeout": 10, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/clock-inject.py\"'" } ] },
         { "matcher": "Bash|Write|Edit|MultiEdit|NotebookEdit", "hooks": [ { "type": "command", "timeout": 30, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/rerun-pass-check.py\"'" } ] }
       ],
       "SessionStart": [
         { "matcher": "compact", "hooks": [ { "type": "command", "timeout": 30, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/constraint-reread.py\"'" } ] }
       ],
       "PreCompact": [
         { "hooks": [ { "type": "command", "timeout": 30, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/constraint-reread.py\"'" } ] }
       ],
       "UserPromptSubmit": [
         { "hooks": [ { "type": "command", "timeout": 30, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/constraint-reread.py\"'" } ] },
         { "hooks": [ { "type": "command", "timeout": 30, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/rerun-pass-check.py\"'" } ] }
       ],
       "Stop": [
         { "hooks": [ { "type": "command", "timeout": 30, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/stamp-truth-stop.py\"'" } ] },
         { "hooks": [ { "type": "command", "timeout": 30, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/constraint-reread.py\"'" } ] },
         { "hooks": [ { "type": "command", "timeout": 30, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/rerun-pass-check.py\"'" } ] }
       ],
       "PreToolUse": [
         { "matcher": "Write|Edit|MultiEdit|Bash", "hooks": [ { "type": "command", "timeout": 30, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/future-stamp-write.py\"'" } ] },
         { "matcher": "Write|Edit|MultiEdit", "hooks": [ { "type": "command", "timeout": 30, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/char-policy-write.py\"'" } ] },
         { "matcher": "Bash", "hooks": [ { "type": "command", "timeout": 30, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/ungated-record.py\"'" } ] },
         { "matcher": "Bash", "hooks": [ { "type": "command", "timeout": 30, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/unbounded-wait.py\"'" } ] },
         { "matcher": "Bash", "hooks": [ { "type": "command", "timeout": 30, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/record-remove-check.py\"'" } ] },
         { "matcher": "Bash", "hooks": [ { "type": "command", "timeout": 30, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/pattern-self-match.py\"'" } ] }
       ]
     }
   }
   ```

   The self-tests for `ungated-record.py`, `unbounded-wait.py`, `record-remove-check.py`, and
   `pattern-self-match.py` include byte-identity checks against `block-bare-detach.py` and (all but
   `pattern-self-match.py`) `inplace-edit-verify.py`, two reference hooks not yet published here. Those
   checks report `SKIPPED` until the reference files are available; skipped is not a pass. The
   `record-remove-check.py` differential check and the `pattern-self-match.py` check that bash accepts
   its deny examples also report `SKIPPED, no trusted bash` when they cannot find a trusted root-owned
   `/usr/bin/bash` or `/bin/bash`.
   The `char-policy-write.py` policy validator is generated from the marked region of the CI gate
   `tools/check_no_dashes.py` by `tools/gen_char_policy.py`. The hook's self-test compares that region byte
   for byte with the gate's, and a sample of policies and its scope test with the gate. The hook's self-test
   H12 walks both files' module-level statements, compound statements' bodies included, and fails when one
   outside the copied region binds a name the region binds or reads, or `__builtins__`, at module scope, or a
   def or class body outside it declares one global; or when one makes an attribute or item store (an
   assignment, an augmented or annotated assignment, a for, with or comprehension target) or deletion, at
   module scope, in a class body or in a def's or lambda's decorators or defaults, whose target chain starts
   from such a name, `json`, `builtins`, `sys.modules`, this module or an alias of one of them.
   Conservatively, whatever the stored value, it also fails on a store whose chain starts from anything but a
   name (a call such as `__import__("json")`, `globals()` or `logging.getLogger("app")`, a conditional
   expression) or has a link (an attribute or a constant string key) named `sys`, `json`, `builtins` or
   `modules`. An alias is a name bound by one of these forms: an import of `json`, `builtins` or `sys`, of a
   submodule of one or of a name from one (`from sys import modules` binds an alias of `sys.modules`); an
   import of this module (`import __main__`, or the gate's module `check_no_dashes` imported under any name);
   a from-import from this module, whose name is an alias of the name it imports; and a for, with,
   comprehension or match target whose source is such a name or alias, `sys.modules` (`sys` or an alias of
   `sys`, then `.modules`), or an item of `sys.modules`, which counts as this module. The source is the
   iterable, the context expression or the subject and, recursively, each element of a tuple, list or set
   display, the element of a comprehension and each positional argument of a call but a bare name of a builtin
   function or class that the file does not bind (`map(list, _rows)`), never the function called or a keyword
   argument. Scope follows Python's rules: a binding is module-level only at module scope or in a def or class
   body whose own scope declares the name global (an enclosing def's declaration does not count); any other
   binding is local to its def or class body, a comprehension target to its comprehension and a nonlocal name
   to the enclosing def, and none aliases the module-level name of the same spelling. A name in a source or a
   store's chain counts as the binding its scope reads: its own; for a name its scope does not bind, the
   nearest enclosing def's that binds it (class bodies skipped) or else the module's; and in a class body that
   binds the name, which can read it before binding it, conservatively both its own and the module's, never an
   enclosing def's. Every other aliasing form is out of scope; diff review is the control. Ordinary stores are
   not flagged: `sys.path[0] = ...`, an item of `os.environ` or of a module-level dict under a key not named
   `sys`, `json`, `builtins` or `modules`, a store to an attribute not so named of any other name
   (`_Options.verbose = True`), and a store whose chain has no link so named and starts from a target whose
   source is none of those (`for _row in sorted(_rows, key=len): _row[0] = 2`). That walk catches accidental
   drift between the two copies; it is not a defence against a deliberate edit that replaces behaviour through
   a path the walk does not model. Examples, not a complete list: an alias made by plain assignment or a
   walrus (`m = sys.modules[__name__]`, then `m.validate_policy = ...`) or by a target over a call's result
   (`for m in (importlib.import_module("__main__"),): ...`); an item key that is not a constant string; a call
   such as `setattr` or `exec`; an in-place method call such as `globals().update`, `sys.modules.update` or
   `json.__dict__.update`; a store in a function or lambda body; reflective access through an object the walk
   cannot name; another module patching the file; and a module that shadows a standard library module, such as
   a `json.py`. The gate imports `json` before it puts `tools/` on `sys.path`, which keeps out a
   `tools/json.py` under `python3 -I`, as CI runs it, but not in a run without `-I`. Diff review is the
   control for a deliberate edit; the hashes recorded in `SHA256SUMS` and the release manifest
   `.aiqt/manifest.toml` let an installer or a release check detect a shipped copy that differs from the
   reviewed one. It finds the gate at `../tools/` from the hook's folder, as in a checkout of this repository;
   installed on its own, that comparison reports skipped.

5. Configure the hook with the environment variables in the next section, then start a new Claude Code
   session so the settings are read.

6. Smoke-test the live hook. For `clock-inject.py`, run any command in the new session (for example
   `true`) and confirm a `CLOCK (read by hook, authoritative):` line reaches the assistant's context. For
   the other nine, a passing self-test in step 3 is the check; they stay silent until they see something
   to flag.

### A note on hooks that record authority

Some hooks, though none of the ten above, need a line in a durable record to switch on or to grant an
exception, for example an entry saying that you, the maintainer, approved something. Expect your assistant
to decline to write such a line itself, even when your permission settings would allow the write: a record
of your own authority is not something it should author on your behalf, and permission allow rules have
not been observed to override that refusal. Make that one write yourself, for example as a shell command
you type directly (in Claude Code, a line starting with `!`).

## Configuration

The hooks read their settings from environment variables. Each feature has its own off state, and nothing
beyond these variables is assumed. Each `AIQT_` variable except `AIQT_CHAR_POLICY_ROOT` also accepts an
older spelling with the prefix `ORCH_` (for example `ORCH_STORE_ROOT`), read only when the `AIQT_` one is
unset, so "unset" below means both spellings are unset. The worker skip likewise also honours `ORCH_WORKER=1` and any value of
`ORCH_VERIFY_OWNER`; if your environment sets either for another purpose, the hooks stay silent there.

- The current time needs no setting. `clock-inject.py` always reads it from the clock and injects it, and
  `stamp-truth-stop.py` always compares zoned timestamps against it.
- With no store configured (`AIQT_STORE_ROOT` unset), no write is inspected for a future date and no
  record removal is inspected: `future-stamp-write.py` and `record-remove-check.py` do nothing.
- With no lease configured (`AIQT_LEASE_FILE` unset), no elapsed time is reported or compared: the clock
  line carries no elapsed segment, and the elapsed-footer check is off.

- **`ungated-record.py`** needs no store or lease setting. It skips helper-session calls and the worker
  processes described above. For a deliberate record that is not a completion claim, end the command
  with a real, unquoted shell comment such as `# record-ok: timing log, not a result`.
- **`unbounded-wait.py`** needs no store or lease setting. It skips helper-session calls and the worker
  processes described above. For a deliberate unbounded watcher, end the command with a real, unquoted
  shell comment such as `# wait-ok: a watcher stopped by hand`.
- **`record-remove-check.py`** uses `AIQT_STORE_ROOT`: empty and relative entries, and entries resolving
  to `/`, are ignored; a set but empty value disables it even if the older spelling is set. Store paths
  follow symbolic links. It skips the worker processes described above, but still checks helper-session
  calls. For an intended destruction, put `# record-rm-ok: <reason>` after the last command token on the
  same line, separated by a blank, with a non-empty reason and nothing but whitespace after the comment.
  The comment records an attestation; it does not prove that the file was read or can be restored.
- **`char-policy-write.py`** uses `AIQT_CHAR_POLICY_ROOT`, which has no older spelling and no default:
  unset or empty, the hook does nothing, and with no policy file under that root it allows every
  well-formed file write silently (a malformed call still gets a note).
  Set to a relative path, or to a path that does not exist or is not a directory, it checks nothing and
  says so in a note on every file write; so does a hook launched with any argument other than
  `--self-test` alone. It has no opt-out comment: to allow a character, write it in words, or narrow the
  policy's scope in a reviewed change to the policy file.
- **`pattern-self-match.py`** needs no store or lease setting. It skips the worker processes described
  above, but still checks helper-session calls. When selecting the shell that runs the command is
  intended, end the command with a real, unquoted shell comment such as
  `# self-match-ok: the shell is meant to stop too`. A `#` directly after `)` does not opt out. After a
  command substitution, as in `$(...)#`, bash reads no comment: the `#` continues the last word the
  substitution gives. After a subshell, as in `(...)#`, bash does read a comment, but this hook
  deliberately does not accept it as an opt-out (a hook policy), so that command gets a note.

The `record-ok`, `wait-ok`, and `self-match-ok` comments must begin a word and be the last non-blank
content of the command. Their reasons are optional; text inside quotes does not opt out.

| Variable | What it does |
|---|---|
| `AIQT_STORE_ROOT` | The folder or folders holding your working records, as absolute paths joined with `:`. The future-date and record-removal checks only look at files under these folders. |
| `AIQT_LEASE_FILE` | The absolute path to a small text file that marks when the current working session started. When it is set and valid, the hooks report and check how long the session has been running. |
| `AIQT_CHAR_POLICY_ROOT` | The absolute path to one repository root, for `char-policy-write.py`. The hook reads the policy file `.aiqt/char-policy.json` under it and checks only files inside it. |
| `AIQT_CONSTRAINT_RECORD` | The absolute path to the project's durable record of standing constraints, for `constraint-reread.py`; unset, empty, or relative, that hook does nothing. It reads `Constraint: <text>` lines as the constraints to name, and `Constraints-reread: <UTC time>` lines (written by the assistant from `date -u +%Y-%m-%dT%H:%M:%SZ` after re-reading) as re-read entries. |
| `AIQT_HOOK_STATE_DIR` | The absolute path to a folder for the per-session state of `constraint-reread.py` and `rerun-pass-check.py`. If unset, they use `$XDG_STATE_HOME/aiqt-guardrails`, else `$HOME/.local/state/aiqt-guardrails`. |
| `AIQT_HOOKS_WORKER` | Set to `1` only in a separate worker process that another program launches to produce output for it to read back (a batch verifier, say), to keep the hooks that read it out of that output; `char-policy-write.py` does not read it and checks worker processes too. Do not set it for a helper session started inside your own session: `future-stamp-write.py` deliberately still checks the record writes such a helper makes, and `clock-inject.py` still gives it the clock. |
| `G_REF_DIR` | Self-tests only: the folder containing reference hooks for the byte-identity checks in `ungated-record.py`, `unbounded-wait.py`, `record-remove-check.py`, and `pattern-self-match.py`. If unset or empty, they look beside the hook itself. Each missing reference makes its check report `SKIPPED`. |

The lease file marks the session's start on a field line of its own:

```
Active-session: <label>-YYYYMMDDTHHMMSSZ
```

where `<label>` is 1 to 32 letters or digits and the time is the session's start in UTC. Only the first
`Active-session` line is read, and the value `none` marks no active session. Write it from the clock at the
start of each session, never by hand:

```sh
printf 'Active-session: sess-%s\n' "$(date -u +%Y%m%dT%H%M%SZ)" > "$AIQT_LEASE_FILE"
```

## What each hook does not catch

Each hook is a best-effort guard, and each one states what it does not catch in the `RESIDUAL COVERAGE`
section of its opening docstring. Read that section before relying on a hook; in outline:

- **`clock-inject.py`** informs and enforces nothing: the assistant can still ignore or mis-copy the line.
  It reproduces the host clock faithfully, so a wrong host clock gives wrong readings, and elapsed time is
  only as right as the lease. It does not fire for a tool call rejected before it ran, or across a stretch
  of prose with no tool call.
- **`stamp-truth-stop.py`** checks only timestamps written with an explicit zone; an unzoned, 12-hour, or
  natural-language time is not checked. A fabricated time in a code span, a code block, a `>` quote line,
  or just after a scheduling word passes. A genuinely future time mentioned in plain prose without a
  scheduling word before it is blocked; the remedy is to add the word or put the time in a code span.
- **`future-stamp-write.py`** checks only folders you declare, and only ISO-like timestamp literals. A
  time in a quote or code span in a record, a time built from parts, or one just after a scheduling word
  passes, and some table layouts give false positives or misses, which the file lists.
- **`ungated-record.py`** checks recognized gates and literal completion claims in one straight-line
  shell command. It misses separate calls, scripts, nested shell strings, compound-command contents,
  background gates, unrecognized runners, claim words outside its fixed, case-sensitive uppercase list
  (which includes `PASS`, `DONE`, and `VERIFIED`), claims built at run time, and destinations held in
  variables.
  For example, `passed` and `OK` are not recognized claim words.
  It skips commands that inspect `$?`, `${?}`, or `PIPESTATUS`, turn on errexit or pipefail, or define a
  function. A status reference even in a comment can skip the check. Its approximate reading of
  `printf` and expansions can miss claims or flag text the shell would not write.
- **`unbounded-wait.py`** allows foreground loops, `for` and `select`, and most loops that read from
  their own input. It can still flag `while ! read` and `until read`, which can spin at end of input;
  reopening a file on every read is polling. It misses nested shell strings, scripts, function bodies,
  substitutions, watcher utilities such as `tail -f`, busy loops without sleep, and a single long sleep.
  Commands whose top-level stream holds `case` or `coproc` are allowed without being followed. It also
  misses sleeps run through unlisted launchers, such as `flock`, `xargs`, or `ssh`. A counter or clock
  marker is enough to allow a loop even if it never limits the wait. It does not verify that a
  foreground process ends when the tool's timeout expires.
- **`constraint-reread.py`** proves only that a re-read entry was written after the compaction, not that
  the record was read or a constraint honoured, and it names only the constraints written in the record.
  It sees a compaction only through the platform markers named above, so a context lost without one (a
  new session, a host without those events, a hook not registered for them) is not seen. Without a state
  folder or a session id it reminds once and then forgets, and a state file deleted after a compaction
  forgets that compaction: the reminder and the refusals stop silently. Its turn-end refusal is capped at
  three in a row, after which the stop is allowed with a warning, unless the host sends `stop_hook_active`
  false on a turn end that continues a refusal (each such turn end is refused again). A refusal whose count
  cannot be saved is allowed with a warning, also on an explicit false. Its state updates are serialized by
  a lock file beside the state file, and a call saves only while that lock file and the state file are the
  ones it locked and loaded. A call that cannot take the lock within two seconds saves nothing, and a turn
  end it would refuse is allowed with a warning that names the lock. A call whose lock file is deleted or
  replaced while it holds it (also one moved away and put back after another call saved), or whose state
  file another call saved meanwhile, saves nothing, and a turn end it would refuse is allowed with a warning
  that the refusal count cannot be saved (a deletion, replacement or save that lands in the few system calls
  between its last check and its write can still let one stale save through, and so can a save whose state
  file matches the loaded one in device, inode, size, modification time and change time, all five fields it
  compares, as when an inode number is reused within the filesystem's timestamp granularity). While the lock
  cannot be taken at all (a lock path
  that is a symbolic link or not a regular file, a lock file it cannot open, a filesystem that refuses
  `flock`, a platform without it), it keeps no state: a compaction is reminded once and then forgotten, and
  with no compaction saved before then every turn end passes silently (a missed reminder and a missed
  refusal); a compaction saved before then is still reminded, and each turn end is allowed with a warning. A
  state folder moved away or deleted during a session forgets the compaction silently, like a deleted state
  file. Once a record is declared, every call it handles creates its state folder and an empty lock file for
  the session, and nothing removes them. Without `UserPromptSubmit`
  registered
  and without a `stop_hook_active` field in the input, the count stays at the cap, so every later turn end
  until the next compaction is allowed with the warning (a missed refusal); a state file that cannot be
  parsed gives an unknown count with the same effect from its first turn end.
- **`rerun-pass-check.py`** sees only the listed CI rerun commands and recognized check commands run
  through the shell tool, with the same words, each as written with its quoting (a word quoted another way
  is a different check, and so is an operator spelled another way, such as a newline in place of `;`
  between two commands; blanks, comments, leading and trailing newlines and a final `;` do not count). A
  check whose words hold an expansion (a `$` expansion, or an unquoted `*`, `?` or `~`) is never compared
  with another run, so its fail-then-pass gets no note (a missed note, never a false one). A command is
  shown in a note as written, or, when it holds a tab, a newline or another control character (C0, DEL
  or C1), as one `$'...'` string with those characters escaped, and is cut after 160 characters. A rerun
  through a web page, a runner's own retry option, or a change made outside the tool calls it sees is
  missed or misread. A command its shell reader cannot parse (a here-document,
  ANSI-C quoting such as `$'...'`, a command substitution, a line continuation, or a command over 8192
  characters, notably) is always noted as a possible CI rerun, even when it fails, and counted as a change
  unless it fails, even when it reruns nothing (a false note). Such a command, and a CI rerun call that
  fails, is an uncertain rerun: it gets that note only and never a turn-end refusal, so a real CI rerun
  written that way, a failed call that did start a rerun, or an off-grammar check that fails and then
  passes is not refused (a missed refusal). A blank command gets no note. Inside its grammar, a rerun
  whose words come into existence only when the command runs (an expansion with a value, a tilde expansion
  such as `~`, `~+` or `~-`, an alias) or that runs `gh` under a hashed name (`hash -p`) is missed. The
  exit status it reads, only from an integer exit-code field where one is present, is the whole call's, so a
  refused `gh` call that a pipe (under default bash options, not under `pipefail`), `|| true`, a later
  command, a background `&`, a `!` or an `if` or `while` condition (each also under `pipefail`) can hide
  is kept as a CI rerun call that was not reported as failed (a false refusal). Its turn-end check reads
  only the final message against fixed phrase lists: any disclosure word such as `flaky` or `rerun`
  clears it wherever the word stands, even inside a denial such as `I did not rerun CI`, whether or not
  the failure is recorded; it does not record or investigate the failure itself. It fails open on its own
  failure, by design for an advisory hook: an internal error or an unwritable stdout gives no note and no
  refusal; an unreadable state file is read as no earlier runs, so a local rerun across it is missed (a
  turn end that reads it is allowed with a warning that the state could not be read when the final message
  calls a pass conclusive or is missing), but the current call's own CI rerun or possible CI rerun note is
  still given. A refusal whose count cannot be saved is allowed with a warning, also on an explicit
  `stop_hook_active` false. Its state updates are serialized by a lock file beside the state file, and a run
  saves only while that lock file and the state file are the ones it locked and loaded. A run that cannot
  take the lock within two seconds saves nothing, and a turn end it would refuse is allowed with a warning
  that names the lock. A run whose lock file is deleted or replaced while it holds it (also one moved away
  and put back after another run saved), or whose state file another run saved meanwhile, saves nothing, and
  a turn end it would refuse is allowed with a warning that the refusal count cannot be saved (a deletion,
  replacement or save that lands in the few system calls between its last check and its write can still
  let one stale save through, and so can a save whose state file matches the loaded one in device, inode,
  size, modification time and change time, all five fields it compares, as when an inode number is reused
  within the filesystem's timestamp granularity). While the lock cannot be taken at all (a lock path that is
  a symbolic link or not a regular
  file, a lock file it cannot open, a filesystem that refuses `flock`, a platform without it), it keeps no
  state: it only notes after each tool call, and with no state saved before then every turn end passes
  silently (a missed refusal); the outstanding reruns of a state saved before then still bring a warning. A
  state folder moved away or deleted during a session is read as no state, so the next turn end passes
  silently (a missed refusal). Nothing removes its state and lock files, one of each per session. A state
  file written by an
  earlier revision of the hook is read as
  no earlier runs too, so
  none of its flags arms a refusal. The state file is trusted: a hand-edited state of the current revision
  whose flags are of the kinds it keeps still arms a refusal. A state file that is missing (first use
  cannot be told from a lost file), unreadable or malformed gives an unknown count. While it cannot be read
  or is malformed, a conclusive turn end, or one with no final message, is allowed with a warning that the
  state could not be read. Once it is missing, or a tool call has written back a lost or unreadable one,
  until a prompt you submit, a disclosure word or an explicit `stop_hook_active` false, a conclusive turn
  end is allowed with a warning instead of refused only while a rerun seen since is outstanding; any other
  is silent, and a lost record of earlier reruns is silent (a missed refusal). Without `UserPromptSubmit`
  registered and
  without a `stop_hook_active`
  field in the input, once it has refused twice in a row every later conclusive turn end is allowed with
  the warning until a final message names a disclosure word (a missed refusal).
- **`char-policy-write.py`** sees only the `Write`, `Edit` and `MultiEdit` tools: shell commands
  (redirections, here-documents, in-place `sed`, `tee`, one-line interpreter scripts), generator scripts,
  notebook edits, other tools and other harnesses are not checked, nor is a session without the hook, so
  the CI gate is the only check on those writes. It is off until you install it and set its root. A
  character written as an HTML entity is not decoded. Moving an existing character within one edit is
  allowed, and a `MultiEdit` call where a later edit removes what an earlier one added is denied. A hard
  link, a case-insensitive or Unicode-normalizing filesystem, or a symbolic link inside the root can make
  its scope differ from the gate's. A malformed policy file lets every write through with a note, while the
  gate fails on it. A reviewed edit to the policy file narrows the hook and the gate together, and only
  review guards that edit. The repository's other character checks, with their own fixed lists, are not
  read. The file can change between the check and the write, and a payload over 64 MiB is allowed with
  a note. The reason writes each policy character as `U+` and hex digits, so a policy that forbids an
  ASCII character, such as `U` or a digit, still sees it in the reason. Not verified here: if Claude Code's
  `Edit` rewrites straight quotes in the new text to the curly quotes the file uses, the file gains
  characters the hook never saw, so a policy that forbids curly quotes can miss them until the CI gate
  runs. Also not verified: if Claude Code writes a lone surrogate (an escaped `\ud800` in the new text)
  through a UTF-8 encoder that replaces it with U+FFFD, the file gains a U+FFFD the hook did not count, so a
  policy that forbids U+FFFD can miss it until the CI gate runs.
- **`record-remove-check.py`** checks only supported shell forms and configured stores. It allows
  absent or empty files and files within a store's `.git` directory, though removing that directory
  whole is checked. It misses editor tools, scripts, nested shell strings, `find`, `rsync`, git
  commands, and optioned `cp`, `mv`, or `tee`. Unknown paths or directory changes, unreadable targets,
  and files beyond its scan budgets are not fully checked. Moving a record out of the store is allowed.
  A file created or filled after the check can be lost without a warning. It can deny unreachable
  commands and files that have a good backup; it does not check for a restore path.
- **`pattern-self-match.py`** denies only its closed set of shapes, and each deny rests on premises
  observed on one host: that the Bash tool runs each command inside a shell whose own command line holds
  the command's text (its docstring gives a one-line `pgrep -c` probe to re-check a host), and how procps
  matches. A shell function or alias that shadows a command word the hook accepts (`kill`, `pkill`,
  `pgrep`, `xargs`, `echo`, `sleep`, `ls`, `date`, `pwd`, `true` or `:`), an alias named for a reserved
  word (`for`, `while`, `until`, `do`, `done`, `if`, `then`, `fi`, `case`, `esac` or `!`; one named for a
  loop word turned the loop into a syntax error in bash 5.3.9, and one named `!` ended an
  `until ! pgrep ...` loop at once), or shell state set before the command (an `IFS`
  without a newline, or a loop variable made readonly), can change what a denied command runs or keep it
  from running. A denied shape may end with one `|| CMD` or `&& CMD` whose `CMD` is a
  simple `echo`, `sleep`, `pwd`, `true`, `:`, `date` or `ls` (the `kill` in a `for` loop body likewise,
  with that body's commands), and its signalling command with `>/dev/null`, `2>/dev/null`, `&>/dev/null`
  or `2>&1` after its last word, with or without a blank before it. A word of digits alone whose value
  is at most 2147483647 needs the blank: bash reads `pkill -f 15>/dev/null` as a redirection of
  descriptor 15, so pkill gets no pattern, and the hook gives that command the note (`pkill -f 15 >/dev/null`,
  `pkill -f "15">/dev/null` and `pkill -f 2fa>/dev/null` are denied). bash 5.3.9 reads a larger run of
  digits, such as `2147483648`, as a word, so `pkill -f 2147483648>/dev/null` is denied. Everything else
  gets a note at most:
  compound commands, other `&&` and `||` lists, `ps | grep` pipelines, `sudo` or `exec` prefixes,
  `killall`, `pidof`, nested shell strings, other redirections, `printf`, `cd`, an `echo` with an option,
  a pattern holding a blank, an all-caps loop variable, and sleeps that add up to more than 60 seconds
  before the shape or in a `for` loop shape's body (a `while` or `until` wait loop's body is not counted:
  its `pgrep` runs first). A
  pattern built when the command runs (holding `$` or a backtick) gets nothing. Its notes come from an
  approximate reading of the words, so some are missed or given wrongly, and a matcher word counts even
  as an argument. Whether Claude Code shows a `PreToolUse` `systemMessage` to the assistant, or only to
  you, has not been checked.

`ungated-record.py`, `unbounded-wait.py`, and `record-remove-check.py` also allow commands over 64 KiB or
input they cannot follow. Their docstrings describe further parsing limits and work budgets. All three skip
verification worker processes; `ungated-record.py` and `unbounded-wait.py` also skip helper-session calls.
`pattern-self-match.py` instead adds a note for a command over 64 KiB, or one it cannot read, that names
a process matcher, and for its own internal error. It also adds a cannot-evaluate note for a hook payload
it cannot read: a closed stdin or a read error, more than 16 MiB in all (every byte through the end of
the input counts, blanks after the JSON too), input whose end the hook does not read within 2 seconds (a
complete JSON prefix followed by a pause is not taken as the whole input), bytes other than JSON blanks
(space, tab, carriage return, newline) after the JSON, input that is not JSON in strict UTF-8 (bytes
that are not valid UTF-8, encoded surrogates, a byte order mark, UTF-16 and UTF-32 included, and the
constants NaN, Infinity and -Infinity, which JSON does not have), JSON that is not an object, or a Bash
call without a string command. A launch with an argument list it does not accept (only a lone
`--self-test` is accepted) gets a note that it checked nothing, written before it reads its input,
except in a verification worker process, where it writes only its skip line to standard error. The
hook reads its clock again after every wait and every read, so input that ends after the 2 seconds gets
the note even when the system runs the hook late; a hook run late can take longer than 2 seconds to
give it. It writes nothing when
standard input is a directory (its launch line exits before Python starts), for another tool or event,
for an empty command, and for a command it allows without a note. It skips verification worker processes
and checks helper-session calls.

## Status and retirement

This is a preview channel. When a hook here becomes part of the pack's plugin, it is removed from this
directory and its row is replaced by a pointer to where it now lives. When every hook has moved, this
directory is removed.

A change to any hook here ships with new checksums on this page and a matching SHA256SUMS, all in the
same change.
