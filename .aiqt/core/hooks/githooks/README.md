# Git hooks shipped by the pack (install required)

This directory holds git hooks that back the pack's runtime controls for processes the Claude Code
hooks cannot see. Nothing installs them automatically: a git hook runs only where it is installed,
and that limit is the first thing to know about this layer.

## commit-msg (rule cmtidn, integ/commit-identity)

Refuses any commit whose message, as git will record it, holds the AI co-author trailer token in
trailer shape: the trailer name, matched case-insensitively with a hyphen or one space between its
words, then optional blanks and a colon. Prose that names the words with no colon after them passes.
The spelling is deliberately not written in this page, because the pack's file-content layer holds
that token out of files. The PreToolUse hooks judge only the assistant's own tool calls; this hook
makes git itself refuse the commit, so a commit staged by a background script, a scheduler, or any
process outside the assistant hook path is refused too. It fails closed: an unreadable or oversized
message file refuses the commit.

git runs commit-msg on its message buffer before its own cleanup, so the hook applies that cleanup
first. When an editor ran (git sets `GIT_EDITOR=:` for the hook when none does), the hook drops
everything from git's scissors line on, and, under the default or `strip` cleanup (`commit.cleanup`),
every line starting with the comment string (`core.commentString`, else `core.commentChar`, else
`#`). So a removal commit made with `git commit -v -e`, whose diff below the scissors line shows the
removed trailer line, passes. With no editor, git keeps comment lines, and the hook counts them.

### Install (one repository)

From the repository you want covered:

    cp <pack>/.aiqt/core/hooks/githooks/commit-msg .git/hooks/commit-msg
    chmod +x .git/hooks/commit-msg

Replace `<pack>` with the path of this pack. For a worktree, `git rev-parse --git-path hooks` names
the hooks directory to copy into.

### Install (every repository of one user)

    git config --global core.hooksPath <absolute path to this githooks directory>

Note that `core.hooksPath` replaces the per-repository `.git/hooks` directory entirely: if a
repository relies on its own hooks, prefer the per-repository copy above, or point `core.hooksPath`
at a directory that chains to them.

### Disclosed limits

- It works ONLY where installed. A repository without it (or a clone, which never copies hooks) is
  not covered by this layer.
- git runs commit-msg for `git commit` and for a `git merge` that records a merge commit, with or
  without `--no-edit`. A fast-forward merge records no commit and runs no hook.
- `git commit --no-verify` and `git merge --no-verify` skip commit-msg by design; inside the
  assistant's own calls the gate-weakening hook denies `--no-verify`, but a process outside the
  assistant can still pass it.
- Plumbing (`git commit-tree`), `git notes`, tag messages and forge-side bodies (a PR description)
  do not run commit-msg. Neither do `git am` (it runs `applypatch-msg` instead) and `git cherry-pick`
  or `git rebase` replaying a recorded message without an editor. The PreToolUse layers cover those
  surfaces best-effort at the tool-call boundary instead: the Bash layer reads the message files and
  patches such a command names, and the recorded messages of the commits it replays.
- The hook is switched off by anything that changes which hooks git runs: `-c core.hooksPath=...` on
  the command line (the Bash layer refuses this beside a commit-creating git command in the same
  command), a `core.hooksPath` set in a configuration file, a `GIT_CONFIG_PARAMETERS` or
  `GIT_CONFIG_COUNT` setting in the environment, or a hook file that is deleted, replaced or made
  non-executable. Of these, only the same-command form is refused at the tool-call boundary.
- The hook cannot see a `--cleanup` option on the command line. With an editor and
  `--cleanup=verbatim` or `--cleanup=whitespace`, git records a comment line that holds the token,
  but the hook does not count it. git does not parse such a line as a trailer; how a forge reads it
  is unverified. With `core.commentChar=auto`, the hook drops no comment line and finds no scissors
  line, so it refuses such a removal commit rather than passing it.
- The scan is byte-based over the message file (surrogateescape decode), so any ASCII-superset
  encoding is read; a wide encoding such as UTF-16 is a disclosed non-catch.

Requires Python 3 on PATH (`/usr/bin/env python3`); with no Python the hook fails and git refuses
the commit, which is the fail-closed direction.
