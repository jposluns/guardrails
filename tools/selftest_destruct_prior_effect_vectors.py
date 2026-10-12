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
    # class 1: a pure name shadow with an inert body, isolating definition tracking from the
    # computed-runner branch (round-4 finding 3)
    ("pe-func-shadows-known-pure", 1, 'echo() { true; }; echo rm -rf src/pkg', "deny"),
    # class 4: an unparseable tail (here-string) must not let the parseable prefix skip the
    # prior-effect run-path pass (round-4 finding 1)
    ("pe-herestring-prefix-run", 4, "cp helper {scratch}/zz && {scratch}/zz; cat <<< ok", "deny"),
    # class 6: each client executable or file-loading meta-command, one vector per form (finding 2)
    ("pe-sqlite-read-pipe", 6, "sqlite3 :memory: '.read \"|rm -rf src/pkg\"'", "deny"),
    ("pe-sqlite-output-pipe-quoted", 6, "sqlite3 :memory: '.output \"|rm -rf src/pkg\"' 'select 1;'", "deny"),
    ("pe-psql-include", 6, "psql -c '\\i /tmp/qa514.sql'", "deny"),
    ("pe-mysql-shell-escape", 6, "mysql -e '\\! rm -rf src/pkg'", "deny"),
    ("pe-mariadb-source", 6, "mariadb -e 'source /tmp/qa514.sql'", "deny"),
    # class 4 on an unparseable command (round-5 blocker): the recover-mode read classifies segments after,
    # between and inside a here-string, a process substitution and an unquoted heredoc
    ("pe-herestring-suffix-run", 4, "cat <<< ok; cp helper {scratch}/zz; {scratch}/zz", "deny"),
    ("pe-procsub-suffix-run", 4, "cat <(printf ok); cp helper {scratch}/zz; {scratch}/zz", "deny"),
    ("pe-heredoc-suffix-run", 4, "cat <<EOF\nok\nEOF\ncp helper {scratch}/zz; {scratch}/zz", "deny"),
    ("pe-herestring-split-run", 4, "cp helper {scratch}/zz; cat <<< ok; {scratch}/zz", "deny"),
    ("pe-procsub-split-run", 4, "cp helper {scratch}/zz; cat <(printf ok); {scratch}/zz", "deny"),
    ("pe-heredoc-split-run", 4, "cp helper {scratch}/zz; cat <<EOF\nok\nEOF\n{scratch}/zz", "deny"),
    ("pe-procsub-diff-suffix", 4, "diff <(echo a) <(echo b); cp helper {scratch}/zz && {scratch}/zz", "deny"),
    ("pe-heredoc-var-suffix", 4, "cat <<EOF\nhi $USER\nEOF\ncp helper {scratch}/zz; {scratch}/zz", "deny"),
    ("pe-herestring-on-creator", 4, "cp helper {scratch}/zz <<< x; {scratch}/zz", "deny"),
    ("pe-herestring-move-run", 4, "cat <<< ok; mv helper {scratch}/zz && {scratch}/zz", "deny"),
    ("pe-herestring-link-run", 4, "cat <<< ok; ln -s {rp}/helper {scratch}/zz; {scratch}/zz", "deny"),
    ("pe-heredoc-script-run", 4, "cat > {scratch}/zz.sh <<EOF\necho $HOME\nEOF\nsh {scratch}/zz.sh", "deny"),
    ("pe-procsub-inside-run", 4, "cp helper {scratch}/zz; cat <({scratch}/zz)", "deny"),
    ("pe-procsub-inside-create", 4, "cat <(cp helper {scratch}/zz); {scratch}/zz", "deny"),
    ("pe-heredoc-body-run", 4, "cp helper {scratch}/zz; cat <<EOF\n$({scratch}/zz)\nEOF", "deny"),
    ("pe-heredoc-body-create", 4, "cat <<EOF\n$(cp helper {scratch}/zz)\nEOF\n{scratch}/zz", "deny"),
    ("pe-heredoc-body-create-same-line", 4, "cat <<EOF; {scratch}/zz\n$(cp helper {scratch}/zz)\nEOF", "deny"),
    ("pe-herestring-word-run", 4, "cp helper {scratch}/zz; cat <<< \"$({scratch}/zz)\"", "deny"),
    ("pe-nested-quote-subst-suffix", 4, "echo \"$(printf \"it's\")\"; cp helper {scratch}/zz; {scratch}/zz", "deny"),
    # the opt-out is not honoured on an unparseable command (round-5 major)
    ("pe-optout-unparseable", 4, "cp helper {scratch}/zz; GUARDRAIL_ALLOW_DESTRUCTIVE=1 {scratch}/zz; cat <<< ok",
     "deny"),
    # a recover-mode stop: a separator after an unbalanced quote, and a NUL, cannot be classified past
    ("pe-stop-separator-after-quote", 4, "cp helper {scratch}/zz; echo \"x; {scratch}/zz", "deny"),
    ("pe-stop-nul", 4, "cp helper {scratch}/zz; {scratch}/zz\x00", "deny"),
    ("pe-stop-nul-simple", 4, "echo ok\x00", "deny"),
    ("pe-stop-case-in-procsub", 4, "cat <(case a in a) echo;; esac); {scratch}/zz", "deny"),
    ("pe-stop-nested-subst", 4,
     "echo \"$(case a in a) echo;; esac)\" <<< x; cp helper {scratch}/zz; {scratch}/zz", "deny"),
    ("pe-stop-nested-heredoc-subst", 4,
     "cat <<EOF\n$(case a in a) cp helper {scratch}/zz;; esac)\nEOF\n{scratch}/zz", "deny"),
    # class 6 after an unparseable construct, and the long and shortcut client spellings
    ("pe-herestring-psql-include", 6, "cat <<< ok; psql -c '\\i /tmp/qa514.sql'", "deny"),
    ("pe-herestring-mariadb-source", 6, "cat <<< ok; mariadb -e 'source /tmp/qa514.sql'", "deny"),
    ("pe-herestring-wrapper-value", 6, "cat <<< ok; nice -n $N make", "deny"),
    ("pe-psql-include-long", 6, "psql -c '\\include /tmp/qa514.sql'", "deny"),
    ("pe-psql-include-relative", 6, "psql -c '\\include_relative qa514.sql'", "deny"),
    ("pe-psql-write-pipe", 6, "psql -c 'select 1 \\w |rm -rf src/pkg'", "deny"),
    ("pe-psql-gx-pipe", 6, "psql -c 'select 1 \\gx |sh'", "deny"),
    ("pe-psql-edit-function", 6, "psql -c '\\ef'", "deny"),
    ("pe-mysql-source-shortcut", 6, "mysql -e '\\. /tmp/qa514.sql'", "deny"),
    ("pe-mariadb-source-shortcut", 6, "mariadb -e '\\. /tmp/qa514.sql'", "deny"),
    ("pe-mysql-pager", 6, "mysql -e 'pager sh'", "deny"),
    ("pe-mysql-edit", 6, "mysql -e '\\e'", "deny"),
    ("pe-sqlite-output-pipe-single", 6, "sqlite3 :memory: \".output '|sh x'\"", "deny"),
    # round 6 (codex Q6): the redesigned reader - a closed certain set, and an uncertain read (a construct
    # that can run a command the reader does not model) denies when the command is also effectful (it
    # creates/copies/links/writes a path, sets a command-valued variable, or names a destructive word).
    # BLOCKER 1: a backslash-newline joins across a heredoc delimiter or substitution the reader cannot track.
    ("pe-heredoc-delim-bs-join", 4, "cat <<EOF\nEO\\\nF\ncp helper {scratch}/zz; {scratch}/zz", "deny"),
    ("pe-heredoc-body-bs-subst", 4, "cp helper {scratch}/zz; cat <<EOF\n$\\\n({scratch}/zz)\nEOF", "deny"),
    # MAJOR 2: coproc runs a literal path the model does not place (unparseable and parseable forms).
    ("pe-coproc-unparseable", 4, "cat <<< ok; cp helper {scratch}/zz; coproc {scratch}/zz", "deny"),
    ("pe-coproc-parseable", 4, "cp helper {scratch}/zz; coproc {scratch}/zz", "deny"),
    # MAJOR 3: the parseable-substitution residual - a written path run inside a $( ) or backtick
    # substitution, in double quotes and unquoted, is now seen on the parseable path too.
    ("pe-parseable-subst-dquote-run", 4, 'cp helper {scratch}/zz; echo "$({scratch}/zz)"', "deny"),
    ("pe-parseable-subst-backtick-run", 4, "cp helper {scratch}/zz; echo `{scratch}/zz`", "deny"),
    ("pe-parseable-subst-unquoted-run", 4, "cp helper {scratch}/zz; echo $({scratch}/zz)", "deny"),
    # select and a function or alias defined and then called in one command are uncertain too.
    ("pe-select-create-run", 4, "cp helper {scratch}/zz; select x in a; do {scratch}/zz; done", "deny"),
    ("pe-funcdef-call-create-run", 4,
     "runner() { {scratch}/zz; }; cp helper {scratch}/zz; runner", "deny"),
    # MINOR 5: one nesting limit covers process substitutions too; beyond it the read is uncertain.
    ("pe-deep-procsub-create-run", 4,
     "cp helper {scratch}/zz; cat " + "<(cat " * 10 + "<({scratch}/zz)" + ")" * 10, "deny"),
]

# Benign shapes that must keep allowing (false-deny controls; none names a destructive word).
PRIOR_EFFECT_CONTROLS = [
    ("pe-control-export-pager-benign", "export PAGER=less; git log -1", "allow"),
    ("pe-control-function-benign", "greet() { echo hi; }; greet", "allow"),
    ("pe-control-mkdir-cd", "mkdir -p {scratch}/build && cd {scratch}/build", "allow"),
    ("pe-control-herestring-benign", "cat <<< ok", "allow"),
    ("pe-control-procsub-benign", "diff <(echo a) <(echo b)", "allow"),
    # the unparseable-command read must keep these benign shapes allowed
    ("pe-control-commit-heredoc", "git commit -F - <<EOF\nfix: thing $USER\nEOF", "allow"),
    ("pe-control-heredoc-to-file", "cat <<EOF > notes.txt\nhello $HOME\nEOF", "allow"),
    ("pe-control-python-heredoc", "python3 - <<EOF\nprint(1)\nEOF", "allow"),
    ("pe-control-grep-herestring", "grep hi <<< hello", "allow"),
    ("pe-control-heredoc-subst", "cat <<EOF > notes.txt\nBuilt $(date)\nEOF", "allow"),
    ("pe-control-procsub-redirect", "while read -r l; do echo $l; done < <(ls)", "allow"),
    ("pe-control-commit-idiom-quotes", "git commit -m \"$(cat <<'EOF'\nFix \"foo's bar\nEOF\n)\"", "allow"),
    ("pe-control-unbalanced-quote", "ls \"unbalanced", "allow"),
    # round 6 (codex Q6): the uncertain constructs with no effect stay allowed, and codex MEDIUM 4
    # (a data 'case' argument) must not deny.
    ("pe-control-coproc-benign", "coproc sleep 1", "allow"),
    ("pe-control-select-benign", "select x in a b; do echo $x; done", "allow"),
    ("pe-control-case-in-procsub", "cat <(printf case)", "allow"),
    ("pe-control-quoted-heredoc-bs", "cat <<'EOF'\nEO\\\nF\nhello\nEOF", "allow"),
    ("pe-control-deep-procsub-benign", "cat " + "<(cat " * 10 + "<(printf ok)" + ")" * 10, "allow"),
]
