#!/bin/sh
# OPF pre-commit floor: the per-clone installer (enforcement pack, spec 1.3.0 (draft) 14.1: the pack MUST
# provide staged-snapshot pre-commit checks with the per-clone installation residual disclosed).
#
#   sh opf/enforcement/precommit/install.sh
#
# It sets core.hooksPath in the local configuration of the clone that holds this file to this directory,
# as a path relative to the repository top level, so git runs the committed pre-commit hook beside it.
# Running it again is a no-op. It refuses (exit 2) and changes nothing when:
#   - this directory is not inside the clone's working tree, or the hook beside it is missing or not
#     executable (git skips a hook it cannot execute);
#   - core.hooksPath is already set, in any configuration scope, to another value: replacing it would
#     silently stop the hooks it names;
#   - the current hooks directory holds a hook (any entry not named *.sample): setting core.hooksPath
#     would silently stop it;
#   - the current hooks directory exists but cannot be listed (an unreadable directory is never read as
#     empty), or its path is not a directory;
#   - git is older than 2.32, the floor the hook needs (see pre-commit).
# Exit status: 0 installed or already installed, 2 refused or cannot evaluate.
#
# Residuals (spec 14.1, disclosed; pre-commit lists them in full): core.hooksPath is local configuration.
# It is not cloned or pushed, so every clone must run this installer; git commit --no-verify still skips
# the hook; a checkout, merge or staged removal that leaves no pre-commit file at this path makes git run
# no hook, silently; and the hook runs the checked-out pre-commit and opf.py. CI (../ci/opf-ci.sh) does
# not close these for the history checks: see pre-commit.
set -u
if [ "$#" -gt 0 ]; then
    echo "opf-pre-commit install: usage: sh install.sh (no operands)" >&2
    exit 2
fi
fail() {
    echo "opf-pre-commit install: refused: $1" >&2
    exit 2
}
# The repository-location variables are dropped, so an inherited GIT_DIR, GIT_WORK_TREE or GIT_CONFIG
# cannot point the install at a repository or file other than the clone that holds this file.
# GIT_CONFIG_PARAMETERS and GIT_CONFIG_COUNT (a `git -c` or alias caller) are dropped too, so a value
# supplied only by the environment cannot read as an installed core.hooksPath.
unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE GIT_OBJECT_DIRECTORY \
    GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_NAMESPACE GIT_CONFIG GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT
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
# CDPATH is cleared for this one command, as in opf-ci.sh: a CDPATH match makes `cd` print a line.
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P) || exit 2
top=$(git -C "$here" rev-parse --show-toplevel) || fail "$here is not inside a git working tree"
top=$(CDPATH= cd -- "$top" && pwd -P) || fail "could not resolve the top level $top"
case "$here" in
    "$top"/*) rel=${here#"$top"/} ;;
    *) fail "$here is not inside the working tree $top" ;;
esac
[ -f "$here/pre-commit" ] && [ -x "$here/pre-commit" ] || \
    fail "$here/pre-commit is missing or not executable, so git would not run it"
current=$(git -C "$top" config --get core.hooksPath)
case "$?" in
    0)
        if [ "$current" = "$rel" ]; then
            echo "opf-pre-commit install: already installed in this clone (core.hooksPath=$rel)"
            exit 0
        fi
        fail "core.hooksPath is already set to $current; replacing it would stop the hooks it names" ;;
    1) ;;
    *) fail "could not read core.hooksPath" ;;
esac
hooks=$(git -C "$top" rev-parse --path-format=absolute --git-path hooks) || fail "could not locate the hooks directory"
# The hooks directory is listed with ls, whose status is checked, never with a glob: a glob over a
# directory it cannot read expands to nothing, and an unreadable directory would read as empty.
if [ -e "$hooks" ] || [ -L "$hooks" ]; then
    [ -d "$hooks" ] || fail "$hooks is not a directory, so the hooks it holds cannot be listed"
    names=$(ls -A -- "$hooks") || fail "could not list the hooks directory $hooks"
    saved_ifs=$IFS
    IFS='
'
    set -f
    for name in $names; do
        case "$name" in
            *.sample) continue ;;
        esac
        fail "$hooks/$name is a hook that setting core.hooksPath would stop; move it into $rel or remove it first"
    done
    set +f
    IFS=$saved_ifs
fi
git -C "$top" config --local core.hooksPath "$rel" || fail "could not set core.hooksPath"
got=$(git -C "$top" config --get core.hooksPath) || fail "core.hooksPath did not read back"
[ "$got" = "$rel" ] || fail "core.hooksPath read back as $got, not $rel"
echo "opf-pre-commit install: installed for this clone only (core.hooksPath=$rel)."
echo "opf-pre-commit install: other clones must run this installer too; git commit --no-verify skips the hook, and CI does not catch a history violation committed that way (see pre-commit)."
