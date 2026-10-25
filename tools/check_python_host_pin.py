#!/usr/bin/env python3
"""Python host-patch pin gate (PYTHON-HOST-PIN): the single source .aiqt/python-host.toml names the exact
CPython patch release the maintainers' host runs, and the host-patch CI jobs run their suites on exactly
that release.

Every other job in .github/workflows/quality.yml that sets up Python pins python-version '3.14' (the
Quality gates job, which aggregates the others, sets up none). With check-latest off, its default,
setup-python may resolve that pin to a 3.14 already on the runner rather than the newest patch release,
while the maintainers' host runs one fixed distribution patch release, so a change can pass every local
suite and fail only in CI on a patch-release difference. The host-patch jobs (HOST_JOBS) run the suites a
newer patch release most often breaks on the host's own patch release, beside the '3.14' jobs of
quality.yml, which stay required and unchanged. A passing local suite on the host patch then predicts a
required CI leg, and a failure only on a '3.14' leg is diagnostic evidence of a patch-release difference,
not proof that the change is innocent. This gate keeps the workflow's top level and each host-patch job
whole and the statements of that patch release in agreement.

Legs, in order:
  source       the single source is a regular UTF-8 file that parses and carries exactly the keys
               format-version (the integer 1) and python-host-patch (a "MAJOR.MINOR.PATCH" string with no
               leading zero and at most four digits in each component, PATCH_RE); anything else is
               cannot-evaluate, and so is a source that tomllib refuses with a RecursionError (deep nesting),
               a bare ValueError (an over-long integer) or a MemoryError, and a component that does not
               convert.
  jobs         .github/workflows/quality.yml carries each HOST_JOBS job, and its top level and each such
               job are whole (JOB_RULES):
                 workflow-keys  the workflow carries no top-level key but name, on, concurrency,
                                permissions and jobs (TOP_KEYS): a top-level defaults: or env: changes how
                                every run step of every job executes (defaults: run: shell: true {0} makes
                                each step exit 0 without running its command); a concurrency: block can
                                only cancel a run, which Quality gates reads as not success, and
                                tools/check_ci_parity.py pins its content;
                 job-keys       the job carries no key but name, runs-on and steps (an if:,
                                continue-on-error:, env:, defaults:, container: or strategy: can skip, mask,
                                move or change what it runs);
                 runner         runs-on is RUNNER, ubuntu-24.04: the setup-python versions manifest lists a
                                patch release's Linux builds by Ubuntu version, so the job names its image;
                 checkout       the first step is the checkout step as written (uses: CHECKOUT_USES and a
                                with: of exactly CHECKOUT_INPUTS, no other key), and no other step uses a
                                checkout action: a ref:, repository: or path: input, or a second checkout,
                                can put another tree under the gate and the suite;
                 step-env       no step of the job carries env:: a step env: changes what an otherwise
                                accepted run: command does (a BASH_ENV value, or a write to GITHUB_PATH
                                or GITHUB_ENV through it), so env: on any step, held or extra, is a
                                finding, and the job-keys rule keeps refusing a job-level env:;
                 setup          exactly one step uses setup-python; it carries no key but name, uses and
                                with:, its with: holds only python-version, and that is the one line in the
                                job that names python-version (a job-level env: or another key never counts
                                as the pin);
                 pin            that python-version is the host patch;
                 gate-step      the step right after the setup-python step is the Python host pin step, an
                                executable step whose run: is GATE_RUN; an executable step carries
                                exactly the keys name, run and shell, its shell: written as the
                                exact line shell: bash once its indentation is removed (RUN_KEYS,
                                STEP_SHELL_LINE: tools/check_ci_parity.py binds that line on every
                                run: step), so a step with no shell:, a shell: other than bash and a
                                shell: bash written on any other line (a quoted 'bash' or "bash", a
                                trailing comment, trailing whitespace, a second space before the
                                value or a - list-item line among them, each of which a YAML reader
                                parses to bash) are each refused in their own words, and so are any
                                other key and a missing name:;
                 suite-step     the job's suite steps, each step whose run: is a HOST_SUITES run or
                                names a suite runner (SUITE_RUNNERS) by a token's normpath base name
                                (_suite_shaped), run exactly the job's HOST_SUITES tuple, in that
                                order, none omitted, repeated or added, each an executable step after
                                the setup-python step, so after the Python host pin step
                                (opf-host-patch: the OPF tooling self-test; git-fixture-env-host-patch:
                                the four git-fixture-env suites the '3.14' jobs run as separate lanes,
                                core, config-combined, config-malformed and config-system, in that
                                order);
                 outside-judge  the judge step holds its place: exactly one step outside the HOST_JOBS
                                jobs runs this gate with --jobs-only (JOBS_ONLY_RUN) as its own run:
                                command, judged by command tokens, never by exact text. EVERY job
                                outside HOST_JOBS is read whole; no job is selected or skipped by
                                literal text. Each is read with the host-patch jobs' own line model
                                extended by three spellings that model refuses there: a LITERAL (|)
                                block scalar (an optional chomping indicator, no indentation
                                indicator), whose body lines, each more indented than its key, are
                                kept, a #-leading line included (inside a literal block it
                                is content, not a YAML comment; a BLANK body line is dropped by
                                the line reader, as the shell ignores one), each held to the same
                                backslash and
                                anchor/alias/tag refusals as any other job line, and read as the
                                value line by line (a FOLDED > opener is cannot-evaluate, never
                                decoded: folding joins body lines with spaces, so the text a YAML
                                reader hands the shell is not any literal line this model reads); a
                                one-line flow sequence that can define no key (no colon, brace,
                                quote or opener inside), read as one opaque scalar; and a bare
                                scalar list item (a needs: member among them). Every LINE of every
                                run: value of every such job, a block scalar's body lines
                                included, is tokenized separately with shlex (POSIX): a newline is a
                                command boundary, and shlex is a tokenizer, not a shell (it resolves
                                quotes, never operators, expansions or continuations), so tokens are
                                compared, never executed:
                                a step is judge-shaped when any token of any of its lines is
                                --jobs-only or any such token's os.path.normpath base name is this
                                gate's file name, and a judge-shaped step counts as the judge only
                                when EXACTLY ONE of its lines carries any token and that line's
                                tokens equal JOBS_ONLY_RUN's token for token (a judge spread over
                                two lines never counts: a shell runs each line as its own command),
                                the path token compared after
                                normpath and carrying no .. segment (so a doubled space, a ./tools/
                                path, quoted trailing whitespace, a tab before the value and a
                                quote-spliced spelling whose quote removal builds the judge's tokens
                                each still count, and a docs/../tools/ path, equal only through the
                                lexical normpath, never does); every other judge-shaped step is a
                                finding (an unresolved judge-shaped command), and so is a step run:
                                that names this gate's file name or the flag literally while its
                                tokens resolve to neither (the literal-substring fallback, a
                                conservative extra category beyond the token predicates). A run: that
                                is not a step's own (a job-level run: or a with: input named run among
                                them) whose tokens are judge-shaped or the judge's, or whose text
                                names either literal, and any other scalar value of any outside job
                                naming either literal (a matrix or include value, an env value, a
                                with: input, a name: or a bare list item among them), are each a
                                finding: only a step's own run: may carry the judge. The counted step
                                carries exactly name, run and the line shell: bash (a missing shell:,
                                a shell: other than bash and a shell: bash written on any other line
                                each named); no non-comment line of a HOST_JOBS
                                job names --jobs-only, and no run: line of a HOST_JOBS job names
                                the flag's stem --jobs-o once every quote, backslash and dollar
                                character is deleted from its raw text (a raw-text read, never
                                token equality: a quote-spliced spelling the grammar admits among
                                them, and deliberately wider than the flag, so a host-patch job
                                naming the stem in any run: text, an echo argument among them, is
                                refused; the scanned lines are the parsed run: scalar's, a trailing
                                YAML comment of a one-line plain scalar excluded as YAML-dropped
                                text; a HOST_JOBS job's run: is a one-line scalar entry (ENTRY_RE),
                                so a run: | block scalar is cannot-evaluate at the host-job line
                                model BEFORE this flag read and no block body reaches it, while an
                                OUTSIDE job's literal block keeps its #-leading body lines; each
                                such run: line is still tokenized, so one carrying a
                                ${{ }} expression or one shlex cannot tokenize is cannot-evaluate
                                there too, and so is one carrying any character outside the closed
                                safe set a host-patch run: line is held to (HOST_RUN_SAFE: a dollar
                                sign, a backquote, a brace, a bracket, a glob character, a
                                redirection, a parenthesis and the other shell-active characters,
                                exit 2, the character named, closing the leak an opener list left
                                to the expansion classes not on it), and so is one whose first
                                word, after any leading time, -p, -- or coproc word, is a shell
                                assignment (NAME=value or NAME+=value, a PATH= prefix that
                                re-points which file the named command runs among them, exit 2,
                                the assignment named); and no job
                                that carries a counted or
                                judge-shaped step, nor any step of such a job, carries env: (a
                                job-level env: can silence, in CI, every step of the carrying job; an
                                env: on the judge step the judge itself; and an env: on an earlier
                                step every later step of the job, through a GITHUB_ENV write; an env:
                                on a step after the judge does not reach it, and is refused as policy
                                so the judge's job stays env-free); each env: and judge-step-key
                                diagnostic names the recorded line of its key and says whether the
                                carrying step is the counted judge or an unresolved judge-shaped
                                step. Because every outside job is read whole, a line of any of them
                                the extended model cannot read (a job property at an unexpected
                                indent, a quoted or explicit key, a flow mapping, a block opener
                                carrying an indentation indicator, an anchor or an alias among them),
                                a job or step key of any of them carrying an upper-case letter
                                (whether a workflow reader folds key case is not modelled), a run: of
                                any of them whose REAL parsed value is not one string (an empty
                                value, a nested mapping or sequence, a flow sequence, or a plain
                                scalar a YAML reader can resolve to an explicit null, a boolean,
                                the = value key, a number or a timestamp (its date and time
                                separated by T, t or any run of spaces and tabs) among them, each
                                refused
                                at its recorded key line by its named kind, the number by a
                                conservative spelling classifier, not a YAML parser (any plain
                                scalar beginning with a digit, a sign or a dot and spelled only in
                                digits, underscores, colons, dots, signs and the 0b/0o/0x letters
                                in either case with hex digits, fractional sexagesimal forms among
                                them, plus the
                                .inf/.nan words, is a number; the over-refusal is the disclosed
                                conservative side); a quoted scalar is a string in every YAML
                                reader and stays read), and a run: of any of them with a line that
                                shlex cannot tokenize or that carries a ${{ }} expression, which
                                GitHub resolves before the shell runs, a #-leading block-body line
                                included, are each
                                cannot-evaluate, never skipped; so is a non-comment line of any
                                outside job, its header and block-scalar bodies included, that
                                carries a backslash (a double-quoted YAML scalar resolves backslash
                                escapes and joins escaped line breaks, and in a block body a
                                trailing backslash is a shell line continuation, so the text a
                                reader or a shell builds can differ from every literal line) or
                                opens a YAML anchor, alias or tag where a value may start, a
                                #-leading block-body line included in both refusals. A comment line
                                outside a block-scalar body is inert (YAML drops it) and is never
                                read; a #-leading line inside a literal block body is not a comment
                                and is kept, refused and tokenized like every other body line (a
                                shell can still reach its text: quoted, piped into another command
                                or rebuilt, its first token is simply #). What no run: token and no
                                scalar value of a job spells is not read: a judge built at run time
                                by a shell expansion from parts that are not the literal names (a
                                variable spliced into the path or the flag among them) passes this
                                gate unread, and tools/check_ci_parity.py is the holder there
                                (measured at this commit: a run: splicing a variable into this gate's
                                path is exit 2 there, dynamic-target).
               So a top-level key outside TOP_KEYS, another runner or checkout, an env: on a
               host-patch job or on any of its steps, removing or moving a
               host-patch job, its setup-python step, its Python host pin step or one of its suite steps,
               omitting, reordering, repeating or adding a suite step, changing a job id or the command a held
               step runs, and disabling or re-pointing a held step (if:, continue-on-error:,
               working-directory:, a missing shell:, a shell: other than bash or a shell: bash written
               on any line but the exact line shell: bash) are each a finding. A held
               step carries a name:, but its display text is not held: it changes neither what a step runs nor how
               the Quality gates job reads a job's result, which is by job id. Jobs are read by a line model.
               A line that still carries a carriage return once its one trailing CR is removed, or carries
               U+0085, U+2028 or U+2029 (LINE_BREAKS, the set the python-floor gate refuses), is
               cannot-evaluate: a YAML reader may break the line there and read a key this model does not see.
               A line of a host-patch job or of any job outside them, the job's header line included,
               carrying a form feed or a vertical tab (CONTROL_CHARS) is also cannot-evaluate: neither
               is a YAML line break, and this model refuses every such line rather than modelling which
               of them another reader accepts.
               A top-level line is a plain key, a colon, then nothing or a value (TOP_RE); any other top-level
               line (a quoted or explicit key, a flow mapping, a document marker) and a repeated top-level key
               are cannot-evaluate. Under the top-level jobs: key, a job is a plain key at indent 2 and its
               content sits at indent 4 or deeper; any other line there, and a repeated job id, is
               cannot-evaluate. Inside a HOST_JOBS job, each line is a block mapping entry, or a "- " list
               item holding one, at indent 10 or less: a plain key, a colon, then nothing, a one-line quoted
               scalar or a plain scalar that opens no flow, block scalar, anchor, alias or tag (ENTRY_RE). Any
               other line (a run: | block among them), a repeated key and a line the indentation does not
               place are cannot-evaluate, and so is a line (a comment included) that names python-version in
               any case and is not one strict pin line (PIN_LINE_RE).
  interpreter  the running interpreter (sys.version_info) is the host patch, a final release. A difference
               is a finding (exit 1): run locally, it reports that this interpreter is not the committed
               host patch, so a passing local suite does not predict the host-patch CI jobs, and
               tools/run_all_checks.sh FAILS there; run in a host-patch CI job, it reports that the job did
               not run the pinned release.

  check_python_host_pin.py              run every leg over this repository
  check_python_host_pin.py --jobs-only  run the source and jobs legs only and skip the interpreter leg:
                                        the CI step of the registry job of quality.yml, which runs
                                        OUTSIDE the host-patch jobs this gate judges, so an edit to
                                        those jobs cannot silence every CI run of the jobs leg, and
                                        which pins the floor '3.14', not the host patch, so the
                                        interpreter leg cannot run there (the outside-judge rule holds
                                        that step's placement and the freedom from env: of its job and
                                        that job's every step)
  check_python_host_pin.py --self-test  fixture trees for every leg and every job rule, plus red-on-revert:
                                        for each finding leg, a copy of this gate loaded through importlib
                                        with that leg's call removed passes the fixture the intact gate
                                        refuses; for each job rule, a copy with that rule removed from
                                        JOB_RULES passes each of that rule's cases; a copy without the
                                        line-break refusal reads each line-break case; a copy without
                                        the control-character refusal reads each form-feed and
                                        vertical-tab case; a copy whose source parse maps only
                                        TOMLDecodeError lets a parser RecursionError escape, and a
                                        copy whose source parse drops one of RecursionError,
                                        MemoryError and ValueError lets that class, injected at the
                                        parse, escape, also over a key parser emulating a release
                                        that refuses the key inside it first; and the
                                        --jobs-only CLI wiring runs through main() on a pristine copy,
                                        with a copy that stops forwarding the flag and a copy whose main
                                        returns 0 without evaluating each red

Exit convention: 0 every leg passes; 1 a finding; 2 usage or cannot-evaluate (a missing, unreadable,
non-regular, symlinked, non-UTF-8 or malformed source or workflow, a workflow line carrying a line-break
character, a line of a host-patch job or of any job outside them, their accepted job headers included,
carrying a form feed or a vertical tab, a top-level line that is not a plain key, a repeated top-level
key, a line the line model cannot place or read, a line of any job outside the host-patch jobs that the
extended job model cannot read, a folded (>) block scalar of such a job, a job or step key of such a job
carrying an upper-case letter, a run: of such a job whose real parsed value is not one string (an empty
value, a nested mapping or sequence, a flow sequence, or a plain null, boolean, value-key (=),
number (a conservative spelling classifier) or timestamp (its date and time separated by T, t or any
run of spaces and tabs) scalar among them), a run: of such a job or of a host-patch job with a line
shlex cannot tokenize or carrying a ${{ }} expression, a run: line of a host-patch job carrying a
character outside the closed safe set it is held to (HOST_RUN_SAFE) or whose first word is a shell
assignment (HOST_RUN_ASSIGNMENT_RE), a
non-comment line of such a job,
its header and block-scalar bodies (#-leading body lines
included) carrying a
backslash or opening a YAML anchor, alias or tag, a pin line outside the strict form, unavailable
temporary storage).

DISCLOSED RESIDUAL. The jobs leg reads only quality.yml: its top-level keys; in the HOST_JOBS jobs, their
keys, runner, checkout step, the env: of every step, setup-python step, Python host pin step and suite steps;
and EVERY job outside those jobs, each read whole with the same job model extended by LITERAL (|) block
scalars (every non-blank body line kept, #-leading lines included, a blank body line dropped, as the
shell ignores one, and read line by line; a folded > opener is
cannot-evaluate, never decoded), colon-free one-line flow sequences (opaque) and bare scalar list items,
every line of every run: value of it, a block scalar's body lines included, tokenized separately with
shlex (a newline is a command boundary) and classified by tokens (the counted judge, one line whose
tokens are the judge command's with no other token-bearing line beside it; an unresolved judge-shaped
command; or neither, with a literal-substring fallback as a conservative extra category), every other
scalar value of it scanned for the two literal names, and the env: of every job that carries a counted
or judge-shaped step and of every step of such a
job (outside-judge). Another step in a host-patch job is only partly held: this gate refuses its env:
(step-env), a non-comment line naming --jobs-only and a run: line of the parsed scalar (a trailing
YAML comment of a one-line plain run: excluded as YAML-dropped text; a host-patch run: is a one-line
scalar entry, so a run: | block scalar is cannot-evaluate at the host-job line model before this
read) whose text names the flag's
stem --jobs-o once every quote, backslash and dollar character is deleted, a quote-spliced spelling
the grammar admits and an echo argument among them, the line still tokenized, so a ${{ }}
expression or a line shlex cannot tokenize there is cannot-evaluate (outside-judge), and so is a
run: line carrying any character outside the closed safe set it is held to (HOST_RUN_SAFE: a dollar
sign, a backquote, a brace, a bracket, a glob character, a redirection, a parenthesis and the other
shell-active characters, each named) or one whose first word is a shell assignment (a PATH=
prefix among them, the assignment named). This gate holds the Python host pin step and each suite
step to exactly name, run and the line shell: bash, and the judge step too; a suite step is one whose
run: is a HOST_SUITES run or has a token whose normpath base name is a suite runner's file name, so a
step that runs a suite runner as a module (python3 -m tools.check_selftest_execution among them)
names no such file, is not a suite step and is not refused when added; on another step of a host-patch job
it does not judge a working-directory:, an if: or a shell: other than bash, which changes what, where
or whether that step runs; and what the suites
test is theirs: a step whose run: command starts another interpreter, appends to GITHUB_PATH or GITHUB_ENV,
changes the checked-out tree, uses a local or composite action, or names an executable that itself
prints or re-interprets text (a script that echoes the flag, or env running its arguments as a
command under an assignment, among them, out of a text grammar's reach) is not judged here.
tools/check_ci_parity.py is the second holder for those edits: it binds the line shell: bash on
every step with a run: key (read once a trailing comment and trailing whitespace are removed, two
spellings this gate refuses on the steps it holds), and its step grammar accepts only name:, uses:,
run:, env:, with: and that line as step keys, so a working-directory:, an if: or any shell: other than
shell: bash is
cannot-evaluate for it (measured at this commit on an extra host-patch-job step carrying each of the
three: this gate passed, check_ci_parity refused), and in every job it refuses a uses: of an action
other than actions/checkout and actions/setup-python and a run: command outside its parity grammar. In
CI the jobs leg runs inside the host-patch jobs (their Python host pin steps) and once outside them, as
the --jobs-only judge step of the registry job, and the outside-judge rule holds that placement by the command tokens of every run: of
every outside job and the raw-text flag read of every run: of the host-patch jobs, so moving the
judge into a
host-patch job (a quote-spliced spelling among them), removing it, doubling it in any spelling
whose shlex tokens equal the judge command's (a quoted run: value, a tab before the value, a doubled
space, a ./tools/ path, quoted trailing whitespace, a quote-spliced path or flag and a one-command
literal block scalar among them), running any other judge-shaped command outside the
host-patch jobs, naming this gate's file name or the flag in a value that is not a step's own run:
property (a matrix or include value, an env value, a with: input or a job-level run: among them),
putting a key beyond name, run and shell on the judge step or leaving it without the line shell: bash, or an
env: on a job that carries a counted or
judge-shaped step or on any of that job's steps is a finding; a line of any outside job the extended
job model cannot read (a re-indented job body, a quoted env key, a flow-mapping step, a block opener
carrying an indentation indicator, an anchor or an alias among them), a folded (>) block scalar of it
(never decoded: folding joins body lines with spaces, so no literal line spells the value), a job or
step key of it carrying an upper-case letter, a run: of it whose real parsed value is not one string
(an empty value, a nested mapping or sequence, a flow sequence, or a plain null, boolean, value-key
(=), number (a conservative spelling classifier) or
timestamp scalar among them, refused at its recorded key line by its named kind; a timestamp is read
by the YAML 1.1 time fields, so a reader-extension spelling such as 2026-10-14T6:3:5Z, with one-digit
minutes and seconds, is read as a string), a run: of it with a
line shlex cannot tokenize or carrying a ${{ }} expression (a #-leading block-body line included), and
a non-comment line of it, its header and block-scalar bodies (#-leading body lines included) carrying
a backslash or opening a YAML anchor, alias or tag are each cannot-evaluate, never skipped; the host-patch
jobs' own Python host pin steps report each in CI while those steps still run, and
tools/run_all_checks.sh reports each in every local run. What stays CI-blind is an edit that leaves no CI run of
the jobs leg at all. A top-level defaults: or env: silences, in CI, every run step of every job, the judge
step included, so CI cannot report it; this gate (workflow-keys) and check_ci_parity refuse it where they
run outside that workflow, in tools/run_all_checks.sh. An edit that silences every step of both host-patch
jobs (a job-level env:, a BASH_ENV value, in each) and with it silences or removes the judge (a
job-level env: on the registry job, which sits inside check_ci_parity's disclosed environment-value
residual, which that gate's docstring owns; an env: on any step of the registry job, the judge step itself
or an earlier step whose GITHUB_ENV write reaches it; or removing the judge step
together with its tools/run_all_checks.sh member, which keeps check_ci_parity's set parity) leaves no CI
run of the jobs leg, so CI cannot report it either; each such pairing is refused by this gate's jobs leg
run outside CI, in tools/run_all_checks.sh, which owns that surface, as a finding or, for a spelling the
job model cannot read (a re-indented registry body or a quoted env key among them), as cannot-evaluate (a
job-level defaults:, container:,
continue-on-error: or uses: on the registry job is a check_ci_parity finding, reported in
tools/run_all_checks.sh when CI is silenced). What this gate's token model never reads: a judge built at
run time by a shell expansion from parts that are not the literal names (a variable spliced into the
path or the flag among them), whose tokens are not the judge's and spell neither literal name;
tools/check_ci_parity.py is the holder for those commands (measured at this commit on a full tree copy:
a run: splicing a variable into this gate's path is exit 2 there, dynamic-target; a run: of
${{ matrix.command }} is cannot-evaluate here and exit 2 there too, workflow-expression). The grammar of the whole workflow,
the setup-python uses: shape and every other pin
belong to the python-floor gate (tools/check_python_floor.py), which accepts a pin of a patch release only
inside the HOST_JOBS jobs of quality.yml and only when it equals this source, and holds every other
python-version in any workflow to the floor exactly. GitHub's runtime is not modelled: when the runner image
retires or the host patch has no build for it, setup-python fails the job, closed and never on another patch
release. On 2026-10-12 the setup-python versions manifest (actions/python-versions) listed Linux x64 builds of
3.14.4 for Ubuntu 22.04 and 24.04 only, so the move off ubuntu-24.04 is one reviewed change of RUNNER, both
runs-on lines and, if needed, the host patch. The interpreter leg judges only the interpreter that runs this
gate; in CI it runs only in the HOST_JOBS jobs. Equal patch releases are not equal builds: a distribution
build's downstream patches and the system libraries an interpreter links (OpenSSL, SQLite, expat, zlib and
others) can differ between the host and the runner, and this gate does not compare them. It does not check
that the committed patch is what the host runs: when the host interpreter changes, the maintainer updates the
source and both host-patch pins in one reviewed change, and until then every local run fails on the
interpreter leg. CI cost: the two jobs run the OPF tooling self-test and the four git-fixture-env suites a
second time in every run of the workflow, and an edit to a pull request's title or body starts no Quality run (the
workflow does not run on edited).

Run this gate isolated: python3 -I -B tools/check_python_host_pin.py
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_python_host_pin.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import importlib.util
import io
import os
import re
import shlex
import stat
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REL = ".aiqt/python-host.toml"
SOURCE_KEYS = {"format-version", "python-host-patch"}
WORKFLOW_REL = ".github/workflows/quality.yml"
# The host-patch jobs of WORKFLOW_REL, each with the ordered tuple of suites it exists to run (the run: text
# of one executable step each, in this order): git-fixture-env-host-patch runs the four git-fixture-env
# suites the '3.14' jobs run as separate lanes, in their order. tools/check_python_floor.py accepts a
# host-patch pin only inside these jobs; its HOST_JOBS names the same jobs, which the self-test holds.
HOST_SUITES = {
    "opf-host-patch": ("python3 -I -B opf/tools/opf.py --self-test",),
    "git-fixture-env-host-patch": (
        "python3 -I -B tools/check_selftest_execution.py --suite git-fixture-env-core",
        "python3 -I -B tools/check_selftest_execution.py --suite git-fixture-env-config-combined",
        "python3 -I -B tools/check_selftest_execution.py --suite git-fixture-env-config-malformed",
        "python3 -I -B tools/check_selftest_execution.py --suite git-fixture-env-config-system"),
}
HOST_JOBS = tuple(HOST_SUITES)
# Every suite run HOST_SUITES names, and the file names of the suite runners they run: a host-patch step
# whose run: is one of SUITE_RUNS, or has a token whose os.path.normpath base name is in SUITE_RUNNERS, is a
# suite step (_suite_shaped), so the suite-step rule holds every such step, an added, repeated or respelled
# one included, never only the texts it wants.
SUITE_RUNS = frozenset(run for runs in HOST_SUITES.values() for run in runs)
SUITE_RUNNERS = frozenset(os.path.basename(token) for run in SUITE_RUNS for token in shlex.split(run)
                          if token.endswith(".py"))
# The path this gate runs as; _judge_shaped and _counts_as_judge read it from command tokens.
GATE_PATH = "tools/check_python_host_pin.py"
# The step right after a host-patch job's setup-python step: this gate, which fails there when the job does
# not run the host patch.
GATE_RUN = "python3 -I -B " + GATE_PATH
# The runner image of each host-patch job. The setup-python versions manifest lists a patch release's Linux
# builds by Ubuntu version (on 2026-10-12, x64 builds of 3.14.4 for 22.04 and 24.04 only), so ubuntu-latest
# would fail the jobs once it moves to an image the host patch has no build for.
RUNNER = "ubuntu-24.04"
# The top-level keys the workflow may carry: a defaults: or env: block changes how every run step of every
# job executes. A concurrency: block can only cancel a run, never pass one, and check_ci_parity.py pins its
# content; a job-level concurrency: on a host-patch job stays a job-keys finding (JOB_KEYS).
TOP_KEYS = ("name", "on", "concurrency", "permissions", "jobs")
JOB_KEYS = {"name", "runs-on", "steps"}
# The first step of each host-patch job, as written: another ref, repository or path can put another tree
# under the gate and the suite.
CHECKOUT_USES = "actions/checkout@v4"
CHECKOUT_KEYS = {"uses", "with"}
CHECKOUT_INPUTS = {"fetch-depth": "0", "fetch-tags": "true"}
SETUP_KEYS = {"name", "uses", "with"}
# The keys of an executable step (_step_departures): the Python host pin step, each suite step and the
# --jobs-only judge step carry exactly these, their shell: written as the one line STEP_SHELL_LINE once its
# indentation is removed (_shell_departure), so a step with no shell:, a shell: other than bash and a
# shell: bash on any other line (a quoted 'bash' or "bash", a trailing comment, trailing whitespace, a
# second space before the value, a - list-item line) are each refused in their own words.
# tools/check_ci_parity.py binds the line shell: bash on every step with a run: key, read once a trailing
# comment and trailing whitespace are removed: this gate is the stricter of the two on those two
# spellings, and both refuse the quoted and doubled-space ones.
RUN_KEYS = frozenset({"name", "run", "shell"})
STEP_SHELL = "bash"
STEP_SHELL_LINE = "shell: " + STEP_SHELL
# The rules of the jobs leg; the self-test removes each from a copy of this gate to show its cases red on
# revert.
JOB_RULES = frozenset({"workflow-keys", "job-keys", "runner", "checkout", "step-env", "setup", "pin",
                       "gate-step", "suite-step", "outside-judge"})
# The jobs leg run outside the host-patch jobs: the one step of quality.yml outside HOST_JOBS that runs
# this gate with --jobs-only (in the committed workflow, the registry job's judge step). The outside-judge
# rule holds its placement by command tokens (_counts_as_judge / _judge_shaped), never by exact text: no
# non-comment host-patch-job line names the flag and no run: line of a host-patch job names the
# flag's stem once every quote, backslash and dollar character is deleted from its raw text
# (_host_flag_findings, a raw-text read), exactly one step outside those jobs has the judge command's shlex
# tokens as one line of its own run: property, with no other token-bearing line beside it and exactly the
# keys name, run and shell: bash, every other judge-shaped step and every other value naming
# GATE_BASENAME or the flag is a finding, and no job that carries a counted or judge-shaped step, nor
# any step of such a job, carries env:. EVERY job outside HOST_JOBS is read whole with the extended job model (_outside_entries /
# _job_tree), never selected by literal text; a line of it that model cannot read is cannot-evaluate,
# and a non-comment line of any outside job, a literal block's body lines included, carrying a backslash
# or opening a YAML anchor, alias or tag is cannot-evaluate, never skipped
# (_outside_line_refusals).
JOBS_ONLY_FLAG = "--jobs-only"
JOBS_ONLY_RUN = GATE_RUN + " " + JOBS_ONLY_FLAG
# The raw-text stem of the flag the host-patch flag read scans for (_names_flag_text /
# _host_flag_findings) once every quote, backslash and dollar character is deleted from a run: line:
# a deletion can leave other text between the stem and the rest of the flag (a quote splice the
# grammar admits among them, --jobs-o""nly leaving --jobs-only), so the scan reads this stem, not the
# whole flag. Every spelling that carries a dollar sign, a brace, a backquote or any other character
# outside the closed safe set (HOST_RUN_SAFE), for example a bare dollar splice --jobs-$X, a braced
# splice --jobs-${X} or --jobs-{o..o}nly, a $' ANSI-C quoted splice or a $( or backquote command
# substitution, is cannot-evaluate in a host-patch run: line (_refused_run_char), before this stem
# scan, rather than left to tools/check_ci_parity.py. No committed host-patch run: line names the
# stem, and each carries only safe characters.
JOBS_ONLY_STEM = "--jobs-o"
# A host-patch job's run: line is held to a CLOSED SAFE-CHARACTER GRAMMAR, not a list of refused
# openers (_host_flag_findings, _refused_run_char): the line is read only when EVERY character of
# its parsed scalar is in HOST_RUN_SAFE (ASCII letters and digits, space and tab, the punctuation
# _ - . / = : @ , + % and the two quote characters " and ' the deletion read models), and any other
# character makes the line cannot-evaluate (exit 2, the character named): a $, a backquote, a brace,
# a bracket, a glob * or ?, a redirection < or >, a (, a ), a |, an &, a ;, a !, a ~, a # (the parsed
# scalar carries no trailing YAML comment, so a # there is command text, not a dropped comment; a
# QUOTED run: whose text holds " # ", which the opener list passed, is now refused for it, the
# fail-closed direction), a backslash (a line continuation among its uses) or any other character.
# An opener LIST leaked expansion classes not on it (pathname expansion echo --jobs-[o]nly,
# echo --jobs-o* and echo --jobs-o?ly, and process substitution source <(printf ...)); the closed
# set refuses each of them by the character it must spell. An eval needs no refused character, so
# the grammar does not refuse one spelled only in safe characters: its line is read by the deletion
# read like any other. A ${{ }} expression is refused first, with its own wording
# (_run_line_tokens runs before this scan). The committed host-patch jobs carry eight run: lines with
# seven distinct texts, each ASCII letters, digits, spaces and the - _ . / punctuation, so each
# passes this grammar unchanged. A line the grammar admits is still cannot-evaluate (exit 2, the
# assignment named) when its first word, once any leading HOST_RUN_PREFIX_WORDS are skipped, is a
# shell assignment (HOST_RUN_ASSIGNMENT_RE, _leading_assignment): a PATH=tools:/usr/bin prefix is
# spelled in safe characters only, yet it re-points which file the named command runs. The held
# steps (the Python host pin step and each suite step) carry exactly name, run and the line shell: bash
# (RUN_KEYS, STEP_SHELL_LINE), so this safe-set reading of their run: lines is a bash reading. RESIDUAL,
# the set the module docstring lists: on a host-patch step this gate does not hold (an extra step
# such as the OPF job's Python host pin self-test step), it does not judge a working-directory:,
# an if: or a shell: other than bash, which changes what, where or whether that step runs (under
# shell: perl {0} the run: text is a perl program; the module docstring names
# tools/check_ci_parity.py as the second holder for those keys),
# and it does not judge a step whose run: command starts another interpreter, appends to
# GITHUB_PATH or GITHUB_ENV, changes the checked-out tree, uses a local or composite action, or
# names an executable that itself prints or re-interprets text (a script that echoes the flag, or
# env running its arguments as a command under an assignment, among them).
HOST_RUN_SAFE = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 \t_-./=:@,+%\"'")
# A shell assignment word a host-patch run: line may not begin with (_leading_assignment): NAME=value
# or NAME+=value, its name unquoted. HOST_RUN_PREFIX_WORDS are the words that can stand before such a
# prefix on one line and keep it an assignment (bash runs time -p PATH=x ls, time -- PATH=x ls and
# coproc PATH=x ls under the assigned PATH); a line beginning with one of them is read from the next
# word. Skipping -- or -p where it begins a line can only refuse more.
HOST_RUN_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")
HOST_RUN_PREFIX_WORDS = frozenset({"time", "-p", "--", "coproc"})
# The name each refused character is reported by (_refused_run_char); a character not listed is named
# by its repr.
HOST_RUN_CHAR_NAMES = {
    "$": "a dollar sign ($)", "`": "a backquote (`)", "{": "an open brace ({)",
    "}": "a close brace (})", "[": "an open bracket ([)", "]": "a close bracket (])",
    "*": "an asterisk (*)", "?": "a question mark (?)", "<": "a less-than sign (<)",
    ">": "a greater-than sign (>)", "(": "an open parenthesis (()", ")": "a close parenthesis ())",
    "|": "a pipe (|)", "&": "an ampersand (&)", ";": "a semicolon (;)", "!": "an exclamation mark (!)",
    "~": "a tilde (~)", "#": "a hash (#)", "\\": "a backslash (\\)",
}
# The judge command's shlex (POSIX) tokens. A step counts as the judge only when its own parsed run:'s
# tokens equal these token for token, the one path token compared after os.path.normpath and carrying
# no .. segment (_counts_as_judge; normpath is lexical, and through a symlinked directory a .. path can
# name another file); a step is judge-shaped when any token is JOBS_ONLY_FLAG or any token's normpath
# base name is GATE_BASENAME (_judge_shaped), and a judge-shaped step that does not count is a finding,
# never a silent pass.
JOBS_ONLY_TOKENS = tuple(shlex.split(JOBS_ONLY_RUN))
# This gate's file name: _judge_text reads it and JOBS_ONLY_FLAG as the literal names in the scan of
# every outside job's scalar values and in the conservative literal-substring fallback of the run:
# classification (_judge_findings).
GATE_BASENAME = os.path.basename(GATE_PATH)
# A YAML anchor, alias or tag opener in a non-comment line of any job outside HOST_JOBS: as the line's
# first content character, or right after a key's colon, a list dash or a flow opener. An alias can
# carry a value defined anywhere in the document and a tag can rewrite one, so such a line can name a
# command no literal line spells, and this model refuses it (_outside_line_refusals).
UNREAD_OPENER_RE = re.compile(r"(?:^[ \t]*|:[ \t]*|-[ \t]+|[\[{,][ \t]*)[&*!]")
# Control characters refused in any line of a host-patch job or of any job outside them, the job's header
# line included: neither is a YAML line break (LINE_BREAKS), and before this refusal some such lines
# passed this line model while others (a header comment carrying one) were accepted or cannot-evaluate
# by position, so the refusal holds every one of them out rather than modelling which.
CONTROL_CHARS = {"\f": "a form feed", "\v": "a vertical tab"}
# MAJOR.MINOR.PATCH, no leading zero, at most four digits in each component.
PATCH_RE = re.compile(r"([1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})")
PIN_KEY = "python-version"
# The one accepted spelling of a pin line, the python-floor gate's strict form: optional space indentation,
# an optional `- ` list marker, the bare key in lower case, optional spaces, a colon, one or more spaces,
# exactly one plain, single-quoted or double-quoted scalar of [A-Za-z0-9._+-] characters, then optional
# whitespace and an optional `#` comment.
PIN_LINE_RE = re.compile(
    r" *(?:- +)?python-version *: +"
    r"(?:(?P<plain>[A-Za-z0-9._+-]+)|'(?P<single>[A-Za-z0-9._+-]+)'|\"(?P<double>[A-Za-z0-9._+-]+)\")"
    r"(?:[ \t]+#.*|[ \t]*)")
# A top-level line of the workflow: a plain key, a colon, then nothing or a value.
TOP_RE = re.compile(r"(?P<key>[A-Za-z_][A-Za-z0-9_-]*) *:(?:[ \t].*)?")
# Characters a YAML reader may read as a line break that the "\n" split does not: a carriage return left
# inside a line, and the next-line, line-separator and paragraph-separator characters a YAML 1.1 reader also
# breaks on. The python-floor gate refuses the same set (LINE_BREAKS there), which the self-test holds.
LINE_BREAKS = {"\r": "a carriage return", "\x85": "U+0085 (next line)",
               "\u2028": "U+2028 (line separator)", "\u2029": "U+2029 (paragraph separator)"}
# A job id under jobs:, a plain key at indent 2, then nothing but an optional comment.
JOB_RE = re.compile(r"  (?P<id>[A-Za-z_][A-Za-z0-9_-]*) *:(?:[ \t]+#.*|[ \t]*)")
# One line of a host-patch job: a block mapping entry, optionally a "- " list item, whose value is nothing,
# a one-line quoted scalar (no inner quote, no backslash) or a plain scalar that opens no flow, block
# scalar, anchor, alias or tag, then an optional comment.
ENTRY_RE = re.compile(
    r"(?P<indent> *)(?P<dash>- +)?(?P<key>[A-Za-z_][A-Za-z0-9_-]*) *:"
    r"(?:[ \t]+(?:'(?P<single>[^']*)'|\"(?P<double>[^\"\\]*)\"|(?P<plain>[A-Za-z0-9_.$/~=+][^#]*?)))?"
    r"(?:[ \t]+#.*)?[ \t]*")
# A LITERAL (|) block-scalar opener in a job outside HOST_JOBS, with an optional chomping indicator and
# no indentation indicator (the body indent of one is set by the indicator, which this line model does
# not read, so it is cannot-evaluate through ENTRY_RE). The body lines, every later line more indented
# than the key, are kept, #-leading lines included (inside a literal block a # line is
# content, not a YAML comment; a BLANK body line is dropped by the line reader, as the shell ignores
# one), each refused like any other job line (_outside_line_refusals) and
# joined with newlines into the value (_outside_entries); _run_line_tokens then reads the value back
# LINE BY LINE: a newline is a command boundary for the shell, and shlex is a tokenizer, not a shell,
# so no claim rests on how a shell would join, expand or continue lines.
BLOCK_RE = re.compile(
    r"(?P<indent> *)(?P<dash>- +)?(?P<key>[A-Za-z_][A-Za-z0-9_-]*) *: "
    r"\|[+-]?(?:[ \t]+#.*)?[ \t]*")
# A FOLDED (>) block-scalar opener in a job outside HOST_JOBS: cannot-evaluate, never decoded. Folding
# joins body lines with single spaces (keeping more-indented lines literally), so the one text a YAML
# reader hands the shell is not any literal line this line model reads; refusing the style outright is
# the only claim this line model can prove (_outside_entries).
FOLDED_RE = re.compile(
    r"(?P<indent> *)(?P<dash>- +)?(?P<key>[A-Za-z_][A-Za-z0-9_-]*) *: "
    r">[+-]?(?:[ \t]+#.*)?[ \t]*")
# A one-line flow-sequence value in a job outside HOST_JOBS, accepted only when it can define no key
# and carry no alias or quote (no colon, brace, bracket, quote, backslash or opener character inside):
# a strategy matrix os list among them. It is read as one opaque scalar value; any other flow spelling
# is cannot-evaluate through ENTRY_RE.
FLOW_RE = re.compile(
    r"(?P<indent> *)(?P<dash>- +)?(?P<key>[A-Za-z_][A-Za-z0-9_-]*) *: "
    r"(?P<flow>\[[A-Za-z0-9_. ,/@=+-]*\])(?:[ \t]+#.*)?[ \t]*")
# A bare scalar list item in a job outside HOST_JOBS (a needs: member among them): a dash, one plain
# scalar with no colon, space, quote or opener character, then an optional comment.
BARE_RE = re.compile(r"(?P<indent> *)- +(?P<text>[A-Za-z0-9_./@=+-]+)(?:[ \t]+#.*)?[ \t]*")
# A plain (unquoted) scalar a YAML 1.1 or 1.2 reader resolves to a non-string: at a run: key of a job
# outside HOST_JOBS, each is refused by its named kind (_refuse_non_string_runs) rather than read as a
# string this gate invents. PLAIN_NULLS and PLAIN_BOOLS are the YAML 1.1 spellings (a superset of the
# 1.2 core set). NUMBER_PLAIN_RE is a CONSERVATIVE SPELLING CLASSIFIER, not a YAML parser: any plain
# scalar that begins with a digit, a sign or a dot and spells only digits, underscores, colons, dots,
# signs and the 0b/0o/0x letters in EITHER case (0X1F, 0O17 and +0X1F among them; 0B101 already sat
# inside the hex letters, and no reader's case folding is assumed) with hex digits (e/E among them)
# is refused as a number. That covers
# the whole YAML 1.1 numeric grammar (decimal, hex, octal, binary, underscore, sexagesimal and
# FRACTIONAL sexagesimal, float, exponent), the .inf/.nan words keeping their own alternation, and it
# also refuses spellings some readers keep as strings (1.2.3.4 among them): the over-refusal is the
# disclosed conservative side, and no committed run: command starts with such a spelling. The plain =
# scalar, which a YAML 1.1 reader resolves to the value key, not one string, is refused by name
# (_non_string_kind). TIMESTAMP_PLAIN_RE covers the date-led timestamp forms that carry a time part,
# the date and the time separated by T, t or ANY RUN OF SPACES AND TABS (the YAML 1.1 timestamp
# grammar's separators, so a tab-separated or mixed-whitespace spelling is a named timestamp too; a
# pure date spelling is digits and signs, already a number to the classifier above). The time
# fields are the YAML 1.1 ones (an hour of one or two digits, two-digit minutes and seconds, an
# optional fraction, an optional Z or signed zone, whitespace allowed before either), so a date-led
# value with any other suffix is a string. RESIDUAL: a spelling outside those fields that a reader
# extension may still resolve to a timestamp (one-digit minutes and seconds, 2026-10-14T6:3:5Z
# among them) is read as a string here. A quoted scalar is a string in every YAML reader and is
# never matched here.
PLAIN_NULLS = frozenset({"~", "null", "Null", "NULL"})
PLAIN_BOOLS = frozenset({"y", "Y", "n", "N", "yes", "Yes", "YES", "no", "No", "NO", "true", "True",
                         "TRUE", "false", "False", "FALSE", "on", "On", "ON", "off", "Off", "OFF"})
NUMBER_PLAIN_RE = re.compile(
    r"[-+.0-9][0-9_:.a-fA-FoxOX+-]*"
    r"|[-+]?\.(?:inf|Inf|INF)|[-+]?\.(?:nan|NaN|NAN)")
TIMESTAMP_PLAIN_RE = re.compile(
    r"[0-9]{4}-[0-9][0-9]?-[0-9][0-9]?(?:(?:[Tt]|[ \t]+)[0-9][0-9]?:[0-9][0-9]:[0-9][0-9]"
    r"(?:\.[0-9]*)?(?:[ \t]*(?:Z|[-+][0-9][0-9]?(?::[0-9][0-9])?))?)?")
# The deepest indentation a host-patch job line may have: a step input under with:.
MAX_INDENT = 10


class CannotEvaluate(Exception):
    """An input this gate cannot read or interpret; reported as exit 2, never as a pass."""


class _Scalar(str):
    """A scalar value of a job, with the number of the line it sits on, the spelling kind it was
    read from (plain, quoted, flow, bare or block) and that line as written (written, its indentation,
    any list dash and any trailing comment included): _refuse_non_string_runs reads the kind at run:
    keys, because only an unquoted spelling can resolve to a non-string in a YAML reader, and
    _shell_departure reads the written line of a shell: value."""
    line = 0
    kind = None
    written = ""


class _Mapping(dict):
    """A block mapping of a job, with the number of the line each of its keys sits on (lines)."""

    def __init__(self):
        super().__init__()
        self.lines = {}


def _read_text(path):
    """Read a regular, non-symlink UTF-8 file, or raise CannotEvaluate."""
    try:
        mode = os.lstat(path).st_mode
    except OSError as exc:
        raise CannotEvaluate("{}: cannot stat: {}".format(path, exc))
    if not stat.S_ISREG(mode):
        raise CannotEvaluate("{}: not a regular file".format(path))
    try:
        return Path(path).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CannotEvaluate("{}: cannot read as UTF-8: {}".format(path, exc))


def load_source(root):
    """Parse and schema-check the single source; return the host patch as (major, minor, micro)."""
    try:
        data = tomllib.loads(_read_text(root / SOURCE_REL))
    except tomllib.TOMLDecodeError as exc:
        raise CannotEvaluate("{}: does not parse: {}".format(SOURCE_REL, exc))
    # tomllib also raises a BARE ValueError on an integer literal past CPython's int-string limit, a
    # RecursionError on a deeply nested array or inline table or, in a release that bounds key depth, on a
    # key of more than 1000 dotted parts, and a MemoryError on a source too large for the memory left:
    # each is cannot-evaluate, never a traceback.
    except (ValueError, RecursionError, MemoryError) as exc:
        raise CannotEvaluate("{}: does not parse ({}): {}".format(SOURCE_REL, type(exc).__name__, exc))
    if set(data) != SOURCE_KEYS:
        raise CannotEvaluate("{}: keys {} (want exactly {})".format(
            SOURCE_REL, sorted(data), sorted(SOURCE_KEYS)))
    if type(data["format-version"]) is not int or data["format-version"] != 1:
        raise CannotEvaluate("{}: format-version must be the integer 1".format(SOURCE_REL))
    text = data["python-host-patch"]
    match = PATCH_RE.fullmatch(text) if isinstance(text, str) else None
    if match is None:
        raise CannotEvaluate("{}: python-host-patch must be a \"MAJOR.MINOR.PATCH\" string with at most four "
                             "digits in each component, got {!r:.60}".format(SOURCE_REL, text))
    # PATCH_RE bounds each component, so this conversion holds; a failure is still cannot-evaluate.
    try:
        return tuple(int(part) for part in match.groups())
    except ValueError as exc:
        raise CannotEvaluate("{}: python-host-patch {!r:.60} does not convert to integers: {}".format(
            SOURCE_REL, text, exc))


def _patch_text(patch):
    return "%d.%d.%d" % tuple(patch)


def _entry(job, number, line, where=None):
    """(line number, indentation, key column, list item, key, value) for one content line of a host-patch
    job or of a job naming the --jobs-only judge (where, defaulting to the host-patch wording, names the
    job in each refusal), or CannotEvaluate for a line ENTRY_RE does not read."""
    where = "the host-patch job " + job if where is None else where
    match = ENTRY_RE.fullmatch(line)
    plain = None if match is None else match.group("plain")
    if match is None or (plain is not None and (": " in plain or plain.endswith(":"))):
        raise CannotEvaluate("{}:{}: a line in {} that is not a block mapping entry with "
                             "a one-line scalar value (a block scalar, flow, anchor, alias or tag among "
                             "them); this job model cannot read it".format(WORKFLOW_REL, number, where))
    indent, dash = len(match.group("indent")), match.group("dash") or ""
    if indent > MAX_INDENT:
        raise CannotEvaluate("{}:{}: a line in {} nested deeper than a step input; this "
                             "job model cannot read it".format(WORKFLOW_REL, number, where))
    value = next((group for group in match.group("single", "double", "plain") if group is not None), None)
    if value is not None:
        value = _Scalar(value)
        value.line = number
        value.kind = "plain" if plain is not None else "quoted"
        value.written = line
    return number, indent, indent + len(dash), bool(dash), match.group("key"), value


def _job_tree(job, entries, where=None):
    """The block mapping of a host-patch job or of a job outside HOST_JOBS from its entries (_entry /
    _outside_entries), or CannotEvaluate for an entry the indentation does not place or a repeated key.
    A bare scalar list item (key None, _outside_entries) is a sequence member holding its scalar.
    MAX_INDENT bounds the nesting, so the recursion is shallow."""
    where = "the host-patch job " + job if where is None else where
    position = [0]

    def refuse(entry, why):
        raise CannotEvaluate("{}:{}: {} in {}; this job model cannot read it".format(
            WORKFLOW_REL, entry[0], why, where))

    def node(entry):
        if entry[5] is not None:
            return entry[5]
        if position[0] < len(entries) and entries[position[0]][1] > entry[2]:
            child = entries[position[0]]
            return sequence(child[1]) if child[3] else mapping(child[2], False)
        return None

    def mapping(column, item):
        # item: the first entry is the "- " line that opens this mapping as a list item.
        out = _Mapping()
        while position[0] < len(entries):
            entry = entries[position[0]]
            if not item and entry[1] < column:
                break
            if not item and (entry[3] or entry[2] != column):
                refuse(entry, "a line the indentation does not place")
            item = False
            position[0] += 1
            if entry[4] in out:
                refuse(entry, "a repeated key " + entry[4])
            out[entry[4]] = node(entry)
            out.lines[entry[4]] = entry[0]
        return out

    def sequence(indent):
        out = []
        while position[0] < len(entries):
            entry = entries[position[0]]
            if entry[1] < indent:
                break
            if not entry[3] or entry[1] != indent:
                refuse(entry, "a line the indentation does not place")
            if entry[4] is None:
                # A bare scalar list item (a needs: member among them): one sequence member.
                position[0] += 1
                out.append(entry[5])
                continue
            out.append(mapping(entry[2], True))
        return out

    return mapping(4, False)


def _count_key(node, key):
    """How many mapping entries named key the tree node holds, at any depth."""
    if isinstance(node, dict):
        return sum((name == key) + _count_key(value, key) for name, value in node.items())
    if isinstance(node, list):
        return sum(_count_key(item, key) for item in node)
    return 0


def _shell_departure(step):
    """The words of how a step's shell: departs from the exact line shell: bash (STEP_SHELL_LINE), or
    None when it is that line: a step with no shell: key, a shell: whose parsed value is anything other
    than the string bash (bash -e {0}, Bash, perl {0} or an empty value among them), and a shell: bash
    written on any other line once its indentation is removed (a quoted 'bash' or "bash", a trailing
    comment, trailing whitespace, a second space before the value or a - list-item line among them,
    each of which parses to bash) are each named in their own words. tools/check_ci_parity.py binds
    the same line but reads it once a trailing comment and trailing whitespace are removed, so this
    gate refuses those two spellings where that gate accepts them."""
    if "shell" not in step:
        return "carries no shell: (want shell: bash)"
    shell = step["shell"]
    written = getattr(shell, "written", "").lstrip(" ")
    if shell == STEP_SHELL and written != STEP_SHELL_LINE:
        return "writes shell: bash as {!r}, not as the exact line {}".format(written, STEP_SHELL_LINE)
    if shell != STEP_SHELL:
        return "carries shell: {!r}, not shell: bash".format(shell)
    return None


def _step_departures(step):
    """Each way a step departs from an executable step, in its own words (an empty list when it is one):
    an executable step carries exactly the keys RUN_KEYS (name, run and shell) with the line shell: bash,
    so a step with no shell:, another shell or shell: bash written on another line (_shell_departure), a
    key beyond RUN_KEYS (an if:,
    continue-on-error:, working-directory: or env: among them, any of which can skip, mask or change
    what it runs) and a missing name: are each named."""
    shell = _shell_departure(step)
    departures = [] if shell is None else [shell]
    departures.extend("carries {}:, which can skip, mask or change what it runs".format(key)
                      for key in sorted(set(step) - RUN_KEYS))
    if "name" not in step:
        departures.append("carries no name:")
    return departures


def _suite_shaped(step):
    """Whether a host-patch step is a suite step: its run: is one of SUITE_RUNS, or a token of one of its
    lines (shlex, POSIX) has an os.path.normpath base name in SUITE_RUNNERS (a respelled, renamed or
    other suite of a suite runner among them). A run: line shlex cannot tokenize counts as one, so it
    is held, never passed unread (_host_flag_findings refuses such a line first while the outside-judge
    rule runs). A step that runs a suite runner as a module (python3 -m
    tools.check_selftest_execution --suite git-fixture-env-core among them) has no token whose base
    name is a runner's file name, so it is not a suite step and adding one is not refused here; the
    HOST_SUITES steps themselves stay required."""
    run = step.get("run")
    if not isinstance(run, str):
        return False
    if run in SUITE_RUNS:
        return True
    for text in run.split("\n"):
        try:
            tokens = shlex.split(text)
        except ValueError:
            return True
        if any(os.path.basename(os.path.normpath(token)) in SUITE_RUNNERS for token in tokens):
            return True
    return False


def _judge_text(text):
    """Whether text names this gate's file name (GATE_BASENAME) or the --jobs-only flag literally: the
    scan of every outside job's scalar values, and the conservative literal-substring fallback of the
    run: classification (a run: naming either literally is refused even when its tokens resolve to
    neither). An escaped, aliased or tagged spelling names neither literally; _outside_line_refusals
    and the job model refuse such lines instead, and a spelling only shell quote removal or expansion
    builds is read from shlex tokens, never from this substring."""
    return GATE_BASENAME in text or JOBS_ONLY_FLAG in text


def _judge_shaped(tokens):
    """Whether any shlex token is JOBS_ONLY_FLAG or names a file whose os.path.normpath base name is
    GATE_BASENAME: the step runs this gate or names its flag in some spelling."""
    return any(token == JOBS_ONLY_FLAG or os.path.basename(os.path.normpath(token)) == GATE_BASENAME
               for token in tokens)


def _counts_as_judge(tokens):
    """Whether a step's shlex tokens are the judge command's, token for token: each token equals its
    JOBS_ONLY_TOKENS counterpart, the one path token (GATE_PATH) compared after os.path.normpath, so a
    doubled space, a ./tools/ path, quoted trailing whitespace, a tab before the value or a
    quote-spliced spelling still counts, and any other judge-shaped token list does not. A path token
    carrying a .. segment never counts: normpath is lexical, and through a symlinked directory a
    docs/../tools/ path can name another file."""
    return len(tokens) == len(JOBS_ONLY_TOKENS) and all(
        token == want or (want == GATE_PATH and ".." not in token.split("/")
                          and os.path.normpath(token) == GATE_PATH)
        for token, want in zip(tokens, JOBS_ONLY_TOKENS))


def _run_line_tokens(job, value, where=None):
    """The shlex (POSIX) tokens of each token-bearing LINE of a run: value, a newline a command
    boundary (a block scalar's body lines reach the shell as separate lines, and shlex is a tokenizer,
    not a shell: it resolves quotes, never operators, expansions or continuations), or CannotEvaluate
    for a value this command model cannot resolve: one carrying a ${{ }} expression on any line, a
    #-leading block-body line included (GitHub resolves it before the shell runs), or a line shlex
    refuses (an unclosed quote among them). where names the job, defaulting to the outside-the-host-
    patch-jobs wording; _host_flag_findings reads host-patch-job run: values with its own wording."""
    if where is None:
        where = "the job {}, outside the host-patch jobs".format(job)
    if "${{" in value:
        raise CannotEvaluate("{}:{}: a run: command of {} carries a ${{{{ }}}} expression; GitHub "
                             "resolves it before the shell runs, so this command model cannot read "
                             "what the step runs".format(WORKFLOW_REL, value.line, where))
    groups = []
    for text in value.split("\n"):
        try:
            tokens = shlex.split(text)
        except ValueError as exc:
            raise CannotEvaluate("{}:{}: a run: command of {} that shlex cannot tokenize ({}); this "
                                 "command model cannot read what the step runs".format(
                                     WORKFLOW_REL, value.line, where, exc))
        if tokens:
            groups.append(tokens)
    return groups


def _judge_values(node, runs):
    """Each (line number, value) scalar under node naming this gate's file name or the flag
    (_judge_text), every run: value already classified by tokens (runs, by identity) excluded: a
    matrix or include value, an env value, a with: input, a name: or a bare list item among them."""
    out = []
    if isinstance(node, dict):
        for value in node.values():
            out.extend(_judge_values(value, runs))
    elif isinstance(node, list):
        for item in node:
            out.extend(_judge_values(item, runs))
    elif isinstance(node, str) and id(node) not in runs and _judge_text(node):
        out.append((node.line, node))
    return out


def _outside_entries(job, raw, where):
    """The entry tuples of a job outside HOST_JOBS, _entry's shape, from its recorded content lines
    (raw: every line after the job's header, comment lines included; the line reader drops blank
    lines, a literal block's blank body lines among them, as the shell ignores them): ENTRY_RE
    entries as for the host-patch jobs, plus the three
    spellings that model refuses there: a LITERAL block-scalar opener (BLOCK_RE) whose body lines,
    each more indented than its key, are kept, a #-leading line included (inside a literal
    block it is content, not a YAML comment), each passed through the backslash and anchor/alias/tag
    refusals (_outside_line_refusals) and joined on the opener's line (_run_line_tokens reads the
    value back line by line); a colon-free one-line flow sequence (FLOW_RE), read as one opaque
    scalar; and a bare scalar list item (BARE_RE), read as a sequence member with no key. A FOLDED
    (>) opener (FOLDED_RE) is cannot-evaluate, never decoded: folding joins body lines with spaces,
    so the text a YAML reader hands the shell is not any literal line this model reads. A comment
    line outside a block body is inert (YAML drops it) and is skipped. Any other line, and any of
    the three spellings nested deeper than a step input (MAX_INDENT, the bound _entry holds for its
    own entries), is cannot-evaluate, never skipped."""
    def deep(number, indent):
        if indent > MAX_INDENT:
            raise CannotEvaluate("{}:{}: a line in {} nested deeper than a step input; this "
                                 "job model cannot read it".format(WORKFLOW_REL, number, where))
        return indent

    entries, index = [], 0
    while index < len(raw):
        number, line = raw[index]
        index += 1
        if line.strip(" \t").startswith("#"):
            continue
        match = FOLDED_RE.fullmatch(line)
        if match is not None:
            raise CannotEvaluate("{}:{}: a folded (>) block scalar in {}: folding joins body lines "
                                 "with spaces, so the text a YAML reader hands the shell is not any "
                                 "literal line; this job model cannot read it, and it never decodes "
                                 "one".format(WORKFLOW_REL, number, where))
        match = BLOCK_RE.fullmatch(line)
        if match is not None:
            indent, dash = deep(number, len(match.group("indent"))), match.group("dash") or ""
            column = indent + len(dash)
            body = []
            while index < len(raw):
                text = raw[index][1]
                if len(text) - len(text.lstrip(" ")) <= column:
                    break
                _outside_line_refusals(job, [raw[index]])
                body.append(text.strip(" \t"))
                index += 1
            value = _Scalar("\n".join(body))
            value.line = number
            value.kind = "block"
            value.written = line
            entries.append((number, indent, column, bool(dash), match.group("key"), value))
            continue
        match = FLOW_RE.fullmatch(line)
        if match is not None:
            indent, dash = deep(number, len(match.group("indent"))), match.group("dash") or ""
            value = _Scalar(match.group("flow"))
            value.line = number
            value.kind = "flow"
            value.written = line
            entries.append((number, indent, indent + len(dash), bool(dash), match.group("key"),
                            value))
            continue
        match = BARE_RE.fullmatch(line)
        if match is not None:
            indent = deep(number, len(match.group("indent")))
            value = _Scalar(match.group("text"))
            value.line = number
            value.kind = "bare"
            value.written = line
            entries.append((number, indent, indent, True, None, value))
            continue
        entries.append(_entry(job, number, line, where))
    return entries


def _non_string_kind(value):
    """The named kind of a run: value that is not one string, or None for a string: the REAL parsed
    shape (an empty value, which YAML reads as null; a nested mapping; a nested sequence), a flow
    sequence FLOW_RE read as one opaque scalar (YAML reads it as a sequence), or a plain (unquoted)
    scalar spelling a YAML 1.1 or 1.2 reader can resolve to a non-string (PLAIN_NULLS, PLAIN_BOOLS,
    the = value key, NUMBER_PLAIN_RE, a conservative spelling classifier, not a YAML parser, and
    TIMESTAMP_PLAIN_RE). A quoted or block-scalar value is a string in every YAML
    reader; a scalar with no recorded kind is held to the plain checks, the conservative side."""
    if value is None:
        return "an empty value, which YAML reads as null"
    if isinstance(value, dict):
        return "a nested mapping"
    if isinstance(value, list):
        return "a nested sequence"
    kind = getattr(value, "kind", None)
    if kind == "flow":
        return "a flow sequence, which YAML reads as a sequence, not one string"
    if kind in ("quoted", "block", "bare"):
        return None
    if value in PLAIN_NULLS:
        return "an explicit null (the plain scalar {})".format(value)
    if value in PLAIN_BOOLS:
        return "a boolean (the plain scalar {})".format(value)
    if value == "=":
        return "a YAML 1.1 value key (the plain scalar =)"
    if NUMBER_PLAIN_RE.fullmatch(value):
        return ("a number (the plain scalar {}; a conservative spelling classifier, not a YAML "
                "parser)".format(value))
    if TIMESTAMP_PLAIN_RE.fullmatch(value):
        return "a timestamp (the plain scalar {})".format(value)
    return None


def _refuse_non_string_runs(job, node):
    """Refuse a run: key of a job outside HOST_JOBS whose REAL parsed value is not one string, naming
    the recorded line of the key and the value's kind (_non_string_kind): every run: of every such
    job is tokenized (_run_line_tokens), so a run: this model cannot resolve to one string (an empty
    value, a nested mapping or sequence, a flow sequence, or a plain null, boolean, value-key (=),
    number or timestamp scalar among them) is cannot-evaluate, never skipped and never coerced to a
    string this
    gate invents; a single- or double-quoted scalar is a string in every YAML reader and stays
    accepted."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "run":
                kind = _non_string_kind(value)
                if kind is not None:
                    raise CannotEvaluate("{}:{}: a run: key of the job {} whose value is not one "
                                         "string ({}); every run: of a job outside the host-patch "
                                         "jobs is tokenized, so this command model cannot read what "
                                         "it runs".format(WORKFLOW_REL, node.lines[key], job, kind))
            _refuse_non_string_runs(job, value)
    elif isinstance(node, list):
        for item in node:
            _refuse_non_string_runs(job, item)


def _other_runs(node, runs):
    """Each (line number, value) string under a run: key of node that is not a step's own run: value
    (runs, by identity): a job-level run:, a with: input named run or a matrix value among them. Each
    is tokenized like a step's own run:, so a judge-shaped spelling there is read, never skipped; it
    can only ever be a finding (only a step's own run: may carry the judge)."""
    out = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "run" and isinstance(value, str) and id(value) not in runs:
                out.append((value.line, value))
            else:
                out.extend(_other_runs(value, runs))
    elif isinstance(node, list):
        for item in node:
            out.extend(_other_runs(item, runs))
    return out


def _refuse_upper_case_keys(job, tree):
    """Refuse a job or step key of a job outside HOST_JOBS that carries an upper-case letter (an Env:
    for env: among them): whether a workflow reader folds key case is not modelled, so this job model
    reads such a job only in the lower-case key spellings it holds. The keys of an env: or with: value
    mapping are data (an environment variable name among them), not job or step properties, and stay
    unjudged."""
    steps = tree.get("steps")
    for node in [tree] + [step for step in (steps if isinstance(steps, list) else ())
                          if isinstance(step, dict)]:
        for key in node:
            if key != key.lower():
                raise CannotEvaluate("{}:{}: the key {}: of the job {}, outside the host-patch jobs, "
                                     "carries an upper-case letter; whether a workflow reader folds "
                                     "key case is not modelled, so this job model cannot read "
                                     "it".format(WORKFLOW_REL, node.lines[key], key, job))


def _outside_line_refusals(job, lines):
    """Refuse a line of a job outside HOST_JOBS, its accepted header and its block-scalar body lines
    included, when it carries a backslash (a double-quoted YAML scalar resolves backslash escapes and
    joins escaped line breaks, so the text a YAML reader hands the shell can differ from every literal
    line) or opens a YAML anchor, alias or tag where a value may start (UNREAD_OPENER_RE: an alias can
    carry a value defined anywhere in the document, and a tag can rewrite one). Every outside job is
    read by literal lines and shlex tokens, so a line that can name a command without spelling it is
    cannot-evaluate, never skipped. A comment line outside a block-scalar body is inert (YAML drops
    it) and is not passed here; a #-leading line inside a literal block body is content, not a YAML
    comment, so _outside_entries passes every block body line, #-leading lines included, through this
    refusal."""
    for number, line in lines:
        if "\\" in line:
            raise CannotEvaluate("{}:{}: carries a backslash in the job {} (its header and "
                                 "block-scalar bodies included): a double-quoted YAML scalar resolves "
                                 "backslash escapes and joins escaped line breaks, so this gate's "
                                 "line model cannot read what the line "
                                 "names".format(WORKFLOW_REL, number, job))
        if UNREAD_OPENER_RE.search(line):
            raise CannotEvaluate("{}:{}: opens a YAML anchor, alias or tag in the job {}: the value it "
                                 "names can be defined or rewritten elsewhere in the document, so this "
                                 "gate's line model cannot read what the line "
                                 "names".format(WORKFLOW_REL, number, job))


def _host_job_findings(job, tree, want):
    """The findings of the JOB_RULES rules for one host-patch job, tree its block mapping."""
    where = "{}: the host-patch job {}".format(WORKFLOW_REL, job)
    found = []
    if "job-keys" in JOB_RULES:
        for key in tree:
            if key not in JOB_KEYS:
                found.append("{} carries {}:, which can skip, mask, move or change what it runs (a host-patch "
                             "job carries only name, runs-on and steps)".format(where, key))
    if "runner" in JOB_RULES and tree.get("runs-on") != RUNNER:
        found.append("{} runs on {!r}, not {!r}: the setup-python versions manifest lists the host patch's "
                     "Linux builds by Ubuntu version, so the job names its image".format(
                         where, tree.get("runs-on"), RUNNER))
    steps = tree.get("steps")
    if not isinstance(steps, list) or not steps:
        return found + ["{} carries no steps list".format(where)]
    if "checkout" in JOB_RULES:
        first = steps[0]
        if not (first.get("uses") == CHECKOUT_USES and set(first) == CHECKOUT_KEYS
                and first.get("with") == CHECKOUT_INPUTS):
            found.append("{}: its first step is not the checkout step as written (uses: {} with only {}, no "
                         "other key): another ref, repository or path can put another tree under the gate "
                         "and the suite".format(where, CHECKOUT_USES, ", ".join(
                             "{}: {}".format(key, value) for key, value in CHECKOUT_INPUTS.items())))
        others = [index for index, step in enumerate(steps) if index
                  and isinstance(step.get("uses"), str) and "checkout" in step["uses"].lower()]
        if others:
            found.append("{} carries another checkout step (step {}), which can put another tree under the "
                         "gate and the suite".format(where, others[0] + 1))
    if "step-env" in JOB_RULES:
        for index, step in enumerate(steps):
            if "env" in step:
                found.append("{} sets env: on step {}: a step env: changes what an otherwise accepted "
                             "run: command does (a BASH_ENV value, or a write to GITHUB_PATH or "
                             "GITHUB_ENV through it); no step of a host-patch job carries env:".format(
                                 where, index + 1))
    setups = [index for index, step in enumerate(steps)
              if isinstance(step.get("uses"), str) and "setup-python" in step["uses"].lower()]
    if "setup" in JOB_RULES:
        if len(setups) != 1:
            found.append("{} carries {} setup-python steps (want exactly one)".format(where, len(setups)))
        else:
            setup = steps[setups[0]]
            for key in setup:
                if key not in SETUP_KEYS:
                    found.append("{} sets {}: on its setup-python step, which can skip it or leave another "
                                 "interpreter in use".format(where, key))
            inputs = setup.get("with")
            if not isinstance(inputs, dict) or set(inputs) != {PIN_KEY}:
                found.append("{}: the with: of its setup-python step holds {} (want python-version alone)"
                             .format(where, sorted(inputs) if isinstance(inputs, dict) else inputs))
        pins = _count_key(tree, PIN_KEY)
        if pins != 1:
            found.append("{} carries {} python-version pins (want exactly one, the with: input of its "
                         "setup-python step; a job-level env: or another key never counts)".format(
                             where, pins))
    if len(setups) != 1:
        # The pin and the step order are judged from the one setup-python step.
        return found
    inputs = steps[setups[0]].get("with")
    value = inputs.get(PIN_KEY) if isinstance(inputs, dict) else None
    if "pin" in JOB_RULES and isinstance(value, str) and value != want:
        found.append("{}:{}: python-version {!r} in the host-patch job {} differs from the host patch {!r} "
                     "that {} names".format(WORKFLOW_REL, value.line, str(value), job, want, SOURCE_REL))
    after = setups[0] + 1
    gate = steps[after] if after < len(steps) else {}
    if "gate-step" in JOB_RULES and gate.get("run") != GATE_RUN:
        found.append("{}: the step right after its setup-python step is not the Python host pin step, an "
                     "executable step (exactly name, run and shell: bash) that runs {}".format(where, GATE_RUN))
    elif "gate-step" in JOB_RULES and _step_departures(gate):
        found.append("{}: the step right after its setup-python step is not the Python host pin step: it runs "
                     "{} but {} (an executable step carries exactly name, run and shell: bash)".format(
                         where, GATE_RUN, "; ".join(_step_departures(gate))))
    if "suite-step" in JOB_RULES:
        found.extend(_suite_findings(where, job, steps, after))
    return found


def _suite_findings(where, job, steps, after):
    """The suite-step rule for one host-patch job: its suite steps (_suite_shaped) run exactly
    HOST_SUITES[job], in that order, none omitted, repeated or added; each sits after the job's
    setup-python step (index after or later; the gate-step rule holds the Python host pin step at index
    after, so the suites follow it); and each is an executable step (_step_departures)."""
    want = HOST_SUITES[job]
    carried = [(index, step) for index, step in enumerate(steps) if _suite_shaped(step)]
    got = tuple(str(step["run"]) for index, step in carried)
    found = []
    if got != want:
        why = (["omits " + run for run in want if run not in got]
               + ["adds " + run for run in dict.fromkeys(got) if run not in want]
               + ["repeats " + run for run in want if got.count(run) > 1])
        found.append("{} does not run its suite steps as HOST_SUITES lists them (exactly {} suite step(s) "
                     "after its setup-python step, in this order: {}): it {}".format(
                         where, len(want), ", ".join(want), "; ".join(why) or "runs them in another order"))
    for index, step in carried:
        if index < after:
            found.append("{} does not run its suite {} after its setup-python step: step {} runs it "
                         "before".format(where, step["run"], index + 1))
        if _step_departures(step):
            found.append("{} does not run its suite {} as an executable step: step {} {} (an executable "
                         "step carries exactly name, run and shell: bash)".format(
                             where, step["run"], index + 1, "; ".join(_step_departures(step))))
    return found


def _names_flag_text(text):
    """Whether one line of a host-patch job's PARSED run: scalar (ENTRY_RE strips a trailing YAML
    comment from a one-line plain scalar, so a stem spelled only inside such a comment is
    YAML-dropped text, never command text) names JOBS_ONLY_STEM once every quote, backslash and
    dollar character is deleted: a bounded raw-text read of the quote splices the deletion models,
    the spellings whose quote removal leaves the stem's own characters in place (--jobs-o""nly and
    --jobs-o'n'ly among them), with no shell grammar, so any other text naming the stem, an echo
    argument among them, is refused too: the over-refusal is the disclosed conservative side. This
    read runs only on the lines the closed safe-character grammar admits; a line carrying any
    character outside that grammar (a dollar sign, a brace, a backquote, a glob character among
    them) is cannot-evaluate upstream instead (_host_flag_findings, _refused_run_char: --jobs-o$'n'ly
    carries a dollar sign and is refused there, this deletion its red-on-removal fallback). A bare
    dollar splice whose VALUE, not its spelling, completes the stem (--jobs-$X among them) also
    carries a dollar sign and is refused there, not left to tools/check_ci_parity.py."""
    for char in "\"'\\$":
        text = text.replace(char, "")
    return JOBS_ONLY_STEM in text


def _refused_run_char(text):
    """The name of the first character of text outside the closed safe set a host-patch job's run:
    line is held to (HOST_RUN_SAFE), or None when every character is in it: HOST_RUN_CHAR_NAMES
    names the shell-active characters, and any other character is named by its repr."""
    for char in text:
        if char not in HOST_RUN_SAFE:
            return HOST_RUN_CHAR_NAMES.get(char, "the character {!r}".format(char))
    return None


def _leading_assignment(text):
    """The first word of a host-patch job's run: line, once any leading HOST_RUN_PREFIX_WORDS are
    skipped, when it is a shell assignment (HOST_RUN_ASSIGNMENT_RE: NAME=value or NAME+=value, a
    PATH= prefix among them), or None. The words are the raw text split on spaces and tabs: the line
    already holds only HOST_RUN_SAFE characters, and an assignment's name is unquoted, so a quoted
    word earlier on the line is never a prefix word and a real assignment prefix is never missed."""
    for word in text.split():
        if word not in HOST_RUN_PREFIX_WORDS:
            return word if HOST_RUN_ASSIGNMENT_RE.match(word) else None
    return None


def _host_flag_findings(job, tree):
    """The outside-judge rule's raw-text read of a HOST_JOBS job's run: values: each PARSED run:
    scalar (ENTRY_RE strips a trailing YAML comment from a one-line plain scalar, so a stem spelled
    only there is YAML-dropped text, never command text) is read line by line, and a line naming
    JOBS_ONLY_STEM once every quote, backslash and dollar character is deleted (_names_flag_text) is
    a finding: the jobs leg's outside run never sits inside a host-patch job it judges. The read is
    raw scalar text, never shlex token equality (which a semicolon or another operator joined to the
    flag token defeats), and deliberately wider than the flag: a host job naming the stem in any
    run: text, an echo argument among them, is refused (the disclosed over-refusal). The literal
    line scan (host_judges in job_findings) keeps covering every non-comment line, a name: or an env
    value among them. Every run: line is still tokenized (_run_line_tokens, with this job's own
    wording) for its refusals alone, so a host-patch run: line carrying a ${{ }} expression (which
    GitHub resolves before the shell runs and can spell the flag in parts no raw text names) or one
    shlex cannot tokenize is cannot-evaluate (exit 2), a disclosed refusal path of the host-patch
    jobs, never a silent pass; and a line carrying any character outside the closed safe set it is
    held to (_refused_run_char, HOST_RUN_SAFE: a dollar sign, a backquote, a brace, a bracket, a
    glob character, a redirection, a parenthesis and the other shell-active characters) is
    cannot-evaluate the same way, the character named, rather than a reading invented for it, and
    so is a line whose first word is a shell assignment (_leading_assignment: a PATH= prefix
    re-points which file the named command runs), the assignment named. This
    read runs only after the host-job line model has accepted every line as a one-line scalar entry
    (ENTRY_RE): a host-patch job's run: | block scalar is cannot-evaluate at _entry, BEFORE this flag
    read, so a block body never reaches here (an OUTSIDE job's literal block, by contrast, keeps its
    #-leading body lines and is read by _outside_entries). A host-patch job's own gate and suite
    steps name this gate's path without the flag and carry only safe characters, so no committed
    line is selected or refused."""
    found = []
    for number, value in _other_runs(tree, set()):
        _run_line_tokens(job, value, where="the host-patch job " + job)
        for text in value.split("\n"):
            named = _refused_run_char(text)
            if named is not None:
                raise CannotEvaluate(
                    "{}:{}: a run: line of the host-patch job {} carries {}, a character outside the "
                    "closed safe set a host-patch run: line is held to; this line cannot-evaluate "
                    "there, never a silent pass".format(WORKFLOW_REL, number, job, named))
            assigned = _leading_assignment(text)
            if assigned is not None:
                raise CannotEvaluate(
                    "{}:{}: a run: line of the host-patch job {} begins with the shell assignment "
                    "{}, which changes the environment of the command it prefixes (a PATH= prefix "
                    "re-points which file that command runs); this line cannot-evaluate there, "
                    "never a silent pass".format(WORKFLOW_REL, number, job, assigned))
        if any(_names_flag_text(text) for text in value.split("\n")):
            found.append("{}:{}: a run: command of the host-patch job {} names {} once every quote, "
                         "backslash and dollar character is deleted from its raw text (a "
                         "quote-spliced or operator-adjacent spelling among them): the jobs leg's "
                         "outside run never sits inside a host-patch job it judges".format(
                             WORKFLOW_REL, number, job, JOBS_ONLY_STEM))
    return found


def _judge_findings(host_judges, other_lines, other_raw, headers):
    """The outside-judge rule over the whole workflow: host_judges the (job, line number) of each
    non-comment HOST_JOBS line naming JOBS_ONLY_FLAG, other_lines the non-comment (line number, line) of
    each job outside HOST_JOBS, other_raw every line of each such job, its accepted header and comment
    lines included, headers each such job's accepted header (line number, line). EVERY job outside
    HOST_JOBS is read whole, never selected or skipped by literal text: its lines are refused for
    control characters (_refuse_control_chars, the header included) and for a backslash or a YAML
    anchor, alias or tag opener (_outside_line_refusals, the header and block-scalar bodies included),
    then read with the host-patch jobs' own line model extended by literal block scalars (every
    non-blank body line kept and refused, a #-leading line included, a blank body line dropped by
    the line reader, as the shell ignores one; a folded opener cannot-evaluate, never
    decoded), colon-free flow sequences and bare list items (_outside_entries / _job_tree); a line
    that extended model cannot read, a job or step key carrying an upper-case letter
    (_refuse_upper_case_keys), a run: whose real parsed value is not one string
    (_refuse_non_string_runs, named by its recorded key line and kind) and a run: with a line shlex
    cannot tokenize or carrying a ${{ }} expression (_run_line_tokens) are each cannot-evaluate,
    never skipped. EVERY line of every run: value is tokenized separately: a step's own run: counts
    as the judge when exactly one of its lines carries any token and that line's tokens are the
    judge command's (_counts_as_judge), and is otherwise a finding when any line is judge-shaped
    (_judge_shaped) or when its text, #-leading block-body lines included,
    names this gate's file name or the flag literally (_judge_text, the conservative literal-substring
    fallback); a run: that is not a step's own (a job-level run: or a with: input named run among them)
    whose tokens are judge-shaped or the judge's or whose text names either literal (_other_runs), and
    any other scalar value naming either literal (_judge_values), are each a finding: only a step's
    own run: may carry the judge. Exactly one step outside the host-patch jobs runs JOBS_ONLY_RUN; that
    step carries exactly name, run and the line shell: bash (a missing shell:, a shell: other than bash,
    a shell: bash written on another line and a missing name: each named); no non-comment
    host-patch-job line names the flag; and no job
    that carries a counted or judge-shaped step, nor any step of such a job, carries env: (in CI a
    job-level env: such as a BASH_ENV value can silence every step of the carrying job, an env: on the
    judge step the judge itself, and an env: on an earlier step every later step of the job, through a
    GITHUB_ENV write; an env: on a step after the judge does not reach it and is refused as policy, so
    the judge's job stays env-free), each env: and judge-step-key diagnostic naming the recorded line
    of its key and whether the carrying step is the counted judge or an unresolved judge-shaped
    step."""
    found = []
    for job, number in host_judges:
        found.append("{}:{}: names {} inside the host-patch job {}: the jobs leg's outside run never "
                     "sits inside a host-patch job it judges".format(
                         WORKFLOW_REL, number, JOBS_ONLY_FLAG, job))
    judges, shaped, trees = [], [], {}
    for job, header in headers.items():
        lines = other_lines.get(job, [])
        where = "the job {}, outside the host-patch jobs".format(job)
        for number, line in other_raw[job]:
            _refuse_control_chars(job, number, line, where)
        _outside_line_refusals(job, [header] + lines)
        # _outside_entries reads other_raw (the header sliced off): a #-leading line inside a literal
        # block body is content and must reach the model; a comment line outside one stays inert there.
        tree = _job_tree(job, _outside_entries(job, other_raw[job][1:], where), where)
        trees[job] = tree
        _refuse_upper_case_keys(job, tree)
        _refuse_non_string_runs(job, tree)
        runs = set()
        steps = tree.get("steps")
        for index, step in enumerate(steps if isinstance(steps, list) else ()):
            if not isinstance(step, dict) or "run" not in step:
                continue
            value = step["run"]
            runs.add(id(value))
            groups = _run_line_tokens(job, value)
            if len(groups) == 1 and _counts_as_judge(groups[0]):
                judges.append((job, index))
            elif any(_judge_shaped(tokens) for tokens in groups) or _judge_text(value):
                shaped.append((job, index))
                found.append("{}:{}: an unresolved judge-shaped command in the job {}: a line of this "
                             "step's own run: carries a token that is {} or a path whose normpath "
                             "base name is this gate's file name, or its text names one of them "
                             "literally, but no single command line of it has the judge command's "
                             "tokens ({}, one line, token for token); a judge-shaped step either "
                             "counts as the one judge or is a finding, never a silent pass".format(
                                 WORKFLOW_REL, value.line, job, JOBS_ONLY_FLAG, JOBS_ONLY_RUN))
        for number, value in _other_runs(tree, runs):
            runs.add(id(value))
            groups = _run_line_tokens(job, value)
            if (any(_counts_as_judge(tokens) or _judge_shaped(tokens) for tokens in groups)
                    or _judge_text(value)):
                found.append("{}:{}: the job {} names this gate's file name or {} in a value that is "
                             "not a step's own run: command (a matrix or include value, an env value, "
                             "a with: input, a job-level run: or a name: among them); only the judge "
                             "step's own run: may carry it".format(
                                 WORKFLOW_REL, number, job, JOBS_ONLY_FLAG))
        for number, value in _judge_values(tree, runs):
            found.append("{}:{}: the job {} names this gate's file name or {} in a value that is not a "
                         "step's own run: command (a matrix or include value, an env value, a with: "
                         "input, a job-level run: or a name: among them); only the judge step's own "
                         "run: may carry it".format(WORKFLOW_REL, number, job, JOBS_ONLY_FLAG))
    if len(judges) != 1:
        found.append("{}: {} steps outside the host-patch jobs run {} as their own run: command (want "
                     "exactly one: the one CI run of the jobs leg that an edit inside the host-patch "
                     "jobs cannot silence)".format(WORKFLOW_REL, len(judges), JOBS_ONLY_RUN))
    counted = {}
    for job, index in judges:
        counted.setdefault(job, set()).add(index)
    carriers = {}
    for job, index in judges + shaped:
        carriers.setdefault(job, []).append(index)
    for job, indexes in carriers.items():
        tree = trees[job]
        job_step_kind = ("the --jobs-only judge step" if counted.get(job)
                         else "an unresolved judge-shaped step")
        job_kind = ("the --jobs-only judge" if counted.get(job) else "an unresolved judge-shaped step")
        if "env" in tree:
            found.append("{}:{}: the job {} carries {} and sets a job-level env:, which in CI can "
                         "silence every step of that job, the carrying step included".format(
                             WORKFLOW_REL, tree.lines["env"], job, job_step_kind))
        for position, step in enumerate(tree["steps"]):
            if not isinstance(step, dict):
                continue
            if position in indexes:
                step_kind = ("the --jobs-only judge" if position in counted.get(job, set())
                             else "an unresolved judge-shaped command")
                for key in sorted(set(step) - RUN_KEYS - {"env"}):
                    found.append("{}:{}: the step that carries {} carries {}:, which can skip, mask "
                                 "or change what it runs (the judge step carries exactly name, run and "
                                 "shell: bash)".format(WORKFLOW_REL, step.lines[key], step_kind, key))
                shell = _shell_departure(step)
                if shell is not None:
                    found.append("{}:{}: the step that carries {} {}: tools/check_ci_parity.py binds "
                                 "shell: bash on every run: step, and the judge step carries exactly that "
                                 "line".format(WORKFLOW_REL, step.lines.get("shell", step.lines.get("run")),
                                                step_kind, shell))
                if "name" not in step:
                    found.append("{}:{}: the step that carries {} carries no name: (the judge step carries "
                                 "exactly name, run and shell: bash)".format(
                                     WORKFLOW_REL, step.lines.get("run"), step_kind))
            if "env" not in step:
                continue
            number = step.lines["env"]
            if position in indexes:
                found.append("{}:{}: the step that carries {} sets env:, which in CI can change or "
                             "silence what it runs (a BASH_ENV value among them)".format(
                                 WORKFLOW_REL, number, step_kind))
            elif position < max(indexes):
                found.append("{}:{}: the job {} carries {} and sets env: on step {}: a step env: "
                             "changes what that step's run: command does, and a write to GITHUB_ENV "
                             "or GITHUB_PATH through it reaches every later step of the job, the "
                             "judge step included".format(WORKFLOW_REL, number, job, job_kind,
                                                          position + 1))
            else:
                found.append("{}:{}: the job {} carries {} and sets env: on step {}, after the judge "
                             "step: that env: does not reach the judge, and this gate refuses env: on "
                             "every step of the judge's job as policy".format(
                                 WORKFLOW_REL, number, job, job_kind, position + 1))
    return found


def _refuse_control_chars(job, number, line, where=None):
    """Refuse a form feed or a vertical tab (CONTROL_CHARS) in a line of a host-patch job (where None)
    or of a job outside them (where naming it), the job's accepted header line included: neither is a
    YAML line break, so this line model reads the line whole, and the refusal holds every such line out
    rather than modelling what another reader does with it."""
    where = "the host-patch job " + job if where is None else where
    for char, label in CONTROL_CHARS.items():
        if char in line:
            raise CannotEvaluate("{}:{}: carries {} in {}; this line model does "
                                 "not accept it".format(WORKFLOW_REL, number, label, where))


def job_findings(root, patch):
    """The top level of WORKFLOW_REL and each HOST_JOBS job in it are whole (JOB_RULES), and each such job
    pins the host patch."""
    want = _patch_text(patch)
    entries = {job: [] for job in HOST_JOBS}
    host_judges, other_lines, other_raw, headers = [], {}, {}, {}
    seen, tops, in_jobs, job = set(), {}, False, None
    for number, line in enumerate(_read_text(root / WORKFLOW_REL).split("\n"), 1):
        line = line[:-1] if line.endswith("\r") else line
        for char, label in LINE_BREAKS.items():
            if char in line:
                raise CannotEvaluate("{}:{}: carries {}, which a YAML reader may read as a line break; this "
                                     "line model splits only on newline and cannot read it".format(
                                         WORKFLOW_REL, number, label))
        stripped = line.strip(" \t")
        if not stripped:
            continue
        comment = stripped.startswith("#")
        if not comment and not line.startswith((" ", "\t")):
            top = TOP_RE.fullmatch(line)
            if top is None:
                raise CannotEvaluate("{}:{}: a top-level line that is not a plain key (a quoted or explicit "
                                     "key, a flow mapping or a document marker among them); this line model "
                                     "cannot read it".format(WORKFLOW_REL, number))
            key = top.group("key")
            if key in tops:
                raise CannotEvaluate("{}:{}: the top-level key {} repeats line {}; this line model cannot "
                                     "tell which YAML keeps".format(WORKFLOW_REL, number, key, tops[key]))
            tops[key] = number
            in_jobs, job = line.split("#", 1)[0].rstrip(" \t") == "jobs:", None
            continue
        if in_jobs and not comment:
            header = JOB_RE.fullmatch(line)
            if header is not None:
                job = header.group("id")
                if job in seen:
                    raise CannotEvaluate("{}:{}: the job id {} repeats; this line model cannot tell which "
                                         "job YAML keeps".format(WORKFLOW_REL, number, job))
                seen.add(job)
                if job in entries:
                    _refuse_control_chars(job, number, line)
                else:
                    other_raw.setdefault(job, []).append((number, line))
                    headers[job] = (number, line)
                continue
            if not line.startswith("    "):
                raise CannotEvaluate("{}:{}: a line under jobs: that is neither a job id at indent 2 nor job "
                                     "content at indent 4 or deeper; this line model cannot place it".format(
                                         WORKFLOW_REL, number))
        if job not in entries:
            if job is not None:
                other_raw.setdefault(job, []).append((number, line))
                if not comment:
                    other_lines.setdefault(job, []).append((number, line))
            continue
        _refuse_control_chars(job, number, line)
        if PIN_KEY in line.lower() and (comment or PIN_LINE_RE.fullmatch(line) is None):
            raise CannotEvaluate("{}:{}: names python-version in the host-patch job {} but is not one strict "
                                 "pin line; write it as python-version: '{}'".format(
                                     WORKFLOW_REL, number, job, want))
        if not comment:
            if JOBS_ONLY_FLAG in line:
                host_judges.append((job, number))
            entries[job].append(_entry(job, number, line))
    findings = []
    if "workflow-keys" in JOB_RULES:
        for key, number in tops.items():
            if key not in TOP_KEYS:
                findings.append("{}:{}: carries the top-level key {}:, which can change how every run step "
                                "of every job executes (a defaults: or env: block among them); the workflow "
                                "carries only {}".format(WORKFLOW_REL, number, key, ", ".join(TOP_KEYS)))
    if "outside-judge" in JOB_RULES:
        findings.extend(_judge_findings(host_judges, other_lines, other_raw, headers))
    for name in HOST_JOBS:
        if name not in seen:
            findings.append("{}: carries no job {} (a host-patch job, HOST_JOBS in "
                            "tools/check_python_host_pin.py)".format(WORKFLOW_REL, name))
            continue
        tree = _job_tree(name, entries[name])
        if "outside-judge" in JOB_RULES:
            findings.extend(_host_flag_findings(name, tree))
        findings.extend(_host_job_findings(name, tree, want))
    return findings


def interpreter_findings(patch, version):
    """The running interpreter, version = (major, minor, micro, releaselevel), is the host patch, final."""
    if tuple(version[:3]) == tuple(patch) and version[3] == "final":
        return []
    return ["the running interpreter is Python {}.{}.{} ({}), but {} names the host patch {}: a passing "
            "local suite does not predict the host-patch CI jobs; run on Python {}, or, when the host "
            "interpreter has changed, update {} and the host-patch pins in {} together".format(
                *version[:4], SOURCE_REL, _patch_text(patch), _patch_text(patch), SOURCE_REL, WORKFLOW_REL)]


def evaluate(root, version, jobs_only=False):
    """Run every leg over root, judging the interpreter version; return (exit code, report lines).
    jobs_only (--jobs-only) runs the source and jobs legs and skips the interpreter leg: the CI step
    outside the host-patch jobs runs on the floor pin, not the host patch, so the interpreter leg
    cannot run there."""
    try:
        patch = load_source(root)
        findings = []
        findings.extend(job_findings(root, patch))
        if not jobs_only:
            findings.extend(interpreter_findings(patch, version))
    except CannotEvaluate as exc:
        return 2, ["CANNOT EVALUATE: {}".format(exc)]
    if findings:
        return 1, ["FAIL: " + finding for finding in findings]
    report = ("PASS: python host patch {} ({}): {} carries only the top-level keys {}, its host-patch jobs "
              "{} each run on {}, carry env: on no job key or step, check out as written, pin it in their "
              "one setup-python step, run this gate right after it and run their suites in order, each "
              "held step with exactly name, run and shell: bash, every run: of "
              "every job outside them is tokenized line by line, and one step outside them carries the jobs leg's "
              "--jobs-only judge as its own run: command with exactly name, run and shell: bash, its job and "
              "every step of that job free of env:".format(
                  _patch_text(patch), SOURCE_REL, WORKFLOW_REL, ", ".join(TOP_KEYS), ", ".join(HOST_JOBS),
                  RUNNER))
    if jobs_only:
        return 0, [report + " (--jobs-only: the interpreter leg did not run)"]
    return 0, [report + ", and this interpreter is Python {}".format(_patch_text(version[:3]))]


# ---------------------------------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------------------------------

FAILURES = []
EXECUTED = []
# Red-on-revert: each finding leg's call in evaluate(), removed in a copy of this gate.
REVERT_CALLS = (("jobs", "job_findings(root, patch)"),
                ("interpreter", "interpreter_findings(patch, version)"))
HOST = (3, 14, 4, "final")
SOURCE_TEXT = "format-version = 1\npython-host-patch = \"3.14.4\"\n"
OPF, GFE = HOST_JOBS
PIN = "python-version: '3.14.4'"
FLOOR_JOB = ("  floor:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n"
             "      - uses: actions/setup-python@v5\n        with:\n          python-version: '3.14'\n"
             "      - run: python3 -I -B tools/x.py\n")
SETUP_STEP = "      - uses: actions/setup-python@v5\n        with:\n          " + PIN + "\n\n"
# Each held step carries exactly name, run and shell: bash, as quality.yml writes it.
SHELL_LINE = "        shell: bash\n"
GATE_STEP = "      - name: Python host pin\n" + SHELL_LINE + "        run: " + GATE_RUN + "\n\n"
JUDGE_STEP = ("      - name: Python host pin (jobs leg, outside the host-patch jobs)\n" + SHELL_LINE
              + "        run: " + JOBS_ONLY_RUN + "\n")
REGISTRY_JOB = ("  registry:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n"
                "      - uses: actions/setup-python@v5\n        with:\n          python-version: '3.14'\n"
                + JUDGE_STEP)
HEAD = "name: Quality\n\non: push\n\npermissions:\n  contents: read\n\njobs:\n"


def check(name, got, want):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


class _FakeSys:
    """A stand-in for the sys module main() reads: a chosen version_info and swallowed output streams."""

    def __init__(self, version):
        self.version_info = version
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()


def _write(root, rel, data):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def _copy(base, name, source):
    """A module named name, loaded through importlib from source written under base."""
    _write(base, name + ".py", source)
    spec = importlib.util.spec_from_file_location(name, base / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mutant(base, name, source, edits):
    """A copy of this gate with each (anchor, replacement) applied once, loaded through importlib: a
    targeted regression of one property, which its vector must hold red; an anchor that does not occur
    exactly once is cannot-evaluate."""
    for anchor, replacement in edits:
        if source.count(anchor) != 1:
            raise CannotEvaluate("mutant anchor {!r} occurs {} times".format(anchor, source.count(anchor)))
        source = source.replace(anchor, replacement)
    return _copy(base, name, source)


def _suite_step(job):
    """The suite steps of a host-patch job, one executable step per HOST_SUITES run, in order: the first
    named Suite and each later one Suite 2, Suite 3 and so on, so an edit anchored on one name line
    meets exactly one step."""
    return "".join("      - name: Suite{}\n".format(" {}".format(number) if number > 1 else "") + SHELL_LINE
                   + "        run: " + run + "\n" for number, run in enumerate(HOST_SUITES[job], 1))


def _committed_host_runs():
    """The text of each run: line of the HOST_JOBS jobs of the committed WORKFLOW_REL (ROOT), in file
    order, each read with the host-job line model (_entry): the committed-line acceptance controls
    read these, not module constants."""
    runs, job = [], None
    for number, line in enumerate(_read_text(ROOT / WORKFLOW_REL).split("\n"), 1):
        line = line[:-1] if line.endswith("\r") else line
        stripped = line.strip(" \t")
        if not stripped or stripped.startswith("#"):
            continue
        header = JOB_RE.fullmatch(line)
        if header is not None or not line.startswith(" "):
            job = header.group("id") if header is not None else None
            continue
        if job in HOST_JOBS:
            entry = _entry(job, number, line)
            if entry[4] == "run":
                runs.append(str(entry[5]))
    return runs


def _workflow_lanes(text):
    """The git-fixture-env lanes of a workflow text, read by command tokens: (the command of each lane
    in a job outside HOST_JOBS, its shlex tokens joined by single spaces, in file order; for each lane,
    the non-comment lines of its job, the job's header excluded, that name python-version). Each line
    has one trailing carriage return removed, and each pin line returned then has its leading and
    trailing spaces and tabs removed, so a lane's job is read for its pin rather than assumed to pin
    '3.14'. A line carrying a line-break character (LINE_BREAKS) or a form feed or vertical tab
    (CONTROL_CHARS), a repeated job id and a line under jobs: that is neither a job id at indent 2 nor
    content at indent 4 or deeper are CannotEvaluate. Each job outside HOST_JOBS is read whole with the
    jobs leg's extended job model (_outside_line_refusals, _outside_entries, _job_tree,
    _refuse_upper_case_keys), and EVERY line of every run: value in it (a step's own run:, a job-level
    run: and a with: input named run among them, a literal block's body lines included) is tokenized
    separately with shlex (POSIX); a line shlex cannot tokenize is CannotEvaluate. A line is a lane
    candidate when any of its tokens contains --suite or the stem of the suite runner HOST_SUITES runs
    in git-fixture-env-host-patch (check_selftest_execution: its file path, its module form
    tools.check_selftest_execution and a command string handed to another shell among them). A
    candidate is kept as a lane only when its tokens are exactly that runner's command (python3 -I -B
    tools/check_selftest_execution.py), --suite and a literal suite name containing git-fixture-env (the
    runner's suite id grammar: a letter or digit, then letters, digits, '.', '_' and '-'); it is
    accepted as no lane only when they are exactly that command, --suite and a literal suite name not
    containing git-fixture-env, or that command and --self-test; any other candidate is CannotEvaluate
    (a dollar sign, a ${{ }} expression, the module form, another shell, an operator, an assignment or
    an extra token among them), never read as no lane. A quote splice that shlex quote removal resolves
    (--su""ite git-fixture-env-extra or --suite 'git-fixture'-env-extra among them) is read by its
    resolved tokens. Every other scalar value of such a job (an env, matrix or with: value, or a name:,
    among them) whose text or shlex tokens contain --suite or the runner's stem is CannotEvaluate too:
    this reader does not follow a value into the command that uses it. NOT read here: a lane none of
    whose run: tokens and values contain --suite or the runner's stem (its runner and flag both
    supplied by a ${{ }} expression, which the jobs leg refuses in any run: outside HOST_JOBS, or by
    variables whose values spell neither name whole, a wrapper script, or a command string handed to
    another shell that splits both names with quotes, among them)."""
    runner = shlex.split(HOST_SUITES[GFE][0])[:-2]
    stems = {os.path.basename(token)[:-len(".py")] for run in HOST_SUITES[GFE] for token in shlex.split(run)
             if token.endswith(".py")}
    suite_name = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

    def candidate(words):
        return any("--suite" in word or any(stem in word for stem in stems) for word in words)

    def scalars(node):
        if isinstance(node, dict):
            for item in node.values():
                yield from scalars(item)
        elif isinstance(node, list):
            for item in node:
                yield from scalars(item)
        elif isinstance(node, str):
            yield node

    jobs, in_jobs, job = {}, False, None
    for number, line in enumerate(text.split("\n"), 1):
        line = line[:-1] if line.endswith("\r") else line
        for char, label in {**LINE_BREAKS, **CONTROL_CHARS}.items():
            if char in line:
                raise CannotEvaluate("{}:{}: carries {}; this lane reader splits only on newline and "
                                     "cannot read it".format(WORKFLOW_REL, number, label))
        stripped = line.strip(" \t")
        if not stripped:
            continue
        comment = stripped.startswith("#")
        if not comment and not line.startswith((" ", "\t")):
            in_jobs, job = line.split("#", 1)[0].rstrip(" \t") == "jobs:", None
            continue
        if in_jobs and not comment:
            header = JOB_RE.fullmatch(line)
            if header is not None:
                job = header.group("id")
                if job in jobs:
                    raise CannotEvaluate("{}:{}: the job id {} repeats; this lane reader cannot tell which "
                                         "job YAML keeps".format(WORKFLOW_REL, number, job))
                jobs[job] = [(number, line)]
                continue
            if not line.startswith("    "):
                raise CannotEvaluate("{}:{}: a line under jobs: that is neither a job id at indent 2 nor job "
                                     "content at indent 4 or deeper; this lane reader cannot place it".format(
                                         WORKFLOW_REL, number))
        if job is not None:
            jobs[job].append((number, line))
    lanes, pins = [], []
    for job, lines in jobs.items():
        if job in HOST_JOBS:
            continue
        where = "the job {}, outside the host-patch jobs".format(job)
        content = [(number, line) for number, line in lines if not line.strip(" \t").startswith("#")]
        _outside_line_refusals(job, content)
        tree = _job_tree(job, _outside_entries(job, lines[1:], where), where)
        _refuse_upper_case_keys(job, tree)
        pinned = tuple(line.strip(" \t") for number, line in content[1:] if PIN_KEY in line)
        runs = set()
        for number, value in _other_runs(tree, set()):
            runs.add(id(value))
            for line_text in value.split("\n"):
                try:
                    words = shlex.split(line_text)
                except ValueError as exc:
                    raise CannotEvaluate("{}:{}: a run: line of the job {} that shlex cannot tokenize ({}); "
                                         "this lane reader cannot read it".format(
                                             WORKFLOW_REL, number, job, exc))
                if not candidate(words):
                    continue
                if words[:-2] == runner and words[-2] == "--suite" and suite_name.fullmatch(words[-1]):
                    if "git-fixture-env" in words[-1]:
                        lanes.append(" ".join(words))
                        pins.append(pinned)
                elif words != runner + ["--self-test"]:
                    raise CannotEvaluate(
                        "{}:{}: a run: line of the job {} has a token naming --suite or the suite runner "
                        "{}, but its tokens are not exactly {} --suite <a literal suite name> or {} "
                        "--self-test (a dollar sign, a ${{{{ }}}} expression, the module form, another shell "
                        "or an extra token among them); this lane reader cannot read which suite it "
                        "runs".format(WORKFLOW_REL, number, job, " or ".join(sorted(stems)),
                                      " ".join(runner), " ".join(runner)))
        for value in scalars(tree):
            try:
                words = shlex.split(value)
            except ValueError:
                words = []
            if id(value) not in runs and candidate([value] + words):
                raise CannotEvaluate(
                    "{}:{}: a value of the job {} that is not a run: command (an env, matrix or with: value, "
                    "or a name:, among them) names --suite or the suite runner {}; a value reaches a command "
                    "only through an expansion or an action input, which this lane reader does not follow, "
                    "so this lane reader cannot read which suite it runs".format(
                        WORKFLOW_REL, value.line, job, " or ".join(sorted(stems))))
    return tuple(lanes), tuple(pins)


def _lane_reading(reader, refusal, text):
    """reader (a _workflow_lanes) over text, or (2, whether the message is the reader's own refusal of a
    line or value naming --suite or the suite runner) when it raises refusal (the CannotEvaluate class of
    reader's module); any other refusal, a job-model refusal of a fixture among them, is (2, False)."""
    try:
        return reader(text)
    except refusal as exc:
        return 2, "this lane reader cannot read which suite it runs" in str(exc)


def _host_job(job):
    """A whole host-patch job, laid out as quality.yml lays it out."""
    return ("  {0}:\n    name: {0}\n    runs-on: {1}\n    steps:\n      - uses: actions/checkout@v4\n"
            "        with:\n          fetch-depth: 0\n          fetch-tags: true\n\n".format(job, RUNNER)
            + SETUP_STEP + GATE_STEP + _suite_step(job))


def _edited(text, edits, part):
    """text with each edit (part, old, new) for this part applied in order, each replacing the one
    occurrence of old."""
    for name, old, new in edits:
        if name == part:
            if text.count(old) != 1:
                raise CannotEvaluate("self-test edit {!r} occurs {} times in {}".format(
                    old, text.count(old), part or "the top level"))
            text = text.replace(old, new)
    return text


def _workflow(edits=(), omit=()):
    """quality.yml: HEAD, a floor job, each host-patch job not in omit, then a registry job carrying the
    --jobs-only judge step (part name "registry" for edits, omitted when omit names it), with the edits
    applied in order; an edit (job, old, new) replaces the one occurrence of old in that job, or in HEAD
    when job is None."""
    jobs = [_edited(HEAD, edits, None), FLOOR_JOB]
    jobs.extend(_edited(_host_job(job), edits, job) for job in HOST_JOBS if job not in omit)
    if "registry" not in omit:
        jobs.append(_edited(REGISTRY_JOB, edits, "registry"))
    return "".join(jobs)


def _fixture(base, source=SOURCE_TEXT, workflow=None):
    """A tree with the single source (absent when source is None) and quality.yml: _workflow() unless the
    workflow text is given."""
    root = Path(tempfile.mkdtemp(prefix="tree-", dir=base))
    if source is not None:
        _write(root, SOURCE_REL, source)
    _write(root, WORKFLOW_REL, _workflow() if workflow is None else workflow)
    return root


def _has(lines, marker):
    return any(marker in line for line in lines)


def _outcome(evaluate_tree, root, version, marker):
    """(exit code, whether a report line holds marker) for evaluate_tree over root, or "raised NAME" when a
    parser exception escapes it."""
    try:
        code, lines = evaluate_tree(root, version)
    except (RecursionError, ValueError, MemoryError) as exc:
        return "raised " + type(exc).__name__
    return code, _has(lines, marker)


class _ParserMismatch(Exception):
    """tomllib's private key parser does not fit the parse_key(src, pos) -> (pos, key) the injection
    wraps."""


def _key_depth_bounded(real, calls, kind=RecursionError):
    """tomllib's key parser, made to raise RecursionError on a key of more than 1000 dotted parts, as a
    release that bounds key depth does, or kind (MemoryError or ValueError, to inject that class at the
    parse: real memory exhaustion cannot be provoked safely). A RecursionError or MemoryError that the
    real parser raises itself (a release whose own parser bounds key depth refuses such a key inside
    it) is raised as kind too, so the class a vector asserts never depends on the patch release that
    runs the self-test. calls records each call; a call or a result that does not fit
    parse_key(src, pos) -> (pos, key), pos an int within src and key a tuple of str, raises
    _ParserMismatch."""
    def parse_key(*args, **kwargs):
        calls.append(len(args))
        try:
            result = real(*args, **kwargs)
        except TypeError as exc:
            raise _ParserMismatch("calling it raised TypeError: {}".format(exc))
        except (RecursionError, MemoryError):
            raise kind("injected: the key parser itself refused a key") from None
        src = args[0] if args else kwargs.get("src")
        if not (isinstance(result, tuple) and len(result) == 2):
            raise _ParserMismatch("it returned {} rather than (pos, key)".format(type(result).__name__))
        if not (isinstance(src, str) and type(result[0]) is int and 0 <= result[0] <= len(src)):
            raise _ParserMismatch("it returned the position {!r:.40}, not an int within the source".format(
                result[0]))
        if not (isinstance(result[1], tuple) and all(isinstance(part, str) for part in result[1])):
            raise _ParserMismatch("it returned the key {!r:.60}, not a tuple of str".format(result[1]))
        if len(result[1]) > 1000:
            raise kind("injected: a key of more than 1000 dotted parts")
        return result
    return parse_key


def _capped_parser(real):
    """real, tomllib's key parser, as a release whose own parser bounds key depth runs it: a key of more
    than 1000 dotted parts is refused by a RecursionError from inside the parser (emulated here once the
    real parser returns such a key), so a vector shows what the injection does on such a release
    whatever patch release runs the self-test."""
    def parse_key(*args, **kwargs):
        result = real(*args, **kwargs)
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], tuple) \
                and len(result[1]) > 1000:
            raise RecursionError("emulated: a key parser that bounds key depth")
        return result
    return parse_key


def _parse_refusal(evaluate_tree, root, bound, kind=RecursionError, capped=False):
    """(exit code, whether the report says the source does not parse) for evaluate_tree over root at HOST,
    with tomllib's key parser bounded when bound is true (raising kind, _key_depth_bounded) and, when
    capped is true, the parser under that bound refusing such a key itself (_capped_parser); "raised
    NAME" when the exception escapes. The bound
    patches a private tomllib function: on a release where it is missing, has another shape or is never
    called, the result is a text naming that cause, so the case fails cleanly rather than crashing."""
    parser = getattr(tomllib, "_parser", None)
    real = getattr(parser, "parse_key", None)
    if bound and not callable(real):
        return ("tomllib._parser.parse_key is missing or not callable on this release, so the injected "
                "key-depth case cannot run")
    calls = []
    if bound:
        parser.parse_key = _key_depth_bounded(_capped_parser(real) if capped else real, calls, kind)
    try:
        result = _outcome(evaluate_tree, root, HOST, SOURCE_REL + ": does not parse")
    except _ParserMismatch as exc:
        result = "tomllib._parser.parse_key does not fit the injection on this release: {}".format(exc)
    finally:
        if bound:
            parser.parse_key = real
    if bound and not calls and not isinstance(result, str):
        return "tomllib._parser.parse_key was never called, so the injected key-depth case did not run"
    return result


# A key parser whose (pos, key) result is malformed, each with the words of the mismatch it must raise: the
# bounded parser names each as a mismatch, never a traceback, never the expected refusal.
MALFORMED_RESULTS = {
    "none-position": (lambda src, pos, key: (None, key), "not an int within the source"),
    "negative-position": (lambda src, pos, key: (-1, key), "not an int within the source"),
    "past-end-position": (lambda src, pos, key: (len(src) + 1, key), "not an int within the source"),
    "bool-position": (lambda src, pos, key: (True, key), "not an int within the source"),
    "non-str-key-part": (lambda src, pos, key: (pos, key + (1,)), "not a tuple of str"),
}


def _malformed_refusal(refusal, damage, marker):
    """True when refusal(), run with tomllib's key parser replaced by one whose result damage(src, pos, key)
    rewrites, returns a text naming the mismatch marker words; otherwise what it returned or raised."""
    parser = getattr(tomllib, "_parser", None)
    real = getattr(parser, "parse_key", None)
    if not callable(real):
        return "tomllib._parser.parse_key is missing or not callable on this release"

    def parse_key(src, pos):
        pos, key = real(src, pos)
        return damage(src, pos, key)
    parser.parse_key = parse_key
    try:
        got = refusal()
    except Exception as exc:
        got = "raised " + type(exc).__name__
    finally:
        parser.parse_key = real
    return True if isinstance(got, str) and "does not fit the injection" in got and marker in got else got


def _job_cases():
    """Each jobs-leg finding case: (check id, edits, omitted jobs, a marker of its finding, and the one
    JOB_RULES rule whose removal lets it pass, or None where no one rule decides it:
    jobs/second-pin-line-finding and jobs/job-env-pin-finding are each refused by two overlapping rules,
    jobs/jobs-only-moved-and-host-jobs-env-finding and jobs/jobs-only-step-env-and-host-jobs-env-finding
    are each refused by both the outside-judge and job-keys rules, and pins/host-job-missing-finding is
    refused by the unconditional missing-job check in job_findings, which no JOB_RULES rule
    controls; a None case runs no rule-removal copy, so it is red through its exit code and finding
    marker alone)."""
    suite = {job: _suite_step(job) for job in HOST_JOBS}
    # The four git-fixture-env suite steps, each a whole step text, in HOST_SUITES order.
    lanes = ["      - name: Suite" + text for text in suite[GFE].split("      - name: Suite")[1:]]
    floor_suite = "python3 -I -B tools/check_selftest_execution.py --suite python-floor-selftest"
    retired_suite = "python3 -I -B tools/check_selftest_execution.py --suite git-fixture-env-selftest"
    gate_name = "      - name: Python host pin\n"
    suite_name = "      - name: Suite\n"
    setup_uses = "      - uses: actions/setup-python@v5\n"
    checkout_uses = "      - uses: actions/checkout@v4\n"
    fetch_tags = "          fetch-tags: true\n"
    runs = "    runs-on: "
    second = "  second:\n    runs-on: ubuntu-latest\n    steps:\n"
    decoy = ("  decoy:\n    runs-on: ubuntu-latest\n    steps:\n"
             "      - name: Decoy judge\n        run: " + JOBS_ONLY_RUN + "\n        if: false\n")
    return (
        ("pins/host-job-missing-finding", (), (GFE,), "carries no job " + GFE, None),
        ("pins/newer-patch-finding", ((OPF, PIN, "python-version: '3.14.8'"),), (),
         "python-version '3.14.8' in the host-patch job opf-host-patch differs", "pin"),
        ("pins/floor-pin-in-host-job-finding", ((GFE, PIN, "python-version: '3.14'"),), (),
         "python-version '3.14' in the host-patch job git-fixture-env-host-patch differs", "pin"),
        ("pins/host-job-without-pin-finding", ((OPF, "        with:\n          " + PIN + "\n", ""),), (),
         "opf-host-patch carries 0 python-version pins", "setup"),
        ("pins/host-job-two-pins-finding", ((OPF, SETUP_STEP, SETUP_STEP + SETUP_STEP),), (),
         "opf-host-patch carries 2 setup-python steps", "setup"),
        ("jobs/setup-step-removed-finding", ((GFE, SETUP_STEP, ""),), (),
         GFE + " carries 0 setup-python steps", "setup"),
        # A pin among the checkout inputs: the setup and checkout rules both refuse it.
        ("jobs/second-pin-line-finding",
         ((OPF, "          fetch-depth: 0\n", "          fetch-depth: 0\n          " + PIN + "\n"),), (),
         "opf-host-patch carries 2 python-version pins", None),
        ("jobs/setup-step-second-pin-finding", ((GFE, setup_uses, setup_uses + "        " + PIN + "\n"),), (),
         GFE + " carries 2 python-version pins", "setup"),
        ("jobs/setup-step-if-finding", ((GFE, setup_uses, setup_uses + "        if: false\n"),), (),
         "sets if: on its setup-python step", "setup"),
        ("jobs/setup-step-extra-input-finding",
         ((OPF, "          " + PIN + "\n", "          " + PIN + "\n          update-environment: false\n"),),
         (), "holds ['python-version', 'update-environment']", "setup"),
        # The setup-python step and the gate step removed, and a job-level env: that names
        # python-version in their place.
        ("jobs/job-env-pin-finding",
         ((GFE, SETUP_STEP + GATE_STEP, ""), (GFE, runs, "    env:\n      " + PIN + "\n" + runs)), (),
         GFE + " carries 0 setup-python steps", None),
        ("jobs/gate-step-removed-finding", ((OPF, GATE_STEP, ""),), (),
         "is not the Python host pin step", "gate-step"),
        ("jobs/gate-step-moved-finding",
         ((GFE, GATE_STEP, ""), (GFE, suite[GFE], suite[GFE] + "\n" + GATE_STEP)), (),
         "is not the Python host pin step", "gate-step"),
        ("jobs/gate-step-other-command-finding",
         ((OPF, "run: " + GATE_RUN, "run: python3 tools/check_python_host_pin.py"),), (),
         "is not the Python host pin step", "gate-step"),
        ("jobs/gate-step-if-finding", ((GFE, gate_name, gate_name + "        if: false\n"),), (),
         "is not the Python host pin step", "gate-step"),
        ("jobs/gate-step-continue-on-error-finding",
         ((OPF, gate_name, gate_name + "        continue-on-error: true\n"),), (),
         "is not the Python host pin step", "gate-step"),
        ("jobs/step-before-gate-finding",
         ((GFE, GATE_STEP, "      - run: pip install other-python\n\n" + GATE_STEP),), (),
         "is not the Python host pin step", "gate-step"),
        ("jobs/opf-suite-removed-finding", ((OPF, suite[OPF], ""),), (),
         "opf-host-patch does not run its suite", "suite-step"),
        ("jobs/git-fixture-env-suite-removed-finding", ((GFE, suite[GFE], ""),), (),
         GFE + " does not run its suite", "suite-step"),
        ("jobs/suite-renamed-finding",
         ((GFE, "--suite git-fixture-env-core", "--suite python-floor-selftest"),), (),
         GFE + " does not run its suite", "suite-step"),
        ("jobs/suite-before-setup-finding",
         ((OPF, suite[OPF], ""), (OPF, SETUP_STEP, suite[OPF] + "\n" + SETUP_STEP)), (),
         "opf-host-patch does not run its suite", "suite-step"),
        ("jobs/suite-step-if-finding", ((GFE, suite_name, suite_name + "        if: false\n"),), (),
         GFE + " does not run its suite", "suite-step"),
        ("jobs/suite-step-continue-on-error-finding",
         ((OPF, suite_name, suite_name + "        continue-on-error: true\n"),), (),
         "opf-host-patch does not run its suite", "suite-step"),
        # The git-fixture-env job runs the four split suites, each its own step, in HOST_SUITES order:
        # omitting, reordering, repeating or adding a suite step (a renamed, retired or respelled suite
        # of a suite runner, or another job's suite, among them) is each a finding.
        ("jobs/git-fixture-env-suite-omitted-finding", ((GFE, lanes[2], ""),), (),
         "it omits " + HOST_SUITES[GFE][2], "suite-step"),
        ("jobs/git-fixture-env-suites-reordered-finding", ((GFE, lanes[0] + lanes[1], lanes[1] + lanes[0]),),
         (), "it runs them in another order", "suite-step"),
        ("jobs/git-fixture-env-suite-repeated-finding",
         ((GFE, lanes[3], lanes[3] + lanes[0].replace("name: Suite\n", "name: Suite again\n")),), (),
         "repeats " + HOST_SUITES[GFE][0], "suite-step"),
        ("jobs/git-fixture-env-suite-added-finding",
         ((GFE, lanes[3], lanes[3] + "      - name: Floor suite\n" + SHELL_LINE + "        run: " + floor_suite
           + "\n"),), (), "adds " + floor_suite, "suite-step"),
        ("jobs/git-fixture-env-retired-suite-added-finding",
         ((GFE, lanes[3], lanes[3] + "      - name: Retired suite\n" + SHELL_LINE + "        run: "
           + retired_suite + "\n"),), (), "adds " + retired_suite, "suite-step"),
        ("jobs/git-fixture-env-suite-respelled-finding",
         ((GFE, "run: " + HOST_SUITES[GFE][0], "run: " + HOST_SUITES[GFE][0].replace("tools/", "./tools/")),),
         (), "adds " + HOST_SUITES[GFE][0].replace("tools/", "./tools/"), "suite-step"),
        ("jobs/opf-other-job-suite-added-finding",
         ((OPF, suite[OPF], suite[OPF] + lanes[0].replace("name: Suite\n", "name: Extra suite\n")),), (),
         "adds " + HOST_SUITES[GFE][0], "suite-step"),
        ("jobs/job-if-finding", ((OPF, runs, "    if: false\n" + runs),), (),
         "opf-host-patch carries if:", "job-keys"),
        ("jobs/job-continue-on-error-finding", ((GFE, runs, "    continue-on-error: true\n" + runs),), (),
         GFE + " carries continue-on-error:", "job-keys"),
        ("jobs/job-container-finding", ((OPF, runs, "    container: python:3.14.8\n" + runs),), (),
         "opf-host-patch carries container:", "job-keys"),
        # The top-level concurrency: key is admitted (TOP_KEYS); a job-level one on a host-patch job is not.
        ("jobs/job-concurrency-finding", ((OPF, runs, "    concurrency: x\n" + runs),), (),
         "opf-host-patch carries concurrency:", "job-keys"),
        ("jobs/runs-on-latest-finding", ((OPF, runs + RUNNER, runs + "ubuntu-latest"),), (),
         "runs on 'ubuntu-latest', not 'ubuntu-24.04'", "runner"),
        ("jobs/runs-on-newer-image-finding", ((GFE, runs + RUNNER, runs + "ubuntu-26.04"),), (),
         "runs on 'ubuntu-26.04', not 'ubuntu-24.04'", "runner"),
        # A workflow-level shell default that runs no step's command.
        ("jobs/workflow-defaults-echo-finding",
         ((None, "jobs:\n", "defaults:\n  run:\n    shell: echo {0}\n\njobs:\n"),), (),
         "carries the top-level key defaults:", "workflow-keys"),
        ("jobs/workflow-defaults-true-finding",
         ((None, "jobs:\n", "defaults:\n  run:\n    shell: \"true {0}\"\n\njobs:\n"),), (),
         "carries the top-level key defaults:", "workflow-keys"),
        ("jobs/workflow-defaults-after-jobs-finding",
         (("registry", JUDGE_STEP, JUDGE_STEP + "\ndefaults:\n  run:\n    shell: echo {0}\n"),), (),
         "carries the top-level key defaults:", "workflow-keys"),
        ("jobs/workflow-env-finding", ((None, "jobs:\n", "env:\n  FOO: bar\n\njobs:\n"),), (),
         "carries the top-level key env:", "workflow-keys"),
        ("jobs/job-defaults-finding",
         ((OPF, runs, "    defaults:\n      run:\n        shell: echo {0}\n" + runs),), (),
         "opf-host-patch carries defaults:", "job-keys"),
        # An extra, otherwise unheld step whose env: makes BASH_ENV append to GITHUB_PATH before an
        # otherwise accepted command runs, and the GITHUB_ENV variant.
        ("jobs/step-env-github-path-finding",
         ((OPF, GATE_STEP, GATE_STEP + "      - name: Extra check\n        run: " + GATE_RUN
           + " --self-test\n        env:\n"
           + "          BASH_ENV: $(printf /review-probe >> \"$GITHUB_PATH\")\n\n"),), (),
         "opf-host-patch sets env: on step 4", "step-env"),
        ("jobs/step-env-github-env-finding",
         ((GFE, GATE_STEP, GATE_STEP + "      - name: Extra check\n        run: " + GATE_RUN
           + " --self-test\n        env:\n"
           + "          BASH_ENV: $(printf REVIEW_PROBE=1 >> \"$GITHUB_ENV\")\n\n"),), (),
         GFE + " sets env: on step 4", "step-env"),
        # A job-level env: (BASH_ENV) in BOTH host-patch jobs, which in CI silences every step of those
        # jobs, this gate's own steps included; job-keys keeps refusing it.
        ("jobs/job-env-bash-env-both-jobs-finding",
         ((OPF, runs, "    env:\n      BASH_ENV: '$(: > \"$0\")'\n" + runs),
          (GFE, runs, "    env:\n      BASH_ENV: '$(: > \"$0\")'\n" + runs)), (),
         "carries env:", "job-keys"),
        # The outside judge: relocating the --jobs-only step into a host-patch job, removing it (or its
        # whole job), doubling it, and an env: on the step or on the job that carries it are each refused
        # by the outside-judge rule.
        ("jobs/jobs-only-into-host-job-finding",
         (("registry", JUDGE_STEP, ""),
          (OPF, suite[OPF], suite[OPF] + "\n      - name: Python host pin jobs leg\n        run: "
           + JOBS_ONLY_RUN + "\n")), (),
         "names --jobs-only inside the host-patch job opf-host-patch", "outside-judge"),
        ("jobs/jobs-only-step-removed-finding", (("registry", JUDGE_STEP, ""),), (),
         "0 steps outside the host-patch jobs run", "outside-judge"),
        ("jobs/jobs-only-job-omitted-finding", (), ("registry",),
         "0 steps outside the host-patch jobs run", "outside-judge"),
        ("jobs/jobs-only-step-twice-finding", (("registry", JUDGE_STEP, JUDGE_STEP + JUDGE_STEP),), (),
         "2 steps outside the host-patch jobs run", "outside-judge"),
        ("jobs/jobs-only-step-env-finding",
         (("registry", JUDGE_STEP,
           JUDGE_STEP + "        env:\n          BASH_ENV: '$(: > \"$0\")'\n"),), (),
         "the step that carries the --jobs-only judge sets env:", "outside-judge"),
        ("jobs/jobs-only-job-env-finding",
         (("registry", "    runs-on: ", "    env:\n      BASH_ENV: '$(: > \"$0\")'\n    runs-on: "),), (),
         "sets a job-level env:", "outside-judge"),
        # A command-shaped value that is not a step's own run: property never counts as the judge (a
        # with: input of the registry checkout step, and a job-level run:), and an env: on an earlier
        # step of the judge's job (whose GITHUB_ENV write reaches the judge step) is a finding.
        ("jobs/jobs-only-with-input-decoy-finding",
         (("registry", JUDGE_STEP, ""),
          ("registry", "      - uses: actions/checkout@v4\n",
           "      - uses: actions/checkout@v4\n        with:\n          run: " + JOBS_ONLY_RUN + "\n")),
         (), "0 steps outside the host-patch jobs run", "outside-judge"),
        ("jobs/jobs-only-job-level-run-finding",
         (("registry", JUDGE_STEP, ""),
          ("registry", "    runs-on: ", "    run: " + JOBS_ONLY_RUN + "\n    runs-on: ")), (),
         "0 steps outside the host-patch jobs run", "outside-judge"),
        ("jobs/jobs-only-earlier-step-env-finding",
         (("registry", "      - uses: actions/setup-python@v5\n",
           "      - uses: actions/setup-python@v5\n        env:\n          BASH_ENV: '$(: > \"$0\")'\n"),),
         (), "sets env: on step 2", "outside-judge"),
        # The relocation above with a job-level env: (BASH_ENV) in both host-patch jobs, and an env: on
        # the judge step with the same host-job env:; the outside-judge and job-keys rules both refuse
        # each.
        ("jobs/jobs-only-moved-and-host-jobs-env-finding",
         (("registry", JUDGE_STEP, ""),
          (OPF, suite[OPF], suite[OPF] + "\n      - name: Python host pin jobs leg\n        run: "
           + JOBS_ONLY_RUN + "\n"),
          (OPF, runs, "    env:\n      BASH_ENV: '$(: > \"$0\")'\n" + runs),
          (GFE, runs, "    env:\n      BASH_ENV: '$(: > \"$0\")'\n" + runs)), (),
         "names --jobs-only inside the host-patch job", None),
        ("jobs/jobs-only-step-env-and-host-jobs-env-finding",
         (("registry", JUDGE_STEP,
           JUDGE_STEP + "        env:\n          BASH_ENV: '$(: > \"$0\")'\n"),
          (OPF, runs, "    env:\n      BASH_ENV: '$(: > \"$0\")'\n" + runs),
          (GFE, runs, "    env:\n      BASH_ENV: '$(: > \"$0\")'\n" + runs)), (),
         "the step that carries the --jobs-only judge sets env:", None),
        # The canonical registry judge KEPT, and a second judge added in a spelling the job model reads
        # (a quoted run: value, a tab before the value): each parses to the judge command, so the
        # doubled judge is a finding, never a silent pass.
        ("jobs/jobs-only-single-quoted-second-judge-finding",
         (("registry", JUDGE_STEP,
           JUDGE_STEP + second + "      - run: '" + JOBS_ONLY_RUN + "'\n"),), (),
         "2 steps outside the host-patch jobs run", "outside-judge"),
        ("jobs/jobs-only-double-quoted-second-judge-finding",
         (("registry", JUDGE_STEP,
           JUDGE_STEP + second + "      - run: \"" + JOBS_ONLY_RUN + "\"\n"),), (),
         "2 steps outside the host-patch jobs run", "outside-judge"),
        ("jobs/jobs-only-tab-value-second-judge-finding",
         (("registry", JUDGE_STEP,
           JUDGE_STEP + second + "      - run:\t" + JOBS_ONLY_RUN + "\n"),), (),
         "2 steps outside the host-patch jobs run", "outside-judge"),
        # The judge step carries exactly name, run and shell: bash: any other key on it (an if: among
        # them) can skip, mask or change what it runs, so it is a finding.
        ("jobs/jobs-only-step-if-finding",
         (("registry", JUDGE_STEP, JUDGE_STEP + "        if: false\n"),), (),
         "the step that carries the --jobs-only judge carries if:", "outside-judge"),
        # An env: on a step after the judge does not reach it; the refusal is policy, so the judge's
        # job stays env-free, and the finding says so by position.
        ("jobs/jobs-only-later-step-env-finding",
         (("registry", JUDGE_STEP, JUDGE_STEP + "      - name: Later step\n        run: echo done\n"
           "        env:\n          SAMPLE: value\n"),), (),
         "sets env: on step 4, after the judge step", "outside-judge"),
        # Three edits combined: the registry judge re-quoted, a job-level env: on the registry job, and
        # a decoy job carrying the canonical judge under if: false. The quoted
        # judge still parses and counts, so the doubled judge, the env: on its job and the if: on the
        # decoy judge step are each findings.
        ("jobs/jobs-only-requoted-judge-env-and-decoy-finding",
         (("registry", "run: " + JOBS_ONLY_RUN + "\n", "run: '" + JOBS_ONLY_RUN + "'\n"),
          ("registry", "    runs-on: ", "    env:\n      SAMPLE: value\n    runs-on: "),
          ("registry", "'" + JOBS_ONLY_RUN + "'\n", "'" + JOBS_ONLY_RUN + "'\n" + decoy)), (),
         "sets a job-level env:", "outside-judge"),
        # The judge is judged by command tokens. With the canonical registry judge KEPT, a second
        # spelling whose tokens equal the judge command's (a doubled space, a ./tools/ path, quoted
        # trailing whitespace) counts, so the doubled judge is a finding; a flag-free step running
        # this gate's path is judge-shaped without counting (an unresolved judge-shaped command); and
        # a judge spelled as a matrix value is a value finding.
        ("jobs/jobs-only-doubled-space-second-judge-finding",
         (("registry", JUDGE_STEP,
           JUDGE_STEP + second + "      - run: " + JOBS_ONLY_RUN.replace("python3 ", "python3  ")
           + "\n"),), (),
         "2 steps outside the host-patch jobs run", "outside-judge"),
        ("jobs/jobs-only-dot-slash-second-judge-finding",
         (("registry", JUDGE_STEP,
           JUDGE_STEP + second + "      - run: " + JOBS_ONLY_RUN.replace("tools/", "./tools/")
           + "\n"),), (),
         "2 steps outside the host-patch jobs run", "outside-judge"),
        ("jobs/jobs-only-trailing-space-second-judge-finding",
         (("registry", JUDGE_STEP,
           JUDGE_STEP + second + "      - run: \"" + JOBS_ONLY_RUN + " \"\n"),), (),
         "2 steps outside the host-patch jobs run", "outside-judge"),
        ("jobs/jobs-only-flag-free-gate-path-finding",
         (("registry", JUDGE_STEP, JUDGE_STEP + second + "      - run: " + GATE_RUN + "\n"),), (),
         "an unresolved judge-shaped command", "outside-judge"),
        ("jobs/jobs-only-matrix-value-finding",
         (("registry", JUDGE_STEP,
           JUDGE_STEP + "  second:\n    runs-on: ubuntu-latest\n    strategy:\n      matrix:\n"
           "        command: '" + JOBS_ONLY_RUN + "'\n    steps:\n      - run: echo ok\n"),), (),
         "in a value that is not a step's own run: command", "outside-judge"),
        # Selection follows the tokens of every run: of every job outside the host-patch jobs, so a
        # quote-spliced spelling (shell quote removal builds the name from parts no literal substring
        # spells), a canonical or spliced judge inside a one-line literal block scalar (a folded
        # opener is cannot-evaluate instead), and a docs/../tools/ path that only the lexical
        # normpath folds are each classified, never passed unread.
        ("jobs/jobs-only-double-quote-spliced-second-judge-finding",
         (("registry", JUDGE_STEP, JUDGE_STEP + second
           + '      - run: python3 -I -B tools/check_python_host_"pin".py --jobs-o""nly\n'),), (),
         "2 steps outside the host-patch jobs run", "outside-judge"),
        ("jobs/jobs-only-single-quote-spliced-second-judge-finding",
         (("registry", JUDGE_STEP, JUDGE_STEP + second
           + "      - run: python3 -I -B tools/check_python_host_'pin'.py --jobs-'only'\n"),), (),
         "2 steps outside the host-patch jobs run", "outside-judge"),
        ("jobs/jobs-only-spliced-flag-free-gate-path-finding",
         (("registry", JUDGE_STEP, JUDGE_STEP + second
           + '      - run: python3 -I -B tools/check_python_host_pi"n".py\n'),), (),
         "an unresolved judge-shaped command", "outside-judge"),
        ("jobs/jobs-only-literal-block-second-judge-finding",
         (("registry", JUDGE_STEP, JUDGE_STEP + second
           + "      - run: |\n          " + JOBS_ONLY_RUN + "\n"),), (),
         "2 steps outside the host-patch jobs run", "outside-judge"),
        # The judge counts only when EXACTLY ONE line of a run: carries tokens and that line's tokens
        # are the judge command's, so a judge spread over two block-scalar lines (either line order)
        # never counts (a shell runs each line as its own command, and the flag line alone fails in
        # CI), and a #-leading body line is content, not a comment: a judge beside one, or spelled on
        # one, is an unresolved judge-shaped finding, never a counted judge and never a silent pass.
        # The flag-first order below is not a discriminating vector of the line-boundary read: a read
        # that joins a run:'s lines in order still finds its tokens out of the judge command's order,
        # so it is red through the same zero-judge count. Its targeted line-boundary mutant lives in
        # _self_test_cases (jobs/jobs-only-split-flag-first-line-boundary-mutant-red); that mutant
        # counts either split order, and the command-first order is asserted red under it there too
        # (jobs/jobs-only-split-command-line-boundary-mutant-red).
        ("jobs/jobs-only-split-command-judge-finding",
         (("registry", "run: " + JOBS_ONLY_RUN,
           "run: |\n          " + GATE_RUN + "\n          " + JOBS_ONLY_FLAG),), (),
         "0 steps outside the host-patch jobs run", "outside-judge"),
        ("jobs/jobs-only-split-flag-first-judge-finding",
         (("registry", "run: " + JOBS_ONLY_RUN,
           "run: |\n          " + JOBS_ONLY_FLAG + "\n          " + GATE_RUN),), (),
         "0 steps outside the host-patch jobs run", "outside-judge"),
        ("jobs/jobs-only-comment-then-judge-block-finding",
         (("registry", JUDGE_STEP, JUDGE_STEP + second
           + "      - run: |\n          # disabled\n          " + JOBS_ONLY_RUN + "\n"),), (),
         "an unresolved judge-shaped command", "outside-judge"),
        ("jobs/jobs-only-comment-spelled-judge-block-finding",
         (("registry", JUDGE_STEP, JUDGE_STEP + second
           + "      - run: |\n          echo start\n          # " + JOBS_ONLY_RUN + "\n"),), (),
         "an unresolved judge-shaped command", "outside-judge"),
        ("jobs/jobs-only-spliced-literal-block-second-judge-finding",
         (("registry", JUDGE_STEP, JUDGE_STEP + second
           + '      - run: |\n          python3 -I -B tools/check_python_host_"pin".py'
           ' --jobs-o""nly\n'),), (),
         "2 steps outside the host-patch jobs run", "outside-judge"),
        ("jobs/jobs-only-dotdot-judge-path-finding",
         (("registry", "run: " + JOBS_ONLY_RUN,
           "run: python3 -I -B docs/../tools/check_python_host_pin.py --jobs-only"),), (),
         "an unresolved judge-shaped command", "outside-judge"),
        ("jobs/checkout-ref-finding", ((OPF, fetch_tags, fetch_tags + "          ref: main\n"),), (),
         "opf-host-patch: its first step is not the checkout step as written", "checkout"),
        ("jobs/checkout-repository-finding",
         ((GFE, fetch_tags, fetch_tags + "          repository: other/repo\n"),), (),
         GFE + ": its first step is not the checkout step as written", "checkout"),
        ("jobs/checkout-path-finding", ((OPF, fetch_tags, fetch_tags + "          path: other\n"),), (),
         "opf-host-patch: its first step is not the checkout step as written", "checkout"),
        ("jobs/checkout-other-ref-of-action-finding",
         ((GFE, "actions/checkout@v4", "actions/checkout@main"),), (),
         GFE + ": its first step is not the checkout step as written", "checkout"),
        ("jobs/checkout-step-if-finding", ((OPF, checkout_uses, checkout_uses + "        if: false\n"),), (),
         "opf-host-patch: its first step is not the checkout step as written", "checkout"),
        ("jobs/second-checkout-finding",
         ((GFE, GATE_STEP, GATE_STEP + checkout_uses + "        with:\n          ref: main\n\n"),), (),
         GFE + " carries another checkout step (step 4)", "checkout"),
    )


def _self_test_cases(base):
    rules = _copy(base, "python_host_pin_rules", _read_text(Path(__file__).resolve()))
    check("fixture/clean-tree-passes", evaluate(_fixture(base), HOST)[0], 0)

    for check_id, source in (
            ("source/missing-cannot-evaluate", None),
            ("source/malformed-cannot-evaluate", "python-host-patch = \n"),
            ("source/extra-key-cannot-evaluate", SOURCE_TEXT + "python-floor = \"3.14\"\n"),
            ("source/missing-key-cannot-evaluate", "format-version = 1\n"),
            ("source/string-format-version-cannot-evaluate",
             "format-version = \"1\"\npython-host-patch = \"3.14.4\"\n"),
            ("source/minor-only-cannot-evaluate", "format-version = 1\npython-host-patch = \"3.14\"\n"),
            ("source/leading-zero-cannot-evaluate", "format-version = 1\npython-host-patch = \"3.14.04\"\n"),
            ("source/suffix-cannot-evaluate", "format-version = 1\npython-host-patch = \"3.14.4rc1\"\n"),
            ("source/number-cannot-evaluate", "format-version = 1\npython-host-patch = 3.14\n"),
            ("source/not-utf8-cannot-evaluate", b"format-version = 1\npython-host-patch = \"3.14.4\"\n\xff")):
        code, lines = evaluate(_fixture(base, source=source), HOST)
        check(check_id, (code, _has(lines, SOURCE_REL)), (2, True))
    root = _fixture(base, source=None)
    (root / SOURCE_REL).mkdir(parents=True)
    code, lines = evaluate(root, HOST)
    check("source/directory-cannot-evaluate", (code, _has(lines, "not a regular file")), (2, True))
    root = _fixture(base, source=None)
    _write(root, "elsewhere.toml", SOURCE_TEXT)
    (root / SOURCE_REL).parent.mkdir(parents=True, exist_ok=True)
    os.symlink(root / "elsewhere.toml", root / SOURCE_REL)
    code, lines = evaluate(root, HOST)
    check("source/symlink-cannot-evaluate", (code, _has(lines, "not a regular file")), (2, True))
    # tomllib refuses some sources outside TOMLDecodeError: a RecursionError on deep nesting (a real payload,
    # far past any default recursion limit) and on a key of more than 1000 dotted parts (INJECTED: the
    # running release may not bound key depth, so its key parser is patched to raise as one that does), and
    # a bare ValueError on an over-long integer literal. Each is cannot-evaluate; an exception that escapes
    # evaluate() is recorded as raised, so each case is red when its mapping is removed.
    for check_id, extra, bound in (
            ("source/deep-nesting-cannot-evaluate", "x = " + "[" * 10000 + "]" * 10000 + "\n", False),
            ("source/long-dotted-key-cannot-evaluate", ".".join(["k"] * 1001) + " = 1\n", True),
            ("source/long-integer-cannot-evaluate", "x = " + "1" * 5000 + "\n", False)):
        check(check_id, _parse_refusal(evaluate, _fixture(base, source=SOURCE_TEXT + extra), bound),
              (2, True))
    # A key parser whose result breaks the (pos, key) contract: the bounded parser names the mismatch.
    root = _fixture(base)
    for check_id, name in (
            ("source/parser-none-position-named-failure", "none-position"),
            ("source/parser-negative-position-named-failure", "negative-position"),
            ("source/parser-past-end-position-named-failure", "past-end-position"),
            ("source/parser-bool-position-named-failure", "bool-position"),
            ("source/parser-non-str-key-part-named-failure", "non-str-key-part")):
        check(check_id, _malformed_refusal(lambda: _parse_refusal(evaluate, root, True),
                                           *MALFORMED_RESULTS[name]), True)
    # A version component: PATCH_RE bounds it at four digits, and a component that still does not convert
    # is cannot-evaluate. Each case also runs the copy with the bound removed: a five-digit component then
    # loads (a finding, so the bound is what refuses it), and a 5,000-digit quoted component, past the
    # int-string limit, is still refused by the conversion mapping, never a raised ValueError.
    five = _fixture(base, source="format-version = 1\npython-host-patch = \"3.14.10000\"\n")
    long = _fixture(base, source="format-version = 1\npython-host-patch = \"3.14." + "1" * 5000 + "\"\n")
    rules.PATCH_RE = re.compile(r"([1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
    try:
        check("source/five-digit-component-cannot-evaluate",
              (_outcome(evaluate, five, HOST, "at most four digits"), rules.evaluate(five, HOST)[0]),
              ((2, True), 1))
        check("source/long-quoted-component-cannot-evaluate",
              (_outcome(evaluate, long, HOST, "at most four digits"),
               _outcome(rules.evaluate, long, HOST, "does not convert to integers")), ((2, True), (2, True)))
    finally:
        rules.PATCH_RE = PATCH_RE

    root = _fixture(base)
    (root / WORKFLOW_REL).unlink()
    code, lines = evaluate(root, HOST)
    check("pins/workflow-missing-cannot-evaluate", (code, _has(lines, "quality.yml: cannot stat")), (2, True))
    # Each finding case is red with the intact gate and, but for a case more than one rule refuses, green in
    # the copy with its one rule removed from JOB_RULES: each rule is red on revert.
    cases = _job_cases()
    if {case[4] for case in cases if case[4] is not None} != JOB_RULES:
        raise CannotEvaluate("a JOB_RULES rule has no finding case")
    for check_id, edits, omit, marker, rule in cases:
        root = _fixture(base, workflow=_workflow(edits, omit))
        code, lines = evaluate(root, HOST)
        got, want = (code, _has(lines, marker)), (1, True)
        if rule is not None:
            rules.JOB_RULES = JOB_RULES - {rule}
            try:
                got, want = got + (rules.evaluate(root, HOST)[0],), want + (0,)
            finally:
                rules.JOB_RULES = JOB_RULES
        check(check_id, got, want)
    for check_id, pin in (
            ("pins/plain-pin-passes", "3.14.4"),
            ("pins/double-quoted-pin-passes", "\"3.14.4\""),
            ("pins/trailing-comment-passes", "'3.14.4'  # the host patch")):
        check(check_id, evaluate(_fixture(base, workflow=_workflow(((OPF, PIN, "python-version: " + pin),))),
                                 HOST)[0], 0)
    check("jobs/extra-step-after-gate-passes", evaluate(_fixture(base, workflow=_workflow(((
        OPF, GATE_STEP, GATE_STEP + "      - name: Python host pin self-test\n        run: " + GATE_RUN
        + " --self-test\n\n"),))), HOST)[0], 0)
    # The top-level concurrency: block quality.yml carries (check_ci_parity.py pins its content): red with
    # concurrency dropped from TOP_KEYS, where it is a workflow-keys finding.
    check("jobs/workflow-concurrency-passes", evaluate(_fixture(base, workflow=_workflow(((
        None, "jobs:\n", "concurrency:\n  group: quality-${{ github.head_ref || github.run_id }}\n"
        "  cancel-in-progress: true\n\njobs:\n"),))), HOST)[0], 0)
    check("jobs/comment-line-passes", evaluate(_fixture(base, workflow=_workflow(((
        GFE, "    steps:\n", "    # the steps\n    steps:\n"),))), HOST)[0], 0)
    check("pins/other-job-pin-ignored-passes", evaluate(_fixture(base, workflow=_workflow().replace(
        "python-version: '3.14'\n", "python-version: '3.13'\n")), HOST)[0], 0)
    root = _fixture(base)
    _write(root, WORKFLOW_REL, (root / WORKFLOW_REL).read_bytes().replace(b"\n", b"\r\n"))
    check("pins/crlf-workflow-passes", evaluate(root, HOST)[0], 0)
    strict = "is not one strict pin line"
    for check_id, pin in (
            ("pins/expression-pin-cannot-evaluate", "${{ matrix.python }}"),
            ("pins/flow-pin-cannot-evaluate", "['3.14.4']"),
            ("pins/upper-case-key-cannot-evaluate", "'3.14.4'\n          PYTHON-VERSION: '3.14.8'"),
            ("pins/comment-naming-key-cannot-evaluate",
             "'3.14.4'\n      # python-version: '3.14.8'"),
            ("pins/version-file-cannot-evaluate",
             "'3.14.4'\n          python-version-file: .python-version")):
        code, lines = evaluate(_fixture(base, workflow=_workflow(((OPF, PIN, "python-version: " + pin),))),
                               HOST)
        check(check_id, (code, _has(lines, strict)), (2, True))
    model = "this job model cannot read it"
    for check_id, edits, marker in (
            ("jobs/block-scalar-run-cannot-evaluate",
             ((OPF, "run: " + HOST_SUITES[OPF][0], "run: |\n          " + HOST_SUITES[OPF][0]),), model),
            ("jobs/flow-runner-cannot-evaluate", ((GFE, "runs-on: " + RUNNER, "runs-on: [" + RUNNER + "]"),),
             model),
            ("jobs/colon-in-plain-value-cannot-evaluate",
             ((OPF, "    name: " + OPF + "\n", "    name: a: b\n"),), model),
            ("jobs/repeated-key-cannot-evaluate",
             ((OPF, "    runs-on: ", "    runs-on: ubuntu-latest\n    runs-on: "),),
             "a repeated key runs-on"),
            ("jobs/misplaced-line-cannot-evaluate", ((GFE, "    steps:\n", "    steps:\n     x: 1\n"),),
             "the indentation does not place"),
            ("jobs/deep-line-cannot-evaluate",
             ((OPF, "          fetch-depth: 0\n", "          fetch-depth: 0\n            x: 1\n"),),
             "nested deeper than a step input")):
        code, lines = evaluate(_fixture(base, workflow=_workflow(edits)), HOST)
        check(check_id, (code, _has(lines, marker)), (2, True))
    for check_id, workflow, marker in (
            ("pins/repeated-job-cannot-evaluate",
             "jobs:\n" + _host_job(OPF) + _host_job(GFE) + _host_job(OPF),
             "the job id opf-host-patch repeats"),
            ("pins/tab-indent-cannot-evaluate", "jobs:\n\topf-host-patch:\n",
             "this line model cannot place it"),
            ("pins/quoted-job-id-cannot-evaluate", "jobs:\n  \"opf-host-patch\":\n",
             "this line model cannot place it"),
            ("pins/indent-three-cannot-evaluate", "jobs:\n" + _host_job(OPF) + "   x: 1\n",
             "this line model cannot place it"),
            ("pins/repeated-top-level-key-cannot-evaluate", _workflow() + "jobs:\n" + FLOOR_JOB,
             "the top-level key jobs repeats"),
            ("pins/repeated-permissions-cannot-evaluate", _workflow() + "permissions:\n  contents: write\n",
             "the top-level key permissions repeats"),
            ("pins/quoted-top-level-key-cannot-evaluate",
             _workflow(((None, "jobs:\n", "\"defaults\":\n  run:\n    shell: echo {0}\n\njobs:\n"),)),
             "a top-level line that is not a plain key"),
            ("pins/explicit-top-level-key-cannot-evaluate",
             _workflow(((None, "jobs:\n", "? defaults\n: {run: {shell: echo {0}}}\n\njobs:\n"),)),
             "a top-level line that is not a plain key")):
        code, lines = evaluate(_fixture(base, workflow=workflow), HOST)
        check(check_id, (code, _has(lines, marker)), (2, True))
    # A line-break character hides a line from the "\n" split: here an if: false on the suite step, written
    # with a tab after its colon. Each is cannot-evaluate, and a copy of this gate without the refusal reads
    # the hidden text as the step's name and passes (red on revert).
    source = _read_text(Path(__file__).resolve())
    anchor = "for char, label in " + "LINE_BREAKS.items():"
    if source.count(anchor) != 1:
        raise CannotEvaluate("red-on-revert anchor for the line-break refusal occurs {} times".format(
            source.count(anchor)))
    breaks = _copy(base, "python_host_pin_without_line_breaks",
                   source.replace(anchor, "for char, label in ():"))
    suite_name = "      - name: Suite\n"
    for check_id, char in (
            ("jobs/carriage-return-cannot-evaluate", "\r"),
            ("jobs/next-line-cannot-evaluate", "\x85"),
            ("jobs/line-separator-cannot-evaluate", "\u2028"),
            ("jobs/paragraph-separator-cannot-evaluate", "\u2029")):
        root = _fixture(base, workflow=_workflow(((OPF, suite_name, suite_name[:-1] + char
                                                   + "        if:\tfalse\n"),)))
        code, lines = evaluate(root, HOST)
        check(check_id, (code, _has(lines, "carries " + LINE_BREAKS[char]), breaks.evaluate(root, HOST)[0]),
              (2, True, 0))
    # A form feed or a vertical tab in a host-patch-job line: cannot-evaluate (CONTROL_CHARS), and a copy
    # of this gate without the refusal reads each line as part of the job and passes (red on revert).
    anchor = "for char, label in " + "CONTROL_CHARS.items():"
    if source.count(anchor) != 1:
        raise CannotEvaluate("red-on-revert anchor for the control-character refusal occurs {} "
                             "times".format(source.count(anchor)))
    controls = _copy(base, "python_host_pin_without_control_chars",
                     source.replace(anchor, "for char, label in ():"))
    for check_id, char in (
            ("jobs/form-feed-cannot-evaluate", "\f"),
            ("jobs/vertical-tab-cannot-evaluate", "\v")):
        root = _fixture(base, workflow=_workflow(((OPF, "    name: " + OPF + "\n",
                                                   "    name: " + OPF + char + " tooling\n"),)))
        code, lines = evaluate(root, HOST)
        check(check_id, (code, _has(lines, "carries " + CONTROL_CHARS[char]),
                         controls.evaluate(root, HOST)[0]), (2, True, 0))

    # A form feed or a vertical tab in an accepted host-patch-job header line: cannot-evaluate before
    # the header's continue, and the copy without the refusal reads each header, its comment included,
    # and passes (red on revert).
    for check_id, job, char in (
            ("jobs/opf-header-form-feed-cannot-evaluate", OPF, "\f"),
            ("jobs/opf-header-vertical-tab-cannot-evaluate", OPF, "\v"),
            ("jobs/git-fixture-env-header-form-feed-cannot-evaluate", GFE, "\f"),
            ("jobs/git-fixture-env-header-vertical-tab-cannot-evaluate", GFE, "\v")):
        root = _fixture(base, workflow=_workflow(((job, "  " + job + ":\n",
                                                   "  " + job + ": #" + char + "\n"),)))
        code, lines = evaluate(root, HOST)
        check(check_id, (code, _has(lines, "carries " + CONTROL_CHARS[char]),
                         controls.evaluate(root, HOST)[0]), (2, True, 0))

    # A form feed or a vertical tab in a line of any job outside the host-patch jobs, its accepted
    # header and its comment lines included: cannot-evaluate, as for the host-patch jobs, and the copy
    # without the refusal reads the job and passes (red on revert).
    for check_id, edits, char in (
            ("jobs/jobs-only-header-form-feed-cannot-evaluate",
             (("registry", "  registry:\n", "  registry: #\f\n"),), "\f"),
            ("jobs/jobs-only-comment-form-feed-cannot-evaluate",
             (("registry", "    steps:\n", "    # judge\f notes\n    steps:\n"),), "\f"),
            ("jobs/jobs-only-name-vertical-tab-cannot-evaluate",
             (("registry", "Python host pin (jobs leg", "Python host pin\v (jobs leg"),), "\v"),
            # A second job whose header comment carries a form feed: every outside job's lines, its
            # header included, are refused for control characters, so the character is never read
            # past, whatever the header names.
            ("jobs/jobs-only-header-mention-form-feed-cannot-evaluate",
             (("registry", JUDGE_STEP,
               JUDGE_STEP + "  second: # --jobs-only\f\n    runs-on: ubuntu-latest\n"
               "    steps:\n      - run: echo ok\n"),), "\f")):
        root = _fixture(base, workflow=_workflow(edits))
        code, lines = evaluate(root, HOST)
        check(check_id, (code, _has(lines, "carries " + CONTROL_CHARS[char]),
                         controls.evaluate(root, HOST)[0]), (2, True, 0))

    # Every job outside the host-patch jobs is read whole with the host-patch jobs' own line model,
    # extended by block scalars, colon-free flow sequences and bare list items: a job property at an
    # unexpected indent (the job body re-indented by two spaces with a job-level env:, its
    # setup-python with: dropped so every line stays within MAX_INDENT and the placement refusal is
    # what fires), a quoted key YAML reads as env (on the job and on the judge
    # step), an explicit ? key, a quoted run key, a flow-mapping step and a block opener carrying an
    # indentation indicator are each cannot-evaluate, and the copy with the outside-judge rule removed
    # never reads the job and passes (red on revert). A run: carrying a ${{ }} expression, a run: shlex
    # cannot tokenize, an upper-case Env: key, a backslash spelling (a hex escape, an escaped line
    # break, a backslash in a header comment) and an alias consuming a top-level anchor are
    # cannot-evaluate in EVERY outside job: the backslash and anchor/alias/tag refusals cover the
    # accepted headers and the block-scalar bodies too.
    judge_env = "      BASH_ENV: '$(: > \"$0\")'\n"
    trimmed = REGISTRY_JOB.replace("        with:\n          python-version: '3.14'\n", "")
    reindented = ("  registry:\n      env:\n  " + judge_env
                  + "".join("  " + text + "\n" for text in trimmed.split("\n", 1)[1].splitlines()))
    quoted_job_env = '    "env":\n' + judge_env + "    runs-on: "
    quoted_step_env = JUDGE_STEP + '        "env":\n    ' + judge_env
    explicit_key = "    ? env\n    : silencer\n    runs-on: "
    second = "  second:\n    runs-on: ubuntu-latest\n    steps:\n"
    for check_id, edits, marker in (
            ("jobs/jobs-only-job-reindented-cannot-evaluate",
             (("registry", REGISTRY_JOB, reindented),), "the indentation does not place"),
            ("jobs/jobs-only-job-quoted-env-cannot-evaluate",
             (("registry", "    runs-on: ", quoted_job_env),), "not a block mapping entry"),
            ("jobs/jobs-only-step-quoted-env-cannot-evaluate",
             (("registry", JUDGE_STEP, quoted_step_env),), "not a block mapping entry"),
            ("jobs/jobs-only-job-explicit-key-cannot-evaluate",
             (("registry", "    runs-on: ", explicit_key),), "not a block mapping entry"),
            ("jobs/jobs-only-quoted-key-second-judge-cannot-evaluate",
             (("registry", JUDGE_STEP,
               JUDGE_STEP + second + '      - "run": ' + JOBS_ONLY_RUN + "\n"),),
             "not a block mapping entry"),
            ("jobs/jobs-only-flow-step-second-judge-cannot-evaluate",
             (("registry", JUDGE_STEP,
               JUDGE_STEP + second + "      - {name: judge, run: " + JOBS_ONLY_RUN + "}\n"),),
             "not a block mapping entry"),
            ("jobs/jobs-only-anchor-alias-second-judge-cannot-evaluate",
             (("registry", JUDGE_STEP,
               JUDGE_STEP + second + "      - run: &judge " + JOBS_ONLY_RUN + "\n"
               "      - run: *judge\n"),),
             "opens a YAML anchor, alias or tag"),
            ("jobs/jobs-only-indentation-indicator-block-cannot-evaluate",
             (("registry", JUDGE_STEP,
               JUDGE_STEP + second + "      - run: |2\n          echo ok\n"),),
             "not a block mapping entry"),
            ("jobs/jobs-only-matrix-run-expression-cannot-evaluate",
             (("registry", JUDGE_STEP,
               JUDGE_STEP + "  second:\n    runs-on: ubuntu-latest\n    strategy:\n      matrix:\n"
               "        command: '" + JOBS_ONLY_RUN + "'\n    steps:\n"
               "      - run: ${{ matrix.command }}\n"),),
             "carries a ${{ }} expression"),
            ("jobs/jobs-only-unclosed-quote-run-cannot-evaluate",
             (("registry", JUDGE_STEP,
               JUDGE_STEP + second + "      - run: " + JOBS_ONLY_RUN + " 'x\n"),),
             "shlex cannot tokenize"),
            ("jobs/jobs-only-upper-case-env-key-cannot-evaluate",
             (("registry", "    runs-on: ", "    Env:\n      SAMPLE: value\n    runs-on: "),),
             "carries an upper-case letter"),
            ("jobs/jobs-only-escaped-run-cannot-evaluate",
             (("registry", JUDGE_STEP,
               JUDGE_STEP + second
               + "      - run: \"python3 -I -B tools/check_python_host_pi\\x6E.py"
               " --jobs-o\\x6Ely\"\n"),),
             "carries a backslash"),
            ("jobs/jobs-only-escaped-break-run-cannot-evaluate",
             (("registry", JUDGE_STEP,
               JUDGE_STEP + second + "      - run: \"python3 -I -B tools/check_python_host_\\\n"
               "          pin.py --jobs-\\\n          only\"\n"),),
             "carries a backslash"),
            ("jobs/jobs-only-header-backslash-cannot-evaluate",
             (("registry", JUDGE_STEP,
               JUDGE_STEP + "  second: # a header comment with a backslash \\\n"
               "    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n"),),
             "carries a backslash"),
            ("jobs/jobs-only-alias-run-cannot-evaluate",
             ((None, "name: Quality\n", "name: &judge " + JOBS_ONLY_RUN + "\n"),
              ("registry", JUDGE_STEP, JUDGE_STEP + second + "      - run: *judge\n")),
             "opens a YAML anchor, alias or tag"),
            # A folded (>) run: block is cannot-evaluate, never decoded (the canonical
            # judge and a commented-out judge inside one included); a ${{ }} expression and a
            # backslash on a #-leading literal-block body line are refused like any other body line;
            # and the real parsed value type of a run: (a flow sequence, an explicit null in both
            # spellings, a boolean, a number) is refused by its named kind, never read as a string
            # this gate invents.
            ("jobs/jobs-only-folded-block-second-judge-cannot-evaluate",
             (("registry", JUDGE_STEP,
               JUDGE_STEP + second + "      - run: >-\n          " + JOBS_ONLY_RUN + "\n"),),
             "a folded (>) block scalar"),
            ("jobs/jobs-only-folded-comment-judge-cannot-evaluate",
             (("registry", "run: " + JOBS_ONLY_RUN,
               "run: >\n          # disabled\n          " + JOBS_ONLY_RUN),),
             "a folded (>) block scalar"),
            ("jobs/jobs-only-comment-expression-block-cannot-evaluate",
             (("registry", JUDGE_STEP,
               JUDGE_STEP + second + "      - run: |\n          # ${{ matrix.command }}\n"
               "          echo ok\n"),),
             "carries a ${{ }} expression"),
            ("jobs/jobs-only-comment-backslash-block-cannot-evaluate",
             (("registry", JUDGE_STEP,
               JUDGE_STEP + second + "      - run: |\n          # a note \\\n          echo ok\n"),),
             "carries a backslash"),
            ("jobs/jobs-only-flow-run-cannot-evaluate",
             (("registry", JUDGE_STEP, JUDGE_STEP + second + "      - run: [echo, ok]\n"),),
             "a flow sequence"),
            ("jobs/jobs-only-null-word-run-cannot-evaluate",
             (("registry", JUDGE_STEP, JUDGE_STEP + second + "      - run: null\n"),),
             "an explicit null"),
            ("jobs/jobs-only-tilde-run-cannot-evaluate",
             (("registry", JUDGE_STEP, JUDGE_STEP + second + "      - run: ~\n"),),
             "an explicit null"),
            ("jobs/jobs-only-boolean-run-cannot-evaluate",
             (("registry", JUDGE_STEP, JUDGE_STEP + second + "      - run: true\n"),),
             "a boolean"),
            ("jobs/jobs-only-number-run-cannot-evaluate",
             (("registry", JUDGE_STEP, JUDGE_STEP + second + "      - run: 123\n"),),
             "a number")):
        root = _fixture(base, workflow=_workflow(edits))
        code, lines = evaluate(root, HOST)
        rules.JOB_RULES = JOB_RULES - {"outside-judge"}
        try:
            removed = rules.evaluate(root, HOST)[0]
        finally:
            rules.JOB_RULES = JOB_RULES
        check(check_id, (code, _has(lines, marker), removed), (2, True, 0))

    # The env and judge-step-key diagnostics carry exact line numbers, the
    # before-the-judge wording is its own, every judge-carrying job reports, the run: classification
    # reads token shape (not only the literal fallback), the carrier set holds judge-shaped steps as
    # well as counted judges, and the value scan reads this gate's file name as well as the flag. Each
    # _mutant copy below is a targeted regression of one of those properties, and each vector holds it
    # red.
    env_line_mutant = _mutant(base, "python_host_pin_env_line_mutant", source,
                              (("tree.lines" + '["env"]', "0"),
                               ("number = step.lines" + '["env"]', "number = 0"),
                               ("step.lines" + "[key]", "0")))
    wording_mutant = _mutant(base, "python_host_pin_before_wording_mutant", source,
                             (("elif position < " + "max(indexes):", "elif False:"),))
    carrier_mutant = _mutant(base, "python_host_pin_single_carrier_mutant", source,
                             (("for job, indexes in " + "carriers.items():",
                               "for job, indexes in list" + "(carriers.items())[:1]:"),))
    token_shape_mutant = _mutant(base, "python_host_pin_token_shape_mutant", source,
                                 (("elif any(_judge_shaped(tokens) for tokens in groups) or "
                                   + "_judge_text(value):",
                                   "elif " + "_judge_text(value):"),))
    carrier_set_mutant = _mutant(base, "python_host_pin_counted_only_carrier_mutant", source,
                                 (("for job, index in " + "judges + shaped:",
                                   "for job, index in " + "judges:"),))
    value_scan_mutant = _mutant(base, "python_host_pin_flag_only_text_mutant", source,
                                (("GATE_BASENAME in text or " + "JOBS_ONLY_FLAG in text",
                                  "JOBS_ONLY" + "_FLAG in text"),))

    def line_of(workflow, text):
        return workflow.split("\n").index(text) + 1

    workflow = _workflow((("registry", "    runs-on: ", "    env:\n      SAMPLE: value\n    runs-on: "),))
    marker = ("{}:{}: the job registry carries the --jobs-only judge step and sets a job-level "
              "env:".format(WORKFLOW_REL, line_of(workflow, "    env:")))
    root = _fixture(base, workflow=workflow)
    code, lines = evaluate(root, HOST)
    check("jobs/jobs-only-job-env-line-finding",
          (code, _has(lines, marker), _has(env_line_mutant.evaluate(root, HOST)[1], marker)),
          (1, True, False))

    step_uses = "      - uses: actions/setup-python@v5\n"
    workflow = _workflow((("registry", step_uses, step_uses + "        env:\n          SAMPLE: value\n"),))
    line_marker = ("{}:{}: the job registry carries the --jobs-only judge and sets env: on step "
                   "2".format(WORKFLOW_REL, line_of(workflow, "        env:")))
    wording_marker = ("a write to GITHUB_ENV or GITHUB_PATH through it reaches every later step of "
                      "the job, the judge step included")
    root = _fixture(base, workflow=workflow)
    code, lines = evaluate(root, HOST)
    check("jobs/jobs-only-before-judge-env-line-finding",
          (code, _has(lines, line_marker), _has(lines, wording_marker),
           _has(env_line_mutant.evaluate(root, HOST)[1], line_marker),
           _has(wording_mutant.evaluate(root, HOST)[1], wording_marker)),
          (1, True, True, False, False))

    workflow = _workflow((("registry", JUDGE_STEP, JUDGE_STEP + "        env:\n          SAMPLE: value\n"),))
    marker = "{}:{}: the step that carries the --jobs-only judge sets env:".format(
        WORKFLOW_REL, line_of(workflow, "        env:"))
    root = _fixture(base, workflow=workflow)
    code, lines = evaluate(root, HOST)
    check("jobs/jobs-only-on-judge-env-line-finding",
          (code, _has(lines, marker), _has(env_line_mutant.evaluate(root, HOST)[1], marker)),
          (1, True, False))

    workflow = _workflow((("registry", JUDGE_STEP,
                           JUDGE_STEP + "      - name: Later step\n        run: echo done\n"
                           "        env:\n          SAMPLE: value\n"),))
    marker = ("{}:{}: the job registry carries the --jobs-only judge and sets env: on step 4, after "
              "the judge step".format(WORKFLOW_REL, line_of(workflow, "        env:")))
    root = _fixture(base, workflow=workflow)
    code, lines = evaluate(root, HOST)
    check("jobs/jobs-only-after-judge-env-line-finding",
          (code, _has(lines, marker), _has(env_line_mutant.evaluate(root, HOST)[1], marker)),
          (1, True, False))

    workflow = _workflow((("registry", JUDGE_STEP, JUDGE_STEP + "        if: false\n"),))
    marker = "{}:{}: the step that carries the --jobs-only judge carries if:".format(
        WORKFLOW_REL, line_of(workflow, "        if: false"))
    root = _fixture(base, workflow=workflow)
    code, lines = evaluate(root, HOST)
    check("jobs/jobs-only-step-if-line-finding",
          (code, _has(lines, marker), _has(env_line_mutant.evaluate(root, HOST)[1], marker)),
          (1, True, False))

    # A present run: whose value is not one string (a null value, a nested mapping), in any outside
    # job: cannot-evaluate at its recorded key line, never skipped, and the copy with the
    # outside-judge rule removed never reads the job (red on revert).
    for check_id, body in (
            ("jobs/jobs-only-null-run-cannot-evaluate", "      - run:\n"),
            ("jobs/jobs-only-mapping-run-cannot-evaluate", "      - run:\n          foo: bar\n")):
        workflow = _workflow((("registry", JUDGE_STEP, JUDGE_STEP + second + body),))
        marker = "{}:{}: a run: key of the job second whose value is not one string".format(
            WORKFLOW_REL, line_of(workflow, "      - run:"))
        root = _fixture(base, workflow=workflow)
        code, lines = evaluate(root, HOST)
        rules.JOB_RULES = JOB_RULES - {"outside-judge"}
        try:
            removed = rules.evaluate(root, HOST)[0]
        finally:
            rules.JOB_RULES = JOB_RULES
        check(check_id, (code, _has(lines, marker), removed), (2, True, 0))

    second_judge = ("  second:\n    env:\n      OTHER: value\n    runs-on: ubuntu-latest\n    steps:\n"
                    "      - name: Second judge\n        run: " + JOBS_ONLY_RUN + "\n")
    workflow = _workflow((("registry", "    runs-on: ", "    env:\n      SAMPLE: value\n    runs-on: "),
                          ("registry", JUDGE_STEP, JUDGE_STEP + second_judge)))
    root = _fixture(base, workflow=workflow)
    code, lines = evaluate(root, HOST)
    first = "the job registry carries the --jobs-only judge step and sets a job-level env:"
    other = "the job second carries the --jobs-only judge step and sets a job-level env:"
    check("jobs/jobs-only-two-carrier-jobs-env-finding",
          (code, _has(lines, first), _has(lines, other),
           _has(carrier_mutant.evaluate(root, HOST)[1], other)),
          (1, True, True, False))

    # The token classification and the carrier set, each red under a targeted mutant: a quote-spliced
    # flag-free gate run (no literal substring names this gate) in a job with a job-level env: is an
    # unresolved judge-shaped command AND an env-checked carrier; the copy whose classification reads
    # only the literal fallback passes the job whole, and the copy whose carrier set holds only
    # counted judges keeps the unresolved finding but loses the env diagnostic.
    carrier_job = ("  second:\n    env:\n      SAMPLE: value\n    runs-on: ubuntu-latest\n    steps:\n"
                   "      - name: Extra gate\n"
                   "        run: python3 -I -B tools/check_python_host_'pin'.py\n")
    root = _fixture(base, workflow=_workflow((("registry", JUDGE_STEP, JUDGE_STEP + carrier_job),)))
    code, lines = evaluate(root, HOST)
    unresolved = "an unresolved judge-shaped command in the job second"
    carrier_env = "the job second carries an unresolved judge-shaped step and sets a job-level env:"
    check("jobs/jobs-only-spliced-env-carrier-finding",
          (code, _has(lines, unresolved), _has(lines, carrier_env)), (1, True, True))
    check("jobs/jobs-only-token-shape-mutant-red", token_shape_mutant.evaluate(root, HOST)[0], 0)
    code, lines = carrier_set_mutant.evaluate(root, HOST)
    check("jobs/jobs-only-carrier-set-mutant-red",
          (code, _has(lines, unresolved), _has(lines, carrier_env)), (1, True, False))

    # The value scan reads this gate's file name as well as the flag: a flag-free literal gate command
    # in a matrix value is a value finding, and the copy whose _judge_text reads only the flag passes
    # it (red on revert).
    root = _fixture(base, workflow=_workflow((("registry", JUDGE_STEP,
                                               JUDGE_STEP + "  second:\n    runs-on: ubuntu-latest\n"
                                               "    strategy:\n      matrix:\n        command: '"
                                               + GATE_RUN + "'\n    steps:\n      - run: echo ok\n"),)))
    code, lines = evaluate(root, HOST)
    check("jobs/jobs-only-value-scan-mutant-red",
          (code, _has(lines, "in a value that is not a step's own run: command"),
           value_scan_mutant.evaluate(root, HOST)[0]), (1, True, 0))

    # The MAX_INDENT bound on the three extended entry kinds (a block
    # opener, a flow sequence, a bare list item nested deeper than a step input): each vector is
    # cannot-evaluate through deep() in _outside_entries, and the copy with that bound removed reads
    # each and passes (red on revert).
    deep_bound_mutant = _mutant(base, "python_host_pin_deep_bound_mutant", source,
                                (("def deep(number, indent):\n        if indent > " + "MAX_INDENT:",
                                  "def deep(number, indent):\n        if " + "False:"),))
    nested = "      - uses: actions/checkout@v4\n        with:\n          nested:\n"
    for check_id, entry in (
            ("jobs/jobs-only-deep-block-cannot-evaluate",
             "            inner: |\n              echo ok\n"),
            ("jobs/jobs-only-deep-flow-cannot-evaluate", "            inner: [a, b]\n"),
            ("jobs/jobs-only-deep-bare-item-cannot-evaluate", "            - item\n")):
        root = _fixture(base, workflow=_workflow((("registry", JUDGE_STEP,
                                                   JUDGE_STEP + second + nested + entry),)))
        code, lines = evaluate(root, HOST)
        check(check_id, (code, _has(lines, "nested deeper than a step input"),
                         deep_bound_mutant.evaluate(root, HOST)[0]), (2, True, 0))

    # A run: that is not a step's own (here a with: input named run) is tokenized and classified, and
    # a flow-mapping step stays outside FLOW_RE's colon-free grammar: each red under a targeted
    # mutant. The spliced spellings name no literal substring, so the copy that no longer walks
    # _other_runs passes the with: input unread, and the copy whose FLOW_RE accepts any bracket text
    # reads the step list as one opaque scalar and passes it unread.
    other_runs_mutant = _mutant(base, "python_host_pin_no_other_runs_mutant", source,
                                (("for number, value in _other_runs" + "(tree, runs):",
                                  "for number, value in " + "():"),))
    flow_grammar_mutant = _mutant(base, "python_host_pin_wide_flow_mutant", source,
                                  (("\\[[A-Za-z0-9_. ,/@=+-]*" + "\\]", "\\[[^]]*" + "\\]"),))
    with_run = ("      - uses: actions/checkout@v4\n        with:\n"
                "          run: python3 -I -B tools/check_python_host_\"pin\".py\n")
    root = _fixture(base, workflow=_workflow((("registry", JUDGE_STEP,
                                               JUDGE_STEP + second + with_run),)))
    code, lines = evaluate(root, HOST)
    check("jobs/jobs-only-spliced-with-run-input-finding",
          (code, _has(lines, "in a value that is not a step's own run: command"),
           other_runs_mutant.evaluate(root, HOST)[0]), (1, True, 0))
    flow_steps = ("  second:\n    runs-on: ubuntu-latest\n    steps:"
                  " [{run: python3 -I -B tools/check_python_host_\"pin\".py --jobs-o\"\"nly}]\n")
    root = _fixture(base, workflow=_workflow((("registry", JUDGE_STEP, JUDGE_STEP + flow_steps),)))
    code, lines = evaluate(root, HOST)
    check("jobs/jobs-only-flow-mapping-steps-cannot-evaluate",
          (code, _has(lines, "not a block mapping entry"),
           flow_grammar_mutant.evaluate(root, HOST)[0]), (2, True, 0))

    # The host-patch jobs' judge-flag read is raw text: a quote-spliced --jobs-only in an extra
    # host-patch-job step (no literal line names the flag) is a finding, and the copy that no longer
    # scans host-patch run: values passes it (red on revert).
    host_scan_mutant = _mutant(base, "python_host_pin_no_host_token_scan_mutant", source,
                               (("findings.extend(_host_flag_findings" + "(name, tree))",
                                 "findings.extend(" + "())"),))
    spliced_host = ("      - name: Extra step\n"
                    "        run: python3 -I -B tools/check_python_host_pin.py --jobs-o\"\"nly\n")
    root = _fixture(base, workflow=_workflow(((OPF, _suite_step(OPF),
                                               _suite_step(OPF) + spliced_host),)))
    code, lines = evaluate(root, HOST)
    check("jobs/host-job-spliced-flag-raw-text-finding",
          (code, _has(lines, "a run: command of the host-patch job " + OPF + " names "
                      + JOBS_ONLY_STEM + " once every quote"),
           host_scan_mutant.evaluate(root, HOST)[0]), (1, True, 0))

    # The host-patch flag read is raw text, never shlex token equality: every quote, backslash and
    # dollar character is deleted from each line of the parsed run: scalar, and a result naming
    # JOBS_ONLY_STEM anywhere is a finding. A quote splice whose shlex token is not the exact flag
    # (echo "--jobs-o here") is red under the token-equality mutant (a token-equality read, which the
    # quoted argument defeats); the plain spelling carries the literal flag, which the literal line
    # scan (host_judges) refuses as well, so the mutant stays exit 1 on it through that scan: it is
    # listed as a vector that does not discriminate the raw-text read.
    token_equality_mutant = _mutant(
        base, "python_host_pin_token_equality_mutant", source,
        (('if any(_names_flag_text(text) for text in value.split("\\n")):',
          "if any(JOBS_ONLY_FLAG in tokens for tokens in "
          "_run_line_tokens(job, value)):"),))
    raw_flag_marker = ("a run: command of the host-patch job " + OPF + " names " + JOBS_ONLY_STEM
                       + " once every quote")
    for check_id, spelling, equality_code in (
            ("jobs/host-job-quote-spliced-argument-flag-finding", 'echo "--jobs-o here"', 0),
            ("jobs/host-job-plain-flag-raw-text-finding", "echo --jobs-only", 1)):
        extra = "      - name: Extra step\n        run: " + spelling + "\n"
        root = _fixture(base, workflow=_workflow(((OPF, _suite_step(OPF),
                                                   _suite_step(OPF) + extra),)))
        code, lines = evaluate(root, HOST)
        check(check_id, (code, _has(lines, raw_flag_marker),
                         token_equality_mutant.evaluate(root, HOST)[0]), (1, True, equality_code))

    # The host-patch run: lines are tokenized for their refusals (a disclosed exit-2 path of the
    # host-patch jobs): a ${{ }} expression in an extra host-patch-job step is cannot-evaluate
    # with this job's own wording, and the copy that no longer scans host-patch run: values passes
    # it (red on revert).
    root = _fixture(base, workflow=_workflow(((OPF, _suite_step(OPF), _suite_step(OPF)
                                               + "      - name: Extra step\n"
                                               "        run: echo ${{ github.sha }}\n"),)))
    code, lines = evaluate(root, HOST)
    check("jobs/host-job-expression-run-cannot-evaluate",
          (code, _has(lines, "a run: command of the host-patch job " + OPF
                      + " carries a ${{ }} expression"),
           host_scan_mutant.evaluate(root, HOST)[0]), (2, True, 0))

    # A host-patch run: line is held to a CLOSED safe-character grammar, not a list of refused
    # openers: any character outside HOST_RUN_SAFE makes the line cannot-evaluate (exit 2, the
    # character named). A list of refused openers leaves pathname expansion and process substitution
    # unread (the [o] glob, the * and ? globs and source <(...) among them, HOST_RUN_SAFE above); the
    # grammar refuses each by the character it must spell, and refuses the other expansion forms
    # here (the eval spelled with a $' opener, the ANSI-C octal and hex quotes, the brace sequence,
    # the backquote and $( substitutions and the dollar-single-quote --jobs-o$'n'ly) by their
    # refused character too. An eval spelled only in safe characters needs no refused character and
    # is not refused by the grammar (the deletion read still reads its line). grammar_removed_mutant
    # drops the whole character refusal, so each vector is red under it, exit 0 where the raw text
    # does not spell the stem (the [o] glob, the process substitution, the eval and the ANSI-C,
    # brace, backquote and $( forms) and exit 1 where the deletion read still finds it (echo
    # --jobs-o*, echo --jobs-o?ly and --jobs-o$'n'ly).
    grammar_removed_mutant = _mutant(
        base, "python_host_pin_no_run_grammar_mutant", source,
        (("named = _refused_run_char" + "(text)", "named = None"),))
    for check_id, spelling, named, removed_code in (
            ("jobs/host-job-glob-bracket-flag-cannot-evaluate", "echo --jobs-[o]nly",
             "an open bracket ([)", 0),
            ("jobs/host-job-glob-star-flag-cannot-evaluate", "echo --jobs-o*",
             "an asterisk (*)", 1),
            ("jobs/host-job-glob-question-flag-cannot-evaluate", "echo --jobs-o?ly",
             "a question mark (?)", 1),
            ("jobs/host-job-process-substitution-flag-cannot-evaluate",
             "source <(printf 'echo --jobs-%bnly' '\\157')", "a less-than sign (<)", 0),
            ("jobs/host-job-eval-refused-char-flag-cannot-evaluate", "eval echo --jobs-$'\\157'nly",
             "a dollar sign ($)", 0),
            ("jobs/host-job-ansi-c-octal-flag-cannot-evaluate", "echo --jobs-$'\\157'nly",
             "a dollar sign ($)", 0),
            ("jobs/host-job-ansi-c-hex-flag-cannot-evaluate", "echo --jobs-$'\\x6f'nly",
             "a dollar sign ($)", 0),
            ("jobs/host-job-brace-sequence-flag-cannot-evaluate", "echo --jobs-{o..o}nly",
             "an open brace ({)", 0),
            ("jobs/host-job-backquote-flag-cannot-evaluate", "echo --jobs-`printf o`nly",
             "a backquote (`)", 0),
            ("jobs/host-job-dollar-paren-flag-cannot-evaluate", "echo --jobs-$(printf o)nly",
             "a dollar sign ($)", 0),
            ("jobs/host-job-dollar-single-quote-flag-cannot-evaluate", "echo --jobs-o$'n'ly",
             "a dollar sign ($)", 1)):
        extra = "      - name: Extra step\n        run: " + spelling + "\n"
        root = _fixture(base, workflow=_workflow(((OPF, _suite_step(OPF),
                                                   _suite_step(OPF) + extra),)))
        code, lines = evaluate(root, HOST)
        check(check_id, (code, _has(lines, named),
                         grammar_removed_mutant.evaluate(root, HOST)[0]), (2, True, removed_code))

    # A host-patch run: line whose first word is a shell assignment is cannot-evaluate (exit 2, the
    # assignment named): a safe-character PATH= prefix re-points which file the named command runs
    # (the closed grammar alone passes it), and so does one after a time -p prefix word, and a +=
    # append. Each is red under assignment_removed_mutant (exit 0). The control after them is
    # ACCEPTANCE ONLY: an assignment-shaped ARGUMENT after the command word is no prefix and passes
    # (exit 0); the refusal reads the first word alone.
    assignment_removed_mutant = _mutant(
        base, "python_host_pin_no_run_assignment_mutant", source,
        (("assigned = _leading_assignment" + "(text)", "assigned = None"),))
    for check_id, spelling, named in (
            ("jobs/host-job-path-assignment-cannot-evaluate",
             "PATH=tools:/usr/bin " + GATE_RUN, "the shell assignment PATH=tools:/usr/bin"),
            ("jobs/host-job-time-prefixed-assignment-cannot-evaluate",
             "time -p PATH=tools:/usr/bin " + GATE_RUN, "the shell assignment PATH=tools:/usr/bin"),
            ("jobs/host-job-append-assignment-cannot-evaluate",
             "PATH+=:tools " + GATE_RUN, "the shell assignment PATH+=:tools")):
        extra = "      - name: Extra step\n        run: " + spelling + "\n"
        root = _fixture(base, workflow=_workflow(((OPF, _suite_step(OPF),
                                                   _suite_step(OPF) + extra),)))
        code, lines = evaluate(root, HOST)
        check(check_id, (code, _has(lines, named),
                         assignment_removed_mutant.evaluate(root, HOST)[0]), (2, True, 0))
    extra = "      - name: Extra step\n        run: " + GATE_RUN + " X=1\n"
    root = _fixture(base, workflow=_workflow(((OPF, _suite_step(OPF), _suite_step(OPF) + extra),)))
    check("jobs/host-job-assignment-argument-passes", evaluate(root, HOST)[0], 0)

    # Acceptance controls read from the committed .github/workflows/quality.yml (ROOT), not from
    # module constants: its host-patch jobs carry eight run: lines with seven distinct texts (the gate
    # line in each job, the gate self-test line, the OPF suite line and the four git-fixture-env suite
    # lines), and each of them passes the closed grammar and the assignment refusal unchanged. Each
    # check holds its texts: how many committed lines carry each, and what each refusal returns for it.
    committed = _committed_host_runs()
    for check_id, texts, count in (
            ("jobs/host-job-committed-gate-line-grammar-passes", (GATE_RUN,), 2),
            ("jobs/host-job-committed-selftest-line-grammar-passes", (GATE_RUN + " --self-test",), 1),
            ("jobs/host-job-committed-opf-suite-line-grammar-passes", HOST_SUITES[OPF], 1),
            ("jobs/host-job-committed-gfe-suite-line-grammar-passes", HOST_SUITES[GFE], 1)):
        check(check_id, [(committed.count(text), _refused_run_char(text), _leading_assignment(text))
                         for text in texts], [(count, None, None)] * len(texts))
    check("jobs/host-job-committed-run-lines-counted", (len(committed), len(set(committed))), (8, 7))

    # The step shape (RUN_KEYS, STEP_SHELL_LINE): the Python host pin step, each suite step and the
    # --jobs-only judge step carry exactly name, run and shell, the shell: written as the exact line
    # shell: bash. A held step with no shell: or with a shell: other than bash (each end of the one
    # accepted value: bash with arguments, Bash, a quoted 'bash ' and an empty value) is a finding in its
    # own words, red in the copy with its rule removed and in the copy whose _shell_departure no longer
    # names that departure; so is a shell: bash written on any other line (a quoted 'bash' or "bash", a
    # trailing comment, a second space before the value, trailing whitespace, a - list-item line that
    # opens the step), each of which parses to bash, red under spelling_mutant, whose _shell_departure
    # reads the parsed value alone. A working-directory:, an env: or a missing name: on a held step is a
    # finding too. The exact line passes in every held step of the clean fixture
    # (fixture/clean-tree-passes).
    no_shell_mutant = _mutant(base, "python_host_pin_no_shell_mutant", source,
                              (('if "shell" not in ' + 'step:\n        return',
                                'if "shell" not in step:\n        return None\n    if False:\n        return'),))
    other_shell_mutant = _mutant(base, "python_host_pin_other_shell_mutant", source,
                                 (('if shell != ' + 'STEP_SHELL:', 'if False:'),))
    spelling_mutant = _mutant(base, "python_host_pin_shell_spelling_mutant", source,
                              (('if shell == STEP_SHELL and ' + 'written != STEP_SHELL_LINE:', 'if False:'),))
    gate_lines = SHELL_LINE + "        run: " + GATE_RUN + "\n"
    judge_lines = SHELL_LINE + "        run: " + JOBS_ONLY_RUN + "\n"
    core_lines = SHELL_LINE + "        run: " + HOST_SUITES[GFE][0] + "\n"
    opf_lines = SHELL_LINE + "        run: " + HOST_SUITES[OPF][0] + "\n"

    def shell(lines, value):
        return lines.replace(SHELL_LINE, "        shell:" + (" " + value if value else "") + "\n")

    judge_carries = "the step that carries the --jobs-only judge carries "
    judge_writes = "the step that carries the --jobs-only judge writes shell: bash as "
    core_step = "does not run its suite {} as an executable step: step 4 ".format(HOST_SUITES[GFE][0])
    opf_step = "does not run its suite {} as an executable step: step 4 ".format(HOST_SUITES[OPF][0])
    for check_id, edits, marker, rule, mutant in (
            ("jobs/gate-step-no-shell-finding", ((OPF, gate_lines, gate_lines.replace(SHELL_LINE, "")),),
             "but carries no shell: (want shell: bash)", "gate-step", no_shell_mutant),
            ("jobs/gate-step-other-shell-finding", ((GFE, gate_lines, shell(gate_lines, "perl {0}")),),
             "but carries shell: 'perl {0}', not shell: bash", "gate-step", other_shell_mutant),
            ("jobs/gate-step-shell-bash-arguments-finding", ((OPF, gate_lines, shell(gate_lines, "bash -e {0}")),),
             "but carries shell: 'bash -e {0}', not shell: bash", "gate-step", other_shell_mutant),
            ("jobs/gate-step-shell-upper-case-finding", ((OPF, gate_lines, shell(gate_lines, "Bash")),),
             "but carries shell: 'Bash', not shell: bash", "gate-step", other_shell_mutant),
            ("jobs/gate-step-shell-quoted-trailing-space-finding",
             ((OPF, gate_lines, shell(gate_lines, "'bash '")),),
             "but carries shell: 'bash ', not shell: bash", "gate-step", other_shell_mutant),
            ("jobs/gate-step-empty-shell-finding", ((GFE, gate_lines, shell(gate_lines, "")),),
             "but carries shell: None, not shell: bash", "gate-step", other_shell_mutant),
            ("jobs/suite-step-no-shell-finding", ((GFE, core_lines, core_lines.replace(SHELL_LINE, "")),),
             core_step + "carries no shell: (want shell: bash)", "suite-step", no_shell_mutant),
            ("jobs/suite-step-other-shell-finding", ((OPF, opf_lines, shell(opf_lines, "sh")),),
             opf_step + "carries shell: 'sh', not shell: bash", "suite-step", other_shell_mutant),
            ("jobs/jobs-only-step-no-shell-finding",
             (("registry", judge_lines, judge_lines.replace(SHELL_LINE, "")),),
             judge_carries + "no shell: (want shell: bash)", "outside-judge", no_shell_mutant),
            ("jobs/jobs-only-step-other-shell-finding", (("registry", judge_lines, shell(judge_lines, "pwsh")),),
             judge_carries + "shell: 'pwsh', not shell: bash", "outside-judge", other_shell_mutant),
            ("jobs/gate-step-single-quoted-shell-finding", ((OPF, gate_lines, shell(gate_lines, "'bash'")),),
             "but writes shell: bash as \"shell: 'bash'\", not as the exact line shell: bash", "gate-step",
             spelling_mutant),
            ("jobs/jobs-only-step-double-quoted-shell-finding",
             (("registry", judge_lines, shell(judge_lines, "\"bash\"")),),
             judge_writes + "'shell: \"bash\"', not as the exact line shell: bash", "outside-judge",
             spelling_mutant),
            ("jobs/suite-step-shell-trailing-comment-finding",
             ((GFE, core_lines, shell(core_lines, "bash  # the bound shell")),),
             core_step + "writes shell: bash as 'shell: bash  # the bound shell', not as the exact line",
             "suite-step", spelling_mutant),
            ("jobs/gate-step-shell-double-space-finding", ((GFE, gate_lines, shell(gate_lines, " bash")),),
             "but writes shell: bash as 'shell:  bash', not as the exact line shell: bash", "gate-step",
             spelling_mutant),
            ("jobs/suite-step-shell-trailing-space-finding", ((OPF, opf_lines, shell(opf_lines, "bash ")),),
             opf_step + "writes shell: bash as 'shell: bash ', not as the exact line", "suite-step",
             spelling_mutant),
            ("jobs/gate-step-shell-list-item-finding",
             ((GFE, "      - name: Python host pin\n" + gate_lines,
               "      - shell: bash\n        name: Python host pin\n        run: " + GATE_RUN + "\n"),),
             "but writes shell: bash as '- shell: bash', not as the exact line shell: bash", "gate-step",
             spelling_mutant),
            ("jobs/gate-step-working-directory-finding",
             ((OPF, gate_lines, "        working-directory: opf\n" + gate_lines),),
             "but carries working-directory:, which can skip", "gate-step", None),
            ("jobs/jobs-only-step-working-directory-finding",
             (("registry", judge_lines, "        working-directory: tools\n" + judge_lines),),
             judge_carries + "working-directory:", "outside-judge", None),
            ("jobs/gate-step-no-name-finding",
             ((OPF, "      - name: Python host pin\n" + gate_lines, "      - run: " + GATE_RUN + "\n" + SHELL_LINE),),
             "but carries no name:", "gate-step", None),
            ("jobs/jobs-only-step-no-name-finding",
             (("registry", "      - name: Python host pin (jobs leg, outside the host-patch jobs)\n" + judge_lines,
               "      - run: " + JOBS_ONLY_RUN + "\n" + SHELL_LINE),),
             judge_carries + "no name:", "outside-judge", None),
            # An env: on the Python host pin step: the step-env and gate-step rules both refuse it.
            ("jobs/gate-step-env-finding",
             ((GFE, gate_lines, gate_lines + "        env:\n          SAMPLE: value\n"),),
             "but carries env:, which can skip", None, None)):
        root = _fixture(base, workflow=_workflow(edits))
        code, lines = evaluate(root, HOST)
        got, want = (code, _has(lines, marker)), (1, True)
        if rule is not None:
            rules.JOB_RULES = JOB_RULES - {rule}
            try:
                got, want = got + (rules.evaluate(root, HOST)[0],), want + (0,)
            finally:
                rules.JOB_RULES = JOB_RULES
        if mutant is not None:
            got, want = got + (mutant.evaluate(root, HOST)[0],), want + (0,)
        check(check_id, got, want)
    # The suite-shape read: a suite of a suite runner that HOST_SUITES does not name is a suite step, so
    # adding one is a finding; the copy whose _suite_shaped reads only the SUITE_RUNS texts passes it.
    suite_shape_mutant = _mutant(base, "python_host_pin_suite_runs_only_mutant", source,
                                 (("if any(os.path.basename(os.path.normpath(token)) in " + "SUITE_RUNNERS",
                                   "if False and any(os.path.basename(os.path.normpath(token)) in "
                                   + "SUITE_RUNNERS"),))
    added = ("      - name: Floor suite\n" + SHELL_LINE
             + "        run: python3 -I -B tools/check_selftest_execution.py --suite python-floor-selftest\n")
    root = _fixture(base, workflow=_workflow(((GFE, _suite_step(GFE), _suite_step(GFE) + added),)))
    code, lines = evaluate(root, HOST)
    check("jobs/git-fixture-env-added-suite-shape-mutant-red",
          (code, _has(lines, "adds python3 -I -B tools/check_selftest_execution.py --suite python-floor-selftest"),
           suite_shape_mutant.evaluate(root, HOST)[0]), (1, True, 0))

    # The number read is the conservative spelling classifier, which covers the fractional
    # sexagesimal forms a narrower numeric pattern misses; each vector is red under the mutant
    # carrying that narrower pattern. The = value key, the date-led timestamp carrying a time part
    # and the quoted-scalar exemption each hold their own targeted mutant; the quoted case is an
    # ACCEPTANCE check WITH a discriminating mutant (the copy without the exemption refuses the
    # string the intact gate accepts), listed apart from the regression vectors above.
    old_number_mutant = _mutant(
        base, "python_host_pin_previous_number_pattern_mutant", source,
        (('r"[-+.0-9][0-9_:' + '.a-fA-FoxOX+-]*"',
          'r"[-+]?(?:[0-9][0-9_]*(?::[0-5]?[0-9])*|0[box][0-9a-fA-F_]+"\n'
          '    r"|(?:[0-9][0-9_]*\\.[0-9_]*|\\.[0-9][0-9_]*)(?:[eE][-+]?[0-9]+)?"\n'
          '    r"|[0-9][0-9_]*[eE][-+]?[0-9]+)"'),))
    for check_id, scalar in (
            ("jobs/jobs-only-sexagesimal-float-run-cannot-evaluate", "1:20.5"),
            ("jobs/jobs-only-sexagesimal-trailing-dot-run-cannot-evaluate", "1:20."),
            ("jobs/jobs-only-three-part-sexagesimal-float-run-cannot-evaluate", "12:34:56.7"),
            ("jobs/jobs-only-wide-sexagesimal-float-run-cannot-evaluate", "190:20:30.15")):
        root = _fixture(base, workflow=_workflow((("registry", JUDGE_STEP,
                                                   JUDGE_STEP + second + "      - run: " + scalar
                                                   + "\n"),)))
        code, lines = evaluate(root, HOST)
        check(check_id, (code, _has(lines, "a number"),
                         old_number_mutant.evaluate(root, HOST)[0]), (2, True, 0))
    # The radix letters match in either case (no reader's case folding is assumed): 0X1F, 0O17 and
    # +0X1F are numbers like their lower-case spellings, each red under the copy carrying the
    # lower-case-only class; 0B101 matches through the hex letters A-F in that class too, so it stays
    # exit 2 under it and does not discriminate the radix change.
    lowercase_radix_mutant = _mutant(
        base, "python_host_pin_lowercase_radix_only_mutant", source,
        (('r"[-+.0-9][0-9_:' + '.a-fA-FoxOX+-]*"', 'r"[-+.0-9][0-9_:.a-fA-Fox+-]*"'),))
    for check_id, scalar, lowercase_code in (
            ("jobs/jobs-only-upper-hex-run-cannot-evaluate", "0X1F", 0),
            ("jobs/jobs-only-upper-octal-run-cannot-evaluate", "0O17", 0),
            ("jobs/jobs-only-signed-upper-hex-run-cannot-evaluate", "+0X1F", 0),
            ("jobs/jobs-only-upper-binary-run-cannot-evaluate", "0B101", 2)):
        root = _fixture(base, workflow=_workflow((("registry", JUDGE_STEP,
                                                   JUDGE_STEP + second + "      - run: " + scalar
                                                   + "\n"),)))
        code, lines = evaluate(root, HOST)
        check(check_id, (code, _has(lines, "a number"),
                         lowercase_radix_mutant.evaluate(root, HOST)[0]), (2, True, lowercase_code))
    equals_mutant = _mutant(base, "python_host_pin_no_value_key_mutant", source,
                            (('if value == "="' + ":", "if False:"),))
    root = _fixture(base, workflow=_workflow((("registry", JUDGE_STEP,
                                               JUDGE_STEP + second + "      - run: =\n"),)))
    code, lines = evaluate(root, HOST)
    check("jobs/jobs-only-equals-run-cannot-evaluate",
          (code, _has(lines, "a YAML 1.1 value key"),
           equals_mutant.evaluate(root, HOST)[0]), (2, True, 0))
    timestamp_mutant = _mutant(base, "python_host_pin_no_timestamp_kind_mutant", source,
                               (("if TIMESTAMP_PLAIN_RE" + ".fullmatch(value):", "if False:"),))
    # The timestamp classifier REQUIRES the YAML 1.1 time fields after the T, t or
    # whitespace separator (an hour of one or two digits, :MM:SS, an optional fraction and an
    # optional Z or +-HH[:MM] zone), so only a real date-and-time spelling is a timestamp. A valid T,
    # tab, mixed-whitespace or space spelling stays refused (exit 2), red under timestamp_mutant (the
    # copy that classifies none).
    for check_id, scalar in (
            ("jobs/jobs-only-timestamp-run-cannot-evaluate", "2026-10-14T06:30:00Z"),
            ("jobs/jobs-only-tab-timestamp-run-cannot-evaluate", "2026-10-14\t06:30:00Z"),
            ("jobs/jobs-only-mixed-whitespace-timestamp-run-cannot-evaluate",
             "2026-10-14\t \t06:30:00Z"),
            ("jobs/jobs-only-space-timestamp-run-cannot-evaluate", "2026-10-14 06:30:00Z")):
        root = _fixture(base, workflow=_workflow((("registry", JUDGE_STEP,
                                                   JUDGE_STEP + second + "      - run: " + scalar
                                                   + "\n"),)))
        code, lines = evaluate(root, HOST)
        check(check_id, (code, _has(lines, "a timestamp"),
                         timestamp_mutant.evaluate(root, HOST)[0]), (2, True, 0))
    # Each OPTIONAL time field of TIMESTAMP_PLAIN_RE has a positive vector (exit 2, a timestamp),
    # red under a flip of that field alone, which reads the vector as a string (exit 0): a
    # fraction, a signed offset, a one-digit hour, a lower-case t, a zone-less time and whitespace
    # before the zone. A pattern that reads any suffix after the separator as a time refuses each of
    # them too, so these vectors hold the fields; the string vectors below hold the suffix reading.
    for check_id, scalar, anchor, replacement in (
            ("jobs/jobs-only-fraction-timestamp-run-cannot-evaluate", "2026-10-14T06:30:00.5Z",
             'r"(?:\\.[0-9]*)?' + '(?:[ \\t]*', 'r"(?:[ \\t]*'),
            ("jobs/jobs-only-signed-offset-timestamp-run-cannot-evaluate",
             "2026-10-14T06:30:00+05:00",
             "[ \\t]*(?:Z|[-+][0-9][0-9]?" + "(?::[0-9][0-9])?)", "[ \\t]*(?:Z)"),
            ("jobs/jobs-only-one-digit-hour-timestamp-run-cannot-evaluate", "2026-10-14T6:30:00Z",
             "[ \\t]+)[0-9]" + "[0-9]?:", "[ \\t]+)[0-9][0-9]:"),
            ("jobs/jobs-only-lowercase-t-timestamp-run-cannot-evaluate", "2026-10-14t06:30:00Z",
             "(?:[Tt]|" + "[ \\t]+)", "(?:T|[ \\t]+)"),
            ("jobs/jobs-only-zoneless-timestamp-run-cannot-evaluate", "2026-10-14T06:30:00",
             "[ \\t]*(?:Z|[-+][0-9][0-9]?(?::[0-9][0-9])?)" + ")?",
             "[ \\t]*(?:Z|[-+][0-9][0-9]?(?::[0-9][0-9])?))"),
            ("jobs/jobs-only-space-before-zone-timestamp-run-cannot-evaluate",
             "2026-10-14T06:30:00 Z", "(?:[ \\t]*" + "(?:Z|", "(?:(?:Z|")):
        field_mutant = _mutant(base, "python_host_pin_timestamp_field_mutant_" + str(len(EXECUTED)),
                               source, ((anchor, replacement),))
        root = _fixture(base, workflow=_workflow((("registry", JUDGE_STEP,
                                                   JUDGE_STEP + second + "      - run: " + scalar
                                                   + "\n"),)))
        code, lines = evaluate(root, HOST)
        check(check_id, (code, _has(lines, "a timestamp"), field_mutant.evaluate(root, HOST)[0]),
              (2, True, 0))
    # A date-led value whose suffix after the separator is NOT a time (--help after a tab, nonsense
    # after a space, garbage after T) is a STRING, not a timestamp, so the run: passes (exit 0). Each
    # is red under loose_timestamp_mutant (a pattern whose .+ suffix matches any text and misreads
    # these as timestamps, exit 2), so the time fields keep that false-positive class out.
    loose_timestamp_mutant = _mutant(
        base, "python_host_pin_loose_timestamp_pattern_mutant", source,
        (('r"[0-9]{4}-[0-9][0-9]?-[0-9][0-9]?(?:(?:[Tt]|[ \\t]+)[0-9][0-9]?:[0-9][0-9]:[0-9][0-9]"\n'
          '    r"(?:\\.[0-9]*)?(?:[ \\t]*(?:Z|[-+][0-9][0-9]?(?::[0-9][0-9])?))?)?"',
          'r"[0-9]{4}-[0-9][0-9]?-[0-9][0-9]?(?:(?:[Tt]|[ \\t]+).+)?"'),))
    for check_id, scalar in (
            ("jobs/jobs-only-tab-nontime-suffix-string-passes", "2026-10-14\t--help"),
            ("jobs/jobs-only-space-nontime-suffix-string-passes", "2026-10-14 nonsense"),
            ("jobs/jobs-only-t-nontime-suffix-string-passes", "2026-10-14Tgarbage")):
        root = _fixture(base, workflow=_workflow((("registry", JUDGE_STEP,
                                                   JUDGE_STEP + second + "      - run: " + scalar
                                                   + "\n"),)))
        check(check_id, (evaluate(root, HOST)[0],
                         loose_timestamp_mutant.evaluate(root, HOST)[0]), (0, 2))
    quoted_exemption_mutant = _mutant(base, "python_host_pin_no_quoted_exemption_mutant", source,
                                      (('if kind in ("quoted", ' + '"block", "bare"):',
                                        'if kind in ("block", "bare"):'),))
    root = _fixture(base, workflow=_workflow((("registry", JUDGE_STEP,
                                               JUDGE_STEP + second + "      - run: '123'\n"),)))
    check("jobs/jobs-only-quoted-number-run-passes",
          (evaluate(root, HOST)[0], quoted_exemption_mutant.evaluate(root, HOST)[0]), (0, 2))

    # The flag-first split judge, red under a targeted mutant of the LINE-BOUNDARY read: the copy
    # that counts a run: as the judge when the tokens of all its lines, line boundaries and token
    # order ignored, are the judge command's counts the two-line spelling and passes the fixture the
    # intact gate refuses (without that mutant the fixture is red through the zero-judge count alone;
    # _job_cases keeps that case and marks it as not discriminating). The mutation replaces the whole
    # single-line token-equality read (the normalized-path comparison among what it drops), so it
    # counts EITHER split order, never the flag-first order alone; the command-first split is
    # asserted red under the same mutant right after.
    line_boundary_mutant = _mutant(
        base, "python_host_pin_joined_lines_judge_mutant", source,
        (("if len(groups) == 1 and _counts_as_judge" + "(groups[0]):",
          "if sorted(token for group in groups for token in group) == "
          "sorted(JOBS_ONLY_TOKENS):"),))
    root = _fixture(base, workflow=_workflow((("registry", "run: " + JOBS_ONLY_RUN,
                                               "run: |\n          " + JOBS_ONLY_FLAG
                                               + "\n          " + GATE_RUN),)))
    code, lines = evaluate(root, HOST)
    check("jobs/jobs-only-split-flag-first-line-boundary-mutant-red",
          (code, _has(lines, "0 steps outside the host-patch jobs run"),
           line_boundary_mutant.evaluate(root, HOST)[0]), (1, True, 0))

    root = _fixture(base, workflow=_workflow((("registry", "run: " + JOBS_ONLY_RUN,
                                               "run: |\n          " + GATE_RUN
                                               + "\n          " + JOBS_ONLY_FLAG),)))
    code, lines = evaluate(root, HOST)
    check("jobs/jobs-only-split-command-line-boundary-mutant-red",
          (code, _has(lines, "0 steps outside the host-patch jobs run"),
           line_boundary_mutant.evaluate(root, HOST)[0]), (1, True, 0))

    # Acceptance boundaries (each exit 0, not a regression vector): a header comment naming the flag
    # selects nothing (selection is by tokens), a clean block-scalar body is read and tokenized, and
    # the committed quality.yml's own outside shapes stay accepted: a multi-line block-scalar run:
    # with shell expansions and quotes, a matrix with a flow-sequence value consumed by ${{ }}
    # expressions outside run:, and an aggregator with a needs: list of bare items.
    check("jobs/jobs-only-header-comment-clean-body-passes",
          evaluate(_fixture(base, workflow=_workflow((("registry", JUDGE_STEP,
                                                       JUDGE_STEP + "  second: # --jobs-only\n"
                                                       "    runs-on: ubuntu-latest\n    steps:\n"
                                                       "      - run: echo ok\n"),))), HOST)[0], 0)
    check("jobs/jobs-only-header-comment-block-body-passes",
          evaluate(_fixture(base, workflow=_workflow((("registry", JUDGE_STEP,
                                                       JUDGE_STEP + "  second: # --jobs-only\n"
                                                       "    runs-on: ubuntu-latest\n    steps:\n"
                                                       "      - run: |\n          echo ok\n"),))),
                   HOST)[0], 0)
    block_job = ("  second:\n    runs-on: ubuntu-latest\n    steps:\n"
                 "      - name: Blocked tool\n        env:\n          SAMPLE: value\n"
                 "        run: |\n          set -euo pipefail\n"
                 '          url="https://example.invalid/v${SAMPLE}/x.tar.gz"\n'
                 '          echo "${SAMPLE}  x.tar.gz" | sha256sum -c -\n')
    check("jobs/outside-block-scalar-run-passes",
          evaluate(_fixture(base, workflow=_workflow((("registry", JUDGE_STEP,
                                                       JUDGE_STEP + block_job),))), HOST)[0], 0)
    matrix_job = ("  second:\n    name: Vectors (${{ matrix.os }})\n    runs-on: ${{ matrix.os }}\n"
                  "    strategy:\n      fail-fast: false\n      matrix:\n"
                  "        os: [ubuntu-latest, macos-latest]\n    steps:\n"
                  "      - run: git config --global core.autocrlf true\n")
    check("jobs/outside-matrix-flow-and-expression-passes",
          evaluate(_fixture(base, workflow=_workflow((("registry", JUDGE_STEP,
                                                       JUDGE_STEP + matrix_job),))), HOST)[0], 0)
    needs_job = ("  second:\n    if: always()\n    needs:\n      - floor\n      - registry\n"
                 "    runs-on: ubuntu-latest\n    steps:\n      - name: All\n        env:\n"
                 "          RESULTS: ${{ join(needs.*.result, ' ') }}\n        run: |\n"
                 '          rest="${RESULTS:-none}"\n          echo "Results: ${RESULTS}"\n')
    check("jobs/outside-needs-list-passes",
          evaluate(_fixture(base, workflow=_workflow((("registry", JUDGE_STEP,
                                                       JUDGE_STEP + needs_job),))), HOST)[0], 0)

    # ACCEPTANCE ONLY (each exit 0; by design these two checks are red under no mutant, so they are
    # listed apart from every regression vector): a #-leading line inside a literal block body is read and passed (the committed
    # workflow carries comment lines inside block bodies), and a literal block whose body carries a
    # blank line is accepted: the blank body line is DROPPED by the line reader, as the shell
    # ignores one (the value joins the non-blank body lines; dropping is the disclosed behaviour,
    # not retention).
    check("jobs/outside-block-comment-line-passes",
          evaluate(_fixture(base, workflow=_workflow((("registry", JUDGE_STEP,
                                                       JUDGE_STEP + second
                                                       + "      - run: |\n          # a note\n"
                                                       "          echo ok\n"),))), HOST)[0], 0)
    check("jobs/outside-block-blank-line-passes",
          evaluate(_fixture(base, workflow=_workflow((("registry", JUDGE_STEP,
                                                       JUDGE_STEP + second
                                                       + "      - run: |\n          echo first\n"
                                                       "\n          echo second\n"),))), HOST)[0], 0)

    # ACCEPTANCE ONLY (exit 0 by design, red under no mutant): the host-patch flag read scans the
    # PARSED run: scalar, so a stem spelled
    # only inside a trailing YAML comment of a one-line plain run: is YAML-dropped text, never
    # command text, and selects and refuses nothing. The two block-scalar scopes differ by job kind:
    # a HOST-PATCH job's run: | block scalar is cannot-evaluate at the host-job line model (ENTRY_RE,
    # _entry) BEFORE the flag read, so a block body never reaches it; an OUTSIDE job's literal block
    # keeps its #-leading body lines and is read line by line.
    comment_extra = ("      - name: Extra step\n"
                     '        run: echo ok # --jobs-o""nly\n')
    root = _fixture(base, workflow=_workflow(((OPF, _suite_step(OPF),
                                               _suite_step(OPF) + comment_extra),)))
    check("jobs/host-job-comment-only-stem-passes", evaluate(root, HOST)[0], 0)
    # The two block-scalar scopes shown directly. A run: | block (echo ok, then a
    # #-leading body line) is cannot-evaluate (exit 2) in a HOST-PATCH job, refused by the host-job
    # line model before the flag read. An OUTSIDE job's literal block KEEPS its #-leading body line,
    # shown observably: when that line carries a ${{ }} expression the run: is cannot-evaluate
    # (exit 2), and the copy whose block reader drops #-leading body lines passes it (exit 0).
    block_step = ("      - name: Extra step\n        run: |\n"
                  "          echo ok\n          # a note\n")
    host_block = _fixture(base, workflow=_workflow(((OPF, _suite_step(OPF),
                                                     _suite_step(OPF) + block_step),)))
    code, lines = evaluate(host_block, HOST)
    check("jobs/host-job-block-scalar-refused-before-flag-read",
          (code, _has(lines, "not a block mapping entry")), (2, True))
    drop_comment_body_mutant = _mutant(
        base, "python_host_pin_drop_comment_body_mutant", source,
        (('body.append(text.strip(' + '" \\t"))',
          'body.extend([] if text.lstrip(" \\t").startswith("#") else [text.strip(" \\t")])'),))
    expression_block = ("      - name: Extra step\n        run: |\n"
                        "          echo ok\n          # ${{ github.sha }}\n")
    outside_block = _fixture(base, workflow=_workflow((("registry", JUDGE_STEP,
                                                        JUDGE_STEP + second + expression_block),)))
    code, lines = evaluate(outside_block, HOST)
    check("jobs/outside-job-block-scalar-keeps-comment-body",
          (code, _has(lines, "carries a ${{ }} expression"),
           drop_comment_body_mutant.evaluate(outside_block, HOST)[0]), (2, True, 0))

    for check_id, version, shown in (
            ("interpreter/newer-patch-finding", (3, 14, 8, "final"), "Python 3.14.8 (final)"),
            ("interpreter/older-patch-finding", (3, 14, 3, "final"), "Python 3.14.3 (final)"),
            ("interpreter/candidate-finding", (3, 14, 4, "candidate"), "Python 3.14.4 (candidate)"),
            ("interpreter/other-minor-finding", (3, 15, 4, "final"), "Python 3.15.4 (final)")):
        code, lines = evaluate(_fixture(base), version)
        check(check_id, (code, _has(lines, "the running interpreter is " + shown)), (1, True))

    # The --jobs-only leg (the CI step of the registry job, outside the host-patch jobs): it skips
    # the interpreter leg, so it can run on the floor pin, and it still runs the jobs leg, so a
    # job-level env: placed in both host-patch jobs (which in CI silences every step of those jobs,
    # this gate's own steps included) is refused from outside them. Red on revert: a jobs_only that no
    # longer skips the interpreter leg fails the first check, and one that no longer runs the jobs leg
    # fails the second.
    check("jobs/jobs-only-skips-interpreter-leg",
          (evaluate(_fixture(base), (3, 14, 8, "final"), jobs_only=True)[0],
           evaluate(_fixture(base), (3, 14, 8, "final"))[0]), (0, 1))
    env_edit = "    env:\n      BASH_ENV: '$(: > \"$0\")'\n    runs-on: "
    root = _fixture(base, workflow=_workflow(((OPF, "    runs-on: ", env_edit),
                                              (GFE, "    runs-on: ", env_edit))))
    code, lines = evaluate(root, HOST, jobs_only=True)
    check("jobs/jobs-only-still-refuses-host-job-env", (code, _has(lines, "carries env:")), (1, True))

    # The --jobs-only CLI wiring: main() itself, on a pristine copy of this gate with its ROOT and sys
    # replaced, forwards the flag (the interpreter leg skipped on a mismatched interpreter, the jobs leg
    # still run on a damaged host-patch job). Red on revert: a copy whose main no longer forwards the
    # flag (jobs_only=False) fails the clean tree on the interpreter leg, and a copy whose main returns 0
    # without evaluating passes the damaged tree the intact main refuses.
    cli = _copy(base, "python_host_pin_cli", source)
    clean = _fixture(base)
    damaged = _fixture(base, workflow=_workflow(((OPF, "    runs-on: ", env_edit),
                                                 (GFE, "    runs-on: ", env_edit))))
    cli.ROOT, cli.sys = clean, _FakeSys((3, 14, 8, "final", 0))
    check("cli/jobs-only-skips-interpreter-leg", (cli.main(["--jobs-only"]), cli.main([])), (0, 1))
    cli.ROOT = damaged
    check("cli/jobs-only-refuses-damaged-host-job", cli.main(["--jobs-only"]), 1)
    anchor = "jobs_only=" + "bool(argv)"
    if source.count(anchor) != 1:
        raise CannotEvaluate("red-on-revert anchor for the CLI forwarding occurs {} times".format(
            source.count(anchor)))
    forced = _copy(base, "python_host_pin_cli_without_forwarding",
                   source.replace(anchor, "jobs_only=" + "False"))
    forced.ROOT, forced.sys = clean, _FakeSys((3, 14, 8, "final", 0))
    check("cli/forwarding-mutant-fails-clean-tree-red", forced.main(["--jobs-only"]), 1)
    anchor = 'if not argv or argv == ["--jobs-' + 'only"]:'
    if source.count(anchor) != 1:
        raise CannotEvaluate("red-on-revert anchor for the CLI dispatch occurs {} times".format(
            source.count(anchor)))
    silent = _copy(base, "python_host_pin_cli_silent_pass",
                   source.replace(anchor, "if argv:\n        return 0\n    " + anchor))
    silent.ROOT, silent.sys = damaged, _FakeSys((3, 14, 4, "final", 0))
    check("cli/silent-pass-mutant-passes-damaged-tree-red", silent.main(["--jobs-only"]), 0)

    try:
        real = load_source(ROOT) + ("final",)
    except CannotEvaluate as exc:
        real = None
        FAILURES.append("real/source: {}".format(exc))
    if real is not None:
        check("real/tree-passes-at-its-host-patch", evaluate(ROOT, real)[0], 0)
    # The git-fixture-env host-patch suites are the '3.14' lanes of the committed workflow, in its order
    # (_workflow_lanes): each lane's command, its shlex tokens joined by single spaces, and the pin lines
    # of the job that carries it. A run: line or value the reader cannot read is cannot-evaluate (exit 2).
    lanes = (HOST_SUITES[GFE], (("python-version: '3.14'",),) * len(HOST_SUITES[GFE]))
    check("real/git-fixture-env-host-suites-are-the-lanes", _workflow_lanes(_read_text(ROOT / WORKFLOW_REL)),
          lanes)
    # Each lanes/ vector adds one job beside the four literal lanes and pairs the intact reader with a copy
    # of this gate (_mutant) whose reader drops one rule: "refusal" leaves a candidate line that is not an
    # exact form unread; "literal" selects run: lines by literal text again (--suite and git-fixture-env
    # both spelled on the line, the reader this one replaces); "runner" drops the runner-stem token test;
    # "flag" drops the --suite token test; "whole-token" tests whole tokens (the exact flag, a path's
    # normpath base name, a module name's last part), not text inside them; "values" no longer refuses a
    # value outside a run: naming --suite or the runner; "lanes-only" accepts a runner --suite line only
    # for a git-fixture-env suite. Each vector is red in its copy: the four literal lanes alone where the
    # intact reader refuses the job or reads a fifth lane, or a refusal where it accepts the line as no lane.
    flag_test, stem_test = '"--suite" ' + 'in word', 'any(stem ' + 'in word for stem in stems)'
    copies = {name: _mutant(base, "python_host_pin_lanes_" + name.replace("-", "_"), source, edits)
              for name, edits in (
                  ("refusal", (('elif words != runner + ' + '["--self-test"]:', "elif False:"),)),
                  ("literal", (("words = shlex." + "split(line_text)",
                                "words = shlex." + "split(line_text) if "
                                '"--suite" in line_text and "git-fixture-env" in line_text else []'),)),
                  ("runner", ((stem_test, "False"),)),
                  ("flag", ((flag_test + " or ", ""),)),
                  ("whole-token", ((flag_test, 'word == "--suite"'),
                                   (stem_test, 'any(os.path.basename(os.path.normpath(word)) == stem + ".py" '
                                    'or word.split(".")[-1] == stem for stem in stems)'))),
                  ("values", (("if id(value) " + "not in runs and ", "if False and "),)),
                  ("lanes-only", (("suite_name.fullmatch(" + "words[-1]):",
                                   'suite_name.fullmatch(words[-1]) and "git-fixture-env" in words[-1]:'),)))}
    lane_jobs = "".join(
        "  lane-{}:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/setup-python@v5\n"
        "        with:\n          python-version: '3.14'\n      - name: Suite\n"
        "        run: {}\n".format(number, run) for number, run in enumerate(HOST_SUITES[GFE], 1))
    runner = "python3 -I -B tools/check_selftest_execution.py"
    widened = (HOST_SUITES[GFE] + (runner + " --suite git-fixture-env-extra",),
               (("python-version: '3.14'",),) * (len(HOST_SUITES[GFE]) + 1))
    refused = (2, True)
    for check_id, copy, matrix, env, run, want, want_copy in (
            # Lines naming the runner or --suite whose suite comes from a matrix value or an expansion.
            ("lanes/matrix-suite-lane-cannot-evaluate", "refusal", "        suite: [git-fixture-env-extra]\n",
             "", runner + " --suite ${{ matrix.suite }}", refused, lanes),
            ("lanes/module-runner-variable-suite-lane-cannot-evaluate", "refusal", "",
             "        env:\n          SUITE: git-fixture-env-extra\n",
             "python3 -I -B -m tools.check_selftest_execution --suite \"$SUITE\"", refused, lanes),
            ("lanes/runner-matrix-arguments-lane-cannot-evaluate", "runner",
             "        flag: [suite]\n        suite: [git-fixture-env-extra]\n", "",
             runner + " --${{ matrix.flag }} ${{ matrix.suite }}", refused, lanes),
            # The module form with every argument from a variable: only the runner's stem names it.
            ("lanes/module-runner-variable-flags-lane-cannot-evaluate", "runner", "", "",
             "python3 -I -B -m tools.check_selftest_execution $FLAGS", refused, lanes),
            # The runner from a variable: only --suite names it.
            ("lanes/variable-runner-suite-lane-cannot-evaluate", "flag", "", "",
             "python3 -I -B \"$RUNNER\" --suite git-fixture-env-extra", refused, lanes),
            # A lane handed to another shell as one argument: the names sit inside one token.
            ("lanes/shell-string-lane-cannot-evaluate", "whole-token", "", "",
             "bash -c \"" + runner + " --suite git-fixture-env-extra\"", refused, lanes),
            # A lane spelled in an env value and run through an expansion.
            ("lanes/env-command-lane-cannot-evaluate", "values", "",
             "        env:\n          CMD: " + runner + " --suite git-fixture-env-extra\n", "$CMD",
             refused, lanes),
            # Quote splices that shell quote removal, and shlex, resolve to --suite git-fixture-env-extra.
            ("lanes/quote-spliced-suite-name-lane-read", "literal", "", "",
             runner + " --suite 'git-fixture'-env-extra", widened, lanes),
            ("lanes/quote-spliced-suite-flag-lane-read", "literal", "", "",
             runner + " --su\"\"ite git-fixture-env-extra", widened, lanes),
            # A runner --suite line of another suite: accepted, not a lane.
            ("lanes/unrelated-suite-line-accepted", "lanes-only", "", "",
             runner + " --suite orch-behaviour-selftest", lanes, refused)):
        extra = ("  lane-extra:\n    runs-on: ubuntu-latest\n"
                 + ("    strategy:\n      matrix:\n" + matrix if matrix else "")
                 + "    steps:\n      - uses: actions/setup-python@v5\n        with:\n"
                 "          python-version: '3.14'\n      - name: Suite\n" + env + "        run: " + run + "\n")
        text = HEAD + lane_jobs + extra
        check(check_id, (_lane_reading(_workflow_lanes, CannotEvaluate, text),
                         _lane_reading(copies[copy]._workflow_lanes, copies[copy].CannotEvaluate, text)),
              (want, want_copy))
    # The python-floor gate accepts a host-patch pin only inside these jobs of this workflow, read from this
    # source in this form, and refuses these line-break characters: its copies of those names must be these.
    floor = _copy(base, "python_floor_names", _read_text(ROOT / "tools" / "check_python_floor.py"))
    check("real/floor-gate-agrees",
          (tuple(floor.HOST_JOBS), floor.HOST_WORKFLOW_REL, floor.HOST_SOURCE_REL,
           floor.HOST_PATCH_RE.pattern, floor.LINE_BREAKS),
          (HOST_JOBS, WORKFLOW_REL, SOURCE_REL, PATCH_RE.pattern, LINE_BREAKS))
    _red_on_revert(base)


def _red_on_revert(base):
    """Each finding leg red on its fixture with the intact gate, which names that leg's own finding, and
    green on the same fixture in a copy of this gate, loaded through importlib, with that leg's call
    removed."""
    source = _read_text(Path(__file__).resolve())
    cases = {"jobs": (dict(workflow=_workflow(((OPF, PIN, "python-version: '3.14.8'"),))), HOST,
                      "differs from the host patch"),
             "interpreter": ({}, (3, 14, 8, "final"), "the running interpreter is Python 3.14.8")}
    for leg, call in REVERT_CALLS:
        line = "findings.extend(" + call + ")"
        if source.count(line) != 1:
            raise CannotEvaluate("red-on-revert anchor for the {} leg occurs {} times".format(
                leg, source.count(line)))
        module = _copy(base, "python_host_pin_without_" + leg, source.replace(line, "findings.extend(())"))
        kwargs, version, marker = cases[leg]
        root = _fixture(base, **kwargs)
        code, lines = evaluate(root, version)
        check("revert/{}-leg".format(leg), (code, _has(lines, marker), module.evaluate(root, version)[0]),
              (1, True, 0))
    # The parse mapping: a copy whose load_source maps only TOMLDecodeError lets the injected key-depth
    # RecursionError escape, which the intact gate reports as cannot-evaluate.
    anchor = "except (ValueError, RecursionError, " + "MemoryError) as exc:"
    if source.count(anchor) != 1:
        raise CannotEvaluate("red-on-revert anchor for the parse mapping occurs {} times".format(
            source.count(anchor)))
    module = _copy(base, "python_host_pin_without_parse_mapping", source.replace(anchor, "except () as exc:"))
    root = _fixture(base, source=SOURCE_TEXT + ".".join(["k"] * 1001) + " = 1\n")
    check("revert/parse-mapping",
          (_parse_refusal(evaluate, root, True), _parse_refusal(module.evaluate, root, True)),
          ((2, True), "raised RecursionError"))
    # Each class the parse sites map (RecursionError, MemoryError and ValueError), INJECTED at tomllib's key
    # parser on a key of more than 1000 dotted parts (real memory exhaustion cannot be provoked safely), is
    # cannot-evaluate, never a traceback, and escapes the copy whose load_source no longer maps that class.
    for check_id, kind, kept in (
            ("source/injected-recursion-error-cannot-evaluate", RecursionError, "ValueError, MemoryError"),
            ("source/injected-memory-error-cannot-evaluate", MemoryError, "ValueError, RecursionError"),
            ("source/injected-value-error-cannot-evaluate", ValueError, "RecursionError, MemoryError")):
        unmapped = _mutant(base, "python_host_pin_without_" + kind.__name__.lower(), source,
                           ((anchor, "except (" + kept + ") as exc:"),))
        check(check_id, (_parse_refusal(evaluate, root, True, kind),
                         _parse_refusal(unmapped.evaluate, root, True, kind)), ((2, True), "raised " + kind.__name__))
    # The same injection over a key parser that refuses the key itself first, by RecursionError, as a
    # release that bounds key depth does (_capped_parser emulates one, so these vectors do not depend on
    # the patch release that runs the self-test): MemoryError and ValueError still escape the copy that
    # no longer maps them. Each is red under passthrough_mutant, whose _key_depth_bounded lets the
    # parser's own RecursionError through: the copy that maps RecursionError then reports
    # cannot-evaluate instead of letting the injected class escape.
    passthrough_mutant = _mutant(base, "python_host_pin_parser_class_passthrough_mutant", source,
                                 (('raise kind("injected: the key parser itself ' + 'refused a key") from None',
                                   "raise"),))
    for check_id, kind, kept in (
            ("source/capped-parser-injected-memory-error-cannot-evaluate", MemoryError,
             "ValueError, RecursionError"),
            ("source/capped-parser-injected-value-error-cannot-evaluate", ValueError,
             "RecursionError, MemoryError")):
        unmapped = _mutant(base, "python_host_pin_capped_without_" + kind.__name__.lower(), source,
                           ((anchor, "except (" + kept + ") as exc:"),))
        check(check_id, (_parse_refusal(evaluate, root, True, kind, capped=True),
                         _parse_refusal(unmapped.evaluate, root, True, kind, capped=True),
                         passthrough_mutant._parse_refusal(unmapped.evaluate, root, True, kind, capped=True)),
              ((2, True), "raised " + kind.__name__, (2, True)))


def self_test():
    try:
        with tempfile.TemporaryDirectory(prefix="python-host-pin-selftest-") as raw:
            _self_test_cases(Path(raw))
    except (CannotEvaluate, OSError) as exc:
        print("SELF-TEST HARNESS ERROR: {}".format(exc), file=sys.stderr)
        return 2
    if FAILURES:
        print("SELF-TEST FAIL:")
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: {} checks executed (each finding leg and each job rule red on its fixture and "
          "green with it removed)".format(len(EXECUTED)))
    return 0


def main(argv):
    if not argv or argv == ["--jobs-only"]:
        code, lines = evaluate(ROOT, tuple(sys.version_info[:4]), jobs_only=bool(argv))
        for line in lines:
            print(line, file=sys.stderr if code == 2 else sys.stdout)
        return code
    if argv == ["--self-test"]:
        return self_test()
    print("usage: check_python_host_pin.py [--jobs-only | --self-test]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
