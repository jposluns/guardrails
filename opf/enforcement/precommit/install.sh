#!/bin/sh
# OPF pre-commit floor: the per-clone installer (enforcement pack, spec 1.3.0 (draft) 14.1: the pack MUST
# provide staged-snapshot pre-commit checks with the per-clone installation residual disclosed).
#
#   sh opf/enforcement/precommit/install.sh
#
# It writes a pre-commit stub into the hooks directory of the clone that holds this file: the untracked
# hooks directory inside the clone's own git directory (for a linked worktree, the common git directory
# its worktrees share). Git runs the stub from the top level of the working tree being committed, and the
# stub runs the pre-commit hook beside this file, found at the same path in that working tree. It never
# sets core.hooksPath: a core.hooksPath naming this tracked directory would let any branch add a hook
# (post-checkout, post-merge) that git runs on checkout, merge or pull.
# Running it again is a no-op. It refuses (exit 2) and changes nothing when:
#   - this directory is not inside the clone's working tree, its path holds a quote or newline the stub
#     cannot name, or the hook beside it is missing or not readable;
#   - core.hooksPath is set, in any configuration scope, to any value, in the configuration this worktree
#     or any other worktree of the clone reads (a worktree's own config.worktree under
#     extensions.worktreeConfig, or an includeIf onbranch: for the branch it has checked out, included):
#     git would not run a hook from the hooks directory there while it is set, and replacing it would
#     stop the hooks it names; a worktree git directory that git cannot read is refused too;
#   - the hooks directory already holds a pre-commit hook other than this stub, byte for byte, as a
#     regular executable file (a symbolic link is never taken as the stub): replacing it would stop it
#     (other hooks there are left as they are and keep running);
#   - the hooks directory is not a real directory (a symbolic link is refused: it could name another
#     repository's hooks directory, or a directory of a working tree that a branch could fill) or is not
#     one this user can read, search and write;
#   - the common git directory lies inside the working tree under no .git component, where git would
#     track the files of its hooks directory;
#   - git is older than 2.32, the floor the hook needs (see pre-commit), or a command it needs (env, sed,
#     dirname, git, cmp) fails.
# The only place it writes is the hooks directory of the clone's common git directory, resolved to its
# physical path: with core.hooksPath unset, the only directory git runs hooks from. It writes the stub
# under a temporary name there, checks it, and links it into place (ln never replaces a file). When a
# step fails (a write that stops partway on a full disk, say, or a stub that does not read back as a
# regular, readable, executable file with the exact stub text), it removes what it wrote, and the hooks
# directory if it created it, before it exits 2.
# Exit status: 0 installed or already installed, 2 refused or cannot evaluate.
#
# Every inherited GIT_ variable is dropped before the first git call, except the three that name the
# global and system configuration files (GIT_CONFIG_GLOBAL, GIT_CONFIG_SYSTEM, GIT_CONFIG_NOSYSTEM), and
# GIT_TRACE2, GIT_TRACE2_EVENT and GIT_TRACE2_PERF are then set to 0, which overrides a trace2 target those
# configuration files name: so an inherited GIT_DIR, GIT_WORK_TREE or GIT_CONFIG cannot point the install
# at another repository or file, a `git -c` value (GIT_CONFIG_PARAMETERS, GIT_CONFIG_COUNT) cannot read as
# configuration, and no trace destination, inherited or configured, makes git write into another file.
#
# Residuals (spec 14.1, disclosed; pre-commit lists them in full): the stub is local to this clone. It is
# not cloned or pushed, so every clone must run this installer; git commit --no-verify skips it; git runs
# no pre-commit hook for cherry-pick, revert, rebase, am or a merge that commits without stopping; a
# core.hooksPath set later, in any scope, stops the stub silently; the core.hooksPath check reads the
# configuration this run sees, so one a later commit reads instead (another GIT_CONFIG_GLOBAL or
# GIT_CONFIG_SYSTEM file, or an includeIf onbranch: for a branch checked out later) is not seen and stops
# the stub silently too; and the stub runs the checked-out pre-commit and opf.py. CI (../ci/opf-ci.sh) does not close these for the history checks: see pre-commit.
set -u
if [ "$#" -gt 0 ]; then
    echo "opf-pre-commit install: usage: sh install.sh (no operands)" >&2
    exit 2
fi
fail() {
    echo "opf-pre-commit install: refused: $1" >&2
    exit 2
}
# The inherited GIT_ variables, listed and parsed as two checked steps, then dropped (allowlist above).
envlist=$(env) || fail "could not list the environment"
git_vars=$(printf '%s\n' "$envlist" | sed -n 's/^\(GIT_[A-Za-z0-9_]*\)=.*/\1/p') || \
    fail "could not read the GIT_ variable names from the environment"
for var in $git_vars; do
    case "$var" in
        GIT_CONFIG_GLOBAL|GIT_CONFIG_SYSTEM|GIT_CONFIG_NOSYSTEM) ;;
        *) unset "$var" || fail "could not drop $var from the environment" ;;
    esac
done
# Trace2 targets the kept configuration files name are overridden: a set GIT_TRACE2* variable wins over
# the trace2.*Target configuration, and 0 turns that trace off.
GIT_TRACE2=0
GIT_TRACE2_EVENT=0
GIT_TRACE2_PERF=0
export GIT_TRACE2 GIT_TRACE2_EVENT GIT_TRACE2_PERF
# The directory of this file, resolved in two checked steps: a dirname that fails (missing, say) stops
# here, rather than leaving an empty name that `cd` would read as the launching directory.
dir=$(dirname -- "$0") || fail "could not resolve the directory of $0 (dirname failed)"
[ -n "$dir" ] || fail "could not resolve the directory of $0"
# CDPATH is cleared for this one command, as in opf-ci.sh: a CDPATH match makes `cd` print a line.
here=$(CDPATH= cd -- "$dir" && pwd -P) || fail "could not enter $dir"
# The git floor, as in pre-commit: 2.32 (GIT_CONFIG_GLOBAL; --path-format=absolute needs 2.31).
gv=$(git --version) || fail "could not read the git version"
gv=${gv#git version }
gmajor=${gv%%.*}
gminor=${gv#*.}
gminor=${gminor%%.*}
case "$gmajor$gminor" in
    ''|*[!0-9]*) fail "could not parse the git version $gv" ;;
esac
[ "$gmajor" -gt 2 ] || { [ "$gmajor" -eq 2 ] && [ "$gminor" -ge 32 ]; } || \
    fail "git $gv is older than 2.32, the floor the pre-commit hook needs"
top=$(git -C "$here" rev-parse --show-toplevel) || fail "$here is not inside a git working tree"
top=$(CDPATH= cd -- "$top" && pwd -P) || fail "could not resolve the top level $top"
case "$here" in
    "$top"/*) rel=${here#"$top"/} ;;
    *) fail "$here is not inside the working tree $top" ;;
esac
case "$rel" in
    *"'"*|*'
'*) fail "the pack path $rel holds a quote or newline, which the stub cannot name" ;;
esac
[ -f "$here/pre-commit" ] && [ -r "$here/pre-commit" ] || \
    fail "$here/pre-commit is missing or not readable, so the stub could not run it"
current=$(git -C "$top" config --get core.hooksPath)
case "$?" in
    0) fail "core.hooksPath is set to $current (git config --show-origin --get core.hooksPath names where); git runs no hook from the hooks directory while it is set, and this installer will not replace it. Unset it in that scope and run this installer again" ;;
    1) ;;
    *) fail "could not read core.hooksPath" ;;
esac
# A core.hooksPath that only another worktree of this clone reads (its own config.worktree under
# extensions.worktreeConfig, or an includeIf onbranch: for the branch it has checked out) stops the stub
# there just as well. Each worktree's configuration is read through its git directory, which needs no
# working tree: the common git directory for the main worktree, and each entry of its worktrees directory.
common=$(git -C "$top" rev-parse --path-format=absolute --git-common-dir) || \
    fail "could not locate the common git directory"
common=$(CDPATH= cd -- "$common" && pwd -P) || fail "could not resolve the common git directory $common"
for wtdir in "$common" "$common"/worktrees/* "$common"/worktrees/.[!.]* "$common"/worktrees/..?*; do
    [ -e "$wtdir" ] || [ -L "$wtdir" ] || continue
    git --git-dir="$wtdir" rev-parse --git-dir > /dev/null || \
        fail "git cannot read $wtdir as the git directory of a worktree of this clone, so its core.hooksPath cannot be checked (git worktree prune removes a stale entry)"
    wcur=$(git --git-dir="$wtdir" config --get core.hooksPath)
    case "$?" in
        0) fail "core.hooksPath is set to $wcur in the configuration the worktree whose git directory is $wtdir reads (git -C <that worktree> config --show-origin --get core.hooksPath names where); git runs no hook from the hooks directory there while it is set, and this installer will not replace it. Unset it in that scope and run this installer again" ;;
        1) ;;
        *) fail "could not read core.hooksPath for the worktree whose git directory is $wtdir" ;;
    esac
done
# The destination, confined by construction: with core.hooksPath unset, git runs hooks only from the
# hooks directory of the common git directory, so that is the one directory written. Its physical path is
# used, the hooks entry must be a real directory (checked below), and a common git directory inside the
# working tree is accepted only under a .git component, a path git never tracks.
case "$common" in
    "$top"/.git|"$top"/.git/*|"$top"/*/.git|"$top"/*/.git/*) ;;
    "$top"/*) fail "the common git directory $common is inside the working tree $top under no .git component, so the files of its hooks directory could be tracked, and a branch could supply a hook" ;;
esac
hooks=$common/hooks
stub=$hooks/pre-commit
body="#!/bin/sh
# OPF pre-commit stub, written by $rel/install.sh (spec 1.3.0 (draft) 14.1). Git runs it from the
# top level of the working tree being committed; it runs the pack hook checked out there, and exits 2
# when that hook is missing or unreadable. Remove this file to uninstall.
hook='./$rel/pre-commit'
if [ ! -f \"\$hook\" ] || [ ! -r \"\$hook\" ]; then
    echo \"opf-pre-commit: cannot evaluate: \$hook is missing or not readable in this working tree\" >&2
    exit 2
fi
sh \"\$hook\"
rc=\$?
case \"\$rc\" in
    0|1|2) exit \"\$rc\" ;;
esac
exit 2"
# True when $1 is the stub: a regular, readable, executable file (not a symbolic link) holding the exact
# stub text, compared byte for byte (a command substitution would drop trailing newlines).
is_stub() {
    [ ! -L "$1" ] && [ -f "$1" ] && [ -r "$1" ] && [ -x "$1" ] && printf '%s\n' "$body" | cmp -s -- - "$1"
}
made_dir=
if [ -L "$hooks" ]; then
    fail "$hooks is a symbolic link; the stub is written only into a real hooks directory in the common git directory $common, never through a link to another repository or a working tree. Replace the link with a directory and run this installer again"
elif [ -e "$hooks" ]; then
    [ -d "$hooks" ] || fail "$hooks is not a directory, so no hook can be written there"
    [ -r "$hooks" ] && [ -x "$hooks" ] && [ -w "$hooks" ] || \
        fail "the hooks directory $hooks cannot be read, searched and written by this user"
    if [ -e "$stub" ] || [ -L "$stub" ]; then
        if is_stub "$stub"; then
            echo "opf-pre-commit install: already installed in this clone ($stub)"
            exit 0
        fi
        fail "$stub is a pre-commit hook that installing would replace; remove it, or have it run ./$rel/pre-commit, first"
    fi
else
    mkdir -- "$hooks" || fail "could not create the hooks directory $hooks"
    made_dir=1
fi
# The stub is written and checked under a temporary name holding this run's process id, then linked into
# place: ln never replaces a file, so a hook that appeared since the check above is kept, and git never
# runs a part-written stub.
tmp=$hooks/.opf-pre-commit-install.$$
[ ! -e "$tmp" ] && [ ! -L "$tmp" ] || fail "$tmp already exists; remove it and run this installer again"
linked=
# Undo what this run wrote: the stub if this run linked it, the temporary file, then the hooks directory
# if this run created it.
rollback() {
    if [ -n "$linked" ]; then
        rm -f -- "$stub" && [ ! -e "$stub" ] && [ ! -L "$stub" ] || \
            fail "$1; the stub $stub could not be removed, so remove it by hand"
    fi
    rm -f -- "$tmp" && [ ! -e "$tmp" ] && [ ! -L "$tmp" ] || \
        fail "$1; the temporary file $tmp could not be removed, so remove it by hand"
    if [ -n "$made_dir" ]; then
        rmdir -- "$hooks" || fail "$1; the files were removed, but the hooks directory $hooks this run created was not"
    fi
    fail "$1; nothing this run wrote is left"
}
( set -C; printf '%s\n' "$body" > "$tmp" ) || rollback "could not write the stub as $tmp"
chmod 755 "$tmp" || rollback "could not make $tmp executable"
is_stub "$tmp" || rollback "$tmp did not read back as a readable, executable file with the exact stub text"
ln -- "$tmp" "$stub" || rollback "could not link the stub into place as $stub (a file there is kept)"
linked=1
is_stub "$stub" || rollback "the stub $stub did not read back as a readable, executable file with the exact stub text"
rm -f -- "$tmp" && [ ! -e "$tmp" ] && [ ! -L "$tmp" ] || rollback "could not remove the temporary file $tmp"
echo "opf-pre-commit install: installed for this clone and its linked worktrees ($stub runs ./$rel/pre-commit)."
echo "opf-pre-commit install: other clones must run this installer too; git commit --no-verify, cherry-pick, revert, rebase, am and a clean merge commit without the hook, and CI does not catch a history violation committed that way (see pre-commit)."
