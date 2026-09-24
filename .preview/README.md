# Hooks preview

This directory is a preview channel for AIQT Guardrails hooks that are not yet part of the pack's plugin.
Each hook is one self-contained Python file that you can download, check, test, and switch on in Claude
Code by hand. This page is written so that you can hand it to your AI coding assistant and ask it to
install a hook for you: every step below is a command it can run, and every check tells it when to stop.

Three hooks are published here, each listed with its checksum and link in the integrity table below.
A hook without a row in that table is not available here, and the install steps do not apply to it.

## What these hooks are

These hooks back the rule that a current timestamp is read from the clock, never recalled or guessed
([the rule text](../.claude/rules/aiqt/10-ACCUR-timestamp-from-clock.md)). Each one is a discipline
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

## Integrity

Every published hook file is listed below with its SHA-256 checksum and a link to the file itself. The
files are served from this repository's main branch; for a raw download, use
`https://raw.githubusercontent.com/jposluns/guardrails/main/.preview/<file>`. The same values are in
[SHA256SUMS](SHA256SUMS), which lists bare file names in the format `sha256sum -c` reads.

| File | SHA-256 | Link |
|---|---|---|
| `clock-inject.py` | `e7f6a8efd76216dedad4522c7d122675db24135e0ea52e8e1f42a2082f5505b5` | [clock-inject.py](clock-inject.py) |
| `future-stamp-write.py` | `51eb6afde84a4362997db3d6ab2e8c53a7c02d556dbbf1c73940686f36296913` | [future-stamp-write.py](future-stamp-write.py) |
| `stamp-truth-stop.py` | `b73a799f81d2fd21d4f368750074f08a65dde29e031a90b60f1de569dee2de99` | [stamp-truth-stop.py](stamp-truth-stop.py) |

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

   A nonzero exit or a reported failure means stop; do not switch the hook on. A hook installed on its own
   reports a few tests as skipped: those compare it with the other two hooks' files, which are not there.
   Skipped is expected; failed is not.

4. Switch the hook on by adding an entry to the `hooks` section of Claude Code's `settings.json` (the
   user file `~/.claude/settings.json`, or a project's `.claude/settings.json`). If `settings.json`
   already has a `hooks` section, add to it rather than replacing it, and keep every existing entry and
   permission. Register each hook once: if a later version of the pack's plugin provides the same hook,
   remove this entry so it does not run twice.

   Every hook is launched the same way:

   ```sh
   /bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B "/ABSOLUTE/PATH/TO/<file>"'
   ```

   - `-I` stops a stray file beside the hook, or a Python environment variable, from changing how Python
     loads it; `-S` skips site packages, which these hooks do not use; `-B` writes no bytecode cache.
   - The `[ -d ... ]` tests are a launch guard: if any standard stream is a directory, Python would fail
     before the hook's own code could fail open, so the guard skips the hook instead.
   - Use the absolute path to the downloaded file. It sits inside double quotes, so a path with spaces
     works; the path must not contain `"`, `'`, `$`, a backtick, or a backslash. The hooks need `python3`
     on the `PATH` that Claude Code runs hook commands with.
   - In JSON, each `"` inside the command is written `\"`, as in the entries below. The `timeout` value is
     the most seconds Claude Code lets one run of the hook take.

   **`clock-inject.py`**, on two events with no matcher (all tools):

   ```json
   {
     "hooks": {
       "PostToolUse": [
         { "hooks": [ { "type": "command", "timeout": 10, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/clock-inject.py\"'" } ] }
       ],
       "PostToolUseFailure": [
         { "hooks": [ { "type": "command", "timeout": 10, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/clock-inject.py\"'" } ] }
       ]
     }
   }
   ```

   **`stamp-truth-stop.py`**, on `Stop` (no matcher):

   ```json
   {
     "hooks": {
       "Stop": [
         { "hooks": [ { "type": "command", "timeout": 30, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/stamp-truth-stop.py\"'" } ] }
       ]
     }
   }
   ```

   **`future-stamp-write.py`**, on `PreToolUse` for file writes and shell commands:

   ```json
   {
     "hooks": {
       "PreToolUse": [
         { "matcher": "Write|Edit|MultiEdit|Bash", "hooks": [ { "type": "command", "timeout": 30, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/future-stamp-write.py\"'" } ] }
       ]
     }
   }
   ```

5. Configure the hook with the environment variables in the next section, then start a new Claude Code
   session so the settings are read.

6. Smoke-test the live hook. For `clock-inject.py`, run any command in the new session (for example
   `true`) and confirm a `CLOCK (read by hook, authoritative):` line reaches the assistant's context. For
   the other two, a passing self-test in step 3 is the check; they stay silent until they see something
   to flag.

### A note on hooks that record authority

Some hooks, though none of the three above, need a line in a durable record to switch on or to grant an
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
- With no store configured (`AIQT_STORE_ROOT` unset), no write is inspected for a future date:
  `future-stamp-write.py` does nothing.
- With no lease configured (`AIQT_LEASE_FILE` unset), no elapsed time is reported or compared: the clock
  line carries no elapsed segment, and the elapsed-footer check is off.

| Variable | What it does |
|---|---|
| `AIQT_STORE_ROOT` | The folder or folders holding your working records, as absolute paths joined with `:`. The future-date check only looks at files under these folders. |
| `AIQT_LEASE_FILE` | The absolute path to a small text file that marks when the current working session started. When it is set and valid, the hooks report and check how long the session has been running. |
| `AIQT_HOOKS_WORKER` | Set to `1` only in a separate worker process that another program launches to produce output for it to read back (a batch verifier, say), to keep the hooks out of that output. Do not set it for a helper session started inside your own session: `future-stamp-write.py` deliberately still checks the record writes such a helper makes, and `clock-inject.py` still gives it the clock. |

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

## Status and retirement

This is a preview channel. When a hook here becomes part of the pack's plugin, it is removed from this
directory and its row is replaced by a pointer to where it now lives. When every hook has moved, this
directory is removed.

A change to any hook here ships with new checksums on this page and a matching SHA256SUMS, all in the
same change.
