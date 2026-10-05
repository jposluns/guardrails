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
#     stop the hooks it names; a worktree git directory that git cannot read, or a worktrees directory
#     (the registry of linked worktrees) that this user cannot read and search, is refused too;
#   - the hooks directory already holds a pre-commit hook other than this stub, byte for byte, as a
#     regular executable file (a symbolic link is never taken as the stub): replacing it would stop it
#     (other hooks there are left as they are and keep running);
#   - the hooks directory is not a real directory (a symbolic link is refused: it could name another
#     repository's hooks directory, or a directory of a working tree that a branch could fill) or is not
#     one this user can read, search and write;
#   - the hooks directory lies inside the top level of any worktree of the clone (the one it runs from,
#     the main worktree, or a linked worktree its registry names), the common git directory equal to that
#     top level included, unless the common git directory is that worktree's own .git directory: git
#     could track the files of the hooks directory there, and a branch could supply a hook. Run from a
#     linked worktree of a clone whose git directory is not named .git, the top level of the main
#     worktree cannot be found, so it is refused too: run it from the main worktree;
#   - a path it resolves holds a newline;
#   - git is older than 2.32, the floor the hook needs (see pre-commit), or a command it needs (env, sed,
#     dirname, git, cat, cmp, ln) fails (ln fails on a filesystem without hard links).
# The only place it writes is the hooks directory of the clone's common git directory, resolved to its
# physical path: with core.hooksPath unset, the only directory git runs hooks from. It writes the stub
# in a private directory it creates there, checks it, and links it into place (ln never replaces a
# file), checking that nothing is at the stub path just before the link and that the link itself is
# there just after (a directory or a link to one that arrives in between would have ln link into it).
# When a step fails (a write that stops partway on a full disk, say, a stub that does not read back as a
# regular, readable, executable file with the exact stub text, or such an arrival), it removes only what
# this run created, and the hooks directory if it created it, before it exits 2, and names anything it
# could not remove.
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
# the stub silently too; and the stub runs the checked-out pre-commit and opf.py. CI (../ci/opf-ci.sh)
# does not close these for the history checks: see pre-commit.
set -u
if [ "$#" -gt 0 ]; then
    echo "opf-pre-commit install: usage: sh install.sh (no operands)" >&2
    exit 2
fi
# Messages that name a path are printed with printf: some shells' echo reads a backslash in its operand
# as an escape.
fail() {
    printf '%s\n' "opf-pre-commit install: refused: $1" >&2
    exit 2
}
nl='
'
# capture CMD...: run CMD and set got to its output less exactly the one newline it ends with (a command
# substitution alone drops every trailing newline, and so would cut a path that ends with one). It
# returns 1 when CMD fails or its output does not end with a newline, and refuses a path that holds one.
capture() {
    got=$("$@" && echo .) || return 1
    case "$got" in
        *"$nl.") got=${got%"$nl."} ;;
        *) return 1 ;;
    esac
    case "$got" in
        *"$nl"*) fail "the path $got holds a newline, which this installer does not handle" ;;
    esac
}
# The physical path of the directory $1 (CDPATH cleared, as in opf-ci.sh: a CDPATH match makes `cd`
# print a line).
physical() {
    CDPATH= cd -- "$1" && pwd -P
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
capture dirname -- "$0" || fail "could not resolve the directory of $0 (dirname failed)"
dir=$got
[ -n "$dir" ] || fail "could not resolve the directory of $0"
capture physical "$dir" || fail "could not enter $dir"
here=$got
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
capture git -C "$here" rev-parse --show-toplevel || fail "$here is not inside a git working tree"
capture physical "$got" || fail "could not resolve the top level $got"
top=$got
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
capture git -C "$top" rev-parse --path-format=absolute --git-common-dir || \
    fail "could not locate the common git directory"
capture physical "$got" || fail "could not resolve the common git directory $got"
common=$got
hooks=$common/hooks
# The destination must not be a path a worktree could track: the hooks directory may lie inside a
# worktree's top level only below that worktree's own .git directory, a path git never tracks.
untracked_in() {
    case "$hooks" in
        "$1"|"$1"/*)
            [ "$common" = "$1/.git" ] || \
                fail "the hooks directory $hooks is inside the top level $1 of a worktree of this clone, and the common git directory $common is not that worktree's own .git directory, so the files of the hooks directory could be tracked, and a branch could supply a hook" ;;
    esac
}
untracked_in "$top"
# The main worktree's top level. Run from the main worktree it is this one, checked above. Run from a
# linked worktree, there is none in a bare repository, and it is the parent of a common git directory
# named .git when core.worktree does not move it, so the hooks directory is below that worktree's own .git
# directory; otherwise git records it nowhere (git worktree list names the git directory itself), so the
# install is refused.
capture git -C "$top" rev-parse --path-format=absolute --git-dir || fail "could not locate the git directory"
capture physical "$got" || fail "could not resolve the git directory $got"
if [ "$got" != "$common" ]; then
    capture git --git-dir="$common" rev-parse --is-bare-repository || \
        fail "could not read whether the common git directory $common is bare"
    if [ "$got" != true ]; then
        case "$common" in
            */.git) ;;
            *) fail "this is a linked worktree of a clone whose common git directory $common is not named .git, so the top level of the main worktree, which could hold it, cannot be found; run this installer from the main worktree" ;;
        esac
        git --git-dir="$common" config --get core.worktree > /dev/null
        case "$?" in
            1) ;;
            0) fail "core.worktree is set for the main worktree of this clone, so its top level, which could hold the common git directory $common, is not read here; run this installer from the main worktree" ;;
            *) fail "could not read core.worktree for the main worktree of this clone" ;;
        esac
    fi
fi
# The registry of linked worktrees must be listable: a worktrees directory that cannot be read or
# searched would list as empty, and the checks below would skip every linked worktree.
if [ -e "$common/worktrees" ] || [ -L "$common/worktrees" ]; then
    [ -d "$common/worktrees" ] && [ -r "$common/worktrees" ] && [ -x "$common/worktrees" ] || \
        fail "the worktree registry $common/worktrees cannot be read and searched by this user, so the linked worktrees of this clone cannot be checked"
fi
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
    [ "$wtdir" = "$common" ] && continue
    # A linked worktree's top level, from the gitdir file of its entry: the path of its .git file,
    # absolute or relative to the entry. A top level that no longer exists is compared as written.
    [ -f "$wtdir/gitdir" ] && [ -r "$wtdir/gitdir" ] || \
        fail "the gitdir file of the worktree entry $wtdir is missing or not readable, so the top level of that worktree cannot be checked (git worktree prune removes a stale entry)"
    capture cat -- "$wtdir/gitdir" || fail "could not read $wtdir/gitdir"
    case "$got" in
        */.git) wtop=${got%/.git} ;;
        *) fail "$wtdir/gitdir does not name the .git file of a worktree, so the top level of that worktree cannot be checked" ;;
    esac
    case "$wtop" in
        /*) ;;
        *) wtop=$wtdir/$wtop ;;
    esac
    if [ -d "$wtop" ]; then
        capture physical "$wtop" || fail "could not resolve the top level $wtop of a linked worktree"
        wtop=$got
    fi
    untracked_in "$wtop"
done
# The destination, confined by construction: with core.hooksPath unset, git runs hooks only from the
# hooks directory of the common git directory, so that is the one directory written. Its physical path is
# used, it lies in no worktree's tracked paths (checked above), and the hooks entry must be a real
# directory (checked below).
stub=$hooks/pre-commit
body="#!/bin/sh
# OPF pre-commit stub, written by $rel/install.sh (spec 1.3.0 (draft) 14.1). Git runs it from the
# top level of the working tree being committed; it runs the pack hook checked out there, and exits 2
# when that hook is missing or unreadable. Remove this file to uninstall.
hook='./$rel/pre-commit'
if [ ! -f \"\$hook\" ] || [ ! -r \"\$hook\" ]; then
    printf '%s\\n' \"opf-pre-commit: cannot evaluate: \$hook is missing or not readable in this working tree\" >&2
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
            printf '%s\n' "opf-pre-commit install: already installed in this clone ($stub)"
            exit 0
        fi
        fail "$stub is a pre-commit hook that installing would replace; remove it, or have it run ./$rel/pre-commit, first"
    fi
else
    mkdir -- "$hooks" || fail "could not create the hooks directory $hooks"
    made_dir=1
fi
# The stub is written and checked in a private directory this run creates (mkdir never takes an
# existing name, so everything in it is this run's), then linked into place: ln never replaces a file, so
# a hook that appeared since the check above is kept, and git never runs a part-written stub.
priv=$hooks/.opf-pre-commit-install.$$
tmp=$priv/.opf-pre-commit-stub.$$
made_priv=
linked=
strayed=
# Undo what this run created, and nothing else: the stub only while it is still this run's link (the
# same file as the temporary one, or the exact stub text once that is removed), a link ln made inside a
# directory that arrived at the stub path only while it is the same file as the temporary one, then the
# private directory and the hooks directory if this run created them. Anything it cannot remove is named.
rollback() {
    left=
    if [ -n "$linked" ] && [ ! -L "$stub" ]; then
        if [ "$stub" -ef "$tmp" ] || { [ ! -e "$tmp" ] && is_stub "$stub"; }; then
            rm -f -- "$stub"
            [ ! -e "$stub" ] && [ ! -L "$stub" ] || left="$left $stub"
        fi
    fi
    if [ -n "$strayed" ] && [ "$strayed" -ef "$tmp" ]; then
        rm -f -- "$strayed"
        [ ! "$strayed" -ef "$tmp" ] || left="$left $strayed"
    fi
    if [ -n "$made_priv" ]; then
        rm -f -- "$tmp"
        rmdir -- "$priv" || left="$left $priv"
    fi
    if [ -n "$made_dir" ]; then
        rmdir -- "$hooks" || left="$left $hooks"
    fi
    [ -z "$left" ] || fail "$1; this run could not remove what it wrote at:$left; remove it by hand"
    fail "$1; nothing this run wrote is left"
}
mkdir -m 700 -- "$priv" || rollback "could not create the private directory $priv (if an earlier run left it, remove it)"
made_priv=1
( set -C; printf '%s\n' "$body" > "$tmp" ) || rollback "could not write the stub as $tmp"
chmod 755 "$tmp" || rollback "could not make $tmp executable"
is_stub "$tmp" || rollback "$tmp did not read back as a readable, executable file with the exact stub text"
[ ! -e "$stub" ] && [ ! -L "$stub" ] || rollback "something appeared at $stub while the stub was being written (it is kept)"
ln -- "$tmp" "$stub" || \
    rollback "could not link the stub into place as $stub (anything there is kept; ln also fails on a filesystem without hard links)"
# ln links into a directory, or a link to one, that arrived at the stub path since the check above,
# rather than failing: the stub path is this run's only when it is now the same file as the temporary one.
if [ ! -L "$stub" ] && [ "$stub" -ef "$tmp" ]; then
    linked=1
else
    strayed=$stub/${tmp##*/}
    rollback "something arrived at $stub while the stub was being placed (it is kept)"
fi
is_stub "$stub" || rollback "the stub $stub did not read back as a readable, executable file with the exact stub text"
rm -f -- "$tmp" && [ ! -e "$tmp" ] && [ ! -L "$tmp" ] || rollback "could not remove the temporary file $tmp"
rmdir -- "$priv" || rollback "could not remove the private directory $priv"
made_priv=
printf '%s\n' "opf-pre-commit install: installed for this clone and its linked worktrees ($stub runs ./$rel/pre-commit)."
echo "opf-pre-commit install: other clones must run this installer too; git commit --no-verify, cherry-pick, revert, rebase, am and a clean merge commit without the hook, and CI does not catch a history violation committed that way (see pre-commit)."
