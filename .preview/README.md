# Hooks preview

This directory is a preview channel for AIQT Guardrails hooks that are not yet part of the pack's plugin.
Each hook is one self-contained Python file that you can download, check, test, and switch on in Claude
Code by hand. This page is written so that you can hand it to your AI coding assistant and ask it to
install a hook for you: every step below is a command it can run, and every check tells it when to stop.

Six hooks are published here, each listed with its checksum and link in the integrity table below.
A hook without a row in that table is not available here, and the install steps do not apply to it.

## What these hooks are

The three clock hooks, `clock-inject.py`, `stamp-truth-stop.py`, and `future-stamp-write.py`, back the
rule that a current timestamp is read from the clock, never recalled or guessed
([the rule text](../.claude/rules/aiqt/10-ACCUR-timestamp-from-clock.md)). The other hooks guard completion
records, background polling loops, and existing working-record files. Each one is a discipline
guard against accidental drift, not a security boundary, and each one fails open: if the hook hits an
error or input it cannot evaluate, it gets out of the way rather than blocking your work. Each file states
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

## Integrity

Every published hook file is listed below with its SHA-256 checksum and a link to the file itself. The
files are served from this repository's main branch; for a raw download, use
`https://raw.githubusercontent.com/jposluns/guardrails/main/.preview/<file>`. The same values are in
[SHA256SUMS](SHA256SUMS), which lists bare file names in the format `sha256sum -c` reads.

| File | SHA-256 | Link |
|---|---|---|
| `clock-inject.py` | `5f7550e8a1afa2c2db6ffe94250de0b740d9f1c92a5e2b1ce52f88b8a1e4775e` | [clock-inject.py](clock-inject.py) |
| `future-stamp-write.py` | `356378740e7e21f531b8c1e2eb5220a2612f48c0027305e2f9d3ecf109e2933d` | [future-stamp-write.py](future-stamp-write.py) |
| `record-remove-check.py` | `53d6b309f10c5ac93c69a492e1440125ce528d138fb1d966963e5bec7c948f2e` | [record-remove-check.py](record-remove-check.py) |
| `stamp-truth-stop.py` | `f6365d467b66de32bd1abc573c852f6e9da6aec19bef0284c44a08eb2c159b55` | [stamp-truth-stop.py](stamp-truth-stop.py) |
| `unbounded-wait.py` | `06129bcf4fe5ff65100a55ddb35d8e51db927e33ab41311dd6c4785929937fdd` | [unbounded-wait.py](unbounded-wait.py) |
| `ungated-record.py` | `286295b9949eda2a6e9bcc919095d9bf14e181578c5e5085381c6106d6a934fd` | [ungated-record.py](ungated-record.py) |

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

3. Run the hook's own self-test and require it to pass:

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

   Use this launch line for each of the six hooks:

   ```sh
   /bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B "/ABSOLUTE/PATH/TO/<file>"'
   ```

   - `-I` stops a stray file beside the hook, or a Python environment variable, from changing how Python
     loads it; `-S` skips site packages, which these hooks do not use; `-B` writes no bytecode cache.
   - The `[ -d ... ]` tests are a launch guard: if any standard stream is a directory, Python would fail
     before the hook's own code could fail open, so the guard skips the hook instead.
   - For `ungated-record.py`, `unbounded-wait.py`, and `record-remove-check.py`, use this guard in place
     of their docstrings' `REGISTRATION` line, which tests only stdin. This guard has a stricter launch
     condition: it also skips directory stdout or stderr. When none of the streams is a directory, it
     runs the same `python3 -I -S -B` command with stdin unchanged. The three clock hooks do not define
     a `REGISTRATION` constant; use this same guard for them.
   - Use the absolute path to the downloaded file. It sits inside double quotes, so a path with spaces
     works; the path must not contain `"`, `'`, `$`, a backtick, or a backslash. The hooks need `python3`
     on the `PATH` that Claude Code runs hook commands with.
   - In JSON, each `"` inside the command is written `\"`, as in the entries below. The `timeout` value is
     the most seconds Claude Code lets one run of the hook take.

   This combined example shows the six hooks. Copy only entries for hooks you have downloaded,
   checked, and tested. `clock-inject.py` needs both `PostToolUse` and `PostToolUseFailure`, with no
   matcher (all tools); `stamp-truth-stop.py` uses `Stop`, with no matcher. On `PreToolUse`,
   `future-stamp-write.py` matches file writes and shell commands, and the other three match `Bash`.

   ```json
   {
     "hooks": {
       "PostToolUse": [
         { "hooks": [ { "type": "command", "timeout": 10, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/clock-inject.py\"'" } ] }
       ],
       "PostToolUseFailure": [
         { "hooks": [ { "type": "command", "timeout": 10, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/clock-inject.py\"'" } ] }
       ],
       "Stop": [
         { "hooks": [ { "type": "command", "timeout": 30, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/stamp-truth-stop.py\"'" } ] }
       ],
       "PreToolUse": [
         { "matcher": "Write|Edit|MultiEdit|Bash", "hooks": [ { "type": "command", "timeout": 30, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/future-stamp-write.py\"'" } ] },
         { "matcher": "Bash", "hooks": [ { "type": "command", "timeout": 30, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/ungated-record.py\"'" } ] },
         { "matcher": "Bash", "hooks": [ { "type": "command", "timeout": 30, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/unbounded-wait.py\"'" } ] },
         { "matcher": "Bash", "hooks": [ { "type": "command", "timeout": 30, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/record-remove-check.py\"'" } ] }
       ]
     }
   }
   ```

   The self-tests for `ungated-record.py`, `unbounded-wait.py`, and `record-remove-check.py` include
   byte-identity checks against `block-bare-detach.py` and `inplace-edit-verify.py`, two reference hooks
   not yet published here. Those checks report `SKIPPED` until the reference files are available;
   skipped is not a pass. The `record-remove-check.py` differential check also reports
   `SKIPPED, no trusted bash` when it cannot find a trusted root-owned `/usr/bin/bash` or `/bin/bash`.

5. Configure the hook with the environment variables in the next section, then start a new Claude Code
   session so the settings are read.

6. Smoke-test the live hook. For `clock-inject.py`, run any command in the new session (for example
   `true`) and confirm a `CLOCK (read by hook, authoritative):` line reaches the assistant's context. For
   the other five, a passing self-test in step 3 is the check; they stay silent until they see something
   to flag.

### A note on hooks that record authority

Some hooks, though none of the six above, need a line in a durable record to switch on or to grant an
exception, for example an entry saying that you, the maintainer, approved something. Expect your assistant
to decline to write such a line itself, even when your permission settings would allow the write: a record
of your own authority is not something it should author on your behalf, and permission allow rules have
not been observed to override that refusal. Make that one write yourself, for example as a shell command
you type directly (in Claude Code, a line starting with `!`).

## Configuration

The hooks read their settings from environment variables. Each feature has its own off state, and nothing
beyond these variables is assumed. Each `AIQT_` variable also accepts an older spelling with the prefix
`ORCH_` (for example `ORCH_STORE_ROOT`), read only when the `AIQT_` one is unset, so "unset" below means
both spellings are unset. The worker skip likewise also honours `ORCH_WORKER=1` and any value of
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

The `record-ok` and `wait-ok` comments must begin a word and be the last non-blank content of the
command. Their reasons are optional; text inside quotes does not opt out.

| Variable | What it does |
|---|---|
| `AIQT_STORE_ROOT` | The folder or folders holding your working records, as absolute paths joined with `:`. The future-date and record-removal checks only look at files under these folders. |
| `AIQT_LEASE_FILE` | The absolute path to a small text file that marks when the current working session started. When it is set and valid, the hooks report and check how long the session has been running. |
| `AIQT_HOOKS_WORKER` | Set to `1` only in a separate worker process that another program launches to produce output for it to read back (a batch verifier, say), to keep the hooks out of that output. Do not set it for a helper session started inside your own session: `future-stamp-write.py` deliberately still checks the record writes such a helper makes, and `clock-inject.py` still gives it the clock. |
| `G_REF_DIR` | Self-tests only: the folder containing reference hooks for the byte-identity checks in `ungated-record.py`, `unbounded-wait.py`, and `record-remove-check.py`. If unset or empty, they look beside the hook itself. Each missing reference makes its check report `SKIPPED`. |

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
- **`record-remove-check.py`** checks only supported shell forms and configured stores. It allows
  absent or empty files and files within a store's `.git` directory, though removing that directory
  whole is checked. It misses editor tools, scripts, nested shell strings, `find`, `rsync`, git
  commands, and optioned `cp`, `mv`, or `tee`. Unknown paths or directory changes, unreadable targets,
  and files beyond its scan budgets are not fully checked. Moving a record out of the store is allowed.
  A file created or filled after the check can be lost without a warning. It can deny unreachable
  commands and files that have a good backup; it does not check for a restore path.

`ungated-record.py`, `unbounded-wait.py`, and `record-remove-check.py` also allow commands over 64 KiB or
input they cannot follow. Their docstrings describe further parsing limits and work budgets. All three skip
verification worker processes; `ungated-record.py` and `unbounded-wait.py` also skip helper-session calls.

## Status and retirement

This is a preview channel. When a hook here becomes part of the pack's plugin, it is removed from this
directory and its row is replaced by a pointer to where it now lives. When every hook has moved, this
directory is removed.

A change to any hook here ships with new checksums on this page and a matching SHA256SUMS, all in the
same change.
