# Hooks preview

This directory is a preview channel for AIQT Guardrails hooks that are not yet part of the pack's plugin.
Each hook is one self-contained Python file that you can download, check, test, and switch on in Claude
Code by hand. This page is written so that you can hand it to your AI coding assistant and ask it to
install a hook for you: every step below is a command it can run, and every check tells it when to stop.

Four hooks are published here, each listed with its checksum and link in the integrity table below.
A hook without a row in that table is not available here, and the install steps do not apply to it.

One document linked from this page is not a hook: [the OPF implementation prompt](../opf/spec/OPF-IMPLEMENTATION-PROMPT.md)
is a prompt you can give to an AI coding assistant so that it implements OPFiles natively in its own
project, from the specification. It has no row in the integrity table, and the install steps below do
not apply to it.

## What these hooks are

The clock hook `stamp-truth-stop.py` backs the rule that a current timestamp is read from the clock, never
recalled or guessed ([the rule text](../.claude/rules/aiqt/10-ACCUR-timestamp-from-clock.md)). Its two
former companions, `clock-inject.py` and `future-stamp-write.py`, are now part of the pack's plugin; see
[Moved to the pack](#moved-to-the-pack). The other hooks guard completion
records, background polling loops, and existing working-record files. Each one is a discipline
guard against accidental drift, not a security boundary, and each one fails open: if the hook hits an
error or input it cannot evaluate, it gets out of the way rather than blocking your work. Each file states
what it does not catch in a section headed `RESIDUAL COVERAGE` in its opening docstring; that section is
the authority, and the summary further down this page only points to it.

- **`stamp-truth-stop.py`** checks each finished turn. When the assistant's prose carries a timestamp with
  an explicit zone that lies ahead of the clock, or an elapsed-time footer that disagrees with the
  configured lease, it blocks the stop and says why, so the assistant corrects the value before handing
  back. Text in code spans, fenced code blocks, and `>` quote lines is not checked, and a time that a
  scheduling word (such as `until`, `due`, or `next run at`) directly precedes is treated as a scheduled
  time, not a reading. It is not part of the pack's plugin. Event: `Stop`.
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
| `record-remove-check.py` | `815563da687c461408c3c584f84adf2080958402ab17798129ba281723b2ee9f` | [record-remove-check.py](record-remove-check.py) |
| `stamp-truth-stop.py` | `f943d5c9fa2dd77b00349d0e5fe21729238f5b81516318f6d3bad3cecce570b6` | [stamp-truth-stop.py](stamp-truth-stop.py) |
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

## Moved to the pack

`clock-inject.py` and `future-stamp-write.py` are no longer published here. They now ship in the pack's
Claude Code plugin, `aiqt-guardrails-hooks` version 0.5.0, whose sources are in
[`.aiqt/core/hooks/scripts/`](../.aiqt/core/hooks/scripts/). If you installed either from this page,
remove that copy and its `settings.json` entries before you install the plugin, so the hook does not run
twice. The last checksums this page published for them, for identifying an old copy:

| Former preview file | Last published SHA-256 |
|---|---|
| `clock-inject.py` | `65fe1cae733f72d2f82b884b9bb710310b0b6ad0dcccd8c874c5f2bdd2e25386` |
| `future-stamp-write.py` | `77d4f32496bde3593845aba73f84dc1498c2ece83c380d491e642885f211c5e9` |

What changed in the pack copies:

- `future-stamp-write.py` no longer denies. A write that would record a future-dated timestamp into a
  declared store gets one warning line (a `systemMessage` of at most 100 characters), and the call goes
  ahead. Whether the assistant itself sees that line is not established.
- Neither script has a worker skip. A session that sets `AIQT_HOOKS_WORKER`, `ORCH_WORKER`, or
  `ORCH_VERIFY_OWNER` now receives the clock line and the warnings.
- Neither script reads the older `ORCH_` spellings. Rename `ORCH_LEASE_FILE` to `AIQT_LEASE_FILE` and
  `ORCH_STORE_ROOT` to `AIQT_STORE_ROOT`. The worker variables have no replacement. A host setting that
  turns off a plugin's hooks is not named here, because it has not been verified.
- The plugin registers `clock-inject.py` on `PostToolUse` only, with matcher `.*`, so a failed tool call
  gets no clock line.
- The plugin does not run `python3 -I -S -B <file>` directly. It runs a short fixed launcher,
  `python3 -I -S -B -c <launcher> <file>`, which runs the file and exits 1 with the reason on stderr if
  the file is missing, cannot be read or parsed, raises an error, or exits with any code other than 0
  through `sys.exit` or by returning. Run directly, Python exits 2 on a missing file, and exit 2 on
  `PreToolUse` blocks the call. Exit 1 is a non-blocking hook error (from the host hook documentation,
  not a live probe). Five cases fall outside the launcher. A `python3` older than 3.4 rejects `-I` and
  exits 2. A `python3` that cannot be found or started gets whatever outcome the host gives it (not
  verified). A script that ends the process with `os._exit(2)` exits 2; the shipped scripts call
  `os._exit` only with 0. A standard stream that is a directory makes Python exit 1 at startup, before
  the hook can fail open, because the plugin does not use the `[ -d ... ]` launch guard this page uses. A
  run past its timeout (30 seconds for `future-stamp-write.py`, 10 for `clock-inject.py`) is ended by
  the host, which its documentation describes as non-blocking (not verified here). The launcher also
  relies on the host passing `args` without a shell (the repository's reading of the host
  documentation, not re-checked for the launcher).

A copy kept from this page keeps the older behaviour: the preview `future-stamp-write.py` denies, and both
preview copies keep the worker skip and the `ORCH_` fallback. `stamp-truth-stop.py` stays here, still
blocks a stop it flags, keeps its worker skip and `ORCH_` fallback, and is not part of the pack. If you set
only `ORCH_LEASE_FILE`, the pack's `clock-inject.py` shows no elapsed time, while this page's
`stamp-truth-stop.py` still reads that lease and checks elapsed-time footers against it; set
`AIQT_LEASE_FILE` to keep the two in step.

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

   Use this launch line for each of the four hooks:

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
     runs the same `python3 -I -S -B` command with stdin unchanged. `stamp-truth-stop.py` does not define
     a `REGISTRATION` constant; use this same guard for it.
   - Use the absolute path to the downloaded file. It sits inside double quotes, so a path with spaces
     works; the path must not contain `"`, `'`, `$`, a backtick, or a backslash. The hooks need `python3`
     on the `PATH` that Claude Code runs hook commands with.
   - In JSON, each `"` inside the command is written `\"`, as in the entries below. The `timeout` value is
     the most seconds Claude Code lets one run of the hook take.

   This combined example shows the four hooks. Copy only entries for hooks you have downloaded,
   checked, and tested. `stamp-truth-stop.py` uses `Stop`, with no matcher. On `PreToolUse`, the other
   three match `Bash`.

   ```json
   {
     "hooks": {
       "Stop": [
         { "hooks": [ { "type": "command", "timeout": 30, "command": "/bin/sh -c '[ -d /dev/stdin ] || [ -d /dev/stdout ] || [ -d /dev/stderr ] || exec python3 -I -S -B \"/ABSOLUTE/PATH/TO/.claude/hooks/stamp-truth-stop.py\"'" } ] }
       ],
       "PreToolUse": [
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

6. Smoke-test the live hook. For each of the four, a passing self-test in step 3 is the check; they stay
   silent until they see something to flag.

### A note on hooks that record authority

Some hooks, though none of the four above, need a line in a durable record to switch on or to grant an
exception, for example an entry saying that you, the maintainer, approved something. Expect your assistant
to decline to write such a line itself, even when your permission settings would allow the write: a record
of your own authority is not something it should author on your behalf, and permission allow rules have
not been observed to override that refusal. Make that one write yourself, for example as a shell command
you type directly (in Claude Code, a line starting with `!`).

## Configuration

The hooks read their settings from environment variables. Each feature has its own off state, and nothing
beyond these variables is assumed. In these preview hooks, each `AIQT_` variable also accepts an older
spelling with the prefix `ORCH_` (for example `ORCH_STORE_ROOT`), read only when the `AIQT_` one is unset,
so "unset" below means both spellings are unset. The worker skip likewise also honours `ORCH_WORKER=1` and
any value of `ORCH_VERIFY_OWNER`; if your environment sets either for another purpose, the hooks stay
silent there. The pack's `clock-inject.py` and `future-stamp-write.py` read neither the older spellings nor
the worker variables; see [Moved to the pack](#moved-to-the-pack).

- The current time needs no setting. `stamp-truth-stop.py` always compares zoned timestamps against it.
- With no store configured (`AIQT_STORE_ROOT` unset), no record removal is inspected:
  `record-remove-check.py` does nothing.
- With no lease configured (`AIQT_LEASE_FILE` unset), no elapsed time is compared: the elapsed-footer
  check is off.

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
| `AIQT_HOOKS_WORKER` | Set to `1` only in a separate worker process that another program launches to produce output for it to read back (a batch verifier, say), to keep these preview hooks out of that output. The pack's `clock-inject.py` and `future-stamp-write.py` do not read it. |
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

- **`stamp-truth-stop.py`** checks only timestamps written with an explicit zone; an unzoned, 12-hour, or
  natural-language time is not checked. A fabricated time in a code span, a code block, a `>` quote line,
  or just after a scheduling word passes. A genuinely future time mentioned in plain prose without a
  scheduling word before it is blocked; the remedy is to add the word or put the time in a code span.
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
