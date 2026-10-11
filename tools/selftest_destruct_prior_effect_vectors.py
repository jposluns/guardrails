"""Inert test data for the destructive-operation guard's prior-effect rule (D-514).

Each vector is a STRING handed to aiqt_hooks.destructive_guard by tools/selftest_aiqt_hooks.py; nothing here
is ever executed. Placeholders: {rp} the fixture repository, {scratch} the fixture scratch root, {absent} a
path the suite never creates; substitute them with str.replace, never str.format. Targets sit inside the fixture repository (src/pkg is tracked and clean), so a
classifier that wrongly trusts an earlier segment's meaning ALLOWS and the vector discriminates. Authored by
the maintainer's orchestrator as reviewed defensive test data (D-514-VECTORS-BY-ORCHESTRATOR).
"""

# (label, defect class, command template, expected)
PRIOR_EFFECT_VECTORS = [
    # class 1: a definition in the same command shadows a trusted name
    ("pe-func-shadows-known", 1, 'echo() { eval "$*"; }; echo rm -rf src/pkg', "deny"),
    ("pe-function-keyword-shadows-known", 1, 'function echo { eval "$@"; }; echo rm -rf src/pkg', "deny"),
    ("pe-func-shadows-wrapper", 1, 'nice() { eval "$*"; }; nice echo rm -rf src/pkg', "deny"),
    ("pe-alias-shadows-known", 1, "shopt -s expand_aliases; alias echo='bash -c'; echo 'rm -rf src/pkg'", "deny"),
    ("pe-func-shadows-cd", 1, "cd() { true; }; cd {scratch}/d && rm -rf src/pkg", "deny"),
    # class 2: state set by one segment and used by a later one
    ("pe-export-pager", 2, "export GIT_PAGER='rm -rf src/pkg'; git log", "deny"),
    ("pe-bare-then-export-pager", 2, "GIT_PAGER='rm -rf src/pkg'; export GIT_PAGER; git log -1", "deny"),
    ("pe-export-external-diff", 2, "export GIT_EXTERNAL_DIFF='rm -rf src/pkg'; git diff", "deny"),
    ("pe-git-config-pager", 2, "git config core.pager 'rm -rf src/pkg' && git log", "deny"),
    ("pe-git-config-alias", 2, "git config alias.zz '!rm -rf src/pkg' && git zz", "deny"),
    ("pe-clone-ssh-command", 2, "git clone -c core.sshCommand='rm -rf src/pkg' ssh://h/r {absent}/c", "deny"),
    # class 3: a computed or globbed command word behind a runner
    ("pe-xargs-variable-word", 3, "X=rm; echo src/pkg | xargs $X -rf", "deny"),
    ("pe-flock-variable-word", 3, "X=rm; flock {scratch}/l $X -rf src/pkg", "deny"),
    ("pe-xargs-glob-word", 3, "echo src/pkg | xargs /bin/r? -rf", "deny"),
    ("pe-find-exec-glob-word", 3, "find src/pkg -maxdepth 0 -exec /bin/r? -rf {} +", "deny"),
    # class 4: an executable created, copied, linked or written, then run
    ("pe-copy-then-run", 4, "cp /bin/rm {scratch}/zz && {scratch}/zz -rf src/pkg", "deny"),
    ("pe-link-then-run", 4, "ln -s /bin/rm {scratch}/zz && {scratch}/zz -rf src/pkg", "deny"),
    ("pe-write-script-then-run", 4, "echo 'rm -rf src/pkg' > {scratch}/s.sh; bash {scratch}/s.sh", "deny"),
    # class 5: a filesystem fact the hook probes is changed by an earlier segment
    ("pe-link-then-remove-through", 5, "ln -s {rp} {scratch}/qa && rm -rf {scratch}/qa/src/pkg", "deny"),
    ("pe-move-then-remove", 5, "mv notes.txt gone && rm -rf gone", "deny"),
    # class 6: client shell-escapes and wrapper option values that expand
    ("pe-psql-shell-escape", 6, "psql -c '\\! rm -rf src/pkg'", "deny"),
    ("pe-sqlite-shell", 6, "sqlite3 :memory: '.shell rm -rf src/pkg'", "deny"),
    ("pe-env-option-value-expands", 6, "X='IGNORE rm -rf src/pkg'; env -u $X echo", "deny"),
]

# Benign shapes that must keep allowing (false-deny controls; none names a destructive word).
PRIOR_EFFECT_CONTROLS = [
    ("pe-control-export-pager-benign", "export PAGER=less; git log -1", "allow"),
    ("pe-control-function-benign", "greet() { echo hi; }; greet", "allow"),
    ("pe-control-mkdir-cd", "mkdir -p {scratch}/build && cd {scratch}/build", "allow"),
]
