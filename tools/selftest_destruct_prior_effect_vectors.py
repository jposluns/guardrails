"""Inert test data for the destructive-operation guard's prior-effect rule.

Each vector is a STRING handed to aiqt_hooks.destructive_guard by tools/selftest_aiqt_hooks.py; nothing here
is ever executed. Placeholders: {rp} the fixture repository, {scratch} the fixture scratch root, {absent} a
path the suite never creates; substitute them with str.replace, never str.format. Targets sit inside the fixture repository (src/pkg is tracked and clean), so a
classifier that wrongly trusts an earlier segment's meaning ALLOWS and the vector discriminates. Authored by
the maintainer's orchestrator as reviewed defensive test data.

A function definition or a coproc now opens a WHOLE-TAIL word
judgment over every later segment of its command, so a row whose probe must be judged by the
DIRECT route spells its uncertainty wrapper AFTER the probe (PROBE; zzf() { ...; }; zzf), and a
row that spells a definition or coproc ahead of later words exercises the tail judgment itself.
"""

# Named template characters for a newline, a backslash or a quote, as PLAIN string literals and
# ordinary escapes (no chr() construction): _NL newline, _BS backslash,
# _SQ single quote, _DQ double quote, _BT backtick.
_NL = "\n"
_BS = "\\"
_SQ = "'"
_DQ = '"'
_BT = "`"
_SUP2 = "\u00b2"  # a Unicode digit (superscript two) that bash treats as a FILENAME

# (label, defect class, command template, expected)
PRIOR_EFFECT_VECTORS = [
    # class 1: a definition in the same command shadows a trusted name
    ("pe-func-shadows-known", 1, 'echo() { eval "$*"; }; echo rm -rf src/pkg', "deny"),
    ("pe-function-keyword-shadows-known", 1, 'function echo { eval "$@"; }; echo rm -rf src/pkg', "deny"),
    ("pe-func-shadows-wrapper", 1, 'nice() { eval "$*"; }; nice echo rm -rf src/pkg', "deny"),
    ("pe-alias-shadows-known", 1, "shopt -s expand_aliases; alias echo='bash -c'; echo 'rm -rf src/pkg'", "deny"),
    # (a cd function definition carries a cd word ended by a separator, which cd-gate already voids the cwd
    # for, so the shadowed-cd witness here is the alias spelling, which carries no cd word)
    ("pe-alias-shadows-cd", 1, "shopt -s expand_aliases; alias cd=true; cd {scratch}/d && rm -rf src/pkg", "deny"),
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
    # computed-runner branch
    ("pe-func-shadows-known-pure", 1, 'echo() { true; }; echo rm -rf src/pkg', "deny"),
    # class 4: an unparseable tail (here-string) must not let the parseable prefix skip the
    # prior-effect run-path pass
    ("pe-herestring-prefix-run", 4, "cp helper {scratch}/zz && {scratch}/zz; cat <<< ok", "deny"),
    # class 6: each client executable or file-loading meta-command, one vector per form
    ("pe-sqlite-read-pipe", 6, "sqlite3 :memory: '.read \"|rm -rf src/pkg\"'", "deny"),
    ("pe-sqlite-output-pipe-quoted", 6, "sqlite3 :memory: '.output \"|rm -rf src/pkg\"' 'select 1;'", "deny"),
    ("pe-psql-include", 6, "psql -c '\\i /tmp/inc.sql'", "deny"),
    ("pe-mysql-shell-escape", 6, "mysql -e '\\! rm -rf src/pkg'", "deny"),
    ("pe-mariadb-source", 6, "mariadb -e 'source /tmp/inc.sql'", "deny"),
    # class 4 on an unparseable command: the recover-mode read classifies segments after,
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
    # a leading GUARDRAIL_ALLOW_DESTRUCTIVE assignment (the removed opt-out) changes nothing on an unparseable
    # command: the run of the copied path still denies
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
    ("pe-herestring-psql-include", 6, "cat <<< ok; psql -c '\\i /tmp/inc.sql'", "deny"),
    ("pe-herestring-mariadb-source", 6, "cat <<< ok; mariadb -e 'source /tmp/inc.sql'", "deny"),
    ("pe-herestring-wrapper-value", 6, "cat <<< ok; nice -n $N make", "deny"),
    ("pe-psql-include-long", 6, "psql -c '\\include /tmp/inc.sql'", "deny"),
    ("pe-psql-include-relative", 6, "psql -c '\\include_relative inc.sql'", "deny"),
    ("pe-psql-write-pipe", 6, "psql -c 'select 1 \\w |rm -rf src/pkg'", "deny"),
    ("pe-psql-gx-pipe", 6, "psql -c 'select 1 \\gx |sh'", "deny"),
    ("pe-psql-edit-function", 6, "psql -c '\\ef'", "deny"),
    ("pe-mysql-source-shortcut", 6, "mysql -e '\\. /tmp/inc.sql'", "deny"),
    ("pe-mariadb-source-shortcut", 6, "mariadb -e '\\. /tmp/inc.sql'", "deny"),
    ("pe-mysql-pager", 6, "mysql -e 'pager sh'", "deny"),
    ("pe-mysql-edit", 6, "mysql -e '\\e'", "deny"),
    ("pe-sqlite-output-pipe-single", 6, "sqlite3 :memory: \".output '|sh x'\"", "deny"),
    # The redesigned reader - a closed certain set, and an uncertain read (a construct
    # that can run a command the reader does not model) denies when the command is also effectful (it
    # creates/copies/links/writes a path, sets a command-valued variable, or names a destructive word).
    # A backslash-newline joins across a heredoc delimiter or substitution the reader cannot track.
    ("pe-heredoc-delim-bs-join", 4, "cat <<EOF\nEO\\\nF\ncp helper {scratch}/zz; {scratch}/zz", "deny"),
    ("pe-heredoc-body-bs-subst", 4, "cp helper {scratch}/zz; cat <<EOF\n$\\\n({scratch}/zz)\nEOF", "deny"),
    # Coproc runs a literal path the model does not place (unparseable and parseable forms).
    ("pe-coproc-unparseable", 4, "cat <<< ok; cp helper {scratch}/zz; coproc {scratch}/zz", "deny"),
    ("pe-coproc-parseable", 4, "cp helper {scratch}/zz; coproc {scratch}/zz", "deny"),
    # The parseable-substitution residual - a written path run inside a $( ) or backtick
    # substitution, in double quotes and unquoted, is now seen on the parseable path too.
    ("pe-parseable-subst-dquote-run", 4, 'cp helper {scratch}/zz; echo "$({scratch}/zz)"', "deny"),
    ("pe-parseable-subst-backtick-run", 4, "cp helper {scratch}/zz; echo `{scratch}/zz`", "deny"),
    # Red on revert - the base discarded a still-runs stop inside a parseable
    # substitution and fell back to the plain read, so the written path ran unseen there
    ('pe-parseable-fallback-stillruns-run', 4,
     'cp helper {scratch}/zz; echo "$(case x in x) {scratch}/zz;; esac)"', 'deny'),
    # select and a function or alias defined and then called in one command are uncertain too.
    # Red on revert - the base creator list had no install, so this select form allowed there
    ('pe-select-install-run', 4, 'install helper {scratch}/zz; select x in a; do {scratch}/zz; done', 'deny'),
    ("pe-funcdef-call-create-run", 4,
     "runner() { {scratch}/zz; }; cp helper {scratch}/zz; runner", "deny"),
    # Red on revert - the base creator list had no rsync and no funcdef-call uncertainty
    # (the depth-limit class keeps pe-deep-procsub-redir-run and its benign control below)
    ('pe-funcdef-rsync-run', 4, 'zzgo() { {scratch}/zz; }; rsync helper {scratch}/zz; zzgo', 'deny'),
    # The editor and pager meta-commands - psql \ev, mysql \P, mariadb \e / \P / pager / edit
    ('pe-psql-edit-view', 6, "psql -c '\\ev'", 'deny'),
    ('pe-mysql-pager-shortcut', 6, "mysql -e '\\P sh'", 'deny'),
    ('pe-mariadb-edit-shortcut', 6, "mariadb -e '\\e'", 'deny'),
    ('pe-mariadb-pager-shortcut', 6, "mariadb -e '\\P sh'", 'deny'),
    ('pe-mariadb-pager-word', 6, "mariadb -e 'pager sh'", 'deny'),
    ('pe-mariadb-edit-word', 6, "mariadb -e 'edit'", 'deny'),
    # An uncertain read (nesting limit, heredoc backslash-newline) with a
    # raw-visible redirect write is effectful and denies; base denied the depth form, missed the heredoc form
    ('pe-deep-procsub-redir-run', 4,
     "cat " + "<(cat " * 10 + "<(printf ok > {scratch}/zz; {scratch}/zz)" + ")" * 10, 'deny'),
    ('pe-heredoc-delim-bs-redir', 4, 'cat <<EOF\nEO\\\nF\nprintf ok > {scratch}/zz; {scratch}/zz', 'deny'),
    # One deny vector per writer creator on an uncertain read (a funcdef-call
    # or a coproc), each red when only its own writer pattern is removed (the writer-* branch mutants);
    # the option-absent controls live in PRIOR_EFFECT_CONTROLS.
    ('pe-writer-curl-run', 4, 'zzf() { {scratch}/zz; }; curl -so {scratch}/zz http://127.0.0.1/x; zzf', 'deny'),
    ('pe-writer-wget-run', 4, 'zzf() { {scratch}/zz; }; wget -O {scratch}/zz http://127.0.0.1/x; zzf', 'deny'),
    ('pe-writer-tar-run', 4, 'tar -xf a.tar -C {scratch}; coproc {scratch}/zz', 'deny'),
    ('pe-writer-unzip-run', 4, 'unzip a.zip -d {scratch}; coproc {scratch}/zz', 'deny'),
    ('pe-writer-chmod-run', 4, 'chmod +x {scratch}/zz; coproc {scratch}/zz', 'deny'),
    ('pe-writer-interp-run', 4,
     "zzf() { {scratch}/zz; }; python3 -c 'open(\"{scratch}/zz\",\"w\")'; zzf", "deny"),
    # Witnesses for the subst-restrict and case-reserved branch mutants; both ALSO deny on
    # the base (isolation witnesses, not revert discriminators - those are the replaced vectors above).
    ("pe-unquoted-subst-sql-run", 6, "echo $(psql -c '\\! sh')", "deny"),
    ("pe-case-reserved-procsub-run", 4,
     "cp helper {scratch}/zz; cat <(! case x in x) {scratch}/zz;; esac)", "deny"),
    # The time reserved word's OPTION words (-p, --) must not break the
    # proved-argument read, or a timed case statement slips the nested-case refusal; every
    # reproduction, red on revert (an earlier hook allows each one; they are also _repro entries).
    ('pe-time-p-case-procsub-run', 4,
     'cp helper {scratch}/zz; cat <(time -p case x in x) {scratch}/zz;; esac)', 'deny'),
    ('pe-time-dashdash-case-procsub-run', 4,
     'cp helper {scratch}/zz; cat <(time -- case x in x) {scratch}/zz;; esac)', 'deny'),
    ('pe-time-p-dashdash-case-procsub-run', 4,
     'cp helper {scratch}/zz; cat <(time -p -- case x in x) {scratch}/zz;; esac)', 'deny'),
    ('pe-bang-time-p-case-procsub-run', 4,
     'cp helper {scratch}/zz; cat <(! time -p case x in x) {scratch}/zz;; esac)', 'deny'),
    ('pe-time-p-case-subst-run', 4,
     'cp helper {scratch}/zz; echo $(time -p case x in x) {scratch}/zz;; esac)', 'deny'),
    ('pe-time-p-case-quoted-subst-run', 4,
     'cp helper {scratch}/zz; echo "$(time -p case x in x) {scratch}/zz;; esac)"', 'deny'),
    # REDESIGN: on an uncertain read the writer signal reads DECODED command words
    # against a closed creator set and denies REGARDLESS of options; every spelling, red on revert
    # (the earlier per-option raw-text regexes allow each one).
    ('pe-writer-curl-quoted-name', 4,
     "c''url -so {scratch}/zz http://127.0.0.1/x; zzf() { {scratch}/zz; }; zzf", 'deny'),
    ('pe-writer-curl-quoted-mid', 4,
     "cu''rl -so {scratch}/zz http://127.0.0.1/x; zzf() { {scratch}/zz; }; zzf", 'deny'),
    ('pe-writer-wget-quoted-name', 4,
     "w''get -O {scratch}/zz http://127.0.0.1/x; zzf() { {scratch}/zz; }; zzf", 'deny'),
    ('pe-writer-tar-quoted-name', 4, "t''ar -xf a.tar -C {scratch}; coproc {scratch}/zz", 'deny'),
    ('pe-writer-unzip-quoted-name', 4, "un''zip a.zip -d {scratch}; coproc {scratch}/zz", 'deny'),
    ('pe-writer-chmod-quoted-name', 4, "ch''mod +x {scratch}/zz; coproc {scratch}/zz", 'deny'),
    ('pe-writer-python-quoted-name', 4,
     'py\'\'thon3 -c \'open("{scratch}/zz","w")\'; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-writer-install-quoted-name', 4,
     "in''stall helper {scratch}/zz; select x in a; do {scratch}/zz; done", 'deny'),
    ('pe-writer-rsync-quoted-name', 4, "zzgo() { {scratch}/zz; }; rsy''nc helper {scratch}/zz; zzgo", 'deny'),
    ('pe-writer-curl-sslo', 4, 'curl -sSLo {scratch}/zz http://127.0.0.1/x; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-writer-curl-lo', 4, 'curl -Lo {scratch}/zz http://127.0.0.1/x; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-writer-curl-fsslo', 4, 'curl -fsSLo {scratch}/zz http://127.0.0.1/x; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-writer-curl-remote-name', 4, 'curl --remote-name http://127.0.0.1/x; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-writer-curl-quoted-separator-url', 4,
     "curl 'http://127.0.0.1/x?a=1&b=2' -so {scratch}/zz; zzf() { {scratch}/zz; }; zzf", 'deny'),
    ('pe-writer-curl-quoted-separator-header', 4,
     "curl -H 'A: b;c' -so {scratch}/zz http://127.0.0.1/x; zzf() { {scratch}/zz; }; zzf", 'deny'),
    ('pe-writer-curl-plain-url', 4, 'curl http://127.0.0.1/x; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-writer-sudo-curl', 4,
     'zzf() { {scratch}/zz; }; sudo curl -so {scratch}/zz http://127.0.0.1/x; zzf', 'deny'),
    ('pe-writer-wget-qo', 4, 'wget -qO {scratch}/zz http://127.0.0.1/x; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-writer-wget-prefix-dir', 4, 'wget -P {scratch} http://127.0.0.1/x; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-writer-wget-plain-url', 4, 'wget http://127.0.0.1/x; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-writer-tar-directory-eq', 4, 'tar -xf a.tar --directory={scratch}; coproc {scratch}/zz', 'deny'),
    ('pe-writer-tar-xvc', 4, 'tar -xvC {scratch} -f a.tar; coproc {scratch}/zz', 'deny'),
    ('pe-writer-tar-no-chdir', 4, 'tar -xf a.tar; coproc {scratch}/zz', 'deny'),
    ('pe-writer-unzip-qd', 4, 'unzip -qd {scratch} a.zip; coproc {scratch}/zz', 'deny'),
    ('pe-writer-unzip-no-destdir', 4, 'unzip a.zip; coproc {scratch}/zz', 'deny'),
    ('pe-writer-chmod-755', 4, 'chmod 755 {scratch}/zz; coproc {scratch}/zz', 'deny'),
    ('pe-writer-chmod-arx', 4, 'chmod a+rx {scratch}/zz; coproc {scratch}/zz', 'deny'),
    ('pe-writer-chmod-urwx', 4, 'chmod u=rwx {scratch}/zz; coproc {scratch}/zz', 'deny'),
    ('pe-writer-chmod-600', 4, 'chmod 600 {scratch}/zz; coproc {scratch}/zz', 'deny'),
    ('pe-writer-python-versioned', 4,
     'python3.12 -c \'open("{scratch}/zz","w")\'; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-writer-python-isolated-c', 4,
     'python3 -Ic \'open("{scratch}/zz","w")\'; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-writer-python-script', 4, 'python3 {scratch}/s.py; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-writer-perl-pe', 4, "perl -pe 's/a/b/' {scratch}/x; zzf() { {scratch}/zz; }; zzf", 'deny'),
    ('pe-writer-perl-cap-e', 4, "perl -E 'say 1'; zzf() { {scratch}/zz; }; zzf", 'deny'),
    ('pe-writer-node-eval', 4, "node --eval 'x'; zzf() { {scratch}/zz; }; zzf", 'deny'),
    # '>&WORD' with a non-numeric WORD and the 'N<>PATH' read-write open each CREATE
    # their path; the redirect-write signal now recognizes them (red on revert: an earlier hook allows both).
    ('pe-redir-ampersand-word', 4, 'cat <<EOF\nEO\\\nF\nprintf ok >&{scratch}/zz; {scratch}/zz', 'deny'),
    ('pe-redir-read-write-open', 4, 'cat <<EOF\nEO\\\nF\nprintf ok 1<>{scratch}/zz; {scratch}/zz', 'deny'),
    # Isolating witnesses for the cmdvar and recover-depth branch mutants, and liveness pins
    # for the raw-text creator backstop (a creator word the recover read does not place as a command
    # word, a disclosed over-match toward deny).
    ('pe-cmdvar-uncertain-assign', 2, 'zzq() { git log; }; GIT_PAGER=less; zzq', 'deny'),
    ('pe-depth-stop-curl-plain', 4,
     'curl http://127.0.0.1/x; ' + 'cat ' + '<(cat ' * 10 + '<(printf ok)' + ')' * 10, 'deny'),
    ('pe-raw-creator-heredoc-swallow', 4,
     'cat <<EOF\nEO\\\nF\ninstall helper {scratch}/zz; {scratch}/zz', 'deny'),
    ('pe-raw-creator-argument', 4, 'printf cp; zzf() { {scratch}/zz; }; zzf', 'deny'),
    # A STOPPED heredoc read (an unquoted backslash-newline join bash joins
    # before matching the delimiter) swallows a creator TAIL the recover read never structures as a
    # command. Every such stop denies OUTRIGHT through recover-stop,
    # whatever the tail spells; these four spellings stay pinned in the corpus.
    ('pe-stopped-heredoc-curl', 4,
     'cat <<EOF\nEO\\\nF\ncurl -so {scratch}/zz http://127.0.0.1/x; {scratch}/zz', 'deny'),
    ('pe-stopped-heredoc-wget', 4,
     'cat <<EOF\nEO\\\nF\nwget -O {scratch}/zz http://127.0.0.1/x; {scratch}/zz', 'deny'),
    ('pe-stopped-heredoc-python', 4,
     'cat <<EOF\nEO\\\nF\npython3 -c \'open("{scratch}/zz","w")\'; {scratch}/zz', 'deny'),
    ('pe-stopped-heredoc-ccurl', 4,
     'cat <<EOF\nEO\\\nF\nc\'\'url -so {scratch}/zz http://127.0.0.1/x; {scratch}/zz', 'deny'),
    # A creator a coproc runs, in the PLAIN spelling: the raw writer
    # backstop and the coproc command-position scan both see it; the quoted
    # spellings that only the decoded scan can see are the pe-coproc-*-quoted-cp vectors below.
    ('pe-coproc-curl-o', 4, 'coproc curl -o {scratch}/f http://127.0.0.1/x', 'deny'),
    # A liveness witness for every closed-set creator member,
    # each pinned live by its own per-word mutant. Each uses a spelling no RAW backstop can see (a quoted
    # name the writer/creator regexes miss, or an interpreter with a SCRIPT arg the writer -c/-e regex
    # does not match), so only the decoded creator set denies it and dropping that member turns red.
    ('pe-creator-dd-quoted', 4, "d''d; zzf() { {scratch}/zz; }; zzf", 'deny'),
    ('pe-creator-tee-quoted', 4, "t''ee; zzf() { {scratch}/zz; }; zzf", 'deny'),
    ('pe-creator-mkdir-quoted', 4, "mk''dir; zzf() { {scratch}/zz; }; zzf", 'deny'),
    ('pe-creator-touch-quoted', 4, "tou''ch; zzf() { {scratch}/zz; }; zzf", 'deny'),
    ('pe-creator-ruby', 4, 'ruby {scratch}/s.rb; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-creator-bash', 4, 'bash {scratch}/s.sh; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-creator-sh', 4, 'sh {scratch}/s.sh; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-creator-zsh', 4, 'zsh {scratch}/s.sh; zzf() { {scratch}/zz; }; zzf', 'deny'),
    # Interpreters beyond the first creator list, and a versioned-name
    # rule per family (perl5.36.0, ruby3.2, pypy3). Each ALLOWS on an earlier hook (red against it).
    ('pe-creator-ksh', 4, 'ksh {scratch}/s.sh; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-creator-dash', 4, 'dash {scratch}/s.sh; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-creator-fish', 4, 'fish {scratch}/s.fish; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-creator-php', 4, 'php {scratch}/s.php; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-creator-lua', 4, 'lua {scratch}/s.lua; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-creator-awk', 4, 'awk 1 {scratch}/x; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-creator-gawk', 4, 'gawk 1 {scratch}/x; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-creator-nodejs', 4, 'nodejs {scratch}/s.js; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-creator-perl-versioned', 4, 'perl5.36.0 {scratch}/s.pl; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-creator-ruby-versioned', 4, 'ruby3.2 {scratch}/s.rb; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-creator-pypy3', 4, 'pypy3 {scratch}/s.py; zzf() { {scratch}/zz; }; zzf', 'deny'),
    # The declaration members an earlier corpus did not pin (its suite stayed
    # green with cp, mv, ln, csh, tcsh, pypy and python dropped), each in a spelling no raw signal can
    # see; the GENERATED liveness loop in the self-test reconciles the rest of the declaration.
    ('pe-creator-cp-quoted', 4, "zzf() { {scratch}/zz; }; c''p a b; zzf", 'deny'),
    ('pe-creator-mv-quoted', 4, "zzf() { {scratch}/zz; }; m''v a b; zzf", 'deny'),
    ('pe-creator-ln-quoted', 4, "zzf() { {scratch}/zz; }; l''n a b; zzf", 'deny'),
    ('pe-creator-csh-quoted', 4, "c''sh s.csh; zzf() { {scratch}/zz; }; zzf", 'deny'),
    ('pe-creator-tcsh-quoted', 4, "t''csh s.csh; zzf() { {scratch}/zz; }; zzf", 'deny'),
    ('pe-creator-pypy-quoted', 4, "p''ypy s.py; zzf() { {scratch}/zz; }; zzf", 'deny'),
    ('pe-creator-python-quoted', 4, "p''ython s.py; zzf() { {scratch}/zz; }; zzf", 'deny'),
    ('pe-creator-perl-quoted', 4, "p''erl s.pl; zzf() { {scratch}/zz; }; zzf", 'deny'),
    ('pe-creator-node-quoted', 4, "n''ode s.js; zzf() { {scratch}/zz; }; zzf", 'deny'),
    # ANY recover-mode stop that leaves text at
    # or after the stop point unread is cannot-evaluate and DENIES OUTRIGHT, whatever that text spells
    # (recover-stop). The reproductions: a stop INSIDE a swallowed heredoc body (a stray quote
    # in body data, a descriptor process substitution, a case statement, the nesting limit) and a
    # depth-limit stop ahead of a quote-concatenated creator tail; each ALLOWS on an earlier
    # hook (red against it) because the body re-read discarded its own failure value.
    ('pe-stop-body-strayquote', 4,
     "cat <<EOF\nignored '\nEO\\\nF\nc''p helper {scratch}/zz; {scratch}/zz", 'deny'),
    ('pe-stop-body-descriptor-procsub', 4,
     "cat <<EOF\nEO\\\nF\ncat 3<(true); c''p helper {scratch}/zz; {scratch}/zz", 'deny'),
    ('pe-stop-body-case', 4,
     "cat <<EOF\nEO\\\nF\ncat <(case x in x) y;; esac); c''p helper {scratch}/zz; {scratch}/zz",
     'deny'),
    ('pe-stop-body-depth', 4,
     "cat <<EOF\nEO\\\nF\n" + "cat " + "<(cat " * 10 + "<(printf ok)" + ")" * 10
     + "; c''p helper {scratch}/zz; {scratch}/zz", 'deny'),
    ('pe-stop-depth-quoted-creator', 4,
     "cat " + "<(cat " * 10 + "<(printf ok)" + ")" * 10
     + "; t''ouch {scratch}/zz; chmod 700 {scratch}/zz; {scratch}/zz", 'deny'),
    # The nesting-limit boundary, both ends: ONE group past the limit stops the read and
    # denies even a fully benign command; the just-inside depth is an allow control below.
    ('pe-stop-depth-benign', 4, "cat " + "<(cat " * 8 + "<(printf ok)" + ")" * 8, 'deny'),
    # On an UNCERTAIN read, an unmodelled runner's EXECUTED command word and
    # the word right after a find action are read against the creator set - and ONLY those words. The
    # certain-read forms (xvfb-run python3, firejail bash, find . -exec bash -n {} +) are allow
    # controls below, the parent-pin behaviour.
    ('pe-runner-xargs-quoted-cp', 4, "xargs c''p; zzf() { {scratch}/zz; }; zzf", 'deny'),
    ('pe-runner-watch-quoted-cp', 4, "watch c''p a b; zzf() { {scratch}/zz; }; zzf", 'deny'),
    ('pe-find-exec-quoted-cp', 4, "find src -exec c''p {} +; zzf() { {scratch}/zz; }; zzf", 'deny'),
    # Coproc is a COMMAND POSITION; a decoded creator after it denies in the
    # plain AND the NAME form, quoted spellings included (both allowed on an earlier hook).
    ('pe-coproc-quoted-cp', 4, "coproc c''p a b", 'deny'),
    ('pe-coproc-named-quoted-cp', 4, "coproc zznm { c''p a b; }", 'deny'),
    # The versioned-name rule covers EVERY interpreter family in the
    # declaration (an earlier hook allowed all three of these; red against it).
    ('pe-creator-bash-versioned', 4, 'bash5 {scratch}/s.sh; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-creator-ksh-versioned', 4, 'ksh93 {scratch}/s.sh; zzf() { {scratch}/zz; }; zzf', 'deny'),
    ('pe-creator-nodejs-versioned', 4, 'nodejs20 {scratch}/s.js; zzf() { {scratch}/zz; }; zzf', 'deny'),
    # The '>&WORD' rule also covers a WORD of digits then '-' then more
    # ('>&2-x' writes the file '2-x'); the dup, move and close spellings are allow controls below. The
    # raw-regex witnesses put the redirect in a DATA argument, which only the raw signal can see, so the
    # redir-write mutants flip them; the direct '>&2-x' spelling was already denied through the lexed
    # redirect record at the pin and stays pinned by the corpus.
    ('pe-redir-fd-minus-word', 4, 'zzf() { {scratch}/zz; }; printf ok >&2-x; zzf', 'deny'),
    ('pe-redir-raw-word-arg', 4, 'zzf() { {scratch}/zz; }; printf " >&zqfile "; zzf', 'deny'),
    ('pe-redir-raw-digits-minus-arg', 4, 'zzf() { {scratch}/zz; }; printf " >&2-x "; zzf', 'deny'),
    # Per-pattern liveness witnesses for the raw writer backstop, each a DATA argument only
    # that one raw pattern sees (printf is a known non-runner, so no decoded check fires), keeping one
    # mutant per pattern rather than one whole-tuple mutant.
    ('pe-writer-raw-curl-arg', 4, 'zzf() { {scratch}/zz; }; printf "curl -o f"; zzf', 'deny'),
    ('pe-writer-raw-wget-arg', 4, 'zzf() { {scratch}/zz; }; printf "wget -O f"; zzf', 'deny'),
    ('pe-writer-raw-tar-arg', 4, 'zzf() { {scratch}/zz; }; printf "tar -x -C d"; zzf', 'deny'),
    ('pe-writer-raw-unzip-arg', 4, 'zzf() { {scratch}/zz; }; printf "unzip z -d d"; zzf', 'deny'),
    ('pe-writer-raw-chmod-arg', 4, 'zzf() { {scratch}/zz; }; printf "chmod +x f"; zzf', 'deny'),
    ('pe-writer-raw-interp-arg', 4, 'zzf() { {scratch}/zz; }; printf "perl -e x"; zzf', 'deny'),
    # The recover-stop class closed at FULL WIDTH. A category-0 SYNTAX stop that
    # leaves text unread denies too (the separator-based category-0 allow is dropped), and a recorded
    # heredoc-join cannot-evaluate is never downgraded by a LATER unterminated quote or escape. Every
    # reproduction row: a heredoc join then an unterminated single or double
    # quote or a trailing backslash, a no-heredoc trailing backslash, and a bare unbalanced quote.
    ("pe-recover-join-trail-squote", 4,
     "cat <<EOF"+_NL+"EO"+_BS+_NL+"F"+_NL+"t"+_SQ+_SQ+"ouch {scratch}/zz; chmod 700 {scratch}/zz; {scratch}/zz"+_NL+"EOF"+_NL+"echo "+_SQ+"x", "deny"),
    ("pe-recover-join-trail-dquote", 4,
     "cat <<EOF"+_NL+"EO"+_BS+_NL+"F"+_NL+"t"+_SQ+_SQ+"ouch {scratch}/zz; chmod 700 {scratch}/zz; {scratch}/zz"+_NL+"EOF"+_NL+"echo "+_DQ+"x", "deny"),
    ("pe-recover-join-trail-bs", 4,
     "cat <<EOF"+_NL+"EO"+_BS+_NL+"F"+_NL+"t"+_SQ+_SQ+"ouch {scratch}/zz; chmod 700 {scratch}/zz; {scratch}/zz"+_NL+"EOF"+_NL+"echo x"+_BS, "deny"),
    ("pe-recover-trailbs-noheredoc", 4,
     "printf ok > {scratch}/zz; chmod 700 {scratch}/zz; {scratch}/zz"+_BS, "deny"),
    ("pe-recover-cp-trailbs", 4, "c"+_SQ+_SQ+"p helper {scratch}/zz; {scratch}/zz"+_BS, "deny"),
    ("pe-recover-syntax-unbalanced", 4, "ls "+_DQ+"unbalanced", "deny"),
    # Coproc behind the time reserved word is a command position;
    # the reserved prefix (time with -p/--, and a leading bang) is skipped before the uncertain-word and
    # creator scans, so a coproc and a creator it runs are seen (an earlier hook allowed all three).
    ("pe-time-coproc-quoted-cp", 4, "time coproc c"+_SQ+_SQ+"p a b", "deny"),
    ("pe-time-p-coproc-named-quoted-cp", 4, "time -p coproc zznm { c"+_SQ+_SQ+"p a b; }", "deny"),
    ("pe-time-coproc-plain-cp", 4, "time coproc cp a b", "deny"),
    # The executed-word scans consume a runner's OWN
    # options by arity and recurse when the executed word is itself a runner or wrapper, so an option
    # VALUE is not mistaken for the command (xargs -I curl printf allows below) and a creator behind a
    # wrapper is found. Each denies behind an uncertain read.
    ("pe-runner-xargs-sudo-cp", 4, "xargs sudo c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-taskset-c-cp", 4, "taskset -c 0 c''p; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-strace-o-cp", 4, "strace -o log c''p; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-find-exec-env-cp", 4, "find src -exec env c''p {} +; zzf() { {scratch}/zz; }; zzf", "deny"),
    # A command-substitution level costs TWO against the recover depth
    # (its own open group plus the nesting increment), so a benign command denies past FOUR levels, as it
    # denies past EIGHT process-substitution groups; the just-inside depths are allow controls.
    ("pe-stop-depth-dollarsub", 4, "echo "+"$(echo "*4+"$(printf ok)"+")"*4, "deny"),
    # Option and assignment syntax is parsed on the RAW decoded
    # token, so an attached or equals-joined option value carrying a slash no longer ends the option
    # scan ahead of a quote-concatenated creator (each allowed on an earlier hook); the same
    # shapes around benign commands are allow controls below.
    ("pe-runner-strace-o-attached-cp", 4, "strace -o/tmp/log c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-strace-output-eq-cp", 4, "strace --output=/tmp/log c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-xargs-a-attached-cp", 4, "xargs -a/tmp/list c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-xargs-argfile-eq-cp", 4, "xargs --arg-file=/tmp/list c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-xargs-I-attached-cp", 4, "xargs -I/tmp/slot c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-xargs-env-assign-cp", 4, "xargs env X=/tmp/a c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    # The FOUR-CLASS option model. A bare optional-argument
    # long option consumes NO word; ltrace (no entry) and the taskset existing-process mode leave the
    # executed word UNKNOWN, denying even a benign command behind an uncertain read (fail closed).
    ("pe-runner-xargs-replace-bare-cp", 4, "xargs --replace c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-xargs-sudo-preserve-cp", 4,
     "xargs sudo --preserve-env=PATH c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-ltrace-unmodelled", 4, 'ltrace -f printf ok; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-runner-taskset-pid-unknown", 4, 'taskset -p 123; zzf() { {scratch}/zz; }; zzf', "deny"),
    # Declared wrapper positionals are consumed on recursion,
    # taskset's mask is a mandatory positional, and a watch string without -x is read as a command.
    ("pe-runner-xargs-timeout-cp", 4, "xargs timeout 5 c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-xargs-chrt-cp", 4, "xargs chrt -f 10 c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-taskset-mask-cp", 4, "taskset 1 c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-watch-string-python", 4,
     "watch -n 1 'python3 w.py'; zzf() { {scratch}/zz; }; zzf", "deny"),
    # Predicate operands are consumed BEFORE an action is
    # recognized, so a pattern that spells an action never swallows the real one; an uncertified
    # predicate ahead of an action denies beside a later creator/runner/wrapper word (a disclosed
    # over-deny: curl below is a -name OPERAND bash never runs).
    ("pe-find-name-exec-operand-cp", 4,
     "find src -name -exec -o -exec c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-find-execdir-name-operand-cp", 4,
     "find src -name -execdir -o -execdir c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-find-ok-name-operand-cp", 4,
     "find src -name -ok -o -ok c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-find-okdir-name-operand-cp", 4,
     "find src -name -okdir -o -okdir c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-find-uncertified-pred-overdeny", 4,
     'find src -zzznewpred -exec true {} \\; -name curl; zzf() { {scratch}/zz; }; zzf', "deny"),
    # The executed-word recursion bound at both ends - a creator at the deepest
    # followed depth still denies, and one wrapper deeper is UNKNOWN, denying even a benign command
    # (fail closed); the just-inside benign form is an allow control below.
    ("pe-runner-depth-creator-at-bound", 4, "xargs env env env c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-depth-past-bound", 4, 'xargs env env env env printf ok; zzf() { {scratch}/zz; }; zzf', "deny"),
    # The recover depth of the QUOTED dollar form denies past FOUR levels exactly
    # as the unquoted form, and a backtick holding process substitutions denies past SEVEN groups
    # (one fewer than the eight a plain command allows); the just-inside depths are controls below.
    ("pe-stop-depth-dollarsub-quoted", 4,
     "echo "+_DQ+"$(echo "*4+"$(printf ok)"+")"*4+_DQ, "deny"),
    ("pe-stop-depth-backtick-procsub", 4,
     "echo "+_BT+"cat "+"<(cat "*7+"<(printf ok)"+")"*7+_BT, "deny"),
    # A runner whose EXECUTED word is find runs the find action
    # scan on find's arguments, sharing the unknown handling (each ALLOWED on an earlier hook).
    ("pe-runner-xargs-find-exec-cp", 4, "xargs find src -exec c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-xargs-I-find-exec-cp", 4, "xargs -I{} find {} -exec c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-strace-find-exec-cp", 4, "strace -f find src -exec c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-taskset-find-exec-cp", 4, "taskset 1 find src -exec c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-watch-exec-find-cp", 4, "watch -x find src -exec c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    # -- ends env option parsing but NOT assignment parsing, so a
    # NAME=VALUE after -- stays an assignment and the command word is cp (each ALLOWED on an earlier hook).
    ("pe-runner-xargs-env-dashdash-assign-cp", 4, "xargs env -- X=/tmp/a c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-find-exec-env-dashdash-assign-cp", 4, "find src -exec env -- X=/tmp/a c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    # + ends an action only for -exec/-execdir; -ok/-okdir end
    # only at ;, so the two-operand -fprintf no longer swallows the real ; and the trailing -exec creator
    # (each ALLOWED on an earlier hook).
    ("pe-find-ok-plus-fprintf-cp", 4, "find src ! -ok printf X {} + -fprintf \\; -exec c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-find-okdir-plus-fprintf-cp", 4, "find src ! -okdir printf X {} + -fprintf \\; -exec c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    # A shell-string runner (watch without -x, eval, builtin eval,
    # sg) hands one string to a shell, so its executed word is UNKNOWN and never whitespace-split; a
    # creator next to a shell separator is now seen (each ALLOWED on an earlier hook). The benign ls -la string
    # is the disclosed over-deny.
    ("pe-runner-watch-string-semicolon", 4, "watch -n 1 'true;python3 w.py'; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-watch-string-andand", 4, "watch -n 1 'true&&python3 w.py'; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-watch-string-subshell", 4, "watch -n 1 '(python3 w.py)'; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-watch-string-dollarsub", 4, "watch 'echo $(python3 w.py)'; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-watch-string-pipe", 4, "watch 'ls|python3'; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-xargs-watch-string", 4, "xargs watch -n 1 'true;python3 w.py'; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-eval-string", 4, "eval 'python3 w.py'; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-builtin-eval-string", 4, "builtin eval 'python3 w.py'; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-sg-string", 4, "sg wheel 'python3 w.py'; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-watch-string-benign-overdeny", 4, "watch -n 1 'ls -la'; zzf() { {scratch}/zz; }; zzf", "deny"),
    # An opaque word
    # ANYWHERE in a runner's argument list - these rows spell it at an option, positional, executed and
    # find action word - is UNKNOWN and denies behind an uncertain read (each ALLOWED on an earlier hook).
    ("pe-opaque-xargs-Z-cp", 4, "xargs $Z c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-opaque-xargs-braceZ-cp", 4, "xargs ${Z} c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-opaque-strace-Z-cp", 4, "strace $Z c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-opaque-xargs-env-Z-cp", 4, "xargs env $Z c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-opaque-xargs-chrt-P-cp", 4, "xargs chrt -f $P c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-opaque-find-exec-Z-cp", 4, "find src -exec $Z c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    # Coverage pins - xargs -i/-l optional-attached class, env lone -, and the
    # ASCII-only digit test in _classify_redirect (a Unicode digit such as the superscript two is NOT a
    # bash file descriptor, so a >& of one WRITES that file and running ./ of it runs it; a certain
    # read, untouched by the word-level rule). Under the word-level rule the three runner rows deny through the
    # word-level creator trigger (cp), so only the ascii-digit row still flips under a single-line
    # mutant of its own test; the option-class readings they once pinned survive only on the stricter
    # deny side of the kept arity scan.
    ("pe-runner-xargs-i-optional-cp", 4, "xargs -i c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-xargs-l-optional-cp", 4, "xargs -l c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-xargs-env-lone-dash-cp", 4, "xargs env - c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-redir-ampersand-ascii-digit", 4, "printf ok >&"+_SUP2+"; ./"+_SUP2, "deny"),
    # An OPAQUE word (an unset or computed expansion) ANYWHERE
    # before the command - an option value, a runner positional (taskset mask, xargs -n value, strace -e
    # value) or a find starting path or expression word - changes the word count or position, so the
    # executed word is UNKNOWN and denies (each ALLOWED on an earlier hook).
    ("pe-opaque-taskset-mask-cp", 4, "taskset $Z 1 c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-opaque-xargs-n-value-cp", 4, "xargs -n $Z 1 c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-opaque-strace-e-value-cp", 4, "strace -e $Z trace=none c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-opaque-find-path-cp", 4, "find src $Z c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-opaque-find-bracepath-cp", 4, "find src ${Z} c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-opaque-find-env-exec-path-cp", 4, "A=-exec; find src $A c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    # Env's and sudo's
    # assignment grammars differ between implementations, so they are no longer position-modelled at
    # all: ANY word containing = after its first character on a line that reaches env or sudo is
    # UNKNOWN and denies behind an uncertain read (each of these rows ALLOWED on an earlier hook).
    ("pe-env-assign-leading-digit-cp", 4, 'xargs env 9x=1 python3 w.py; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-env-assign-dash-name-cp", 4, "find src -exec env a-b=1 c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-env-assign-dashdash-digit-cp", 4, "xargs env -- 1=2 c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    # A runner with NO option model and any word before its command
    # is UNKNOWN; su, runuser and script hand a -c string to a shell (shell-string set) and chroot/ssh take
    # an operand (DIR/HOST) before the command (each ALLOWED on an earlier hook). The benign ssh is the over-deny.
    ("pe-runner-su-c-string", 4, "su root -c 'python3 w.py'; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-xargs-su-c-string", 4, "xargs su root -c 'python3 w.py'; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-runuser-c-string", 4, "runuser root -c 'python3 w.py'; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-script-c-string", 4, "script out.txt -c 'python3 w.py'; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-chroot-operand", 4, 'chroot / python3 w.py; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-runner-ssh-operand", 4, "ssh localhost 'python3 w.py'; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-ssh-benign-overdeny", 4, 'ssh localhost true; zzf() { {scratch}/zz; }; zzf', "deny"),
    # Find shares ONE recursion budget - depth is threaded into the
    # find action scan and its runner call - so a wrapper chain THROUGH find reads past the bound as UNKNOWN
    # and a deep alternation can never raise RecursionError (the first form ALLOWED on an earlier hook).
    ("pe-runner-find-shared-budget", 4, 'xargs env env env find src -exec env env env printf ok \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-runner-find-deep-chain", 4, 'xargs env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env find src -exec env env env env printf ok \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    # The three surviving single-line mutants get a committed witness - a
    # runner resolving to find with an opaque action word, a find action whose runner has an opaque word,
    # and a creator through a wrapper then find (each denies here; the mutant flips it to allow).
    ("pe-runner-xargs-find-exec-opaque-cp", 4, "xargs find src -exec $Z c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-find-exec-env-opaque-cp", 4, "find src -exec env $Z c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-runner-xargs-sudo-find-exec-cp", 4, "xargs sudo find src -exec c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    # The WORD-LEVEL rule. Behind an uncertain read EVERY word
    # of a runner's argument list is read with NO position assigned: any opaque word (an expansion,
    # quoted or not, a glob, a brace, a leading tilde), any creator, interpreter or shell word, env's
    # split-string option, and any env/sudo-line word containing = after its first character is
    # UNKNOWN and denies. Each row below ALLOWED on an earlier hook.
    # (a) find consumed an opaque OPERAND unchecked (-name value, -D value, action operands, -newer
    # value, both -fprintf values), so word splitting could smuggle a second action past the scan.
    ("pe-opaque-find-name-operand-cp", 4, "find src -name $Z c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-opaque-find-D-value-cp", 4, "find -D $Z c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-opaque-find-exec-operand-cp", 4, "find src -exec printf $Z c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-opaque-find-newer-operand-cp", 4, "find src -newer $Z c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-opaque-find-fprintf-operand-cp", 4, "find src -fprintf $Z x c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    # (b) env's split-string option re-splits ONE string into new words (-S/--split-string, attached
    # or separated), and an assignment NAME may contain a newline, so no equals-regex with a dot can
    # read it: both are now word-level UNKNOWN (the --split-string rows had also regressed
    # once against an older hook that denied them as unmodelled options).
    ("pe-env-split-string-eq-cp", 4, "xargs env --split-string='c''p a b'; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-env-split-string-attached-cp", 4, "xargs env -S'x=1 c''p a b'; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-find-exec-env-split-string-cp", 4, "find src -exec env --split-string='c''p a b' \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-env-newline-assign-cp", 4, "xargs env 'a\nb=1' c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-env-split-string-benign-unknown", 4, "xargs env -S'printf ok'; zzf() { {scratch}/zz; }; zzf", "deny"),
    # (c) a SHELL reached through a runner or a find action is a creator-or-shell WORD, never a
    # recursion step that returns its script operand; source, . , mksh and ash (outside the creator
    # set) deny the same way, directly and behind runners.
    ("pe-runner-xargs-bash-script", 4, 'xargs bash s.sh; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-runner-xargs-sh-script", 4, 'xargs sh s.sh; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-runner-xargs-zsh-script", 4, 'xargs zsh s.sh; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-find-exec-bash-script", 4, 'find src -exec bash s.sh \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-runner-xargs-sudo-sh-script", 4, 'xargs sudo sh w.sh; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-runner-xargs-env-bash-script", 4, 'xargs env bash w.sh; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-runner-xargs-nice-zsh-script", 4, 'xargs nice zsh w.sh; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-runner-source-script", 4, 'source ./w.sh; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-runner-dot-script", 4, '. ./w.sh; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-runner-mksh-script", 4, 'mksh w.sh; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-runner-xargs-ash-script", 4, 'xargs ash w.sh; zzf() { {scratch}/zz; }; zzf', "deny"),
    # (d) sudo's assignment grammar differs from env's AND between sudo implementations (A+=1 and
    # a-b=1 are read differently by sudo and sudo-rs), so any sudo-line word with = denies.
    ("pe-sudo-plus-assign-cp", 4, "xargs sudo A+=1 c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-sudo-dash-assign-cp", 4, "xargs sudo a-b=1 c''p a b; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-find-exec-sudo-plus-assign-cp", 4, "find src -exec sudo X+=1 c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-sudo-plus-assign-benign-unknown", 4, 'xargs sudo A+=1 printf ok; zzf() { {scratch}/zz; }; zzf', "deny"),
    # The DISCLOSED over-denies of the word-level rule, each named. A word
    # is read with no position, so a benign creator-or-shell NAME that is only DATA, a quoted
    # expansion or tilde that bash keeps as exactly ONE word (it cannot move the command position),
    # and every env/sudo assignment or equals-joined option now deny behind an uncertain read. The
    # rule accepts these as the bounded cost of assigning NO positions: certifying "exactly one word"
    # or "only data" would be position grammar again, the class that kept reopening.
    ("pe-word-xargs-grep-sh-overdeny", 4, 'xargs grep -l sh; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-xargs-grep-curl-overdeny", 4, 'xargs grep -l curl; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-xargs-n-quoted-overdeny", 4, 'xargs -n "$N" printf ok; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-home-path-overdeny", 4, 'find "$HOME" -name x; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-strace-tilde-overdeny", 4, 'strace -o ~/t.txt printf ok; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-env-assign-overdeny", 4, 'xargs env X=/tmp/a printf; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-env-dashdash-assign-overdeny", 4, 'xargs env -- X=/tmp/a printf ok; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-sudo-preserve-env-overdeny", 4, 'xargs sudo --preserve-env=PATH printf; zzf() { true; }; zzf', "deny"),
    ("pe-word-xargs-I-curl-overdeny", 4, 'xargs -I curl printf; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-name-after-term-overdeny", 4, 'find src -exec true {} + -name curl; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-printf-exec-curl-overdeny", 4, 'find src -exec printf -exec curl {} +; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-exec-plus-interior-overdeny", 4, 'find src -exec printf + -exec curl {} \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-execdir-plus-interior-overdeny", 4, 'find src -execdir printf + -execdir curl {} \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-ok-plus-interior-overdeny", 4, 'find src -ok printf + -ok curl {} \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-okdir-plus-interior-overdeny", 4, 'find src -okdir printf + -okdir curl {} \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-ok-plus-operand-overdeny", 4, "find src -ok printf X {} + -exec c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-word-find-okdir-plus-operand-overdeny", 4, "find src -okdir printf X {} + -exec c''p a b \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    ("pe-word-find-global-H-overdeny", 4, 'find -H src -name curl -exec printf ok {} \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-global-L-overdeny", 4, 'find -L src -name curl -exec printf ok {} \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-global-P-overdeny", 4, 'find -P src -name curl -exec printf ok {} \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-global-O-overdeny", 4, 'find -O3 src -name curl -exec printf ok {} \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-global-D-overdeny", 4, 'find -D tree src -name curl -exec printf ok {} \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-xargs-find-printf-cp-operand-overdeny", 4, "xargs find src -exec printf c''p {} \\;; zzf() { {scratch}/zz; }; zzf", "deny"),
    # Committed witnesses for the two branches no committed row
    # pinned - the find action recursion charges the SHARED budget (depth + 1: with the increment
    # dropped, the fourth wrapper level behind find resolves and this row allows), and the word-level
    # opacity trigger read at a SEPARATE long-option value (with the trigger dropped, --max-args
    # consumes the opaque value and the benign tail allows; an earlier hook denied this row through its
    # long-option opacity check, which the word-level rule subsumes).
    ("pe-wit-find-action-depth-edge", 4, 'xargs env env env find src -exec env printf ok \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-wit-long-value-opaque", 4, 'xargs --max-args $Z 1 printf ok; zzf() { {scratch}/zz; }; zzf', "deny"),
    # Source and . are shell WORDS for the rule wherever they
    # appear as an argument of a runner, builtin and command INCLUDED - those two execute shell
    # builtins, so the first two rows are executable routes - and for xargs the rule stays word-level
    # with no executability reasoning. Each row ALLOWED on an earlier hook, whose argument-word
    # set held only mksh/ash. The find starting-path exception is
    # REMOVED: a literal dot word denies wherever it appears (the pe-word-*find-dot* rows below),
    # and the uncertain-read find rows in this file spell their starting path src so each keeps
    # pinning its own trigger.
    ("pe-word-builtin-source-script", 4, 'builtin source ./w.sh; zzf() { true; }; zzf', "deny"),
    ("pe-word-command-dot-script", 4, 'command . ./w.sh; zzf() { true; }; zzf', "deny"),
    ("pe-word-xargs-source-script", 4, 'xargs source ./w.sh; zzf() { true; }; zzf', "deny"),
    # The word rule runs on EVERY route into a listed runner.
    # time is itself a _COMMAND_RUNNERS word the reserved-prefix skip removed before dispatch, and a
    # coproc (plain and NAME forms) reaches a runner an earlier helper only creator-scanned. One
    # row per route and trigger kind - opaque operand, shell word, env split-string - each ALLOWED on
    # an earlier hook.
    ("pe-word-time-opaque-operand", 4, 'time printf "$Z"; zzf() { true; }; zzf', "deny"),
    ("pe-word-time-p-shell-word", 4, 'time -p printf ash; zzf() { true; }; zzf', "deny"),
    ("pe-word-bang-time-shell-word", 4, '! time printf sh; zzf() { true; }; zzf', "deny"),
    ("pe-word-coproc-xargs-opaque", 4, "coproc xargs printf \"$Z\"", "deny"),
    ("pe-word-coproc-env-split-string", 4, "coproc env -S'printf ok'", "deny"),
    ("pe-word-coproc-builtin-source", 4, "coproc builtin source ./w.sh", "deny"),
    ("pe-word-coproc-named-xargs-opaque", 4, "coproc zznm { xargs printf \"$Z\"; }", "deny"),
    # The literal {} placeholder spellings are the ONLY
    # opacity exception, accepted by POSITIVE match; the brace and bracket words now deny as runner
    # argument words (a DISCLOSED over-denial; both rows ALLOWED on an earlier hook through its
    # _DESTRUCT_LITERAL_WORDS carve-out).
    ("pe-word-xargs-printf-bracket-overdeny", 4, 'xargs printf [; zzf() { true; }; zzf', "deny"),
    ("pe-word-xargs-printf-brace-overdeny", 4, 'xargs printf {; zzf() { true; }; zzf', "deny"),
    # The retained find grammar refuses an uncertified word
    # ONLY beside a LATER action flag and a LATER creator, runner or wrapper word (env here, which the
    # word rule alone does not deny). This row denied on an earlier hook too (not a flip): it pins the deny
    # side of the documented CONDITIONAL refusal; the allow side is pe-control-find-unknown-pred-alone
    # and pe-control-find-unknown-pred-print below.
    ("pe-word-find-unknown-pred-before-action", 4,
     'find src -unmodelled -exec env printf ok \\;; zzf() { true; }; zzf', "deny"),
    # The find starting-path dot exception is REMOVED. A
    # helper that located the FIRST argument word spelling find without certifying that find
    # executes let a data or option-value find reposition the exemption (the first four rows, each
    # ALLOWED on an earlier hook). Behind an uncertain read a literal dot word now denies
    # WHEREVER it appears, a literal find starting path included: the next two rows are the
    # DISCLOSED over-denial of the removed exception, both ALLOWED on an earlier hook; a
    # non-dot starting path and the spelling ./ keep allowing (pe-control-find-src-path,
    # pe-control-find-dotslash-path below).
    ("pe-word-xargs-printf-find-dot-overdeny", 4, 'xargs printf find .; zzf() { true; }; zzf', "deny"),
    ("pe-word-command-printf-find-dot-overdeny", 4, 'command printf find .; zzf() { true; }; zzf', "deny"),
    ("pe-word-xargs-I-find-dot-script", 4, 'xargs -I find . ./w.sh; zzf() { true; }; zzf', "deny"),
    ("pe-word-env-u-find-builtin-dot-script", 4, 'env -u find builtin . ./w.sh; zzf() { true; }; zzf', "deny"),
    ("pe-word-xargs-find-dot-path-overdeny", 4, 'xargs find . -name x -print; zzf() { true; }; zzf', "deny"),
    ("pe-word-find-dot-path-overdeny", 4, 'find . -name x -print; zzf() { true; }; zzf', "deny"),
    # The data-word over-denials an earlier docstring
    # disclosed only in prose now carry their own named rows (each DENIED on that earlier hook
    # too: deny-preservation, not flips).
    ("pe-word-xargs-printf-dot-overdeny", 4, 'xargs printf .; zzf() { true; }; zzf', "deny"),
    ("pe-word-xargs-grep-source-overdeny", 4, 'xargs grep -l source; zzf() { true; }; zzf', "deny"),
    # A coproc segment (any form) and a function
    # NAME { ...; } definition segment are judged word by word with NO runner position code, the
    # coproc NAME and the first body command included, and a LISTED RUNNER word itself denies there.
    # The first four rows each ALLOWED on an earlier hook (its NAME-form dispatcher read the
    # NAME as the runner, so a NAME spelling find read the compound words as find starting paths;
    # its function-keyword segment never reached the scan). The benign coproc xargs printf ok, once a
    # CONTROL, is the DISCLOSED over-denial of the runner-word trigger. The last row (the
    # NAME() form) DENIED on that earlier hook too: that body arrives as its own ordinary segment, judged
    # like the direct route (deny-preservation, not a flip).
    ("pe-word-coproc-named-brace-xargs-script", 4, "coproc zznm { xargs ./w.sh; }", "deny"),
    ("pe-word-coproc-named-plain-xargs-script", 4, "coproc zznm xargs ./w.sh", "deny"),
    ("pe-word-coproc-name-find-dot-script", 4, "coproc find if . ./w.sh; then true; fi", "deny"),
    ("pe-word-function-keyword-xargs-script", 4, "zzf() { true; }; function zzg { xargs ./w.sh; }; zzg", "deny"),
    ("pe-word-coproc-xargs-printf-ok-overdeny", 4, "coproc xargs printf ok", "deny"),
    ("pe-word-funcdef-source-script", 4, "zzf() { true; }; zzg() { source ./w.sh; }; zzg", "deny"),
    # The env split-string option in EVERY spelling env
    # accepts - the bundled short group, the abbreviated long form, the minimal --s abbreviation -
    # at ANY position of a runner argument list on a line that reaches env. The three data-position
    # rows each ALLOWED on an earlier hook (its word scan read only the canonical spellings
    # and its arity scan examined printf, never env); the option-position row DENIED there through
    # the arity scan and now also denies through the word rule (deny-preservation).
    ("pe-word-env-bundled-split-data", 4, 'xargs printf env -iS"printf ok"; zzf() { true; }; zzf', "deny"),
    ("pe-word-env-abbrev-split-time-data", 4, 'time printf env --split-str "printf ok"; zzf() { true; }; zzf', "deny"),
    ("pe-word-env-min-abbrev-split-data", 4, 'xargs printf env --s "printf ok"; zzf() { true; }; zzf', "deny"),
    ("pe-word-env-bundled-split-option", 4, 'xargs env -iS"printf ok"; zzf() { true; }; zzf', "deny"),
    # The split-string predicate reads the COMPLETE
    # lexer-decoded word; call sites that handed it the word's BASENAME let an attached or
    # bundled split string carrying a slash lose its option prefix at the rsplit and escape the
    # match (a deny-to-allow regression). One slash-bearing row per position - a
    # data word, an option value and a find operand - for the attached and the bundled spelling,
    # each ALLOWED on that basename-reading hook. The equals-joined slash spelling DENIED there through
    # the env/sudo-equals rule (every equals-joined word on an env line already denies), so its
    # row is deny-preservation, never red: the slash bug had no reachable equals-joined form.
    ("pe-word-env-slash-split-data", 4, 'xargs printf env -S"/usr/bin/printf ok"; zzf() { true; }; zzf', "deny"),
    ("pe-word-env-slash-split-value", 4, 'xargs -I -S"/usr/bin/printf ok" printf env; zzf() { true; }; zzf', "deny"),
    ("pe-word-env-slash-bundled-split-data", 4, 'time printf env -iS"/usr/bin/printf ok"; zzf() { true; }; zzf', "deny"),
    ("pe-word-env-slash-bundled-split-value", 4, 'xargs -I -iS"/usr/bin/printf ok" printf env; zzf() { true; }; zzf', "deny"),
    ("pe-word-env-slash-split-find-operand", 4, 'find src -name env -printf -S"/usr/bin/printf ok"; zzf() { true; }; zzf', "deny"),
    ("pe-word-env-slash-split-eq-find-operand", 4, 'find src -name env -newer --split-string=/usr/bin/x; zzf() { true; }; zzf', "deny"),
    # A NAME () or function NAME () definition
    # opens the whole-tail judgment, so the definition header, the WHOLE body and everything
    # after the definition are judged by the segment word rule across the lexer segment boundary.
    # A runner-word and an opaque-word witness per spelling, each ALLOWED on an earlier
    # hook, whose loop judged those body segments exactly like the direct route.
    ("pe-word-funcparen-xargs-script", 4, "zzf() { true; }; zzg() { xargs ./w.sh; }; zzg", "deny"),
    ("pe-word-funcparen-opaque", 4, "zzf() { true; }; zzg() { printf \"$Z\"; }; zzg", "deny"),
    # Each function-keyword OPENER row spells its opener as the FIRST
    # definition of its command - an earlier zzf() definition had already opened the whole-tail
    # judgment and masked a broken word == "function" branch - so every such row flips under
    # the function-branch source mutant (dz-funckw-opener-mutant-*); the already-active-tail
    # behaviour keeps its own pe-word-funckw-active-tail-* rows below.
    ("pe-word-funckw-paren-xargs-script", 4, "function zzg() { xargs ./w.sh; }; zzg", "deny"),
    ("pe-word-funckw-paren-opaque", 4, "function zzg() { printf \"$Z\"; }; zzg", "deny"),
    ("pe-word-funcparen-subshell-xargs-script", 4, "zzf() { true; }; zzg() ( xargs ./w.sh ); zzg", "deny"),
    ("pe-word-coproc-paren-xargs-script", 4, "coproc zznm ( xargs ./w.sh )", "deny"),
    # The over-denials the former one-segment carry ADDED, named, now produced by
    # the whole-tail judgment: a tilde word and a runner-named data word in the first
    # command of a NAME () body, each ALLOWED on an older hook; benign printf ok bodies
    # keep allowing (the pe-control-funcparen-*, pe-control-funckw-paren-* and
    # pe-control-coproc-paren-* controls).
    ("pe-word-funcparen-tilde-overdeny", 4, "zzf() { true; }; zzg() { ls ~/x; }; zzg", "deny"),
    ("pe-word-funcparen-echo-time-overdeny", 4, "zzf() { true; }; zzg() { echo time; }; zzg", "deny"),
    # The over-denials the segment word rule added,
    # named - a tilde word and a runner-named data word in a function NAME { ...; } body, a glob
    # operand behind a plain coproc, and the dash data word whose alphanumerics carry an S on an
    # env line (an earlier docstring disclosed it in prose only). Each DENIED on that earlier
    # hook too: deny-preservation rows, not flips (each ALLOWED on the hook before it).
    ("pe-word-funckw-tilde-overdeny", 4, "function zzg { ls ~/x; }; zzg", "deny"),
    ("pe-word-funckw-echo-time-overdeny", 4, "function zzg { echo time; }; zzg", "deny"),
    ("pe-word-coproc-glob-overdeny", 4, "coproc ls *.txt", "deny"),
    ("pe-word-env-dash-data-S-overdeny", 4, 'xargs printf env -iSam x; zzf() { true; }; zzf', "deny"),
    # The shell-word match reads each word's BASENAME (_destruct_word),
    # so a starting path whose LAST component is the dot word denies too, a disclosed over-denial
    # (ALLOWED on an earlier hook; the ./ and src allow ends stay pinned by
    # pe-control-find-dotslash-path and pe-control-find-src-path).
    ("pe-word-find-dot-suffix-path-overdeny", 4, 'find src/. -name x -print; zzf() { true; }; zzf', "deny"),
    # The former dot spellings of the EIGHTEEN uncertain-read find
    # controls whose starting path was re-spelled src. Each CHANGED VERDICT when the starting-path
    # dot exception was removed (allowed before, denying since through the removed
    # exception), so each over-denial shape carries its own named row beside its re-spelled
    # control; the dot word is the only difference between each pair.
    ("pe-word-find-dashdash-dot-overdeny", 4, 'find -- . -exec printf ok {} \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-exec-plus-interior-dot-overdeny", 4, 'find . -exec printf + -exec zq {} \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-execdir-plus-interior-dot-overdeny", 4, 'find . -execdir printf + -execdir zq {} \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-global-D-dot-overdeny", 4, 'find -D tree . -name zq -exec printf ok {} \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-global-H-dot-overdeny", 4, 'find -H . -name zq -exec printf ok {} \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-global-L-dot-overdeny", 4, 'find -L . -name zq -exec printf ok {} \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-global-O-dot-overdeny", 4, 'find -O3 . -name zq -exec printf ok {} \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-global-P-dot-overdeny", 4, 'find -P . -name zq -exec printf ok {} \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-name-after-term-dot-overdeny", 4, 'find . -exec true {} + -name zq; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-ok-plus-interior-dot-overdeny", 4, 'find . -ok printf + -ok zq {} \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-ok-plus-operand-dot-overdeny", 4, 'find . -ok printf X {} + -exec zq a b \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-okdir-plus-interior-dot-overdeny", 4, 'find . -okdir printf + -okdir zq {} \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-okdir-plus-operand-dot-overdeny", 4, 'find . -okdir printf X {} + -exec zq a b \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-printf-exec-dot-overdeny", 4, 'find . -exec printf -exec zq {} +; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-find-unknown-pred-alone-dot-overdeny", 4, 'find . -unmodelled; zzf() { true; }; zzf', "deny"),
    ("pe-word-find-unknown-pred-print-dot-overdeny", 4, 'find . -unmodelled -print; zzf() { true; }; zzf', "deny"),
    ("pe-word-xargs-find-exec-printf-dot-overdeny", 4, 'xargs find . -exec printf ok {} \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    ("pe-word-xargs-find-exec-printf-operand-dot-overdeny", 4, 'xargs find . -exec printf zq {} \\;; zzf() { {scratch}/zz; }; zzf', "deny"),
    # Once a segment opens a
    # function definition (a header the reader recognizes, newline-split or not) or a coproc
    # (any form), EVERY later
    # segment of the command, to its end, is judged WHOLE by the segment word rule; the
    # former one-segment carry leaked at a newline-split brace body, a function-keyword
    # parenthesized body and a second body command. A runner-word and an opaque-word witness per
    # reviewed shape, each ALLOWED on an earlier hook.
    ('pe-word-funckw-brace-newline-xargs-script', 4, 'function zzg {\nxargs ./w.sh; }; zzg', 'deny'),
    ('pe-word-funckw-brace-newline-opaque', 4, 'function zzg {\nprintf "$Z"; }; zzg', 'deny'),
    ('pe-word-funckw-name-newline-brace-xargs-script', 4, 'function zzg\n{\nxargs ./w.sh; }; zzg', 'deny'),
    ('pe-word-funckw-name-newline-brace-tilde', 4, 'function zzg\n{\nprintf ~/x\n}; zzg', 'deny'),
    ('pe-word-funckw-brace-blank-line-xargs-script', 4, 'function zzg {\n\nxargs ./w.sh\n}; zzg', 'deny'),
    ('pe-word-funckw-brace-blank-line-tilde', 4, 'function zzg {\n\nprintf ~/x\n}; zzg', 'deny'),
    ('pe-word-coproc-brace-newline-xargs-script', 4, 'coproc zznm {\nxargs ./w.sh; }', 'deny'),
    ('pe-word-coproc-brace-newline-opaque', 4, 'coproc zznm {\nprintf "$Z"; }', 'deny'),
    ('pe-word-funckw-paren-body-xargs-script', 4, 'function zzg ( xargs ./w.sh ); zzg', 'deny'),
    ('pe-word-funckw-paren-body-opaque', 4, 'function zzg ( printf "$Z" ); zzg', 'deny'),
    # The SECOND body command, which a segment-local limit left to the direct
    # route, is judged by the whole-tail rule too; each row ALLOWED on an earlier hook.
    ('pe-word-funcparen-second-xargs-script', 4, 'zzg() { true; xargs ./w.sh; }; zzg', 'deny'),
    ('pe-word-coproc-brace-second-xargs-script', 4, 'coproc zznm { true; xargs ./w.sh; }', 'deny'),
    # A tail segment is judged from its FIRST word, the
    # leading assignment words WITH their opacity flags included; the former carry sliced
    # them off at the command-word index. Each row ALLOWED on an earlier hook.
    ('pe-word-funcparen-newline-assign-opaque', 4, 'zzg() {\nX="$Z" printf ok; }; zzg', 'deny'),
    ('pe-word-coproc-paren-assign-opaque', 4, 'coproc zznm ( X="$Z" printf ok )', 'deny'),
    ('pe-word-funcparen-subshell-assign-opaque', 4, 'zzg() ( X="$Z" printf ok ); zzg', 'deny'),
    # The cost of the design: the over-denial classes the whole-tail
    # judgment ADDS, each named: a benign runner word, or a reserved or listed runner word (time,
    # sudo), ANYWHERE in the command after a definition or coproc - in a body, a second body
    # command, or an unrelated command after a COMPLETED definition. The defn-tail rows and the
    # second-runner rows ALLOWED on an earlier hook (the second-runner rows are its two
    # "second-runner allow" controls, now named over-denial rows); the body-runner,
    # time, sudo and coproc-paren rows DENIED there too, through its one-segment carry
    # (deny-preservation).
    ('pe-word-defn-tail-runner-overdeny', 4, 'zzf() { true; }; xargs printf ok; zzf', 'deny'),
    ('pe-word-defn-tail-find-overdeny', 4, 'zzf() { true; }; find src -name x -print; zzf', 'deny'),
    ('pe-word-funcparen-body-runner-overdeny', 4, 'zzg() { xargs printf ok; }; zzg', 'deny'),
    ('pe-word-funcparen-body-time-overdeny', 4, 'zzg() { time printf ok; }; zzg', 'deny'),
    ('pe-word-funcparen-body-sudo-overdeny', 4, 'zzg() { sudo true; }; zzg', 'deny'),
    ('pe-word-coproc-paren-runner-overdeny', 4, 'coproc zznm ( xargs printf ok )', 'deny'),
    ('pe-word-funcparen-second-runner-overdeny', 4, 'zzg() { true; xargs printf ok; }; zzg', 'deny'),
    ('pe-word-coproc-brace-second-runner-overdeny', 4, 'coproc zznm { true; xargs printf ok; }', 'deny'),
    # An assignment-only parenthesized coproc body is a coproc all the
    # same, so the tail judgment reaches the UNRELATED later command, a named over-denial; an
    # earlier hook denied this row too, through its leaked pending flag.
    ('pe-word-coproc-assign-tail-overdeny', 4, 'coproc ( X=1 ); printf "$Z"', 'deny'),
    # The already-active-tail rows - a function-keyword definition
    # BEHIND an earlier completed definition, where the whole-tail judgment (not the opener
    # branch) denies; the opener-isolated pe-word-funckw-* rows above each spell their opener
    # as the FIRST definition of the command and flip under the function-branch source mutant
    # (dz-funckw-opener-mutant-*), while these two stay denied through the already-open tail.
    ('pe-word-funckw-active-tail-brace-newline-xargs-script', 4,
     'zzf() { true; }; function zzg {\nxargs ./w.sh; }; zzg', 'deny'),
    ('pe-word-funckw-active-tail-paren-body-opaque', 4,
     'zzf() { true; }; function zzg ( printf "$Z" ); zzg', 'deny'),
    # A QUOTED definition NAME that merely DECODES to a
    # reserved word ('if'() { true; }, and every member of _DESTRUCT_KEYWORDS plus time) is a
    # definition header all the same - bash reads a reserved word only when it is written PLAIN
    # - so it opens the whole-tail judgment and the trailing runner word denies. Each row
    # ALLOWED on an earlier hook, whose reserved-prefix skip consumed the decoded NAME
    # before the header test ran; the select wrapper spells the uncertainty AHEAD of the
    # definition, so each row exercises the header recognition itself, never the
    # defined-then-called uncertainty.
    ('pe-word-quoted-defn-bang', 4, "select z in x; do break; done; '!'() { true; }; xargs ./w.sh", 'deny'),
    ('pe-word-quoted-defn-do', 4, "select z in x; do break; done; 'do'() { true; }; xargs ./w.sh", 'deny'),
    ('pe-word-quoted-defn-elif', 4, "select z in x; do break; done; 'elif'() { true; }; xargs ./w.sh", 'deny'),
    ('pe-word-quoted-defn-else', 4, "select z in x; do break; done; 'else'() { true; }; xargs ./w.sh", 'deny'),
    ('pe-word-quoted-defn-if', 4, "select z in x; do break; done; 'if'() { true; }; xargs ./w.sh", 'deny'),
    ('pe-word-quoted-defn-then', 4, "select z in x; do break; done; 'then'() { true; }; xargs ./w.sh", 'deny'),
    ('pe-word-quoted-defn-time', 4, "select z in x; do break; done; 'time'() { true; }; xargs ./w.sh", 'deny'),
    ('pe-word-quoted-defn-until', 4, "select z in x; do break; done; 'until'() { true; }; xargs ./w.sh", 'deny'),
    ('pe-word-quoted-defn-while', 4, "select z in x; do break; done; 'while'() { true; }; xargs ./w.sh", 'deny'),
    ('pe-word-quoted-defn-lbrace', 4, "select z in x; do break; done; '{'() { true; }; xargs ./w.sh", 'deny'),
    # ... and the same quoted definition inside a brace group and inside a subshell: the
    # enclosing PLAIN { or ( is an actual group opener the header skip still consumes
    # (argv_plain tells the plain brace from the quoted NAME), and the runner inside the group
    # denies through the tail.
    ('pe-word-quoted-defn-brace-group', 4,
     "select z in x; do break; done; { 'if'() { true; }; xargs ./w.sh; }", 'deny'),
    ('pe-word-quoted-defn-subshell', 4,
     "select z in x; do break; done; ( 'if'() { true; }; xargs ./w.sh )", 'deny'),
    # A function ACTUALLY NAMED declare, local,
    # readonly, export or typeset is a single-NAME header and still opens the tail, so its
    # xargs body denies; each row DENIED at the pin too (deny-preservation, the boundary
    # beside the pe-control-*-then-* declaration controls that now allow).
    ('pe-word-func-named-declare', 4, 'declare() { xargs ./w.sh; }; declare -p x', 'deny'),
    ('pe-word-func-named-local', 4, 'local() { xargs ./w.sh; }; local -p x', 'deny'),
    ('pe-word-func-named-readonly', 4, 'readonly() { xargs ./w.sh; }; readonly -p x', 'deny'),
    ('pe-word-func-named-export', 4, 'export() { xargs ./w.sh; }; export -p', 'deny'),
    ('pe-word-func-named-typeset', 4, 'typeset() { xargs ./w.sh; }; typeset -p x', 'deny'),
]

# Benign shapes that must keep allowing (false-deny controls; none names a destructive word).
# A control whose PROBE must stay on the direct route spells
# its uncertainty wrapper AFTER the probe (PROBE; zzf() { ...; }; zzf), because a definition ahead
# of the probe now opens the whole-tail word judgment, which denies every benign runner word
# behind it (the pe-word-defn-tail-*-overdeny rows name that cost); a control that KEEPS the
# definition-first spelling (the grep, head, echo, perldoc, python-config and redirect rows and
# the printf ok bodies) pins exactly the benign side of that tail judgment.
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
    # The uncertain constructs with no effect stay allowed, and a data 'case'
    # argument must not deny.
    ("pe-control-coproc-benign", "coproc sleep 1", "allow"),
    ("pe-control-select-benign", "select x in a b; do echo $x; done", "allow"),
    ("pe-control-case-in-procsub", "cat <(printf case)", "allow"),
    ("pe-control-quoted-heredoc-bs", "cat <<'EOF'\nEO\\\nF\nhello\nEOF", "allow"),
    # The benign deep nesting sits JUST INSIDE the recover-depth limit (seven
    # nested groups against _LEX_RECOVER_DEPTH 8); one past it is the pe-stop-depth-benign DENY vector
    # above, because a recover-mode stop now denies whatever the text spells (recover-stop).
    ("pe-control-deep-procsub-benign", "cat " + "<(cat " * 6 + "<(printf ok)" + ")" * 6, "allow"),
    # A closed-set creator now denies on an uncertain read REGARDLESS of its
    # options (the option-absent creator controls moved to the deny vectors above, a disclosed
    # bounded over-fire); NON-creator commands on the same uncertain reads stay allowed.
    ('pe-control-grep-uncertain', 'zzf() { {scratch}/zz; }; grep -c x {scratch}/x; zzf', 'allow'),
    ('pe-control-head-uncertain', 'zzf() { {scratch}/zz; }; head -c 4 {scratch}/x; zzf', 'allow'),
    ('pe-control-sort-coproc', 'sort -u {scratch}/x; coproc {scratch}/zz', 'allow'),
    ('pe-control-cat-coproc', 'cat -n {scratch}/x; coproc {scratch}/zz', 'allow'),
    ('pe-control-printf-coproc', 'printf ok; coproc {scratch}/zz', 'allow'),
    ('pe-control-echo-funcdef', 'zzf() { {scratch}/zz; }; echo ok; zzf', 'allow'),
    # The harmless runner and find forms,
    # each a CERTAIN read the creator rules must not deny (the parent pin allows every one; an
    # earlier hook denied every one).
    ('pe-control-xvfbrun-python', 'xvfb-run python3 -m pytest', 'allow'),
    ('pe-control-taskset-python', 'taskset -c 0 python3 bench.py', 'allow'),
    ('pe-control-strace-python', 'strace -f python3 x.py', 'allow'),
    ('pe-control-unbuffer-python', 'unbuffer python3 app.py', 'allow'),
    ('pe-control-numactl-node', 'numactl --cpunodebind=0 node server.js', 'allow'),
    ('pe-control-firejail-bash', 'firejail bash', 'allow'),
    ('pe-control-xargs-grep-sh', 'xargs grep -l sh', 'allow'),
    ('pe-control-xargs-grep-install', 'xargs grep -l install', 'allow'),
    ('pe-control-xargs-echo-dash', 'xargs echo dash', 'allow'),
    ('pe-control-xargs-tar-list', 'xargs -0 tar -tf', 'allow'),
    ("pe-control-find-exec-bash-n", "find . -name '*.sh' -exec bash -n {} +", 'allow'),
    ("pe-control-find-exec-pycompile", "find . -name '*.py' -exec python3 -m py_compile {} +",
     'allow'),
    ('pe-control-find-exec-grep-curl', 'find . -exec grep -l curl {} +', 'allow'),
    ('pe-control-find-exec-chmod-644', 'find . -type f -exec chmod 644 {} +', 'allow'),
    ("pe-control-find-exec-awk", "find src -name '*.awk' -exec awk 'END{print NR}' {} +", 'allow'),
    # Under the word-level rule a
    # creator-NAMED data word now denies behind an uncertain read (the disclosed pe-word-*-overdeny
    # vectors); a data word that names NO creator, shell or expansion still never fires.
    ('pe-control-xargs-grep-data', 'xargs grep -l zq; zzf() { {scratch}/zz; }; zzf', 'allow'),
    ('pe-control-find-name-after-term-data',
     'find src -exec true {} + -name zq; zzf() { {scratch}/zz; }; zzf', 'allow'),
    # A non-version tail stays outside the versioned-name rule.
    ('pe-control-perldoc-uncertain', 'zzf() { {scratch}/zz; }; perldoc x; zzf', 'allow'),
    ('pe-control-python-config-uncertain', 'zzf() { {scratch}/zz; }; python-config --cflags; zzf',
     'allow'),
    # A descriptor dup and a close are not writes, even on an uncertain read.
    ('pe-control-redir-dup-uncertain', 'zzf() { {scratch}/zz; }; printf ok 2>&1; zzf', 'allow'),
    ('pe-control-redir-close-uncertain', 'zzf() { {scratch}/zz; }; printf ok >&-; zzf', 'allow'),
    # A FULLY READ command - the recover read reaches the end, no stop - still
    # allows; contrast the recover-stop deny vectors above.
    ("pe-control-recover-fully-read", "cat <<EOF"+_NL+"line hi"+_NL+"EOF"+_NL+"printf ok", "allow"),
    # An option VALUE or earlier
    # action OPERAND that happens to NAME a creator now denies by the word-level rule (the disclosed
    # pe-word-*-overdeny vectors); the same shapes around words that name nothing keep allowing.
    ("pe-control-xargs-I-value-data", 'xargs -I slot printf; zzf() { {scratch}/zz; }; zzf', "allow"),
    ("pe-control-find-printf-exec-data", 'find src -exec printf -exec zq {} +; zzf() { {scratch}/zz; }; zzf', "allow"),
    # The just-inside recover depths allow (eight process-substitution
    # groups, four command-substitution levels); one past them denies (the pe-stop-depth-* vectors).
    ("pe-control-deep-procsub-max", "cat "+"<(cat "*7+"<(printf ok)"+")"*7, "allow"),
    ("pe-control-deep-dollarsub-max", "echo "+"$(echo "*3+"$(printf ok)"+")"*3, "allow"),
    # A descriptor MOVE (>&2-, >&12-) is not a write even on an uncertain read.
    ("pe-control-redir-move-2minus", "zzf() { true; }; printf ok >&2-; zzf", "allow"),
    ("pe-control-redir-move-12minus", "zzf() { true; }; printf ok >&12-; zzf", "allow"),
    # Benign option shapes with NO
    # equals sign on an env or sudo line keep allowing - attached and equals-joined values carrying
    # slashes on other runners, bare and equals-joined optional-argument long options, separate option
    # values and the declared positionals. env/sudo assignments and sudo --preserve-env=PATH now deny
    # by the word-level equals rule (the disclosed pe-word-*-overdeny vectors).
    ("pe-control-strace-o-attached", 'strace -o/tmp/log printf ok; zzf() { {scratch}/zz; }; zzf', "allow"),
    ("pe-control-strace-output-eq", 'strace --output=/tmp/log printf ok; zzf() { {scratch}/zz; }; zzf', "allow"),
    ("pe-control-xargs-a-attached", 'xargs -a/tmp/list printf; zzf() { {scratch}/zz; }; zzf', "allow"),
    ("pe-control-xargs-argfile-eq", 'xargs --arg-file=/tmp/list printf; zzf() { {scratch}/zz; }; zzf', "allow"),
    ("pe-control-xargs-I-attached", 'xargs -I/tmp/slot printf; zzf() { {scratch}/zz; }; zzf', "allow"),
    ("pe-control-xargs-env-u-printf", 'xargs env -u X printf ok; zzf() { {scratch}/zz; }; zzf', "allow"),
    ("pe-control-xargs-replace-bare", 'xargs --replace printf a; zzf() { {scratch}/zz; }; zzf', "allow"),
    ("pe-control-xargs-replace-eq", 'xargs --replace={} printf; zzf() { {scratch}/zz; }; zzf', "allow"),
    ("pe-control-xargs-sudo-u-printf",
     'xargs sudo -u root printf ok; zzf() { true; }; zzf', "allow"),
    ("pe-control-xargs-timeout-printf", 'xargs timeout 5 printf ok; zzf() { {scratch}/zz; }; zzf', "allow"),
    ("pe-control-xargs-chrt-printf", 'xargs chrt -f 10 printf ok; zzf() { {scratch}/zz; }; zzf', "allow"),
    ("pe-control-taskset-mask-printf", 'taskset 1 printf ok; zzf() { {scratch}/zz; }; zzf', "allow"),
    ("pe-control-watch-exec-printf", 'watch -x printf ok; zzf() { {scratch}/zz; }; zzf', "allow"),
    # An interior + stays an operand
    # of the FIRST action (+ terminates only right after {}); the trailing word is that action's
    # OPERAND, and under the word-level rule it denies exactly when it NAMES a creator, shell or
    # expansion (the disclosed pe-word-*-overdeny vectors), whatever position find's grammar gives it.
    ("pe-control-find-exec-plus-interior-data",
     'find src -exec printf + -exec zq {} \\;; zzf() { {scratch}/zz; }; zzf', "allow"),
    ("pe-control-find-execdir-plus-interior-data",
     'find src -execdir printf + -execdir zq {} \\;; zzf() { {scratch}/zz; }; zzf', "allow"),
    ("pe-control-find-ok-plus-interior-data",
     'find src -ok printf + -ok zq {} \\;; zzf() { {scratch}/zz; }; zzf', "allow"),
    ("pe-control-find-okdir-plus-interior-data",
     'find src -okdir printf + -okdir zq {} \\;; zzf() { {scratch}/zz; }; zzf', "allow"),
    # The just-inside depths - three wrapper recursions, four QUOTED
    # command-substitution levels, seven process-substitution groups inside a backtick.
    ("pe-control-runner-depth-max", 'xargs env env env printf ok; zzf() { {scratch}/zz; }; zzf', "allow"),
    ("pe-control-deep-dollarsub-quoted-max",
     "echo "+_DQ+"$(echo "*3+"$(printf ok)"+")"*3+_DQ, "allow"),
    ("pe-control-deep-backtick-procsub-max",
     "echo "+_BT+"cat "+"<(cat "*6+"<(printf ok)"+")"*6+_BT, "allow"),
    # A runner that resolves to find with a benign action operand allows.
    ("pe-control-xargs-find-exec-printf", 'xargs find src -exec printf ok {} \\;; zzf() { {scratch}/zz; }; zzf', "allow"),
    # -ok/-okdir take no + form, so
    # the words after an interior + stay OPERANDS of the first action; a creator-named operand now
    # denies by the word-level rule (the disclosed pe-word-*-overdeny vectors), a benign one allows.
    ("pe-control-find-ok-plus-operand-data", 'find src -ok printf X {} + -exec zq a b \\;; zzf() { {scratch}/zz; }; zzf', "allow"),
    ("pe-control-find-okdir-plus-operand-data", 'find src -okdir printf X {} + -exec zq a b \\;; zzf() { {scratch}/zz; }; zzf', "allow"),
    # Find's global options -H/-L/-P
    # and the value-taking -O/-D precede the starting paths; with a creator-NAMED predicate operand the
    # line now denies by the word-level rule (pe-word-find-global-*-overdeny), with a benign operand
    # the global-option shapes keep allowing.
    ("pe-control-find-global-H-data", 'find -H src -name zq -exec printf ok {} \\;; zzf() { {scratch}/zz; }; zzf', "allow"),
    ("pe-control-find-global-L-data", 'find -L src -name zq -exec printf ok {} \\;; zzf() { {scratch}/zz; }; zzf', "allow"),
    ("pe-control-find-global-P-data", 'find -P src -name zq -exec printf ok {} \\;; zzf() { {scratch}/zz; }; zzf', "allow"),
    ("pe-control-find-global-O-data", 'find -O3 src -name zq -exec printf ok {} \\;; zzf() { {scratch}/zz; }; zzf', "allow"),
    ("pe-control-find-global-D-data", 'find -D tree src -name zq -exec printf ok {} \\;; zzf() { {scratch}/zz; }; zzf', "allow"),
    # A word that is opaque ONLY for the literal {}
    # placeholder (the lexer flags its braces) is carved out of the word-level opacity trigger - an
    # empty {} is NOT a brace expansion, so bash passes {}, -I{} and --replace={} through unchanged -
    # and a literal mask or option value is no expansion either, so these benign shapes keep allowing.
    ("pe-control-xargs-I-brace", 'xargs -I{} printf ok; zzf() { {scratch}/zz; }; zzf', "allow"),
    ("pe-control-xargs-I-brace-operand", 'xargs -I{} printf ok {}; zzf() { {scratch}/zz; }; zzf', "allow"),
    # The SEPARATED literal placeholder is one literal word too; an earlier
    # hook read its opacity flag at the option-value position and falsely denied this shape.
    ("pe-control-xargs-I-separated-brace", 'xargs -I {} printf ok {}; zzf() { {scratch}/zz; }; zzf', "allow"),
    # A find ACTION
    # OPERAND now denies exactly when it NAMES a creator, shell or expansion, whatever its position
    # (pe-word-xargs-find-printf-cp-operand-overdeny); a benign operand keeps allowing, and -- before
    # the starting paths is consumed so the path . does not read as a runner (an earlier hook denied these).
    ("pe-control-xargs-find-exec-printf-operand-data", 'xargs find src -exec printf zq {} \\;; zzf() { {scratch}/zz; }; zzf', "allow"),
    ("pe-control-find-dashdash-paths", 'find -- src -exec printf ok {} \\;; zzf() { {scratch}/zz; }; zzf', "allow"),
    # The find starting-path dot exception is REMOVED - a literal dot word
    # behind an uncertain read denies wherever it appears, a find starting path included (the
    # pe-word-*find-dot* rows above). The two ALLOW ends of the removed bound stay pinned: a non-dot
    # literal starting path, and the spelling ./ (not the dot word).
    ("pe-control-find-src-path", 'find src -name x -print; zzf() { true; }; zzf', "allow"),
    ("pe-control-find-dotslash-path", 'find ./ -name x -print; zzf() { true; }; zzf', "allow"),
    # The three {} placeholder spellings accepted by POSITIVE match, with a
    # bare {} operand beside the equals-joined long form (the attached -I{}, the separated {} and the
    # bare --replace={} controls sit above).
    ("pe-control-xargs-replace-eq-operand", 'xargs --replace={} printf ok {}; zzf() { true; }; zzf', "allow"),
    # Benign commands on the time route keep
    # allowing - the word rule passes and the kept arity scan is NOT re-run behind the reserved
    # prefix, so the skip's time -p -- consumption still decides those words. On the coproc and
    # function routes a benign command with NO runner, creator, shell or opaque word keeps allowing
    # (the runner-word over-denial row is pe-word-coproc-xargs-printf-ok-overdeny above).
    ("pe-control-time-printf-ok", 'time printf ok; zzf() { true; }; zzf', "allow"),
    ("pe-control-time-p-dashdash-printf", 'time -p -- printf ok; zzf() { true; }; zzf', "allow"),
    ("pe-control-coproc-printf-ok", "coproc printf ok", "allow"),
    ("pe-control-coproc-named-printf-ok", "coproc zznm { printf ok; }", "allow"),
    ("pe-control-function-keyword-printf-ok", "zzf() { true; }; function zzg { printf ok; }; zzg", "allow"),
    # An unknown find predicate ALONE, and one followed by an ordinary
    # action with no creator, runner or wrapper word behind it, report nothing - the retained find
    # grammar's refusal is CONDITIONAL (its deny side: pe-word-find-unknown-pred-before-action).
    ("pe-control-find-unknown-pred-alone", 'find src -unmodelled; zzf() { true; }; zzf', "allow"),
    ("pe-control-find-unknown-pred-print", 'find src -unmodelled -print; zzf() { true; }; zzf', "allow"),
    # The ALLOW ends of the split-string predicate - a dash word with no S
    # and a long option that abbreviates nothing of --split-string, each at a DATA position on an
    # env line (the deny ends: the pe-word-env-*-split-* rows above).
    ("pe-control-env-data-dash-no-split", 'xargs printf env -u X; zzf() { true; }; zzf', "allow"),
    ("pe-control-env-long-not-split-prefix", 'xargs printf env --unset X; zzf() { true; }; zzf', "allow"),
    # The ALLOW ends of the fixed slash bound - a dash data word whose
    # alphanumeric run stops at the slash without an S, and a non-dash operand carrying -S inside
    # it, each on a line that reaches env (the deny ends: the pe-word-env-slash-* rows above).
    ("pe-control-env-dash-slash-no-split", 'xargs printf env -u/usr/bin/x X; zzf() { true; }; zzf', "allow"),
    ("pe-control-env-nondash-S-operand", 'find src -name env -printf x-S/usr/bin/y; zzf() { true; }; zzf', "allow"),
    # Benign printf ok bodies on the definition
    # and coproc routes keep allowing under the whole-tail judgment: every word of the body, and
    # of everything after the definition, passes the segment word rule. The two earlier
    # "second-runner allow" controls that pinned the removed segment-local limit are now the
    # named over-denial rows pe-word-funcparen-second-runner-overdeny and
    # pe-word-coproc-brace-second-runner-overdeny above.
    ('pe-control-funcparen-printf-ok', 'zzf() { true; }; zzg() { printf ok; }; zzg', 'allow'),
    ('pe-control-funckw-paren-printf-ok', 'zzf() { true; }; function zzg() { printf ok; }; zzg', 'allow'),
    ('pe-control-coproc-paren-printf-ok', 'coproc zznm ( printf ok )', 'allow'),
    # A declaration builtin with an array assignment (declare -a arr=( ),
    # and the local, readonly, export and typeset forms) ends at the same ( as a definition header
    # but is NOT a definition, so NO tail judgment starts there and a benign runner, tilde, time
    # or find word in an UNRELATED later command keeps allowing. Each of the first five rows
    # DENIED on an earlier hook, whose pending flag leaked through the empty ( ) pair into
    # the later command. The uncertainty wrapper sits AFTER the probe (a function definition, or
    # the coproc of the sixth row): spelled ahead of it, the wrapper itself would open the tail.
    ('pe-control-declare-array-runner', 'declare -a arr=( ); xargs printf ok; zzf() { true; }; zzf', 'allow'),
    ('pe-control-local-array-tilde', 'local arr=( )\nls ~/x\nzzf() { true; }\nzzf', 'allow'),
    ('pe-control-readonly-array-runner', 'readonly arr=(); xargs printf ok; zzf() { true; }; zzf', 'allow'),
    ('pe-control-export-array-time', 'export arr=(); time printf ok; zzf() { true; }; zzf', 'allow'),
    ('pe-control-typeset-array-find', 'typeset -a arr=()\nfind src -name x -print\nzzf() { true; }\nzzf', 'allow'),
    ('pe-control-declare-array-coproc-runner', 'declare -a arr=( ); xargs printf ok; coproc printf ok', 'allow'),
    # An assignment-only parenthesized coproc body with NOTHING after it
    # stays benign; the deny end of this bound is pe-word-coproc-assign-tail-overdeny above.
    ('pe-control-coproc-assign-only', 'coproc ( X=1 )', 'allow'),
    # A declaration builtin with an array assignment
    # followed by a LATER INVOCATION of the same builtin. An earlier sibling predicate in
    # _destruct_prior_effects read ANY word count ahead of the empty ( ) pair as a definition,
    # so it recorded the builtin of declare -a arr=( ) as a defined function and the later
    # declare -p arr denied as a prior-effect; the shared exact header predicate
    # (_destruct_defn_header) records nothing here. Each row DENIED on that earlier hook
    # AND at the pin (an intended false-deny fix, so each row sits on the reviewed differential
    # allow-list in selftest_aiqt_hooks).
    ('pe-control-declare-then-declare', 'declare -a arr=(); declare -p arr', 'allow'),
    ('pe-control-local-then-local', 'local arr=(); local -p arr', 'allow'),
    ('pe-control-readonly-then-readonly', 'readonly arr=(); readonly -p arr', 'allow'),
    ('pe-control-export-then-export', 'export arr=(); export -p', 'allow'),
    ('pe-control-typeset-then-typeset', 'typeset -a arr=(); typeset -p arr', 'allow'),
]
