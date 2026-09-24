#!/usr/bin/env python3
"""PreToolUse hook (Write|Edit|MultiEdit|Bash): deny writing a FUTURE-dated observed-time literal into a store.

THREAT MODEL. An ACCIDENTAL-DRIFT discipline guard: it catches the model composing times instead of reading
the clock and writing them into a store in its NORMAL record format. It is NOT an adversarial boundary: an
evasion that needs deliberately unusual construction (a variable, a glob, a literal built from parts, a
schedule word placed before the time) is a disclosed residual, not a defect. It fails OPEN on malformed input.

Motivated by an assistant composing timestamps from its own sense of time instead of reading the clock,
drifting up to 4h40m ahead, and persisting those invented times into its durable records
(timestamp-from-clock; records-first). An observed-time record (a heartbeat, a recorded-at) must come from
the clock, so one dated in the future is a composed value. A scheduled value (a deadline, a next run) is
legitimately future and is allowed.

Store roots. env AIQT_STORE_ROOT (a legacy spelling is accepted as a fallback, see _cfg): one or more
absolute paths joined by os.pathsep; an empty or relative entry is ignored. There is NO default: when neither
spelling is set, when AIQT_STORE_ROOT is set but empty (it takes precedence over the fallback), or when the
value in effect names no absolute path, no store is configured and the hook is INERT (every call is
allowed). No store is derived from the project directory or the cwd. For Write/Edit/MultiEdit a relative
file_path is resolved against the payload cwd.

Write. Checked only when tool_input.file_path lies under a store root. The new content's lines are compared
with the existing file's lines as a MULTISET of whole lines: a new line identical to an existing line is an
unchanged carry-over and is exempt (as many times as it occurs there); every other line is checked whole. A
markdown table row is compared together with its table context (see the table-header exemption below).

Quoted text (Write, and Edit/MultiEdit; the Stop hook's rules, with its helpers duplicated verbatim and a
self-test asserting they are identical). Quoted text is not an observed-time record, so these are exempt:
a line whose stripped form starts with `>` (a blockquote); a literal starting inside an inline code span (a
backtick run closed by the next run of the same length on the same line; an unclosed run opens nothing);
and, on a reconstructed whole file only, every line of a MATCHED fenced code block (``` or ~~~, closed by a
fence of the same character at least as long; an UNCLOSED fence is not code, so its lines are checked). A
line's carry-over is keyed by its fence membership as well as its text, so an edit that moves an existing
line out of a code block (by deleting a fence, say) checks it. A bare field on its own line stays checked.

Edit and MultiEdit. Checked only under a store root. The COMPLETE resulting file is reconstructed by applying
the edits in tool order to the existing file content (old_string replaced once, or everywhere with
replace_all; an empty old_string creates an empty or absent file), and every line of the result that is not
an unchanged carry-over from the original (the same line multiset diff) is checked WHOLE, so a fragment edit
that repurposes a line (renaming `deadline` to `heartbeat`, or changing only the time on a heartbeat) is
seen on the full resulting line, and a scheduling keyword immediately preceding the literal on that line
still exempts it. The existing file is opened non-blocking, must be a regular file, and at most 4 MiB is
read. When the WHOLE file was read (or it is absent) and an edit cannot apply (an old_string not found), the
call is allowed: the tool itself fails and writes nothing. When the file was NOT read whole (over the 4 MiB
cap, unreadable, or not a regular file), whole-line reconstruction is impossible, so each edit's new_string
lines (less lines identical to its old_string lines) are checked, and the call is denied only when that
fragment itself carries a future literal with no immediately preceding keyword (blockquote lines and code
spans are exempt there too, but not fences, whose context the fragment cannot see); a time-only fragment (for
example `17:45` to `22:25`) in such a file is not caught (disclosed below). An UNREADABLE target (it could not
be opened or read, or is not a regular file, so nothing of it was read) fails OPEN except for what the new
content itself introduces (round 24): an Edit or MultiEdit literal that already appears in that edit's
old_string (text the file held) is not counted, and a Write's content is checked line by line as for an
absent file (with the file unread, all of it is new content). No shell expansion happens in
these tools, so every character is literal (a `$(...)` is text, not a substitution).

Bash. A lexical rule (quoting is followed to find write indicators, their targets, the data each write
carries, and date substitutions): DENY when a WRITE indicator's TARGET is a store path (a word holding a store
path token: a configured store root spelled absolutely, standing alone within the target word: not preceded
by a path character and followed
by `/`, whitespace, a quote, a shell operator, or the end; or, round 31, a RELATIVE target that resolves
under a store root, see WHICH DIRECTORY below) AND a future-dated literal is DATA of that write (round 31, see
WHICH LITERALS below; until round 30 any literal anywhere in the command counted). Round
24 binds each write to its target, so a command TARGETS a store path only through: a redirection's operand; a
tee operand; an operand of an in-place sed or perl (not its program: round 26, a sed or perl given no -e, -E,
-f, --expression, or --file takes its first operand as the program); the destination of cp, mv, or install
(every -t, -tDIR, --target-directory DIR, or --target-directory=DIR argument, else the LAST operand); a dd of=
operand; a truncate, ed, or ex operand; or the file ex's -i, -w, -W, --startuptime, or --log writes. Round 26:
each command's option arguments are consumed before its operands are collected, walking a short cluster
letter by letter as getopt does, so an option's argument is never read as a target: sed's -e, -f, -l,
--expression, --file, --line-length; perl's -e, -E, -I, -M, -m (and its attached -i, -x, -D, -F, -0, -l, -C,
-d, -V forms); truncate's -r, -s, --reference, --size; ed's -p, --prompt; ex's -c, -S, -u, -U, -T, -t,
--cmd; cp's and mv's -S, --suffix (and cp's --sparse, --no-preserve); install's -m, -o, -g, -S, --mode,
--owner, --group, --suffix, --strip-program. Round 27: where the real tool stops parsing options at its
first operand, so does this model: perl does (observed with perl 5.40.1: `perl -pi -e CODE X -I S` edits X,
fails to open a file named `-I`, and edits S), so every word after perl's first operand is an operand; the
other modeled commands permute (observed with GNU sed 4.9, GNU cp and mv 9.7, GNU ed 1.22.4, vim 9.1 as ex,
and uutils install, truncate, and tee 0.8.0: an option after an operand is still an option). Round 27 also
reads SCRIPT TEXT: a sed expression (-e, --expression, or the program operand of a sed given no -e or -f),
an ex command (-c, --cmd), or a perl program (-e, -E) that names a store path makes that write's target
UNKNOWN (the text itself can write the path: a sed `w` command or `s///w` flag, an ex `w!`, a perl `open`),
whether or not the sed or perl has an in-place option; an option argument that names a FILE (sed -f, perl
-I or its program file, truncate -r, ex -u) is still consumed and never a target. Round 28: a sed with NO
in-place option whose program (its -e pieces, or its program operand; not a -f file) is parsed with
confidence and holds no command that can write (no `w`, `W`, or `e` command, no `s///w` or `s///e` flag) is
no write, even when its program MENTIONS a store path (`sed -n '\\|<store file> <future>|p' audit.log`); an
in-place sed, a -f program, --posix, or a program not parsed with confidence (any `$` in it included) keeps
the round-27 UNKNOWN target, and ex -c and perl -e script text is unchanged. Round 29: a long option is
resolved as the real tool resolves it before its argument is consumed: GNU sed, cp, and mv, GNU ed, and uutils
install, tee, and truncate (observed on this host) accept any UNAMBIGUOUS prefix of a long option, so
`--expr`, `--in`, `--po`, `--targ`, or `--suf` is read as `--expression`, `--in-place`, `--posix`,
`--target-directory`, or `--suffix` (aliases of one option, such as sed's `--quiet` and `--silent`, count
once); vim's ex accepts only its exact long options and perl none, so on sed, perl, or ex a `--` word that
is AMBIGUOUS or UNRECOGNIZED makes the write's target UNKNOWN, while on the other commands it is read as an
option with no argument (the tool rejects it and writes nothing). Round 30: perl accepts exactly two long
options, `--help` and `--version` (exact, no abbreviation, no `=value`), and exits at once on either while its
options are still parsed, touching no file and running no program (observed with perl 5.40.1, even after
`-pi -e CODE`), so such a perl writes nothing; after perl's first operand either word is an operand as before.
mv's `--exchange` (or an unambiguous abbreviation such as `--exch`) swaps each source with its destination
(observed with GNU mv 9.7: `mv -T --exchange S T` replaces S with T's content), and install's `-d` or
`--directory` creates every operand as a directory (an existing file operand has its mode set), so under
either EVERY operand and every -t or --target-directory argument is a write target. install's `--context`
takes an OPTIONAL argument whose arity differs between implementations: GNU install takes it only attached
(`--context=CTX`), so the next word is an operand, while uutils install 0.8.0 (this host's install) also
consumes the SEPARATE next word when it is `-` or does not start with `-` (`install src <store file>
--context foo` writes the store file, `foo` being the context); the model takes the conservative union, keeping
that word as an operand and taking as destination candidates both the last operand and the last operand
other than that word. Round 30 also swept every recorded long option, and each modeled command's short
letters, for separate-word consumption against the real tools on this host; every other recorded arity
matched. Round 31: GNU cp, mv, sed, and ed and uutils install, tee, and truncate also exit at once on `--help` or
`--version` (or an unambiguous abbreviation), wherever it stands before `--`, since they permute, and reject an
attached `=value` before writing anything (observed on this host), so such a command writes nothing (after
`--` either word is an operand, and as the separate argument of an option, `cp -S --version`, it is that
argument); ex is not given this, since vim opens a -w script-out file before it reaches --version.

WHICH DIRECTORY (round 31). A relative write target (not starting with `/` or `~`) is resolved against every
directory the command can be in at that point: the payload's cwd, then each confidently understood `cd` or
`pushd` (one literal operand, after -L, -P, -e, -@, or `--`, naming an existing directory; with CDPATH set in
the hook's environment, a relative operand not starting with `./` or `../` is unknown). A cd that surely runs
before what follows (first in its list at the top level of its stream, outside any compound command, not piped,
not in the background) REPLACES the set; any other cd JOINS it; a cd to anything else (a variable, `-`, `~`,
a missing directory), and popd, adds an UNKNOWN directory. A target that resolves under a store root from any
directory in the set is a store target; with an unknown directory in the set it is UNKNOWN (it then counts only
when the command names a store path, as any unknown target). A substitution starts from the directories of the
command holding it, and a cd inside it applies only there. Round 32: a `( ... )` subshell SCOPES the set: the
set in force at its `(` is restored at its `)`, so `(cd <store dir> && ls); printf ... > state.md` resolves
state.md in the parent's directory (round 31 let the subshell's cd leak into the parent); inside the subshell a
cd first in its list, outside any other compound, replaces the set as at the top. A `{ ... }` group does not
scope, as in bash. A stream the walk finds unreliable keeps the join-only walk (no replacement, no scoping). At
most MAX_CWDS (32) directories are tracked per command point: a larger set spends the work budget (fail OPEN).

SHELL -c (round 31). bash, sh, dash, or zsh with -c (a short option cluster holding c; -o, -O, --rcfile, and
--init-file take an argument) whose command string is ONE single-quoted string, or ONE double-quoted string with
no unescaped `$`, backtick, or quote (a backslash before `$`, a backtick, `"`, or a backslash yields that
character and one before a newline removes both, as bash reads it), is analysed recursively (to SHELL_C_DEPTH,
3 levels) with the same directories: its store writes are the enclosing command's (so that command's pipeline
feeds them), and a literal inside the string counts when that inner analysis decides it does. Round 32: it
ALSO counts, as for echo, when the WRAPPER's output reaches a store write: `bash -c 'echo heartbeat: <literal>'
>> <store file>` and `sh -c 'echo heartbeat: <literal>' | tee <store file>` are denied. The wrapper is then
judged like a read command whose data is the whole string: its own redirection or substitution writing a store
path, another store-writing command in its pipeline, an enclosing redirected or piped compound, an unreliable
command, or a pipeline that is not a pure read (a staging one only when a store write names a file it writes)
makes every literal in the string count, even one the string itself sends elsewhere (`bash -c 'echo
<literal> > /tmp/x' | tee <store file>` is denied: the conservative direction). The string's own writes to known
files are the wrapper's writes for staging, and the words of a string holding a store write are the words that
write names, so `printf <literal> > stage; bash -c 'cp stage <store file>'` counts the staged literal. KNOWN GAP
(disclosed, not modeled): a literal inside a shell -c string that is itself inside a command substitution
counts whenever the enclosing command is not a pure read (the conservative direction); and staging chains
running through several shell -c strings are followed only as far as the words and targets each one exposes.
Any other command string (a variable, an expansion, a concatenation of quotes) is not inspected, as before.

WHICH LITERALS (round 31). A future literal counts only when it is DATA of a store write: in the store write's
own simple command (its arguments, here-string, redirections, and substitutions), in a here-document body of
it, in another command of its pipeline, or anywhere inside a compound command (`{ ... }`, `( ... )`, if, while,
until, for, select) whose output is redirected or piped onward. A literal does NOT count when it lies in a
comment (outside a here-document body); in a top-level pipeline made only of READ_COMMANDS (grep, rg, cat,
head, tail, wc, ls, echo, printf without -v, test, `[[`, and the like) with no redirection or write anywhere in
it, not inside a compound whose output is redirected (`grep -F <literal> <store file>; echo checked >> <store
file>`, or `if grep -q <literal> <store file>; then echo found >> <store file>; fi`); in a pipeline of
READ_COMMANDS and tee whose writes all go to KNOWN files that no store-writing command names (`echo <literal> >
/tmp/log; echo checked >> <store file>` is allowed, while `printf <literal> > x; cp x <store dir>` counts, the
store write naming x, as a word or a resolved path, transitively through further staged files); or as the
PATTERN of a grep, egrep, fgrep, or rg inside a command substitution made only of such searches and wc, reading
no redirected input, whose pipeline's output CANNOT hold the pattern (round 32): the pipeline ends in wc or in a
search with a count, quiet, or file-list option (-c, -q, -l, grep -L, or an exact long form such as --count or
--files-with-matches), as in `n=$(grep -cF <literal> <store file>); echo "n=$n" >> <store file>`. A MATCHING
search that prints lines prints file text holding its pattern (with -o, exactly the pattern), so `echo "$(grep
-oF <literal> hits.log)" >> <store file>` counts (round 31 exempted every search pattern, stating wrongly that
a search never prints its pattern); rg -r or --replace and grep --label, which print their argument, are
excluded as before. Every other literal counts, the conservative direction: an assignment (`x=<literal>; echo "$x" >>
<store file>`), a command outside READ_COMMANDS, a for or select word list, and every literal of a command
holding a case, a function definition, coproc, a bare exec (its redirections apply to everything after it), or
a compound the walk cannot match, or of a command that cannot be parsed. BY DESIGN (not modeled): a write in a
branch that never runs is judged as written, so `if false; then <future store write>; fi`, or a write guarded by
a sentinel test (`[ -e <missing file> ] && <future store write>`), is denied.

WORK BUDGET (round 31). The Bash analysis runs under BASH_WORK_BUDGET (80,000) steps: a character-loop step of
the tokenizer counts 3, a token taken by its regex fast path 1, a reserved or special word it inspects in full
2 more, a here-document line 1, a substitution frame 8 in the tokenizer and 8 again in the analysis, the
command-position walk 2 per token, a nested shell -c 30, and the later walks (simple commands, compound
structure, pipelines, staging) 1 to 4 per token or command they visit. Round 32: DIRECTORY work is charged too,
BEFORE it is done (round 31 left it unbudgeted, so 64 KiB of `(cd <dir>);` repeats over 400 directories took
about 200 ms): CWD_COST (6) per directory in the set each cd is resolved from, each write target or staged word
is resolved against, and each set union joins, and a set above MAX_CWDS (32) spends the budget at once; a
budget step of directory work measured cheaper on this host than a tokenizer step. Round 33: the shell -c WRAPPER
check is charged too: each pipeline's aggregates are computed ONCE, charged before the pass, and each wrapper then
costs one step (round 32 rescanned the whole pipeline per wrapper, unbudgeted, so 64 KiB of 450 piped `sh -c`
wrappers took about 107 ms; that input now spends the budget). A command that spends it is
ALLOWED unchecked (fail OPEN, as for any input the hook cannot evaluate), so no input holds the hook past its
latency bound: 64 KiB of dense
trivial commands (about 10,900 repetitions of `: > x;`, about 0.67 tokens per byte) exhausts it in roughly 20 to 40
ms on this host and is allowed even when it ends in a future store write (an EXOTIC, disclosed miss), while
ordinary commands, including 64 KiB of prose, a quoted here-document, or a script of ordinary lines such as
`echo <text> >> <file>`, stay inside it. A read-only search, or a
write whose target lies elsewhere, is allowed even when the command also names a store path and a future
literal. A parsed target that is a
store path establishes the store write by itself (round 26: `cp -t<store dir> x`, whose attached spelling the
raw command text does not show as a standalone token, is caught). A write whose target is UNKNOWN counts as
targeting the store (the fail-toward-writing direction) only when the command text names a store path
anywhere: an unparseable command, a redirection with no operand, a target word holding a `$` (a variable or a
substitution's value), a find `{}` placeholder, a write command run through xargs, which adds operands
from its input, or (round 27) sed, ex, or perl script text that names a store path. A write indicator is, outside quotes
and `#` comments, an output redirection (`>`, `>>`, `>|`, `&>`, an fd-prefixed `N>`, or `<>`) whose target
is not /dev/null (an fd duplication is not one: a `>&` whose operand, after optional whitespace and after
dequoting, is a descriptor number, a number followed by `-`, or `-`, such as `2>&1`, `>&2`, `1>& 2`, or
`>&'2'`), or a word in COMMAND position whose basename is among tee, cp, mv, install, dd, truncate, ed, and
ex (command position: the first word of a token stream, the first word after a separator `;`, `|`, `&`, a
newline, `(`, or `)`, so also after `&&` and `||` and at the start of a `$(...)` or backtick body; the word
after a reserved word `{`, `then`, `do`, `else`, `!`, `if`, `elif`, `while`, `until`, the body of a
function header (`function NAME {`, `function NAME () {`, `NAME() {`, or `NAME ()` then `{` on the next
line: the body's opening `{` or `(` is in command position, and after `function NAME` with no `()` so is
any compound-command body, so `function NAME (( count > 0 ))` and `function NAME [[ ... > ... ]]` are an
arithmetic command and a conditional, and `function NAME while tee ...` runs tee; a write in a function body is detected
whether or not the function is ever called, the conservative direction), after VAR=value
assignments, or after a leading redirection and its operand, output (`N>`) or input (`<`, `N<`, `<<<`,
`<<`, `<<-`, `<&`); the command a prefix command runs, a prefix
being sudo, doas, env, command, builtin, exec, nohup, time, nice, ionice, stdbuf, timeout, chroot, flock, or
xargs, whose options are skipped, together with the separate argument of the options listed for it in
PREFIX_COMMANDS and its leading operands (the timeout duration, the chroot root, the flock file); and the
word after -exec, -execdir, -ok, or -okdir in a simple command whose command word is find, up to that
command's terminating `;` or `+` word), so `rg -e tee <store file>` and `printf '%s' -exec tee` are reads;
or, in command position, sed or perl with an in-place option (`-i`, a short cluster of letters
and digits holding i such as `-pi`, `-i.bak`, `-0777pi`, `-0pi`, or `-l0pi`, or sed's `--in-place` or an
unambiguous abbreviation of it such as `--in`, round 29) later in the same simple command; an unterminated quote, an unterminated substitution, or a redirection with no target
counts as a write. Inside a `[[ ... ]]` conditional (a `[[` word in command position up to its `]]`
word; command position here also survives bash's `time` reserved word and its `-p` then `--` or `--`
options, and `!`, so `if time [[ ... > ... ]]` and `! [[ ... ]]` are conditionals), everything up to
the closing `]]` is operand text: `<` and `>` are string comparisons, not redirections, `(`, `)`, `&&`,
`||`, a `;`, a newline, and the `(`, `)`, and `|` of a regex operand (`[[ $r =~ op=(cp|mv) ]]`) are not
separators, and a `#` is not a comment (`[[ $r =~ ^[[:space:]]*(#|$) ]]`), so no word in the conditional is
a command word (a `[[` touching a metacharacter, as in `[[(-f x) ]]`, opens it too, and that
metacharacter is conditional text); the conditional closes only at a `]]` that starts a word (after
unquoted whitespace) and is followed by whitespace, `;`, `&`, `|`, `<`, `>`, `)`, or the end of the command,
or, in a backtick substitution, by that substitution's closing backtick, so a quoted `']]'` and a `]]`
glued to preceding text (`x]]`, the regex word `(x)]]`, a grouping `)]]`) never close it; there is no
re-read: a conditional still open when its frame ends (the end of the command, a closing backtick, or an
unterminated quote) makes the command unparseable, counted as a write, so `[[ ( -n x )]] && echo ok`, which
bash closes at the glued `)]]`, is unparseable here (a disclosed residual below); a `$(...)`,
backtick, or `<(...)` or `>(...)` process substitution inside it is still scanned as its own command stream;
in `[ ... ]` and `test` an unquoted `>` is
a real output redirection and counts. After the closing `))` of an arithmetic command and the closing `]]`
of a conditional, the next word is in command position whether or not a `;` or newline comes first, so
`while (( n > 0 )) do tee ...` and `if [[ ... ]] then tee ...` run tee. Inside an arithmetic command `(( ... ))` (a `((` in command position,
or the header of `for (( ... ))`, including when the `((` touches its keyword as in `if((`) and an
arithmetic expansion `$(( ... ))`, up to the matching `))`, `<`,
`>`, `<<`, and `>>` are arithmetic operators, not redirections, and no word there is in command position,
so `if (( count > 0 ))` and `"$(( count > 0 ))"` are reads, and the body after a `for ((...))` header is
in command position whether or not a `;` precedes its `do`; a `$(...)` or backtick substitution inside the
arithmetic is still scanned as its own command stream, and a real redirection after the closing `))`
(`(( x )) > <store file>`) still counts. A `$(...)` or backtick substitution, unquoted OR inside double
quotes, is executable text: its body is scanned as its own independent command stream with its own quoting context (as
date_spans() does), so `x="$(printf ... | tee <store file>)"` shows the tee write. Inside a `$(...)`, an
unquoted `case` word in COMMAND POSITION (the first word of the substitution, or the first word after a
separator `;`, `|`, `&`, a newline, `(`, `)`, or after one of the reserved words `{`, `then`, `do`, `else`,
`!`, `if`, `elif`, `while`, `until` that is itself in command position) opens a case
construct until its unquoted `esac` in command position, and while one is open a `)` is a pattern
terminator, not the substitution's end, so `$(case x in x) printf ... > <store file>;; esac)` shows its
write, while a `case` argument (`x="$(rg case <store file>)"`) is an ordinary word. A command with no write
indicator (a read such as `grep` or `rg` over the store) is allowed. The future-dated
literal must not be inside a `$(date ...)` or backtick `date ...`
substitution (`date` optionally after `env`, VAR=value words, or /bin/ or /usr/bin/). Substitution spans are
found in ONE linear pass that follows shell quoting like the shell does: single quotes (and $'...' with its
backslash escapes) make everything literal, double quotes keep only `$(` and backticks active (a parenthesis
inside quotes is not a delimiter), and each substitution starts its own quoting context; an unmatched opener
or an unterminated quote starts no span, so the literals it would have covered are checked.

Literals and zones. YYYY-MM-DD[T or space]HH:MM(:SS(.fraction)?)? then an optional zone: Z (attached or after
one space); a numeric offset +HH, +HHMM, or +HH:MM, or with - (attached or after one space); or, after one
space, UTC, GMT, or a letter abbreviation of 2 to 6 capitals. This zone grammar and the scheduling keyword
list are duplicated verbatim in stamp-truth-stop.py (python3 -I forbids a sibling import); a self-test
asserts they are identical. Z, UTC, and GMT are UTC; an offset is applied as written; a letter abbreviation
must be the process local zone's abbreviation for that wall time (both DST abbreviations are tried; the
earlier instant is taken if both match); any other abbreviation is an unknown zone and that literal is
SKIPPED, never guessed. A literal with NO zone is compared as local wall time AND as UTC, and counts as future
only if it is future under both readings, so an unzoned local time up to the zone's offset ahead of the clock
can pass (a disclosed miss west of UTC, where the UTC reading of a composed local time lies in the past). Each literal is converted in isolation in integer microseconds (an
overflowing or invalid literal is skipped without disabling the rest). A literal more than 60 seconds after
now is future.

Observed-time vs scheduled. A future literal is allowed when a scheduling keyword IMMEDIATELY precedes it on
its line (a whole line of the resulting file, or a line of the Bash command text): the keyword ends at most
SCHED_GAP_TOKENS (3) word tokens before the literal (a word token holds a letter or digit; `:` or `[` do not
count), as in "next_run: T", "due T", "deadline: T", "scheduled for T", "until T", "not before T", "by T".
Keywords (case-insensitive, each a whole word, optionally plural, except the stem `expir`, which covers
expires and expiry): due, deadline, expir, until, next, scheduled, not before, not-before, eta, planned, "target date",
"target:", `by ` (with its trailing space), and (round 24, the validity words, as in "valid through T")
through and valid. Each keyword matches as a WHOLE word, optionally plural (round 24: "validated",
"throughput", "duet", or "etag" exempt nothing), except the stem `expir`. A keyword after the literal, or further back, does not exempt it.

Table-header exemption (Write, and Edit/MultiEdit on a reconstructed whole file only). A future literal in a
markdown table row is ALSO allowed when the header cell of its column carries a scheduling keyword (the same
keyword regex, on the stripped header cell text), so `| Item | Due |` over `|---|---|` over a row whose Due
cell holds a future time is allowed. This is additive: the keyword-on-line rule above is unchanged. Table
context is tracked over EVERY line of the resulting file, including unchanged carry-over lines that are not
themselves checked, so a new row under an existing header is exempt. The recognized forms follow GFM: a
header is a line holding at least one unescaped `|` (a leading pipe is optional), immediately followed by a
delimiter row (the whole stripped line is cells of an optional colon, ONE or more hyphens, and an optional
colon, pipe-separated, containing at least one pipe, leading and trailing pipes optional) with the same cell
count as the header. Each following line is a row of that table unless it ends the table: a line with no
unescaped pipe is still a row for continuation (GFM continues a table on a pipe-free line, so a later piped
row keeps its header exemption), but a pipe-free row NEVER receives a header-column exemption: it is checked
like an ordinary line, so only the keyword-on-its-own-line rule can exempt its literals; a blank line, or a line whose stripped
form starts with a list marker (`-`, `*`, or `+`, or digits then `.` or `)`), or an ATX heading (one to six `#`), each followed by
whitespace or the line end, a `>` blockquote, or a code fence (three backticks or tildes) ends it; a `#` not
forming an ATX heading (a row such as `#123 | ...`) does not. As in GFM, leading and trailing pipes are
optional on every line independently: a header with a leading pipe (`| Item | Due |`) may be followed by a
bare row (`release | ...`), and each row's columns are counted from its own leading pipe, if any.
Cells are split on unescaped `|` only (a `\\|` does not split, so it
does not shift columns); a leading pipe (only whitespace before it) opens the first cell and a trailing pipe
closes the last, so a literal lies in column k when k unescaped pipes (not counting a leading one) precede
it. The header line and the delimiter row are themselves checked normally, as ordinary lines. A row's
carry-over is keyed by its table context: a row line is an unchanged carry-over only when the same line
existed in the old file as a row under the SAME header line and delimiter row, so when an edit changes a
table's header or delimiter (repurposing a `Due` column as `Observed`, say), or moves a row into or out of
a table, EVERY row of that table is checked as changed, even rows identical to lines of the old file.
Each distinct (header, delimiter) context is given an integer id once, shared by the old- and new-file scans,
so a row's carry-over key never re-compares the header text (linear in rows, flat in header length).

Contract: allow = no stdout, exit 0; deny = the PreToolUse hookSpecificOutput permissionDecision "deny" JSON
on stdout, exit 0. Fail-OPEN (allow) on unparseable input or any internal error, including an argument,
stdin, or JSON error before the payload is evaluated: a DISCIPLINE guard, not a security boundary. The
payload is read as BYTES and parsed by json.loads, so its decoding does not depend on the process locale. An
error writing the deny (a closed or full stdout) also fails open (round 24): it is swallowed and the hook
exits 0; if the stream cannot even be pointed at /dev/null, the hook ends at once with os._exit(0), so no
exit-time flush can fail it. Kill-switch: a subordinate worker process, detected as env AIQT_HOOKS_WORKER=1
(legacy spellings are also accepted, see _is_worker), allows, writing one warning line to stderr (round 24;
never stdout, and on exit 0 stderr reaches only the host's debug log, so the skip is logged, not shown).
Subagent calls (a payload carrying agent_id or agent_type) are DELIBERATELY checked exactly like
main-session calls, with no skip: a subagent applies store writes on the main session's behalf, and a helper
can compose a timestamp that is then relayed into a record, so exempting it would open the very drift path
this hook guards. Only the worker kill-switch above allows.

RESIDUAL COVERAGE. Quoted text is trusted as quotation (a MISS,
disclosed): a composed stamp placed inside a quote or code span in a record (a `>` line, an inline code
span, or, on a whole file, a matched fenced block) is not caught; a later edit that removes the `>`, the
backticks, or the fence around it does expose it to the check. Only the ISO-like literal shape above is caught:
a compact sess-YYYYMMDDTHHMMSSZ id, an epoch number, a 12-hour or natural-language time, or a time split
across tokens is not. The keyword test is lexical: a keyword (as a whole word in a path, an identifier, or prose) within three
word tokens before a fabricated literal exempts it (for example "hb (next check) 2099-..."). The carry-over
test is a line multiset (a table row keyed by its header and delimiter), not an alignment: a line that
already existed elsewhere in the file, in the same table context, is exempt wherever it lands. Only the first 4 MiB of an existing file count: for a larger file a Write's carry-over
test sees only that prefix, and an Edit is checked on its fragment only, so a time-only fragment edit
(`17:45` to `22:25`) there is a MISS. The table-header exemption applies only to whole files: the fragment
path (an Edit on a file not read whole) and Bash get no table context, so a future literal in a table row
under a scheduling header cell is denied there unless a keyword immediately precedes it on its own line (a
disclosed false positive). Table continuation follows GFM's pipe rules but recognizes only a subset of its
block-interruption rules (a blank line, a list marker, an ATX heading, a blockquote, or a fence end the
table), and that mismatch errs in BOTH directions. A MISS: a line GFM would end the table on for another
reason (an HTML block or a thematic break, say) is treated as a row here, so a literal in its
scheduling-header column is exempt, as is every later non-blank line up to the next recognized block end. A
FALSE POSITIVE: a new table GFM would start after such an unrecognized block is not recognized (its header and
delimiter row read as rows of the old table), so its scheduling column gets no exemption; and a line this hook
treats as a block start where GFM would not (for example a list-marker shape GFM does not let interrupt the
table) ends the table early, so a later row loses its header exemption.
The header rule is lexical like the keyword rule: a keyword-bearing header cell
exempts every literal in its column of a row holding an unescaped pipe. A pipe-free row gets no header
exemption, so a bare scheduled value on its own pipe-free row under a scheduling header cell 0 (a GFM
one-cell row) is denied (a disclosed false positive); the remedy is a scheduling keyword immediately before
the value on that line, or a pipe that makes it a piped row. MISS (disclosed): because GFM keeps a table
open through pipe-free lines until a blank line, prose written directly under a table with no blank line
stays in it, and a later prose line that happens to hold an unescaped pipe (a shell pipe, say) is read as a
piped row, so text before its first pipe gets column 0's header exemption; leave a blank line after a
table. Bash is lexical by design. FALSE POSITIVES: (round 31) a future literal in an assignment, a command
outside READ_COMMANDS, or a loop's word list counts for a store write anywhere in the same command (`ts=<future>;
echo ok >> <store file>` is denied), as does every literal of a command the structure walk finds unreliable (a
case, a function definition, coproc, a bare exec, an unmatched compound), and a write in a branch that never
runs is judged as written (by design, see WHICH LITERALS); a write whose target is UNKNOWN counts as a store write
when the command names a store path anywhere, so `rg <future> <store file> > /dev/shm/out-$(date +%s)` (a `$`
in the target), a `find /elsewhere ... -exec cp <store file> {} +` (the `{}` TARGET is unknown; a
`-exec cp {} /elsewhere` from a store dir is allowed, `{}` being cp's source), and an xargs-run write to /elsewhere are
denied (round 31: a here-document body is no longer read as command text: a quoted delimiter's body is skipped,
and an unquoted one is scanned only for the substitutions that run in it, so a `>` in a body is text, as bash
reads it), as is a command where an `i` in a sed or perl short cluster is really an option
argument rather than the in-place switch (`sed -ei...`, `perl -Mstrict`), or where a write-command name
lands in command position only lexically (the argument of `command -v`, say); likewise (round 27, as in
round 25) a sed expression or program, an ex -c or --cmd command, or a perl -e or -E program that merely
MENTIONS a store path while writing elsewhere (`sed -i 's#<store dir>/a#b#' /dev/shm/x`, a pattern) makes
the target unknown and is denied (round 28 narrows this for sed only: a sed with no in-place option whose
program parses as holding no `w`, `W`, `e`, `s///w`, or `s///e` is allowed; a sed program the conservative
parser does not accept, for example one holding any `$` such as `\\|<store file>$|p`, a label or a/i/c text
it does not model, or a backslash inside a bracket expression, is still denied, and ex is NOT narrowed: an
ex -c or --cmd command naming a store path is denied even when it only prints or searches, such as `-c
'g#<store file>#p'`, because ex's command grammar, with `|` chaining, :global and :normal nesting, shell
escapes, and many abbreviated write commands, cannot be parsed here with confidence); (round 30) under
install's bare `--context` the next word is ALSO a destination candidate, so where uutils consumes a store
path there as the context while the real destination lies elsewhere (`install src /elsewhere --context
<store dir>`) the command is denied, and `install -d` or `mv --exchange` naming a store path is denied
whenever the command also holds a future literal; and a perl `-i`
placed after perl's first operand (a file name to perl,
not the in-place switch) still arms in-place detection; rephrase or split the
command. MISSES: a write whose target reaches a store file by a spelling neither the store path token rule
nor the round-31 directory resolution follows (a relative path after a cd the model cannot follow, a
symlink, a variable, a glob); round 31 resolves `cd <store dir> && echo <future> > state.md`, a miss until round
30; a write whose
option parsing differs from the modeled getopt subset: an option's separate argument the model does not list
is read as an operand (round 29 closes the abbreviated-long-option case, `cp src <store file> --suf .bak`,
which round 26 disclosed as a miss: `--suf` now resolves to `--suffix` and `.bak` is consumed; a long option
the recorded per-command set lacks, on a command other than sed, perl, or ex, still takes no argument here;
the arities recorded are those of the tools observed on this host, GNU sed, cp, mv, and ed and uutils
install, tee, and truncate, so another implementation whose option arity differs, beyond install's
`--context` modeled as the union above, is not modeled),
and POSIXLY_CORRECT in the environment is not modeled: under it GNU sed, cp, and mv stop at
their first operand (observed, round 27), so `sed -i s/a/b/ X -f <store file>` then edits the store file
while this model consumes it as the -f script file; a store write performed by a script the command names
as a FILE rather than carrying as text (a sed -f file, an ex -S or -u file, a perl program file or -M
module, each holding a `w` or an `open`), or by script text whose store path is spelled other than as a
store path token (relative, or through a variable); a write command
run through a wrapper not listed as a prefix (ssh, su -c, parallel, watch, or a shell -c string that is not
one quoted literal with no expansion: round 31 inspects a literal one), named through
a variable or substitution (`$T`, `"$(command -v tee)"`), or placed after an option of a listed prefix that
takes a separate argument but is not listed for it (the argument is then read as the command, and the write
command after it is not in command position); a store path reached through a variable, a glob, a `cd` the
model cannot follow (see WHICH DIRECTORY), a symlink, or, in an absolute word, a `..` or doubled-slash
spelling; a future literal that reaches the store through a file the store write names only by a variable,
glob, or other spelling than the staging write's (round 31: that staging pipeline's literals are then not
counted); a write
through a program or script with no write indicator in the command text (`python -c`, `python3 script.py`,
`awk -i inplace`, `rsync`, `ln`, an editor, a heredoc-fed interpreter), or a script that writes internally;
a future literal inside a `$(date ...)` span in a quoted heredoc body or a
comment (text the shell never runs); a literal built from parts; and a write via another tool
(NotebookEdit). Only the configured store roots are covered: nothing is when neither AIQT_STORE_ROOT nor
its legacy fallback spelling is set, or when AIQT_STORE_ROOT is set but empty (it then takes precedence over
the fallback); and a store the configuration does not name, or reaches by a different spelling, is not
covered. The
date-substitution scan reads a heredoc body (of either delimiter kind) like command text, so an apostrophe there can hide a
later `$(date ...)` span, which then fails toward denying. date_spans() is not case-aware: a case pattern
`)` inside a `$(date ...)` or enclosing substitution ends that span early, which only shrinks the exempt
span (fails toward denying); the write test's case tracking is a lexical command-position rule, not a
parse: it does not follow a backtick frame (where `)` never closes anyway), and exotic placements (a `case`
after a leading redirection or after an unlisted reserved word such as `coproc`, a pattern word spelled `case`
after `;;`, or a keyword produced by an alias or eval) are not emulated; such a construct can end the
substitution early and hide a write inside it (a MISS) or leave it unterminated (a false deny). A `((` or
`$((` whose text closes with a `)` at its own level that is not followed by `)` (bash then re-reads it as a
subshell group or a command substitution holding one, `$((rg -c x <store file>) )`) is not emulated and
counts as a write (a false deny; spell it `$( (...) )`); a `((` not in command position is not arithmetic
(the body word after `function NAME` is in command position, so `function NAME (( ... ))` is arithmetic),
so its `>` counts (bash rejects it as a syntax error anyway). A store write whose future timestamp falls
within three words of the shell keyword `until` (for example `until tee <store file> <<< '<future>'; do :;
done`) is not caught, because the scheduling-keyword exemption reads it as "until <date>"; this is an exotic
form and a disclosed miss. Inside `[[ ... ]]` a `#` is never read as a comment, while bash starts a comment
at a `#` that begins a word (after whitespace) there, legal only where bash then accepts a newline (right
after the opening `[[`, or after `&&`, `||`, `(`, or `!`); the comment's text is scanned as conditional
text, so a `]]` or a quote inside it can move where the conditional or a quoted string ends, which can add a
command word (a false deny) or hide a later write (an exotic, disclosed possible miss). A `]]` glued to a
grouping `)` closes the conditional in bash (`[[ ( a )]]`), but never here: a conditional closes only at a
word-initial `]]`. With no later word-initial `]]` in its frame the conditional is still open when the frame
ends (the end of input, a closing backtick, or an unterminated quote), so the command is unparseable and
counts as a write, denied only when it also names a store path and holds a future literal (an exotic,
disclosed false deny, for example `rg <future> <store file>; [[ ( -n x )]] && echo ok`). With a later
word-initial `]]` in the same frame the conditional is read as running on to it, and a write between the
two is not caught (exotic; a disclosed miss), for example
`[[ ( a )]] && tee <store file> <<< '<future>'; [[ x ]]`. Inside the conditional a `;`, a lone `&`, a lone
`|` outside a regex operand (`[[ a =~ | ]]` is legal), and a `]]}` word are bash syntax errors, read here as
operand text. A wrong host clock is enforced faithfully.

Self-test: python3 -I -S -B future-stamp-write.py --self-test
Run beside its sibling hooks, the self-test also checks that the code shared verbatim with them is identical.
Run alone (a single-hook install), those sibling-parity checks are SKIPPED, not passed, each naming the absent
sibling; with env AIQT_HOOKS_REQUIRE_SIBLINGS=1 an absent sibling FAILS them instead (for a repository gate). A
sibling that is present but unreadable fails them either way.
"""

import bisect
import collections
import datetime
import json
import os
import re
import stat
import sys
import time

UTC = datetime.timezone.utc
US = 1_000_000
FUTURE_SLACK_US = 60 * US
EXISTING_MAX_BYTES = 4 << 20
MAX_REPORTED = 20
_EPOCH = datetime.datetime(1970, 1, 1)
_EPOCH_UTC = _EPOCH.replace(tzinfo=UTC)
_ONE_US = datetime.timedelta(microseconds=1)

# Duplicated verbatim in stamp-truth-stop.py; the self-test asserts the two copies are identical.
SCHED_KEYWORDS = ("due", "deadline", "expir", "until", "next", "scheduled", "not before", "not-before", "eta",
                  "planned", "target date", "target:", "by ", "through", "valid")
SCHED_GAP_TOKENS = 3
TIME_GRAMMAR = r"(?<!\d)(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,9}))?)?"
ZONE_GRAMMAR = r"(?:[ ]?(Z)|[ ]?([+-]\d{2}(?::?\d{2})?)|[ ](UTC|GMT|[A-Z]{2,6}))(?![A-Za-z0-9])"

# Round 24: a keyword matches as a WHOLE word (an optional plural `s`, then no further letter or digit), so
# "validated", "throughput", "duet", or "etag" exempt nothing; only a listed STEM matches as a prefix ("expir"
# covers expires, expiry, expiration).
SCHED_STEMS = ("expir",)
_SCHED_RE = re.compile(r"(?<![a-z0-9])(?:" + "|".join(
    re.escape(k) + (r"[a-z]*" if k in SCHED_STEMS else r"(?:s(?![a-z0-9]))?(?![a-z0-9])" if k[-1].isalnum() else "")
    for k in SCHED_KEYWORDS) + ")", re.IGNORECASE)  # a keyword ending in a space or `:` already ends a word
_TOKEN_RE = re.compile(r"\S+")
_WORDCH_RE = re.compile(r"[A-Za-z0-9]")
_STAMP_RE = re.compile(TIME_GRAMMAR + "(?:" + ZONE_GRAMMAR + r")?(?![\d:])")
_TOKEN_BEFORE = r"(?<![A-Za-z0-9_.~/-])"
_TOKEN_AFTER = r"(?![^/\s'\"`;|&<>()])"
_DATE_CMD_RE = re.compile(r"[ \t]*(?:(?:(?:/usr)?/bin/)?env[ \t]+)?(?:[A-Za-z_][A-Za-z0-9_]*=[^ \t`()]*[ \t]+){0,4}"
                          r"(?:(?:/usr)?/bin/)?date(?![\w-])")
DATE_LOOKAHEAD = 256
# Duplicated verbatim in stamp-truth-stop.py (code and quote exemption); the self-test asserts they are identical.
_BTICK_RE = re.compile(r"`+")
_FENCE_RE = re.compile(r"`{3,}|~{3,}")


# ---- store paths ----

class _StoreCtx:
    """Round 31: store-root matching computed ONCE per evaluation (round 30 re-read the environment, re-tested
    the lease file with a stat, and rebuilt the token pattern for every target word): the roots, one compiled
    store-path token pattern, and memos for word tests, relative-path resolution, and directory tests."""
    __slots__ = ("roots", "token_re", "names_memo", "under_memo", "isdir_memo")

    def __init__(self):
        self.roots = _store_roots_uncached()
        pats = [_TOKEN_BEFORE + re.escape(r.rstrip("/") or "/") + _TOKEN_AFTER for r in self.roots]
        self.token_re = re.compile("|".join(pats)) if pats else None
        self.names_memo, self.under_memo, self.isdir_memo = {}, {}, {}

    def names(self, text):
        got = self.names_memo.get(text)
        if got is None:
            got = self.names_memo[text] = self.token_re is not None and self.token_re.search(text) is not None
        return got

    def under(self, p):
        """True when the normalized absolute path `p` lies under a store root (the under_store() test)."""
        got = self.under_memo.get(p)
        if got is None:
            got = any(p == r or p.startswith(r.rstrip("/") + "/") for r in self.roots)
            self.under_memo[p] = got
        return got

    def isdir(self, p):
        got = self.isdir_memo.get(p)
        if got is None:
            got = self.isdir_memo[p] = os.path.isdir(p)
        return got


_ACTIVE = None  # round 31: the _StoreCtx of the evaluation in progress (set by evaluate()), else None


def _store_ctx():
    return _ACTIVE if _ACTIVE is not None else _StoreCtx()


def store_roots():
    """The configured store roots (env AIQT_STORE_ROOT; there is no default); see the docstring. Round 31:
    within one evaluation the value is computed once (_StoreCtx)."""
    if _ACTIVE is not None:
        return _ACTIVE.roots
    return _store_roots_uncached()


def _store_roots_uncached():
    env = _cfg("STORE_ROOT")
    if env:
        return [os.path.normpath(r) for r in env.split(os.pathsep) if r and os.path.isabs(r)]
    return []


def under_store(path, cwd=None):
    if not isinstance(path, str) or not path:
        return False
    if not os.path.isabs(path):
        if not cwd:
            return False
        path = os.path.join(cwd, path)
    return _store_ctx().under(os.path.normpath(path))


def names_store_path(cmd):
    """True when the command text contains an explicit store path token (lexical; see the docstring)."""
    return _store_ctx().names(cmd)


# ---- literals ----

def _naive_us(y, mo, d, hh, mi, ss, us):
    return (datetime.datetime(y, mo, d, hh, mi, ss, us) - _EPOCH) // _ONE_US


def _local_us(y, mo, d, hh, mi, ss, abbr=None):
    """Epoch us for a local wall time; with `abbr`, only a reading whose zone abbreviation matches."""
    found = []
    for isdst in (0, 1):  # explicit isdst: deterministic, independent of earlier mktime calls
        try:
            ts = time.mktime((y, mo, d, hh, mi, ss, 0, 0, isdst))
            lt = time.localtime(ts)
        except (OverflowError, ValueError, OSError):
            continue
        if (lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, lt.tm_min, lt.tm_sec) != (y, mo, d, hh, mi, ss):
            continue
        if abbr is None or lt.tm_zone == abbr:
            found.append(int(ts))
    return min(found) * US if found else None


def literal_us(m):
    """Earliest plausible epoch us for a matched literal, or None (invalid, overflow, or unknown zone)."""
    try:
        y, mo, d, hh, mi = (int(m.group(i)) for i in range(1, 6))
        ss = int(m.group(6) or 0)
        us = int(((m.group(7) or "") + "000000")[:6])
        z, off, abbr = m.group(8), m.group(9), m.group(10)
        naive = _naive_us(y, mo, d, hh, mi, ss, us)
        if z or abbr in ("UTC", "GMT"):
            return naive
        if off:
            digits = off[1:].replace(":", "")
            hours, mins = int(digits[:2]), int(digits[2:] or 0)
            if hours > 18 or mins > 59:
                return None
            delta = (hours * 3600 + mins * 60) * US
            return naive - delta if off[0] == "+" else naive + delta
        loc = _local_us(y, mo, d, hh, mi, ss, abbr)
        if abbr:
            return None if loc is None else loc + us
        return None if loc is None else min(naive, loc + us)
    except (OverflowError, ValueError, OSError, TypeError):
        return None


def sched_exempter(line):
    """A predicate pos -> True when a scheduling keyword ends at most SCHED_GAP_TOKENS word tokens before
    position `pos` of `line` (a word token holds a letter or digit; punctuation-only tokens such as `:` or `[`,
    and the token containing `pos`, are not counted). Linear: keyword ends and tokens are found once per line.
    Duplicated verbatim in the sibling hook; the self-test asserts the two copies are identical."""
    ends = [k.end() for k in _SCHED_RE.finditer(line)]
    if not ends:
        return lambda pos: False
    toks = [(t.start(), t.end()) for t in _TOKEN_RE.finditer(line) if _WORDCH_RE.search(t.group())]
    starts, tends = [s for s, _ in toks], [e for _, e in toks]

    def exempt(pos):
        k = bisect.bisect_right(ends, pos) - 1
        if k < 0:
            return False
        between = bisect.bisect_right(tends, pos) - bisect.bisect_left(starts, ends[k])
        return between <= SCHED_GAP_TOKENS
    return exempt


def _code_lines(lines):
    """Indexes of lines inside a MATCHED fenced code block (fence lines included); an unclosed fence is text."""
    code, open_i, fch, flen = set(), None, "", 0
    for i, ln in enumerate(lines):
        s = ln.strip(" \t")
        if open_i is None:
            m = _FENCE_RE.match(s)
            if m and not (s[0] == "`" and "`" in s[m.end():]):
                open_i, fch, flen = i, s[0], m.end()
        elif s and len(s) >= flen and s == fch * len(s):
            code.update(range(open_i, i + 1))
            open_i = None
    return code


def _code_spans(line):
    """Sorted [start, end) inline code spans on one line: a backtick run closed by the next run of the same
    length (runs of other lengths inside are content); an unclosed run opens nothing. Linear."""
    spans, open_len, open_start = [], 0, 0
    for r in _BTICK_RE.finditer(line):
        n = r.end() - r.start()
        if not open_len:
            open_len, open_start = n, r.start()
        elif n == open_len:
            spans.append((open_start, r.end()))
            open_len = 0
    return spans


def _in_spans(spans, pos):
    k = bisect.bisect_right(spans, (pos, float("inf"))) - 1
    return k >= 0 and pos < spans[k][1]


def future_on_line(line, now_us, base=0, in_subst=None, cache=None, extra_exempt=None, spans=None):
    """Future literals on one line not immediately preceded by a scheduling keyword. `in_subst(pos)` is true
    for a literal starting at absolute position base+pos that lies inside a date substitution; `cache` maps
    a literal to its converted value so a repeated literal is converted once; `extra_exempt(pos)`, when
    given, is an ADDITIONAL exemption for a literal starting at line position pos (the table-header rule);
    `spans`, when given, are the line's inline code spans, and a literal starting inside one is exempt."""
    found, seen, sched = [], set(), None
    cache = {} if cache is None else cache
    for m in _STAMP_RE.finditer(line):
        lit = m.group(0)
        if lit in seen or (in_subst is not None and in_subst(base + m.start())) \
                or (spans and _in_spans(spans, m.start())):
            continue
        if lit not in cache:
            cache[lit] = literal_us(m)
        got = cache[lit]
        if got is None or got - now_us <= FUTURE_SLACK_US:
            continue
        if sched is None:
            sched = sched_exempter(line)
        if not sched(m.start()) and not (extra_exempt is not None and extra_exempt(m.start())):
            seen.add(lit)
            found.append(lit)
    return found


# a GFM delimiter row (whole stripped line): cells of optional colon, 1+ hyphens, optional colon
_DELIM_ROW_RE = re.compile(r"\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?")


def _pipes(line):
    """Positions of the unescaped `|` characters of `line` (a `\\|` does not split a cell). Linear."""
    return [i for i, c in enumerate(line) if c == "|" and (i == 0 or line[i - 1] != "\\")]


def _leading_pipe(line, pipes):
    """1 when the first unescaped pipe of `line` has only whitespace before it (a leading pipe), else 0."""
    return 1 if pipes and not line[:pipes[0]].strip() else 0


def _table_cells(line, pipes):
    """The cell texts of a table line split on its unescaped pipes (`pipes`, non-empty): a leading pipe opens
    the first cell and a trailing pipe closes the last, so neither adds an empty cell."""
    bounds = [-1] + pipes + [len(line)]
    cells = [line[bounds[k] + 1:bounds[k + 1]] for k in range(len(bounds) - 1)]
    if not line[pipes[-1] + 1:].strip():
        cells.pop()
    return cells[_leading_pipe(line, pipes):]


def _delim_cell_count(s):
    """The cell count of a stripped GFM delimiter row (it must contain a pipe), else None."""
    if "|" not in s or not _DELIM_ROW_RE.fullmatch(s):
        return None
    s = s[1:] if s.startswith("|") else s
    s = s[:-1] if s.endswith("|") else s
    return s.count("|") + 1


def _row_exempter(line, pipes, sched_cols):
    """A predicate pos -> True when position `pos` of table row `line` lies in a column whose header cell
    carries a scheduling keyword (column k: k unescaped pipes, not counting a leading one, precede pos)."""
    lead = _leading_pipe(line, pipes)

    def exempt(pos):
        return (bisect.bisect_right(pipes, pos) - lead) in sched_cols
    return exempt


_BLOCK_START_RE = re.compile(r"(?:[-*+]|[0-9]+[.)]|#{1,6})(?:[ \t]|$)|>|```|~~~")


def _ends_table(line, pipes):
    """True when `line` cannot continue the table it follows: it is blank or starts a new block (a list
    marker, an ATX heading (`#` to `######` then whitespace or the line end), a `>` blockquote, or a code
    fence). A pipe-free line continues the table as a one-cell row (GFM), so `pipes` does not decide it; a
    leading pipe is optional on a row whatever the header had (GFM)."""
    s = line.strip()
    return not s or bool(_BLOCK_START_RE.match(s))


def _table_walk(text, tables=True, ids=None):
    """Yield (line, ctx, extra) for every line of `text`, linearly. ctx is an integer id of the table context
    (header line, delimiter row) for a row of a markdown table (see the docstring's GFM forms), assigned once
    per distinct context through `ids` (a dict shared across scans, so equal contexts get equal ids and a row
    key never compares the header text again), and None for any other line (a header and a delimiter row
    included); extra is the row's header-keyword exempter, or None (always None for a pipe-free row, which
    keeps the table open but gets no header exemption). With tables False, every line is
    (line, None, None)."""
    ids = {} if ids is None else ids
    prev, ctx, sched = None, None, None  # prev: (line, pipes) of a possible header; ctx, sched: table
    for line in text.splitlines():
        if not tables:
            yield line, None, None
            continue
        pipes = _pipes(line)
        if ctx is not None:
            if not _ends_table(line, pipes):
                # only a row with an unescaped pipe gets the header exemption; a pipe-free row keeps the
                # table open (GFM) but is checked as an ordinary line
                yield line, ctx, (_row_exempter(line, pipes, sched) if sched and pipes else None)
                continue
            ctx = None  # a new block (see _ends_table) ends the table
        if prev is not None:
            ncols = _delim_cell_count(line.strip())
            if ncols is not None:
                hcells = _table_cells(*prev)
                if ncols == len(hcells):
                    ctx = ids.setdefault((prev[0], line), len(ids))
                    sched = {k for k, c in enumerate(hcells) if _SCHED_RE.search(c.strip())}
                    prev = None
                    yield line, None, None
                    continue
        prev = (line, pipes) if pipes else None
        yield line, None, None


def future_in_changed_lines(new, old, now_us, tables=True, fences=True):
    """Future observed-time literals on whole lines of `new` that are not unchanged carry-overs from `old`.
    With `tables`, markdown-table context is tracked over EVERY line of `new` (carry-overs included, though
    they are not checked), so a row's literal is also exempt when its column's header cell holds a keyword;
    and a row is a carry-over only under the same header and delimiter it had in `old`. Quoted text is not
    an observed-time record: a `>` blockquote line and a literal inside an inline code span are exempt, and
    with `fences` so is every line of a MATCHED fenced code block of `new` (the Stop hook's rules)."""
    ids = {}  # table context -> integer id, shared by both scans (a row key never re-compares a header)
    # a carry-over is keyed by fence membership too, so a line an edit moves out of a code block is checked
    old_code = _code_lines((old or "").splitlines()) if fences else set()
    carry = collections.Counter((ctx, i in old_code, line)
                                for i, (line, ctx, _x) in enumerate(_table_walk(old or "", tables, ids)))
    bad, seen, cache = [], set(), {}
    code = _code_lines((new or "").splitlines()) if fences else set()
    for i, (line, ctx, extra) in enumerate(_table_walk(new or "", tables, ids)):  # one item per line of new
        key = (ctx, i in code, line)
        if carry[key] > 0:
            carry[key] -= 1
            continue
        if i in code or line.lstrip().startswith(">"):
            continue
        spans = _code_spans(line) if "`" in line else None
        for lit in future_on_line(line, now_us, cache=cache, extra_exempt=extra, spans=spans):
            if lit not in seen:
                seen.add(lit)
                bad.append(lit)
    return bad


def read_existing(path, limit):
    """(text, whole): the text of at most `limit` bytes of a REGULAR file, and whether that is the WHOLE file.
    An absent file is ("", True); a file over `limit`, unreadable, or not regular (FIFO, device) has whole
    False (with "" for the last two)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOCTTY", 0) | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        return "", True
    except (OSError, TypeError, ValueError):
        return "", False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return "", False
        chunks, got = [], 0
        while got <= limit:
            b = os.read(fd, min(1 << 16, limit + 1 - got))
            if not b:
                break
            chunks.append(b)
            got += len(b)
        data = b"".join(chunks)
        return data[:limit].decode("utf-8", "replace"), got <= limit
    except OSError:
        return "", False
    finally:
        os.close(fd)


def apply_edits(original, edits):
    """The file text after applying [(old, new, replace_all)] in order, or None when an edit cannot apply."""
    text = original
    for old, new, every in edits:
        if old == "":
            if text != "":
                return None
            text = new
        elif old not in text:
            return None
        else:
            text = text.replace(old, new) if every else text.replace(old, new, 1)
    return text


# ---- shell (lexical only) ----

_DS_TOP_RE = re.compile(r"[\\$`\"'()]")  # round 31: the characters date_spans() acts on outside quotes
_DS_DQ_RE = re.compile(r'[\\$`"]')  # ... inside double quotes
_DS_ANSI_RE = re.compile(r"[\\']")  # ... inside $'...'


def date_spans(cmd, budget=None):
    """Sorted, merged [start, end) spans of `$(date ...)` and backtick `date ...` substitutions. ONE linear,
    quote-aware pass: each frame on the stack (the top level, a `$(`, a plain `(`, or a backtick) keeps its own
    quote state; inside single quotes (or $'...', which honours backslash escapes) nothing is special; inside
    double quotes only `$(` and backticks open frames and a parenthesis is literal. A span is recorded only
    when its frame closes, so an unmatched opener or an unterminated quote starts no span. The `date` test
    looks ahead a bounded DATE_LOOKAHEAD characters. Round 31: the pass jumps straight to the next character
    that can matter in the current quote state (a regex search), not one character per step; `budget`, when
    given, is charged one unit per step (_Exhausted when it runs out)."""
    def is_date(j):
        return _DATE_CMD_RE.match(cmd[j:j + DATE_LOOKAHEAD]) is not None

    # frame: [kind, start, dated, quote]; kind in "top", "$(", "(", "`"; quote in None, "'", "$'", '"'
    spans, stack, i, n = [], [["top", 0, False, None]], 0, len(cmd)
    steps = 0
    while i < n:
        steps += 1
        top = stack[-1]
        q = top[3]
        if q == "'":
            j = cmd.find("'", i)
            if j < 0:
                break
            top[3], i = None, j + 1
            continue
        if q == "$'":
            m = _DS_ANSI_RE.search(cmd, i)
            if m is None:
                break
            i = m.start()
            if cmd[i] == "\\":
                i += 2
                continue
            top[3], i = None, i + 1
            continue
        m = (_DS_DQ_RE if q == '"' else _DS_TOP_RE).search(cmd, i)
        if m is None:
            break
        i = m.start()
        c = cmd[i]
        if c == "\\":
            i += 2
            continue
        if c == "$" and cmd.startswith("$(", i):
            stack.append(["$(", i, is_date(i + 2), None])
            i += 2
            continue
        if c == "`":
            if top[0] == "`":
                stack.pop()
                if top[2]:
                    spans.append((top[1], i + 1))
            else:
                stack.append(["`", i, is_date(i + 1), None])
            i += 1
            continue
        if q == '"':
            if c == '"':
                top[3] = None
        elif c == "'":
            top[3] = "'"
        elif c == '"':
            top[3] = '"'
        elif c == "$" and cmd.startswith("$'", i):
            top[3] = "$'"
            i += 2
            continue
        elif c == "(":
            stack.append(["(", i, False, None])
        elif c == ")" and top[0] in ("$(", "("):
            stack.pop()
            if top[2]:
                spans.append((top[1], i + 1))
        i += 1
    if budget is not None:
        budget.spend(steps)
    spans.sort()
    merged = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


WRITE_COMMANDS = frozenset(("tee", "cp", "mv", "install", "dd", "truncate", "ed", "ex"))
INPLACE_COMMANDS = frozenset(("sed", "perl"))
_INPLACE_OPT_RE = re.compile(r"-[A-Za-z0-9]*i")  # the short forms; a `--` word goes through _resolve_long
_FD_DUP_RE = re.compile(r"[0-9]+-?|-")
_SHELL_SEPARATORS = ";|&\n"
_CMD_PREFIX_WORDS = frozenset(("{", "then", "do", "else", "!", "if", "elif", "while", "until"))  # the next word is again in command position
# bash's `time` reserved word keeps command position too, and so do its options `-p` then `--`, or `--` alone
# (observed in bash 5.3: `time -p -- [[ a > b ]]` is a conditional; `time -p -p` and `time -x` run a command)
_TIME_OPTS = {1: {"-p": 2, "--": 0}, 2: {"--": 0}}  # time state -> option word -> next time state
# Prefix commands that run the command named after them: name -> (options that take a SEPARATE argument,
# count of leading positional operands before the command). Their other options are skipped.
PREFIX_COMMANDS = {
    "sudo": (frozenset(("-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-T", "-U", "--user", "--group", "--host",
                        "--prompt", "--close-from", "--chdir", "--role", "--type", "--command-timeout",
                        "--other-user")), 0),
    "doas": (frozenset(("-u", "-C")), 0),
    "env": (frozenset(("-u", "-C", "-S", "--unset", "--chdir", "--split-string")), 0),
    "command": (frozenset(), 0), "builtin": (frozenset(), 0), "exec": (frozenset(("-a",)), 0),
    "nohup": (frozenset(), 0), "time": (frozenset(("-f", "-o", "--format", "--output")), 0),
    "nice": (frozenset(("-n", "--adjustment")), 0),
    "ionice": (frozenset(("-c", "-n", "-p", "-P", "-u", "--class", "--classdata")), 0),
    "stdbuf": (frozenset(("-i", "-o", "-e", "--input", "--output", "--error")), 0),
    "timeout": (frozenset(("-s", "-k", "--signal", "--kill-after")), 1),
    "chroot": (frozenset(("--userspec", "--groups")), 1),
    "flock": (frozenset(("-w", "-E", "--timeout", "--conflict-exit-code")), 1),
    "xargs": (frozenset(("-a", "-d", "-E", "-I", "-L", "-n", "-P", "-s", "--arg-file", "--delimiter",
                         "--max-args", "--max-procs", "--max-chars", "--eof", "--max-lines")), 0),
}
_EXEC_WORDS = frozenset(("-exec", "-execdir", "-ok", "-okdir"))  # find: the next word is a command
_ASSIGN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=.*", re.S)


class _Exhausted(Exception):
    """Round 31: the Bash analysis spent its work budget (BASH_WORK_BUDGET); the hook then fails open."""


class _Budget:
    """Round 31: a counter of Bash analysis steps (tokenizer iterations, heredoc lines, tokens and units walked),
    shared by one evaluation and its nested shell -c analyses; spend() raises _Exhausted when it runs out."""
    __slots__ = ("left",)

    def __init__(self, steps):
        self.left = steps

    def spend(self, steps):
        self.left -= steps
        if self.left < 0:
            raise _Exhausted()


class _Tokens:
    """Round 31: what _tokenize() returns. streams: the token streams (as _shell_tokens() returns them, the
    top-level stream last); poss: per stream, a (start, end) raw offset pair per token ((-1, -1) for a separator
    the tokenizer inserts itself); spans: per stream, the raw (start, end) of its frame; heredocs: (operator
    offset, body start, body end) per here-document body; comments: the raw (start, end) of each comment."""
    __slots__ = ("streams", "poss", "spans", "heredocs", "comments")

    def __init__(self, streams, poss, spans, heredocs, comments):
        self.streams, self.poss, self.spans, self.heredocs, self.comments = streams, poss, spans, heredocs, comments


_HD_SPECIAL_RE = re.compile(r"\\|\$\(|`")  # round 31: what runs in an unquoted here-document body
_PLAIN_RUN_RE = re.compile(r"[^\s\\'\"`$<>()|&;#]+")  # round 31: word characters with no shell meaning
_PLAIN_SPECIAL = frozenset("\\'\"`$<>()|&;#")
_DQ_RUN_RE = re.compile(r'[^"\\`$]+')  # round 31: double-quoted text with no shell meaning
_BLANK_RUN_RE = re.compile(r"[^\S\n]+")  # round 31: whitespace other than a newline
# round 31: the words flush() must inspect (every other word takes the fast path)
_FLUSH_WORDS = frozenset(("case", "esac", "[[", "for", "function", "time")) | _CMD_PREFIX_WORDS
# round 31: one SIMPLE token after optional blanks, as the tokenizer's regex fast path takes it: a plain word (ended
# by whitespace, an operator, or the end), `>>`, `>|`, or `>` (not `>&`), a plain `<`, or a separator; group 5
# takes any other character, which ends the run (so a finditer from any offset never skips text)
_FAST_TOK_RE = re.compile(r"[^\S\n]*(?:([^\s\\'\"`$<>()|&;#]+)(?=[\s;|&<>()]|\Z)|(>>|>\||>(?!&))|(<(?![<>(&]))"
                          r"|([;|\n]|&(?!>))|(.))", re.S)


def _frame(kind, cmdpos, start, arith_cmd=False):
    """A tokenizer frame (see _tokenize): [kind, toks, word chars, has word, in double quotes, paren depth, open
    cases, word quoted, command position, inside [[, function header state, time state, arithmetic command or
    `for` seen, word start (round 31), token positions (round 31), pending here-documents (round 31), awaiting
    a here-document delimiter (round 31), frame start (round 31)]."""
    return [kind, [], [], False, False, 0, 0, False, cmdpos, False, 0, 0, arith_cmd, 0, [], [], None, start]


def _shell_tokens(cmd):
    """The token STREAMS of `cmd` (see _tokenize), or None when it cannot be parsed."""
    t = _tokenize(cmd)
    return None if t is None else t.streams


def _tokenize(cmd, budget=None):
    """Lexical shell token STREAMS of `cmd` in ONE linear pass: a list holding the top-level stream and one
    stream per `$(...)` or backtick substitution, each a list of ("w", dequoted word), ("s",) for a separator
    (`;`, `|`, `&`, `(`, `)`, a newline), ("r", dup) for an output redirection (dup True for the `>&`
    form, whose operand may name a descriptor), and ("i",) for an input redirection (`<`, `<<<`, `<<`, `<<-`,
    `<&`; the next word is its operand). `<>` is an output redirection; the `<` of a `<(` process substitution
    is dropped. Inside a `[[ ... ]]` conditional (a `[[` word in command position up to its closing `]]`),
    everything is operand text: `<` and `>` are string comparisons and `(`, `)`, `|`, `&`, and `;` are
    grouping, logical, or regex operand text, all emitting nothing and splitting no word, a newline only
    ends a word, and a `#` is word text, never a comment (a `<(` or `>(` there opens a process substitution,
    a frame like a `$(...)`; quoting and substitutions work as outside). The conditional closes only at a
    `]]` that starts a word (after unquoted whitespace) and is followed by whitespace, `;`, `&`, `|`, `<`,
    `>`, `)`, the end, or (in a backtick frame) that frame's closing backtick, never at a `]]` glued to
    preceding text; a `[[` word ended by a metacharacter
    (`[[(-f x) ]]`) opens it with that metacharacter as conditional text; the closing `]]` word is
    followed by a separator (bash takes a `do`, `then`, or `{` after it with or without a `;` first). A
    conditional still open when its frame ends (at the end of input, at a closing backtick, or at an
    unterminated quote; a `)` inside it is operand text, so a `$(...)` never closes over it) makes the whole
    result None, never a silent pass (round 22: there is no loose re-read, so a grouping `)` glued to `]]`,
    `[[ ( -n x )]]`, which bash does close, leaves the conditional open here and the command unparseable). This
    never applies to `[ ... ]` or `test`, where an unquoted `>` is a real redirection. A substitution, unquoted or inside double quotes, is its own
    frame with its own quoting context (as in date_spans()), its body a separate stream, and it leaves a `$`
    placeholder in the enclosing word. Single quotes and $'...' make everything literal; double quotes make
    every operator literal except a substitution opener; a backslash escapes the next character; an unquoted
    `#` at a word start comments out the rest of its line. Inside a `$(...)`, an unquoted `case` word in
    command position opens a case construct until its unquoted `esac` in command position, and while one is
    open a `)` is a pattern terminator (a separator), not the end of the substitution. Command position is
    the first word of the substitution or the first word after a separator, `(`, `)`, or a reserved word
    (`{`, `then`, `do`, `else`, `!`, `if`, `elif`, `while`, `until`) that is itself in command position, so a
    `case` argument (`rg case <file>`, `rg then case <file>`) is not a keyword. The `time` reserved word in
    command position, and after it `-p` then `--` or `--` alone, also keep command position (`if time [[`).
    A function header restores command position at its body: after `function NAME` (with or without `()`),
    the body's opening `{` keeps it (a `(` or `()` is a separator anyway, as is the newline of `NAME ()`
    followed by `{` on the next line), and after `function NAME` with no `()` the body word is itself in
    command position (bash takes any compound command there: `((`, `[[`, `(`, `{`, `if`, `while`, `until`,
    `for`, `case`), so the header words themselves are never command words. A `((` in
    command position (or right after a `for` in command position) opens an arithmetic command, and `$((` an
    arithmetic expansion, each through its matching `))` (nested parentheses counted, adapted from the
    block-truncated-reads hook's _arith_end): it contributes a `$` placeholder word to the enclosing stream,
    a `(` ends any word before it first (so `if((`, `while((`, `until((`, `elif((`, and `!((` keep their
    reserved word's command position), a closed arithmetic command (a `for ((...))` header or a plain
    `((...))`, not a `$((` expansion) is followed by a separator (bash takes its `do`, `then`, or `{` with or
    without a `;` first, so the body stays in command position), and inside it `<`, `>`, `<<`, `>>`, separators, and `#` are arithmetic text that emits nothing, while a
    `$(...)` or backtick substitution inside it is still its own scanned stream. A `)` at the arithmetic's
    own level not followed by `)` (bash then re-reads the text as nested groups) returns None. Returns None
    for an unterminated quote, substitution, arithmetic, or `[[` conditional (in any frame).
    Round 31: the pass also records where each token lies in `cmd` (the raw offsets the analysis attributes
    literals and writes by), each substitution frame's span, each comment, and each here-document body: at
    the newline ending a line that holds `<<` or `<<-` operators, their bodies run through their delimiter
    lines, and they are never tokenized as commands: when every such delimiter was quoted (no expansion in the
    body), or the bodies hold no `$(` and no backtick, they are skipped, and otherwise only the substitutions
    in them are tokenized (body mode, see bodies()), as bash expands them. Runs of characters with no shell
    meaning are consumed
    in one step. With `budget` (a _Budget) the pass raises _Exhausted once it has taken that many steps."""
    # frame: see _frame(); kind in "top", "$(", "`", or "((" (an arithmetic command or expansion, whose own text
    # emits no tokens and is never appended as a stream; paren depth counts its nested parentheses)
    streams, poss, spans, heredocs, comments = [], [], [], [], []
    stack, i, n = [_frame("top", True, 0)], 0, len(cmd)
    limit = budget.left + 1 if budget is not None else 1 << 62
    steps = 0

    def flush(f, closing=False):  # closing: this word is the `]]` that closes a [[ ... ]] conditional
        if f[3]:
            w = "".join(f[2])
            f[1].append(("w", w))
            f[14].append((f[13], i))
            if f[16] is not None:  # round 31: the delimiter word of a pending here-document
                f[15].append((w, f[16][0], f[7], f[16][1]))
                f[16] = None
            if not f[9] and not f[10] and not f[11] and w not in _FLUSH_WORDS:
                f[8] = f[12] = False  # round 31: the common case, an ordinary word, decided without the chain below
                f[2], f[3], f[7] = [], False, False
                return
            nonlocal steps
            steps += 2  # the full chain below
            if f[0] == "$(" and not f[7] and f[8]:
                if w == "case":
                    f[6] += 1
                elif w == "esac" and f[6]:
                    f[6] -= 1
            closed = False  # this word is the `]]` that closes a [[ ... ]] conditional command
            if f[9]:  # the main loop decides where the conditional closes (round 20), never the word alone
                closed = closing
                f[9] = False if closing else f[9]
            elif not f[7] and f[8] and w == "[[":
                f[9] = i  # where it opened (never 0: the `[[` precedes i), so the flag is always truthy
            pos = f[8] and not f[7]
            f[12] = pos and w == "for"  # `for ((` opens an arithmetic header
            if f[10] == 1:  # the function name: the body, a compound command, follows in command position, so
                f[8], f[10] = True, 2  # a `((`, `[[`, `(`, `for ((`, or `case` there opens its construct
            elif f[10] == 2:  # the function body opens: `{`, `if`, `while`, `until` keep command position
                f[8], f[10] = pos and w in _CMD_PREFIX_WORDS, 0
            elif pos and w == "function":
                f[8], f[10], f[11] = False, 1, 0
            elif pos and w in _TIME_OPTS.get(f[11], ()):
                f[11] = _TIME_OPTS[f[11]][w]  # an option of the time reserved word; the position is kept
            else:
                f[11] = 1 if pos and w == "time" else 0
                f[8] = pos and (w in _CMD_PREFIX_WORDS or w == "time")  # only a reserved word keeps the position
            if closed:  # as after a closed arithmetic command: the `do`, `then`, or `{` after `]]`, with or
                sep(f, i, True)  # without a `;` or newline first, is again in command position, here and in the stream
        f[2], f[3], f[7] = [], False, False

    def sep(f, at, synthetic=False):
        f[1].append(("s",))
        f[14].append((at, at) if synthetic else (at, at + 1))  # round 31: an inserted separator is zero-width
        f[8], f[10], f[11], f[12] = True, 0, 0, False
        f[16] = None  # a separator before a here-document's delimiter word: bash rejects it; no body

    def bodies(f, start):
        """Round 31: record the bodies of the here-documents pending in frame `f`, which start at `start` (just
        past the newline ending their operator's line), each through its delimiter line (tabs stripped first
        for `<<-`), or to the end of input when none follows. When EVERY pending delimiter was quoted the bodies
        are literal text in bash (no expansion), and when the bodies hold no `$(` and no backtick nothing in them
        can run a command, so in either case tokenizing resumes after the last delimiter line; otherwise the
        bodies are scanned in BODY MODE (an "hd" frame through their end), where only a `$(`, `$((`, or backtick
        substitution is tokenized (as its own stream) and a backslash escapes the next character, as bash expands
        an unquoted here-document. Returns where to resume."""
        nonlocal steps
        at, skip = start, all(quoted for _w, _strip, quoted, _op in f[15])
        for delim, strip, _quoted, op in f[15]:
            body = at
            while True:
                steps += 1
                e = cmd.find("\n", at)
                end = n if e < 0 else e
                line = cmd[at:end]
                if (line.lstrip("\t") if strip else line) == delim:
                    heredocs.append((op, body, at))
                    at = n if e < 0 else e + 1
                    break
                if e < 0:
                    heredocs.append((op, body, n))
                    at = n
                    break
                at = e + 1
        f[15] = []
        if steps >= limit:
            raise _Exhausted()
        if skip or ("$(" not in cmd[start:at] and "`" not in cmd[start:at]):
            return at
        body = _frame("hd", False, start)
        body[5] = at  # the end of the bodies
        stack.append(body)
        return start

    while i < n:
        steps += 3  # a character-loop step costs about three times a token taken by the regex fast path
        if steps >= limit:
            raise _Exhausted()
        f, c = stack[-1], cmd[i]
        if f[0] == "hd":  # round 31: here-document body mode (see bodies()): only substitutions run
            m = _HD_SPECIAL_RE.search(cmd, i, f[5])
            if m is None:
                stack.pop()
                i = f[5]
                continue
            i = m.start()
            c = cmd[i]
            if c == "\\":
                i += 2
                continue
        # round 31: runs of characters with no shell meaning are consumed whole (identical results, far fewer steps)
        if f[4]:
            if c not in '"\\`$':
                m = _DQ_RUN_RE.match(cmd, i)
                f[2].append(m.group())
                i = m.end()
                continue
        elif not f[9] and f[0] not in ("((", "hd"):
            if not f[3] and f[16] is None and not f[10] and not f[11]:
                # round 31: a run of SIMPLE tokens (a plain word that is none of _FLUSH_WORDS, `;`, `|`, `&`, a
                # newline unless a here-document is pending, `>`, `>>`, `>|`, or a plain `<`, each after optional
                # blanks) is tokenized by anchored regex matches, exactly as the character loop below would
                toks, tpos, f8, f12, pend, at = f[1], f[14], f[8], f[12], f[15], i
                for t in _FAST_TOK_RE.finditer(cmd, i):  # every position matches: a group 5 match stops the run
                    g = t.lastindex
                    if g == 1:
                        w = t.group(1)
                        if w in _FLUSH_WORDS:
                            break
                        toks.append(("w", w))
                        tpos.append((t.start(1), t.end()))
                        f8 = f12 = False
                    elif g == 4:
                        a = t.start(4)
                        if pend and cmd[a] == "\n":
                            break
                        toks.append(("s",))
                        tpos.append((a, a + 1))
                        f8, f12 = True, False
                    elif g == 2:
                        toks.append(("r", False))
                        tpos.append((t.start(2), t.end()))
                    elif g == 3:
                        a = t.start(3)
                        toks.append(("i",))
                        tpos.append((a, a + 1))
                    else:
                        break
                    steps += 1
                    at = t.end()
                if at != i:  # some token was taken: resume after it (else the loop below handles cmd[i])
                    f[8], f[12], i = f8, f12, at
                    if steps >= limit:
                        raise _Exhausted()
                    continue
            if c not in _PLAIN_SPECIAL:
                if c.isspace():
                    if c != "\n":
                        flush(f)
                        i = _BLANK_RUN_RE.match(cmd, i).end()
                        continue
                else:
                    m = _PLAIN_RUN_RE.match(cmd, i)
                    if not f[3]:
                        f[13] = i
                    f[2].append(m.group())
                    f[3] = True
                    i = m.end()
                    continue
        if c == "\\":
            if i + 1 < n and cmd[i + 1] != "\n":
                if not f[3]:
                    f[13] = i
                f[2].append(cmd[i + 1])
                f[3] = f[7] = True
            i += 2
            continue
        if c == "`" and f[0] == "`":
            flush(f)
            if f[9]:  # a conditional still open when its backtick frame closes: unparseable (round 22)
                return None
            streams.append(f[1])
            poss.append(f[14])
            spans.append((f[17], i + 1))
            stack.pop()
            i += 1
            continue
        if c == "$" and cmd.startswith("$((", i):  # an arithmetic expansion: its value joins the enclosing word
            if not f[3]:
                f[13] = i
            f[2].append("$")
            f[3] = f[7] = True
            steps += STREAM_COST
            stack.append(_frame("((", False, i))
            i += 3
            continue
        if c == "`" or (c == "$" and cmd.startswith("$(", i)):
            if not f[3]:
                f[13] = i
            f[2].append("$")  # the substitution's value, in the enclosing word
            f[3] = f[7] = True
            steps += STREAM_COST
            stack.append(_frame(c if c == "`" else "$(", True, i))
            i += 1 if c == "`" else 2
            continue
        if f[4]:
            if c == '"':
                f[4] = False
            else:
                f[2].append(c)
            i += 1
            continue
        if c == "'" or (c == "$" and cmd.startswith("$'", i)):
            if not f[3]:
                f[13] = i
            if c == "'":  # round 31: the quoted text is taken whole, not one character per step
                j = cmd.find("'", i + 1)
                if j < 0:
                    return None
                f[2].append(cmd[i + 1:j])
            else:  # $'...': a backslash escapes the next character (it is kept, the backslash dropped)
                i += 1
                j = i + 1
                while True:
                    steps += 1
                    m = _DS_ANSI_RE.search(cmd, j)
                    if m is None:
                        return None
                    k = m.start()
                    f[2].append(cmd[j:k])
                    if cmd[k] == "'":
                        j = k
                        break
                    if k + 1 >= n:
                        return None
                    f[2].append(cmd[k + 1])
                    j = k + 2
            f[3], f[7], i = True, True, j + 1
            continue
        if c == '"':
            if not f[3]:
                f[13] = i
            f[3] = f[4] = f[7] = True
            i += 1
            continue
        if f[9]:  # inside [[ ... ]] (round 20): everything up to the closing `]]` is operand text
            if c in "<>" and cmd.startswith("(", i + 1):  # a process substitution: its body is its own
                flush(f)  # scanned stream, as a `$(...)` is, and its value joins the word
                f[13] = i
                f[2].append("$")
                f[3] = f[7] = True
                steps += STREAM_COST
                stack.append(_frame("$(", True, i))
                i += 2
                continue
            if cmd.startswith("]]", i) and (i + 2 >= n or cmd[i + 2].isspace() or cmd[i + 2] in ";&|<>)" or (
                    cmd[i + 2] == "`" and f[0] == "`")) and not f[3] and cmd[i - 1].isspace():
                flush(f)  # the `]]` word closes the conditional: it starts a word (after unquoted whitespace;
                f[2], f[3], f[7], f[13] = ["]", "]"], True, False, i  # a glued `)]]` never closes, round 22) and
                flush(f, True)  # a metacharacter, the end, or the closing backtick of this backtick frame (round
                i += 2  # 23) follows it; the next word is again in command position, with or without a `;` first
                continue
            if c.isspace():  # a newline too: bash takes one only after `[[`, `&&`, `||`, `(`, or `!`, never as a
                flush(f)  # separator
            elif c not in "<>()|&;":  # comparison, grouping, logical, and regex operand text emit nothing: never
                if not f[3]:  # a redirection, a separator, or a command boundary (a `;`, a lone `&`, or a lone
                    f[13] = i  # `|` outside a regex operand is a bash syntax error here); a `#` is word text
                f[2].append(c)
                f[3] = True
            i += 1
            continue
        if f[0] == "((":  # arithmetic text: `<`, `>`, `<<`, `>>`, separators, and `#` are operators, not shell syntax
            if c == "(":
                f[5] += 1
            elif c == ")":
                if f[5]:
                    f[5] -= 1
                elif cmd.startswith("))", i):
                    stack.pop()  # an arithmetic frame is not an executable stream
                    i += 2
                    if f[12]:  # a closed arithmetic COMMAND (a `for ((...))` header or a plain `((...))`, never
                        flush(stack[-1])  # a `$((` expansion, whose value joins the word): the `do`, `then`,
                        sep(stack[-1], i, True)  # or `{` after it, with or without a `;` or newline first, is again in
                    continue  # command position, in the tokenizer state and, through a separator, in the stream
                else:  # bash then re-reads the `((` as nested groups: not emulated, fails toward writing
                    return None
            i += 1
            continue
        if c in "<>()|&":
            flush(f)  # a metacharacter ends the word before it (`if((`, `while((`, `!((` first end their reserved
            if f[9]:  # word); a `[[` ended so opens the conditional (round 21): re-read this character as
                continue  # conditional text, never as a separator or a redirection (`[[(-f x) ]]`)
        if c == "(" and cmd.startswith("((", i) and (f[8] or f[12]):
            if not f[3]:
                f[13] = i
            f[2].append("$")  # an arithmetic command (or a `for ((` header): one opaque word
            f[3] = f[7] = True
            steps += STREAM_COST
            stack.append(_frame("((", False, i, True))
            i += 2
            continue
        if c == "#" and not f[3]:  # (inside [[ ... ]] a `#` never reaches here: it is operand text)
            nl = cmd.find("\n", i)
            comments.append((i, n if nl < 0 else nl))  # round 31: recorded, so a literal there feeds no write
            i = n if nl < 0 else nl
            continue
        if c == ">" or (c == "&" and cmd.startswith("&>", i)):
            flush(f)
            at, amp = i, c == "&"
            i += 2 if amp else 1
            dup = False
            if i < n and cmd[i] in ">|":
                i += 1
            elif not amp and i < n and cmd[i] == "&":
                i += 1
                dup = True
            f[1].append(("r", dup))
            f[14].append((at, i))
            continue
        if c == ")" and f[0] == "$(" and f[5] == 0:
            flush(f)
            if f[6]:  # a case pattern terminator inside the substitution, not its closing paren
                sep(f, i)
                i += 1
                continue
            streams.append(f[1])
            poss.append(f[14])
            spans.append((f[17], i + 1))
            stack.pop()
            i += 1
            continue
        if c in "()":
            if f[0] == "$(":
                f[5] += 1 if c == "(" else -1
            flush(f)
            sep(f, i)
            i += 1
            continue
        if c in _SHELL_SEPARATORS:
            flush(f)
            sep(f, i)
            if c == "\n" and f[15]:  # round 31: here-document bodies start after this newline
                i = bodies(f, i + 1)
                continue
            i += 1
            continue
        if c == "<":
            flush(f)
            if cmd.startswith("<>", i) or cmd.startswith("<(", i):  # `<>` is read-write, an output
                i += 1  # redirection (the `>` branch emits it); `<(` opens a process substitution group
                continue
            at = i
            heredoc = cmd.startswith("<<", i) and not cmd.startswith("<<<", i)
            strip = cmd.startswith("<<-", i)
            i += 3 if cmd.startswith(("<<<", "<<-"), i) else 2 if cmd.startswith(("<<", "<&"), i) else 1
            f[1].append(("i",))
            f[14].append((at, i))
            if heredoc:
                f[16] = (strip, at)  # round 31: the next word is the here-document's delimiter
            continue
        if c.isspace():
            flush(f)
            i += 1
            continue
        if not f[3]:
            f[13] = i
        f[2].append(c)
        f[3] = True
        i += 1
    if budget is not None:
        budget.spend(steps)
    if len(stack) > 1 and stack[-1][0] == "hd":  # a here-document body that ran to the end of the input
        stack.pop()
    if len(stack) > 1 or stack[0][4]:
        return None
    flush(stack[0])
    if stack[0][9]:  # a conditional still open at the end of input: unparseable, never a silent pass
        return None
    streams.append(stack[0][1])
    poss.append(stack[0][14])
    spans.append((0, n))
    return _Tokens(streams, poss, spans, heredocs, comments)


def bash_writes(cmd):
    """True when the command text shows a WRITE indicator (lexical; see the docstring) in ANY of its token
    streams (the top level or any substitution body, each with independent state): an unquoted output
    redirection (`>`, `>>`, `>|`, `&>`, `N>`, `<>`) whose target is not /dev/null and not a `>&` descriptor
    duplication; or, in command position of any simple command, a word (its basename) among WRITE_COMMANDS,
    or sed or perl followed in the same simple command by an in-place option (`-i`, a letter-and-digit short cluster
    holding i such as `-pi`, `-i.bak`, or `-0777pi`, or `--in-place`). An unterminated quote or
    substitution, or a redirection with no target, fails toward writing (True)."""
    streams = _shell_tokens(cmd)
    return True if streams is None else any(_stream_writes(toks) for toks in streams)


def bash_store_write(cmd, cwd=None):
    """True when a write in the command TARGETS a store path (round 24): a redirection target, a tee operand, a
    sed -i or perl -i operand, a cp, mv, or install destination (the -t or --target-directory argument, else the
    last operand), a dd of= operand, or a truncate, ed, or ex operand, each tested with the same lexical store
    path token rule as names_store_path(). Round 26: a PARSED target that is a store path establishes the store
    write by itself (an attached `cp -t<store dir>` spelling included, which the raw command text does not show
    as a standalone store path token); only a command that cannot be parsed, or a write whose target is unknown
    (a redirection with no operand, a `$` target, an xargs-run write), falls back to the raw test and counts as
    a store write when the command text names a store path anywhere (the fail-toward-writing direction). Round
    31: a relative target is resolved against `cwd` and any cd before it, and a literal shell -c string's writes
    count (see _analyze); a command that spends BASH_WORK_BUDGET is reported as no store write (fail open)."""
    start = frozenset((os.path.normpath(cwd) if isinstance(cwd, str) and os.path.isabs(cwd) else None,))
    try:
        return _analyze(cmd, start, _store_ctx(), _Budget(BASH_WORK_BUDGET)).store
    except _Exhausted:
        return False


# Round 26: how each modeled write command's options take arguments, so an option's argument (a sed script
# file, a truncate reference file, an install mode) is consumed before the destination operands are collected
# and is never read as a write target. Per command: "req", short letters taking a REQUIRED argument (the rest of
# the cluster, else the next word); "opt", short letters taking an OPTIONAL argument only when attached (the
# rest of the cluster, which they end); "run", short letters followed by an attached run the regex matches
# (perl's -0777, -l0, -C), after which the cluster continues; "long", long options taking a REQUIRED argument
# (`--opt value` or `--opt=value`; any other long option takes none, or only an attached `=value`); "target",
# the letters and long options whose argument IS a write target; "script", the letters and long options that
# supply the program, or None for a command with no program operand (a sed or perl given none of them takes its
# FIRST operand as the program, not as a file); "text" (round 27), the letters and long options whose argument
# is SCRIPT TEXT the tool runs (a sed expression, an ex command, a perl program), and "text_prog", whether the
# dropped program operand is script text too (sed's, never perl's, which names a program FILE): script text
# that names a store path makes the write's target UNKNOWN (a sed `w` command or `s///w` flag, or an ex `w!`,
# can write that path), while an option argument that names a FILE (sed -f, truncate -r, perl -I) stays
# consumed; "stop" (round 27), option parsing ends at the FIRST operand, as the real tool does, so every later
# word is an operand; "all" (round 30), the letters and long options under which EVERY operand (and every
# target-directory argument) is written: mv's --exchange swaps each source with its destination, and install's
# -d or --directory creates every operand as a directory (an existing one has its mode set); "optsep" (round
# 30), long options whose optional argument one implementation takes only attached (`--opt=value`: GNU
# install's --context) and another also takes as the SEPARATE next word when that word is `-` or does not
# start with `-` (uutils install 0.8.0, this host's install), so that word is kept as an operand AND the
# destination is also computed without it (the union of both readings); "exit" (round 30), long options on
# which the tool exits at once, touching no file and running no program, when they are met while options are
# still parsed (perl's --help and --version; an attached `=value` is unrecognized; round 31: also --help and
# --version of cp, mv, install, tee, truncate, ed, and sed, which permute, so either stops them wherever it
# stands before `--`; "exit_eq", the tool also rejects an attached `=value` before writing anything, observed
# on this host with GNU cp and mv 9.7, GNU sed 4.9, GNU ed 1.22.4, and uutils install, tee, and truncate 0.8.0;
# ex is not given them, since vim opens a -w script-out file before it reaches --version). Observed on this host
# (round 27): perl 5.40.1 stops at its first operand (`perl -pi -e
# CODE X -I S` edits X, fails to open `-I`, and edits S); GNU sed 4.9, GNU cp and mv 9.7, GNU ed 1.22.4, vim 9.1
# as ex, and uutils 0.8.0 install, truncate, and tee all permute (an option after an operand is still an
# option); GNU sed, cp, and mv stop at the first operand only under POSIXLY_CORRECT (not modeled; disclosed).
_EXITS = ("--help", "--version")  # round 31: long options on which the tool exits at once, writing nothing
_OPTION_SPECS = {
    "cp": {"req": "St", "long": ("--suffix", "--target-directory", "--sparse", "--no-preserve"),
           "target": ("t", "--target-directory"), "exit": _EXITS, "exit_eq": True},
    "mv": {"req": "St", "long": ("--suffix", "--target-directory"), "target": ("t", "--target-directory"),
           "all": ("--exchange",), "exit": _EXITS, "exit_eq": True},
    "install": {"req": "mogSt", "long": ("--mode", "--owner", "--group", "--suffix", "--target-directory",
                                         "--strip-program"), "target": ("t", "--target-directory"),
                "all": ("d", "--directory"), "optsep": ("--context",), "exit": _EXITS, "exit_eq": True},
    "tee": {"exit": _EXITS, "exit_eq": True},
    "truncate": {"req": "rs", "long": ("--reference", "--size"), "exit": _EXITS, "exit_eq": True},
    "ed": {"req": "p", "long": ("--prompt",), "exit": _EXITS, "exit_eq": True},
    # ex (vim in Ex mode): -i names the viminfo file and -w/-W a script-out file, both written, so both are targets
    "ex": {"req": "cSuUiTtwW", "long": ("--cmd", "--startuptime", "--log"),
           "target": ("i", "w", "W", "--startuptime", "--log"), "text": ("c", "--cmd")},
    "sed": {"req": "efl", "opt": "i", "long": ("--expression", "--file", "--line-length"),
            "script": ("e", "f", "--expression", "--file"), "text": ("e", "--expression"), "text_prog": True,
            "exit": _EXITS, "exit_eq": True},
    "perl": {"req": "eEIMm", "opt": "ixDF",
             "run": {"0": re.compile(r"[xX][0-9A-Fa-f]*|[0-7]*"), "l": re.compile(r"[0-7]*"),
                     "C": re.compile(r"[0-9]+|[IOESioDALa]*"), "d": re.compile(r"t?(?::.*)?"),
                     "V": re.compile(r"(?::.*)?")},
             "script": ("e", "E"), "text": ("e", "E"), "stop": True, "exit": _EXITS},
}
_DEST_COMMANDS = frozenset(("cp", "mv", "install"))  # the destination is the -t argument, else the last operand

# Round 29: every long option each modeled command accepts, so an abbreviated spelling resolves as the real tool
# resolves it (a MISS before: `sed --expr='s/x/<future>/w <store file>' log` and `sed --in s/x/<future>/ <store
# file>` wrote the store unseen). Observed on this host: GNU sed 4.9, GNU cp and mv 9.7, and GNU ed 1.22.4
# (getopt_long, or ed's own parser) and uutils 0.8.0 install, tee, and truncate (clap, inferring long names)
# accept any UNAMBIGUOUS prefix, a prefix of several aliases of ONE option counting once (`sed --z`, `sed
# --q`), and reject an ambiguous one (`sed --f`: --file or --follow-symlinks; `cp --s`; `ed --s`); vim 9.1 as ex
# accepts only an exact long option (`--cm` and `--cmdx` are errors, and `--CMD` is not --cmd); perl 5.40.1
# accepts exactly two, --help and --version, each only exact and without `=value` (round 30: `perl --vers`,
# `perl --version=1`, and `perl --in` are unrecognized switches), and exits at once on either. The sets are the
# tools' --help lists plus the hidden spellings observed (sed --binary and --zero-terminated, cp --path) and
# ex's modeled --startuptime and --log. Round 30: ed's --script is its -s, not an alias of --quiet (-q).
# Round 30 swept every long option here, and each modeled command's short letters, for separate-word
# consumption against the real tools on this host (the tool run with the option followed by a word, and
# whether that word was then used as an operand); every recorded arity matched except uutils install's
# --context, now modeled ("optsep").
_LONG_OPTIONS = {
    "sed": ("--binary", "--debug", "--expression", "--file", "--follow-symlinks", "--help", "--in-place",
            "--line-length", "--null-data", "--posix", "--quiet", "--regexp-extended", "--sandbox", "--separate",
            "--silent", "--unbuffered", "--version", "--zero-terminated"),
    "cp": ("--archive", "--attributes-only", "--backup", "--context", "--copy-contents", "--debug", "--dereference",
           "--force", "--help", "--interactive", "--keep-directory-symlink", "--link", "--no-clobber",
           "--no-dereference", "--no-preserve", "--no-target-directory", "--one-file-system", "--parents", "--path",
           "--preserve", "--recursive", "--reflink", "--remove-destination", "--sparse", "--strip-trailing-slashes",
           "--suffix", "--symbolic-link", "--target-directory", "--update", "--verbose", "--version"),
    "mv": ("--backup", "--context", "--debug", "--exchange", "--force", "--help", "--interactive", "--no-clobber",
           "--no-copy", "--no-target-directory", "--strip-trailing-slashes", "--suffix", "--target-directory",
           "--update", "--verbose", "--version"),
    "install": ("--backup", "--compare", "--context", "--directory", "--group", "--help", "--mode",
                "--no-target-directory", "--owner", "--preserve-context", "--preserve-timestamps", "--strip",
                "--strip-program", "--suffix", "--target-directory", "--unprivileged", "--verbose", "--version"),
    "tee": ("--append", "--help", "--ignore-interrupts", "--output-error", "--version"),
    "truncate": ("--help", "--io-blocks", "--no-create", "--reference", "--size", "--version"),
    "ed": ("--extended-regexp", "--help", "--loose-exit-status", "--prompt", "--quiet", "--restricted", "--script",
           "--silent", "--strip-trailing-cr", "--traditional", "--unsafe-names", "--verbose", "--version"),
    "ex": ("--clean", "--cmd", "--help", "--log", "--noplugin", "--not-a-term", "--startuptime", "--ttyfail",
           "--version"),
    "perl": ("--help", "--version"),
}
_LONG_ALIASES = {  # (command, alias): the option it names, so a prefix of both is not ambiguous
    ("sed", "--silent"): "--quiet", ("sed", "--zero-terminated"): "--null-data", ("cp", "--path"): "--parents",
    ("ed", "--silent"): "--quiet"}
_ABBREV_COMMANDS = frozenset(("sed", "cp", "mv", "install", "tee", "truncate", "ed"))
_STRICT_LONG_COMMANDS = frozenset(("sed", "perl", "ex"))  # an unresolvable `--` word: the target is UNKNOWN


def _resolve_long(kind, name):
    """Round 29: the long option `name` (a `--` word up to any `=`) stands for on command `kind`, or None when it
    is UNRECOGNIZED or AMBIGUOUS. An exact known name is itself; on a command whose tool accepts abbreviations
    (_ABBREV_COMMANDS) a prefix of exactly one known option, or of several aliases of one option, is that
    option (its canonical name when it is an alias's); any other word is None. A command with no recorded set
    returns `name` unchanged."""
    known = _LONG_OPTIONS.get(kind)
    if known is None:
        return name
    if name in known:
        return _LONG_ALIASES.get((kind, name), name)
    if kind not in _ABBREV_COMMANDS or len(name) < 3:
        return None
    options = {_LONG_ALIASES.get((kind, n), n) for n in known if n.startswith(name)}
    return options.pop() if len(options) == 1 else None


def _is_inplace_opt(kind, w):
    """Round 29: True when the argument word `w` of sed or perl `kind` is an in-place option: a short cluster
    _INPLACE_OPT_RE matches, or a `--` word resolving to sed's --in-place (`--in-place`, `--in`, `--in=.bak`)."""
    if w.startswith("--"):
        return _resolve_long(kind, w.partition("=")[0]) == "--in-place"
    return bool(_INPLACE_OPT_RE.match(w))

# Round 28: the GNU sed program grammar the read-only classifier accepts (observed with GNU sed 4.9). Commands
# taking no argument, commands taking an optional number, and the command-ending characters.
_SED_PLAIN = frozenset("=dDgGhHnNpPxzF")
_SED_NUMBERED = frozenset("lqQ")
_SED_S_FLAGS = frozenset("gpiImM0123456789")


def _sed_program_cannot_write(prog):
    """Round 28: True only when the sed program `prog` (its -e pieces joined by newlines, as sed joins them) is
    parsed with CONFIDENCE and holds no command that can write a file or run one: no `w` or `W` command, no `e`
    command, and no `w` or `e` flag on an `s` command (GNU sed opens a `w` file while parsing, so any `w` counts,
    wherever the program later fails). Anything this conservative parser does not recognize with confidence
    returns False (the caller then keeps the round-27 UNKNOWN target): a `$` anywhere (shell text the tokenizer
    left unexpanded, or a substitution placeholder, could expand into a command), a backslash or newline inside
    a bracket expression, a newline inside a regex, an a, i, or c text line ending in a backslash or an
    `a\\text` one-liner, a label holding anything unusual, a second `!`, unbalanced braces, an unknown command
    or `s` flag, or trailing characters after a command. Grammar followed (observed with GNU sed 4.9): an
    address is a number (with `~step`), `/re/`, or `\\cREc` (`$` is refused, above), a regex address taking I or
    M flags, and an optional second address after `,` (also `+N` or `~N`), then optional spaces and one `!`;
    in a regex a backslash escapes the next character and a bracket expression protects the delimiter; the `s`
    replacement and the `y` operands are delimited by backslash escapes only; `s` flags may be separated by
    spaces; a, i, and c text, and r, R, w, and W file names run to the end of the line; `b`, `t`, `T`, `:`, and
    `v` labels end at `;` or whitespace; `#` starts a comment to the end of the line; a command ends at `;`, a
    newline, `}`, `#`, or the end."""
    if "$" in prog:
        return False
    n, i, depth = len(prog), 0, 0

    def spaces(j):
        while j < n and prog[j] in " \t":
            j += 1
        return j

    def regex_end(j, delim):
        """Index just past the closing `delim` of a regex starting at j, or -1 (not parsed with confidence)."""
        while j < n:
            c = prog[j]
            if c == "\n":
                return -1
            if c == "\\":
                if j + 1 >= n or prog[j + 1] == "\n":
                    return -1
                j += 2
            elif c == "[":
                k = j + 1
                if k < n and prog[k] == "^":
                    k += 1
                if k < n and prog[k] == "]":
                    k += 1
                while k < n and prog[k] != "]":
                    if prog[k] in "\\\n":
                        return -1
                    if prog[k] == "[" and k + 1 < n and prog[k + 1] in ":=.":
                        close = prog.find(prog[k + 1] + "]", k + 2)
                        if close < 0 or "\n" in prog[k:close] or "\\" in prog[k:close]:
                            return -1
                        k = close + 2
                    else:
                        k += 1
                if k >= n:
                    return -1
                j = k + 1
            elif c == delim:
                return j + 1
            else:
                j += 1
        return -1

    def plain_end(j, delim):
        """Index just past the closing `delim` of an s replacement or y operand (backslash escapes only)."""
        while j < n:
            c = prog[j]
            if c == "\\":
                if j + 1 >= n:
                    return -1
                j += 2
            elif c == delim:
                return j + 1
            elif c == "\n":
                return -1
            else:
                j += 1
        return -1

    def address(j):
        """Index past one address at j (j itself when there is none), or -1."""
        if j < n and prog[j].isdigit():
            while j < n and prog[j].isdigit():
                j += 1
            if j < n and prog[j] == "~":
                j += 1
                if not (j < n and prog[j].isdigit()):
                    return -1
                while j < n and prog[j].isdigit():
                    j += 1
            return j
        if j < n and prog[j] in "/\\":
            if prog[j] == "\\":
                if j + 1 >= n or prog[j + 1] in "\n\\":
                    return -1
                delim, j = prog[j + 1], j + 2
            else:
                delim, j = "/", j + 1
            j = regex_end(j, delim)
            while j >= 0 and j < n and prog[j] in "IM":
                j += 1
            return j
        return j

    def line_end(j):
        k = prog.find("\n", j)
        return n if k < 0 else k

    def command_end(j):
        """Index of the separator after a command (at j after optional spaces), or -1 when trailing text follows."""
        j = spaces(j)
        if j >= n or prog[j] in ";\n}#":
            return j
        return -1

    while i < n:
        i = spaces(i)
        if i >= n:
            break
        c = prog[i]
        if c in ";\n":
            i += 1
            continue
        if c == "#":
            i = line_end(i)
            continue
        if c == "}":
            if depth == 0:
                return False
            depth -= 1
            i = command_end(i + 1)
            if i < 0:
                return False
            continue
        j = address(i)
        if j < 0:
            return False
        if j > i and j < n and prog[j] == ",":
            if j + 1 < n and prog[j + 1] in "+~":
                k = j + 2
                if not (k < n and prog[k].isdigit()):
                    return False
                while k < n and prog[k].isdigit():
                    k += 1
                j = k
            else:
                k = address(j + 1)
                if k <= j + 1:
                    return False  # no second address after the `,` (a `$` one is refused above)
                j = k
        j = spaces(j)
        if j < n and prog[j] == "!":
            j = spaces(j + 1)
            if j < n and prog[j] == "!":
                return False  # GNU sed rejects multiple `!`s
        if j >= n:
            return False
        c = prog[j]
        if c in "wWe":
            return False  # a command that writes a file or runs one
        if c == "{":
            depth += 1
            i = j + 1
            continue
        if c in _SED_PLAIN:
            i = command_end(j + 1)
        elif c in _SED_NUMBERED:
            k = spaces(j + 1)
            while k < n and prog[k].isdigit():
                k += 1
            i = command_end(k)
        elif c in "btTv:":
            k = spaces(j + 1)
            while k < n and prog[k] not in " \t;\n":
                if prog[k] in "}#\\{":
                    return False  # a label holding a brace, `#`, or backslash: not parsed with confidence
                k += 1
            if c == ":" and k == spaces(j + 1):
                return False  # `:` needs a label
            i = command_end(k)
        elif c in "rR":
            i = line_end(j + 1)  # the file name (read, never written) runs to the end of the line
        elif c in "aic":
            k = spaces(j + 1)
            if k + 1 < n and prog[k] == "\\" and prog[k + 1] == "\n":
                k += 2  # the classic `a\` form: the text is the next line
            elif k < n and prog[k] in "\\\n":
                return False  # an `a\text` one-liner or an empty text: not parsed with confidence
            e = line_end(k)
            if e > k and prog[e - 1] == "\\":
                return False  # a text line ending in a backslash continues: not parsed with confidence
            i = e
        elif c in "sy":
            if j + 1 >= n or prog[j + 1] in "\n\\":
                return False
            delim = prog[j + 1]
            k = regex_end(j + 2, delim) if c == "s" else plain_end(j + 2, delim)
            if k < 0:
                return False
            k = plain_end(k, delim)
            if k < 0:
                return False
            if c == "s":
                while k < n and prog[k] not in ";\n}#":
                    if prog[k] in "we":
                        return False  # an s///w or s///e flag (spaces may separate flags)
                    if prog[k] not in _SED_S_FLAGS and prog[k] not in " \t":
                        return False
                    k += 1
            i = command_end(k)
        else:
            return False  # an unknown command
        if i < 0:
            return False
    return depth == 0


def _write_targets(kind, words, inplace=True):
    """The TARGET words of one write command `kind` given its argument `words` (round 24): the destination of
    cp, mv, or install (every -t, -tDIR, --target-directory DIR, or --target-directory=DIR argument, else the
    last operand); the of= operand of dd; and every operand (a word that is not an option, and every word
    after `--`) of tee, truncate, ed, ex, and an in-place sed or perl, plus ex's written-file option arguments.
    Round 26: every option argument the command's _OPTION_SPECS entry declares is consumed first (a short
    cluster is walked letter by letter, as getopt does), so `sed -f <file>`, `sed -e <script>`, `truncate -r
    <file>`, or `perl -I <dir>` never names a target, and a sed or perl given no -e/-f style option drops its
    first operand, the program. Round 27: for a command whose spec sets "stop" (perl), option parsing ends at
    the FIRST operand, as the real tool's does, so a later option-looking word (`X -I S`) is an operand; and
    None (an UNKNOWN target) when any SCRIPT TEXT argument ("text", or sed's dropped program) names a store
    path, since that text can itself write the path (a sed `w` command or `s///w` flag, an ex `w!`). With
    `inplace` False (a sed or perl with no in-place option, which edits no operand) the result is [] unless
    such script text makes it None. Round 30: under an "all" option (mv --exchange, install -d or --directory)
    every operand and every target-directory argument is a target; after install's bare --context the next
    word, when it is `-` or does not start with `-`, may be its argument (uutils) or an operand (GNU), so the
    destination candidates are the last operand under BOTH readings; and a perl --help or --version met while
    options are parsed makes the command write nothing ([])."""
    if kind == "dd":
        return [w[3:] for w in words if w.startswith("of=")]
    spec = _OPTION_SPECS.get(kind, {})
    req, opt, run = spec.get("req", ""), spec.get("opt", ""), spec.get("run", {})
    long_req, target, script = spec.get("long", ()), spec.get("target", ()), spec.get("script")
    text, stop = spec.get("text", ()), spec.get("stop", False)
    operands, dirs, ends, pending, scripted, texts = [], [], False, None, False, []
    filescript = False  # round 28: a program option whose argument names a FILE (sed -f, --file) was given
    posix = False  # round 29: sed's --posix, or an unambiguous abbreviation of it, was given
    every_opts, optsep, exits = spec.get("all", ()), spec.get("optsep", ()), spec.get("exit", ())
    every = False  # round 30: an "all" option was given: every operand is written
    maybe, optional = set(), False  # round 30: operand indices an "optsep" option may have consumed
    for w in words:
        optional_next, optional = optional, False
        if pending is not None:  # the separate argument word of the option `pending`
            if pending in target:
                dirs.append(w)
            if pending in text:
                texts.append(w)
            pending = None
        elif optional_next and not ends and (w == "-" or not w.startswith("-")):
            maybe.add(len(operands))  # round 30: install --context's argument (uutils) or an operand (GNU)
            operands.append(w)
        elif ends or not w.startswith("-") or w == "-":
            operands.append(w)
            ends = ends or stop  # round 27: perl parses no option after its first operand
        elif w == "--":
            ends = True
        elif w.startswith("--"):
            name, eq, value = w.partition("=")
            name = _resolve_long(kind, name)  # round 29: `--expr` is --expression, as the real tool reads it
            if name is None:
                if kind in _STRICT_LONG_COMMANDS:
                    return None  # an ambiguous or unrecognized long option on sed, perl, or ex: target UNKNOWN
                continue  # elsewhere the tool rejects it and writes nothing: an option with no argument
            posix = posix or name == "--posix"
            if name in exits:
                # round 30: perl exits at once on --help or --version, and `=value` is an unknown switch; round 31:
                # GNU cp, mv, sed, and ed and uutils install, tee, and truncate also exit at once on either (an
                # abbreviation included), wherever it stands before `--`, and reject `=value` without writing
                return None if eq and not spec.get("exit_eq") else []
            every = every or name in every_opts
            optional = name in optsep and not eq
            if name in long_req:
                scripted = scripted or (script is not None and name in script)
                filescript = filescript or (script is not None and name in script and name not in text)
                if not eq:
                    pending = name
                else:
                    if name in target:
                        dirs.append(value)
                    if name in text:
                        texts.append(value)
        else:
            j = 1
            while j < len(w):
                c = w[j]
                every = every or c in every_opts
                if c in req:
                    scripted = scripted or (script is not None and c in script)
                    filescript = filescript or (script is not None and c in script and c not in text)
                    if j + 1 < len(w):
                        if c in target:
                            dirs.append(w[j + 1:])
                        if c in text:
                            texts.append(w[j + 1:])
                    else:
                        pending = c
                    break
                if c in opt:
                    break  # an attached optional argument: the rest of the cluster
                j = run[c].match(w, j + 1).end() if c in run else j + 1  # every run pattern matches empty
    if script is not None and not scripted:
        if operands and spec.get("text_prog"):
            texts.append(operands[0])  # sed's program operand is script text; perl's names a program FILE
        operands = operands[1:]  # no -e/-f style option: the first operand is the program, never a target
    if any(names_store_path(t) for t in texts):
        # round 28: a sed with no in-place option whose program is parsed with confidence and holds no command
        # that can write (no w, W, or e command, and no s///w or s///e flag) edits nothing, so a store path it
        # merely MENTIONS (a search pattern) is no write target; any other script text naming a store path (an
        # in-place sed, a program read from a -f file, a program not parsed with confidence, an ex -c command,
        # a perl program) keeps the round-27 UNKNOWN target
        read_only = (kind == "sed" and not inplace and not filescript and not posix
                     and _sed_program_cannot_write("\n".join(texts)))
        if not read_only:
            return None  # round 27: script text naming a store path can write it: the target is UNKNOWN
    if not inplace:
        return []
    if kind in _DEST_COMMANDS:
        if every:
            return operands + dirs  # round 30: mv --exchange or install -d writes every operand
        if dirs or not operands:
            return dirs
        # round 30: the last operand, and (after install's bare --context) the last one not possibly consumed
        last = {len(operands) - 1} | {max((i for i in range(len(operands)) if i not in maybe), default=-1)}
        return [operands[i] for i in sorted(last) if i >= 0]
    return operands + dirs


def _stream_writes(toks):
    """True when one token stream of _shell_tokens shows a write indicator (see bash_writes). A WRITE_COMMANDS
    word, and the sed or perl word an in-place option must follow, count only in COMMAND position: the
    stream's first word, the first word after a separator, after a reserved word that keeps the position
    (_CMD_PREFIX_WORDS), after VAR=value assignments, after an input or output redirection and its operand,
    after a PREFIX_COMMANDS word with its options, option arguments, and leading operands, or, in a simple
    command whose command word is find, after its -exec-style word (up to that command's terminating `;` or
    `+` word), or at the body of a function header (`function NAME {`, or `function NAME if`, `while`, or
    `until`; the `NAME() {` forms restore it through their `(` and `)` separators). A write inside a function body is thus detected whether or not the
    function is ever called (the conservative direction). The output-redirection test is position-free."""
    return bool(_stream_targets(toks))


def _stream_targets(toks):
    """The write indicators of one token stream (see _stream_writes, whose command-position rules this
    applies), one entry per indicator (round 24): the list of that write's TARGET words (_write_targets for a
    write command, possibly empty, as for a bare `tee`; the operand word for an output redirection), or None
    when its target is unknown: a redirection with no operand, a target word holding a `$` expansion or a find
    `{}` placeholder, or a write command run through xargs (which supplies more operands on stdin)."""
    return [targets for targets, _k in _stream_write_detail(toks)]


def _stream_write_detail(toks):
    """Round 31: _stream_targets() with each entry paired with the index in `toks` of the token that makes it
    (the write command's word, or the redirection's operand or operator), so the analysis can tell which
    simple command, and so which data, each write belongs to."""
    out, cur = [], None  # cur: the simple command's write command, [kind, argument words, in-place option
    # seen, run through xargs, token index]
    via_xargs = False  # the simple command's prefix chain includes xargs

    def finish():
        if cur is not None:
            in_place = cur[0] not in INPLACE_COMMANDS or cur[2]
            # round 27: a sed or perl with no in-place option edits no operand, but its script text can still
            # write a named store path (a sed `w`), so it is a write of UNKNOWN target exactly when that text
            # names one; otherwise it is no write at all
            targets = _write_targets(cur[0], cur[1], in_place)
            if targets is None:
                out.append((None, cur[4]))
            elif in_place:
                out.append((None if cur[3] or any("$" in w or "{}" in w for w in targets) else targets, cur[4]))
    inplace, target = False, None  # target: None, "in" (an input redirection awaiting its operand), or the dup
    # flag of an output redirection awaiting its operand
    cmdpos, prefix, optarg = True, None, False  # prefix: [options taking an argument, operands still to skip]
    in_find, in_exec = False, False  # the simple command is find; inside one of its -exec-style commands
    fdef = 0  # function header: 1 awaiting the name, 2 awaiting the body
    for k, t in enumerate(toks):
        if t[0] in ("r", "i"):
            if target not in (None, "in"):
                out.append((None, k))  # a redirection with no operand: its target is unknown
            target = t[1] if t[0] == "r" else "in"
            continue
        if t[0] == "s":
            if target not in (None, "in"):
                out.append((None, k))
            target = None
            finish()
            cur, via_xargs = None, False
            inplace, cmdpos, prefix, optarg, in_find, in_exec, fdef = False, True, None, False, False, False, 0
            continue
        w = t[1]
        if target == "in":
            target = None  # the operand of an input redirection (a file, here-string, delimiter, or descriptor)
            continue
        if target is not None:
            if w != "/dev/null" and not (target and _FD_DUP_RE.fullmatch(w)):
                out.append((None if "$" in w else [w], k))  # the redirection's target (a `$` expansion: unknown)
            target = None
            continue
        if cmdpos and w.isdigit() and k + 1 < len(toks) and toks[k + 1][0] in ("r", "i"):
            continue  # the descriptor of an `N>` or `N<` redirection, not a command word
        if fdef:
            # the name; then the body's `{`, `if`, `while`, or `until` (a bash function body is any compound command)
            fdef, cmdpos = (2, False) if fdef == 1 else (0, w in _CMD_PREFIX_WORDS)
            continue
        if cmdpos:
            base = os.path.basename(w)  # round 31: computed only where it is used (a command word)
            if prefix is not None:
                if optarg:
                    optarg = False
                elif w.startswith("-") and w != "-":
                    optarg = w in prefix[0]
                elif prefix[1] > 0:
                    prefix[1] -= 1
                else:
                    prefix = None
            if prefix is None and not (_ASSIGN_RE.fullmatch(w) or w in _CMD_PREFIX_WORDS):
                if w == "function":
                    cmdpos, fdef = False, 1
                    continue
                if base in PREFIX_COMMANDS:
                    prefix = [PREFIX_COMMANDS[base][0], PREFIX_COMMANDS[base][1]]
                    via_xargs = via_xargs or base == "xargs"
                else:
                    cmdpos, inplace = False, base in INPLACE_COMMANDS
                    if base in WRITE_COMMANDS or inplace:
                        finish()
                        cur = [base, [], False, via_xargs, k]  # its argument words follow; targets read at its end
                    if not in_exec:
                        in_find = base == "find"
        elif in_exec and w in (";", "+"):
            finish()
            cur = None
            in_exec = inplace = False  # the end of a find -exec-style command; find's own arguments resume
        elif in_find and not in_exec and w in _EXEC_WORDS:
            cmdpos = in_exec = True
        else:
            if cur is not None:
                cur[1].append(w)
            if inplace and _is_inplace_opt(cur[0], w):
                cur[2] = True
    if target not in (None, "in"):
        out.append((None, len(toks) - 1))
    finish()
    return out


# ---- round 31: which data each write carries ----

# Round 31: the work budget of one Bash evaluation (tokenizer steps, here-document lines, tokens and units walked,
# nested shell -c analyses included). A command that spends it is ALLOWED unchecked (fail open), so no input can
# hold the hook past its latency bound; ordinary commands, and 64 KiB of ordinary text, stay well inside it.
BASH_WORK_BUDGET = 80_000
STREAM_COST = 8  # round 31: budget steps charged per substitution frame, in the tokenizer and again in the analysis
SHELL_C_COST = 30  # round 31: budget steps charged per nested shell -c analysis
SHELL_C_DEPTH = 3  # round 31: nesting depth to which literal bash/sh/dash/zsh -c strings are analysed
CWD_COST = 6  # round 32: budget steps charged per directory, BEFORE a cd is resolved from, or a target or staged
#               word resolved against, each directory the command can be in, and per directory in a set union
MAX_CWDS = 32  # round 32: the most directories a command point can be in; a larger set spends the budget (fail open)
# Round 31: commands that only read (their output goes to the caller, never into a file of their own choosing), so
# a literal in a pipeline of these, with no redirection or write anywhere in it, feeds no write
READ_COMMANDS = frozenset((
    "grep", "egrep", "fgrep", "rg", "cat", "head", "tail", "wc", "ls", "stat", "file", "diff", "cmp", "echo",
    "printf", "test", "[", "true", "false", ":", "date", "pwd", "basename", "dirname", "realpath", "readlink",
    "cut", "tr", "jq", "nl", "od", "xxd", "md5sum", "sha1sum", "sha256sum", "cksum", "which"))
_SHELLS = frozenset(("bash", "sh", "dash", "zsh"))
_CD_COMMANDS = frozenset(("cd", "pushd", "popd"))
_OPENERS = frozenset(("{", "if", "while", "until", "for", "select"))
_CLOSER_OF = {"}": ("{",), "fi": ("if",), "done": ("while", "until", "for", "select"), "esac": ()}
_CONTINUERS = frozenset(("then", "do", "else", "elif", "!"))
_UNRELIABLE_WORDS = frozenset(("case", "function", "coproc"))
_CD_HINT_RE = re.compile(r"(?<![^\s;&|()`{}])(?:cd|pushd|popd)(?![^\s;&|()`<>])")
_SHELL_HINT_RE = re.compile(r"(?<![^\s;&|()`{}/])(?:bash|sh|dash|zsh)(?![^\s;&|()`<>])")


def _is_pipe(b):
    """Round 31: True when the separator text `b` between two simple commands is a pipe (`|` or `|&`, possibly
    with a subshell's parentheses or newlines after it), not a list operator (`;`, `&`, `&&`, `||`, a newline)."""
    s = b.replace("(", "").replace(")", "")
    if "~" in s:  # the separator the tokenizer inserted after `]]` or `))`: one command, not a list
        s = s.replace("~", "")
        if not s:
            return True
    if s.strip("\n"):
        s = s.replace("\n", "")
    return s in ("|", "|&")


def _units(toks, pos, cmd):
    """Round 31: split one token stream into its simple commands. Returns (units, bounds, tok_unit): units are
    [first token, end token (exclusive), raw start, raw end] per simple command, bounds[k] the separator text
    before unit k (bounds[len(units)] the text after the last; a separator the tokenizer inserted after a closed
    `]]` or `))` reads `~`, joining its neighbours like a pipe), and tok_unit the unit index of every token (a
    separator maps to the unit before it, or 0)."""
    units, bounds, bnd, cur, tok_unit = [], [], [], None, []
    for k, t in enumerate(toks):
        if t[0] == "s":
            if cur is not None:
                units.append(cur)
                cur = None
            a, b = pos[k]
            bnd.append(cmd[a] if b > a else "~")
            tok_unit.append(max(len(units) - 1, 0))
            continue
        if cur is None:
            bounds.append("".join(bnd))
            bnd = []
            cur = [k, k + 1, pos[k][0], pos[k][1]]
        else:
            cur[1] = k + 1
            if pos[k][1] > cur[3]:
                cur[3] = pos[k][1]
        tok_unit.append(len(units))
    if cur is not None:
        units.append(cur)
    bounds.append("".join(bnd))
    return units, bounds, tok_unit


def _unit_words(toks, a, b):
    """Round 31: the (token index, word) pairs of tokens a..b, less redirection operands and the descriptor
    number of an `N>` or `N<` redirection."""
    out, skip = [], False
    for k in range(a, b):
        t = toks[k]
        if t[0] != "w":
            skip = t[0] in ("r", "i")
            continue
        if skip:
            skip = False
            continue
        if t[1].isdigit() and k + 1 < b and toks[k + 1][0] in ("r", "i"):
            continue
        out.append((k, t[1]))
    return out


def _classify(words, cmd, pos):
    """Round 31: (kind, rest) of one simple command whose leading reserved words are already removed: "empty" (no
    command word), "assign" (assignments only), "exec" (a bare exec: its redirections apply to every later
    command), "shell" (bash, sh, dash, or zsh; rest: its argument words), "cd" or "popd" (rest: the arguments),
    "tee", "read" (a READ_COMMANDS word, printf without -v, `[[`, or an arithmetic command), or "other". Leading
    assignments and PREFIX_COMMANDS with their options are passed over; xargs makes it "other"."""
    j, nw = 0, len(words)
    while j < nw and _ASSIGN_RE.fullmatch(words[j][1]):
        j += 1
    if not nw:
        return "empty", None
    if j == nw:
        return "assign", None
    saw_exec = False
    while j < nw:
        base = os.path.basename(words[j][1])
        if base not in PREFIX_COMMANDS:
            break
        if base == "xargs":
            return "other", None
        saw_exec = saw_exec or base == "exec"
        opts, nops = PREFIX_COMMANDS[base]
        j += 1
        while j < nw:
            w = words[j][1]
            if w.startswith("-") and w != "-":
                j += 2 if w in opts else 1
            elif base == "env" and _ASSIGN_RE.fullmatch(w):
                j += 1
            elif nops:
                nops -= 1
                j += 1
            else:
                break
    if j >= nw:
        return ("exec" if saw_exec else "empty"), None
    k, w = words[j]
    base, rest = os.path.basename(w), words[j + 1:]
    if base in _SHELLS:
        return "shell", rest
    if base in _CD_COMMANDS:
        return ("popd" if base == "popd" else "cd"), rest
    if base == "tee":
        return "tee", None
    if w == "[[" or (w == "$" and cmd.startswith("((", pos[k][0])):
        return "read", None
    if base in READ_COMMANDS and not (base == "printf" and any(x.startswith("-v") for _k, x in rest)):
        return "read", None
    return "other", None


class _Structure:
    """Round 31: the compound structure of one token stream (see _structure())."""
    __slots__ = ("kinds", "rest", "depth", "tainted", "opens", "closes", "unreliable")


def _structure(toks, pos, units, bounds, cmd):
    """Round 31: walk the simple commands of one stream, tracking compound commands: a `(` in a separator opens
    a subshell and a `)` closes it; a leading `{`, `if`, `while`, `until`, `for`, or `select` opens a compound and
    a leading `}`, `fi`, or `done` closes it (a `then`, `do`, `else`, `elif`, or `!` passes). A compound whose
    output is redirected (a `>` after its closer) or piped onward TAINTS every simple command inside it: all their
    literals are data of that write. Per unit it records its kind (_classify), depth, and whether it opened or
    closed a compound; the stream is UNRELIABLE (no literal is then excluded as unrelated) when it holds a
    `case`, a function definition (`function` or `NAME()`), `coproc`, a bare `exec`, or a closer that does not
    match, or leaves a compound open."""
    nu = len(units)
    st = _Structure()
    st.kinds, st.rest, st.depth = [None] * nu, [None] * nu, [0] * nu
    st.opens, st.closes = [False] * nu, [False] * nu
    diff, stack, unreliable = [0] * (nu + 2), [], False

    def close(accept, first_ok, u_end, tainting):
        nonlocal unreliable
        if not stack or stack[-1][0] not in accept:
            unreliable = True
            return
        _kind, first = stack.pop()
        if tainting and first_ok:
            diff[first] += 1
            diff[u_end + 1] -= 1

    for u in range(nu + 1):
        b = bounds[u]
        if "()" in b.replace(" ", ""):
            unreliable = True  # a NAME() function header
        for x, ch in enumerate(b):
            if ch == "(":
                stack.append(("(", u))
            elif ch == ")":
                after = b[x + 1:]
                redirected = after == "" and u < nu and toks[units[u][0]][0] == "r"
                close(("(",), True, u - 1 if not redirected else u, redirected or _is_pipe(after))
        if u == nu:
            break
        a, e = units[u][0], units[u][1]
        words = _unit_words(toks, a, e)
        j = 0
        while j < len(words):
            w = words[j][1]
            if w in _OPENERS:
                stack.append((w, u))
                st.opens[u] = True
                j += 1
                if w in ("for", "select"):
                    st.kinds[u] = "header"  # its words (the list) are data of the loop body
                    break
            elif w in _CONTINUERS:
                st.opens[u] = True
                j += 1
            elif w in _CLOSER_OF:
                if w == "esac":
                    unreliable = True
                st.closes[u] = True
                redirected = any(toks[k][0] == "r" for k in range(words[j][0] + 1, e))
                close(_CLOSER_OF[w], True, u, redirected or _is_pipe(bounds[u + 1]))
                j += 1
            elif w in _UNRELIABLE_WORDS:
                unreliable = True
                j += 1
            else:
                break
        st.depth[u] = len(stack)
        if st.kinds[u] is None:
            st.kinds[u], st.rest[u] = _classify(words[j:], cmd, pos)
            if st.kinds[u] == "exec":
                unreliable = True
    if stack:
        unreliable = True
    run, st.tainted = 0, [False] * nu
    for u in range(nu):
        run += diff[u]
        st.tainted[u] = run > 0
    st.unreliable = unreliable
    return st


def _cd_target(kind, rest, cwds, ctx, budget=None):
    """Round 31: the directories a cd or pushd with arguments `rest` (word pairs) can leave, from each directory
    in `cwds` (None: unknown): an existing directory named literally, else None (unknown); popd is unknown.
    Round 32: CWD_COST per directory of `cwds` is charged to `budget` BEFORE any is resolved."""
    if budget is not None:
        budget.spend(CWD_COST * (len(cwds) + 1))
    if kind != "cd":
        return frozenset((None,))
    args = [w for _k, w in rest]
    while args and args[0] in ("-L", "-P", "-e", "-@", "-LP", "-PL", "-Pe"):
        args.pop(0)
    if args and args[0] == "--":
        args.pop(0)
    if len(args) != 1 or args[0] in ("-", "") or args[0].startswith("~") or "$" in args[0]:
        return frozenset((None,))
    d = args[0]
    if not d.startswith("/") and not d.startswith(("./", "../")) and d not in (".", "..") \
            and os.environ.get("CDPATH"):
        return frozenset((None,))  # CDPATH may send a relative cd elsewhere
    out = set()
    for c in cwds:
        if d.startswith("/"):
            p = os.path.normpath(d)
        elif c is None:
            out.add(None)
            continue
        else:
            p = os.path.normpath(os.path.join(c, d))
        out.add(p if ctx.isdir(p) else None)
    return frozenset(out)


def _target_status(w, cwds, ctx, budget=None):
    """Round 31: "store", "unknown", or "other" for one write TARGET word. A word holding a store path token is a
    store target (the lexical rule of earlier rounds); any other relative word is resolved against every
    directory the command can be in at that point (`cwds`, from the payload cwd and any cd before it): under a store root from any of them is a
    store target, and an unknown directory (None) or a `~` word makes it unknown. Round 32: CWD_COST per
    directory is charged to `budget` BEFORE the resolution."""
    if budget is not None:
        budget.spend(CWD_COST * (len(cwds) + 1))
    if ctx.names(w):
        return "store"  # a store path token anywhere in the word (the lexical rule of every earlier round)
    if w.startswith("/"):
        return "other"
    if w.startswith("~"):
        return "unknown"
    unknown = False
    for c in cwds:
        if c is None:
            unknown = True
        elif ctx.under(os.path.normpath(os.path.join(c, w))):
            return "store"
    return "unknown" if unknown else "other"


def _shell_c_string(rest):
    """Round 31: the token index of the command string of a shell run with -c (a short option cluster holding
    c; `-o`, `-O`, `--rcfile`, and `--init-file` take a separate argument), or None."""
    cflag, j = False, 0
    while j < len(rest):
        w = rest[j][1]
        if w == "--":
            j += 1
            break
        if w in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
            j += 2
            continue
        if w.startswith("--"):
            j += 1
            continue
        if len(w) > 1 and w[0] in "-+":
            cflag = cflag or (w[0] == "-" and "c" in w[1:])
            j += 1
            continue
        break
    return rest[j][0] if cflag and j < len(rest) else None


_SEARCH_ARG_OPTS = frozenset(("-f", "--file", "-m", "--max-count", "-A", "-B", "-C", "--after-context",
                              "--before-context", "--context", "-d", "-D", "-g", "--glob", "-t", "--type", "-T",
                              "--type-not", "-M", "--max-columns", "-j", "--threads", "--color", "--colors"))


_SEARCH_QUIET_LONG = {"rg": frozenset(("--count", "--count-matches", "--quiet", "--files-with-matches",
                                        "--files-without-match")),
                      "grep": frozenset(("--count", "--quiet", "--silent", "--files-with-matches",
                                         "--files-without-match"))}
_SEARCH_QUIET_SHORT = {"rg": frozenset("cql"), "grep": frozenset("cqlL")}
_SEARCH_ARG_SHORT = frozenset("efmABCdDgtTMj")  # a short option taking an argument: the rest of a cluster is it


def _search_patterns(toks, pos=None, cmd=""):
    """Round 31: the token indices of the PATTERN words in a token stream made only of grep, egrep, fgrep, rg,
    and wc commands (each unit's first word; no redirection, here-string, or here-document; no rg -r or
    --replace, which prints the replacement text, and no grep --label, which prints its text), else an empty
    set. A pattern is the argument of -e or --regexp (attached or separate), else the first operand; an option
    listed in _SEARCH_ARG_OPTS takes the next word; anything else starting with `-` is taken as a flag.
    Round 32: a MATCHING search prints file text that holds its pattern (with -o, exactly the pattern), so a
    pattern is returned only when its pipeline's output cannot hold it: the pipeline's LAST command is wc, or a
    search with a count, quiet, or file-list option (-c, -q, -l, grep -L, or the long forms --count,
    --count-matches, --quiet, --silent, --files-with-matches, --files-without-match; exact spellings only, an
    abbreviation is not recognized, the conservative direction). `pos` and `cmd` give each separator's text (a
    `|` joins a pipeline); without them every separator is taken as a list operator."""
    out, j, n = set(), 0, len(toks)
    pipeline, safe_last = set(), False  # the patterns of the current pipeline, and whether its last command hides them

    def flush():
        if safe_last:
            out.update(pipeline)
        pipeline.clear()
    sep = ""
    while j < n:
        if toks[j][0] == "s":
            a, b = pos[j] if pos is not None else (0, 0)
            sep += cmd[a] if b > a else "~"
            j += 1
            continue
        if sep and not (pos is not None and _is_pipe(sep)):
            flush()  # a list operator (`;`, `&&`, `||`, `&`, a newline) or an unknown separator ends the pipeline
        sep = ""
        k = j
        while k < n and toks[k][0] != "s":
            if toks[k][0] != "w":
                return set()  # a redirection or input of any kind
            k += 1
        words = [(x, toks[x][1]) for x in range(j, k)]
        cw = os.path.basename(words[0][1])
        if cw == "wc":
            safe_last = True
            j = k
            continue
        if cw not in ("grep", "egrep", "fgrep", "rg"):
            return set()
        fam = "rg" if cw == "rg" else "grep"
        quiet = False
        for _x, w in words[1:]:
            if w == "--":
                break
            if w.startswith("--"):
                quiet = quiet or w in _SEARCH_QUIET_LONG[fam]
            elif w.startswith("-") and w != "-":
                for ch in w[1:]:
                    if ch in _SEARCH_QUIET_SHORT[fam]:
                        quiet = True
                    if ch in _SEARCH_ARG_SHORT:
                        break
        safe_last = quiet
        seen_e, x, found = False, 1, None  # this search's patterns join `pipeline`, kept only if it hides them
        while x < len(words):
            idx, w = words[x]
            if w.startswith("--label") or (cw == "rg" and (w.startswith("--replace") or (
                    w.startswith("-") and not w.startswith("--") and "r" in w[1:]))):
                return set()
            if w == "--":
                if not seen_e and x + 1 < len(words) and found is None:
                    found = words[x + 1][0]
                break
            if w in ("-e", "--regexp"):
                if x + 1 < len(words):
                    pipeline.add(words[x + 1][0])
                seen_e, x = True, x + 2
                continue
            if w.startswith("--regexp=") or (w.startswith("-e") and len(w) > 2 and not w.startswith("--")):
                pipeline.add(idx)
                seen_e, x = True, x + 1
                continue
            if w in _SEARCH_ARG_OPTS:
                seen_e = seen_e or w in ("-f", "--file")
                x += 2
                continue
            if w.startswith("-") and w != "-":
                x += 1
                continue
            if found is None:
                found = idx
            x += 1
        if found is not None and not seen_e:
            pipeline.add(found)
        j = k
    flush()
    return out


def _literal_shell_string(raw):
    """Round 31: (text, offsets) of a shell -c command string whose raw word `raw` is ONE single-quoted string, or
    ONE double-quoted string with no expansion (no unescaped `$`, backtick, or quote; a backslash before `$`, a
    backtick, `"`, or a backslash yields that character and one before a newline removes both, as bash reads
    it); offsets[i] is the offset within `raw` of text[i]. Anything else is None (left uninspected)."""
    if len(raw) < 2 or raw[0] != raw[-1] or raw[0] not in "'\"":
        return None
    if raw[0] == "'":
        body = raw[1:-1]
        return None if "'" in body else (body, list(range(1, len(raw) - 1)))
    text, offs, j, end = [], [], 1, len(raw) - 1
    while j < end:
        c = raw[j]
        if c in '"$`':
            return None
        if c == "\\":
            if j + 1 >= end:
                return None  # the closing quote is escaped: not one string
            nx = raw[j + 1]
            if nx in '$`"\\':
                text.append(nx)
                offs.append(j + 1)
                j += 2
                continue
            if nx == "\n":
                j += 2
                continue
        text.append(c)
        offs.append(j)
        j += 1
    return "".join(text), offs


class _Analysis:
    """Round 31: the result of _analyze(): `store`, whether the command writes a store path (a parsed store
    target, or an unknown target in a command naming a store path); `counts(p)`, whether a literal starting at
    offset p is data of a store write (see _analyze). Round 32, for an enclosing analysis of a shell -c string:
    `targets`, the (word, directories) pairs of its writes to KNOWN non-store files; `unknown`, whether it has a
    write whose target is unknown; `words()`, the (word, directories) pairs of every word it holds (the files
    its store write can read, for staging)."""
    __slots__ = ("store", "counts", "targets", "unknown", "words")

    def __init__(self, store, counts, targets=(), unknown=False, words=lambda: ()):
        self.store, self.counts = store, counts
        self.targets, self.unknown, self.words = targets, unknown, words


def _span_index(spans, starts, p):
    k = bisect.bisect_right(starts, p) - 1
    return k if k >= 0 and p < spans[k][1] else -1


def _analyze(cmd, cwds, ctx, budget, depth=0):
    """Round 31: which store writes a Bash command makes, and which literals are their DATA. A literal counts
    only for the write it can feed: the store write's own simple command (its arguments, its here-string, its
    redirections), its here-document bodies, and the other commands of its pipeline (their output is its input),
    or every command inside a compound command whose output is redirected or piped to it. A literal is NOT
    counted when it lies in a comment (outside a here-document body), or in a pipeline of READ_COMMANDS with no
    redirection and no write anywhere in it, standing outside any such compound (a search: `grep -F <literal>
    <store file>; echo checked >> <store file>`), or in a pipeline whose only writes go to other known files that
    no store-writing command then names (so `printf <literal> > x; cp x <store dir>` still counts: x flows into
    the store). Every other literal counts, the conservative direction: an assignment, an unknown program, a
    loop's word list, and anything in a command the analysis finds unreliable (see _structure) or cannot parse.
    Relative write targets are resolved against `cwds`, the directories the command can be in (the payload cwd,
    then each confidently understood cd or pushd: an existing directory named literally, replacing the set when
    the cd runs unconditionally at the top of its stream, joining it otherwise; any other cd adds an unknown).
    A bash, sh, dash, or zsh -c whose command string is a single- or double-quoted literal with no expansion is
    analysed recursively (to SHELL_C_DEPTH) with the same directories; its store writes are the enclosing
    command's, and literals inside the string are decided by that inner analysis. Round 32: a `( ... )` subshell
    scopes the directory set (restored at its `)`); directory work is charged to `budget` before it is done and
    a set above MAX_CWDS spends it; a literal inside a shell -c string ALSO counts when the wrapper's output
    reaches a store write (wrapper_counts), and the string's writes and, for a string with a store write, its
    words join the enclosing staging walk."""
    ds = date_spans(cmd, budget)
    ds_starts = [s for s, _ in ds]

    def dated(p):
        return _span_index(ds, ds_starts, p) >= 0

    tk = _tokenize(cmd, budget)
    if tk is None:  # unparseable: a store write when the text names a store path, and every literal counts
        return _Analysis(ctx.names(cmd), lambda p: not dated(p), unknown=True)
    streams, poss, fspans = tk.streams, tk.poss, tk.spans
    budget.spend(STREAM_COST * len(streams))
    top, ns = len(streams) - 1, len(streams)
    infos = [None] * ns

    def info(s):
        if infos[s] is None:
            budget.spend(len(streams[s]))
            infos[s] = _units(streams[s], poss[s], cmd)
        return infos[s]

    structs = [None] * ns

    def struct(s):
        if structs[s] is None:
            units, bounds, _tu = info(s)
            budget.spend(3 * len(streams[s]) + 4 * len(units))
            structs[s] = _structure(streams[s], poss[s], units, bounds, cmd)
        return structs[s]

    # the directories each simple command can run in (only a command that can change directory needs the walk)
    unit_cwds = {}
    if _CD_HINT_RE.search(cmd):
        order = sorted(range(ns), key=lambda s: (fspans[s][0], -fspans[s][1], -s))
        pstack, ustarts = [], {}
        for s in order:
            while pstack and fspans[pstack[-1]][1] <= fspans[s][0]:
                pstack.pop()
            if s == top or not pstack:
                start = cwds
            else:
                par = pstack[-1]
                if par not in ustarts:
                    ustarts[par] = [u[2] for u in info(par)[0]]
                pu = bisect.bisect_right(ustarts[par], fspans[s][0]) - 1
                start = unit_cwds[par][pu] if pu >= 0 and unit_cwds.get(par) else cwds
            units, bounds, _tu = info(s)
            st = struct(s)
            cur, out, saved = start, [], []
            for u in range(len(units)):
                if not st.unreliable:
                    # round 32: a `( ... )` subshell SCOPES directory state: the set in force at its `(` is
                    # restored at its `)` (round 31 let a subshell's cd leak into the parent); a `{ ... }` group
                    # does not scope, as in bash. An unreliable stream keeps round 31's join-only walk.
                    for ch in bounds[u]:
                        if ch == "(":
                            saved.append(cur)
                        elif ch == ")" and saved:
                            cur = saved.pop()
                out.append(cur)
                if st.kinds[u] in ("cd", "popd"):
                    new = _cd_target(st.kinds[u], st.rest[u], cur, ctx, budget)
                    before = bounds[u].replace("(", "").replace(")", "")
                    after = bounds[u + 1]
                    # replaced only when the cd surely runs, in this shell, before what follows: at the depth of
                    # its innermost enclosing subshell (0 at the top) inside no other compound, first in its
                    # list (after nothing, `;`, or a newline, once any subshell parentheses are set aside), not
                    # piped and not in the background; otherwise it may or may not have run, so both are kept
                    sure = (not st.unreliable and st.depth[u] == len(saved) and (saved or not st.tainted[u])
                            and set(before) <= {";", "\n"} and not _is_pipe(after) and after.strip("\n;") != "&")
                    if not sure:
                        budget.spend(CWD_COST * (len(cur) + len(new)))  # round 32: the union, charged first
                    cur = new if sure else cur | new
                    if len(cur) > MAX_CWDS:
                        raise _Exhausted()  # round 32: too many possible directories: fail open
            unit_cwds[s] = out
            pstack.append(s)

    def cwd_of(s, k):  # the directories token k of stream s runs in
        got = unit_cwds.get(s)
        return got[info(s)[2][k]] if got else cwds

    # every write: (stream, token index, status, known non-store target words, directories); a sub-stream's
    # write belongs to the top-level simple command holding the substitution
    writes, memo, fallback = [], {}, None
    store_toks, store_pos = [], []  # the top-stream token indices, and the raw offsets, of store writes
    for s in range(ns):
        budget.spend(2 * len(streams[s]))  # the command-position walk costs about two fast-path tokens a token
        det = _stream_write_detail(streams[s])
        for targets, k in det:
            dirs = cwd_of(s, min(k, len(streams[s]) - 1)) if unit_cwds else cwds
            if targets is None:
                status = "unknown"
            else:
                status = "other"
                for w in targets:
                    key = (w, dirs)
                    got = memo.get(key)
                    if got is None:
                        got = memo[key] = _target_status(w, dirs, ctx, budget)
                    if got == "store":
                        status = "store"
                        break
                    if got == "unknown":
                        status = "unknown"
            if status == "unknown":
                if fallback is None:
                    fallback = ctx.names(cmd)
                if fallback:
                    status = "store"  # an unknown target in a command naming a store path counts as a store write
            writes.append((s, k, status, targets if status == "other" else None, dirs))
            if status == "store":
                if s == top:
                    store_toks.append(k)
                else:
                    store_pos.append(fspans[s][0])
    # literal shell -c strings, analysed recursively
    delegates = []
    if depth < SHELL_C_DEPTH and _SHELL_HINT_RE.search(cmd):
        for s in range(ns):
            if not any(t[0] == "w" and os.path.basename(t[1]) in _SHELLS for t in streams[s]):
                continue
            st, units = struct(s), info(s)[0]
            for u in range(len(units)):
                if st.kinds[u] != "shell":
                    continue
                k = _shell_c_string(st.rest[u])
                if k is None:
                    continue
                a, b = poss[s][k]
                got = _literal_shell_string(cmd[a:b])
                if got is None:
                    continue  # not one quoted literal with no expansion: left uninspected
                inner, offs = got
                budget.spend(SHELL_C_COST)
                sub = _analyze(inner, cwd_of(s, k), ctx, budget, depth + 1)
                delegates.append((a + 1, b - 1, sub, [a + o for o in offs], s, k, a))
                if sub.store:
                    store_pos.append(a)
    targets_out = [(w, dirs) for _s, _k, status, targets, dirs in writes if status == "other" for w in targets]
    targets_out.extend(t for d in delegates for t in d[2].targets)
    unknown_out = any(w[2] == "unknown" for w in writes) or any(d[2].unknown for d in delegates)

    def words_out():
        """Round 32: the (word, directories) pairs of every word of this command (its streams and its literal
        shell -c strings), for an enclosing staging walk."""
        got = []
        for s in range(ns):
            budget.spend(len(streams[s]))
            for k, t in enumerate(streams[s]):
                if t[0] == "w":
                    got.append((t[1], cwd_of(s, k) if unit_cwds else cwds))
        for d in delegates:
            got.extend(d[2].words())
        return got
    if not store_toks and not store_pos:
        return _Analysis(False, lambda p: False, targets_out, unknown_out, words_out)
    delegates.sort(key=lambda d: d[0])
    d_starts = [d[0] for d in delegates]
    hd = sorted((a, b, op) for op, a, b in tk.heredocs if b > a)
    hd_starts = [h[0] for h in hd]
    cm, cm_starts = tk.comments, [c[0] for c in tk.comments]
    store_toks.sort()
    store_pos.sort()
    ttoks, tposs = streams[top], poss[top]
    tok_starts, sep_idx, unit_memo = [], [], {}  # built on first use; unit_memo: first token -> decision

    def local_unit(q):
        """The (first, end) token indices of the top-level simple command holding offset q, or None."""
        if not tok_starts:
            tok_starts.extend(ps[0] for ps in tposs)
            sep_idx.extend(k for k, t in enumerate(ttoks) if t[0] == "s")
            budget.spend(len(ttoks) // 4)
        k = bisect.bisect_right(tok_starts, q) - 1
        if k < 0 or ttoks[k][0] == "s" or q >= tposs[k][1]:
            return None
        x = bisect.bisect_left(sep_idx, k)
        return (sep_idx[x - 1] + 1 if x else 0), (sep_idx[x] if x < len(sep_idx) else len(ttoks))

    def local_store(a, b):
        """True when the top-level simple command of tokens a..b holds a store write (its own, one inside a
        substitution in it, or one in a literal shell -c string in it)."""
        x = bisect.bisect_left(store_toks, a)
        if x < len(store_toks) and store_toks[x] < b:
            return True
        budget.spend(b - a)
        lo, hi = tposs[a][0], max(ps[1] for ps in tposs[a:b])
        x = bisect.bisect_left(store_pos, lo)
        return x < len(store_pos) and store_pos[x] < hi

    lazy = {}

    def pipelines():
        """Per top-level pipeline: its class ("store", "read", "stage", or "other"), the known files a "stage"
        pipeline writes, and the words each simple command names (for staging), computed once."""
        if lazy:
            return lazy
        st = struct(top)
        units, bounds, tok_unit = info(top)
        nt = len(units)
        tstarts = [u[2] for u in units]

        def unit_at(p):
            return max(bisect.bisect_right(tstarts, p) - 1, 0)
        t_store, t_write, t_unknown = [False] * nt, [False] * nt, [False] * nt
        t_targets = [[] for _ in range(nt)]
        for s, k, status, targets, dirs in writes:
            u = tok_unit[k] if s == top and k < len(tok_unit) else unit_at(fspans[s][0])
            t_write[u] = True
            if status == "store":
                t_store[u] = True
            elif status == "unknown":
                t_unknown[u] = True
            else:
                t_targets[u].extend((w, dirs) for w in targets)
        # round 32: the store writes of a literal shell -c string, kept apart per unit (d_store: the raw offsets
        # of the delegated strings with a store write), and its other writes, which are the unit's writes too
        own = list(t_store)
        d_store, extra = [[] for _ in range(nt)], [[] for _ in range(nt)]
        d_at = {d[6] for d in delegates if d[2].store}
        for p in store_pos:
            u = unit_at(p)
            t_store[u] = True
            if p in d_at:
                d_store[u].append(p)
            else:
                own[u] = True
        for d in delegates:
            u = tok_unit[d[5]] if d[4] == top and d[5] < len(tok_unit) else unit_at(fspans[d[4]][0])
            if d[2].targets or d[2].unknown:
                t_write[u] = True
            t_unknown[u] = t_unknown[u] or d[2].unknown
            t_targets[u].extend(d[2].targets)
            if d[2].store:
                extra[u].extend(d[2].words())  # the files its store write can read: staged through (round 32)
        nonread = [False] * nt
        words = [set() for _ in range(nt)]
        for s in range(ns):
            if s == top:
                continue
            u = unit_at(fspans[s][0])
            sst = struct(s)
            if sst.unreliable or any(kd not in ("read", "empty") for kd in sst.kinds):
                nonread[u] = True
            words[u].update(t[1] for t in streams[s] if t[0] == "w")
        budget.spend(len(ttoks) + nt)  # round 33: charged before the walk below, not after it
        pipe = [0] * nt
        for u in range(1, nt):
            pipe[u] = pipe[u - 1] if _is_pipe(bounds[u]) else u
        members = {}
        for u in range(nt):
            members.setdefault(pipe[u], []).append(u)
            words[u].update(t[1] for t in ttoks[units[u][0]:units[u][1]] if t[0] == "w")
        # round 33: each pipeline's aggregates, computed ONCE (charged before the pass), so that excluding one
        # unit (a shell -c wrapper, see wrapper_counts) takes constant time; round 32 rescanned the whole pipeline
        # and rebuilt its separator text per wrapper, work of wrappers x pipeline length the budget never saw.
        # Per pipeline: whether it is not plain or holds a non-read substitution or an unknown write ("bad"), and
        # how many of its units are not a non-writing read, are not a read or tee, and have a store write.
        budget.spend(4 * nt + sum(map(len, bounds)) + len(store_pos) + sum(map(len, t_targets)))
        agg = {}
        for pid, us in members.items():
            plain = (not any("(" in bounds[u] or ")" in bounds[u] or st.opens[u] for u in us[1:])
                     and not any(st.closes[u] for u in us))
            agg[pid] = (not plain or any(nonread[u] or t_unknown[u] for u in us),
                        sum(1 for u in us if st.kinds[u] != "read" or t_write[u]),
                        sum(1 for u in us if st.kinds[u] not in ("read", "tee")),
                        sum(1 for u in us if t_store[u]))
        # per unit, whether its delegated store writes hold two distinct strings, and its first (round 33: so that
        # "a delegated store write other than this wrapper's" is a constant-time test)
        d_multi = [bool(x) and any(y != x[0] for y in x) for x in d_store]

        def klass(pid, as_read=-1):
            """The class of pipeline `pid` apart from any store write in it, the unit `as_read` (round 32: a
            shell -c wrapper whose string is judged by its own analysis, a member of the pipeline) taken as a
            read. Round 33: from the pipeline's aggregates, adjusted for `as_read` in constant time."""
            bad, n_read, n_stage, _n = agg[pid]
            if bad:
                return "other"
            if as_read >= 0:  # its term: a read (whatever its kind) that writes nothing, and a read for staging
                n_read -= (st.kinds[as_read] != "read" or t_write[as_read]) - t_write[as_read]
                n_stage -= st.kinds[as_read] not in ("read", "tee")
            if n_read == 0:
                return "read"
            if n_stage == 0:
                return "stage"
            return "other"
        pclass, ptargets = {}, {}
        for pid, us in members.items():
            if not any(t_unknown[u] for u in us):  # the known files it writes: its data can flow on through them
                ptargets[pid] = [t for u in us for t in t_targets[u]]
            pclass[pid] = "store" if agg[pid][3] else klass(pid)
        lazy.update(st=st, pipe=pipe, pclass=pclass, ptargets=ptargets, members=members, words=words, live=None,
                    unit_at=unit_at, units=units, klass=klass, own=own, d_store=d_store, t_store=t_store,
                    extra=extra, tok_unit=tok_unit, agg=agg, d_multi=d_multi)
        return lazy

    def resolved(w, dirs):
        budget.spend(CWD_COST * (len(dirs) + 1))  # round 32: charged before the resolution
        out = {w}
        if w.startswith("/"):
            out.add(os.path.normpath(w))
        elif not w.startswith("~") and "$" not in w:
            out.update(os.path.normpath(os.path.join(c, w)) for c in dirs if c is not None)
        return out

    def live():
        """The pipelines whose written files a store-writing pipeline names, directly or through a chain of
        pipelines that each name a file the next one wrote (`echo <literal> > a; cp a b; mv b <store file>`): a
        staging pipeline's literals reach the store through those files."""
        pl = pipelines()
        if pl["live"] is not None:
            return pl["live"]
        by_target = {}
        for pid, targets in pl["ptargets"].items():
            for w, dirs in targets:
                for r in resolved(w, dirs):
                    by_target.setdefault(r, set()).add(pid)
        alive = {pid for pid, c in pl["pclass"].items() if c == "store"}
        queue = list(alive)
        while queue:
            pid = queue.pop()
            for u in pl["members"][pid]:
                budget.spend(len(pl["words"][u]) + 1)
                dirs = cwd_of(top, pl["units"][u][0])
                # round 32: a literal shell -c string's store write names files too (`bash -c 'cp <staged file>
                # <store file>'`), each resolved against the directories it runs in
                for w, wd in [(w, dirs) for w in pl["words"][u]] + pl["extra"][u]:
                    for r in resolved(w, wd):
                        for q in by_target.get(r, ()):
                            if q not in alive:
                                alive.add(q)
                                queue.append(q)
        pl["live"] = alive
        return alive

    patterns = {}  # sub-stream -> the token indices of its search patterns (see search_pattern)
    sub_order, sub_starts, sub_parent = [], [], {}

    def search_pattern(p):
        """True when offset p lies in the PATTERN word of a grep, egrep, fgrep, or rg inside a command
        substitution whose every command is such a search or wc, reading no here-string, here-document, or
        other input redirection and writing nothing (rg with a replacement, or grep with --label, excluded),
        and whose pipeline ends in wc or a counting, quiet, or file-list search (round 32, see _search_patterns):
        that output is a count, a status, or file names, never the pattern, so the pattern feeds no write
        (`n=$(grep -cF <literal> <store file>); echo "n=$n" >> <store file>`). A matching search printing
        lines prints text holding its pattern, so its pattern is not exempted."""
        if ns < 2:
            return False
        if not sub_order:  # the substitution streams by start, each with its enclosing one (spans nest)
            sub_order.extend(sorted(range(ns - 1), key=lambda x: (fspans[x][0], -fspans[x][1])))
            sub_starts.extend(fspans[x][0] for x in sub_order)
            stack = []
            for x in sub_order:
                while stack and fspans[stack[-1]][1] <= fspans[x][0]:
                    stack.pop()
                sub_parent[x] = stack[-1] if stack else None
                stack.append(x)
            budget.spend(ns)
        j = bisect.bisect_right(sub_starts, p) - 1
        best = sub_order[j] if j >= 0 else None
        while best is not None and not fspans[best][0] <= p < fspans[best][1]:
            best = sub_parent[best]
        if best is None:
            return False
        if best not in patterns:
            budget.spend(len(streams[best]))
            patterns[best] = _search_patterns(streams[best], poss[best], cmd)
        pats = patterns[best]
        if not pats:
            return False
        k = bisect.bisect_right(poss[best], (p, float("inf"))) - 1
        return k in pats and p < poss[best][k][1]

    def counts(p):
        if dated(p):
            return False
        k = _span_index(delegates, d_starts, p)
        if k >= 0:
            d = delegates[k]
            offs = d[3]  # the outer offset of each inner character, ascending
            if d[2].counts(bisect.bisect_left(offs, p)):
                return True
            # round 32: whatever the inner analysis decides, a literal in the string is also data of the WRAPPER
            # command, as for echo: it counts when the wrapper's output reaches a store write
            got = wrapper_memo.get(d[6])
            if got is None:
                got = wrapper_memo[d[6]] = wrapper_counts(d)
            return got
        k = _span_index(hd, hd_starts, p)
        if k >= 0:
            q = hd[k][2]  # a here-document body is data of the command holding its operator
        elif _span_index(cm, cm_starts, p) >= 0:
            return False  # a comment feeds no write
        elif search_pattern(p):
            return False  # a counting or quiet search's pattern in a substitution: its output cannot hold it
        else:
            q = p
        loc = local_unit(q)
        if loc is None:
            return True
        got = unit_memo.get(loc[0])
        if got is None:
            got = unit_memo[loc[0]] = unit_counts(q, *loc)
        return got

    wrapper_memo = {}

    def wrapper_counts(d):
        """Round 32: whether the output of the shell -c wrapper of delegate `d` reaches a store write: the
        wrapper's own redirection or substitution writes a store path, another command of its pipeline writes
        one, it lies in an unreliable stream or a compound whose output is redirected or piped, or its pipeline,
        the wrapper taken as a read, is not a pure read (a staging one only when a store write names a file it
        writes). The store writes of the string itself are judged by the inner analysis, so they are set aside
        (a string with its own store write does not, by that alone, make every literal in it data)."""
        pl = pipelines()
        budget.spend(1)  # round 33: constant work per wrapper, from the pipeline aggregates (see pipelines)
        s, k, a = d[4], d[5], d[6]
        u = pl["tok_unit"][k] if s == top and k < len(pl["tok_unit"]) else pl["unit_at"](fspans[s][0])
        if pl["st"].unreliable or pl["st"].tainted[u]:
            return True
        ds = pl["d_store"][u]
        if pl["own"][u] or (ds and (pl["d_multi"][u] or ds[0] != a)):  # a delegated store write not this one's
            return True
        pid = pl["pipe"][u]
        if pl["agg"][pid][3] - pl["t_store"][u] > 0:  # another unit of its pipeline has a store write
            return True
        cls = pl["klass"](pid, u)
        if cls == "read":
            return False
        if cls == "stage":
            return pid in live()
        return True

    def unit_counts(q, a, b):
        """Whether the literals of the top-level simple command of tokens a..b (holding offset q) are data of a
        store write (see _analyze)."""
        if local_store(a, b):
            return True
        pl = pipelines()
        u = pl["unit_at"](q)
        if pl["st"].unreliable or pl["st"].tainted[u]:
            return True
        cls = pl["pclass"][pl["pipe"][u]]
        if cls == "read":
            return False
        if cls == "stage":
            return pl["pipe"][u] in live()
        return True

    return _Analysis(True, counts, targets_out, unknown_out, words_out)


def bash_future(cmd, now_us, cwd=None):
    """Future observed-time literals that are DATA of a Bash command's store write (else []; see _analyze).
    `cwd` is the payload's working directory, against which relative write targets resolve (round 31). Round
    31: the analysis runs under BASH_WORK_BUDGET; a command that spends it is allowed (fail open)."""
    if _STAMP_RE.search(cmd) is None:
        return []  # no literal at all: nothing to deny, and the tokenizer need not run
    start = frozenset((os.path.normpath(cwd) if isinstance(cwd, str) and os.path.isabs(cwd) else None,))
    try:
        an = _analyze(cmd, start, _store_ctx(), _Budget(BASH_WORK_BUDGET))
        if not an.store:
            return []
        bad, seen, cache, base = [], set(), {}, 0
        for line in cmd.split("\n"):
            for lit in future_on_line(line, now_us, base, lambda p: not an.counts(p), cache):
                if lit not in seen:
                    seen.add(lit)
                    bad.append(lit)
            base += len(line) + 1
        return bad
    except _Exhausted:
        return []  # round 31: the work budget ran out: fail open, as for any input the hook cannot evaluate


def _s(value):
    return value if isinstance(value, str) else ""


# every literal _STAMP_RE matches starts with this 16-character prefix, and none is longer than LITERAL_MAX_LEN:
# 16, plus `:SS` (3), plus `.` and 9 fraction digits (10), plus a 7-character zone (` ABCDEF` or ` +HH:MM`)
_LIT_PREFIX_RE = re.compile(r"(?=\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2})")
LITERAL_MIN_LEN, LITERAL_MAX_LEN = 16, 36


def _literal_substrings(text):
    """The set of substrings of `text` that COULD equal a literal: from every position where the literal prefix
    starts, each length from LITERAL_MIN_LEN to LITERAL_MAX_LEN. `lit in _literal_substrings(text)` is exactly
    `lit in text` for any literal _STAMP_RE matched (round 26), in time linear in `text`, once per text, instead
    of a full rescan of `text` per literal."""
    held = set()
    for m in _LIT_PREFIX_RE.finditer(text):
        p = m.start()
        held.update(text[p:p + n] for n in range(LITERAL_MIN_LEN, min(LITERAL_MAX_LEN, len(text) - p) + 1))
    return held


def evaluate(payload, now):
    """Return a list of offending future literals (empty = allow). `now` is an aware datetime. Round 31: the
    store roots are read once for the whole evaluation (_StoreCtx). With no store root configured the hook is
    inert: nothing is evaluated and the call is allowed."""
    global _ACTIVE
    _ACTIVE = _StoreCtx()
    try:
        if not _ACTIVE.roots:
            return []
        return _evaluate(payload, now)
    finally:
        _ACTIVE = None


def _evaluate(payload, now):
    now_us = (now - _EPOCH_UTC) // _ONE_US
    tool = payload.get("tool_name")
    ti = payload.get("tool_input") or {}
    if not isinstance(ti, dict):
        return []
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
    if tool in ("Write", "Edit", "MultiEdit"):
        fp = ti.get("file_path")
        if not under_store(fp, cwd):
            return []
        if not os.path.isabs(fp):
            fp = os.path.join(cwd, fp)
        original, whole = read_existing(fp, EXISTING_MAX_BYTES)
        if tool == "Write":
            return future_in_changed_lines(_s(ti.get("content")), original, now_us)
        if tool == "Edit":
            edits = [(_s(ti.get("old_string")), _s(ti.get("new_string")), ti.get("replace_all") is True)]
        else:
            edits = [(_s(e.get("old_string")), _s(e.get("new_string")), e.get("replace_all") is True)
                     for e in (ti.get("edits") or []) if isinstance(e, dict)]
        if whole:
            result = apply_edits(original, edits)
            # an edit that cannot apply to the WHOLE file makes the tool itself fail: nothing is written
            return [] if result is None else future_in_changed_lines(result, original, now_us)
        # not read whole (over the cap, unreadable, not regular): no reconstruction is possible, so each
        # fragment is checked on its own lines with no table context; a time-only fragment here is a disclosed miss
        # round 24: an UNREADABLE target (not read at all: "" with whole False, unlike an over-cap prefix) fails
        # OPEN except for what the new content itself introduces: a literal already in the edit's old_string (text
        # the file held) is not counted, so only a future literal the new_string brings in can deny
        unreadable = original == ""
        bad, seen = [], set()
        for old, new, _every in edits:
            held = None  # round 26: old's literal-shaped substrings, indexed once per edit (was `lit in old` per literal)
            for lit in future_in_changed_lines(new, old, now_us, tables=False, fences=False):
                if unreadable:
                    if held is None:
                        held = _literal_substrings(old)
                    if lit in held:
                        continue
                if lit not in seen:
                    seen.add(lit)
                    bad.append(lit)
        return bad
    if tool == "Bash":
        cmd = ti.get("command")
        return bash_future(cmd, now_us, cwd) if isinstance(cmd, str) else []
    return []


def _emit_line(text, *stream):
    """Write one line to `stream` (default stdout) and flush it. An output error fails OPEN (round 24): it is
    swallowed, and the stream's descriptor is pointed at /dev/null so the interpreter's exit flush cannot fail
    either, so the hook still exits 0. If that rescue fails too, the process ends at once with os._exit(0)
    (no flush is retried), since any later write or exit flush could fail the hook."""
    # a line meant for another stream (stderr) is written there or dropped, NEVER sent to stdout, the hook's
    # protocol channel: sys.stderr is None when descriptor 2 was closed at startup, and a None default once
    # read that as stdout
    s = stream[0] if stream else sys.stdout
    if s is None:
        return False
    try:
        print(text, file=s, flush=True)
        return True
    except Exception:
        try:
            fd = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(fd, s.fileno())
            finally:
                os.close(fd)
        except Exception:
            os._exit(0)
        return False


def _deny(bad, now):
    local = now.astimezone()
    _emit_line(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "Blocked: this write to a durable store carries observed-time literal(s) dated in the FUTURE: "
                + ", ".join(repr(b) for b in bad[:MAX_REPORTED])
                + (f" and {len(bad) - MAX_REPORTED} more" if len(bad) > MAX_REPORTED else "")
                + f". The real clock now reads {now.strftime('%Y-%m-%dT%H:%M:%SZ')} "
                f"({local.strftime('%Y-%m-%d %H:%M:%S')} {local.tzname()}). A record timestamp is read from the "
                "clock, never composed: use $(date -u +%Y-%m-%dT%H:%M:%SZ) in a shell write, or run `date -u` and "
                "copy its output. A genuinely scheduled value is allowed when a schedule word immediately precedes "
                "it (due, deadline, expires, until, through, valid, next run, scheduled for, not before, eta, "
                "planned, target date, by). A Bash command is checked lexically: a command whose write TARGETS a "
                "store path (a `>` redirection target, a tee operand, a sed/perl -i file, a cp/mv/install "
                "destination, dd of=, or a truncate, ed, or ex file; a relative path is resolved against the cwd "
                "and any cd before it) is denied when a future literal outside a $(date ...) substitution is DATA "
                "of that write (in its own command, here-document, here-string, or pipeline, or a compound whose "
                "output it redirects); a comment, an unrelated read-only search, or a write elsewhere is allowed."
            ),
        }
    }))
    return 0


def _cfg(name, env=None):
    """AIQT_<name> primary; the legacy ORCH_<name> spelling is accepted as a fallback."""
    env = os.environ if env is None else env
    v = env.get("AIQT_" + name)
    return v if v is not None else env.get("ORCH_" + name)


def _is_worker(env=None):
    """True in a subordinate worker process: AIQT_HOOKS_WORKER=1. Kept identical across the three hooks
    (python3 -I forbids a sibling import)."""
    env = os.environ if env is None else env
    if env.get("AIQT_HOOKS_WORKER") == "1":
        return True
    return env.get("ORCH_WORKER") == "1" or "ORCH_VERIFY_OWNER" in env  # legacy spellings

def _sibling_or_skip(name, env=None):
    """Self-test helper, kept identical across the three hooks: the path of sibling hook `name` beside this file.
    A genuinely absent sibling (os.lstat raises FileNotFoundError, nothing broader) SKIPS the calling test with a
    message naming it, so a single-hook install self-tests clean; with env AIQT_HOOKS_REQUIRE_SIBLINGS=1 the
    absence FAILS the test instead, so a repository gate never skips parity silently. Any other error (an
    unreadable directory, say) propagates, and a sibling that exists but cannot be loaded fails when it is read,
    so only a genuine absence ever skips."""
    import unittest
    env = os.environ if env is None else env
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    try:
        os.lstat(path)
    except FileNotFoundError:
        if env.get("AIQT_HOOKS_REQUIRE_SIBLINGS") == "1":
            raise AssertionError(f"sibling hook {name} is absent ({path}) and AIQT_HOOKS_REQUIRE_SIBLINGS=1 "
                                 "requires it") from None
        raise unittest.SkipTest(f"sibling hook {name} is absent (a standalone install); set "
                                "AIQT_HOOKS_REQUIRE_SIBLINGS=1 to require it") from None
    return path


def _wall_clock_asserts(source, exempt=()):
    """Self-test helper, kept identical across the three hooks: [(function, line)] of every assertion in
    `source` whose operand is a TIME figure, so no test's verdict rests on a wall-clock bound, which depends on
    the host's speed. A time figure is a clock reading (time.time, perf_counter, monotonic, process_time,
    thread_time, clock_gettime, or an _ns variant; datetime.now, utcnow, or today), a result of a timing
    harness (run_timed, growth_in_child), a name assigned from one (a for target or unpacking included), or
    arithmetic, a comparison, a subscript, an attribute, min, max, abs, sum, int, float, round, or a method
    call on one; a quotient of two time figures is a dimensionless ratio and may be bounded. A tuple or list
    literal assigned to a tuple or list target is matched element by element, so only the names that receive
    a time figure are tainted, and a shape it cannot match (a starred element, a length mismatch) taints
    nothing, the not-flagging direction. A clock read through an alias
    counts too: a module alias (`import time as t`, `import datetime as d`), an imported one (`from time import
    X as Y`, or `*`; `from datetime import datetime as Y`), and an assigned one (a plain `name = time.X` or
    `name = X` of a clock or alias), each bound at module level or within the function. An assertion's operands
    are its leading positional arguments and every keyword argument but msg. Every assertion in the source is
    scanned, under its enclosing test_ function (else its innermost function); `exempt` names the hang-guard
    tests, whose bound on elapsed time is their point. Residual (disclosed): the taint is by name within one
    test_ function and its nested functions, so a time figure passed through a container mutation, a global, a
    call to another helper, or a harness this list does not name escapes the scan; so does one routed through
    an expression form the scan does not follow: an assignment expression (walrus) in an asserted operand, a
    dict literal, a container built by a comprehension or filled by a store into a subscript, or any other
    routing not listed above; so does the time figure in a starred or mismatched unpacking; a clock this list does not
    name (os.times, date.today, time.localtime or gmtime, a third-party clock, a file's mtime) escapes too, as
    does an alias bound any other way (an attribute, a tuple target, getattr, a module or class assigned to a
    name); a subprocess timeout is not an assertion and is not scanned."""
    import ast
    clocks = {"perf_counter", "perf_counter_ns", "monotonic", "monotonic_ns", "process_time", "process_time_ns",
              "thread_time", "thread_time_ns", "clock_gettime", "clock_gettime_ns", "run_timed", "growth_in_child"}
    stdclocks = {"time", "time_ns", "perf_counter", "perf_counter_ns", "monotonic", "monotonic_ns",
                 "process_time", "process_time_ns", "thread_time", "thread_time_ns", "clock_gettime",
                 "clock_gettime_ns"}
    dtclocks = ("now", "utcnow", "today")
    single = {"assertTrue", "assertFalse", "assertIsNone", "assertIsNotNone"}

    def dtclass(node, al):  # a reference to the datetime class: a class alias, or <datetime module>.datetime
        if isinstance(node, ast.Name):
            return node.id in al["dtclass"]
        return isinstance(node, ast.Attribute) and node.attr == "datetime" and \
            isinstance(node.value, ast.Name) and node.value.id in al["datetime"]

    def timed(node, names, al):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                if f.id in clocks or f.id in al["clock"]:
                    return True
                return f.id in ("min", "max", "abs", "sum", "int", "float", "round") and any(
                    timed(a, names, al) for a in node.args)
            if isinstance(f, ast.Attribute):
                if f.attr in ("time", "time_ns"):
                    return isinstance(f.value, ast.Name) and f.value.id in al["time"]
                if f.attr in dtclocks and dtclass(f.value, al):
                    return True
                return f.attr in clocks or timed(f.value, names, al)
            return False
        if isinstance(node, ast.Name):
            return node.id in names
        if isinstance(node, ast.BinOp):
            left, right = timed(node.left, names, al), timed(node.right, names, al)
            return left != right if isinstance(node.op, ast.Div) else left or right
        if isinstance(node, ast.Compare):
            return any(timed(x, names, al) for x in [node.left] + node.comparators)
        if isinstance(node, ast.BoolOp):
            return any(timed(x, names, al) for x in node.values)
        if isinstance(node, ast.UnaryOp):
            return timed(node.operand, names, al)
        if isinstance(node, (ast.Subscript, ast.Starred, ast.Attribute)):
            return timed(node.value, names, al)
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return any(timed(e, names, al) for e in node.elts)
        if isinstance(node, ast.IfExp):
            return timed(node.body, names, al) or timed(node.orelse, names, al)
        return False

    def targets(node):
        if isinstance(node, ast.Name):
            yield node.id
        elif isinstance(node, (ast.Tuple, ast.List)):
            for e in node.elts:
                yield from targets(e)
        elif isinstance(node, ast.Starred):
            yield from targets(node.value)

    def split(target, value):  # an assignment's (target, value) pairs, a tuple or list literal matched in step
        if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)):
            if len(target.elts) == len(value.elts) and not any(
                    isinstance(e, ast.Starred) for e in target.elts + value.elts):
                for t, v in zip(target.elts, value.elts):
                    yield from split(t, v)
            return  # otherwise a shape it cannot match: nothing is tainted, the not-flagging direction
        yield target, value

    def aliases(node, al):  # the (kind, name) aliases node binds; kind: time, datetime (modules), dtclass, clock
        if isinstance(node, ast.Import):
            return [(a.name, a.asname or a.name) for a in node.names if a.name in ("time", "datetime")]
        if isinstance(node, ast.ImportFrom) and node.module == "time" and not node.level:
            bound = []
            for a in node.names:
                if a.name == "*":
                    bound += [("clock", c) for c in stdclocks]
                elif a.name in stdclocks:
                    bound.append(("clock", a.asname or a.name))
            return bound
        if isinstance(node, ast.ImportFrom) and node.module == "datetime" and not node.level:
            return [("dtclass", a.asname or "datetime") for a in node.names if a.name in ("datetime", "*")]
        if isinstance(node, ast.Assign) and (
                isinstance(node.value, ast.Name) and (node.value.id in clocks or node.value.id in al["clock"]) or
                isinstance(node.value, ast.Attribute) and node.value.attr in stdclocks and
                isinstance(node.value.value, ast.Name) and node.value.value.id in al["time"] or
                isinstance(node.value, ast.Attribute) and node.value.attr in dtclocks and
                dtclass(node.value.value, al)):
            return [("clock", t.id) for t in node.targets if isinstance(t, ast.Name)]
        return []

    tree, parent, cache = ast.parse(source), {}, {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    def scope(node):  # the enclosing test_ function, else the innermost function (None at module level)
        inner, up = None, parent.get(node)
        while up is not None:
            if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if up.name.startswith("test_"):
                    return up
                inner = inner or up
            up = parent.get(up)
        return inner

    def bind(nodes, al):  # add the aliases nodes bind to al, to a fixed point; True if any was new
        grew, changed = False, True
        while changed:
            changed = False
            for node in nodes:
                for kind, name in aliases(node, al):
                    if name not in al[kind]:
                        al[kind].add(name)
                        grew = changed = True
        return grew

    binders = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign))]
    top = {"time": {"time"}, "datetime": {"datetime"}, "dtclass": {"datetime"}, "clock": set()}
    bind([node for node in binders if scope(node) is None], top)

    def tainted(fn):  # (names, al): the time figures and aliases fn (nested functions included) binds
        if fn not in cache:
            names, al = set(), {kind: set(bound) for kind, bound in top.items()}
            nodes = list(ast.walk(fn))
            changed = True
            while changed:
                changed = bind(nodes, al)
                for node in nodes:
                    if isinstance(node, ast.Assign):
                        pairs = [pair for t in node.targets for pair in split(t, node.value)]
                    elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.NamedExpr)) and \
                            node.value is not None:
                        pairs = [(node.target, node.value)]
                    elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                        pairs = [(node.target, node.iter)]
                    elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                        pairs = [(node.optional_vars, node.context_expr)]
                    else:
                        continue
                    for target, value in pairs:
                        new = set(targets(target)) - names if timed(value, names, al) else set()
                        if new:
                            names |= new
                            changed = True
            cache[fn] = names, al
        return cache[fn]

    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            operands = [node.test]
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and \
                node.func.attr.startswith("assert"):
            operands = node.args[:1] if node.func.attr in single else node.args[:2]
            operands = operands + [k.value for k in node.keywords if k.arg != "msg"]
        else:
            continue
        fn = scope(node)
        name = fn.name if fn is not None else "<module>"
        names, al = tainted(fn) if fn is not None else (set(), top)
        if name not in exempt and any(timed(x, names, al) for x in operands):
            found.append((name, node.lineno))
    return sorted(found, key=lambda item: item[1])


def _wall_clock_alias_fixtures():
    """Self-test data, kept identical across the three hooks: (bad, flagged, good) for _wall_clock_asserts.
    `bad` bounds a time figure read through each alias form, through keyword operands, and from each added
    clock (datetime.now, utcnow, today; clock_gettime), and every function it names in `flagged` must be
    flagged; `good` uses the same alias forms and clocks only for a hang-guard timeout, an assertion on a count,
    a msg keyword, a ratio, a datetime constructor, or a name that is an alias only in another function, and
    nothing in it may be flagged."""
    bad = ("import time as tm\nfrom time import perf_counter as clock\ntick = tm.monotonic_ns\n"
           "import datetime as dt\n"
           "def test_i(self):\n    t = clock()\n    self.assertLess(clock() - t, 0.5)\n"
           "def test_j(self):\n    my_time = time.time\n    t0 = my_time()\n    self.assertLess(my_time() - t0, 1)\n"
           "def test_k(self):\n    t0 = tm.time()\n    self.assertLessEqual(tm.time() - t0, 1)\n"
           "def test_l(self):\n    self.assertLess(tick() - start, 10)\n"
           "def test_m(self):\n    from time import process_time as cpu\n    c0 = cpu()\n"
           "    assert cpu() - c0 < 2\n"
           "def test_n(self):\n    c1 = clock\n    c2 = c1\n    self.assertGreater(1.0, c2() - base)\n"
           "def test_o(self):\n    from time import time\n    self.assertTrue(time() - t0 < 1)\n"
           "def test_p(self):\n    from time import *\n    self.assertLess(time_ns() - t0, 5)\n"
           "def test_q(self):\n    t = time.monotonic()\n    self.assertLess(a=time.monotonic()-t, b=0.5)\n"
           "def test_r(self):\n    self.assertTrue(expr=time.perf_counter() - t0 < 1, msg='slow')\n"
           "def test_s(self):\n    now = tm.perf_counter\n    elapsed = now() - t0\n"
           "    self.assertLessEqual(first=elapsed, second=2)\n"
           "def test_t(self):\n    pc = perf_counter\n    self.assertLess(pc() - t, 1)\n"
           "def test_ba(self):\n    t0 = datetime.datetime.now()\n"
           "    self.assertLess((datetime.datetime.now() - t0).total_seconds(), 2.0)\n"
           "def test_bb(self):\n    from datetime import datetime as DT\n    t0 = DT.utcnow()\n"
           "    self.assertLess((DT.utcnow() - t0).total_seconds(), 2)\n"
           "def test_bc(self):\n    stamp = dt.datetime.now\n    t0 = stamp()\n"
           "    self.assertLess((stamp() - t0).seconds, 2)\n"
           "def test_bd(self):\n    t0 = time.clock_gettime(time.CLOCK_MONOTONIC)\n"
           "    self.assertLess(time.clock_gettime(time.CLOCK_MONOTONIC) - t0, 1)\n"
           "def test_be(self):\n    from time import clock_gettime_ns as cg\n    t0 = cg(1)\n"
           "    assert cg(1) - t0 < 10\n"
           "def test_bf(self):\n    from datetime import *\n    self.assertLess((datetime.today() - t0).seconds, 5)\n"
           "def test_bj(self):\n    elapsed, n = clock() - t0, 3\n    self.assertLess(elapsed, 1)\n"
           "def test_bk(self):\n    t0 = time.monotonic()\n    e = int((time.monotonic() - t0) * 1000)\n"
           "    self.assertLess(e, 500)\n")
    flagged = ["test_i", "test_j", "test_k", "test_l", "test_m", "test_n", "test_o", "test_p", "test_q", "test_r",
               "test_s", "test_t", "test_ba", "test_bb", "test_bc", "test_bd", "test_be", "test_bf", "test_bj",
               "test_bk"]
    good = ("import time as tm\nfrom time import perf_counter as clock\ntick = tm.monotonic\n"
            "import datetime as dt\n"
            "def test_u(self):\n    deadline = clock() + HANG_TIMEOUT\n    out = run(timeout=deadline - clock())\n"
            "    proc.wait(timeout=tick() + 1)\n    self.assertEqual(len(out), 3)\n"
            "def test_v(self):\n    my_time = tm.time\n    proc.wait(timeout=my_time() + 30)\n"
            "    self.assertEqual(proc.returncode, 0, msg=my_time())\n"
            "def test_w(self):\n    from time import process_time as cpu\n    limit = cpu() + 5\n"
            "    calls = count_calls(limit)\n    self.assertEqual(first=calls, second=2, msg=cpu() - limit)\n"
            "def test_x(self):\n    from time import time\n    subprocess.run(cmd, timeout=time() + 5)\n"
            "    self.assertEqual(n_lines, 4)\n"
            "def test_y(self):\n    my_time = len\n    self.assertEqual(my_time([1, 2]), 2)\n"
            "def test_z(self):\n    a, b = clock(), clock()\n    self.assertLess(b / max(a, 1e-3), LIMIT)\n"
            "def test_bg(self):\n    stamp = dt.datetime.now(dt.timezone.utc)\n"
            "    self.assertEqual(len(render(stamp)), 17)\n    self.assertEqual(dt.time(12, 0).hour, 12)\n"
            "    self.assertEqual(datetime.date(2026, 9, 24).day, 24)\n"
            "def test_bh(self):\n    t0 = time.clock_gettime(time.CLOCK_MONOTONIC)\n"
            "    lines = run(timeout=HANG_TIMEOUT - (time.clock_gettime(time.CLOCK_MONOTONIC) - t0))\n"
            "    self.assertEqual(lines.count, 2)\n"
            "def test_bi(self):\n    started, count = clock(), 3\n    self.assertEqual(count, 3)\n"
            "    [(t1, n), m] = [(tick(), 4), 5]\n    self.assertEqual(n + m, 9)\n"
            "    first, *rest = clock(), 1, 2\n    self.assertEqual(rest, [1, 2])\n")
    return bad, flagged, good


def main(argv):
    try:
        self_test = len(argv) > 1 and argv[1] == "--self-test"
    except Exception:
        return 0  # an unusable argv fails open: exit 0 at once, evaluating nothing
    if self_test:
        return _self_test()
    try:
        if _is_worker():
            # round 24: the skip is no longer silent (stderr only, so worker output is never distorted)
            _emit_line("future-stamp-write: skipped, worker marker present (AIQT_HOOKS_WORKER=1 or a legacy "
                       "spelling)", sys.stderr)
            return 0
        buf = getattr(sys.stdin, "buffer", None)  # bytes; a text stream (the self-test) has no buffer
        payload = json.loads(buf.read() if buf is not None else sys.stdin.read())
        if not isinstance(payload, dict):
            return 0
        now = datetime.datetime.now(UTC)
        bad = evaluate(payload, now)
    except Exception:
        return 0  # fail-open
    if bad:
        return _deny(bad, now)
    return 0


def _self_test():
    import importlib.util
    import inspect
    import io
    import shutil
    import subprocess
    import tempfile
    import unittest

    # round 26 (finding 6): timed runs execute in a child interpreter under a subprocess timeout, so a hang is
    # INTERRUPTED (subprocess.TimeoutExpired fails the test) rather than stalling the self-test before any bound
    # is checked. The child loads this file, pins the zone, sets the fixture store root, and defines `best`.
    # Every fixture path lives under one private temporary base: STORE is the fixture store root (set as
    # AIQT_STORE_ROOT in each test) and PROJ/repo a non-store directory beside it. Neither PROJ nor
    # anything under it is created, so a `cd` into it is to a missing directory, as for any unknown path.
    _BASE = tempfile.mkdtemp(prefix="fsw.", dir="/dev/shm" if os.path.isdir("/dev/shm") else None)
    PROJ = os.path.join(_BASE, "proj")
    STORE = os.path.join(PROJ, "private")
    HANG_TIMEOUT = 120  # seconds: far above the timed runs' own total (a few seconds), so only a hang reaches it
    # A timing verdict compares the same code with itself on the same host, never with a wall-clock figure (a
    # 2.0 s ceiling failed at 2.53 s on a slower CI runner). A GROWTH check (ratio in TIMED_PRELUDE) times one run
    # at GROWTH * n against GROWTH runs at n, so the two samples do the same work when the pass is linear and
    # span about the same time, and a preemption or load spike lands on both alike (a sample shorter than a
    # scheduler slice escaped the preemption a longer one took, observed at 22 times on a pinned, loaded CPU);
    # the sizes are interleaved, best of N. A PEER check bounds an adversarial command's time by an ORDINARY
    # command of about the same size that the analysis judges in full (see TIMED_PRELUDE).
    GROWTH = 8
    LINEAR_LIMIT = 2.0  # GROWTH growth: about 1 when linear, about GROWTH when quadratic
    FLAT_LIMIT = 3.0  # a pass independent of the varied size stays near 1; one linear in it grows by the step
    PEER_LIMIT = 4.0  # adversarial over ordinary: about 1 to 2 on the development host
    TIMED_PRELUDE = (
        "import importlib.util as u, datetime, json, os, time\n"
        "os.environ['TZ'] = 'EST5EDT,M3.2.0,M11.1.0'\n"
        "time.tzset()\n"
        "for k in ('ORCH_STORE_ROOT', 'CLAUDE_PROJECT_DIR'):\n"
        "    os.environ.pop(k, None)\n"
        "os.environ['AIQT_STORE_ROOT'] = %r\n"
        "s = u.spec_from_file_location('m', %r)\n"
        "m = u.module_from_spec(s)\n"
        "s.loader.exec_module(m)\n"
        "now = datetime.datetime(2026, 9, 23, 17, 45, tzinfo=datetime.timezone.utc)\n"
        "def best(n, run):\n"
        "    times = []\n"
        "    for _ in range(3):\n"
        "        t0 = time.monotonic()\n"
        "        run(n)\n"
        "        times.append(time.monotonic() - t0)\n"
        "    return min(times)\n"
        # round 32: a load spike during one size's runs skewed a growth ratio (one flake at load 34); the sizes
        # are now INTERLEAVED, best of `reps` each, so a spike lands on both sizes' samples alike
        "def interleaved(sizes, run, reps=5):\n"
        "    times = [[] for _ in sizes]\n"
        "    for _ in range(reps):\n"
        "        for n, out in zip(sizes, times):\n"
        "            t0 = time.monotonic()\n"
        "            run(n)\n"
        "            out.append(time.monotonic() - t0)\n"
        "    return [min(t) for t in times]\n"
        "def ratio(n, run, reps=5):\n"
        "    def same_work(k):\n"
        "        for _ in range(GROWTH * n // k):\n"
        "            run(k)\n"
        "    return interleaved((n, GROWTH * n), same_work, reps)\n"
        "def versus(subject, reference, reps=5):\n"
        "    return interleaved((0, 1), lambda k: (subject, reference)[k](), reps)\n"
        # the PEER reference: about 64 KiB of ordinary audit-log appends and a future write into `store`, which
        # the analysis judges in full (denied), so an adversarial command's time is compared with it
        "def ordinary(cwd, store):\n"
        "    c = ''.join(\"echo 'line %%d of the audit log' >> /dev/shm/x%%d\\n\" %% (i, i %% 7)\n"
        "                for i in range(1400))\n"
        "    c += \"printf 'hb 2099-01-01T00:00Z' > \" + store + '/state.md'\n"
        "    def run():\n"
        "        assert m.evaluate({'tool_name': 'Bash', 'tool_input': {'command': c}, 'cwd': cwd}, now)\n"
        "    return run\n"
        "GROWTH = %d\n") % (STORE, os.path.abspath(__file__), GROWTH)

    def run_timed(code, timeout):
        """Run `code` in a fresh isolated interpreter, killed at `timeout` seconds (raising TimeoutExpired); return
        the JSON its last stdout line prints. A non-zero exit raises AssertionError with its stderr."""
        r = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise AssertionError(f"timed child failed ({r.returncode}): {r.stderr}")
        return json.loads(r.stdout.strip().splitlines()[-1])

    class T(unittest.TestCase):
        def setUp(self):
            self._tz = os.environ.get("TZ")
            self._saved = {k: os.environ.pop(k, None) for k in ("AIQT_STORE_ROOT", "ORCH_STORE_ROOT",
                                                                 "CLAUDE_PROJECT_DIR")}
            os.environ["AIQT_STORE_ROOT"] = STORE  # the fixture store root (there is no default)
            os.environ["TZ"] = "EST5EDT,M3.2.0,M11.1.0"  # pinned zone, no tzdata needed
            time.tzset()
            base = "/dev/shm" if os.path.isdir("/dev/shm") else None
            self.tmp = tempfile.mkdtemp(prefix="clk.", dir=base)
            self.now = datetime.datetime(2026, 9, 23, 17, 45, 0, tzinfo=UTC)
            self.store = f"{PROJ}/private/state.md"

        def tearDown(self):
            shutil.rmtree(self.tmp, ignore_errors=True)
            for k, v in (("TZ", self._tz),) + tuple(self._saved.items()):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            time.tzset()

        def ev(self, tool, cwd=f"{PROJ}/repo", **ti):
            return evaluate({"tool_name": tool, "tool_input": ti, "cwd": cwd}, self.now)

        def store_file(self, text):
            fp = os.path.join(self.tmp, "state.md")
            with open(fp, "w") as f:
                f.write(text)
            os.environ["AIQT_STORE_ROOT"] = self.tmp
            return fp

        # -- Write / Edit / MultiEdit --
        def test_future_utc_literal_into_store_denied(self):
            self.assertEqual(self.ev("Write", file_path=self.store, content="hb: 2026-09-23T22:25:00Z"),
                             ["2026-09-23T22:25:00Z"])

        def test_future_local_literal_edit_denied(self):
            fp = self.store_file("a\n")
            self.assertTrue(self.ev("Edit", file_path=fp, old_string="a", new_string="[2026-09-23 14:10 EDT]"))

        def test_multiedit_future_denied(self):
            fp = self.store_file("x\ny\n")
            self.assertTrue(self.ev("MultiEdit", file_path=fp, edits=[{"old_string": "x", "new_string": "ok"},
                                                                      {"old_string": "y", "new_string": "at 2026-09-24T01:00Z"}]))

        def test_past_literal_passes(self):
            self.assertEqual(self.ev("Write", file_path=self.store, content="2026-09-23T17:44:30Z 2026-09-01 10:00"), [])

        def test_within_60s_slack_passes(self):
            self.assertEqual(self.ev("Edit", file_path=self.store, old_string="", new_string="2026-09-23T17:45:50Z"), [])

        def test_non_store_path_passes(self):
            self.assertEqual(self.ev("Write", file_path=f"{PROJ}/repo/x.md", content="2099-01-01T00:00Z"), [])

        def test_write_substitution_text_is_literal(self):
            self.assertEqual(self.ev("Write", file_path=self.store, content="$(echo 2099-01-01T00:00Z)"),
                             ["2099-01-01T00:00Z"])
            self.assertTrue(self.ev("Write", file_path=self.store, content="$(date -d 2099-01-01T00:00Z)"))

        def test_date_only_passes(self):
            self.assertEqual(self.ev("Write", file_path=self.store, content="due 2099-12-31, review 2027-01-01"), [])

        def test_offset_literal_resolved(self):
            self.assertEqual(self.ev("Write", file_path=self.store, content="2026-09-23T19:00+02:00"), [])
            self.assertTrue(self.ev("Write", file_path=self.store, content="2026-09-23T23:00+02:00"))

        def test_spaced_numeric_zone(self):
            # finding (codex r2): a space-separated numeric zone was dropped and the literal read as naive
            os.environ["TZ"] = "UTC0"
            time.tzset()
            self.assertEqual(self.ev("Write", file_path=self.store, content="[2026-09-23 22:45 +05]"), [])
            self.assertEqual(self.ev("Write", file_path=self.store, content="[2026-09-23 13:45 -09]"),
                             ["2026-09-23 13:45 -09"])

        def test_zone_abbreviations(self):
            self.assertEqual(self.ev("Write", file_path=self.store, content="[2026-09-23 17:45 UTC]"), [])
            self.assertTrue(self.ev("Write", file_path=self.store, content="[2026-09-23 22:25 UTC]"))
            os.environ["TZ"] = "JST-9"
            time.tzset()
            self.assertEqual(self.ev("Write", file_path=self.store, content="[2026-09-24 02:45 JST]"), [])
            self.assertTrue(self.ev("Write", file_path=self.store, content="[2026-09-24 07:25 JST]"))

        def test_unknown_zone_skipped(self):
            self.assertEqual(self.ev("Write", file_path=self.store, content="[2099-01-01 10:00 XYZT]"), [])

        def test_naive_literal_future_under_both_readings(self):
            self.assertTrue(self.ev("Write", file_path=self.store, content="hb: 2026-09-23 18:25"))
            self.assertEqual(self.ev("Write", file_path=self.store, content="hb: 2026-09-23 17:30"), [])

        def test_dst_fold_deterministic(self):
            now = datetime.datetime(2026, 11, 1, 5, 45, tzinfo=UTC)
            ev = lambda c: evaluate({"tool_name": "Write", "tool_input": {"file_path": self.store, "content": c},
                                     "cwd": "/"}, now)
            self.assertEqual(ev("hb [2026-11-01 01:30 EDT]"), [])
            self.assertTrue(ev("hb [2026-11-01 01:30 EST]"))

        def test_overflow_literal_isolated(self):
            self.assertEqual(self.ev("Write", file_path=self.store,
                                     content="0001-01-01T00:00+01:00\n9999-12-31T23:59-01:00 heartbeat 2099-01-01T00:00Z"),
                             ["9999-12-31T23:59-01:00", "2099-01-01T00:00Z"])  # integer arithmetic: no overflow

        def test_scheduled_keyword_allowed(self):
            for line in ("next_run: 2099-01-01T09:30:00Z", "Deadline 2099-01-01 09:00 UTC", "expires 2099-01-01T00:00Z",
                         "not-before: 2099-01-01T00:00Z", "ETA 2099-01-01T00:00Z", "done by 2099-01-01T00:00Z"):
                self.assertEqual(self.ev("Write", file_path=self.store, content=line), [], line)
            for line in ("metadata: 2099-01-01T00:00Z", "beta 2099-01-01T00:00Z", "Last-heartbeat: 2099-01-01T00:00Z"):
                self.assertTrue(self.ev("Write", file_path=self.store, content=line), line)

        def test_unchanged_line_carry_over_allowed(self):
            fp = self.store_file("hb: 2099-01-01T00:00Z\nother\n")
            self.assertEqual(self.ev("Write", file_path=fp, content="hb: 2099-01-01T00:00Z\nother\nnew line\n"), [])
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="other", new_string="changed"), [])

        def test_repurposed_timestamp_on_changed_line_denied(self):
            fp = self.store_file("deadline: 2099-01-01T00:00Z\n")
            self.assertTrue(self.ev("Write", file_path=fp, content="Last-heartbeat: 2099-01-01T00:00Z\n"))
            fp = self.store_file("hb: 2099-01-01T00:00Z\n")
            self.assertTrue(self.ev("Write", file_path=fp, content="hb: 2099-01-01T00:00Z\nhb: 2099-01-01T00:00Z\n"))

        def test_edit_fragments_reconstructed_on_whole_lines(self):
            # finding (codex r2): Edit checked the replacement fragment, not the changed file line
            fp = self.store_file("deadline: 2099-01-01T00:00Z\nheartbeat: 2026-09-23T17:45Z\n"
                                 "next_run: 2099-03-01T00:00Z\n")
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="deadline", new_string="heartbeat"),
                             ["2099-01-01T00:00Z"])
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="17:45", new_string="22:25"),
                             ["2026-09-23T22:25Z"])
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="2099-03-01", new_string="2099-04-01"), [])
            self.assertEqual(self.ev("MultiEdit", file_path=fp, edits=[
                {"old_string": "heartbeat: 2026-09-23T17:45Z", "new_string": "heartbeat: X"},
                {"old_string": "X", "new_string": "2026-09-23T23:00Z"}]), ["2026-09-23T23:00Z"])
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="99-0", new_string="99-1", replace_all=True), [])
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="T17:45Z", new_string="T17:45Z\nhb: 2099-02-02T00:00Z",
                                     replace_all=True), ["2099-02-02T00:00Z"])

        def test_edit_old_string_absent_on_whole_file_allowed(self):
            # finding (gemini r3 M2): a non-applying edit fell back to naked fragments (false deny); on a file
            # read whole the tool itself fails, so the call is allowed
            fp = self.store_file("a\n")
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="zzz", new_string="hb 2099-01-01T00:00Z"), [])
            fp = self.store_file("deadline: 2026-09-23T17:45Z\n")
            self.assertEqual(self.ev("MultiEdit", file_path=fp, edits=[
                {"old_string": "2026-09-23T17:45Z", "new_string": "2099-01-01T00:00Z"},
                {"old_string": "nope", "new_string": "y"}]), [])
            self.assertEqual(self.ev("Edit", file_path=os.path.join(self.tmp, "absent.md"), old_string="x",
                                     new_string="hb 2099-01-01T00:00Z"), [])

        def test_edit_beyond_read_cap_checks_fragment(self):
            # finding (codex r3 m): past the cap the hook cannot reconstruct; the fragment is checked on its own
            fp = self.store_file("x\n" * (EXISTING_MAX_BYTES // 2) + "hb: 2026-09-23T17:45Z\n")
            self.assertEqual(read_existing(fp, EXISTING_MAX_BYTES)[1], False)
            self.assertTrue(self.ev("Edit", file_path=fp, old_string="hb: 2026-09-23T17:45Z",
                                    new_string="hb: 2026-09-23T22:25Z"))
            self.assertTrue(self.ev("Edit", file_path=fp, old_string="zzz", new_string="hb: 2099-01-01T00:00Z"))
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="17:45", new_string="22:25"), [])  # disclosed
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="x", new_string="next run 2099-01-01T00:00Z"), [])
            fp2 = self.store_file("x\n" * (EXISTING_MAX_BYTES // 2 - 1))
            self.assertEqual(read_existing(fp2, EXISTING_MAX_BYTES)[1], True)

        def test_fifo_existing_content_does_not_block(self):
            fifo = os.path.join(self.tmp, "fifo")
            os.mkfifo(fifo)
            code = ("import importlib.util as u;s=u.spec_from_file_location('m',%r);m=u.module_from_spec(s);"
                    "s.loader.exec_module(m);print(repr(m.read_existing(%r, 10)))" % (os.path.abspath(__file__), fifo))
            r = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True,
                               timeout=HANG_TIMEOUT)  # the hang guard: a blocking FIFO open never returns
            self.assertEqual(r.stdout.strip(), "('', False)")

        def test_store_root_env_override(self):
            os.environ["AIQT_STORE_ROOT"] = self.tmp
            self.assertEqual(self.ev("Write", file_path=self.store, content="2099-01-01T00:00Z"), [])
            self.assertTrue(self.ev("Write", file_path=os.path.join(self.tmp, "a.md"), content="2099-01-01T00:00Z"))
            self.assertTrue(self.ev("Bash", command=f"echo 2099-01-01T00:00Z >> {self.tmp}/a.md"))
            self.assertEqual(self.ev("Bash", command=f"echo 2099-01-01T00:00Z >> {self.tmp}x/a.md"), [])
            # several roots joined by os.pathsep; an empty or relative entry is ignored
            os.environ["AIQT_STORE_ROOT"] = os.pathsep.join(("", "rel/dir", self.tmp, STORE))
            self.assertEqual(store_roots(), [os.path.normpath(self.tmp), STORE])
            self.assertTrue(self.ev("Write", file_path=self.store, content="2099-01-01T00:00Z"))

        def test_store_root_precedence_and_legacy_spelling(self):
            other = tempfile.mkdtemp(dir=self.tmp)
            del os.environ["AIQT_STORE_ROOT"]
            os.environ["ORCH_STORE_ROOT"] = other  # the legacy spelling is a fallback
            self.assertEqual(store_roots(), [other])
            self.assertTrue(self.ev("Write", file_path=os.path.join(other, "a.md"), content="2099-01-01T00:00Z"))
            os.environ["AIQT_STORE_ROOT"] = STORE  # AIQT_ beats ORCH_
            self.assertEqual(store_roots(), [STORE])
            self.assertEqual(self.ev("Write", file_path=os.path.join(other, "a.md"), content="2099-01-01T00:00Z"), [])
            self.assertTrue(self.ev("Write", file_path=self.store, content="2099-01-01T00:00Z"))
            os.environ["AIQT_STORE_ROOT"] = ""  # set but empty still beats ORCH_: no store, so inert
            self.assertEqual(store_roots(), [])
            self.assertEqual(self.ev("Write", file_path=os.path.join(other, "a.md"), content="2099-01-01T00:00Z"), [])
            self.assertEqual(_cfg("X", {"AIQT_X": "a", "ORCH_X": "o"}), "a")
            self.assertEqual(_cfg("X", {"AIQT_X": "", "ORCH_X": "o"}), "")
            self.assertEqual(_cfg("X", {"ORCH_X": "o"}), "o")
            self.assertIsNone(_cfg("X", {}))

        def test_no_store_configured_is_inert(self):
            # there is no default store: with no root configured (or only relative ones) nothing is checked, even
            # a project directory holding the files an earlier default treated as a store marker
            work = os.path.join(self.tmp, ".working")
            os.makedirs(work)
            for name in ("state.md", "session-lease.md"):
                with open(os.path.join(work, name), "w") as f:
                    f.write("lease\n")
            os.environ["CLAUDE_PROJECT_DIR"] = self.tmp
            for value in (None, "", "rel/store", os.pathsep):
                if value is None:
                    os.environ.pop("AIQT_STORE_ROOT", None)
                else:
                    os.environ["AIQT_STORE_ROOT"] = value
                self.assertEqual(store_roots(), [], value)
                fp = os.path.join(work, "x.md")
                self.assertFalse(under_store(fp), value)
                self.assertFalse(under_store(self.store), value)
                self.assertEqual(self.ev("Write", file_path=fp, content="hb 2099-01-01T00:00Z"), [], value)
                self.assertEqual(self.ev("Write", file_path=self.store, content="hb 2099-01-01T00:00Z"), [], value)
                self.assertEqual(self.ev("Bash", command=f"echo 2099-01-01T00:00Z >> {fp}"), [], value)
                self.assertEqual(self.ev("Bash", command=f"echo 2099-01-01T00:00Z >> {self.store}"), [], value)
                out = self.main_out({"tool_name": "Write", "cwd": self.tmp,
                                     "tool_input": {"file_path": fp, "content": "hb 2099-01-01T00:00Z"}})
                self.assertEqual(out, "", value)

        def test_relative_path_under_store_cwd(self):
            r = evaluate({"tool_name": "Write", "cwd": f"{PROJ}/private",
                          "tool_input": {"file_path": "notes.md", "content": "2099-01-01T00:00Z"}}, self.now)
            self.assertTrue(r)

        # -- Bash: the simple lexical rule --
        def test_bash_future_into_store_denied(self):
            for cmd in (f"echo 'Last-heartbeat-UTC: 2026-09-23T19:00:00Z' >> {PROJ}/private/state.md",
                        f"printf 'hb 2099-01-01T00:00Z' | tee -a {PROJ}/private/a.md",
                        f"sed -i 's/x/2099-01-01T00:00Z/' {PROJ}/private/a.md",
                        f"cp /dev/shm/2099-01-01T00:00Z {PROJ}/private/",
                        f"printf '%s' '$(echo 2099-01-01T00:00Z)' > {PROJ}/private/log.md"):
                self.assertTrue(self.ev("Bash", command=cmd), cmd)

        def test_bash_parse_evasions_now_denied(self):
            # findings (codex/gemini r2): comment heredoc, wrapper options, and $(echo ...) evaded parsing
            for cmd in (f"# <<EOF\nprintf '%s\\n' 'hb: 2099-01-01T00:00Z' > {PROJ}/private/s.md",
                        f"printf '%s\\n' '<<EOF'\nprintf 'hb: 2099-01-01T00:00Z' > {PROJ}/private/s.md",
                        f"echo 2099-01-01T00:00Z | env -i tee {PROJ}/private/s.md",
                        f"echo 2099-01-01T00:00Z | sudo -n tee {PROJ}/private/s.md",
                        f"echo $(echo 2099-01-01T00:00Z) > {PROJ}/private/a.md",
                        f"echo \"hb: `echo 2099-01-01T00:00Z`\" > '{PROJ}/private/a.md'",
                        f"dd of={PROJ}/private/a.md <<< 2099-01-01T00:00Z"):
                self.assertTrue(self.ev("Bash", command=cmd), cmd)

        def test_bash_documented_false_positives(self):
            # round 24 (item 3): these were documented false positives (the write was not bound to the store
            # path, so a write ELSEWHERE was denied); the write is now bound to its target, so they are allowed
            for cmd in (f"cp -t /dev/shm/ {PROJ}/private/report-2099-01-01T00:00Z.md",
                        f"grep 2099-01-01T00:00Z {PROJ}/private/s.md > /dev/shm/out",
                        f"echo 'hb 2099-01-01T00:00Z' >> {PROJ}/repo/n.md # cf {PROJ}/private/old.md"):
                self.assertTrue(bash_writes(cmd), cmd)
                self.assertEqual(self.ev("Bash", command=cmd), [], cmd)
            # round 13: a write-command name as an ARGUMENT is no longer a write (it was a disclosed false positive)
            self.assertEqual(self.ev("Bash", command=f"grep tee {PROJ}/private/s.md 2099-01-01T00:00Z"), [])

        def test_r7_read_only_store_inspection_allowed(self):
            # peer finding (MEDIUM): a read-only command naming a store path was denied; the old
            # documented false positives for a read (grep, a quoted `>` argument) now pass
            for cmd in (f"rg -n '2099-01-01T00:00Z' {PROJ}/private/pending-decisions.md",
                        f"grep -c 2099-01-01T00:00Z {PROJ}/private/s.md",
                        f"grep -n 2099-01-01T00:00Z {PROJ}/private/s.md 2>/dev/null | head -5",
                        f"rg 2099-01-01T00:00Z {PROJ}/private/ 2>&1 | wc -l",
                        f"cat {PROJ}/private/defect.md | grep '2099-01-01T00:00Z >> x'",
                        f"printf '%s\\n' '>' {PROJ}/private/s.md 2099-01-01T00:00Z",
                        f"sed -n '/2099-01-01T00:00Z/p' {PROJ}/private/s.md",
                        f"ls {PROJ}/private/ # 2099-01-01T00:00Z > x",
                        f"rg -i 2099-01-01T00:00Z {PROJ}/private/s.md | sed 's/a/b/'"):
                self.assertEqual(self.ev("Bash", command=cmd), [], cmd)

        def test_r7_write_indicators_still_denied(self):
            for cmd in (f"echo 2099-01-01T00:00Z >> {PROJ}/private/s.md",
                        f"echo 2099-01-01T00:00Z>{PROJ}/private/s.md",
                        f"echo 2099-01-01T00:00Z 1> {PROJ}/private/s.md",
                        f"echo 2099-01-01T00:00Z &> {PROJ}/private/s.md",
                        f"echo 2099-01-01T00:00Z >| {PROJ}/private/s.md",
                        f"echo 2099-01-01T00:00Z >&{PROJ}/private/s.md",
                        f"printf 'hb 2099-01-01T00:00Z' | tee -a {PROJ}/private/s.md",
                        f"printf 'hb 2099-01-01T00:00Z' | '/usr/bin/tee' {PROJ}/private/s.md",
                        f"cp /dev/shm/x {PROJ}/private/2099-01-01T00:00Z.md",
                        f"mv /dev/shm/2099-01-01T00:00Z.md {PROJ}/private/",
                        f"install -m 600 /dev/shm/2099-01-01T00:00Z {PROJ}/private/x",
                        f"truncate -s 0 {PROJ}/private/2099-01-01T00:00Z.md",
                        f"sed -i.bak 's/x/2099-01-01T00:00Z/' {PROJ}/private/s.md",
                        f"sed -Ei 's/x/2099-01-01T00:00Z/' {PROJ}/private/s.md",
                        f"sed --in-place 's/x/2099-01-01T00:00Z/' {PROJ}/private/s.md",
                        f"perl -pi -e 's/x/2099-01-01T00:00Z/' {PROJ}/private/s.md",
                        f"echo 'unterminated 2099-01-01T00:00Z {PROJ}/private/s"):
                self.assertTrue(self.ev("Bash", command=cmd), cmd)
            # sed/perl without an in-place option, and a -i on another command, are reads
            self.assertFalse(bash_writes(f"sed 's/a/b/' {PROJ}/private/s.md"))
            self.assertFalse(bash_writes("grep -i x f; sed 's/a/b/' g"))
            self.assertTrue(bash_writes("grep x f; sed -i 's/a/b/' g"))
            self.assertFalse(bash_writes("cmd 2>&1 >&2 1>&- | cat"))
            self.assertFalse(bash_writes("echo \\> x"))
            self.assertTrue(bash_writes("cat f >"))

        def test_r7_bash_writes_is_linear(self):
            # round 32: best of 3 in a child under the hang ceiling (one in-process run flaked on a loaded host).
            # The verdict is the growth from n to GROWTH * n (up to the former sizes), not a wall-clock ceiling
            code = TIMED_PRELUDE + (
                "def run(n):\n"
                "    for cmd in (\"'\" + 'a' * (4 * n), 'x ' * (2 * n), '2>&1 ' * n, '\\\\' * (4 * n),\n"
                "                \"$'\\\\'\" * n):\n"
                "        m.bash_writes(cmd)\n"
                "print(json.dumps(ratio(12500, run, 3)))\n")
            small, large = run_timed(code, HANG_TIMEOUT)
            self.assertLess(large / max(small, 1e-3), LINEAR_LIMIT, (small, large))

        def test_bash_date_substitution_passes(self):
            for cmd in ('echo "hb: $(date -u +%Y-%m-%dT%H:%M:%SZ) $(date -d \'2099-01-01 10:00\')" >> '
                        f"{PROJ}/private/s.md",
                        f"echo `date -d '2099-01-01 10:00'` >> {PROJ}/private/s.md",
                        f"echo \"$(TZ=UTC date -d '2099-01-01T00:00Z' +%s)\" > {PROJ}/private/s.md",
                        f"echo $( /bin/date -d \"$(printf x) 2099-01-01T00:00Z\" ) > {PROJ}/private/s.md"):
                self.assertEqual(self.ev("Bash", command=cmd), [], cmd)
            self.assertTrue(self.ev("Bash", command=f"echo $(date -u) 2099-01-01T00:00Z > {PROJ}/private/s.md"))
            self.assertTrue(self.ev("Bash", command=f"echo $(date -u 2099-01-01T00:00Z > {PROJ}/private/s.md"))

        def test_bash_without_store_token_allowed(self):
            # round 31: `echo <future> > notes.md` with the cwd in the store (a documented miss until round 30) is
            # now resolved against the cwd and denied (test_r31_relative_targets_resolve_against_cwd_and_cd)
            for cwd, cmd in ((f"{PROJ}/private", "printf '%s' 2099-01-01T00:00Z > /dev/shm/example"),
                             (f"{PROJ}/repo", "echo 2099-01-01T00:00Z > notes.md"),
                             ("/", f"echo 2099-01-01T00:00Z > {PROJ}/private.bak/x"),
                             ("/", f"echo 2099-01-01T00:00Z > /x{PROJ}/private/y"),
                             ("/", f"cat {PROJ}/private/s.md; echo past 2026-09-01T00:00Z > /dev/shm/x")):
                self.assertEqual(self.ev("Bash", cwd=cwd, command=cmd), [], cmd)

        def test_bash_heredoc_is_plain_text(self):
            self.assertTrue(self.ev("Bash", command=f"cat <<'EOF' >> {PROJ}/private/s.md\nhb: 2099-01-01T00:00Z\nEOF"))
            self.assertEqual(self.ev("Bash", command=f"cat <<EOF >> {PROJ}/private/s.md\nhb: $(date -u)\nEOF"), [])
            self.assertEqual(self.ev("Bash", command="cat <<'EOF' > /dev/shm/x\nhb: 2099-01-01T00:00Z\nEOF"), [])
            self.assertTrue(self.ev("Bash", command=f"echo 'unterminated 2099-01-01T00:00Z > {PROJ}/private/s"))

        def test_bash_scheduled_keyword_allowed(self):
            self.assertEqual(self.ev("Bash", command=f"echo 'next_run: 2099-01-01T09:30Z' >> {PROJ}/private/s.md"),
                             [])

        def test_bash_scan_is_linear(self):
            # finding (codex r2): unmatched $( openers rescanned the suffix quadratically
            # round 31: at this size (1.35 MB) the analysis spends BASH_WORK_BUDGET and fails OPEN (allowed), still
            # within the timeout; a hundredth of it stays inside the budget and is still denied (unparseable).
            # The timeout is the hang guard only; linearity is the growth from n to GROWTH * n (up to that size)
            for scale, want in ((100, True), (1, False)):
                code = ("import importlib.util as u,datetime;s=u.spec_from_file_location('m',%r);m=u.module_from_spec(s);"
                        "s.loader.exec_module(m);n=datetime.datetime(2026,9,23,17,45,tzinfo=datetime.timezone.utc);"
                        f"c=': > {PROJ}/private/s; echo '+'$('*(500000//%d)+'`'*(100001//%d)+'$(A=B'*(50000//%d)"
                        "+' 2099-01-01T00:00Z';print(m.evaluate({'tool_name':'Bash','tool_input':{'command':c}},n))"
                        % (os.path.abspath(__file__), scale, scale, scale))
                r = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True,
                                   timeout=HANG_TIMEOUT)
                self.assertEqual("2099-01-01T00:00Z" in r.stdout, want, (scale, r.stdout, r.stderr))
            code = TIMED_PRELUDE + (
                "def run(n):\n"
                f"    c = ': > {PROJ}/private/s; echo ' + '$(' * (5 * n) + '`' * (n + 1) + '$(A=B' * (n // 2)\n"
                "    m.evaluate({'tool_name': 'Bash', 'tool_input': {'command': c + ' 2099-01-01T00:00Z'}}, now)\n"
                "print(json.dumps(ratio(12500, run, 3)))\n")
            small, large = run_timed(code, HANG_TIMEOUT)
            self.assertLess(large / max(small, 1e-3), LINEAR_LIMIT, (small, large))

        def test_main_deny_shape_and_fail_open(self):
            payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": self.store,
                                                                       "content": "2099-01-01T00:00:00Z"}})
            for stdin_text, env, want_deny in ((payload, {}, True), ("not json", {}, False),
                                               (payload, {"AIQT_HOOKS_WORKER": "1"}, False),
                                               (payload, {"AIQT_HOOKS_WORKER": "0"}, True),
                                               (payload, {"AIQT_HOOKS_WORKER": ""}, True),
                                               (payload, {"ORCH_WORKER": "1"}, False),
                                               (payload, {"ORCH_VERIFY_OWNER": "x"}, False),
                                               (payload, {"ORCH_VERIFY_OWNER": ""}, False)):
                old_in, old_out, old_env = sys.stdin, sys.stdout, dict(os.environ)
                sys.stdin, sys.stdout = io.StringIO(stdin_text), io.StringIO()
                try:
                    os.environ.pop("AIQT_HOOKS_WORKER", None)
                    os.environ.pop("ORCH_WORKER", None)
                    os.environ.pop("ORCH_VERIFY_OWNER", None)
                    os.environ.update(env)
                    rc = main(["future-stamp-write.py"])
                    out = sys.stdout.getvalue()
                finally:
                    sys.stdin, sys.stdout = old_in, old_out
                    os.environ.clear()
                    os.environ.update(old_env)
                self.assertEqual(rc, 0)
                if want_deny:
                    h = json.loads(out)["hookSpecificOutput"]
                    self.assertEqual((h["hookEventName"], h["permissionDecision"]), ("PreToolUse", "deny"))
                    self.assertIn("2099-01-01T00:00:00Z", h["permissionDecisionReason"])
                else:
                    self.assertEqual(out, "")

        def test_shared_grammar_identical_to_sibling(self):
            sib = _sibling_or_skip("stamp-truth-stop.py")
            spec = importlib.util.spec_from_file_location("sts_sibling", sib)
            mod = importlib.util.module_from_spec(spec)
            # the sibling is loaded with no bytecode written, so no __pycache__ is left beside the hooks
            old_dwb, sys.dont_write_bytecode = sys.dont_write_bytecode, True
            try:
                spec.loader.exec_module(mod)
            finally:
                sys.dont_write_bytecode = old_dwb
            self.assertEqual(mod.SCHED_KEYWORDS, SCHED_KEYWORDS)
            self.assertEqual(mod.SCHED_GAP_TOKENS, SCHED_GAP_TOKENS)
            self.assertEqual((mod.TIME_GRAMMAR, mod.ZONE_GRAMMAR), (TIME_GRAMMAR, ZONE_GRAMMAR))
            self.assertEqual((mod._SCHED_RE.pattern, mod._SCHED_RE.flags), (_SCHED_RE.pattern, _SCHED_RE.flags))
            self.assertEqual((mod._TOKEN_RE.pattern, mod._WORDCH_RE.pattern), (_TOKEN_RE.pattern, _WORDCH_RE.pattern))
            self.assertEqual(inspect.getsource(mod.sched_exempter), inspect.getsource(sched_exempter))
            # the configuration and kill-switch helpers are shared verbatim across the three hooks
            for name in ("_cfg", "_is_worker", "_sibling_or_skip", "_wall_clock_asserts",
                         "_wall_clock_alias_fixtures"):
                self.assertEqual(inspect.getsource(getattr(mod, name)), inspect.getsource(globals()[name]), name)

        # -- round 4 --
        def test_r4_date_span_respects_quoting(self):
            # finding (codex r3 M2): a quoted paren inside $(date ...) extended the span over a later literal
            deny = (f"echo \"$(date '+(')\" \"hb: 2099-01-01T00:00Z\" > {PROJ}/private/s.md; echo ')'",
                    f"echo '$(date -d 2099-01-01T00:00Z)' > {PROJ}/private/s.md",
                    f"echo $'$(date \\' 2099-01-01T00:00Z' > {PROJ}/private/s.md",
                    f"echo \"(\" $(date -u) \")\" hb 2099-01-01T00:00Z > {PROJ}/private/s.md",
                    f"echo \"$(date -u\" 2099-01-01T00:00Z > {PROJ}/private/s.md")
            for cmd in deny:
                self.assertTrue(self.ev("Bash", command=cmd), cmd)
            allow = (f"echo \"$(date '+%H:%M (%Z)' -d 2099-01-01T00:00Z)\" > {PROJ}/private/s.md",
                     f"echo \"hb $(date -d \"2099-01-01T00:00Z\")\" > {PROJ}/private/s.md",
                     f"echo \"$(date -d \"$(echo ')') 2099-01-01T00:00Z\")\" > {PROJ}/private/s.md",
                     f"echo `date -d '2099-01-01T00:00Z'` > {PROJ}/private/s.md")
            for cmd in allow:
                self.assertEqual(self.ev("Bash", command=cmd), [], cmd)

        def test_r4_env_prefix_date_span(self):
            # finding (gemini r3 m1): `env TZ=UTC date` was not a date substitution
            for cmd in (f"echo \"hb $(env TZ=UTC date -d 2099-01-01T00:00Z)\" > {PROJ}/private/s.md",
                        f"echo \"hb $(/usr/bin/env TZ=UTC date -d 2099-01-01T00:00Z)\" > {PROJ}/private/s.md"):
                self.assertEqual(self.ev("Bash", command=cmd), [], cmd)
            self.assertTrue(self.ev("Bash", command=f"echo $(/usrdate 2099-01-01T00:00Z) > {PROJ}/private/s.md"))
            self.assertTrue(self.ev("Bash", command=f"echo $(envdate 2099-01-01T00:00Z) > {PROJ}/private/s.md"))

        def test_r4_keyword_must_immediately_precede(self):
            for line in ("hb: 2099-01-01T00:00Z next", "target branch pushed, hb: 2099-01-01T00:00Z",
                         "next we did a b c and hb 2099-01-01T00:00Z"):
                self.assertTrue(self.ev("Write", file_path=self.store, content=line), line)
            for line in ("next run at 2099-01-01T00:00Z", "target date: 2099-01-01T00:00Z", "due: **[2099-01-01T00:00Z]**"):
                self.assertEqual(self.ev("Write", file_path=self.store, content=line), [], line)

        def test_r4_scan_is_linear(self):
            # round 25 (finding 3): a 1.0 s wall-clock budget flaked on a loaded host (1.094 s observed); the
            # check is now the growth ratio from N to 2N (best of 3 each), which does not depend on host speed.
            # Round 26 (finding 6): the in-process 30 s ceiling ran only after every timed run had returned, so it
            # could not interrupt a hang; the runs now execute in a child under a subprocess timeout (the hang guard)
            code = TIMED_PRELUDE + (
                f"S='{PROJ}/private/state.md'\n"
                "def ev(tool, **ti):\n"
                f"    return m.evaluate({{'tool_name': tool, 'tool_input': ti, 'cwd': '{PROJ}/repo'}}, now)\n"
                "def past(n):\n"
                "    c = '2026-09-23T17:45Z ' * n\n"
                "    assert ev('Write', file_path=S, content=c) == []\n"
                f"    assert ev('Bash', command='echo ' + c + '> {PROJ}/private/s.md') == []\n"
                "def future(n):\n"
                "    got = ev('Write', file_path=S, content='next ' + '2099-01-01T00:00Z x ' * n)\n"
                "    assert got == ['2099-01-01T00:00Z'], got\n"
                "print(json.dumps({r.__name__: ratio(n, r) for r, n in ((past, 12500), (future, 6250))}))\n")
            # the step is now GROWTH (N to 8N, up to the former sizes, equal work per sample): a 2N step bounded
            # at 3.0 left little room between a linear pass (about 2) and a quadratic one (about 4)
            for name, (small, large) in run_timed(code, HANG_TIMEOUT).items():
                self.assertLess(large / max(small, 1e-3), LINEAR_LIMIT, (name, small, large))

        def test_r5_many_distinct_literals_linear_and_bounded(self):
            # finding (codex r4, sibling pattern): the fragment path scanned the growing list per literal
            lits = [f"2099-{1 + i % 12:02d}-{1 + (i // 12) % 28:02d}T{(i // 336) % 24:02d}:{(i // 8064) % 60:02d}Z"
                    for i in range(40000)]
            fp = self.store_file("x\n" * (EXISTING_MAX_BYTES // 2 + 1))  # over the cap: the fragment path
            r = self.ev("MultiEdit", file_path=fp, edits=[{"old_string": "x", "new_string": "\n".join(lits[:20000])},
                                                          {"old_string": "x", "new_string": "\n".join(lits[20000:])}])
            w = self.ev("Write", file_path=os.path.join(self.tmp, "w.md"), content=" ".join(lits))
            self.assertEqual((len(r), len(w)), (40000, 40000))
            # linear: the growth from n to GROWTH * n literals (up to 40,000) in a child under the hang ceiling,
            # not a wall-clock ceiling (a 1.0 s one depended on the host's speed)
            code = TIMED_PRELUDE + (
                "fp = %r\n"
                "os.environ['AIQT_STORE_ROOT'] = os.path.dirname(fp)\n"
                "lits = [f'2099-{1 + i %% 12:02d}-{1 + (i // 12) %% 28:02d}T{(i // 336) %% 24:02d}:"
                "{(i // 8064) %% 60:02d}Z' for i in range(40000)]\n"
                "def ev(tool, **ti):\n"
                "    return m.evaluate({'tool_name': tool, 'tool_input': ti, 'cwd': %r}, now)\n"
                "def run(n):\n"
                "    edits = [{'old_string': 'x', 'new_string': '\\n'.join(lits[:n // 2])},\n"
                "             {'old_string': 'x', 'new_string': '\\n'.join(lits[n // 2:n])}]\n"
                "    wp = os.path.join(os.path.dirname(fp), 'w.md')\n"
                "    got = (len(ev('MultiEdit', file_path=fp, edits=edits)),\n"
                "           len(ev('Write', file_path=wp, content=' '.join(lits[:n]))))\n"
                "    assert got == (n, n), got\n"
                "print(json.dumps(ratio(5000, run, 3)))\n") % (fp, f"{PROJ}/repo")
            small, large = run_timed(code, HANG_TIMEOUT)
            self.assertLess(large / max(small, 1e-3), LINEAR_LIMIT, (small, large))
            old_out = sys.stdout
            sys.stdout = io.StringIO()
            try:
                _deny(w, self.now)
                reason = json.loads(sys.stdout.getvalue())["hookSpecificOutput"]["permissionDecisionReason"]
            finally:
                sys.stdout = old_out
            self.assertIn(f" and {40000 - MAX_REPORTED} more", reason)
            self.assertLess(len(reason), 2000)

        # -- round 6: markdown-table header exemption --
        def tw(self, *lines):
            return self.ev("Write", file_path=self.store, content="\n".join(lines) + "\n")

        def test_r6_table_header_keyword_column_allowed(self):
            # finding (codex r5 MAJOR): the scheduling keyword lived in the header cell, not on the row's line
            self.assertEqual(self.tw("| Item | Due | Owner |", "|---|---|---|", "| x | 2099-01-01T00:00Z | me |"), [])
            self.assertEqual(self.tw("| Item | Next run |", "| :--- | ---: |", "| x | 2099-01-01T00:00Z |",
                                     "| y | 2099-02-01T00:00Z |"), [])
            self.assertEqual(self.tw("  | Item | Due", "  ---|:---:", "  | x | 2099-01-01T00:00Z"), [])

        def test_r6_table_non_keyword_column_denied(self):
            self.assertEqual(self.tw("| Item | Seen |", "|---|---|", "| x | 2099-01-01T00:00Z |"),
                             ["2099-01-01T00:00Z"])
            self.assertEqual(self.tw("| Due | Seen |", "|---|---|", "| 2099-01-01T00:00Z | 2099-02-01T00:00Z |"),
                             ["2099-02-01T00:00Z"])

        def test_r6_table_ended_row_denied(self):
            self.assertTrue(self.tw("| Item | Due |", "|---|---|", "| x | 2099-01-01T00:00Z |", "",
                                    "| y | 2099-02-01T00:00Z |"))
            # round 11: a pipe-free line is a one-cell row (GFM), so it no longer ends the table
            self.assertEqual(self.tw("| Item | Due |", "|---|---|", "text", "| y | 2099-02-01T00:00Z |"), [])

        def test_r6_table_without_delimiter_denied(self):
            for lines in (("| Item | Due |", "| x | 2099-01-01T00:00Z |"),
                          ("| Item | Due |", "|---|", "| x | 2099-01-01T00:00Z |"),  # cell count mismatch
                          ("| Item | Due |", "", "|---|---|", "| x | 2099-01-01T00:00Z |"),
                          ("| Item | Due |", "|---|---|x", "| x | 2099-01-01T00:00Z |"),  # not a delimiter row
                          ("| Item | Due |", "| |---|", "| x | 2099-01-01T00:00Z |"),  # an empty delimiter cell
                          ("Item Due", "---|---", "x | 2099-01-01T00:00Z"),  # a header needs an unescaped pipe
                          ("Item \\| Due", "---|---", "x | 2099-01-01T00:00Z")):
                self.assertTrue(self.tw(*lines), lines)

        def test_r6_table_escaped_pipe_does_not_shift_columns(self):
            self.assertEqual(self.tw("| Item | Due |", "|---|---|", "| a \\| b | 2099-01-01T00:00Z |"), [])
            self.assertTrue(self.tw("| Item \\| Due | Seen |", "|---|---|", "| x | 2099-01-01T00:00Z |"))
            self.assertTrue(self.tw("| Seen | Due |", "|---|---|", "| a \\| 2099-02-01T00:00Z | c |"))

        def test_r6_table_header_carried_over_edit_allowed(self):
            fp = self.store_file("# log\n| Item | Due |\n|---|---|\n| a | 2026-09-01T00:00Z |\n")
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="| a | 2026-09-01T00:00Z |",
                                     new_string="| a | 2026-09-01T00:00Z |\n| b | 2099-01-01T00:00Z |"), [])
            fp = self.store_file("| Item | Seen |\n|---|---|\n| a | 2026-09-01T00:00Z |\n")
            self.assertTrue(self.ev("Edit", file_path=fp, old_string="| a | 2026-09-01T00:00Z |",
                                    new_string="| a | 2026-09-01T00:00Z |\n| b | 2099-01-01T00:00Z |"))

        def test_r6_table_context_absent_on_fragment_path_and_bash(self):
            # disclosed: no table context on the Bash path or the fragment path
            self.assertTrue(self.ev("Bash", command="printf '| Item | Due |\n|---|---|\n| x | 2099-01-01T00:00Z |\n' "
                                                    f">> {PROJ}/private/s.md"))
            fp = self.store_file("x\n" * (EXISTING_MAX_BYTES // 2 + 1))  # over the cap: the fragment path
            self.assertTrue(self.ev("Edit", file_path=fp, old_string="x",
                                    new_string="| Item | Due |\n|---|---|\n| x | 2099-01-01T00:00Z |"))

        def test_r6_table_scan_is_linear(self):
            rows = ["| x | 2099-01-01T00:00Z |"] * 50000
            self.assertEqual(self.tw("| Item | Due |", "|---|---|", *rows), [])
            self.assertEqual(self.tw(*(["| a | b |"] * 50000)), [])
            # linear: the growth from n to GROWTH * n rows (up to 50,000) in a child under the hang ceiling, not
            # a wall-clock ceiling (a 1.0 s one depended on the host's speed)
            code = TIMED_PRELUDE + (
                "def tw(*lines):\n"
                f"    ti = {{'file_path': '{PROJ}/private/state.md', 'content': '\\n'.join(lines) + '\\n'}}\n"
                f"    return m.evaluate({{'tool_name': 'Write', 'tool_input': ti, 'cwd': '{PROJ}/repo'}}, now)\n"
                "def run(n):\n"
                "    assert tw('| Item | Due |', '|---|---|', *(['| x | 2099-01-01T00:00Z |'] * n)) == []\n"
                "    assert tw(*(['| a | b |'] * n)) == []\n"
                "print(json.dumps(ratio(6250, run, 3)))\n")
            small, large = run_timed(code, HANG_TIMEOUT)
            self.assertLess(large / max(small, 1e-3), LINEAR_LIMIT, (small, large))

        # -- round 7 (codex round-6 findings) --
        def test_r7_dq_substitution_write_seen(self):
            # finding 1 (HIGH): a write inside a double-quoted command substitution was hidden
            for cmd in (f"result=\"$(printf '%s\\n' 'heartbeat: 2099-01-01T00:00Z' | tee {PROJ}/private/s.md)\"",
                        f"r=\"`printf 'hb 2099-01-01T00:00Z' | tee {PROJ}/private/s.md`\"",
                        f"r=\"x $(echo \"$(printf 'hb 2099-01-01T00:00Z' > {PROJ}/private/s.md)\") y\"",
                        f"r=\"$(sed -i 's/a/2099-01-01T00:00Z/' {PROJ}/private/s.md)\"",
                        f"r=\"$(echo ')' | tee {PROJ}/private/s.md) 2099-01-01T00:00Z\"",
                        f"echo \"$(printf 2099-01-01T00:00Z\" > {PROJ}/private/s.md"):  # unterminated
                self.assertTrue(self.ev("Bash", command=cmd), cmd)
            # substitutions stay independent: a write-free substitution and quoted text remain reads
            for cmd in (f"n=\"$(grep -c 2099-01-01T00:00Z {PROJ}/private/s.md)\"",
                        f"grep \"$(echo 2099-01-01T00:00Z)\" {PROJ}/private/s.md 2>&1",
                        f"grep '$(tee x)' {PROJ}/private/s.md 2099-01-01T00:00Z",
                        f"grep \"\\$(tee x)\" {PROJ}/private/s.md 2099-01-01T00:00Z",
                        f"echo \"$(sed 's/a/b/' {PROJ}/private/s.md)\" -i 2099-01-01T00:00Z"):
                self.assertEqual(self.ev("Bash", command=cmd), [], cmd)
            self.assertTrue(bash_writes("sed \"$(echo x)\" -i f"))  # the outer stream continues past a substitution

        def test_r7_perl_digit_clusters_in_place(self):
            # finding 2 (HIGH): a perl switch cluster with digits hid the in-place i
            for cmd in (f"perl -0777pi -e 's/old/2099-01-01T00:00Z/' {PROJ}/private/s.md",
                        f"perl -0pi -e 's/old/2099-01-01T00:00Z/' {PROJ}/private/s.md",
                        f"perl -l0pi -e 's/old/2099-01-01T00:00Z/' {PROJ}/private/s.md",
                        f"perl -0777 -pi.bak -e 's/old/2099-01-01T00:00Z/' {PROJ}/private/s.md",
                        f"sed -E -s -i 's/x/2099-01-01T00:00Z/' {PROJ}/private/s.md",
                        f"sed -nEi 's/x/2099-01-01T00:00Z/' {PROJ}/private/s.md"):
                self.assertTrue(self.ev("Bash", command=cmd), cmd)
            for cmd in (f"perl -0777 -ne 'print if /2099-01-01T00:00Z/' {PROJ}/private/s.md",
                        f"sed -n -E '/2099-01-01T00:00Z/p' {PROJ}/private/s.md"):
                self.assertEqual(self.ev("Bash", command=cmd), [], cmd)

        def test_r7_gfm_table_forms(self):
            # finding 3 (MED): no outer pipes, and a one-hyphen delimiter, are valid GFM tables
            self.assertEqual(self.tw("Item | Due", "---|---", "x | 2099-01-01T00:00Z"), [])
            self.assertEqual(self.tw("| Item | Due |", "|-|-|", "| x | 2099-01-01T00:00Z |"), [])
            self.assertEqual(self.tw("| Item | Due |", "|--|--|", "| x | 2099-01-01T00:00Z |"), [])
            self.assertEqual(self.tw("Item | Due |", "|:-|-:|", "| x | 2099-01-01T00:00Z |"), [])
            self.assertEqual(self.tw("Item | Due", "- | -", "x | 2099-01-01T00:00Z", "y | 2099-02-01T00:00Z"), [])
            self.assertEqual(self.tw("Due | Seen", "---|---", "2099-01-01T00:00Z | 2099-02-01T00:00Z"),
                             ["2099-02-01T00:00Z"])
            self.assertEqual(self.tw("Item | Due", "---|---", "x | 2099-01-01T00:00Z", "no pipe here",
                                     "y | 2099-02-01T00:00Z"), [])  # round 11: a pipeless line is a row (GFM)

        def test_r7_header_repurpose_rechecks_rows(self):
            # finding 4 (MED): a header or delimiter change must re-check every row of its table
            fp = self.store_file("| Item | Due |\n|---|---|\n| x | 2099-01-01T00:00Z |\n")
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="Due", new_string="Observed"),
                             ["2099-01-01T00:00Z"])
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="| Item | Due |", new_string="| Due | Seen |"),
                             ["2099-01-01T00:00Z"])  # column shift
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="|---|---|\n", new_string=""),
                             ["2099-01-01T00:00Z"])  # the row leaves its table
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="Due", new_string="Due date"), [])
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="|---|---|", new_string="|:--|--:|"), [])
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="| Item |", new_string="| Thing |"), [])
            fp = self.store_file("| A | Seen |\n|---|---|\n| r | 2026-09-01T00:00Z |\n\n"
                                 "| B | Due |\n|---|---|\n| x | 2099-01-01T00:00Z |\n")
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="\n\n| B | Due |\n|---|---|\n", new_string="\n"),
                             ["2099-01-01T00:00Z"])  # the row joins the Seen table
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="| r |", new_string="| s |"), [])

        def test_r7_spaced_and_quoted_fd_duplication(self):
            # finding 5 (MED): the redirection operand is classified whole, after dequoting
            for cmd in (f"grep 2099-01-01T00:00Z {PROJ}/private/s.md 1>& 2",
                        f"grep 2099-01-01T00:00Z {PROJ}/private/s.md >&'2'",
                        f"grep 2099-01-01T00:00Z {PROJ}/private/s.md 2>& \"1\"",
                        f"grep 2099-01-01T00:00Z {PROJ}/private/s.md >& -",
                        f"grep 2099-01-01T00:00Z {PROJ}/private/s.md 3>&1-",
                        f"grep 2099-01-01T00:00Z {PROJ}/private/s.md >& /dev/null"):
                self.assertEqual(self.ev("Bash", command=cmd), [], cmd)
            for cmd in (f"echo 2099-01-01T00:00Z >& {PROJ}/private/s.md",
                        f"echo 2099-01-01T00:00Z >&'{PROJ}/private/s.md'",
                        f"echo 2099-01-01T00:00Z >& $fd {PROJ}/private/s.md"):  # a `$` target is unknown: denied
                self.assertTrue(self.ev("Bash", command=cmd), cmd)
            # round 24 (item 3): `&> 2` and `>& 2x` are still writes (to files named 2 and 2x, not duplications),
            # but their targets are not the store path, which appears only as a comment or an echo argument
            for cmd in (f"echo 2099-01-01T00:00Z &> 2 # {PROJ}/private/s.md",
                        f"echo 2099-01-01T00:00Z >& 2x {PROJ}/private/s.md"):
                self.assertTrue(bash_writes(cmd), cmd)
                self.assertEqual(self.ev("Bash", command=cmd), [], cmd)

        def test_r7_subagent_store_write_is_checked(self):
            # finding 6 (refuted, intended): a subagent's store write is checked, never skipped
            for extra in ({"agent_id": "a1b2"}, {"agent_id": "a1b2", "agent_type": "general-purpose"}):
                payload = dict({"tool_name": "Write", "cwd": "/",
                                "tool_input": {"file_path": self.store, "content": "hb 2099-01-01T00:00Z"}}, **extra)
                self.assertEqual(evaluate(payload, self.now), ["2099-01-01T00:00Z"], extra)
                payload = dict({"tool_name": "Bash", "cwd": "/", "tool_input": {
                    "command": f"echo 'hb 2099-01-01T00:00Z' >> {PROJ}/private/s.md"}}, **extra)
                self.assertTrue(evaluate(payload, self.now), extra)
            self.assertIn("DELIBERATELY checked", __doc__)

        def test_r7_shell_tokens_linear(self):
            # round 32: best of 3 in a child under the hang ceiling (2.08 s in-process was observed at load 30,
            # and a 2.0 s ceiling failed at 2.53 s on a slower CI runner). The verdict is the growth from n to
            # GROWTH * n (up to the former sizes), not a wall-clock ceiling
            code = TIMED_PRELUDE + (
                "def run(n):\n"
                "    for cmd in ('\"$(' * n, '\"$(x)\"' * n, '\"`x`\"' * n, '$((' * n + '))' * n, '1>& 2 ' * n):\n"
                "        m.bash_writes(cmd)\n"
                "print(json.dumps(ratio(12500, run, 3)))\n")
            small, large = run_timed(code, HANG_TIMEOUT)
            self.assertLess(large / max(small, 1e-3), LINEAR_LIMIT, (small, large))

        # -- round 8 (codex round-7 findings) --
        def test_r8_new_block_ends_table(self):
            # finding 1 (HIGH): a list item, heading, blockquote, or fence after a Due table inherited its context
            hdr = ("| Item | Due |", "|---|---|", "| a | 2026-09-01T00:00Z |")
            for tail in (("- heartbeat | 2099-01-02T00:00Z",), ("* hb | 2099-01-02T00:00Z",),
                         ("+ hb | 2099-01-02T00:00Z",), ("1. hb | 2099-01-02T00:00Z",), ("2) hb | 2099-01-02T00:00Z",),
                         ("# hb | 2099-01-02T00:00Z",),
                         ("```text | x", "| b | 2099-01-02T00:00Z |"), ("~~~ | x", "| b | 2099-01-02T00:00Z |"),
                         ("", "hb | 2099-01-02T00:00Z"), ("hb 2099-01-02T00:00Z",)):
                self.assertEqual(self.tw(*(hdr + tail)), ["2099-01-02T00:00Z"], tail)
            # round 13: a `>` line ends the table AND is quoted text, exempt whatever its column (item 4)
            self.assertEqual(self.tw(*(hdr + ("> hb | 2099-01-02T00:00Z", "| b | 2099-01-02T00:00Z |"))),
                             ["2099-01-02T00:00Z"])  # the row after the quote is outside the table: checked
            # still rows: a leading-pipe row, a row of a pipeless-header table, a hyphen cell that is no list marker
            self.assertEqual(self.tw(*(hdr + ("  | b | 2099-01-02T00:00Z |",))), [])
            self.assertEqual(self.tw("Item | Due", "---|---", "x | 2026-09-01T00:00Z", "y | 2099-01-02T00:00Z"), [])
            self.assertEqual(self.tw("Item | Due", "---|---", "-| 2099-01-02T00:00Z"), [])
            # a new table may start right after the interrupting block
            self.assertEqual(self.tw(*(hdr + ("# next", "| Item | Due |", "|---|---|",
                                              "| b | 2099-01-02T00:00Z |"))), [])

        def test_r10_bare_row_under_leading_pipe_header(self):
            # codex round-8 [H]: GFM leading pipes are optional per line, so a bare row continues a `| ... |` table
            self.assertEqual(self.tw("| Item | Due |", "|---|---|", "release | 2099-01-01T00:00Z"), [])
            self.assertEqual(self.tw("| Item | Due |", "|---|---|", "| a | x |", "release | 2099-01-01T00:00Z |"), [])
            # the column is counted from the row's own leading pipe: a bare row's Item column is not exempt
            self.assertEqual(self.tw("| Item | Due |", "|---|---|", "2099-01-01T00:00Z | x"),
                             ["2099-01-01T00:00Z"])
            # the genuine block-ending checks still end it
            for gap in ("", "- x", "# h", "> q", "```"):
                self.assertEqual(self.tw("| Item | Due |", "|---|---|", gap, "release | 2099-01-01T00:00Z"),
                                 ["2099-01-01T00:00Z"], gap)
            self.assertNotIn("stricter than GFM", __doc__)

        def test_r11_pipe_free_row_continues_table(self):
            # codex round-10 [H]: GFM example 202, a pipe-free body row continues the table (one cell, column 0)
            body = "| Item | Due |\n|---|---|\nTBD\nrelease | %s\n"
            self.assertEqual(self.tw(*(body % "2099-01-01T00:00Z").splitlines()), [])
            fp = os.path.join(self.tmp, "r11.md")
            os.environ["AIQT_STORE_ROOT"] = self.tmp  # round 12: the Edit must reach a store path to count
            with open(fp, "w") as f:
                f.write(body % "2026-09-01T00:00Z")
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="2026-09-01T00:00Z",
                                     new_string="2099-01-01T00:00Z"), [])
            # control: the same Edit with the row's pipe removed (no header exemption) is denied, so the
            # allow above comes from the table rule, not from the path falling outside the store
            with open(fp, "w") as f:
                f.write(body.replace("release | ", "release ") % "2026-09-01T00:00Z")
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="2026-09-01T00:00Z",
                                     new_string="2099-01-01T00:00Z"), ["2099-01-01T00:00Z"])
            os.environ["AIQT_STORE_ROOT"] = STORE  # back to the fixture store root
            # a blank line still ends the table
            self.assertEqual(self.tw("| Item | Due |", "|---|---|", "TBD", "", "release | 2099-01-01T00:00Z"),
                             ["2099-01-01T00:00Z"])

        def test_r11_pipe_free_line_is_column_zero(self):
            # a pipe-free paragraph line after a table stays in it until a blank line, but (round 12) it never
            # gets a header exemption: it is checked like an ordinary line, even under a scheduling cell 0
            self.assertEqual(self.tw("| Item | Due |", "|---|---|", "| a | x |", "hb 2099-01-01T00:00Z"),
                             ["2099-01-01T00:00Z"])
            self.assertEqual(self.tw("| Item | Due |", "|---|---|", "| a | x |", "note", "more text",
                                     "release | 2099-01-01T00:00Z"), [])
            self.assertEqual(self.tw("| Item | Due |", "|---|---|", "note", "", "hb 2099-01-01T00:00Z",
                                     "release | 2099-01-01T00:00Z"),
                             ["2099-01-01T00:00Z"])
            self.assertEqual(self.tw("| Due | Item |", "|---|---|", "| x | a |", "2099-01-01T00:00Z"),
                             ["2099-01-01T00:00Z"])
            # the keyword-on-its-own-line rule still exempts a pipe-free row
            self.assertEqual(self.tw("| Due | Item |", "|---|---|", "| x | a |", "due 2099-01-01T00:00Z"), [])
            # and the table stays open past it: a later piped row keeps its Due exemption
            self.assertEqual(self.tw("| Due | Item |", "|---|---|", "note", "| 2099-01-01T00:00Z | a |"), [])
            self.assertNotIn("a line with no unescaped pipe, or", __doc__)
            self.assertNotIn("so it is exempt only when header cell 0 carries a keyword", __doc__)

        def test_r12_pipe_free_heartbeat_under_due_header_denied(self):
            # codex round-11 [H]: a pipe-free line under `| Due | Item |` inherited column 0's exemption, so
            # a fabricated future heartbeat was allowed through Write, Edit, and MultiEdit
            fut = "2099-01-01T00:00Z"
            body = "| Due | Item |\n| --- | --- |\n| 2099-02-01T09:00Z | release |\nLast-heartbeat: %s\n"
            fp = os.path.join(self.tmp, "r12.md")
            os.environ["AIQT_STORE_ROOT"] = self.tmp
            self.assertEqual(self.ev("Write", file_path=fp, content=body % fut), [fut])
            with open(fp, "w") as f:
                f.write(body % "2026-09-01T00:00Z")
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="2026-09-01T00:00Z", new_string=fut), [fut])
            self.assertEqual(self.ev("MultiEdit", file_path=fp,
                                     edits=[{"old_string": "2026-09-01T00:00Z", "new_string": fut}]), [fut])
            # the piped Due row itself stays exempt (unchanged scheduled row, and a fresh one)
            self.assertEqual(self.ev("Write", file_path=fp, content=body % "2026-09-01T00:00Z"), [])
            self.assertIn("A pipe-free row gets no header", __doc__)

        def test_r8_table_context_carry_over_scales(self):
            # finding 2 (MED perf): a row key compared the full header text, so time grew with header x rows
            # round 32: interleaved best of 3 in a child under the hang ceiling (was one in-process run each)
            code = TIMED_PRELUDE + (
                "def run(hl):\n"
                "    old = '\\n'.join(['| Item | Due | ' + 'h' * hl + ' |', '|---|---|---|']\n"
                "                     + ['| x | 2026-09-01T00:00Z | y |'] * 160000) + '\\n'\n"
                "    assert m.future_in_changed_lines(old, old, 0) == []\n"
                "print(json.dumps(interleaved((10000, 160000), run, 3)))\n")
            times = run_timed(code, HANG_TIMEOUT)
            # flat in header length (16 times longer): a ratio, with no wall-clock ceiling or absolute slack
            self.assertLess(times[1] / max(times[0], 1e-3), FLAT_LIMIT, times)

        def test_r8_case_pattern_paren_in_substitution(self):
            # finding 3 (HIGH, exotic): a case pattern `)` closed the $(...) frame early, hiding the write
            deny = (f'result="$(case ok in ok) printf \'%s\\n\' \'hb 2099-01-02T00:00Z\' > {PROJ}/private/s.md;; esac)"',
                    f'r=$(case ok in (ok) tee {PROJ}/private/s.md <<< "2099-01-02T00:00Z";; esac)',
                    f'r="$(case a in a) case b in b) :;; esac; printf 2099-01-02T00:00Z > {PROJ}/private/s.md;; esac)"')
            for cmd in deny:
                self.assertTrue(bash_writes(cmd), cmd)
                self.assertEqual(self.ev("Bash", command=cmd), ["2099-01-02T00:00Z"], cmd)
            # a quoted case/esac is no keyword, and a read inside a case construct stays a read
            allow = (f'r="$(case ok in ok) grep 2099-01-02T00:00Z {PROJ}/private/s.md;; esac)"',
                     f'r="$(echo \'case\'; grep 2099-01-02T00:00Z {PROJ}/private/s.md)"')
            for cmd in allow:
                self.assertFalse(bash_writes(cmd), cmd)
                self.assertEqual(self.ev("Bash", command=cmd), [], cmd)

        def test_r9_hash_row_continues_pipeless_table(self):
            # round 9 finding 1 (HIGH, false deny): any `#` line ended a table, so an issue-number row lost its
            # Due-column exemption; only an ATX heading (1 to 6 `#` then whitespace or the line end) ends one
            for rows in (("#123 | 2099-12-31T00:00Z",), ("x | 2026-09-01T00:00Z", "#7 | 2099-12-31T00:00Z"),
                         ("####### | 2099-12-31T00:00Z",), ("#tag | 2099-12-31T00:00Z",)):
                self.assertEqual(self.tw("Issue | Due", "---|---", *rows), [], rows)
            for head in ("# h | 2099-12-31T00:00Z", "###### h | 2099-12-31T00:00Z", "#\t| 2099-12-31T00:00Z",
                         "## | 2099-12-31T00:00Z"):
                self.assertEqual(self.tw("Issue | Due", "---|---", head), ["2099-12-31T00:00Z"], head)

        def test_r9_case_counted_only_in_command_position(self):
            # round 9 finding 2 (HIGH, false deny): a `case` ARGUMENT inside a substitution opened a case
            # construct, left the substitution unterminated, and read as a write
            reads = (f'x="$(rg case {PROJ}/private/s.md | grep 2099-12-31T00:00Z)"',
                     f'x="$(echo case; grep 2099-12-31T00:00Z {PROJ}/private/s.md)"',
                     f'x="$(rg then case {PROJ}/private/s.md | grep 2099-12-31T00:00Z)"',
                     f'x=$(grep -e esac -e case 2099-12-31T00:00Z {PROJ}/private/s.md)')
            for cmd in reads:
                self.assertIsNotNone(_shell_tokens(cmd), cmd)
                self.assertFalse(bash_writes(cmd), cmd)
                self.assertEqual(self.ev("Bash", command=cmd), [], cmd)
            # a case in command position, after a separator or a reserved word, still shows the write inside it
            writes = (f'r="$(true; case a in a) printf 2099-12-31T00:00Z > {PROJ}/private/s.md;; esac)"',
                      f'r="$(if true; then case a in a) tee {PROJ}/private/s.md <<< 2099-12-31T00:00Z;; esac; fi)"',
                      f'r="$(! case a in a) printf 2099-12-31T00:00Z > {PROJ}/private/s.md;; esac)"',
                      f'r="$(case a in a) echo esac; printf 2099-12-31T00:00Z > {PROJ}/private/s.md;; esac)"')
            for cmd in writes:
                self.assertTrue(bash_writes(cmd), cmd)
                self.assertEqual(self.ev("Bash", command=cmd), ["2099-12-31T00:00Z"], cmd)

        def test_r4_threat_model_stated(self):
            self.assertIn("THREAT MODEL", __doc__.split("\n\n")[1])

        # -- round 13 (peer adoption QA) --
        def main_out(self, payload):
            old_in, old_out, old_env = sys.stdin, sys.stdout, dict(os.environ)
            sys.stdin, sys.stdout = io.StringIO(json.dumps(payload)), io.StringIO()
            try:
                os.environ.pop("AIQT_HOOKS_WORKER", None)
                os.environ.pop("ORCH_WORKER", None)  # a worker environment would skip the hook entirely
                os.environ.pop("ORCH_VERIFY_OWNER", None)
                self.assertEqual(main(["future-stamp-write.py"]), 0)
                return sys.stdout.getvalue()
            finally:
                sys.stdin, sys.stdout = old_in, old_out
                os.environ.clear()
                os.environ.update(old_env)

        def test_r13_write_word_only_in_command_position(self):
            # item 3 (false deny): every argument's basename was compared against WRITE_COMMANDS
            store = f"{PROJ}/private/state.md"
            reads = (f"rg -e tee -e '2099-01-01T00:00Z' {store}", f"grep -n -e cp -e mv 2099-01-01T00:00Z {store}",
                     f"rg 'dd|truncate' {store} | grep 2099-01-01T00:00Z", f"echo install 2099-01-01T00:00Z; cat {store}",
                     f"sudo rg -e tee 2099-01-01T00:00Z {store}", f"env LC_ALL=C grep tee 2099-01-01T00:00Z {store}",
                     f"x=\"$(rg -c ed {store})\" 2099-01-01T00:00Z", f"find {PROJ}/private -name tee 2099-01-01T00:00Z")
            for cmd in reads:
                self.assertFalse(bash_writes(cmd), cmd)
                self.assertEqual(self.ev("Bash", command=cmd), [], cmd)
            self.assertEqual(self.main_out({"tool_name": "Bash", "tool_input": {"command": reads[0]}}), "")
            writes = ("echo 2099-01-01T00:00Z | tee -a S", "true && tee S <<< 2099-01-01T00:00Z",
                      "false || tee S <<< 2099-01-01T00:00Z", "true; cp /dev/shm/2099-01-01T00:00Z S",
                      "true\ntee S <<< 2099-01-01T00:00Z", "x=$(tee S <<< 2099-01-01T00:00Z)",
                      "x=`tee S <<< 2099-01-01T00:00Z`", "(tee S <<< 2099-01-01T00:00Z)",
                      "{ tee S <<< 2099-01-01T00:00Z; }", "if true; then tee S <<< 2099-01-01T00:00Z; fi",
                      "! tee S <<< 2099-01-01T00:00Z", "FOO=1 BAR=2 tee S <<< 2099-01-01T00:00Z",
                      "2>/dev/null tee S <<< 2099-01-01T00:00Z", "sudo -u root -n tee S <<< 2099-01-01T00:00Z",
                      "sudo --user root /usr/bin/tee S <<< 2099-01-01T00:00Z", "env -i FOO=1 tee S <<< 2099-01-01T00:00Z",
                      "env -u HOME -- tee S <<< 2099-01-01T00:00Z", "command tee S <<< 2099-01-01T00:00Z",
                      "nohup nice -n 5 tee S <<< 2099-01-01T00:00Z", "time -p tee S <<< 2099-01-01T00:00Z",
                      "timeout -s KILL 5 tee S <<< 2099-01-01T00:00Z", "flock -w 3 /dev/shm/l tee S <<< 2099-01-01T00:00Z",
                      "ls | xargs -I {} cp {} S 2099-01-01T00:00Z", "find . -name x -exec tee S \\; 2099-01-01T00:00Z",
                      "echo 2099-01-01T00:00Z | sudo sed -i 's/a/b/' S")
            for cmd in writes:
                cmd = cmd.replace("S", store)
                self.assertTrue(bash_writes(cmd), cmd)
                self.assertEqual(self.ev("Bash", command=cmd), ["2099-01-01T00:00Z"], cmd)
            self.assertIn("deny", self.main_out({"tool_name": "Bash", "tool_input": {
                "command": f"echo 2099-01-01T00:00Z | tee -a {store}"}}))
            # redirection, in-place, and substitution detection are position-free, as before
            for cmd in ("rg tee 2099-01-01T00:00Z > S", "rg x | sed -n -i 2099-01-01T00:00Z S",
                        "echo \"$(rg tee S)\" 2099-01-01T00:00Z >> S"):
                self.assertTrue(self.ev("Bash", command=cmd.replace("S", store)), cmd)

        def test_r13_quoted_text_in_store_write_exempt(self):
            # item 4 (false deny): a blockquote, inline code span, or fenced block holding a future literal
            fut = "2099-01-01T00:00Z"
            for content in (f"> quoted: {fut}\n", f"  > nested > {fut}\n", f"example `{fut}` shape\n",
                            f"example ``a ` {fut}`` shape\n", f"```\nhb: {fut}\n```\n", f"~~~~\n{fut}\n~~~~~\n",
                            f"| Item | Seen |\n|---|---|\n| x | `{fut}` |\n"):
                self.assertEqual(self.ev("Write", file_path=self.store, content=content), [], content)
            # bare observed-time fields stay checked, including beside an exempt copy on the same line
            for content in (f"Last-heartbeat: {fut}\n", f"`x` then {fut} outside\n", f"quote `{fut}` and {fut}\n",
                            f"unclosed `{fut} shape\n", f"```\nhb: {fut}\n", f"x > {fut}\n",
                            f"```\ncode\n```\nLast-heartbeat: {fut}\n"):
                self.assertEqual(self.ev("Write", file_path=self.store, content=content), [fut], content)
            # Edit: the reconstructed whole file decides; breaking a fence exposes its lines
            fp = self.store_file(f"# log\n```\nhb: {fut}\n```\nStatus: ok\n")
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="Status: ok", new_string=f"> was {fut}"), [])
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="hb: ", new_string="hb2: "), [])
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="```\nStatus", new_string="Status"), [fut])
            # the fragment path (file not read whole) exempts quote lines and code spans, never fences
            fp = self.store_file("x\n" * (EXISTING_MAX_BYTES // 2 + 1))
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="x", new_string=f"> {fut}\n`{fut}`"), [])
            self.assertEqual(self.ev("Edit", file_path=fp, old_string="x", new_string=f"```\n{fut}\n```"), [fut])
            # Bash is unchanged: text in a command is not markdown
            os.environ["AIQT_STORE_ROOT"] = STORE  # back to the fixture store root
            self.assertTrue(self.ev("Bash", command=f"echo '> {fut}' >> {PROJ}/private/s.md"))
            self.assertEqual(self.main_out({"tool_name": "Write", "tool_input": {"file_path": self.store,
                                                                                  "content": f"> {fut}\n"}}), "")
            self.assertIn("a composed stamp placed inside a quote", " ".join(__doc__.split()))

        def test_r13_code_quote_helpers_identical_to_sibling(self):
            sib = _sibling_or_skip("stamp-truth-stop.py")
            spec = importlib.util.spec_from_file_location("sts_sibling13", sib)
            mod = importlib.util.module_from_spec(spec)
            # the sibling is loaded with no bytecode written, so no __pycache__ is left beside the hooks
            old_dwb, sys.dont_write_bytecode = sys.dont_write_bytecode, True
            try:
                spec.loader.exec_module(mod)
            finally:
                sys.dont_write_bytecode = old_dwb
            for name in ("_code_lines", "_code_spans", "_in_spans"):
                self.assertEqual(inspect.getsource(getattr(mod, name)), inspect.getsource(globals()[name]), name)
            for name in ("_BTICK_RE", "_FENCE_RE"):
                self.assertEqual((getattr(mod, name).pattern, getattr(mod, name).flags),
                                 (globals()[name].pattern, globals()[name].flags), name)

        # -- round 14 --
        def r14(self, cmds, want):
            store = f"{PROJ}/private/state.md"
            for cmd in cmds:
                cmd = cmd.replace("S", store).replace("F", "2099-01-01T00:00Z")
                self.assertEqual(bash_writes(cmd), want, cmd)
                self.assertEqual(self.ev("Bash", command=cmd), ["2099-01-01T00:00Z"] if want else [], cmd)

        def test_r14_leading_input_redirection_keeps_command_position(self):
            # item 1 (regression from r12): a leading input redirection's operand was read as the command word
            self.r14(("<<<'Last-heartbeat: F' tee S", "< /dev/shm/in tee S F", "0</dev/shm/in tee S F",
                      "<&0 tee S F", "<<-E tee S\nF\nE", "sudo < /dev/shm/in tee S F", "echo F <> S",
                      "x=$(<<<F tee S)", "cat < /dev/shm/in | tee -a S F"), True)
            self.r14(("cat < S F", "rg x < S F", "while read l; do echo; done < <(rg x S) F",
                      "diff <(sort S) /dev/shm/b F", "rg -c x <<< F S"), False)

        def test_r14_exec_only_under_find(self):
            # item 2 (false deny): any -exec word re-entered command position, even outside find
            self.r14(("printf '%s\\n' '-exec' tee F S", "echo -ok cp F S", "rg -e -execdir -e mv F S",
                      "find . -exec rg x {} + -name tee F S", "find . -exec rg x {} \\; -name cp F S"), False)
            self.r14(("find . -name x -exec tee S \\; F", "sudo find . -okdir cp x S \\; F",
                      "find . -exec rg x {} + -exec tee S \\; F", "find . -execdir sed -i s/a/b/ S + F"), True)

        def test_r14_inplace_only_in_command_position(self):
            # item 3 (false deny): sed or perl as a mere argument (a search pattern) armed in-place detection
            self.r14(("rg -e sed -i -e F S", "grep -e perl -i F S", "echo sed -i F; cat S"), False)
            self.r14(("sed -i s/a/b/ S F", "echo F | sudo sed -i 's/a/b/' S", "perl -pi -e 1 S F",
                      "ls S | xargs sed -i s/a/F/", "LC_ALL=C sed --in-place s/a/b/ S F"), True)

        def test_r14_conditional_comparison_is_not_redirection(self):
            # item 5 (false deny): inside [[ ]], `>` and `<` compare strings
            self.r14(("stamp=$(cat S); [[ \"$stamp\" > 'F' ]]", "stamp=$(cat S); [[ \"$stamp\" < 'F' ]]",
                      "if [[ $(cat S) > F ]]; then echo late; fi", "[[ a > b && ( c < F ) ]] && cat S"), False)
            # [ ] and test are commands: an unquoted `>` there IS an output redirection (real bash creates b)
            self.r14(("[ \"$(cat F)\" > S ]", "test x > S F", "[[ a < b ]] && echo F > S",
                      "[[ $(tee S <<< F) > a ]]", "[[ a > b ]]; echo F >> S", "echo '[[' > S F",
                      "[[ a > b F S"), True)

        def test_r14_real_bash_agrees(self):
            # the redirection semantics the fixes rest on, observed in real bash
            if not shutil.which("bash"):
                self.skipTest("bash absent")
            cmds = ("<<<'x' tee o1 >/dev/null", "< in tee o2 >/dev/null", "0<in tee o3 >/dev/null",
                    "[ a > b1 ]", "[[ a > c1 ]]", "[[ z < c2 ]]", "test a > b2")
            with open(os.path.join(self.tmp, "in"), "w") as f:
                f.write("i\n")
            subprocess.run(["bash", "-c", "; ".join(cmds)], cwd=self.tmp, capture_output=True, timeout=5)
            made = set(os.listdir(self.tmp))
            self.assertTrue({"o1", "o2", "o3", "b1", "b2"} <= made, made)
            self.assertFalse({"c1", "c2"} & made, made)

        def test_r14_helpers_clear_worker_env(self):
            # item 4: main_out ran main() with ORCH_WORKER / ORCH_VERIFY_OWNER inherited, so main() skipped
            payload = {"tool_name": "Write", "tool_input": {"file_path": self.store, "content": "2099-01-01T00:00Z"}}
            for k, v in (("AIQT_HOOKS_WORKER", "1"), ("ORCH_VERIFY_OWNER", "x"), ("ORCH_WORKER", "1")):
                old = os.environ.get(k)
                os.environ[k] = v
                try:
                    self.assertIn("deny", self.main_out(payload))
                    self.assertEqual(os.environ.get(k), v)  # the environment is restored afterwards
                finally:
                    if old is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = old

        # -- round 15 --
        def test_r15_function_body_is_command_position(self):
            # item 1 (regression from r12/r13): `function NAME {` consumed command position, so an in-place
            # write in the body was missed; detected whether or not the function is called (conservative)
            self.r14(("function update_state { sed -i 's/OLD/F/' S; }; update_state",
                      "function update_state { perl -pi -e 's/OLD/F/' S; }; update_state",
                      "function update_state { sed -i 's/OLD/F/' S; }", "function u\n{ tee S <<< F; }",
                      "function u () { sed -i s/a/F/ S; }", "function u() ( perl -pi -e 1 S F )",
                      "update_state() { sed -i 's/OLD/F/' S; }; update_state", "u ()\n{ perl -pi -e 's/a/F/' S; }",
                      "u () ( sed -i s/a/b/ S F )", "function u ( sed -i s/a/b/ S F )",
                      "x=$(function u { tee S <<< F; }; u)", "if true; then function u { cp F S; }; fi"),
                     True)
            # the header words are never command words, and a conditional in a body still compares
            self.r14(("rg function S -e F", "function tee { rg x S F; }", "function u { [[ $(cat S) > F ]]; }",
                      "u() { [[ $(cat S) < F ]] && echo late; }", "echo function sed -i F S"), False)

        def test_r15_time_and_negation_keep_conditional(self):
            # item 2 (false deny): command position was lost at `time`, so `[[` was not a conditional and its
            # `>` read as a redirection
            self.r14(("if time [[ $(cat S) > F ]]; then echo corrupt; fi", "time [[ $(cat S) > F ]]",
                      "time -p [[ $(cat S) > F ]] && echo corrupt", "time -- [[ $(cat S) > F ]]",
                      "time -p -- [[ $(cat S) > F ]]", "! [[ $(cat S) > F ]] && echo ok",
                      "if ! time [[ $(cat S) > F ]]; then echo corrupt; fi", "time ! [[ $(cat S) > F ]]",
                      "x=$(time [[ $(cat S) > F ]] && echo late)"), False)
            # bash runs a command after `time -p -p`, `time -x`, or `time -- -p`: there `>` IS a redirection;
            # and a write after time or ! is still a write
            self.r14(("time -p -p [[ a > S F", "time -x [[ a > S F", "time -- -p [[ a > S F",
                      "time echo F > S", "time -p tee S <<< F", "! tee S <<< F", "time ! sed -i s/a/F/ S"), True)

        def test_r15_real_bash_agrees(self):
            # the reserved-word and function semantics the fixes rest on, observed in real bash
            if not shutil.which("bash"):
                self.skipTest("bash absent")
            cmds = ("time [[ a > c1 ]]", "time -p [[ a > c2 ]]", "time -- [[ a > c3 ]]", "time -p -- [[ a > c4 ]]",
                    "time ! [[ a > c5 ]]", "! time [[ a > c6 ]]", "if time [[ a > c7 ]]; then :; fi",
                    "time -p -p [[ a > b1 ]]", "time -x [[ a > b2 ]]", "time -- -p [[ a > b3 ]]",
                    "function f { echo > b4; }; f", "g() { echo > b5; }; g", "function h { [[ a > c8 ]]; }; h")
            for c in cmds:
                subprocess.run(["bash", "-c", c], cwd=self.tmp, capture_output=True, timeout=5)
            made = set(os.listdir(self.tmp))
            self.assertTrue({"b1", "b2", "b3", "b4", "b5"} <= made, made)
            self.assertFalse({"c%d" % k for k in range(1, 9)} & made, made)

        # -- round 16 --
        def test_r16_arithmetic_is_not_redirection(self):
            # codex round-15 finding (false deny): `>` inside `(( ... ))` and `$(( ... ))` read as a redirection
            self.r14(("count=$(grep -Fc 'F' S)\nif (( count > 0 )); then printf '%s\\n' \"$count\"; fi",
                      "count=$(grep -Fc 'F' S)\nprintf '%s\\n' \"$(( count > 0 ))\"",
                      "rg -c F S; (( x = 1 << 2 ))", "rg -c F S; echo $(( a >> 1 ))", "rg F S; (( a > b ))",
                      "rg F S; echo $(( (a) > (b) ))", "rg F S; for ((i = 0; i < 2; i++)); do (( i > 0 )); done",
                      "rg F S; for (( ; ; )) do break; done", "x=$(( $(rg -c F S) > 1 ))",
                      "if ! (( $(rg -c F S) > 0 )); then echo none; fi", "time (( $(rg -c F S) > 0 ))",
                      "x=$(rg F S; (( 2 > 1 )) && echo y)", "echo \"$(( `rg -c F S` > 0 ))\"",
                      "(( a > b )) && rg F S", "echo $(( 16#ff > 1 )) F S", "x=$(( $(( 1 > 0 )) << 1 )) F S"),
                     False)
            # a write inside a substitution nested in arithmetic, a real redirect after `))`, a `((` not in
            # command position, and the unemulated `((` fallback all still count as writes
            self.r14(("(( $(echo F > S; echo 1) > 0 ))", "echo $(( `tee S <<< F` + 1 ))",
                      "x=$(( $(sed -i s/a/F/ S) + 1 ))", "echo F | (( 1 > 0 )) > S", "echo $(( 1 )) > S F",
                      "for ((i = 0; i < 1; i++)); do :; done > S F", "if echo F | (( 1 > 0 )) > S; then :; fi",
                      "echo ((x > S)) F", "rg for ((x > S)) F", "((echo F) > S)", "x=$((cat S) > S) F",
                      "$(( 1 > 0 S F"), True)

        def test_r16_real_bash_agrees(self):
            # the arithmetic semantics the fix rests on, observed in real bash
            if not shutil.which("bash"):
                self.skipTest("bash absent")
            cmds = ("count=2; if (( count > 0 )); then :; fi", "count=2; printf '%s' \"$(( count > 0 ))\"",
                    "(( x = 1 << 2 ))", "a=8; echo $(( a >> 1 ))", "for ((i = 0; i < 2; i++)); do (( i > 0 )); done",
                    "(( $(echo > b1; echo 1) > 0 ))", "(( 1 > 0 )) > b2", "((echo x) > b3)",
                    "x=$((echo x) > b4)", "echo $(( `echo > b5` 1 > 0 ))")
            before = set(os.listdir(self.tmp))
            for c in cmds:
                subprocess.run(["bash", "-c", c], cwd=self.tmp, capture_output=True, timeout=5)
            self.assertEqual(set(os.listdir(self.tmp)) - before, {"b1", "b2", "b3", "b4", "b5"})

        # -- round 17 --
        def test_r17_for_arith_header_restores_command_position(self):
            # codex round-16 finding (MISS, a regression from r12/r15): after a `for ((...))` header the body
            # lost command position, so `do tee` and `do sed -i` were missed; bash takes `do` with or without a
            # `;` or newline before it, and `{` in place of `do`
            self.r14(("for ((i=0;i<1;i++)) do tee S <<< 'Last-heartbeat: F'; done",
                      "for ((i=0;i<1;i++)); do tee S <<< 'Last-heartbeat: F'; done",
                      "for ((i=0;i<1;i++))\ndo tee S <<< 'Last-heartbeat: F'; done",
                      "for ((i=0;i<1;i++))do tee S <<< F; done", "for ((i=0;i<1;i++)) ;do tee S <<< F; done",
                      "for ((i=0;i<1;i++)) do sed -i 's/Last/F/' S; done",
                      "for ((i=0;i<1;i++)); do sed -i 's/Last/F/' S; done",
                      "for((i=0;i<1;i++)) do sed -i 's/Last/F/' S; done",
                      "for ((i=0;i<1;i++)) { tee S <<< F; }", "for (( ; ; )) do cp /dev/shm/x S; break; done F",
                      "x=$(for ((i=0;i<1;i++)) do tee S <<< F; done)",
                      "for ((i=0;i<1;i++)) do for ((j=0;j<1;j++)) do tee S <<< F; done; done"), True)
            self.r14(("for ((i=0;i<1;i++)) do rg -c F S; done", "for ((i=0;i<1;i++)) do echo tee S F; done",
                      "for ((i=0;i<1;i++)) { rg F S; }", "rg F S; for ((i=0;i<2;i++)) do (( i > 0 )); done"),
                     False)
            # the tokenizer emits a separator after the closed header, so the stream sees `do` in command position
            toks = _shell_tokens("for ((i=0;i<1;i++)) do tee x; done")[-1]
            self.assertEqual(toks[:4], [("w", "for"), ("w", "$"), ("s",), ("w", "do")], toks)

        def test_r17_keyword_touching_arith_is_arithmetic(self):
            # codex round-16 finding (false deny): the reserved word before `((` was not flushed at the `(`
            # metacharacter, so `if((` read as a subshell and its `>` as a redirection
            self.r14(("count=$(grep -Fc 'F' S); if(( count > 0 )); then echo found; fi",
                      "count=$(grep -Fc 'F' S); while(( count > 5 )); do break; done",
                      "count=$(grep -Fc 'F' S); until(( count > -1 )); do break; done",
                      "if false; then :; elif(( $(rg -c F S) > 0 )); then echo found; fi",
                      "count=$(rg -c F S); !(( count > 5 )) && echo ok", "if !(( $(rg -c F S) > 0 )); then :; fi",
                      "rg F S; for((i=0;i<2;i++)); do (( i > 0 )); done", "x=$(if(( 2 > 1 )); then rg F S; fi)"),
                     False)
            # a non-keyword word touching `((` is still no arithmetic, and a write after `if((` still counts
            self.r14(("if(( 1 > 0 )); then tee S <<< F; fi", "echo((x > S)) F", "rg((x > S)) F",
                      "while(( 1 > 0 )); do sed -i s/a/F/ S; break; done"), True)

        def test_r17_real_bash_agrees(self):
            # the syntax the fixes rest on, observed in real bash: the header body runs without a `;` before
            # `do`, and a keyword touching `((` opens arithmetic (no file named by the `>` operand appears)
            if not shutil.which("bash"):
                self.skipTest("bash absent")
            cmds = ("for ((i=0;i<1;i++)) do echo > b1; done", "for ((i=0;i<1;i++))do echo > b2; done",
                    "for ((i=0;i<1;i++)) { echo > b3; }", "for((i=0;i<1;i++)) do echo > b4; done",
                    "c=2; if(( c > c1 )); then :; fi", "c=2; while(( c > c2 )); do break; done",
                    "c=2; until(( c > c3 )); do break; done", "if false; then :; elif(( 1 > c4 )); then :; fi",
                    "!(( 1 > c5 ))")
            before = set(os.listdir(self.tmp))
            for c in cmds:
                subprocess.run(["bash", "-c", c], cwd=self.tmp, capture_output=True, timeout=5)
            self.assertEqual(set(os.listdir(self.tmp)) - before, {"b1", "b2", "b3", "b4"})

        # -- round 18 --
        def test_r18_function_compound_body_without_parens(self):
            # round-17 finding (false deny): after `function NAME` with no `()` the body word lost command
            # position, so `function NAME (( ... ))` read its `>` as a redirection; a sibling of the same class
            # (a MISS) hid a write in a `function NAME while|until|if` body
            self.r14(("count=$(grep -c 'F' S); function has_matches (( count > 0 ))", "function g [[ 1 < 2 ]]",
                      "function g [[ $(rg -c F S) > 0 ]]", "count=$(rg -c F S); function h (( count > 0 )); h",
                      "function h (( $(rg -c F S) >> 1 ))", "function w while rg -q F S; do break; done",
                      "function tee { rg x S F; }", "function u { [[ $(cat S) > F ]]; }"), False)
            self.r14(("function f { tee S <<< 'F'; }", "function f (( $(tee S <<< F) ))",
                      "function f (( `tee S <<< F` ))", "function f [[ $(tee S <<< F) ]]",
                      "function f (( 1 > 0 )) > S; echo F", "function f (( 1 > 0 )) >> S F",
                      "function f [[ 1 < 2 ]] > S F", "function f ( tee S <<< F )",
                      "function f while tee S <<< F; do break; done", "function f if tee S <<< F; then :; fi", "function f for ((i=0;i<1;i++)) do tee S <<< F; done",
                      "x=$(function f (( 1 > 0 )) > S; echo F)"), True)
            # an `until` body is in command position too (the literal right after `until` would be exempt as a
            # schedule, so the write test is asserted directly)
            self.assertTrue(bash_writes(f"function f until tee {PROJ}/private/state.md; do :; done"))
            self.assertFalse(bash_writes(f"function f until rg -q x {PROJ}/private/state.md; do :; done"))
            # the tokenizer opens an arithmetic frame for the body: one `$` word, no redirection token (and, since
            # round 19, the separator that restores command position after a closed arithmetic command)
            self.assertEqual(_shell_tokens("function f (( c > 0 ))")[-1],
                             [("w", "function"), ("w", "f"), ("w", "$"), ("s",)])

        def test_r18_real_bash_agrees(self):
            # the syntax the fix rests on, observed in real bash: after `function NAME` with no `()`, a `((` or
            # `[[` body is arithmetic or a conditional (no file named by a `>` operand appears), and an if,
            # while, until, for, or subshell body runs its commands; a redirection after the body still applies
            if not shutil.which("bash"):
                self.skipTest("bash absent")
            cmds = ("function f (( 1 > c1 )); f", "function g [[ b > c2 ]]; g", "function h if true; then echo > b1; fi; h",
                    "function k while echo > b2; do break; done; k", "function m until echo > b3; do :; done; m",
                    "function n ( echo > b4 ); n", "function p (( 1 )) > b5; p",
                    "function q for ((i=0;i<1;i++)) do echo > b6; done; q")
            before = set(os.listdir(self.tmp))
            for c in cmds:
                subprocess.run(["bash", "-c", c], cwd=self.tmp, capture_output=True, timeout=5)
            self.assertEqual(set(os.listdir(self.tmp)) - before, {"b1", "b2", "b3", "b4", "b5", "b6"})

        # -- round 19 --
        def test_r19_arith_and_conditional_command_restore_command_position(self):
            # round-18 finding (MISS, a round-12 regression): only a `for ((...))` header restored command position
            # at its closing `))`, so a `while`, `until`, or `if` condition that is a plain `((...))` or a
            # `[[ ... ]]`, followed by `do` or `then` with no `;`, hid the body's write
            self.r14(("until ((a>b)) do tee S <<< 'Last-heartbeat: F'; break; done",
                      "while !((a>0)) do tee S <<< F; break; done", "until ((a>b)) do sed -i s/x/F/ S; done",
                      "if (( 1 > 0 )) then tee S <<< F; fi", "while [[ 1 > 0 ]] do tee S <<< F; break; done",
                      "if [[ 1 > 0 ]] then tee S <<< F; fi", "until [[ a > b ]] do sed -i s/x/F/ S; done",
                      "if false; then :; elif (( 1 )) then tee S <<< F; fi", "x=$(if (( 1 > 0 )) then tee S <<< F; fi)",
                      "while ! [[ -n x ]] do cp /dev/shm/x S; break; done F", "(( x )) > S F", "[[ x ]] > S F",
                      "if echo F | (( 1 )) > S; then :; fi"), True)
            # pure arithmetic and conditional reads in the same no-separator form stay reads, and a `$((` expansion
            # does not restore command position (its value is a word of the enclosing command)
            self.r14(("until ((a>b)) do rg F S; break; done", "if (( 1 > 0 )) then rg -c F S; fi",
                      "while [[ 1 > 0 ]] do rg F S; break; done", "if [[ $(cat S) > F ]] then echo later; fi",
                      "while !((a>0)) do echo tee S F; break; done", "if (( $(rg -c F S) > 0 )) then echo y; fi",
                      "(( x )) && rg F S", "[[ x ]] && rg F S", "echo $(( 1 > 0 )) tee F S", "rg $((1)) tee F S",
                      "printf '%s' \"$(( 1 > 0 ))\" do tee F S"), False)
            toks = _shell_tokens("until ((a>b)) do tee x; done")[-1]
            self.assertEqual(toks[:4], [("w", "until"), ("w", "$"), ("s",), ("w", "do")], toks)
            toks = _shell_tokens("while [[ a > b ]] do tee x; done")[-1]
            self.assertEqual(toks[:6], [("w", "while"), ("w", "[["), ("w", "a"), ("w", "b"), ("w", "]]"), ("s",)], toks)
            self.assertEqual(_shell_tokens("echo $((1)) tee")[-1], [("w", "echo"), ("w", "$"), ("w", "tee")])

        def test_r19_conditional_operand_text_is_not_a_separator(self):
            # codex round-18 finding (false deny, pre-existing): inside [[ ... ]] a regex operand's `(`, `)`, and
            # `|` were separators, so `cp` in `=~ operation=(cp|mv)` read as a command word
            self.r14(("record=$(grep -c 'F' S)\n[[ \"$record\" =~ operation=(cp|mv) ]]",
                      "record=$(grep -c 'F' S); function check [[ \"$record\" =~ operation=(cp|mv) ]]",
                      "record=$(grep -c 'F' S); function check [[ $record =~ (tee|dd) ]]; check",
                      "rg F S; [[ a && (b || c) ]]", "rg F S; [[ x =~ ^(tee|dd)$ ]] && echo y",
                      "rg F S; [[ ( a > b ) || (cp) ]]", "if [[ $(rg -c F S) =~ (sed|tee) ]] then echo y; fi"), False)
            # a substitution inside the conditional is still its own scanned stream, and a redirection after `]]`
            # still counts; a `>(...)` process substitution is now scanned too (round 18 dropped its `>` inside [[)
            self.r14(("[[ x =~ ($(tee S <<< F)|y) ]]", "[[ x =~ (`tee S <<< F`|y) ]]",
                      "function check [[ x =~ ($(tee S <<< F)) ]]", "[[ -n <(tee S <<< F) ]]",
                      "[[ -e >(cat > S <<< F) ]]", "[[ x =~ (cp|mv) ]] > S F",
                      "[[ x =~ (cp|mv) ]] && cp F S"), True)

        def test_r19_real_bash_agrees(self):
            # the syntax the fixes rest on, observed in real bash: a `((...))` or `[[ ... ]]` condition takes `do`
            # or `then` with no `;` and runs the body; a regex operand's `(`, `)`, and `|` are operand text (no
            # file named by a `>` operand appears), and a substitution inside the conditional still runs
            if not shutil.which("bash"):
                self.skipTest("bash absent")
            cmds = ("a=0; b=1; until ((a>b)) do echo > b1; break; done", "a=0; while !((a>0)) do echo > b2; break; done",
                    "if (( 1 > 0 )) then echo > b3; fi", "while [[ 1 > 0 ]] do echo > b4; break; done",
                    "if [[ b > a ]] then echo > b5; fi", "r=operation=cp; [[ $r =~ operation=(cp|mv) ]] && echo > b6",
                    "[[ x =~ ($(echo > b7)|y) ]]", "function f [[ a =~ (b|c) ]]; f; [[ 1 > c1 ]]",
                    "if (( 2 > c2 )) then :; fi", "[[ a && (b || c > c3) ]]")
            before = set(os.listdir(self.tmp))
            for c in cmds:
                subprocess.run(["bash", "-c", c], cwd=self.tmp, capture_output=True, timeout=5)
            self.assertEqual(set(os.listdir(self.tmp)) - before, {"b1", "b2", "b3", "b4", "b5", "b6", "b7"})

        # -- round 20 --
        def test_r20_conditional_hash_is_operand_text(self):
            # codex round-19 finding (false deny, present since round 17): inside [[ ... ]] the `#` of a regex
            # operand such as `(#|$)` started a comment that swallowed the closing `]]`, so the command read as
            # an unterminated conditional (a write); a newline inside the conditional is no separator either
            self.r14(("record=$(grep 'F' S)\n[[ \"$record\" =~ ^[[:space:]]*(#|$) ]]",
                      "record=$(grep 'F' S); [[ \"$record\" =~ ^[[:space:]]*(#|$) ]] && echo skip",
                      "record=$(grep 'F' S); [[ $record =~ ^# ]] && echo comment", "rg F S; [[ x =~ a#b ]] || echo no",
                      "record=$(grep 'F' S); if [[ $record =~ (#|;) ]] then echo y; fi",
                      "rg F S; [[ a &&\n tee ]] && echo ok", "rg F S; if [[ a ||\n cp ]]; then echo y; fi"), False)
            self.r14(("[[ x =~ (#|$) ]] && tee S <<< F", "[[ x =~ a#b ]]\ntee S <<< F", "[[ x =~ ^# ]] > S F",
                      "[[ x =~ (#|$($(tee S <<< F))) ]]"), True)

        def test_r20_conditional_closes_only_at_a_closing_word(self):
            # codex round-19 finding (miss): a `]]` glued to a regex word's `)` closed the conditional, so the
            # real closing `]]` read as a command word and the `then` body after it lost command position
            self.r14(("if [[ 'x]]' =~ (x)]] ]] then tee S <<< 'Last-heartbeat: F'; fi",
                      "if [[ 'x]]' =~ (x)]] ]]\nthen tee S <<< 'Last-heartbeat: F'; fi",
                      "[[ x == x]] ]] && tee S <<< F", "[[ a ]]>S F", "while [[ a =~ (b)]] ]] do tee S <<< F; done",
                      "[[ ( a )]] && tee S <<< F"), True)
            # a regex word glued to `]]` before the real `]]` is no false deny (round 22: a grouping `)` glued to
            # `]]` with no later close leaves the command unparseable; see test_r22_*)
            self.r14(("rg F S; [[ x =~ (a)]] ]] && echo ok",
                      "rg F S; ( [[ x ]])", "if [[ $(rg -c F S) =~ (x)]] ]] then echo y; fi"), False)
            self.assertEqual(_shell_tokens("[[ a =~ (#|x)]] ]] && tee y")[-1],
                             [("w", "[["), ("w", "a"), ("w", "=~"), ("w", "#x]]"), ("w", "]]"), ("s",), ("s",),
                              ("s",), ("w", "tee"), ("w", "y")])
            self.assertIsNone(_shell_tokens("[[ ( a )]] && echo ok"))  # round 22: no loose re-read

        def test_r20_real_bash_agrees(self):
            # the syntax round 20 rests on, observed in real bash: a `#` inside a regex word is no comment, a `]]`
            # glued to a regex word's `)` does not close the conditional while one glued to a grouping `)` does, a
            # newline after `&&` stays inside the conditional, and a `>` right after the closing `]]` redirects
            if not shutil.which("bash"):
                self.skipTest("bash absent")
            cmds = ("r='  #x'; [[ $r =~ ^[[:space:]]*(#|$) ]] && echo > b1", "[[ '#' =~ ^# ]] && echo > b2",
                    "[[ a#b =~ a#b ]] && echo > b3", "if [[ 'x]]' =~ (x)]] ]] then echo > b4; fi",
                    "if [[ 'x]]' =~ (x)]] ]]\nthen echo > b5; fi", "[[ ( a )]] && echo > b6",
                    "[[ a &&\n b ]] && echo > b7", "[[ a ]]>b8", "[[ ';' =~ (#|;) ]] && echo > b9",
                    "[[ x =~ (a>c1) ]]", "[[ x == x]] ]] && [[ y > c2 ]]")
            before = set(os.listdir(self.tmp))
            for c in cmds:
                subprocess.run(["bash", "-c", c], cwd=self.tmp, capture_output=True, timeout=5)
            self.assertEqual(set(os.listdir(self.tmp)) - before, {"b%d" % k for k in range(1, 10)})

        # -- round 21 --
        def test_r21_touching_conditional_opener_is_no_separator(self):
            # codex round-20 finding (false deny, an r20 regression): in a compact `[[(`, flushing `[[` opened the
            # conditional only after its branch was passed, so the `(` became a separator (and, in a substitution,
            # a depth change that left the substitution unterminated)
            self.r14(("result=$( [[(-f S) ]] && grep -c 'F' S )", "result=$( [[(-f S) ]] &&\n grep -c 'F' S )",
                      "record=$(grep 'F' S); [[(tee == \"$record\") ]] && echo same",
                      "rg F S; [[((-n x)) ]] && echo ok", "rg F S; [[<(rg F S) ]] || echo no"), False)
            self.r14(("[[(-f S) ]] && tee S <<< F", "[[(x) ]]>S F", "x=$( [[(-n y) ]] && tee S <<< F )"), True)
            self.assertEqual(_shell_tokens("[[(tee == x) ]] && y")[-1],
                             [("w", "[["), ("w", "tee"), ("w", "=="), ("w", "x"), ("w", "]]"), ("s",), ("s",),
                              ("s",), ("w", "y")])

        def test_r21_glued_close_never_closes_before_a_later_close(self):
            # codex round-20 finding (false deny): the removed alternate reading closed at the regex word `(x)]]`
            # and invented a redirection out of the string comparison `>` after it; with the single rule a `]]`
            # glued to preceding text never closes while a word-initial `]]` follows
            self.r14(("record=$(grep 'F' S)\n[[ \"$record\" =~ (x)]] || \"$record\" > 'F' ]]",
                      "record=$(grep 'F' S); [[ \"$record\" =~ (x)]] || \"$record\" > 'F' ]] && echo ok"), False)
            # the round-19 item-2 miss stays caught, and a lone glued close (unparseable since round 22) still
            # denies a write after it
            self.r14(("if [[ 'x]]' =~ (x)]] ]] then tee S <<< 'Last-heartbeat: F'; fi",
                      "[[ ( -n x )]] && tee S <<< F", "[[ ( a )]] && [[ ( b )]] && tee S <<< F"), True)
            # a strict parse is kept as is
            self.assertEqual(_shell_tokens("[[ x =~ (a)]] ]] && y")[-1],
                             [("w", "[["), ("w", "x"), ("w", "=~"), ("w", "a]]"), ("w", "]]"), ("s",), ("s",),
                              ("s",), ("w", "y")])
            self.assertIsNone(_shell_tokens("[[ ( a )]] && echo 'x"))
            # the disclosed miss (module docstring): a glued grouping `)]]`, then a write, then a later
            # word-initial `]]`; pinned so a future fix flips this deliberately
            self.assertFalse(bash_writes(f"[[ ( a )]] && tee {PROJ}/private/state.md <<< 2099-01-01T00:00Z; [[ x ]]"))

        def test_r21_real_bash_agrees(self):
            # the syntax round 21 rests on, observed in real bash: a compact `[[(` opens the conditional, a regex
            # word `(x)]]` does not close it before a later `]]` (so the `>` after it compares strings), a comment
            # may follow the opening `[[`, a lone `|` is a legal regex operand, and the disclosed miss is real
            if not shutil.which("bash"):
                self.skipTest("bash absent")
            cmds = ("[[(-n x) ]] && echo > b1", "[[ \"x\" =~ (x)]] || \"x\" > c1 ]] && echo > b2",
                    "[[ # c\n a ]] && echo > b3", "[[ a =~ | ]] && echo > b4", "[[((-n x)) ]] && echo > b5",
                    "[[(tee == \"x\") ]] || echo > b6", "r=$( [[(-f c2) ]] && echo hi ); echo > b7",
                    "[[ ( a )]] && echo > b8; [[ x ]]")
            before = set(os.listdir(self.tmp))
            for c in cmds:
                subprocess.run(["bash", "-c", c], cwd=self.tmp, capture_output=True, timeout=5)
            self.assertEqual(set(os.listdir(self.tmp)) - before, {"b%d" % k for k in range(1, 9)})

        # -- round 22 --
        def test_r22_conditional_open_at_frame_end_is_unparseable(self):
            # codex round-21 finding (miss, an r21 regression): a backtick frame closed while its conditional was
            # open, so the round-21 fallback never ran and the write read as conditional operand text; the
            # fallback is removed, and a conditional open when its frame ends makes the command unparseable
            self.r14(("result=`[[ ( -n x )]] && tee S <<< 'Last-heartbeat: F'`",
                      "result=`[[ ( -n x )]]` && tee S <<< F", "x=$( [[ ( -n y )]] && tee S <<< F )",
                      "r=`[[` && tee S <<< F"), True)
            for c in ("r=`[[ ( -n x )]] && echo ok`", "r=`[[`", "r=$( [[ a )", "[[ ( -n x )]] && echo ok",
                      "[[ a ]] && [[ ( b )]] && echo ok", "[[ ( a )]] && echo 'x"):
                self.assertIsNone(_shell_tokens(c), c)
            self.assertEqual(list(inspect.signature(_shell_tokens).parameters), ["cmd"])  # no re-read machinery

        def test_r22_glued_group_close_is_disclosed_false_deny(self):
            # codex round-21 finding (false deny from the fallback's loose closing, now removed): the rule leaves
            # the first conditional's glued `)]]` unclosed, so the command is unparseable and denies only
            # because it names a store and holds a future literal (the disclosed exotic false deny); the same
            # holds for the former glued-close allows, pinned here so a future fix flips them deliberately
            self.r14(("record=$(grep 'F' S)\n[[ ( -n x )]] # it's fine\n[[ \"$record\" =~ (x)]] || \"$record\" > 'F' ]]",
                      "rg F S; [[ ( -n x )]] && echo ok", "rg F S; [[ x =~ (a)]] && echo ok",
                      "rg F S; [[ ( a )]] # it's fine"), True)
            # with no store path and no future literal an unparseable command has nothing to deny: allowed
            # end to end through the hook entry point
            for c in ("[[ ( -n x )]] && echo ok", "result=`[[ ( -n x )]] && echo ok`"):
                self.assertTrue(bash_writes(c), c)
                self.assertEqual(self.ev("Bash", command=c), [], c)
                payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": c}, "cwd": f"{PROJ}/repo"})
                old_in, old_out, old_env = sys.stdin, sys.stdout, dict(os.environ)
                sys.stdin, sys.stdout = io.StringIO(payload), io.StringIO()
                try:
                    os.environ.pop("AIQT_HOOKS_WORKER", None)
                    os.environ.pop("ORCH_WORKER", None)
                    os.environ.pop("ORCH_VERIFY_OWNER", None)
                    rc = main(["future-stamp-write.py"])
                    out = sys.stdout.getvalue()
                finally:
                    sys.stdin, sys.stdout = old_in, old_out
                    os.environ.clear()
                    os.environ.update(old_env)
                self.assertEqual((rc, out), (0, ""), c)

        def test_r22_real_bash_agrees(self):
            # the syntax round 22 rests on, observed in real bash: a glued grouping `)]]` closes the conditional
            # inside a backtick or `$(...)` frame and at top level (so the backtick counterexample writes), and
            # codex's three-line command writes nothing (the pinned deny above is a genuine false deny)
            if not shutil.which("bash"):
                self.skipTest("bash absent")
            cmds = ("r=`[[ ( -n x )]] && echo > b1`", "r=$( [[ ( -n x )]] && echo > b2 )",
                    "[[ ( -n x )]] && echo > b3",
                    "r=x\n[[ ( -n x )]] # it's fine\n[[ \"$r\" =~ (x)]] || \"$r\" > 'c1' ]]")
            before = set(os.listdir(self.tmp))
            for c in cmds:
                subprocess.run(["bash", "-c", c], cwd=self.tmp, capture_output=True, timeout=5)
            self.assertEqual(set(os.listdir(self.tmp)) - before, {"b1", "b2", "b3"})

        # -- round 23 --
        def test_r23_word_initial_close_before_closing_backtick(self):
            # codex round-22 finding (false deny, an r22 regression): a word-initial `]]` glued to the closing
            # backtick of the backtick frame being scanned was not a close, so the conditional was still open at
            # the frame end and a read was unparseable; that backtick now closes it, as `)` does for `$(...)`
            self.r14(("record=`grep 'F' S && [[ -s S ]]`", "record=`grep 'F' S && [[ -s S ]] `",
                      "record=`grep 'F' S && [[ -s S ]]\t`", "record=`grep 'F' S && [[ -s S ]]\n`",
                      "record=$(grep 'F' S && [[ -s S ]])", "record=$(grep 'F' S && [[ -s S ]] )",
                      "x=$(echo `rg F S && [[ -s S ]]`)", "r=`[[ -s S ]]`; rg F S"), False)
            self.assertEqual(_shell_tokens("r=`g && [[ -s x ]]`")[0],
                             [("w", "g"), ("s",), ("s",), ("w", "[["), ("w", "-s"), ("w", "x"), ("w", "]]"), ("s",)])
            # the round-21 glued-grouping backtick write still denies, and a write after the close is caught
            self.r14(("result=`[[ ( -n x )]] && tee S <<< 'Last-heartbeat: F'`",
                      "result=`[[ -n x ]]` && tee S <<< F", "result=`[[ -n x ]] && tee S <<< F`"), True)
            # genuinely unclosed conditionals stay unparseable: a glued close, a `]]` glued to a following word,
            # a top-level `]]` before an OPENING backtick (not a frame end), and a backtick that closes a
            # different frame than the one the conditional is in
            for c in ("r=`[[ -s x`", "r=`[[ -s x ]]y`", "r=`[[ ( -n x )]]`", "[[ -s x ]]`echo`",
                      "r=`[[ -s x ]]\\``", "x=$( [[ -s y ]]` )", "[[ -s x ]]`"):
                self.assertIsNone(_shell_tokens(c), c)

        def test_r23_real_bash_agrees(self):
            # the syntax round 23 rests on, observed in real bash: a word-initial `]]` glued to the closing
            # backtick closes the conditional (the command runs and its status is the conditional's), while a
            # `]]` glued to a following word inside the backtick does not
            if not shutil.which("bash"):
                self.skipTest("bash absent")
            ok = subprocess.run(["bash", "-c", "r=`[[ -n x ]]` && r=`[[ -n x ]] ` && echo > b1"], cwd=self.tmp,
                                capture_output=True, timeout=5)
            bad = subprocess.run(["bash", "-c", "r=`[[ -n x ]]y` && echo > b2"], cwd=self.tmp, capture_output=True,
                                 timeout=5)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertNotEqual(bad.returncode, 0)
            self.assertTrue(os.path.exists(os.path.join(self.tmp, "b1")))
            self.assertFalse(os.path.exists(os.path.join(self.tmp, "b2")))


        # -- round 24 (field validation of round 12) --
        def test_r24_item3_write_bound_to_store_target(self):
            # item 3: a write-indicator word, a store path, and a future literal merely co-occurring denied
            # read-only searches and writes elsewhere; a Bash command is denied only when a write TARGETS the store
            S, F = f"{PROJ}/private/state.md", "2099-01-01T00:00Z"
            allowed = (f"rg {F} {S} > /dev/shm/hits.txt", f"rg {F} {S} | tee /dev/shm/hits.txt",
                       f"cp {S} /dev/shm/copy.md && rg {F} /dev/shm/copy.md", f"cp -t /dev/shm/out {S}; echo {F}",
                       f"grep -c x {S} > /dev/null; echo {F} > /dev/shm/note.txt",
                       f"install -m 644 {S} /dev/shm/x.md <<< {F}", f"dd if={S} of=/dev/shm/x bs=1 <<< {F}",
                       f"mv {S} /dev/shm/old.md; echo {F}")
            for cmd in allowed:
                self.assertEqual(self.ev("Bash", command=cmd), [], cmd)
            denied = (f"echo 'Last-heartbeat: {F}' > {S}", f"echo 'hb {F}' | tee -a {S}",
                      f"sed -i 's/^hb.*/hb {F}/' {S}", f"perl -pi -e 's/x/{F}/' {S}",
                      f"cp /dev/shm/x.md {S} <<< 'hb {F}'", f"printf 'hb {F}' | dd of={S}",
                      f"cp -t {PROJ}/private /dev/shm/{F}.md", f"cp --target-directory={S} x <<< {F}",
                      f"install -m 644 /dev/shm/x {S} <<< {F}", f"mv /dev/shm/{F}.md {S}", f"truncate -s 0 {S} <<< {F}",
                      f"X={S}; echo {F} > \"$X\"", f"find {PROJ}/private -exec sed -i s/a/{F}/ {{}} \\;",
                      f"ls {S} | xargs sed -i s/a/{F}/", f"echo {F} > {S} 2>/dev/null",
                      f"x=$(tee {S} <<< {F})")
            for cmd in denied:
                self.assertEqual(self.ev("Bash", command=cmd), [F], cmd)
            self.assertEqual(_write_targets("cp", ["-S", ".bak", "a", "b", "-t", "d1", "--target-directory=d2"]),
                             ["d1", "d2"])
            self.assertEqual(_write_targets("install", ["-m", "644", "src", "dst"]), ["dst"])
            self.assertEqual(_write_targets("tee", ["-a", "--", "-x", "f"]), ["-x", "f"])

        def test_r24_item4_unreadable_target_fails_open(self):
            # item 4: an unreadable target made the Edit path deny a literal the file already held; it fails OPEN
            # now, except that a future literal the new content itself introduces still counts
            F = "2099-01-01T00:00Z"
            fp = self.store_file(f"Last-heartbeat: {F}\nStatus: ok\n")
            os.chmod(fp, 0)
            try:
                if os.access(fp, os.R_OK):
                    self.skipTest("privileges bypass mode 0")
                self.assertEqual(self.ev("Edit", file_path=fp, old_string=f"Last-heartbeat: {F}",
                                         new_string=f"Last-heartbeat: {F} (confirmed)"), [])
                self.assertEqual(self.ev("Edit", file_path=fp, old_string="Status: ok", new_string="Status: done"), [])
                self.assertEqual(self.ev("Edit", file_path=fp, old_string="Status: ok",
                                         new_string="Status: ok 2098-01-01T00:00Z"), ["2098-01-01T00:00Z"])
                self.assertEqual(self.ev("MultiEdit", file_path=fp, edits=[
                    {"old_string": f"hb {F}", "new_string": f"hb {F}!"},
                    {"old_string": "a", "new_string": "hb 2098-01-01T00:00Z"}]), ["2098-01-01T00:00Z"])
                self.assertEqual(self.ev("Write", file_path=fp, content="Status: ok\n"), [])
                self.assertEqual(self.ev("Write", file_path=fp, content="hb 2098-01-01T00:00Z\n"),
                                 ["2098-01-01T00:00Z"])
            finally:
                os.chmod(fp, 0o600)
            # an OVER-CAP file (read, but not whole) keeps the fragment rule unchanged
            big = self.store_file(f"hb {F}\n" + "x\n" * (EXISTING_MAX_BYTES // 2 + 1))
            self.assertEqual(self.ev("Edit", file_path=big, old_string=f"hb {F}", new_string=f"hb {F} (confirmed)"),
                             [F])

        # -- generic port: the kill-switch spellings, bytes payload, a failed output rescue, fail-open before
        # evaluation --
        def test_aiqt_hooks_worker(self):
            self.assertTrue(_is_worker({"AIQT_HOOKS_WORKER": "1"}))
            for value in ("0", "", "true", "yes", " 1"):
                self.assertFalse(_is_worker({"AIQT_HOOKS_WORKER": value}), value)
            for env in ({"ORCH_WORKER": "1"}, {"ORCH_VERIFY_OWNER": ""}, {"ORCH_VERIFY_OWNER": "x"},
                        {"AIQT_HOOKS_WORKER": "0", "ORCH_VERIFY_OWNER": ""}):
                self.assertTrue(_is_worker(env), env)  # legacy spellings
            self.assertFalse(_is_worker({"ORCH_WORKER": "0"}))
            self.assertFalse(_is_worker({}))

        def run_stream(self, stream, argv=("future-stamp-write.py",)):
            old_in, old_out, old_env = sys.stdin, sys.stdout, dict(os.environ)
            sys.stdin, sys.stdout = stream, io.StringIO()
            try:
                for k in ("AIQT_HOOKS_WORKER", "ORCH_WORKER", "ORCH_VERIFY_OWNER"):
                    os.environ.pop(k, None)
                rc = main(list(argv) if isinstance(argv, tuple) else argv)
                return rc, sys.stdout.getvalue()
            finally:
                sys.stdin, sys.stdout = old_in, old_out
                os.environ.clear()
                os.environ.update(old_env)

        def test_payload_read_as_bytes(self):
            # a UTF-8 payload behind a text layer that cannot decode it: only a bytes read parses it
            content = "hb 2099-01-01T00:00Z " + chr(0xe9) + chr(0x2603)
            raw = json.dumps({"tool_name": "Write", "tool_input": {"file_path": self.store, "content": content}},
                             ensure_ascii=False).encode("utf-8")
            rc, out = self.run_stream(io.TextIOWrapper(io.BytesIO(raw), encoding="ascii"))
            self.assertEqual((rc, json.loads(out)["hookSpecificOutput"]["permissionDecision"]), (0, "deny"))
            bad = io.TextIOWrapper(io.BytesIO(b"\xff\xfe{"), encoding="ascii")  # undecodable: fails open
            self.assertEqual(self.run_stream(bad), (0, ""))

        def test_emit_rescue_failure_exits_0(self):
            code = ("import importlib.util as u, io;s=u.spec_from_file_location('m',%r);m=u.module_from_spec(s);"
                    "s.loader.exec_module(m);print('before', flush=True);b=io.StringIO();b.close();"
                    "m._emit_line('x', b);print('after', flush=True)" % os.path.abspath(__file__))
            r = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=30)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "before\n", ""))

        def test_closed_stderr_line_never_reaches_stdout(self):
            # the worker-skip line is for stderr only: with descriptor 2 closed (sys.stderr is None) it is dropped,
            # never redirected to stdout, the hook's protocol channel; the open-stderr control shows it is emitted
            env = {k: v for k, v in os.environ.items() if k not in ("ORCH_WORKER", "ORCH_VERIFY_OWNER")}
            env["AIQT_HOOKS_WORKER"] = "1"
            hook = [sys.executable, "-I", "-S", "-B", os.path.abspath(__file__)]
            close2 = "import os, sys; os.close(2); os.execv(sys.argv[1], sys.argv[1:])"
            for closed in (False, True):
                argv = [sys.executable, "-I", "-S", "-B", "-c", close2] + hook if closed else hook
                p = subprocess.run(argv, input="{}", capture_output=True, text=True, env=env, timeout=30)
                self.assertEqual((p.returncode, p.stdout), (0, ""), (closed, p.stderr))
                self.assertEqual("skipped, worker marker present" in p.stderr, not closed, (closed, p.stderr))

        def test_fail_open_before_evaluation(self):
            class Unreadable:
                def read(self, *a):
                    raise OSError("unreadable stdin")
            for stdin in (Unreadable(), io.StringIO("[1, 2]"), io.StringIO('"text"'), io.StringIO("null"),
                          io.StringIO("")):
                self.assertEqual(self.run_stream(stdin), (0, ""))
            p = json.dumps({"tool_name": "Write", "tool_input": {"file_path": self.store,
                                                                 "content": "hb 2099-01-01T00:00Z"}})
            for argv in (None, 7):  # argv that cannot be inspected fails open: exit 0, silent, nothing evaluated
                self.assertEqual(self.run_stream(io.StringIO(p), argv=argv), (0, ""), argv)
            rc, out = self.run_stream(io.StringIO(p), argv=[None, 3])  # inspectable: evaluated as a hook
            self.assertEqual((rc, json.loads(out)["hookSpecificOutput"]["permissionDecision"]), (0, "deny"))

        def test_r24_worker_skip_warns_and_output_error_fails_open(self):
            old_in, old_out, old_err = sys.stdin, sys.stdout, sys.stderr
            had = os.environ.get("AIQT_HOOKS_WORKER")
            sys.stdin, sys.stdout, sys.stderr = io.StringIO("{}"), io.StringIO(), io.StringIO()
            try:
                os.environ["AIQT_HOOKS_WORKER"] = "1"
                rc = main(["future-stamp-write.py"])
                out, err = sys.stdout.getvalue(), sys.stderr.getvalue()
            finally:
                sys.stdin, sys.stdout, sys.stderr = old_in, old_out, old_err
                if had is None:
                    os.environ.pop("AIQT_HOOKS_WORKER", None)
                else:
                    os.environ["AIQT_HOOKS_WORKER"] = had
            self.assertEqual((rc, out), (0, ""))
            self.assertEqual(err.count("\n"), 1)
            self.assertIn("skipped, worker marker present", err)
            if not os.path.exists("/dev/full"):
                self.skipTest("/dev/full absent")
            env = {k: v for k, v in os.environ.items() if k not in ("AIQT_HOOKS_WORKER", "ORCH_WORKER",
                                                                    "ORCH_VERIFY_OWNER", "ORCH_STORE_ROOT")}
            env["AIQT_STORE_ROOT"] = STORE
            payload = {"tool_name": "Bash", "tool_input": {"command": f"echo 2099-01-01T00:00Z > {PROJ}/private/s.md"}}
            with open("/dev/full", "w") as full:
                p = subprocess.run([sys.executable, "-I", "-B", os.path.abspath(__file__)], stdout=full,
                                   stderr=subprocess.PIPE, text=True, env=env, timeout=30, input=json.dumps(payload))
            self.assertEqual(p.returncode, 0, p.stderr)

        def test_r24b_keywords_match_whole_words(self):
            # follow-up (B): a keyword was a PREFIX, so a word that merely STARTS with one exempted a future literal
            S = f"{PROJ}/private/state.md"
            for bad in ("hb validated 2099-01-01T00:00Z", "hb throughput 2099-01-01T00:00Z",
                        "hb duet 2099-01-01T00:00Z", "hb etag 2099-01-01T00:00Z", "hb nextcloud 2099-01-01T00:00Z"):
                self.assertEqual(self.ev("Write", file_path=S, content=bad), ["2099-01-01T00:00Z"], bad)
            for ok in ("valid through 2099-01-01T00:00Z", "due 2099-01-01T00:00Z", "next_run: 2099-01-01T00:00Z",
                       "Deadlines: 2099-01-01T00:00Z", "expiration 2099-01-01T00:00Z", "ETA 2099-01-01T00:00Z"):
                self.assertEqual(self.ev("Write", file_path=S, content=ok), [], ok)
            self.assertEqual(self.ev("Write", file_path=S, content="| Item | Validated |\n|---|---|\n| a | "
                                     "2099-01-01T00:00Z |"), ["2099-01-01T00:00Z"])  # the header rule too

        def test_r24_disclosures(self):
            doc = " ".join(__doc__.split())
            for s in ("compared as local wall time", "TARGETS a store path", "fails OPEN", "through", "valid"):
                self.assertIn(s, doc)

        # -- round 25 (claude-fable-5 expensive QA of round 24) --
        def test_r25_item2_find_exec_false_positive_example_is_accurate(self):
            # finding 2 (LOW): the FALSE POSITIVES text named `find <store dir> ... -exec cp {} /elsewhere` as
            # denied, but it is allowed ({} is cp's source); the example now has {} as the TARGET, and each
            # disclosed form is checked against the hook, not only against the text
            doc = " ".join(__doc__.split())
            self.assertIn("`find /elsewhere ... -exec cp <store file> {} +`", doc)
            self.assertNotIn("`find <store dir> ... -exec cp {} /elsewhere`", doc)
            S, F = f"{PROJ}/private", "2099-01-01T00:00Z"
            for term in ("+", "\\;"):
                self.assertEqual(self.ev("Bash", command=f"find /elsewhere -type f -exec cp {S}/s.md {{}} {term} <<< {F}"),
                                 [F], term)
            self.assertEqual(self.ev("Bash", command=f"find {S} -type f -exec cp {{}} /elsewhere \\; <<< {F}"), [])
            # a {} target under a find over the store itself is a true store write, denied
            self.assertEqual(self.ev("Bash", command=f"find {S} -type f -exec tee {{}} \\; <<< {F}"), [F])

        # -- round 26 (codex gpt-6-astra high QA of round 25) --
        def test_r26_item2_option_arguments_are_not_targets(self):
            # finding 2 (MED): a command-specific option argument was read as a write target, so a read-only
            # store file named by sed -f or truncate -r denied a write elsewhere; fixed at class width for every
            # modeled command (sed, perl, truncate, ed, ex, cp, mv, install)
            S, F, X = f"{PROJ}/private", "2099-01-01T00:00Z", "/dev/shm/X"
            allowed = (f"sed -i -f {S}/rules.sed -e 's/new/{F}/' {X}", f"sed -i --file={S}/rules.sed {X} <<< {F}",
                       # round 27: a sed EXPRESSION or program naming a store path (formerly `--expression
                       # {S}/x` and `'s#{S}/a#b#'` here) is script text that can write it, now an unknown
                       # target and denied (test_r27_script_text_naming_store_is_unknown_target)
                       f"sed -i --file {S}/rules.sed {X} <<< {F}", f"sed -i -e 's/a/b/' --expression 's/c/d/' {X} <<< {F}",
                       f"sed -ni -f{S}/r.sed {X} <<< {F}", f"sed -i -l 80 -f {S}/r.sed {X} <<< {F}",
                       f"truncate -r {S}/reference {X} <<< {F}",
                       f"truncate --reference {S}/r {X} <<< {F}", f"truncate -cr {S}/r {X} <<< {F}",
                       f"perl -pi {S}/prog.pl {X} <<< {F}", f"perl -pi -I {S}/lib -e 1 {X} <<< {F}",
                       f"perl -pi -M{S}/x -e 1 {X} <<< {F}", f"perl -0777 -pi -e 's/a/{F}/' {X} # {S}/s.md",
                       f"perl -pi.bak -e 1 {X} <<< '{F} {S}/s.md'", f"ed -p {S}/p {X} <<< {F}", f"ed --prompt={S}/p {X} <<< {F}",
                       f"ex -c wq -u {S}/vimrc {X} <<< {F}", f"cp --no-preserve {S}/a /dev/shm/src /dev/shm/dst <<< {F}",
                       f"cp --sparse always {S}/s.md /dev/shm/dst <<< {F}")
            for cmd in allowed:
                self.assertEqual(self.ev("Bash", command=cmd), [], cmd)
            denied = (f"sed -i -f /dev/shm/r.sed {S}/s.md <<< {F}", f"sed -i -e 's/a/{F}/' {S}/s.md",
                      f"sed -i 's/a/{F}/' {S}/s.md", f"sed -ie 's/a/{F}/' {S}/s.md",  # -ie: -i with suffix e
                      f"truncate -r /dev/shm/ref {S}/s.md <<< {F}", f"truncate -s 0 {S}/s.md <<< {F}",
                      f"perl -pi -I /dev/shm/lib -e 1 {S}/s.md <<< {F}", f"perl -pi /dev/shm/prog.pl {S}/s.md <<< {F}",
                      f"perl -0777pi -e 's/a/{F}/' {S}/s.md", f"perl -l0pi -e 1 {S}/s.md <<< {F}",
                      f"ed -p x {S}/s.md <<< {F}", f"ex -w {S}/scriptout /dev/shm/x <<< {F}", f"ex -i {S}/viminfo x <<< {F}",
                      f"cp -vt {S} /dev/shm/x <<< {F}", f"cp --sparse always /dev/shm/x {S}/s.md <<< {F}",
                      f"install --strip-program strip /dev/shm/x {S}/s.md <<< {F}", f"mv --suffix .x /dev/shm/x {S}/s.md <<< {F}")
            for cmd in denied:
                self.assertEqual(self.ev("Bash", command=cmd), [F], cmd)
            self.assertEqual(_write_targets("sed", ["-i", "-f", "a", "-e", "b", "c"]), ["c"])
            self.assertEqual(_write_targets("sed", ["-i", "s/x/y/", "c", "d"]), ["c", "d"])
            self.assertEqual(_write_targets("truncate", ["-r", "a", "-s", "0", "b"]), ["b"])
            self.assertEqual(_write_targets("perl", ["-0777", "-pi.bak", "-e", "code", "f"]), ["f"])
            self.assertEqual(_write_targets("ex", ["-c", "wq", "-wout", "f"]), ["f", "out"])
            self.assertEqual(_write_targets("cp", ["-vt", "d", "a"]), ["d"])
            # the read-only-argument semantics the fix rests on, observed with the real tools
            for tool in ("bash", "sed", "truncate", "perl"):
                if not shutil.which(tool):
                    self.skipTest(f"{tool} absent")
            with open(os.path.join(self.tmp, "rules.sed"), "w") as f:
                f.write("s/a/b/\n")
            for name, text in (("X", "a\n"), ("ref", "abc"), ("prog.pl", "1;\n"), ("P", "x\n")):
                with open(os.path.join(self.tmp, name), "w") as f:
                    f.write(text)
            before = {n: open(os.path.join(self.tmp, n)).read() for n in ("rules.sed", "ref", "prog.pl")}
            subprocess.run(["bash", "-c", "sed -i -f rules.sed -e 's/b/c/' X && truncate -r ref T && "
                            "perl -pi -I . -e 's/x/y/' P && perl -i prog.pl P"], cwd=self.tmp, timeout=10, check=True)
            self.assertEqual({n: open(os.path.join(self.tmp, n)).read() for n in before}, before)
            self.assertEqual((open(os.path.join(self.tmp, "X")).read(), os.path.getsize(os.path.join(self.tmp, "T")),
                              open(os.path.join(self.tmp, "P")).read()), ("c\n", 3, "y\n"))

        def test_r26_item4_parsed_store_target_needs_no_raw_token(self):
            # finding 4 (MED): the raw-command prefilter names_store_path(cmd) missed an attached -tDIR, so a parsed
            # store destination was allowed and the future literal really reached the store
            F = "2099-01-01T00:00Z"
            for root, dest in ((None, f"{PROJ}/private"), (self.tmp, self.tmp)):
                if root:
                    os.environ["AIQT_STORE_ROOT"] = root
                for cmd in (f"printf '%s' {F} > /dev/shm/X; cp -t{dest} /dev/shm/X", f"cp -t{dest} /dev/shm/{F}.md",
                            f"mv -t{dest} /dev/shm/{F}.md", f"install -t{dest} /dev/shm/{F}.md",
                            f"cp -vt{dest} /dev/shm/{F}.md"):
                    self.assertFalse(names_store_path(cmd), cmd)  # the raw text shows no standalone store token
                    self.assertEqual(self.ev("Bash", command=cmd), [F], cmd)
            os.environ["AIQT_STORE_ROOT"] = STORE  # back to the fixture store root
            # the raw test remains the fallback for an UNKNOWN target only
            self.assertEqual(self.ev("Bash", command=f"echo {F} > \"$X\""), [])  # no store named: allowed
            self.assertEqual(self.ev("Bash", command=f"X={PROJ}/private/s.md; echo {F} > \"$X\""), [F])
            self.assertEqual(self.ev("Bash", command=f"echo 'unterminated {F} {PROJ}/private/s"), [F])
            self.assertEqual(self.ev("Bash", command=f"echo 'unterminated {F} /dev/shm/s"), [])
            # and a known non-store target stays allowed beside a named store path
            self.assertEqual(self.ev("Bash", command=f"cp -t/dev/shm {PROJ}/private/s.md <<< {F}"), [])

        def test_r26_item5_unreadable_edit_literal_index_is_linear(self):
            # finding 5 (MED): each new literal ran `lit in old` over the whole old fragment (quadratic); the old
            # fragment's literal-shaped substrings are indexed once, with exactly the substring semantics
            corpus = ("x2099-01-01T00:00Z", "12099-01-01T00:00Z", "2099-01-01 00:00 UTC", "2099-01-01T00:00:00Z",
                      "2099-01-01T00:00:00.123456789 ABCDEF", "2099-01-01T00:00 +05:30", "2099-01-01T00:00Z",
                      "\N{ARABIC-INDIC DIGIT TWO}099-01-01T00:00Z", "2099-01-01T00:0", "")
            news = [m.group(0) for c in corpus for m in _STAMP_RE.finditer(c)] + [
                "2099-01-01T00:00", "2099-01-01 00:00", "2099-01-01T00:00:00.123456789 ABCDEF", "2099-01-01T00:00Z"]
            for old in corpus + ("".join(corpus), " ".join(corpus)):
                held = _literal_substrings(old)
                for lit in news:
                    self.assertEqual(lit in held, lit in old, (old, lit))
            self.assertEqual(max(len(lit) for lit in news), LITERAL_MAX_LEN)
            code = TIMED_PRELUDE + (
                "fifo = %r\n"
                "os.mkfifo(fifo)\n"
                "os.environ['AIQT_STORE_ROOT'] = os.path.dirname(fifo)\n"
                "def lits(n, off):\n"
                "    return [f'2099-{1 + i %% 12:02d}-{1 + (i // 12) %% 28:02d}T{(i // 336) %% 24:02d}:"
                "{(i // 8064) %% 60:02d}Z' for i in range(off, off + n)]\n"
                "def run(n):\n"
                "    old, new = '\\n'.join(lits(n, 100000)), '\\n'.join(lits(n, 0))\n"
                "    got = m.evaluate({'tool_name': 'Edit', 'tool_input': {'file_path': fifo, 'old_string': old,"
                " 'new_string': new}}, now)\n"
                "    assert len(got) == n, len(got)\n"
                "print(json.dumps(ratio(4000, run)))\n") % os.path.join(self.tmp, "fifo")
            small, large = run_timed(code, HANG_TIMEOUT)
            # N to GROWTH * N (up to the former 32,000): about 1 when linear, about GROWTH when quadratic
            self.assertLess(large / max(small, 1e-3), LINEAR_LIMIT, (small, large))

        def test_r26_item6_hang_guard_interrupts(self):
            # finding 6 (LOW): the growth test's ceiling was asserted only after every run returned, so it could
            # not interrupt a hang; timed runs now go through run_timed, which kills the child at its timeout
            t0 = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                run_timed("import time\ntime.sleep(60)\n", 1)
            self.assertLess(time.monotonic() - t0, 30.0)
            for test in (T.test_r4_scan_is_linear, T.test_r26_item5_unreadable_edit_literal_index_is_linear):
                src = inspect.getsource(test)
                self.assertIn("run_timed(code, HANG_TIMEOUT)", src)
                self.assertNotIn("self.assertLess(large, 30.0)", src)

        def test_r26_item7_trailing_flag_residual_removed(self):
            # finding 7 (LOW): the residual said `install src <store file> -v` is skipped, but it is denied; only a
            # demonstrated unsupported-option case is disclosed now (round 29: that case, `--suf`, is closed by the
            # long-option resolution, so it is denied too)
            doc = " ".join(__doc__.split())
            self.assertNotIn("`install src <store file> -v`", doc)
            self.assertIn("`cp src <store file> --suf .bak`", doc)
            S, F = f"{PROJ}/private/s.md", "2099-01-01T00:00Z"
            self.assertEqual(self.ev("Bash", command=f"install /dev/shm/src {S} -v <<< {F}"), [F])
            self.assertEqual(self.ev("Bash", command=f"cp /dev/shm/src {S} --suf .bak <<< {F}"), [F])  # round 29: closed
            if not shutil.which("cp"):
                self.skipTest("cp absent")
            os.mkdir(os.path.join(self.tmp, "store"))
            with open(os.path.join(self.tmp, "src"), "w") as f:
                f.write("s\n")
            subprocess.run(["cp", "src", "store/s.md", "--suf", ".bak"], cwd=self.tmp, timeout=10, check=True)
            self.assertTrue(os.path.exists(os.path.join(self.tmp, "store", "s.md")))  # the write is real

        # -- round 27 (codex gpt-6-astra high and claude QA of round 26) --
        def real_env(self):
            # the real tools run with POSIXLY_CORRECT removed, as the model assumes (test hermeticity)
            return {k: v for k, v in os.environ.items() if k != "POSIXLY_CORRECT"}

        def test_r27_perl_stops_parsing_options_at_first_operand(self):
            # finding (codex MED, a round-26 regression): the option parser consumed `-I S` after perl's first
            # operand, but perl parses no option there, so S was a file perl edited in place and the store write
            # was allowed (round 25 denied it)
            S, F, X = f"{PROJ}/private", "2099-01-01T00:00Z", "/dev/shm/X"
            for cmd in (f"perl -pi -e 's/old/{F}/' {X} -I {S}/s.md", f"perl -pi -e 1 {X} -e {S}/s.md <<< {F}",
                        f"perl -pi /dev/shm/prog.pl {X} -I {S}/s.md <<< {F}", f"perl -pi -e 1 {X} -- {S}/s.md <<< {F}",
                        f"perl -pi -e 1 {X} -M {S}/s.md <<< {F}", f"perl -0777 -pi -e 1 {X} -x {S}/s.md <<< {F}"):
                self.assertEqual(self.ev("Bash", command=cmd), [F], cmd)
            # before the first operand an option still consumes its argument (round 26 behaviour kept)
            for cmd in (f"perl -pi -I {S}/lib -e 1 {X} <<< {F}", f"perl -pi -e 1 {X} -I /dev/shm/lib <<< '{F} {S}/s.md'",
                        f"perl -pi -M{S}/x -e 1 {X} <<< {F}"):
                self.assertEqual(self.ev("Bash", command=cmd), [], cmd)
            self.assertEqual(_write_targets("perl", ["-pi", "-e", "c", "X", "-I", "S"]), ["X", "-I", "S"])
            self.assertEqual(_write_targets("perl", ["-pi", "prog.pl", "X", "-e", "S"]), ["X", "-e", "S"])
            # the other modeled commands permute, like getopt: an option after an operand is still an option
            self.assertEqual(_write_targets("sed", ["-i", "s/a/b/", "X", "-f", "S"]), ["s/a/b/", "X"])
            self.assertEqual(_write_targets("truncate", ["X", "-r", "S"]), ["X"])
            self.assertEqual(_write_targets("ex", ["X", "-u", "S"]), ["X"])
            for spec in ("sed", "ex", "ed", "cp", "mv", "install", "truncate", "tee"):
                self.assertFalse(_OPTION_SPECS[spec].get("stop"), spec)
            self.assertTrue(_OPTION_SPECS["perl"]["stop"])
            # the real tools, observed: perl stops at its first operand, GNU sed and truncate permute
            for tool in ("perl", "sed", "truncate"):
                if not shutil.which(tool):
                    self.skipTest(f"{tool} absent")
            for name in ("X", "S"):
                with open(os.path.join(self.tmp, name), "w") as f:
                    f.write("old\n")
            r = subprocess.run(["perl", "-pi", "-e", f"s/old/{F}/", "X", "-I", "S"], cwd=self.tmp, timeout=10,
                               capture_output=True, text=True, env=self.real_env())
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("-I", r.stderr)  # perl tried to open a FILE named -I
            self.assertEqual([open(os.path.join(self.tmp, n)).read() for n in ("X", "S")], [F + "\n"] * 2)
            r = subprocess.run(["sed", "s/old/new/", "S", "-n"], cwd=self.tmp, timeout=10, capture_output=True,
                               text=True, env=self.real_env())
            self.assertEqual((r.returncode, r.stdout), (0, ""))  # -n after the operand is still an option
            with open(os.path.join(self.tmp, "T"), "w") as f:
                f.write("abc")
            subprocess.run(["truncate", "T", "-s", "0"], cwd=self.tmp, timeout=10, check=True, env=self.real_env())
            self.assertEqual(os.path.getsize(os.path.join(self.tmp, "T")), 0)

        def test_r27_script_text_naming_store_is_unknown_target(self):
            # finding (claude LOW, a round-26 regression): a sed program or an ex -c argument that itself writes a
            # store file was allowed once round 26 consumed option arguments and dropped the sed program; script
            # text naming a store path now makes the target UNKNOWN, while a FILE-naming argument stays consumed
            S, F, X = f"{PROJ}/private", "2099-01-01T00:00Z", "/dev/shm/X"
            denied = (f"sed -i 's/ts: .*/ts: {F}/w {S}/cap.md' /dev/shm/in",
                      f"sed -i -e 'w {S}/cap.md' -e 's/x/{F}/' /dev/shm/in",
                      f"ex -c \"w! {S}/s.md\" -c q /dev/shm/in <<< {F}",
                      f"sed -i 's#{S}/a#b#' {X} <<< {F}", f"sed -i -e 's/a/b/' --expression {S}/x {X} <<< {F}",
                      f"sed -i --expression='w {S}/c' /dev/shm/in <<< {F}", f"sed -i -e'w {S}/c' {X} <<< {F}",
                      f"sed -ne 'w {S}/cap.md' /dev/shm/in <<< {F}", f"sed 's/x/{F}/w {S}/cap.md' /dev/shm/in",
                      f"ex --cmd 'w! {S}/s.md' /dev/shm/in <<< {F}", f"ex -s '+w! {S}/s.md' +q /dev/shm/in <<< {F}",
                      f"perl -pi -e 'open my $f, \">\", \"{S}/s.md\"' {X} <<< {F}",
                      f"perl -e 'open F, \">{S}/s.md\"; print F \"{F}\"'", f"perl -E 'say 1' -e '{S}/x' <<< {F}")
            for cmd in denied:
                self.assertEqual(self.ev("Bash", command=cmd), [F], cmd)
            allowed = (f"sed -i -f {S}/rules.sed {X} <<< {F}", f"truncate -r {S}/ref {X} <<< {F}",
                       f"perl -pi -I {S}/lib -e 1 {X} <<< {F}", f"perl -pi {S}/prog.pl {X} <<< {F}",
                       f"ex -u {S}/vimrc -c wq {X} <<< {F}", f"sed -n '/x/p' {S}/s.md <<< {F}",
                       f"sed 's/a/{F}/' {S}/s.md", f"ex -c 'w! /dev/shm/out' -c q {X} <<< '{F} {S}/s.md'",
                       f"sed -i 's/a/b/w {S}/cap.md' {X}")  # no future literal at all
            for cmd in allowed:
                self.assertEqual(self.ev("Bash", command=cmd), [], cmd)
            self.assertIsNone(_write_targets("sed", ["-i", f"s/x/y/w {S}/c", "f"]))
            self.assertIsNone(_write_targets("sed", ["-n", f"w {S}/c"], False))
            self.assertEqual(_write_targets("sed", ["-n", "p"], False), [])
            self.assertEqual(_write_targets("sed", ["-i", "--expression", "s/c/d/", "X"]), ["X"])
            self.assertIsNone(_write_targets("ex", ["-c", f"w! {S}/s.md", "f"]))
            self.assertEqual(_write_targets("perl", ["-pi", f"{S}/prog.pl", "f"]), ["f"])  # a program FILE
            self.assertTrue(bash_writes(f"sed 's/x/y/w {S}/c' in"))
            self.assertFalse(bash_writes(f"sed -n p {S}/s.md"))
            doc = " ".join(__doc__.split())
            for s in ("SCRIPT TEXT", "POSIXLY_CORRECT", "perl 5.40.1", "MENTIONS a store path",
                      "as a FILE rather than carrying as text"):
                self.assertIn(s, doc)
            # the real tools, observed: sed's `w` flag and `w` command, and an ex -c `w!`, write the named file
            if not shutil.which("sed"):
                self.skipTest("sed absent")
            os.mkdir(os.path.join(self.tmp, "store"))
            cap = os.path.join(self.tmp, "store", "cap.md")
            with open(os.path.join(self.tmp, "in"), "w") as f:
                f.write("ts: old\n")
            subprocess.run(["sed", "-i", f"s/ts: .*/ts: {F}/w {cap}", "in"], cwd=self.tmp, timeout=10, check=True,
                           env=self.real_env())
            self.assertEqual(open(cap).read(), f"ts: {F}\n")
            os.unlink(cap)
            subprocess.run(["sed", "-n", "-e", f"w {cap}", "in"], cwd=self.tmp, timeout=10, check=True,
                           env=self.real_env())
            self.assertEqual(open(cap).read(), f"ts: {F}\n")
            if not shutil.which("ex"):
                self.skipTest("ex absent")
            exw = os.path.join(self.tmp, "store", "ex.md")
            subprocess.run(["ex", "-s", "-c", f"w! {exw}", "-c", "q", "in"], cwd=self.tmp, timeout=10,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           env=self.real_env())
            self.assertEqual(open(exw).read(), f"ts: {F}\n")

        # -- round 28 (codex gpt-6-astra high QA of round 27) --
        def test_r28_read_only_sed_mentioning_store_is_no_write(self):
            # finding (codex MED, a round-27 regression): `sed -n '\|S F|p' audit.log` only prints matching lines,
            # but its program MENTIONS a store path, so round 27 made its target unknown and denied it (round 26
            # allowed it); a sed with no in-place option whose program parses as unable to write is now no write
            S, F = f"{PROJ}/private", "2099-01-01T00:00Z"
            allowed = (f"sed -n '\\|{S}/s.md {F}|p' audit.log", f"sed -n 's|{S}/s.md|X|p' audit.log <<< {F}",
                       f"sed -n -e '/x/p' -e '\\|{S}/s.md|=' audit.log <<< {F}", f"sed '/{F}/d;\\|{S}|d' audit.log",
                       f"sed -n '\\,{S}/s.md,{{p;q}}' audit.log <<< {F}", f"sed -n '/[/]x/p;\\|{S}|p' audit.log <<< {F}",
                       f"sed -n '1a {S}/w' audit.log <<< {F}", f"sed -n '1r {S}/s.md' audit.log <<< {F}",
                       f"grep -c x audit.log && sed -n '\\|{S}/s.md {F}|p' audit.log")
            for cmd in allowed:
                self.assertEqual(self.ev("Bash", command=cmd), [], cmd)
            denied = (f"sed -n '\\|{F}|w {S}/s.md' audit.log", f"sed 's/x/{F}/ w {S}/s.md' audit.log",
                      f"sed -n 's/x/{F}/gw {S}/s.md' audit.log", f"sed -n '1{{w {S}/s.md}}' audit.log <<< {F}",
                      f"sed -n '1e cat {S}/s.md' audit.log <<< {F}", f"sed 's/x/y/e;\\|{S}|p' audit.log <<< {F}",
                      f"sed -n 'W {S}/s.md' audit.log <<< {F}",
                      # an in-place sed keeps the round-27 treatment even when its program cannot write
                      f"sed -i 's#{S}/a#b#' /dev/shm/x <<< {F}", f"sed -ni '\\|{S}|p' /dev/shm/x <<< {F}",
                      # a program read from a FILE, or under --posix, is not parsed: kept unknown
                      f"sed -n -f /dev/shm/r.sed -e '\\|{S}|p' audit.log <<< {F}",
                      f"sed --posix -n '\\|{S}|p' audit.log <<< {F}",
                      # a program not parsed with confidence: a `$`, a backslash in a bracket, a trailing command
                      f"sed -n '\\|{S}/s.md$|p' audit.log <<< {F}", f"sed -n \"\\|{S}/$D|p\" audit.log <<< {F}",
                      f"sed -n 's/[\\\\/]/X/;\\|{S}|p' audit.log <<< {F}", f"sed -n '1{{p}}p;\\|{S}|p' audit.log <<< {F}",
                      f"sed -n -e '1a foo\\\\' -e '\\|{S}|p' audit.log <<< {F}",
                      # ex is NOT narrowed: a read-only ex -c command naming a store path is still denied
                      f"ex -c 'g#{S}/s.md#p' -c 'q!' /dev/shm/x <<< {F}",
                      f"ex --cmd 'echo \"{S}/s.md\"' /dev/shm/x <<< {F}")
            for cmd in denied:
                self.assertEqual(self.ev("Bash", command=cmd), [F], cmd)
            cannot = ("p", f"\\|{S}/s.md {F}|p", "s/[/]/X/p", "1,+2p", "0~2p", "/x/I,/y/Mp", "1 ! p",
                      ":a;s/x/y/;ta;p", "1{p};2p", "y/ab/AB/", "1a w out", "1a\\\nw out", "p # w out", "l 5", "q5",
                      "s/a/b/ 2 g p", "s/[[:alpha:]/]/Z/gp", "", "s/a/1\\\n2/p", "/a/{\np\n}")
            can_or_unsure = ("w x", "W x", "e ls", "s/a/b/w x", "s/a/b/ w x", "s/a/b/e", "1{w x}", "/a/!w x", "$p",
                             "s/[\\/]/X/", "1{p}p", "1!!p", "1a foo\\", "1a\\text", "{p", "p}", "L", "s/a/b/X",
                             "s/a/b/#c\nw x", "b a}", "p x")
            for prog in cannot:
                self.assertTrue(_sed_program_cannot_write(prog), prog)
            for prog in can_or_unsure:
                self.assertFalse(_sed_program_cannot_write(prog), prog)
            self.assertEqual(_write_targets("sed", ["-n", f"\\|{S}/s.md|p", "log"], False), [])
            self.assertIsNone(_write_targets("sed", ["-n", f"\\|{S}/s.md|w x", "log"], False))
            self.assertIsNone(_write_targets("sed", ["-n", "-f", "r.sed", "-e", f"\\|{S}|p", "log"], False))
            self.assertIsNone(_write_targets("sed", ["-i", f"s#{S}/a#x#", "log"]))
            self.assertIsNone(_write_targets("ex", ["-c", f"g#{S}/s.md#p", "x"]))
            doc = " ".join(__doc__.split())
            for s in ("Round 28: a sed with NO in-place option", "ex is NOT narrowed", "MENTIONS a store path"):
                self.assertIn(s, doc)
            # the real tool, observed: the read-only search writes nothing; a `w` file is opened while the program is
            # PARSED (so even a program that later fails writes it), and an s flag after a space is still a flag
            if not shutil.which("sed"):
                self.skipTest("sed absent")
            store = os.path.join(self.tmp, "store")
            os.mkdir(store)
            with open(os.path.join(self.tmp, "audit.log"), "w") as f:
                f.write(f"read {store}/s.md {F}\nother\n")
            r = subprocess.run(["sed", "-n", f"\\|{store}/s.md {F}|p", "audit.log"], cwd=self.tmp, timeout=10,
                               capture_output=True, text=True, env=self.real_env())
            self.assertEqual((r.returncode, r.stdout, os.listdir(store)), (0, f"read {store}/s.md {F}\n", []))
            r = subprocess.run(["sed", "-n", f"1{{w {store}/early}}", "audit.log"], cwd=self.tmp, timeout=10,
                               capture_output=True, text=True, env=self.real_env())
            self.assertNotEqual(r.returncode, 0)  # the script fails (unmatched `{`) ...
            self.assertEqual(os.listdir(store), ["early}"])  # ... after its `w` file was created
            subprocess.run(["sed", "-n", f"s/other/{F}/ w {store}/flag", "audit.log"], cwd=self.tmp, timeout=10,
                           check=True, env=self.real_env())
            self.assertEqual(open(os.path.join(store, "flag")).read(), f"{F}\n")

        # -- round 29 (claude QA of round 28) --
        def test_r29_long_option_abbreviations_resolved(self):
            # finding (claude LOW): GNU long-option ABBREVIATIONS were not modelled, so `sed --expr='s/x/F/w S' log`
            # and `sed --in 's/x/F/' S` were allowed while real GNU sed 4.9 wrote the store; a long option is now
            # resolved as the real tool resolves it, and on sed, perl, or ex an ambiguous or unrecognized one makes
            # the target UNKNOWN
            S, F = f"{PROJ}/private", "2099-01-01T00:00Z"
            denied = (f"sed --expr='s/x/{F}/w {S}/s.md' audit.log", f"sed --in 's/x/{F}/' {S}/state.md",
                      f"sed --in=.bak 's/x/{F}/' {S}/state.md", f"sed --expr 's/x/{F}/w {S}/s.md' audit.log",
                      f"sed -n --e='w {S}/c' audit.log <<< {F}", f"sed --i -n 'p' {S}/s.md <<< {F}",
                      f"sed --po -n '\\|{S}|p' audit.log <<< {F}",  # --posix: the program is not parsed (round 28)
                      f"sed --f -n '\\|{S}|p' audit.log <<< {F}",  # ambiguous: --file or --follow-symlinks
                      f"sed --bogus -n '\\|{S}|p' audit.log <<< {F}",  # unrecognized
                      f"perl --in -pe 1 {S}/s.md <<< {F}",  # perl has no long options
                      f"ex --cm 'w! {S}/s.md' /dev/shm/in <<< {F}", f"ex --CMD 'w! {S}/s.md' /dev/shm/in <<< {F}",
                      f"cp --targ {S} /dev/shm/src <<< {F}", f"cp /dev/shm/src {S}/s.md --suf .bak <<< {F}",
                      f"mv --t={S} /dev/shm/src <<< {F}", f"install --target {S} -m 600 /dev/shm/src <<< {F}",
                      f"tee --a {S}/s.md < /dev/shm/x <<< {F}", f"truncate --si 0 {S}/s.md <<< {F}")
            for cmd in denied:
                self.assertEqual(self.ev("Bash", command=cmd), [F], cmd)
            allowed = (f"sed -n --expr='\\|{S}/s.md {F}|p' audit.log", f"sed --z -n '\\|{S}|p' audit.log <<< {F}",
                       f"sed --q '\\|{S}|p' audit.log <<< {F}", f"sed --debug -n '\\|{S}|p' audit.log <<< {F}",
                       # an abbreviated option now consumes its argument: a store path there is no target
                       f"cp --targ /dev/shm/out {S}/s.md <<< {F}", f"truncate --ref {S}/s.md /dev/shm/x <<< {F}",
                       f"install --m 600 {S}/s.md /dev/shm/x <<< {F}", f"ed --prom {S}/p /dev/shm/x <<< {F}")
            for cmd in allowed:
                self.assertEqual(self.ev("Bash", command=cmd), [], cmd)
            for kind, name, want in (("sed", "--expr", "--expression"), ("sed", "--in", "--in-place"),
                                     ("sed", "--i", "--in-place"), ("sed", "--po", "--posix"),
                                     ("sed", "--f", None), ("sed", "--s", None), ("sed", "--z", "--null-data"),
                                     ("sed", "--silent", "--quiet"), ("sed", "--posix", "--posix"),
                                     ("sed", "--bogus", None), ("ex", "--cmd", "--cmd"), ("ex", "--cm", None),
                                     ("ex", "--CMD", None), ("perl", "--in", None), ("cp", "--pa", "--parents"),
                                     ("cp", "--s", None), ("cp", "--targ", "--target-directory"),
                                     ("ed", "--s", None), ("ed", "--si", "--quiet"), ("install", "--strip", "--strip"),
                                     ("truncate", "--r", "--reference"), ("dd", "--x", "--x")):
                self.assertEqual(_resolve_long(kind, name), want, (kind, name))
            for kind, w, want in (("sed", "--in", True), ("sed", "--in=.bak", True), ("sed", "--in-place", True),
                                  ("sed", "-i", True), ("sed", "--i", True), ("sed", "--inx", False),
                                  ("sed", "--debug", False), ("perl", "--in-place", False), ("perl", "-pi", True)):
                self.assertEqual(_is_inplace_opt(kind, w), want, (kind, w))
            self.assertEqual(_write_targets("sed", ["--in", "s/a/b/", "X"]), ["X"])
            self.assertEqual(_write_targets("sed", ["--expr=p", "X"], False), [])
            self.assertIsNone(_write_targets("sed", ["--bogus", "p", "X"], False))
            self.assertIsNone(_write_targets("perl", ["--in", "-pe", "1", "X"], False))
            self.assertEqual(_write_targets("cp", ["--targ", "D", "a", "b"]), ["D"])
            self.assertEqual(_write_targets("cp", ["a", "D", "--suf", ".bak"]), ["D"])
            self.assertEqual(_write_targets("truncate", ["--ref", "R", "X"]), ["X"])
            doc = " ".join(__doc__.split())
            for s in ("Round 29: a long option is resolved as the real tool resolves it", "UNAMBIGUOUS prefix",
                      "AMBIGUOUS or UNRECOGNIZED makes the write's target UNKNOWN"):
                self.assertIn(s, doc)
            # the real tools, observed: GNU sed accepts the abbreviations and writes the store, and rejects an
            # ambiguous prefix; GNU cp resolves --targ; perl rejects any long option
            if not shutil.which("sed"):
                self.skipTest("sed absent")
            store = os.path.join(self.tmp, "store")
            os.mkdir(store)
            with open(os.path.join(self.tmp, "audit.log"), "w") as f:
                f.write("x\n")
            subprocess.run(["sed", f"--expr=s/x/{F}/w {store}/s.md", "audit.log"], cwd=self.tmp, timeout=10,
                           check=True, capture_output=True, env=self.real_env())
            self.assertEqual(open(os.path.join(store, "s.md")).read(), f"{F}\n")
            with open(os.path.join(store, "state.md"), "w") as f:
                f.write("x\n")
            subprocess.run(["sed", "--in", f"s/x/{F}/", f"{store}/state.md"], cwd=self.tmp, timeout=10, check=True,
                           env=self.real_env())
            self.assertEqual(open(os.path.join(store, "state.md")).read(), f"{F}\n")
            r = subprocess.run(["sed", "--f", "-n", "p", "audit.log"], cwd=self.tmp, timeout=10, capture_output=True,
                               text=True, env=self.real_env())
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("ambiguous", r.stderr)
            if shutil.which("cp"):
                subprocess.run(["cp", "--targ", store, "audit.log"], cwd=self.tmp, timeout=10, check=True,
                               env=self.real_env())
                self.assertTrue(os.path.exists(os.path.join(store, "audit.log")))
            if shutil.which("perl"):
                r = subprocess.run(["perl", "--in", "-e", "1"], cwd=self.tmp, timeout=10, capture_output=True,
                                   text=True, env=self.real_env())
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("Unrecognized switch", r.stderr)

        # -- round 30 (codex gpt-6-astra high and claude-fable-5 QA of round 29) --
        def test_r30_perl_help_and_version_exit_writing_nothing(self):
            # finding (codex MED, a round-29 regression): perl's option table was empty, so `perl --version` (a
            # read-only diagnostic) made the target UNKNOWN and a later read-only store search in the same command
            # was denied; perl accepts exactly --help and --version and exits at once on either
            S, F = f"{PROJ}/private", "2099-01-01T00:00Z"
            allowed = (f"perl --version; grep -F '{F}' {S}/s.md", f"perl --help; grep -F '{F}' {S}/s.md",
                       f"perl --version && rg '{F}' {S}", f"perl --version -pi -e 's/x/{F}/' {S}/s.md",
                       f"perl -pi -e 's/x/{F}/' --help {S}/s.md")
            for cmd in allowed:
                self.assertEqual(self.ev("Bash", command=cmd), [], cmd)
            denied = (f"perl -pi -e 's/x/{F}/' {S}/s.md --version",  # after the first operand: an operand
                      f"perl --vers -pi -e 1 {S}/s.md <<< {F}", f"perl --version=1 -pi -e 1 {S}/s.md <<< {F}",
                      f"perl --VERSION -pi -e 1 {S}/s.md <<< {F}", f"perl --in -pe 1 {S}/s.md <<< {F}")
            for cmd in denied:
                self.assertEqual(self.ev("Bash", command=cmd), [F], cmd)
            for name, want in (("--version", "--version"), ("--help", "--help"), ("--vers", None), ("--hel", None),
                               ("--in", None)):
                self.assertEqual(_resolve_long("perl", name), want, name)
            self.assertEqual(_write_targets("perl", ["--version", "-pi", "-e", "1", "X"]), [])
            self.assertEqual(_write_targets("perl", ["-pi", "-e", "1", "X", "--version"]), ["X", "--version"])
            self.assertIsNone(_write_targets("perl", ["--version=1", "-pi", "-e", "1", "X"]))
            doc = " ".join(__doc__.split())
            self.assertIn("Round 30: perl accepts exactly two long options", doc)
            with open(os.path.abspath(__file__)) as f:
                self.assertNotIn("perl has " + "none", f.read())  # the round-29 comment corrected
            # the real tool, observed: it exits 0 on either, before editing the file or running the program
            if not shutil.which("perl"):
                self.skipTest("perl absent")
            x = os.path.join(self.tmp, "x")
            for args, edited in ((["--version"], False), (["--help"], False),
                                 (["-pi", "-e", "s/a/b/", "--version", "x"], False),
                                 (["-e", "open F, q(>x); print F 1", "--help"], False),
                                 (["-pi", "-e", "s/a/b/", "x", "--version"], True)):
                with open(x, "w") as f:
                    f.write("a\n")
                r = subprocess.run(["perl"] + args, cwd=self.tmp, timeout=10, capture_output=True,
                                   stdin=subprocess.DEVNULL, env=self.real_env())
                self.assertEqual((r.returncode, open(x).read()), (0, "b\n" if edited else "a\n"), args)
            for bad in ("--vers", "--version=1"):
                r = subprocess.run(["perl", bad], cwd=self.tmp, timeout=10, capture_output=True, text=True,
                                   stdin=subprocess.DEVNULL, env=self.real_env())
                self.assertIn("Unrecognized switch", r.stderr, bad)

        def test_r30_every_operand_written_under_exchange_or_directory(self):
            # finding (codex MED, pre-existing): `mv -T --exchange S T` swaps S and T, so it WRITES S (the first
            # operand), but only the destination was a target; under mv's --exchange, and install's -d or
            # --directory, every operand (and every -t directory) is now a target
            S, F = f"{PROJ}/private", "2099-01-01T00:00Z"
            denied = (f"printf '%s\\n' 'heartbeat: {F}' > /dev/shm/t; mv -T --exchange {S}/s.md /dev/shm/t",
                      f"mv --exchange {S}/s.md /dev/shm/t <<< {F}", f"mv --exch -t /dev/shm/d {S}/s.md <<< {F}",
                      f"mv --e {S}/s.md /dev/shm/t <<< {F}", f"mv --exchange {S}/s.md /dev/shm/a /dev/shm/D <<< {F}",
                      f"install -d {S}/new /dev/shm/x <<< {F}", f"install --directory {S}/new /dev/shm/x <<< {F}",
                      f"install -vd {S}/new /dev/shm/x <<< {F}")
            for cmd in denied:
                self.assertEqual(self.ev("Bash", command=cmd), [F], cmd)
            allowed = (f"mv {S}/s.md /dev/shm/t <<< {F}", f"mv --exchange /dev/shm/a /dev/shm/b <<< {F}",
                       f"install -d /dev/shm/a /dev/shm/b <<< {F}", f"cp -d {S}/s.md /dev/shm/t <<< {F}")
            for cmd in allowed:
                self.assertEqual(self.ev("Bash", command=cmd), [], cmd)
            self.assertEqual(_write_targets("mv", ["-T", "--exchange", "S", "T"]), ["S", "T"])
            self.assertEqual(_write_targets("mv", ["--exch", "-t", "D", "S"]), ["S", "D"])
            self.assertEqual(_write_targets("mv", ["-T", "S", "T"]), ["T"])
            self.assertEqual(_write_targets("install", ["-d", "A", "B"]), ["A", "B"])
            self.assertEqual(_write_targets("install", ["--dir", "A", "B"]), ["A", "B"])
            self.assertEqual(_write_targets("cp", ["-d", "A", "B"]), ["B"])  # cp's -d is --no-dereference
            doc = " ".join(__doc__.split())
            self.assertIn("so under either EVERY operand and every -t or --target-directory argument is a write target",
                          doc)
            # the real tools, observed: mv --exchange rewrites its FIRST operand; install -d writes every operand
            store = os.path.join(self.tmp, "store")
            os.mkdir(store)
            with open(os.path.join(store, "s.md"), "w") as f:
                f.write("old\n")
            with open(os.path.join(self.tmp, "t"), "w") as f:
                f.write(f"heartbeat: {F}\n")
            r = subprocess.run(["mv", "-T", "--exchange", os.path.join(store, "s.md"), "t"], cwd=self.tmp,
                               timeout=10, capture_output=True, text=True, env=self.real_env())
            if r.returncode != 0 and "exchange" in r.stderr:
                self.skipTest("this mv has no --exchange")
            self.assertEqual((r.returncode, open(os.path.join(store, "s.md")).read()), (0, f"heartbeat: {F}\n"))
            if shutil.which("install"):
                subprocess.run(["install", "-d", os.path.join(store, "d1"), "d2"], cwd=self.tmp, timeout=10,
                               check=True, env=self.real_env())
                self.assertTrue(os.path.isdir(os.path.join(store, "d1")))

        def test_r30_install_context_arity_union(self):
            # finding (claude MED): uutils install 0.8.0 consumes a SEPARATE word after --context (GNU install takes
            # only an attached value), so `install src <store file> --context foo` wrote the store file while the
            # model read `foo` as the destination; the model now takes the union of both readings
            S, F = f"{PROJ}/private", "2099-01-01T00:00Z"
            denied = (f"install /dev/shm/src {S}/s.md --context foo <<< {F}",
                      f"install /dev/shm/src {S}/s.md --cont foo <<< {F}",
                      f"install /dev/shm/src {S}/s.md --context - <<< {F}",
                      f"install --context foo /dev/shm/src {S}/s.md <<< {F}",
                      f"install /dev/shm/src /dev/shm/dst --context {S}/s.md <<< {F}")  # GNU reads S as the dest
            for cmd in denied:
                self.assertEqual(self.ev("Bash", command=cmd), [F], cmd)
            allowed = (f"install /dev/shm/src /dev/shm/dst --context foo <<< {F}",
                       f"install --context -m 600 {S}/s.md /dev/shm/x <<< {F}",
                       f"install --context=foo {S}/s.md /dev/shm/x <<< {F}",
                       f"install {S}/s.md /dev/shm/x --context <<< {F}")
            for cmd in allowed:
                self.assertEqual(self.ev("Bash", command=cmd), [], cmd)
            for words, want in ((["src", "S", "--context", "foo"], ["S", "foo"]),
                                (["src", "S", "--cont", "foo"], ["S", "foo"]),
                                (["--context", "foo", "src", "D"], ["D"]), (["src", "D", "--context", "-"], ["D", "-"]),
                                (["src", "D", "--context", "--", "x"], ["x"]), (["src", "D", "--context"], ["D"]),
                                (["-t", "D", "--context", "a"], ["D"]), (["--context=foo", "a", "D"], ["D"])):
                self.assertEqual(_write_targets("install", words), want, words)
            self.assertEqual(_write_targets("cp", ["src", "D", "--context", "foo"]), ["foo"])  # GNU cp: attached only
            doc = " ".join(__doc__.split())
            for s in ("GNU install takes it only attached", "the model takes the conservative union",
                      "another implementation whose option arity differs"):
                self.assertIn(s, doc)
            # the real tool, observed: uutils install consumes the separate word (GNU install would not)
            if not shutil.which("install"):
                self.skipTest("install absent")
            v = subprocess.run(["install", "--version"], capture_output=True, text=True, timeout=10,
                               env=self.real_env())
            if "uutils" not in v.stdout:
                self.skipTest("install is not uutils")
            store = os.path.join(self.tmp, "store")
            os.mkdir(store)
            with open(os.path.join(self.tmp, "src"), "w") as f:
                f.write(f"heartbeat: {F}\n")
            subprocess.run(["install", "src", os.path.join(store, "s.md"), "--context", "foo"], cwd=self.tmp,
                           timeout=10, check=True, capture_output=True, env=self.real_env())
            self.assertEqual(open(os.path.join(store, "s.md")).read(), f"heartbeat: {F}\n")
            r = subprocess.run(["install", "src", "--context", os.path.join(store, "t.md")], cwd=self.tmp,
                               timeout=10, capture_output=True, text=True, env=self.real_env())
            self.assertNotEqual(r.returncode, 0)  # the store path was consumed as the context: no destination
            self.assertIn("missing destination", r.stderr)

        def test_r30_recorded_long_option_arity_matches_real_tools(self):
            # the round-30 sweep, kept as a check: for cp, mv, and install, every recorded long option is run as
            # `TOOL a D OPT WORD` (D an existing directory, WORD a name that does not exist); the tool CONSUMED
            # WORD as the option's argument exactly when it copied or moved a into D and succeeded, and read WORD
            # as an operand exactly when it failed on WORD as the destination. The model must agree: an option
            # the spec lists as taking an argument consumes it, any other does not, and an "optsep" option may
            # do either (the union is modeled)
            values = {"--suffix": "x", "--mode": "600", "--owner": str(os.getuid()), "--group": str(os.getgid()),
                      "--strip-program": "true", "--sparse": "auto", "--no-preserve": "mode"}
            skip = {"--help", "--version", "--target-directory", "--no-target-directory", "--directory",
                    "--exchange", "--strip"}  # exits, or changes the operand shape, so this probe cannot read it
            checked = 0
            for tool in ("cp", "mv", "install"):
                if not shutil.which(tool):
                    continue
                spec = _OPTION_SPECS[tool]
                for opt in _LONG_OPTIONS[tool]:
                    if opt in skip:
                        continue
                    d = tempfile.mkdtemp(dir=self.tmp)
                    with open(os.path.join(d, "a"), "w") as f:
                        f.write("A\n")
                    os.mkdir(os.path.join(d, "D"))
                    word = values.get(opt, "Wn")
                    r = subprocess.run([tool, "a", "D", opt, word], cwd=d, timeout=10, capture_output=True,
                                       text=True, stdin=subprocess.DEVNULL, env=self.real_env())
                    consumed = r.returncode == 0 and os.path.lexists(os.path.join(d, "D", "a"))
                    if not consumed:
                        self.assertNotEqual(r.returncode, 0, (tool, opt, r.stderr))
                        self.assertFalse(os.path.lexists(os.path.join(d, word)), (tool, opt))
                    canon = _resolve_long(tool, opt)
                    if canon in spec.get("optsep", ()):
                        checked += 1
                        continue
                    self.assertEqual(consumed, canon in spec.get("long", ()), (tool, opt, r.stderr))
                    checked += 1
            if not checked:
                self.skipTest("cp, mv, and install absent")
            self.assertGreater(checked, 40)

        # -- round 31 (codex gpt-6-astra high and claude-fable-5 QA of round 30) --
        def bash_run(self, cmd, cwd):
            """Run `cmd` in real bash in `cwd` (inside self.tmp), with no inherited environment beyond PATH."""
            return subprocess.run(["bash", "-c", cmd], cwd=cwd, capture_output=True, text=True, timeout=10,
                                  stdin=subprocess.DEVNULL, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                                                 "HOME": self.tmp, "LC_ALL": "C"})

        def r31_store(self):
            store = os.path.join(self.tmp, "store")
            os.makedirs(store, exist_ok=True)
            with open(os.path.join(store, "state.md"), "w") as f:
                f.write("old\n")
            os.environ["AIQT_STORE_ROOT"] = store
            return store

        def test_r31_work_budget_bounds_the_bash_analysis(self):
            # finding (codex HIGH): a 65,536-byte command of `: > x;` repeats before a future store write took about
            # 75 ms of analysis; the analysis now runs under a work budget and fails OPEN when it is spent, with the
            # per-token work cut (store roots read once, regex fast path, lazy per-literal decisions)
            S, F = f"{PROJ}/private/state.md", "2099-01-01T00:00Z"
            tail = f"printf '%s\\n' 'heartbeat: {F}' > {S}"
            dense = ": > x;" * ((65536 - len(tail)) // 6) + tail
            self.assertEqual(self.ev("Bash", command=dense), [])  # the budget is spent: fail open
            with self.assertRaises(_Exhausted):  # deterministic: the budget, not the host's speed, bounds it
                _analyze(dense, frozenset((f"{PROJ}/repo",)), _StoreCtx(), _Budget(BASH_WORK_BUDGET))
            small = ": > x;" * 1000 + tail
            self.assertEqual(self.ev("Bash", command=small), [F])  # inside the budget: still judged
            ordinary = "".join(f"echo 'line {i} of the audit log' >> /dev/shm/x{i % 7}\n" for i in range(1400)) + tail
            self.assertGreater(len(ordinary), 60000)
            self.assertEqual(self.ev("Bash", command=ordinary), [F])  # 64 KiB of ordinary lines stays inside it
            # load-robust timing: the growth ratio from N to GROWTH * N (interleaved, best of 5) of an in-budget
            # command, and a subprocess ceiling (a hang or a runaway is interrupted, never waited out). The dense
            # command's latency is bounded by an ORDINARY 64 KiB command judged in full on the same host (a PEER
            # check), not by a wall-clock ceiling
            code = TIMED_PRELUDE + (
                f"S = '{PROJ}/private/state.md'\n"
                "def run(n):\n"
                "    c = ''.join('echo line %d >> /dev/shm/x\\n' % i for i in range(n)) + "
                "\"printf 'hb 2099-01-01T00:00Z' > \" + S\n"
                "    assert m.evaluate({'tool_name': 'Bash', 'tool_input': {'command': c}, 'cwd': '/'}, now), n\n"
                "t = ': > x;' * 10900 + \"printf 'hb 2099-01-01T00:00Z' > \" + S\n"
                "def dense():\n"
                "    m.evaluate({'tool_name': 'Bash', 'tool_input': {'command': t}, 'cwd': '/'}, now)\n"
                "print(json.dumps(ratio(250, run) + versus(dense, ordinary('/', os.path.dirname(S)))))\n")
            small_t, large_t, dense_t, ref_t = run_timed(code, HANG_TIMEOUT)
            self.assertLess(large_t / max(small_t, 1e-3), LINEAR_LIMIT, (small_t, large_t))
            self.assertLess(dense_t / max(ref_t, 1e-3), PEER_LIMIT, (dense_t, ref_t))
            doc = " ".join(__doc__.split())
            for s in ("WORK BUDGET (round 31)", "is ALLOWED unchecked (fail OPEN", "an EXOTIC, disclosed miss"):
                self.assertIn(s, doc)

        def test_r31_relative_targets_resolve_against_cwd_and_cd(self):
            # finding (codex MED): a relative Bash destination bypassed store detection: `printf ... > store/state.md`
            # with the cwd holding store/, and `cd <store> && printf ... > state.md`, wrote the future heartbeat
            store = self.r31_store()
            F = "2099-01-01T00:00Z"
            denied = ((self.tmp, f"printf '%s\\n' 'heartbeat: {F}' > store/state.md"),
                      (self.tmp, f"cd {store} && printf '%s\\n' 'heartbeat: {F}' > state.md"),
                      (self.tmp, f"cd store; echo 'heartbeat: {F}' >> ./state.md"),
                      (self.tmp, f"cd -P -- store && echo 'heartbeat: {F}' >> state.md"),
                      (self.tmp, f"(cd {store} && echo 'heartbeat: {F}' > state.md)"),
                      (self.tmp, f"x=$(cd store && echo 'heartbeat: {F}' | tee -a state.md)"),
                      (store, f"echo 'heartbeat: {F}' >> state.md"),
                      (os.path.join(store, "sub") + "/..", f"echo 'heartbeat: {F}' >> state.md"),
                      (self.tmp, f"cd /dev/shm; cd {store}; echo 'heartbeat: {F}' >> state.md"),
                      (self.tmp, f"true || cd {store}; echo 'heartbeat: {F}' >> store/state.md"),
                      (self.tmp, f"cp /dev/null store/state.md <<< {F}"),
                      (self.tmp, f"cd {self.tmp}/nowhere; echo 'heartbeat: {F}' >> state.md # {store}"))
            for cwd, cmd in denied:
                self.assertEqual(self.ev("Bash", cwd=cwd, command=cmd), [F], (cwd, cmd))
            allowed = ((self.tmp, f"cd /dev/shm && echo 'heartbeat: {F}' > r31-not-a-store"),
                       (store, f"cd {self.tmp} && echo 'heartbeat: {F}' >> state.md"),
                       (self.tmp, f"echo 'heartbeat: {F}' >> storex/state.md"),
                       (self.tmp, f"echo 'heartbeat: {F}' >> state.md"),
                       (self.tmp, f"cd \"$D\" && echo 'heartbeat: {F}' >> state.md"),  # unknown, and no store named
                       (self.tmp, "printf '%s\\n' 'heartbeat: 2020-01-01T00:00Z' > store/state.md"))
            for cwd, cmd in allowed:
                self.assertEqual(self.ev("Bash", cwd=cwd, command=cmd), [], (cwd, cmd))
            # no payload cwd: a relative target is UNKNOWN, a store write only when the command names a store path
            self.assertEqual(evaluate({"tool_name": "Bash", "tool_input": {"command": f"echo {F} >> x.md"}},
                                      self.now), [])
            self.assertEqual(evaluate({"tool_name": "Bash", "tool_input": {
                "command": f"echo {F} >> x.md # {store}"}}, self.now), [F])
            self.assertIn("WHICH DIRECTORY (round 31)", " ".join(__doc__.split()))
            # real bash agrees: every denied command above writes the future heartbeat into the store
            if not shutil.which("bash"):
                self.skipTest("bash absent")
            for cwd, cmd in denied[:9]:
                with open(os.path.join(store, "state.md"), "w") as f:
                    f.write("old\n")
                os.makedirs(os.path.join(store, "sub"), exist_ok=True)
                self.bash_run(cmd, cwd)
                with open(os.path.join(store, "state.md")) as f:
                    self.assertIn(F, f.read(), cmd)

        def test_r31_literal_shell_c_strings_are_inspected(self):
            # finding (codex MED): `bash -c "printf ... > <store file>"` was allowed although real bash wrote the
            # future heartbeat; a statically known -c string is now analysed recursively with the same cwd
            store = self.r31_store()
            S, F = os.path.join(store, "state.md"), "2099-01-01T00:00Z"
            denied = (f"bash -c \"printf '%s\\n' 'heartbeat: {F}' > {S}\"", f"sh -c 'echo heartbeat: {F} >> {S}'",
                      f"dash -c 'echo heartbeat: {F} >> {S}'", f"zsh -c 'echo heartbeat: {F} >> {S}'",
                      f"bash -lc 'echo heartbeat: {F} >> {S}'", f"bash -e -o pipefail -c 'echo {F} >> {S}'",
                      f"/bin/bash -c \"echo \\\"heartbeat: {F}\\\" >> {S}\"",
                      f"sudo -n bash -c 'echo heartbeat: {F} >> {S}'",
                      f"bash -c \"sh -c 'echo heartbeat: {F} >> {S}'\"",
                      f"bash -c 'cd {store} && echo heartbeat: {F} >> state.md'",
                      f"bash -c 'echo heartbeat: {F} >> store/state.md'",  # the payload cwd (self.tmp)
                      f"echo heartbeat: {F} | bash -c 'cat >> {S}'")  # the enclosing pipeline feeds it
            for cmd in denied:
                self.assertEqual(self.ev("Bash", cwd=self.tmp, command=cmd), [F], cmd)
            allowed = (f"bash -c 'grep -F {F} {S}; echo checked >> {S}'", f"bash -c 'echo heartbeat: {F} > /dev/null'",
                       f"bash -c 'echo checked >> {S} # {F}'", f"bash -c \"echo {F} >> {S}; echo $HOME\"",
                       f"bash -c 'next run: {F}' >> /dev/null")
            for cmd in allowed:
                self.assertEqual(self.ev("Bash", cwd=self.tmp, command=cmd), [], cmd)
            self.assertEqual(_literal_shell_string("'a b'"), ("a b", [1, 2, 3]))
            self.assertEqual(_literal_shell_string('"a\\"b"'), ('a"b', [1, 3, 4]))
            for raw in ('"a $x"', '"a `b`"', "'a'b'", '"a', '"a\\"'):
                self.assertIsNone(_literal_shell_string(raw), raw)
            self.assertIn("SHELL -c (round 31)", " ".join(__doc__.split()))
            if not shutil.which("bash"):
                self.skipTest("bash absent")
            for cmd in denied[:3] + denied[6:7] + denied[8:9]:
                with open(S, "w") as f:
                    f.write("old\n")
                self.bash_run(cmd, self.tmp)
                with open(S) as f:
                    self.assertIn(F, f.read(), cmd)

        def test_r31_literal_counts_only_for_the_write_it_feeds(self):
            # finding (codex MED): an unrelated literal tainted every store write in the command: a search for the
            # literal, then an audit append, and a literal only in a comment, were denied although bash appended
            # only `checked`; a literal now counts only as DATA of a store write
            store = self.r31_store()
            S, F = os.path.join(store, "state.md"), "2099-01-01T00:00Z"
            allowed = (f"grep -F '{F}' {S}; echo checked >> {S}", f"echo checked >> {S} # {F}",
                       f"rg -c '{F}' {S} | wc -l; date -u >> {S}",
                       f"if grep -qF '{F}' {S}; then echo found >> {S}; else echo none >> {S}; fi",
                       f"n=$(grep -cF '{F}' {S}); echo \"n=$n\" >> {S}",
                       f"echo \"matches: $(grep -cF -e '{F}' {S})\" >> {S}",
                       f"echo 'seen {F}' > {self.tmp}/log.txt; echo checked >> {S}",
                       f"grep -F '{F}' {S} > {self.tmp}/hits; echo checked >> {S}",
                       f"echo checked >> {S}\n# note: {F} was already recorded",
                       f"test -s {S} && echo ok >> {S} # compare against {F}")
            for cmd in allowed:
                self.assertEqual(self.ev("Bash", cwd=self.tmp, command=cmd), [], cmd)
            denied = (f"printf '%s\\n' 'heartbeat: {F}' >> {S}", f"echo 'hb {F}' | tee -a {S}",
                      f"cat >> {S} <<< 'hb {F}'", f"cat <<'EOF' >> {S}\nhb {F}\nEOF",
                      f"cat <<EOF >> {S}\n# hb {F}\nEOF", f"cat <<-EOF >> {S}\n\thb {F}\n\tEOF",
                      f"printf 'hb {F}\\n' > {self.tmp}/stage; cp {self.tmp}/stage {S}",
                      f"echo 'hb {F}' > {self.tmp}/a; cp {self.tmp}/a {self.tmp}/b; mv {self.tmp}/b {S}",
                      f"{{ grep -F '{F}' {S}; echo checked; }} >> {S}", f"( echo 'hb {F}' ) >> {S}",
                      f"for t in '{F}'; do echo \"hb $t\" >> {S}; done", f"ts='{F}'; echo \"hb $ts\" >> {S}",
                      f"echo 'hb {F}' | while read l; do echo \"$l\" >> {S}; done",
                      f"x=$(grep -o '{F}' <<< 'hb {F}'); echo \"$x\" >> {S}",
                      f"rg -r '{F}' old {S} > {self.tmp}/o; cp {self.tmp}/o {S}",
                      f"exec >> {S}; echo 'hb {F}'", f"f() {{ echo 'hb {F}'; }}; f >> {S}",
                      f"echo \"$(echo 'hb {F}')\" >> {S}")
            for cmd in denied:
                self.assertEqual(self.ev("Bash", cwd=self.tmp, command=cmd), [F], cmd)
            self.assertIn("WHICH LITERALS (round 31)", " ".join(__doc__.split()))
            # real bash: the allowed commands never put the literal into the store; the denied ones do
            if not shutil.which("bash"):
                self.skipTest("bash absent")
            for cmd, want in [(c, False) for c in allowed] + [(c, True) for c in denied[:8] + denied[9:14]]:
                with open(S, "w") as f:
                    f.write("old\n")
                self.bash_run(cmd, self.tmp)
                with open(S) as f:
                    self.assertEqual(F in f.read(), want, cmd)

        def test_r31_unexecuted_conditional_write_is_judged_as_written(self):
            # by design (maintainer ruling on codex's MED): a write in a branch that never runs is judged as
            # written, and the docstring says so
            S, F = f"{PROJ}/private/state.md", "2099-01-01T00:00Z"
            for cmd in (f"if false; then printf '%s\\n' 'heartbeat: {F}' > {S}; fi",
                        f"[ -e /dev/shm/r31-missing-sentinel ] && echo 'heartbeat: {F}' >> {S}"):
                self.assertEqual(self.ev("Bash", command=cmd), [F], cmd)
            doc = " ".join(__doc__.split())
            self.assertIn("BY DESIGN (not modeled): a write in a branch that never runs is judged as written", doc)

        def test_r31_help_and_version_exit_on_every_modeled_tool(self):
            # finding (claude LOW): only perl's --help and --version were modeled as exiting, so `cp --version src
            # <store file>` was denied although cp exits at once; GNU cp, mv, sed, and ed and uutils install, tee,
            # and truncate exit on either wherever it stands before `--` (they permute)
            store = self.r31_store()
            S, F = os.path.join(store, "state.md"), "2099-01-01T00:00Z"
            allowed = (f"cp --version src {S} <<< {F}", f"cp src {S} --help <<< {F}", f"mv --help src {S} <<< {F}",
                       f"mv src {S} --vers <<< {F}", f"install --version src {S} <<< {F}",
                       f"echo {F} | tee --help {S}", f"echo {F} | tee {S} --version=1",
                       f"sed --version -i s/old/{F}/ {S}", f"sed -i s/old/{F}/ {S} --help",
                       f"truncate -s0 {S} --help <<< {F}", f"ed -s {S} --version <<< {F}")
            for cmd in allowed:
                self.assertEqual(self.ev("Bash", cwd=self.tmp, command=cmd), [], cmd)
            denied = (f"echo {F} | tee -- {S} --help", f"cp -S --version src {S} <<< {F}",
                      f"sed -i s/old/{F}/ -- {S} --help", f"cp src {S} <<< {F}",
                      f"ex -c 'w! {S}' --version src <<< {F}")
            for cmd in denied:
                self.assertEqual(self.ev("Bash", cwd=self.tmp, command=cmd), [F], cmd)
            for kind in ("cp", "mv", "install", "tee", "truncate", "ed", "sed"):
                self.assertEqual(_write_targets(kind, ["--help", "a", "b"]), [], kind)
                self.assertEqual(_write_targets(kind, ["a", "b", "--version=1"]), [], kind)
            self.assertIsNone(_write_targets("perl", ["--version=1", "-pi", "-e", "1", "X"]))  # round 30, unchanged
            self.assertNotIn("exit", _OPTION_SPECS["ex"])
            # the real tools, observed: each exits 0 and leaves the store file unchanged
            with open(os.path.join(self.tmp, "src"), "w") as f:
                f.write("src\n")
            for cmd in allowed:
                tool = cmd.split("| ")[-1].split()[0]
                if not shutil.which(tool):
                    continue
                with open(S, "w") as f:
                    f.write("old\n")
                r = self.bash_run(cmd, self.tmp)
                with open(S) as f:
                    self.assertEqual((r.returncode == 0 or "--version=1" in cmd, f.read()), (True, "old\n"), cmd)

        def test_r31_tokenizer_records_positions_heredocs_and_comments(self):
            # the analysis attributes literals by raw offsets: a word's (start, end) spans its text, a quoted
            # here-document body is skipped as bash reads it (its `>` is text), an unquoted one still tokenized, and
            # comments are recorded; the regex fast path and the character loop agree token for token
            cmd = "echo a > /tmp/x; cat <<'EOF' >> y\nb > z\nEOF\nls # c\ncat <<E\n$(tee w)\nE\n"
            t = _tokenize(cmd)
            words = [(tok[1], cmd[a:b]) for tok, (a, b) in zip(t.streams[-1], t.poss[-1]) if tok[0] == "w"]
            self.assertTrue(all(w == raw for w, raw in words if w != "$" and "'" not in raw), words)
            self.assertIn(("EOF", "'EOF'"), words)  # a quoted word spans its quotes
            self.assertNotIn("z", [w for w, _ in words])  # the quoted body is not command text
            self.assertEqual([cmd[a:b] for _op, a, b in t.heredocs], ["b > z\n", "$(tee w)\n"])
            self.assertEqual([cmd[a:b] for a, b in t.comments], ["# c"])
            self.assertEqual(len(t.streams), 2)  # the unquoted body's substitution is still scanned
            # a body is text, never commands: its `>` writes nothing (a false deny until round 30), while a write in
            # a substitution in an unquoted body still counts
            S, F = f"{PROJ}/private/state.md", "2099-01-01T00:00Z"
            for delim in ("E", "'E'"):
                self.assertEqual(self.ev("Bash", command=f"cat <<{delim} > /dev/shm/r31x\nhb > {S} {F} $(date -u)\nE"),
                                 [], delim)
            self.assertEqual(self.ev("Bash", command=f"cat <<E > /dev/shm/r31x\n$(echo hb {F} >> {S})\nE"), [F])
            self.assertEqual(self.ev("Bash", command=f"cat <<'E' > /dev/shm/r31x\n$(echo hb {F} >> {S})\nE"), [])
            for c in ("a;b|c&&d||e > f >> g < h 2>&1 &", "x=1 y=2 cmd -o --long=v 'q' \"d\" $v `b`",
                      "if a; then b; fi; for i in 1; do :; done; [[ a > b ]] && (( c > d ))"):
                self.assertEqual(_shell_tokens(c), _tokenize(c).streams)
            self.assertEqual(_shell_tokens("a  b\t;c"), [[("w", "a"), ("w", "b"), ("s",), ("w", "c")]])

        def test_r31_disclosures(self):
            doc = " ".join(__doc__.split())
            for s in ("WHICH DIRECTORY (round 31)", "SHELL -c (round 31)", "WHICH LITERALS (round 31)",
                      "WORK BUDGET (round 31)", "Round 31: GNU cp, mv, sed, and ed and uutils install, tee, and truncate",
                      "one quoted literal with no expansion: round 31 inspects a literal one",
                      "round 31 resolves `cd <store dir> && echo <future> > state.md`"):
                self.assertIn(s, doc)
            for s in ("(a relative path is never resolved", "no heredoc or wrapper analysis",
                      "AND the command holds a future-dated literal"):
                self.assertNotIn(s, doc)

        # -- round 32 (codex gpt-6-astra high and claude QA of round 31; the bounded final round) --
        def r32_check_bash(self, cases, store, cwd=None):
            """Real bash agrees with each (command, wants-denied) case: it writes F into the store exactly when the
            hook denies."""
            if not shutil.which("bash"):
                self.skipTest("bash absent")
            S = os.path.join(store, "state.md")
            for cmd, want in cases:
                for p in (S, os.path.join(self.tmp, "state.md"), os.path.join(self.tmp, "stage")):
                    if os.path.exists(p):
                        os.unlink(p)
                with open(S, "w") as f:
                    f.write("old\n")
                self.bash_run(cmd, cwd or self.tmp)
                with open(S) as f:
                    self.assertEqual("2099-01-01T00:00Z" in f.read(), want, cmd)

        def test_r32_item2_directory_work_is_budgeted(self):
            # finding (codex HIGH): directory-set expansion, path resolution, and set union were not charged to the
            # work budget, so 64 KiB of `(cd <dir>);` over 400 directories took about 200 ms in-process. They are
            # now charged BEFORE they are done, and a set above MAX_CWDS spends the budget (fail open)
            store = self.r31_store()
            F = "2099-01-01T00:00Z"
            cap, cost = globals().get("MAX_CWDS", 32), globals().get("CWD_COST", 6)  # r31 has neither
            dirs = []
            for i in range(cap + 8):
                d = os.path.join(self.tmp, f"d{i}")
                os.mkdir(d)
                dirs.append(d)
            tail = f"printf '%s\\n' 'heartbeat: {F}' > {store}/state.md"
            grow = "".join(f"true && cd {d}; " for d in dirs) + tail  # each cd JOINS the set: it grows past the bound
            self.assertLess(len(grow), 4096)
            with self.assertRaises(_Exhausted):  # deterministic: the bound, not the host's speed
                _analyze(grow, frozenset((self.tmp,)), _StoreCtx(), _Budget(BASH_WORK_BUDGET))
            self.assertEqual(self.ev("Bash", cwd=self.tmp, command=grow), [])  # fail open
            # inside the bound every directory resolution is charged: 31 directories, then 50 relative targets
            few = "".join(f"true && cd {d}; " for d in dirs[:cap - 1]) + \
                "".join(f"echo x > t{i}; " for i in range(50)) + tail
            b = _Budget(BASH_WORK_BUDGET)
            self.assertTrue(_analyze(few, frozenset((self.tmp,)), _StoreCtx(), b).store)
            self.assertGreater(BASH_WORK_BUDGET - b.left, 50 * cost * cap)
            self.assertEqual(self.ev("Bash", cwd=self.tmp, command=few), [F])  # still judged
            # the codex construct: 400 directories, 800 `(cd .);`, a future store write, padded to 65,536 bytes:
            # analysed in-process well under the latency bound, or the budget is spent (best of 5 in a child). The
            # bound is a PEER check: at most PEER_LIMIT times an ordinary 64 KiB command judged in full on the same
            # host (round 31's uncharged directory work took about 200 ms), not a wall-clock ceiling
            code = TIMED_PRELUDE + (
                "D = %r\n"
                "os.environ['AIQT_STORE_ROOT'] = os.path.join(D, 'store')\n"
                "ds = []\n"
                "for i in range(400):\n"
                "    p = os.path.join(D, 'c%%d' %% i)\n"
                "    os.mkdir(p)\n"
                "    ds.append(p)\n"
                "body = ''.join('(cd %%s);' %% p for p in ds) + '(cd .);' * 800\n"
                "tail = \"printf '%%s\\\\n' 'heartbeat: 2099-01-01T00:00Z' > \" + D + '/store/state.md'\n"
                "c = body + ' ' * (65536 - len(body) - len(tail)) + tail\n"
                "assert len(c) == 65536\n"
                "def run():\n"
                "    m.evaluate({'tool_name': 'Bash', 'tool_input': {'command': c}, 'cwd': D}, now)\n"
                "print(json.dumps(versus(run, ordinary(D, os.path.join(D, 'store')))))\n") % self.tmp
            t, ref_t = run_timed(code, HANG_TIMEOUT)
            self.assertLess(t / max(ref_t, 1e-3), PEER_LIMIT, (t, ref_t))  # about 1.7 on the development host
            self.assertEqual((globals().get("CWD_COST"), globals().get("MAX_CWDS")), (6, 32))

        def test_r32_item3_subshell_scopes_directory_state(self):
            # finding (codex MED, a round-31 regression): a subshell's cd leaked into the parent, so
            # `(cd <store> && ls >/dev/null); printf ... > state.md` was denied although bash wrote only the cwd's
            # state.md; `( ... )` now scopes the directory set, and `{ ... }` does not, as in bash
            store = self.r31_store()
            F = "2099-01-01T00:00Z"
            w = f"printf '%s\\n' 'heartbeat: {F}' > state.md"
            allowed = (f"(cd {store} && ls >/dev/null); {w}", f"(cd {store}) && {w}", f"( (cd {store}); ls ); {w}",
                       f"(cd {store}; ls)\n{w}", f"x=$(cd {store}; ls); {w}")
            denied = (f"{{ cd {store}; }}; {w}", f"(cd {store} && {w})", f"(cd {store}; (ls); {w})",
                      f"cd {store}; (cd /); {w}", f"(cd /; cd {store}; {w})")
            for cmd in allowed:
                self.assertEqual(self.ev("Bash", cwd=self.tmp, command=cmd), [], cmd)
            for cmd in denied:
                self.assertEqual(self.ev("Bash", cwd=self.tmp, command=cmd), [F], cmd)
            self.r32_check_bash([(c, False) for c in allowed] + [(c, True) for c in denied], store)

        def test_r32_item4_shell_c_output_reaching_the_store_counts(self):
            # finding (codex MED MISS): a literal shell -c string whose OUTPUT is redirected or piped into a store
            # target was allowed; a literal in the string is now also data of the wrapper, as for echo
            store = self.r31_store()
            S, F = os.path.join(store, "state.md"), "2099-01-01T00:00Z"
            denied = (f"bash -c 'echo heartbeat: {F}' >> {S}", f"sh -c 'echo heartbeat: {F}' | tee {S}",
                      f"bash -c \"printf 'hb {F}\\n'\" > {S}", f"bash -c 'echo hb {F}' | cat >> {S}",
                      f"bash -c 'echo hb {F}' > stage; cp stage {S}",
                      f"{{ bash -c 'echo hb {F}'; }} >> {S}", f"bash -c \"sh -c 'echo hb {F}'\" >> {S}")
            allowed = (f"bash -c 'echo hb {F}'; echo checked >> {S}", f"bash -c 'echo hb {F}' > /dev/null; date >> {S}",
                       f"bash -c 'grep -cF {F} {S}; echo checked >> {S}'",
                       f"bash -c 'echo hb {F}' > {self.tmp}/log; echo checked >> {S}")
            for cmd in denied:
                self.assertEqual(self.ev("Bash", cwd=self.tmp, command=cmd), [F], cmd)
            for cmd in allowed:
                self.assertEqual(self.ev("Bash", cwd=self.tmp, command=cmd), [], cmd)
            self.r32_check_bash([(c, True) for c in denied] + [(c, False) for c in allowed], store)

        def test_r32_item5_staging_through_shell_c(self):
            # finding (codex MED): a file staged by the outer command and copied by a literal shell -c string
            # (`printf ... > stage; bash -c 'cp stage <store file>'`) was allowed; the string's store write now
            # exposes the words it names, and its own writes, to the enclosing staging walk
            store = self.r31_store()
            S, F = os.path.join(store, "state.md"), "2099-01-01T00:00Z"
            denied = (f"printf '%s\\n' 'heartbeat: {F}' > stage; bash -c 'cp stage {S}'",
                      f"echo 'hb {F}' > stage; sh -c 'cat stage >> {S}'",
                      f"bash -c 'echo hb {F} > stage'; cp stage {S}",
                      f"echo 'hb {F}' > stage; bash -c 'cd {self.tmp} && cp stage {S}'")
            allowed = (f"printf '%s\\n' 'heartbeat: {F}' > stage; bash -c 'cp /dev/null {S}'",
                       f"bash -c 'echo hb {F} > stage'; cp /dev/null {S}")
            for cmd in denied:
                self.assertEqual(self.ev("Bash", cwd=self.tmp, command=cmd), [F], cmd)
            for cmd in allowed:
                self.assertEqual(self.ev("Bash", cwd=self.tmp, command=cmd), [], cmd)
            self.r32_check_bash([(c, True) for c in denied] + [(c, False) for c in allowed], store)

        def test_r32_matching_search_pattern_counts(self):
            # finding (claude LOW EXOTIC): every grep/rg pattern in a search-only substitution was exempt, "a search
            # prints file text or a count, never its pattern", but a MATCHING search prints file text holding its
            # pattern (with -o, exactly it); only a pipeline ending in wc or a counting/quiet/file-list search is
            # exempt now
            store = self.r31_store()
            S, F = os.path.join(store, "state.md"), "2099-01-01T00:00Z"
            hits = os.path.join(self.tmp, "hits.log")
            with open(hits, "w") as f:
                f.write(f"seen {F}\n")
            denied = (f"echo \"$(grep -oF '{F}' {hits})\" >> {S}", f"x=$(grep -F '{F}' {hits}); echo \"$x\" >> {S}",
                      f"echo \"$(rg -F '{F}' {hits})\" >> {S}", f"echo \"$(grep -F '{F}' {hits} | grep -v none)\" >> {S}",
                      f"echo \"$(grep -F '{F}' {hits} || wc -l {hits})\" >> {S}")
            allowed = (f"echo \"n=$(grep -cF '{F}' {hits})\" >> {S}", f"echo \"f=$(grep -lF '{F}' {hits})\" >> {S}",
                       f"echo \"n=$(grep -F '{F}' {hits} | wc -l)\" >> {S}", f"echo \"n=$(rg --count -F '{F}' {hits})\" >> {S}",
                       f"echo \"f=$(grep -LF '{F}' {S})\" >> {S}", f"echo \"q=$(grep -qF '{F}' {hits}; echo $?)\" >> {S}",
                       f"echo \"n=$(grep -F '{F}' {hits} | grep -c seen)\" >> {S}")
            for cmd in denied:
                self.assertEqual(self.ev("Bash", cwd=self.tmp, command=cmd), [F], cmd)
            for cmd in allowed[:5] + allowed[6:]:
                self.assertEqual(self.ev("Bash", cwd=self.tmp, command=cmd), [], cmd)
            # a substitution that also runs echo is not search-only: its pattern counts (the conservative direction)
            self.assertEqual(self.ev("Bash", cwd=self.tmp, command=allowed[5]), [F])
            if not shutil.which("rg"):
                denied, allowed = denied[:2] + denied[3:], allowed[:3] + allowed[4:]
            self.r32_check_bash([(c, True) for c in denied] + [(c, False) for c in allowed], store)
            doc = " ".join(__doc__.split())
            self.assertIn("A MATCHING search that prints lines prints file text holding its pattern", doc)
            self.assertNotIn("a search prints file text or a count, never its pattern", doc)

        def test_r32_timing_tests_are_interleaved_and_hang_guarded(self):
            # the load-sensitive growth-ratio tests (one flaked at load 34) now interleave their sizes, best of N,
            # at larger sizes, in a child under the hang ceiling
            self.assertIn("def interleaved(sizes, run, reps=5)", TIMED_PRELUDE)
            self.assertIn("return interleaved((n, GROWTH * n), same_work, reps)", TIMED_PRELUDE)
            self.assertIn("GROWTH = %d\n" % GROWTH, TIMED_PRELUDE)
            for test, needle in ((T.test_r4_scan_is_linear, "ratio(n, r)"),
                                 (T.test_r26_item5_unreadable_edit_literal_index_is_linear, "ratio(4000, run)"),
                                 (T.test_r31_work_budget_bounds_the_bash_analysis, "ratio(250, run)"),
                                 (T.test_r8_table_context_carry_over_scales, "interleaved((10000, 160000), run, 3)"),
                                 (T.test_r7_bash_writes_is_linear, "ratio(12500, run, 3)"),
                                 (T.test_r7_shell_tokens_linear, "ratio(12500, run, 3)"),
                                 (T.test_bash_scan_is_linear, "ratio(12500, run, 3)"),
                                 (T.test_r5_many_distinct_literals_linear_and_bounded, "ratio(5000, run, 3)"),
                                 (T.test_r6_table_scan_is_linear, "ratio(6250, run, 3)"),
                                 (T.test_r32_item2_directory_work_is_budgeted, "versus(run, ordinary("),
                                 (T.test_r33_wrapper_check_is_constant_per_wrapper, "versus(run_codex, ordinary(")):
                src = inspect.getsource(test)
                self.assertIn(needle, src)
                self.assertIn("run_timed(code, HANG_TIMEOUT)", src)
                self.assertNotIn("best(2 * n, r)", src)
            # a wall-clock bound in any test is rejected by test_no_wall_clock_verdict (an AST scan)

        def test_r32_disclosures(self):
            doc = " ".join(__doc__.split())
            for s in ("a `( ... )` subshell SCOPES the set", "A `{ ... }` group does not scope, as in bash",
                      "At most MAX_CWDS (32) directories are tracked", "it ALSO counts, as for echo, when the WRAPPER's "
                      "output reaches a store write", "KNOWN GAP (disclosed, not modeled)", "DIRECTORY work is charged too, "
                      "BEFORE it is done", "(cd <store dir> && ls); printf ... > state.md"):
                self.assertIn(s, doc)

        def test_r33_wrapper_check_is_constant_per_wrapper(self):
            # finding (codex HIGH, a round-32 regression): each shell -c wrapper rescanned its whole pipeline and
            # rebuilt its separator text, work of wrappers x pipeline length the budget never saw: 64 KiB of 450
            # piped `sh -c` wrappers and 1,050 `:` took about 107 ms in-process (round 31: 14.5 ms) with 1,794
            # budget steps left. Each pipeline's aggregates are now computed once, charged before the pass
            store = self.r31_store()
            F = "2099-01-01T00:00Z"
            tail = f"; echo checked > {store}/state.md"

            def build(w, n):
                return " |\n".join([f"sh -c 'echo {F}'"] * w + [":"] * n) + tail

            def spend(c):  # the analysis, then every literal's decision, on one budget as bash_future runs them
                b = _Budget(BASH_WORK_BUDGET)
                an = _analyze(c, frozenset((self.tmp,)), _StoreCtx(), b)
                left = b.left
                for i in range(len(c)):
                    if c.startswith(F, i):
                        an.counts(i)
                return left - b.left
            codex = build(450, 1050).ljust(65536)
            self.assertEqual(len(codex), 65536)
            with self.assertRaises(_Exhausted):  # deterministic: the budget, not the host's speed, bounds it
                spend(codex)
            self.assertEqual(self.ev("Bash", cwd=self.tmp, command=codex), [])  # fail open
            # inside the budget it is still judged (the conservative direction: each wrapper's output is piped into
            # a command outside READ_COMMANDS), and the per-literal phase pays for the pipeline walk up front
            for w in (120, 300):
                c = build(w, 3 * w)
                self.assertEqual(self.ev("Bash", cwd=self.tmp, command=c), [F])
                self.assertGreaterEqual(spend(c), 4 * 4 * w)
            # in a child under the hang ceiling: the codex construction in-process against an ordinary 64 KiB
            # command judged in full (a PEER check, about 1.2 on the development host; round 32 took about 107 ms),
            # and the growth from 37 to GROWTH * 37 wrappers (interleaved, best of 5; round 32's per-wrapper
            # rescan grew it by about 3.2 times per doubling)
            code = TIMED_PRELUDE + (
                "D = %r\n"
                "os.environ['AIQT_STORE_ROOT'] = os.path.join(D, 'store')\n"
                "tail = '; echo checked > ' + D + '/store/state.md'\n"
                "def build(w, n):\n"
                "    return ' |\\n'.join([\"sh -c 'echo 2099-01-01T00:00Z'\"] * w + [':'] * n) + tail\n"
                "codex = build(450, 1050).ljust(65536)\n"
                "def run(w):\n"
                "    m.evaluate({'tool_name': 'Bash', 'tool_input': {'command': build(w, 3 * w)}, 'cwd': D}, now)\n"
                "def run_codex():\n"
                "    m.evaluate({'tool_name': 'Bash', 'tool_input': {'command': codex}, 'cwd': D}, now)\n"
                "print(json.dumps(versus(run_codex, ordinary(D, os.path.join(D, 'store'))) + ratio(37, run)))\n"
                ) % self.tmp
            t_codex, ref_t, small, large = run_timed(code, HANG_TIMEOUT)
            self.assertLess(t_codex / max(ref_t, 1e-3), PEER_LIMIT, (t_codex, ref_t))
            self.assertLess(large / max(small, 1e-3), LINEAR_LIMIT, (small, large))
            doc = " ".join(__doc__.split())
            self.assertIn("Round 33: the shell -c WRAPPER check is charged too", doc)

        # -- no wall-clock verdict (a 2.0 s ceiling failed at 2.53 s on a slower CI runner) --
        HANG_GUARD_TESTS = ("test_r26_item6_hang_guard_interrupts",)

        def test_no_wall_clock_verdict(self):
            """Residual (disclosed): the scan covers direct calls, imported aliases, and assigned aliases within a
            function, not values passed between functions, so a time figure handed to a helper that asserts on it
            is not flagged. Nor is a reading from a clock the scan does not name (os.times, date.today,
            time.localtime or gmtime, a third-party clock, a file's mtime), nor a time figure routed through an
            expression form the scan does not follow: an assignment expression (walrus), a dict literal, a
            container built by a comprehension or filled by a store into a subscript, a starred or mismatched
            unpacking, or any other routing; see _wall_clock_asserts."""
            # every assertion of this file is scanned (see _wall_clock_asserts): none bounds a time figure, a
            # ratio of two time figures excepted, outside the hang-guard tests named in HANG_GUARD_TESTS
            with open(os.path.abspath(__file__), encoding="utf-8") as f:
                src = f.read()
            self.assertEqual(_wall_clock_asserts(src, self.HANG_GUARD_TESTS), [])
            flagged = [name for name, _line in _wall_clock_asserts(src)]
            for name in self.HANG_GUARD_TESTS:  # an exemption names a real hang guard the scan would flag
                self.assertIn(name, flagged)
            # the forms a substring check missed are caught: a harness result, an elapsed name, a bare assert, a
            # for target, a time.time difference, and a comparison inside assertTrue
            bad = ("def test_a(self):\n    small, large = run_timed(code, HANG_TIMEOUT)\n"
                   "    self.assertLess(large, 2.0)\n"
                   "def test_b(self):\n    started = time.monotonic()\n    run()\n"
                   "    elapsed = time.monotonic() - started\n    self.assertLess(elapsed, 0.5)\n"
                   "def test_c(self):\n    t0 = time.perf_counter()\n    assert time.perf_counter() - t0 < 1\n"
                   "def test_d(self):\n    for name, (s, big) in run_timed(c, 9).items():\n"
                   "        self.assertTrue(big < 3, name)\n"
                   "def test_e(self):\n    t = time.time()\n    self.assertLessEqual(time.time() - t, 1)\n"
                   "def test_f(self):\n    def inner():\n        self.assertLess(growth_in_child(src, 9)[1], 1)\n")
            self.assertEqual([name for name, _line in _wall_clock_asserts(bad)],
                             ["test_a", "test_b", "test_c", "test_d", "test_e", "test_f"])
            # a ratio of two time figures, a time figure in the message only, and an exempt hang guard pass
            good = ("def test_g(self):\n    small, large = run_timed(code, HANG_TIMEOUT)\n"
                    "    self.assertLess(large / max(small, 1e-3), LINEAR_LIMIT, (small, large))\n"
                    "    self.assertTrue(large / small < 2, (small, large))\n"
                    "def test_h(self):\n    t0 = time.monotonic()\n    self.assertLess(time.monotonic() - t0, 30.0)\n")
            self.assertEqual(_wall_clock_asserts(good, ("test_h",)), [])
            self.assertEqual(_wall_clock_asserts(good), [("test_h", 7)])
            # a clock read through an alias (import-as, from-import, an assigned name) or a keyword operand is
            # caught; the same aliases used for a hang-guard timeout, a count, or a msg keyword are not
            bad, want, good = _wall_clock_alias_fixtures()
            self.assertEqual([name for name, _line in _wall_clock_asserts(bad)], want)
            self.assertEqual(_wall_clock_asserts(good), [])

        # -- sibling parity on a single-hook install --
        PARITY_TESTS = ("test_shared_grammar_identical_to_sibling",
                        "test_r13_code_quote_helpers_identical_to_sibling")
        PARITY_SIBLINGS = ("stamp-truth-stop.py",)

        def _parity_in_copy(self, siblings, value, dangling=False):
            """Copy this file (and `siblings`, found beside it) into a fresh directory, run ONLY the copy's
            sibling-parity tests in a child interpreter with AIQT_HOOKS_REQUIRE_SIBLINGS set to `value` (None:
            unset, whatever the caller has), and return ([rc, run, skipped, failures, errors], child stderr).
            With `dangling`, each sibling is a symlink to a missing target: it EXISTS but cannot be read."""
            base = "/dev/shm" if os.path.isdir("/dev/shm") else None
            d = tempfile.mkdtemp(prefix="sib.", dir=base)
            try:
                me = os.path.join(d, os.path.basename(os.path.abspath(__file__)))
                shutil.copyfile(os.path.abspath(__file__), me)
                for sib in siblings:
                    shutil.copyfile(_sibling_or_skip(sib), os.path.join(d, sib))
                if dangling:
                    for sib in self.PARITY_SIBLINGS:
                        os.symlink(os.path.join(d, "no-such-target"), os.path.join(d, sib))
                env = {k: v for k, v in os.environ.items() if k != "AIQT_HOOKS_REQUIRE_SIBLINGS"}
                if value is not None:
                    env["AIQT_HOOKS_REQUIRE_SIBLINGS"] = value
                code = ("import importlib.util as u, json, unittest\n"
                        "s = u.spec_from_file_location('m', %r)\n"
                        "m = u.module_from_spec(s)\n"
                        "s.loader.exec_module(m)\n"
                        "names = %r\n"
                        "unittest.TestLoader.loadTestsFromTestCase = lambda self, tc: unittest.TestSuite("
                        "tc(n) for n in names)\n"
                        "box, run = [], unittest.TextTestRunner.run\n"
                        "unittest.TextTestRunner.run = lambda self, t: box.append(run(self, t)) or box[-1]\n"
                        "rc = m._self_test()\n"
                        "r = box[0]\n"
                        "print(json.dumps([rc, r.testsRun, len(r.skipped), len(r.failures), len(r.errors)]))\n"
                        ) % (me, self.PARITY_TESTS)
                p = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code], env=env, capture_output=True,
                                   text=True, timeout=120)
                self.assertTrue(p.stdout.strip(), p.stderr)
                return json.loads(p.stdout.strip().splitlines()[-1]), p.stderr
            finally:
                shutil.rmtree(d, ignore_errors=True)

        def test_sibling_parity_skips_alone_and_fails_when_required(self):
            # a single-hook install: each sibling-parity test is SKIPPED (not passed) with a message naming the
            # absent sibling, unless AIQT_HOOKS_REQUIRE_SIBLINGS=1, when the same absence FAILS it
            n = len(self.PARITY_TESTS)
            for value in (None, "0", ""):
                got, err = self._parity_in_copy((), value)
                self.assertEqual(got, [0, n, n, 0, 0], (value, err))
                for sib in self.PARITY_SIBLINGS:
                    self.assertIn(f"sibling hook {sib} is absent (a standalone install)", err)
            got, err = self._parity_in_copy((), "1")
            self.assertEqual(got, [1, n, 0, n, 0], err)
            self.assertIn("AIQT_HOOKS_REQUIRE_SIBLINGS=1 requires it", err)
            # a sibling that EXISTS but cannot be read fails (never skips), with or without the variable
            for value in (None, "1"):
                got, err = self._parity_in_copy((), value, dangling=True)
                self.assertEqual((got[0], got[1], got[2], got[3] + got[4]), (1, n, 0, n), (value, err))

        def test_sibling_parity_runs_and_passes_with_siblings_present(self):
            # with every sibling beside the copy the parity tests RUN and pass, whatever the variable says
            n = len(self.PARITY_TESTS)
            for value in (None, "1"):
                got, err = self._parity_in_copy(self.PARITY_SIBLINGS, value)
                self.assertEqual(got, [0, n, 0, 0, 0], (value, err))

    try:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.TestLoader().loadTestsFromTestCase(T))
    finally:
        shutil.rmtree(_BASE, ignore_errors=True)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
