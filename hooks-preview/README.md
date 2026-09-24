# Hooks preview

This directory is a preview channel for AIQT Guardrails hooks that are not yet part of the pack's plugin.
Each hook is one self-contained Python file that you can download, check, test, and switch on in Claude
Code by hand. This page is written so that you can hand it to your AI coding assistant and ask it to
install a hook for you: every step below is a command it can run, and every check tells it when to stop.

## What these hooks are

These hooks back the rule that a current timestamp is read from the clock, never recalled or guessed
([the rule text](../.claude/rules/aiqt/10-ACCUR-timestamp-from-clock.md)). Each one is a discipline
guard that fails open: if the hook itself hits an internal error, it gets out of the way rather than
blocking your work.

TODO(hook content): one short paragraph per hook, naming what it does, the Claude Code event it runs on,
and where its residual coverage is disclosed inside the file. Planned hooks: `clock-inject.py`,
`stamp-truth-stop.py`, `future-stamp-write.py`.

No hook is published in this channel yet.

## Integrity

Every hook file is listed below with its SHA-256 checksum and a download link pinned to a fixed release
tag of this repository. The checksum is what you trust: always compare the file you downloaded against
the checksum on this page before running it. The same values are in [SHA256SUMS](SHA256SUMS), in the
format `sha256sum -c` reads.

| File | SHA-256 | Pinned raw link |
|---|---|---|

TODO(integrity): one row per hook, in the form
`` | `<file>` | `<64-character SHA-256>` | https://raw.githubusercontent.com/jposluns/guardrails/hooks-preview-v<N>/hooks-preview/<file> | ``,
with the same file and checksum added to SHA256SUMS. The repository's quality gate checks that this
table, SHA256SUMS, and the files agree. It also checks that each raw link is pinned to a release tag and
that the file at that tag matches its checksum (where the tag is available to the check), but it does
not check that the link points at this repository's canonical origin, so a reviewer must confirm the
owner and repository in each link.

## Installing a hook

These steps are for an AI coding assistant to carry out, one hook at a time. Replace `<file>`,
`<checksum>`, and `<pinned raw link>` with the values from the integrity table above. Stop at the first
step that fails and report it; do not work around a failed check.

1. Create the hooks directory and download the hook from its pinned link:

   ```sh
   mkdir -p ~/.claude/hooks
   curl -fsSL '<pinned raw link>' -o ~/.claude/hooks/<file>
   ```

2. Check the checksum. The command prints `OK` only when the file matches the value on this page:

   ```sh
   (cd ~/.claude/hooks && echo '<checksum>  <file>' | sha256sum -c -)
   ```

   If it prints anything other than `<file>: OK`, delete the downloaded file and stop. A mismatch means
   the file is not the one this page describes.

3. Run the hook's own self-test and require it to pass:

   ```sh
   python3 -I -B ~/.claude/hooks/<file> --self-test
   ```

   A nonzero exit or a reported failure means stop; do not switch the hook on.

4. Switch the hook on by adding an entry to the `hooks` section of Claude Code's `settings.json` (the
   user file `~/.claude/settings.json`, or a project's `.claude/settings.json`). Use the absolute path to
   the downloaded file, and keep the `-I` flag, which stops a stray file beside the hook from changing
   how Python loads it. The hooks need `python3` on the `PATH` that Claude Code runs hook commands with.
   Put the path in single quotes inside the `command` string, as below, so a path that contains spaces
   still works; single quotes need no escaping in JSON (a path that itself contains a single quote needs
   that quote written as `'\''`). The shape is:

   ```json
   {
     "hooks": {
       "<event>": [
         {
           "matcher": "<matcher>",
           "hooks": [
             { "type": "command", "command": "python3 -I -B '/ABSOLUTE/PATH/TO/.claude/hooks/<file>'" }
           ]
         }
       ]
     }
   }
   ```

   If `settings.json` already has a `hooks` section, add to it rather than replacing it.

   TODO(hook content): the exact entry for each hook, with its event and matcher.

5. Configure the hook with the environment variables in the next section, then start a new Claude Code
   session so the settings are read.

## Configuration

The hooks read their settings from environment variables. Nothing is assumed, and each feature has its
own off state: with no store configured (`AIQT_STORE_ROOT` unset), no write is inspected for a future
date; with no lease configured (`AIQT_LEASE_FILE` unset), no elapsed time is compared or reported. The
current time itself needs no setting: it is always read from the clock and injected.

| Variable | What it does |
|---|---|
| `AIQT_STORE_ROOT` | The folder or folders holding your working records, as absolute paths joined with `:`. The future-date check only looks at files under these folders; when it is unset, that hook does nothing. |
| `AIQT_LEASE_FILE` | The absolute path to a small text file that marks when the current working session started. When it is set and valid, the hooks can report how long the session has been running. When it is unset, elapsed time is left out. |
| `AIQT_HOOKS_WORKER` | Set to `1` in a helper or subordinate session (one started by another session to do a piece of work) to keep the hooks quiet there. |

TODO(hook content): confirm each variable against the published hooks, and give the exact first-line
format of the `AIQT_LEASE_FILE` file and the command to write it.

## What each hook does not catch

Each hook is a best-effort guard, and each one states what it does not catch inside its own file. The
summary below will quote those statements; the file is the authority.

TODO(hook content): the residual coverage of each hook, quoted from its file.

## Status and retirement

This is a preview channel. When a hook here becomes part of the pack's plugin, it is removed from this
directory, its row is replaced by a pointer to where it now lives, and the pinned tag number goes up.
When every hook has moved, this directory is removed.

A change to any hook here ships with a new pinned tag, new checksums on this page, and a matching
SHA256SUMS, all in the same change.
