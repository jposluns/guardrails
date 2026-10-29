# Git hooks shipped by the pack (install required)

This directory holds git hooks that back the pack's runtime controls for processes the Claude Code
hooks cannot see. Nothing installs them automatically: a git hook runs only where it is installed,
and that limit is the first thing to know about this layer.

## commit-msg (rule cmtidn, integ/commit-identity)

Refuses any commit whose final message holds the AI co-author trailer token (the trailer name,
matched case-insensitively, with a hyphen or a space between its words; the spelling is deliberately
not written in this page, because the pack's file-content layer holds that token out of files). The
PreToolUse hooks judge only the assistant's own tool calls; this hook makes git itself refuse the
commit, so a commit staged by a background script, a scheduler, or any process outside the assistant
hook path is refused too. It fails closed: an unreadable or oversized message file refuses the
commit.

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
- `git commit --no-verify` and `git merge --no-verify` skip commit-msg by design; inside the
  assistant's own calls the gate-weakening hook denies `--no-verify`, but a process outside the
  assistant can still pass it.
- Plumbing (`git commit-tree`), `git notes`, tag messages and forge-side bodies (a PR description)
  do not run commit-msg; the PreToolUse layers cover those surfaces best-effort at the tool-call
  boundary instead.
- The scan is byte-based over the final message file (surrogateescape decode), so any ASCII-superset
  encoding is read; a wide encoding such as UTF-16 is a disclosed non-catch.

Requires Python 3 on PATH (`/usr/bin/env python3`); with no Python the hook fails and git refuses
the commit, which is the fail-closed direction.
