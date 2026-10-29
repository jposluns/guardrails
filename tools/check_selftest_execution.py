#!/usr/bin/env python3
"""Execution-set gate for registered self-test suites (chgchk/evgcmp): prove every registered check
RAN, not merely that the suite went green.

A present-but-unreached check() call (dead code after an early return, a misindented block, a loop that
no longer iterates) is indistinguishable from a passing one when judged by the suite's exit code alone.
This gate closes that with three layers. First, a STATIC pre-launch reconciliation resolves every
check() call site in the runner's SOURCE to literal ids (a string constant, or a for loop over literal
rows; anything else is a fail-closed error, and a duplicate source id is refused) and requires that set
to equal the manifest exactly, so a check that is both unreachable and unregistered, or a dead
duplicate-id source alias, is refused before any child runs. Second, the suite's check() choke point
records every check id actually reached, the child emits them as a structured JSON report, and this
gate reconciles that report, as an exact set, against the hand-authored expectation manifest
tools/selftest_checks.toml. Third, the child carries its own in-run self-guard. The manifest is
authored from SOURCE REVIEW and is NEVER regenerated from a runtime capture (a captured manifest would
drop an accidentally-unreachable check from both sides of the comparison and stay green, the exact
failure this gate exists to catch); there is deliberately no accept, update, or baseline command.

  check_selftest_execution.py --suite <id>   run the registered suite and reconcile its execution set
  check_selftest_execution.py --self-test    synthetic manifests and fake runners assert every leg fires

The child is launched [sys.executable, -I, -B, -c, <shim>, <runner's directory>, <runner>, <private
abs path>, <row args>...]: the shim imports tools/_selftest_exit_report.py from beside the runner and
hands it control, so that module's observation hooks (its audit hook, threading.excepthook wrapper,
and threading._shutdown wrapper) are installed BEFORE any runner statement, import-time included, and
the runner then executes as __main__ (the real sys.modules["__main__"] for the whole run, exit handlers
included) with [<runner>, <row args>..., --execution-report, <private abs path>] as its argv; a row
with no args key launches exactly as before, with [<runner>, --execution-report, <private abs path>].

SPLIT SUITES. A [[suite]] row may carry one optional key beyond id, runner and expected-check-ids:
args, a non-empty array of literal arguments forwarded to the runner's launch ahead of the gate's own
--execution-report pair. Each argument must be a string of at most ROW_ARG_MAX characters matching
ROW_ARG_RE (printable ASCII from a closed set: a letter or digit first, or one or two dashes and then
one; no whitespace, quote, shell, glob or expansion character, and no leading '/', '.', '@' or '~'),
and an argument that names the gate's own --execution-report option, attaches a value to it, or
abbreviates it (an argparse-style runner resolves a unique prefix to the full option) is refused; any
other shape (a non-array, an empty array, a non-string element) is refused too. The filter is
LEXICAL: no row can redirect the report through the named option, and nothing more is claimed about
what an accepted argument means to a given runner (one that stops parsing at it, exits early on it,
or takes the rest of its argv as data can leave the report unwritten); a runner that writes no
report is a missing report, cannot-evaluate (exit 2), never a pass. Rows whose runners resolve
(os.path.realpath) to one path form a GROUP: before any launch, every row of the requested suite's
group must carry args that no other row of the group carries, the rows' expected sets must be
pairwise disjoint, and their union must equal the runner's static check() set exactly; a gap, an
overlap, an extra id, or a missing or repeated args list is cannot-evaluate. Membership is decided by
resolving EVERY row's runner strictly (os.path.realpath(strict=True)), so a missing path, a
non-directory component, a symlink loop or a permission error in any row's runner leaves membership
undecidable and is cannot-evaluate before any report directory is allocated, never an assumed
non-member; a runner path carrying a control or format character (Unicode category Cc or Cf, so C0,
DEL, C1 and the bidi controls, plus the line and paragraph separators U+2028 and U+2029) is refused
by the schema, through the one predicate every schema string shares. A single-row group is held, as
before, to expected set == static set, with the same diagnostics. The runtime layer then
reconciles the launched row's executed set against that row's own expected set, so a runner that
ignores its arguments and runs every check is an extra-id finding (exit 1).

LAUNCH COVERAGE. Before any launch, every registered [[suite]] row, not only the requested one, must
have its launch line (tools/check_selftest_execution.py --suite <id>) in BOTH launch rosters, the
local roster tools/run_all_checks.sh and the CI workflow .github/workflows/quality.yml. Each is read
only, as a regular non-symlink file, through the parity gate's own validated loader
(check_ci_parity.read_runner_text with extract_local or extract_ci, never a second parser), so a
commented-out line is no launch line; and a roster member counts as a row's launch line only when
it is EXACTLY that two-argument form, the one argv shape this gate's CLI accepts, so a member
carrying any further argument, or a near-miss id, launches nothing. A suite id itself must match a
CLOSED grammar (SUITE_ID_RE: a letter or digit first, then letters, digits, '.', '_' and '-'
only), refused by name otherwise, so a space-joined roster identity can never stand for a
registered suite. A roster that is missing, not a regular file, unreadable, or drawing a loader
diagnostic is cannot-evaluate, and so is a row whose launch line is missing from at least one of
the two rosters. The
check is LEXICAL: it proves each line is present, not that it runs or passes (a CI job or step
condition, or a local run stopped early, is outside it).
The child runs with
cwd at the repo root and a git-neutral environment built in two layers: HOME and XDG_CONFIG_HOME are
pinned to a fresh empty directory, so the per-user global config, ignore, and attributes surfaces are
empty for EVERY descendant git call, including one made after a descendant scrubs the GIT_*
environment (the registered runner imports aiqt_hooks, whose recovery-snapshot code takes exactly
that allowlist posture, dropping any GIT_* pin this gate injected); and, for the immediate child,
every ambient GIT_* variable is dropped, GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM are pinned to
os.devnull with GIT_CONFIG_NOSYSTEM=1, and core.excludesFile and core.attributesFile are overridden
to os.devnull through injected GIT_CONFIG_COUNT/KEY/VALUE entries. The child environment also drops
every ambient PYTHON*-prefixed variable: -I isolates only the immediate child, which passes its
environment on to the interpreter grandchildren the runner launches, so a hostile PYTHONPATH would
otherwise inject an attacker sitecustomize module into them; unlike the GIT_* pins this drop IS
transitive (nothing downstream re-derives or re-sets a PYTHON* variable), and the runner's own
grandchild launch sites carry -I -B as well, defence in depth for a direct developer run. Three
launch-environment bounds are
disclosed rather than implied closed. First, the system-wide surfaces: the system-wide git config
($(prefix)/etc/gitconfig) is neutralized only for the immediate child (GIT_CONFIG_NOSYSTEM and the
GIT_CONFIG_SYSTEM pin are themselves GIT_-prefixed, so a scrubbing descendant drops them), and the
system-wide attributes file ($(prefix)/etc/gitattributes) is not neutralized at all (no
GIT_ATTR_NOSYSTEM is injected, and a scrubbing descendant would drop it the same way); a hostile
system-wide file is root-owned, the same trust tier as the git binary itself, so it sits at the
threat-model boundary, outside what this gate defends. Second, git itself is resolved through the
ambient PATH, a trusted-toolchain concern outside this gate's threat model, the same disclosure the
sibling branch-root gate carries. Third, the environment drop neutralizes the PYTHON*-prefixed
family only: a loader-level injection control such as LD_PRELOAD, and the interpreter binary
sys.executable itself, sit at the same trusted-toolchain tier, outside this gate's threat model.
The child's full stdout and stderr are forwarded UNFILTERED to this
gate's own streams (no grep, no truncation), and its verdict is judged by its real return code, its
error stream, and the strict report reconcile, never by its prose. The fail-closed child contract
(.aiqt/core/rules/security-seci-fail-closed.md): a pass needs a zero exit, the report FINALIZED at the
child's exit, and a WHOLE error stream byte-identical to the suite's declared bytes (DECLARED_STDERR;
a suite with no entry must leave it empty, and no registered suite declares any). The child arms
tools/_selftest_exit_report.py as its first atexit registration, so the report is written only after
every non-daemon thread is joined, every later atexit callback has run, and a bounded garbage
collection; a refusing child ends with os._exit(2), while the reporting path returns, so the process
ends with the interpreter's own exit status; the report carries format_version 2, finalized
true, and the exit_code that must equal the child's real exit status. A fault the child survived (an
atexit callback, a destructor, a thread) therefore refuses the verdict when it reaches the error
stream, which a destructor fault at late interpreter teardown can fail to do (disclosed below); a
thread that ends in a failure SystemExit, which writes nothing to stderr, a thread started through
_thread directly at any moment from process start (whose SystemExit _thread ignores silently), an
installed trace, profile or monitoring callback (the audit events sys.settrace, sys.setprofile and
sys.monitoring.register_callback; such a callback runs in a thread's frames after run(), where a
SystemExit it raises is dropped silently by _thread), a
delivered interpreter-creation audit event (cpython.PyInterpreterState_New; measured on CPython
3.14.4 as never delivered to an in-process hook), a call of an _interpreters function that creates,
runs code in, or destroys an interpreter (the bootstrap imports _interpreters first and replaces
those functions with refusals, so the import itself, which CPython 3.14's concurrent.futures and so
asyncio make at module level, is no fault), and a fresh load of an interpreter-creating extension
module (_interpreters, _xxsubinterpreters, _testcapi, _testinternalcapi), identified by the
extension FILE it loads from, by identity or content, under any module name, or by its exact plain
name (every in-process hook is per-interpreter, so code in another interpreter is unobservable and
creating or driving one is itself the fault; an interpreter created through any other C extension or
ctypes is the disclosed C-extension tier), a thread started or an atexit callback
registered after the exit-time thread join (neither is ever joined or run), and a status recorded
through exit_with that the interpreter's own exit did not confirm (its exit was caught before the
process left by another path, or an earlier exit_with outlived it), each,
when it occurs before the finalizer runs, make the child write no report and exit 2 (one that occurs
at interpreter teardown, after the report is written, refuses nothing; and the finalizer detects a
late atexit registration by comparing callback counts, so an atexit.unregister made after the join
can mask one; both disclosed below); a run that arms the finalizer without the bootstrap's
hooks (a launch that bypassed this gate) exits 2 the same way; and an in-band (format 1) report is
refused. The finalizer's reporting path defers to the interpreter's own exit status rather than
overriding it, so the exit_code reconcile above is against the status the child REALLY exited with:
a confirmation forged by a C callable reading the caught exception's code with no Python frame on
the stack (for example a weakref callback) cannot make a failure exit pass, because either the real
exit status contradicts the report (refused here) or the process really exited with the recorded
status (nothing was hidden). The
child-side teardown residual (a daemon thread is killed, post-report teardown destructor effects and
faults, a late atexit registration masked by an atexit.unregister under the finalizer's count
comparison, an execution context created or an asynchronous exception injected below the audited
Python surface by a C extension or ctypes) is disclosed in that module. A report that is missing, truncated, malformed,
wrong-suite, non-regular, or carrying a duplicate or wrong-typed entry is CANNOT-EVALUATE, never a
pass, whatever the child's exit code; completeness is never inferred from output volume or from the
absence of a reported problem.

Exit convention: 0 the execution set matches exactly AND the child passed; 1 a real finding (a missing
or extra check id, or a set-complete child that itself reported assertion failures); 2 usage or
cannot-evaluate (an unreadable, malformed, or suite-missing expectation manifest; a runner that is
absent, non-regular, a symlink, or escapes the repo; runner source that does not parse or carries an
unresolvable, aliased (a non-call check reference), comprehension-bound, or duplicate check id; a
static source-to-manifest set mismatch (for rows that share a runner, a group whose expected sets do
not partition the static set, or whose args are missing or repeated); a malformed args list, or one
naming the report option; a suite id outside the closed suite-id grammar; a runner path carrying
a control or format character; any row's runner path that
does not resolve strictly (a missing path, a non-directory component, a symlink loop or a permission
error); a registered row whose launch line is missing from at least one launch roster, or a launch
roster that is missing, not a regular file, unreadable or rejected by its loader; an unconfirmable
repo root;
unavailable temp storage; a launch failure; a child return code outside {0, 1}; a child error
stream that differs by any byte from the suite's declared bytes; or an invalid, unfinalized, or
exit-status-mismatched report).

DISCLOSED RESIDUAL: this gate proves INVOCATION IDENTITY only. It does not prove an invoked assertion is
discriminating (a constant-true check counts as executed), carries no mutation sensitivity, sees nothing
outside the instrumented check() helper (an assertion made by a direct FAILURES.append outside the
choke point, whose non-execution is invisible to the execution set, a direct EXECUTED.append or
_EXECUTED_SET.add that manufactures execution evidence for a check
that never really ran, or a suite not registered in the manifest), and cannot distinguish a legitimate
from an illegitimate coordinated deletion of a check and its manifest row in one reviewed change; the
expectation manifest is trusted hand-authored input whose schema is machine-validated here but whose
completeness rests on source review. The static layer proves
source-to-manifest SET EQUALITY, not reachability (a dead literal call still counts as a source site;
proving execution is the runtime layer's job), and resolves only DIRECT call sites of the bare name
check with string-literal or for-loop-literal ids: a non-call reference to that bare name (an alias
would carry a call site beyond the scan), an id Name bound inside a comprehension (whose scope the
lexical for-stack does not model), and a statically empty literal loop (whose check() site would
otherwise resolve to no id at all) are refused fail-closed, never silently resolved. Two static
blind spots remain, bounded the same way. First, an invocation route the static layer never sees a
call site for, because it never names check as a bare Name in Load context: getattr, exec, an
import that binds a different name and is then called through it, or a globals()['check'] lookup.
Second, a rebinding of the name check the static layer cannot detect (any Store-class or non-Load
binding: an assignment, a with-as or except-as target, a nested def check or class check, an
import check, a del check, or a global or nonlocal declaration), which leaves the direct call
sites visible and counted while they may no longer invoke the instrumented helper. Both are
caught, if at all, by the runtime set reconcile; a registered id whose ONLY route is invisible (no
direct call site at all) is refused by the static layer pre-launch (registered id has no source
check()), and the runtime reconcile is the catcher specifically for the dead-direct-site decoy, a
route that re-executes an already-registered id whose direct call site is dead, which passes both
set layers and is bounded only by the non-discrimination residual (a constant-true check counts as
executed). The remaining
lexical binding (the innermost enclosing for) does not model any intervening dataflow between the
loop header and the call site (a rebinding of the loop variable, a nested function boundary), so
such a site can mis-attribute its ids; the runtime
reconcile still proves the executed set independently. Repo-root confirmation is a .git-existence
proxy anchored to this gate's own file, not a full git-identity check; and the static scan and the
launch read the runner's source at two moments, so a concurrent same-user writer between them is
outside this repo's sole-orchestrator threat model (the runtime layer still reconciles what
actually ran). The group rule proves a PARTITION of the static set for the requested suite's group
only, once per invocation, and launches that one row: that every other row is launched rests on the
launch-coverage check, which proves only that a launch line for each row is present in both rosters
(a lexical check, not proof that the line runs or passes); nor does the group rule prove that the
runner's handling of its args selects the declared subset other than through the runtime reconcile
of the row actually launched; and a runner reached through a hard link or a byte copy under
another path resolves to another path, so it forms its own group, held alone to the whole static set
(fail-safe: an undetected group member can only be refused, never pass with a subset). The
fail-closed child contract has two permitted residuals. First, a destructor fault
at late interpreter teardown can pass silently. The residual is a CLASS, not a list of placements: a
destructor of an object whose last reference is held by interpreter-level state that CPython clears
after it has dropped sys.stderr (for example the codec search registry (codecs.register), audit
hooks (sys.addaudithook), and a cycle anchored on a sys attribute) runs and raises after the report
is written with an empty error stream, and this gate passes the run; such a fault is silent under
the gate exactly as under a direct launch. The examples are not exhaustive and no further placement
is enumerated; no in-process fix is known, and the self-test pins the three named examples as a
residual witness.
Second, loaded code replacing the reporting machinery: code running inside the child can write a complete finalized-shape report itself
(for example, then os._exit(0), whether or not it armed the finalizer), or replace sys.stderr,
sys.unraisablehook, threading.excepthook, threading._shutdown, threading.Thread._bootstrap
or the private per-thread run machinery it calls (a threading.Thread subclass overriding
_bootstrap_inner, a replaced _invoke_excepthook), os._exit, the atexit registrations, or the finalizer's state; this gate sees the child only from outside and no in-process secret is hidden from that code,
so the channel is closed only by the suite's own source being reviewed and pinned. The child runs un-timed
(parity with the roster's other selftest steps; the CI job timeout is the outer bound).
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_selftest_execution.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import contextlib
import dis
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import types
import unicodedata
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    import os
    try:
        sys.stderr.write(
            "error: check_selftest_execution.py cannot import tomllib, part of the Python standard library; "
            "this installation is incomplete. Nothing was run (cannot evaluate).\n")
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import check_ci_parity  # noqa: E402  (the validated launch-roster loaders)
except ImportError as _parity_import_error:
    # A plain fallback: the launch-coverage check refuses it before any launch (cannot evaluate), so
    # an exported copy without its sibling still reaches main's own repo-root refusal.
    check_ci_parity = None
    PARITY_IMPORT_ERROR = _parity_import_error

MANIFEST_TOP_KEYS = {"format-version", "suite"}
SUITE_ROW_KEYS = {"id", "runner", "expected-check-ids"}
# The one optional row key: literal arguments forwarded to the runner's launch ahead of the gate's own
# --execution-report pair (_row_args_problem holds the accepted shape).
SUITE_ROW_OPTIONAL_KEYS = {"args"}
# One forwarded argument: printable ASCII from a closed set (no whitespace, quote, shell, glob or
# expansion character), starting with a letter or digit, or with one or two dashes and then one (so
# no leading '/', '.', '@' or '~', and no bare '-' or '--' end-of-options marker).
ROW_ARG_RE = re.compile(r"(?:--?)?[A-Za-z0-9][A-Za-z0-9._,:=/+-]*")
ROW_ARG_MAX = 200
# The gate's own evidence option: no forwarded argument may name it, attach a value to it, or
# abbreviate it.
REPORT_OPTION = "--execution-report"
# The two launch rosters, each read through the parity gate's validated loader for that file: every
# registered [[suite]] row must have its launch member (SUITE_LAUNCH, as the loader normalizes it) in
# both before any suite is launched.
LAUNCH_ROSTERS = (("tools/run_all_checks.sh", "extract_local"),
                  (".github/workflows/quality.yml", "extract_ci"))
GATE_REL = "tools/check_selftest_execution.py"
SUITE_LAUNCH = GATE_REL + " --suite {}"
# A suite id is one launch-line argument in both rosters and the one CLI argument after --suite, so
# it comes from a CLOSED grammar: a letter or digit first, then letters, digits, '.', '_' and '-'
# only. An id outside it is refused BY NAME, so a space-joined roster identity can never tokenize a
# registered id as more than one argument, and an id can never spell an option (no leading '-').
SUITE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
REPORT_KEYS = {"format_version", "suite", "check_ids", "exit_code", "finalized"}
REPORT_FORMAT_VERSION = 2
# Per suite id, the exact bytes (a bytes value) the child's WHOLE error stream must equal on a run that
# is a verdict; a suite with no entry must leave the stream empty, and any other byte is a fault, never
# a pass. Measured through this gate on a passing run: orch-behaviour-selftest,
# ci-status-behaviour-selftest, instruction-budget-selftest, python-floor-selftest and
# entry-guard-selftest each write 0 stderr bytes, and the four git-fixture-env suites
# (git-fixture-env-core, git-fixture-env-config-combined, git-fixture-env-config-malformed and
# git-fixture-env-config-system), each run to completion through this gate with the empty
# allowance (gate exit 0), wrote 0 stderr bytes too, so all nine suites were measured at 0
# stderr bytes.
DECLARED_STDERR = {}


def _cannot(msg):
    """Print a cannot-evaluate finding (the caller returns 2). A pending, missing, ambiguous, or
    malformed input is unverified, never a pass."""
    print("CANNOT EVALUATE: {}".format(msg), file=sys.stderr)


def _has_ctrl(text):
    """True when text carries a control or format character, decided by this one shared predicate:
    Unicode category Cc (C0, DEL and C1), Cf (the format characters, the bidi controls among them),
    Zl (U+2028) or Zp (U+2029)."""
    return any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") for ch in text)


def _manifest_suites(manifest_path):
    """The strictly validated [[suite]] rows of the expectation manifest, or None after printing the
    violation (the caller exits 2). Present-but-unparseable is a refusing failure naming the record,
    never silent absence."""
    try:
        st = os.lstat(manifest_path)
    except OSError as exc:
        _cannot("expectation manifest {} unreadable: {}".format(manifest_path, exc))
        return None
    if not stat.S_ISREG(st.st_mode):
        _cannot("expectation manifest {} is not a regular file (a symlink or directory is refused)"
                .format(manifest_path))
        return None
    try:
        with open(manifest_path, "rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        _cannot("expectation manifest {} unreadable: {}".format(manifest_path, exc))
        return None
    # ValueError and RecursionError too: tomllib raises a BARE ValueError (not TOMLDecodeError) on an
    # integer literal past CPython's 4300-digit int-string limit, and a RecursionError (a RuntimeError)
    # on a deeply nested array or inline table (F-TOML-BARE-VALUEERROR-CLASS).
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        _cannot("expectation manifest {} is not valid TOML: {}".format(manifest_path, exc))
        return None
    return _manifest_rows(data, manifest_path)


def _manifest_rows(data, manifest_path):
    """The strictly validated [[suite]] rows of an already-parsed expectation manifest, or None after
    printing the violation (the caller exits 2). Shared with the entry-guard gate, so both gates hold the
    registry to one schema."""
    if set(data) != MANIFEST_TOP_KEYS:
        _cannot("{}: top-level keys must be exactly {} (got {})".format(
            manifest_path, sorted(MANIFEST_TOP_KEYS), sorted(data)))
        return None
    if type(data["format-version"]) is not int or data["format-version"] != 1:
        _cannot("{}: format-version must be exactly the integer 1".format(manifest_path))
        return None
    suites = data["suite"]
    if not isinstance(suites, list) or not suites:
        _cannot("{}: [[suite]] must be a non-empty array of tables".format(manifest_path))
        return None
    seen_ids = set()
    for index, row in enumerate(suites):
        where = "{}: suite[{}]".format(manifest_path, index)
        if (not isinstance(row, dict) or not SUITE_ROW_KEYS <= set(row)
                or not set(row) <= SUITE_ROW_KEYS | SUITE_ROW_OPTIONAL_KEYS):
            _cannot("{}: keys must be exactly {} (optionally also {})".format(
                where, sorted(SUITE_ROW_KEYS), sorted(SUITE_ROW_OPTIONAL_KEYS)))
            return None
        sid = row["id"]
        if not isinstance(sid, str) or not SUITE_ID_RE.fullmatch(sid):
            _cannot("{}: id {!r} must match the closed suite-id grammar (a letter or digit first, "
                    "then letters, digits, '.', '_' and '-' only); the id is a single "
                    "launch-line argument, so any other name is refused".format(where, sid))
            return None
        if sid in seen_ids:
            _cannot("{}: duplicate suite id {!r}".format(where, sid))
            return None
        seen_ids.add(sid)
        runner = row["runner"]
        if (not isinstance(runner, str) or not runner or _has_ctrl(runner) or os.path.isabs(runner)
                or ".." in Path(runner).parts):
            _cannot("{}: runner must be a repo-relative path with no '..' and no control or "
                    "format character".format(where))
            return None
        ids = row["expected-check-ids"]
        if not isinstance(ids, list) or not ids:
            _cannot("{}: expected-check-ids must be a non-empty array".format(where))
            return None
        row_seen = set()
        for cid in ids:
            if not isinstance(cid, str) or not cid or _has_ctrl(cid):
                _cannot("{}: check id {!r} must be a non-empty string with no control or format "
                        "character".format(where, cid))
                return None
            if cid in row_seen:
                _cannot("{}: duplicate expected check id {!r}".format(where, cid))
                return None
            row_seen.add(cid)
        if "args" in row:
            problem = _row_args_problem(row["args"])
            if problem is not None:
                _cannot("{}: {}".format(where, problem))
                return None
    return suites


def _row_args_problem(args):
    """None when args is a forwardable argument list, else the reason it is refused: a non-empty array
    of strings, each at most ROW_ARG_MAX characters and matching ROW_ARG_RE, none naming, carrying a
    value for, or abbreviating the gate's own REPORT_OPTION (a runner resolving a unique prefix, as
    argparse does, would otherwise read it as that option). Lexical only: it says nothing about what
    any other accepted argument means to a given runner; a runner that then writes no report is a
    missing report, cannot-evaluate."""
    if not isinstance(args, list) or not args:
        return "args must be a non-empty array of strings (omit the key for no arguments)"
    for arg in args:
        if type(arg) is not str:
            return "args element {!r} is not a string".format(arg)
        if len(arg) > ROW_ARG_MAX or not ROW_ARG_RE.fullmatch(arg):
            return ("args element {!r} is not a literal argument (at most {} characters from "
                    "[A-Za-z0-9._,:=/+-], a letter or digit first, or one or two dashes and then "
                    "one)".format(arg, ROW_ARG_MAX))
        name = arg.split("=", 1)[0]
        if name.startswith("--") and (REPORT_OPTION.startswith(name) or name.startswith(REPORT_OPTION)):
            return ("args element {!r} names, abbreviates, or attaches a value to the gate's own {} "
                    "option".format(arg, REPORT_OPTION))
    return None


def _runner_path(root, runner):
    """The runner resolved inside the repo as a regular non-symlink file, or None after printing (the
    caller exits 2 and NEVER launches)."""
    path = Path(root) / runner
    try:
        st = os.lstat(path)
    except OSError as exc:
        _cannot("runner {} unreadable: {}".format(path, exc))
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        _cannot("runner {} is not a regular non-symlink file".format(path))
        return None
    real_root = os.path.realpath(root)
    try:
        contained = os.path.commonpath([os.path.realpath(path), real_root]) == real_root
    except ValueError:
        contained = False
    if not contained:
        _cannot("runner {} escapes the repo root {}".format(path, root))
        return None
    return path


def _runner_group(root, suites, row):
    """The rows whose runner resolves to the same path as row's (row included, in manifest order), or
    None after printing (the caller exits 2 and NEVER launches). Membership is by resolved path, so
    two spellings of one runner form one group. Every row's runner is resolved STRICTLY
    (os.path.realpath(strict=True)): a missing path, a non-directory component, a symlink loop or a
    permission error leaves that row's membership undecidable, so it is cannot-evaluate, never an
    assumed non-member (a non-strict resolution swallows each of them and returns a lexical guess)."""
    resolved = {}
    for other in suites:
        try:
            resolved[other["id"]] = os.path.realpath(Path(root) / other["runner"], strict=True)
        except (OSError, ValueError, RuntimeError) as exc:
            _cannot("suite {}: cannot decide which rows share its runner, because the runner {} of "
                    "suite {} does not resolve: {}".format(
                        row["id"], other["runner"], other["id"], exc))
            return None
    return [other for other in suites if resolved[other["id"]] == resolved[row["id"]]]


def _unlaunched_rows(root, suites):
    """The diagnostic lines naming each registered row whose launch line is missing from a launch
    roster (none when every row is launched from both), or None after printing (the caller exits 2
    and NEVER launches) when a roster is not a regular non-symlink file, cannot be read, or draws a
    diagnostic from the parity gate's loader. A roster member counts as a row's launch line only
    when it is EXACTLY [GATE_REL, "--suite", <id>], the one argv shape main() accepts, so a member
    carrying any further token, or a near-miss id, launches nothing. Read-only and LEXICAL: it
    proves each row's launch line is present in each roster, not that the line runs or passes."""
    if check_ci_parity is None:
        _cannot("the parity gate's roster loaders (check_ci_parity.py beside this gate) cannot be "
                "imported, so the launch rosters cannot be read: {}".format(PARITY_IMPORT_ERROR))
        return None
    lines = []
    for rel, loader in LAUNCH_ROSTERS:
        path = Path(root) / rel
        try:
            st = os.lstat(path)
        except OSError as exc:
            _cannot("launch roster {} unreadable: {}".format(path, exc))
            return None
        if not stat.S_ISREG(st.st_mode):
            _cannot("launch roster {} is not a regular file (a symlink or directory is refused)"
                    .format(path))
            return None
        text, diagnostic = check_ci_parity.read_runner_text(path, rel)
        if diagnostic is None:
            extraction = getattr(check_ci_parity, loader)(text)
            diagnostic = extraction.diagnostics[0] if extraction.diagnostics else None
        if diagnostic is not None:
            _cannot("launch roster {} cannot be evaluated: line {}: {}".format(
                rel, diagnostic.line, diagnostic.message))
            return None
        launched_ids = set()
        for member in extraction.members:
            argv = member.split(" ")
            # Exactly the command main() accepts (["--suite", <id>] after the script): a member
            # carrying any further token is some other command, never a launch of <id>.
            if len(argv) == 3 and argv[0] == GATE_REL and argv[1] == "--suite" and argv[2]:
                launched_ids.add(argv[2])
        for other in suites:
            if other["id"] not in launched_ids:
                lines.append("  suite {} has no launch line ({}) in {}".format(
                    other["id"], SUITE_LAUNCH.format(other["id"]), rel))
    return lines


def _group_problems(group, source_set):
    """The group rule for two or more rows that share one runner, as diagnostic lines (none when it
    holds): every row carries args, no two rows carry the same args, the rows' expected sets are
    pairwise disjoint, and their union equals the runner's static check() set exactly."""
    lines, launches, owners = [], {}, {}
    for row in group:
        if "args" not in row:
            lines.append("  suite {} shares its runner but carries no args, so its launch cannot "
                         "differ from the others'".format(row["id"]))
        else:
            launch = tuple(row["args"])
            if launch in launches:
                lines.append("  suites {} and {} carry the same args {}".format(
                    launches[launch], row["id"], list(launch)))
            else:
                launches[launch] = row["id"]
        for cid in row["expected-check-ids"]:
            owners.setdefault(cid, []).append(row["id"])
    for cid in sorted(owners):
        if len(owners[cid]) > 1:
            lines.append("  overlap: {} registered by suites {}".format(cid, ", ".join(owners[cid])))
    for cid in sorted(source_set - set(owners)):
        lines.append("  gap: source check registered by no suite of the group: {}".format(cid))
    for cid in sorted(set(owners) - source_set):
        lines.append("  extra: registered id has no source check(): {}".format(cid))
    return lines


class _CheckIdVisitor(ast.NodeVisitor):
    """Resolve the first argument of every DIRECT call to the bare name check() to string-literal
    ids. Two patterns only: a string constant, or a Name bound by an enclosing for over a tuple/list
    of tuple/list rows carrying a string literal at that Name's target position. Anything else is a
    fail-closed error, never a guess: a Load of the bare name check that is not the callee of a call
    this visitor resolves (an alias such as renamed = check would carry a call site beyond this
    scan's reach) and a Name id inside a comprehension (whose generator binding the lexical
    for-stack does not model, so an enclosing for could mis-attribute it) are both refused. ast
    carries no parent links, so the visitor keeps its own stack of the enclosing For nodes, a
    comprehension depth, and the id() of every callee Name it resolved."""

    def __init__(self, runner_path):
        self.runner_path = runner_path
        self.for_stack = []
        self.comp_depth = 0
        self.callee_names = set()   # id() of each Name node resolved as a direct check() callee
        self.found = []      # (check id, line) per literal site or per resolved loop row
        self.errors = []

    def visit_For(self, node):
        self.for_stack.append(node)
        self.generic_visit(node)
        self.for_stack.pop()

    def _visit_comprehension(self, node):
        self.comp_depth += 1
        self.generic_visit(node)
        self.comp_depth -= 1

    visit_ListComp = _visit_comprehension
    visit_SetComp = _visit_comprehension
    visit_DictComp = _visit_comprehension
    visit_GeneratorExp = _visit_comprehension

    def visit_Name(self, node):
        if (node.id == "check" and isinstance(node.ctx, ast.Load)
                and id(node) not in self.callee_names):
            self.errors.append(
                "check referenced outside a direct call at {}:{}; the static layer resolves only "
                "direct check(...) call sites".format(self.runner_path, node.lineno))

    def visit_Call(self, node):
        is_check = isinstance(node.func, ast.Name) and node.func.id == "check"
        if is_check:
            # Marked BEFORE the generic descent reaches it, so visit_Name does not flag the very
            # reference this call resolves.
            self.callee_names.add(id(node.func))
        self.generic_visit(node)
        if not is_check:
            return
        arg = node.args[0] if node.args else None
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            self.found.append((arg.value, node.lineno))
            return
        if isinstance(arg, ast.Name) and self.comp_depth:
            self.errors.append(
                "check() id resolved inside a comprehension at {}:{}; unresolvable".format(
                    self.runner_path, node.lineno))
            return
        rows = self._loop_rows(arg.id) if isinstance(arg, ast.Name) else None
        if rows is None:
            self.errors.append(
                "unresolvable check id at {}:{}; every check id must be a string literal or a "
                "for-loop literal".format(self.runner_path, node.lineno))
        else:
            self.found.extend(rows)

    def _loop_rows(self, name):
        """The (id, line) rows the innermost enclosing for binds to Name <name>, or None when no
        enclosing for binds it as a tuple target over all-literal rows at its position, or when the
        literal iterable is empty (a statically empty loop would otherwise resolve its check() site
        to zero ids and silently drop it)."""
        for loop in reversed(self.for_stack):
            target = loop.target
            if isinstance(target, ast.Name):
                if target.id == name:
                    return None
                continue
            if not isinstance(target, ast.Tuple):
                continue
            names = [elt.id if isinstance(elt, ast.Name) else None for elt in target.elts]
            if name not in names:
                continue
            pos = names.index(name)
            if not isinstance(loop.iter, (ast.Tuple, ast.List)):
                return None
            rows = []
            for row in loop.iter.elts:
                if (not isinstance(row, (ast.Tuple, ast.List)) or len(row.elts) != len(names)
                        or not isinstance(row.elts[pos], ast.Constant)
                        or not isinstance(row.elts[pos].value, str)):
                    return None
                rows.append((row.elts[pos].value, row.lineno))
            return rows if rows else None
        return None


def _source_check_ids(runner_path):
    """The statically resolved check ids of every check() call site in the runner's source, as
    (sorted id list, None); or (None, error) on a source that does not parse, an unresolvable id, or
    a duplicate call-site id. Fail closed: no id is guessed, and source-site uniqueness is required
    (a dead duplicate-id alias would otherwise hide behind its reachable twin in the runtime set)."""
    try:
        source = Path(runner_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, "runner {} unreadable as UTF-8 source: {}".format(runner_path, exc)
    try:
        tree = ast.parse(source, filename=str(runner_path))
    except (SyntaxError, ValueError) as exc:
        return None, "runner {} does not parse: {}".format(runner_path, exc)
    visitor = _CheckIdVisitor(runner_path)
    visitor.visit(tree)
    if visitor.errors:
        return None, "; ".join(visitor.errors)
    first_seen = {}
    for cid, line in visitor.found:
        if cid in first_seen:
            return None, "duplicate source check id {!r} at lines {} and {} in {}".format(
                cid, first_seen[cid], line, runner_path)
        first_seen[cid] = line
    return sorted(first_seen), None


def _forward(blob, stream):
    """Forward the child's captured bytes IN FULL to our own stream: no filter, no truncation. Bytes go
    to the raw buffer where one exists; under an in-process capture (the self-test's StringIO) there is
    no buffer, so the bytes are decoded with replacement, which substitutes only invalid sequences and
    never drops content."""
    if not blob:
        return
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        stream.flush()
        buffer.write(blob)
        buffer.flush()
    else:
        stream.write(blob.decode("utf-8", errors="replace"))


def _reject_dup_keys(pairs):
    """json object_pairs_hook: a duplicate member is a malformed report, refused, so a doctored
    report cannot carry two check_ids members and have the last one silently win."""
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate JSON object member {!r}".format(key))
        obj[key] = value
    return obj


def _read_report(report_path, suite_id, returncode):
    """The report's check-id list, strictly validated, or None after printing (the caller exits 2). A
    child exit 0 never overrides an invalid report; a run that did not deliver its agreed structured
    verdict evidence is no verdict. Only the FINALIZED report (tools/_selftest_exit_report.py, written
    at interpreter exit after the child's cleanup, its exit_code the status the child then exited
    with) is accepted; an in-band format-1 report is refused."""
    try:
        st = os.lstat(report_path)
    except OSError:
        _cannot("no execution report at {}; a run that did not deliver its report is no verdict"
                .format(report_path))
        return None
    if not stat.S_ISREG(st.st_mode):
        _cannot("execution report {} is not a regular file (a symlink or directory is refused)"
                .format(report_path))
        return None
    try:
        with open(report_path, "r", encoding="utf-8") as handle:
            data = json.load(handle, object_pairs_hook=_reject_dup_keys)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        # ValueError covers json.JSONDecodeError and the duplicate-member rejection above.
        _cannot("execution report {} unreadable or malformed: {}".format(report_path, exc))
        return None
    if not isinstance(data, dict) or set(data) != REPORT_KEYS:
        _cannot("execution report {}: keys must be exactly {}".format(report_path, sorted(REPORT_KEYS)))
        return None
    if type(data["format_version"]) is not int or data["format_version"] != REPORT_FORMAT_VERSION:
        _cannot("execution report {}: format_version must be exactly the integer {} (the report "
                "finalized at exit)".format(report_path, REPORT_FORMAT_VERSION))
        return None
    if data["finalized"] is not True:
        _cannot("execution report {}: finalized must be exactly true".format(report_path))
        return None
    if type(data["exit_code"]) is not int or data["exit_code"] != returncode:
        _cannot("execution report {}: exit_code {!r} is not the child's exit status {}".format(
            report_path, data["exit_code"], returncode))
        return None
    if data["suite"] != suite_id:
        _cannot("execution report {}: suite {!r} is not the requested suite {!r}".format(
            report_path, data["suite"], suite_id))
        return None
    ids = data["check_ids"]
    if not isinstance(ids, list) or not all(isinstance(cid, str) and cid for cid in ids):
        _cannot("execution report {}: check_ids must be a list of non-empty strings".format(report_path))
        return None
    if len(set(ids)) != len(ids):
        dupes = sorted(cid for cid in set(ids) if ids.count(cid) > 1)
        _cannot("execution report {}: duplicate observed check ids {}".format(report_path, dupes))
        return None
    return ids


def _stderr_fault(blob, suite_id):
    """The child's WHOLE error stream compared byte for byte with the suite's declared bytes (empty when
    the suite declares none): None on equality, else a description of the first differing byte. No
    decoding, line splitting, repetition, or reordering is applied, and a declaration that is not a
    bytes value is itself a fault. A fault the child survived (an atexit callback, a destructor, a
    thread) that writes to the error stream reaches only this stream, so it is never a pass; a fault
    that writes nothing (the disclosed late-teardown residual class) is invisible here."""
    allowed = DECLARED_STDERR.get(suite_id, b"")
    if type(allowed) is not bytes:
        return "the declared stderr for suite {!r} is not a bytes value".format(suite_id)
    if blob == allowed:
        return None
    at = next((i for i, (got, want) in enumerate(zip(blob, allowed)) if got != want),
              min(len(blob), len(allowed)))
    return "stderr differs from the {} declared bytes at byte {}: {!r}".format(
        len(allowed), at, blob[at:at + 200])


def run_suite(root, suite_id):
    """Validate the manifest, statically reconcile the runner's source check() set against it BEFORE
    any launch (the whole set for a single-row runner; for rows that share a runner, the group
    partition), require a launch line for every registered row in both launch rosters, then launch
    the registered runner with the row's args and a private report path, forward its full output,
    require an error stream byte-identical to the suite's declared bytes (empty unless declared), and
    reconcile the executed set from the report finalized at the child's exit. Returns the gate exit
    code."""
    root = Path(root)
    manifest_path = root / "tools" / "selftest_checks.toml"
    suites = _manifest_suites(manifest_path)
    if suites is None:
        return 2
    row = next((r for r in suites if r["id"] == suite_id), None)
    if row is None:
        _cannot("no suite {!r} registered in {}".format(suite_id, manifest_path))
        return 2
    expected = set(row["expected-check-ids"])
    runner = _runner_path(root, row["runner"])
    if runner is None:
        return 2
    group = _runner_group(root, suites, row)
    if group is None:
        return 2
    source_ids, error = _source_check_ids(runner)
    if error is not None:
        _cannot(error)
        return 2
    source_set = set(source_ids)
    if len(group) > 1:
        problems = _group_problems(group, source_set)
        if problems:
            _cannot("suite {}: the {} rows of {} sharing its runner do not partition the runner's "
                    "static check() set; rows that share a runner need pairwise distinct args, "
                    "pairwise disjoint expected sets, and a union equal to the source set exactly "
                    "before any launch".format(suite_id, len(group), manifest_path))
            for line in problems:
                print(line, file=sys.stderr)
            return 2
    elif source_set != expected:
        _cannot("suite {}: the runner's static check() set does not match {}; the manifest must "
                "mirror the source exactly before any launch".format(suite_id, manifest_path))
        for cid in sorted(source_set - expected):
            print("  source check not registered: {}".format(cid), file=sys.stderr)
        for cid in sorted(expected - source_set):
            print("  registered id has no source check(): {}".format(cid), file=sys.stderr)
        return 2
    unlaunched = _unlaunched_rows(root, suites)
    if unlaunched is None:
        return 2
    if unlaunched:
        _cannot("suite {}: not every row of {} has a launch line in both launch rosters; a registered "
                "row that is never launched leaves its ids unexecuted, so no suite is launched until "
                "every row is".format(suite_id, manifest_path))
        for line in unlaunched:
            print(line, file=sys.stderr)
        return 2
    try:
        private = tempfile.mkdtemp(prefix="aiqt-selftest-exec-")
    except OSError as exc:
        _cannot("cannot create the private report directory: {}".format(exc))
        return 2
    try:
        report_path = os.path.join(private, "execution-report.json")
        if os.path.lexists(report_path):
            _cannot("report path {} already exists before launch".format(report_path))
            return 2
        # The fresh EMPTY per-launch home the child's HOME and XDG_CONFIG_HOME pin to (see the
        # child-env comment below): under `private`, so the finally's rmtree removes it too.
        git_home = os.path.join(private, "home")
        try:
            os.mkdir(git_home)
        except OSError as exc:
            _cannot("cannot create the neutral child home directory: {}".format(exc))
            return 2
        # The bootstrap shim: the helper module beside the runner installs the observation hooks
        # BEFORE any runner statement (import-time included), then runs the runner as __main__ with
        # the [<runner>, <row args>..., --execution-report, <path>] argv it expects (_bootstrap_main
        # documents the shim's own argv contract; the row's args, validated by _row_args_problem,
        # follow the report path here and are absent for a row with no args key). A raw launch of
        # the runner would leave its import-time and pre-arm code outside the hooks, the round-2
        # pre-arm thread gap.
        bootstrap = ("import sys; sys.path.insert(0, sys.argv[1]); "
                     "import _selftest_exit_report; _selftest_exit_report._bootstrap_main()")
        command = [sys.executable, "-I", "-B", "-c", bootstrap,
                   str(runner.parent), str(runner), report_path] + list(row.get("args", ()))
        # Git-neutral, interpreter-neutral child environment. The git side has two layers. Layer
        # one, GIT_*: drop every ambient GIT_*
        # variable, then pin the global and system config surfaces to os.devnull, so a hostile
        # GIT_CONFIG_GLOBAL or core.hooksPath cannot make the child's git subprocesses run an
        # external hook or read a decoy repository (the runner's fixtures set their git identity
        # inline with -c, which outranks the injected entries below, so commits still work).
        # GIT_CONFIG_GLOBAL covers only the global CONFIG file: git reads the global ignore and
        # attributes surfaces from XDG_CONFIG_HOME/git/{ignore,attributes} defaults even with no
        # config file, so core.excludesFile and core.attributesFile are overridden to os.devnull
        # through git's environment-config mechanism (GIT_CONFIG_COUNT/KEY/VALUE, set AFTER the
        # drop loop so they survive it). Layer two, HOME/XDG_CONFIG_HOME: the GIT_* pins alone are
        # NOT transitive, because the child imports aiqt_hooks, whose recovery-snapshot code scrubs
        # every GIT_*-prefixed variable before its own git subprocesses (the allowlist posture the
        # inert-data rule requires), dropping the layer-one pins and re-exposing ~/.gitconfig and
        # XDG_CONFIG_HOME/git/{config,ignore,attributes} to every descendant git call; HOME and
        # XDG_CONFIG_HOME are not GIT_-prefixed, so that scrub cannot strip them, and pinning both
        # at the fresh empty git_home keeps the per-user config, ignore, and attributes surfaces
        # empty for EVERY descendant call, scrubbed or not (layer one stays as defence in depth for
        # the immediate child; the system-wide config and attributes residual for a scrubbing
        # descendant is disclosed in the module docstring). The drop loop also strips every ambient
        # PYTHON*-prefixed variable: -I isolates only the immediate child, which passes its
        # environment on, and the runner launches interpreter grandchildren, so a hostile
        # PYTHONPATH would otherwise run an attacker sitecustomize module in them. Unlike the GIT_*
        # pins this drop IS transitive, because nothing downstream re-derives or re-sets a PYTHON*
        # variable, so no descendant interpreter ever sees one; the runner's grandchild launch
        # sites carry -I -B as well, defence in depth for a direct developer run. This hardens the
        # GATE-launched child only; the runner's own direct-invocation hermeticity is tracked
        # separately (F-249).
        child_env = dict(os.environ)
        for key in list(child_env):
            if key.startswith("GIT_") or key.startswith("PYTHON"):
                del child_env[key]
        child_env["GIT_CONFIG_GLOBAL"] = os.devnull
        child_env["GIT_CONFIG_SYSTEM"] = os.devnull
        child_env["GIT_CONFIG_NOSYSTEM"] = "1"
        child_env["GIT_CONFIG_COUNT"] = "2"
        child_env["GIT_CONFIG_KEY_0"] = "core.excludesFile"
        child_env["GIT_CONFIG_VALUE_0"] = os.devnull
        child_env["GIT_CONFIG_KEY_1"] = "core.attributesFile"
        child_env["GIT_CONFIG_VALUE_1"] = os.devnull
        child_env["HOME"] = git_home
        child_env["XDG_CONFIG_HOME"] = git_home
        try:
            child = subprocess.run(command, cwd=str(root), capture_output=True, env=child_env)
        except OSError as exc:
            _cannot("cannot launch {}: {}".format(command, exc))
            return 2
        _forward(child.stdout, sys.stdout)
        _forward(child.stderr, sys.stderr)
        if child.returncode not in (0, 1):
            _cannot("child exited {} (a harness error or signal is no verdict)".format(child.returncode))
            return 2
        fault = _stderr_fault(child.stderr, suite_id)
        if fault is not None:
            _cannot("suite {}: the child's error stream carries a fault ({}); a run that faulted, "
                    "even after its last check, is no verdict".format(suite_id, fault))
            return 2
        observed = _read_report(report_path, suite_id, child.returncode)
        if observed is None:
            return 2
        missing = sorted(expected - set(observed))
        extra = sorted(set(observed) - expected)
        if missing or extra:
            print("FAIL: suite {} execution set does not match {}".format(suite_id, manifest_path))
            for cid in missing:
                print("  missing (registered, never executed): {}".format(cid))
            for cid in extra:
                print("  extra (executed, not registered): {}".format(cid))
            return 1
        if child.returncode == 1:
            print("FAIL: suite {}: execution set complete; child reported assertion failures "
                  "(see its output above)".format(suite_id))
            return 1
        digest = hashlib.sha256(("\n".join(sorted(observed)) + "\n").encode("utf-8")).hexdigest()
        print("PASS: suite {} executed {} unique registered checks; set-sha256={}".format(
            suite_id, len(observed), digest))
        return 0
    finally:
        shutil.rmtree(private, ignore_errors=True)


# -------------------------------------------------------------------------- the hermetic self-test

GOOD_IDS = ["a/one", "a/two", "a/three"]


def _manifest_text(ids=None, runner="tools/fake_runner.py", header="format-version = 1"):
    rendered = ", ".join('"{}"'.format(cid) for cid in (GOOD_IDS if ids is None else ids))
    return (header + '\n\n[[suite]]\nid = "demo"\nrunner = "' + runner
            + '"\nexpected-check-ids = [' + rendered + ']\n')


def _group_text(rows, runner="tools/fake_runner.py", header="format-version = 1\n"):
    """A manifest of several [[suite]] rows, each (suite id, ids, args[, runner]): args None omits the
    key, a list renders as a TOML array, and a str is raw TOML text (a malformed value)."""
    text = header
    for row in rows:
        sid, ids, args = row[:3]
        text += "\n[[suite]]\nid = {}\nrunner = {}\nexpected-check-ids = {}\n".format(
            json.dumps(sid), json.dumps(row[3] if len(row) > 3 else runner), json.dumps(ids))
        if args is not None:
            text += "args = {}\n".format(args if isinstance(args, str) else json.dumps(args))
    return text


# The local roster's dispatcher definition in the one shape the parity gate's loader recognizes, so a
# synthetic roster reaches its launch lines rather than a dispatcher diagnostic.
ROSTER_DISPATCHER = ('run_gate() {\n  local name="$1"; shift\n  echo "--- ${name} ---"\n'
                     '  if "$@"; then :; else\n    local rc=$?\n    failed=1\n'
                     '    failed_names="${failed_names:+${failed_names}, }${name}"\n'
                     '    echo "GATE FAILED: ${name} (exit ${rc})"\n  fi\n  echo\n}\n')


def _roster_texts(ids):
    """A synthetic local roster and CI workflow, each launching every suite id in ids through this gate,
    as {repo-relative path: text}, in the smallest shapes the parity gate's loaders accept."""
    local = 'failed=0\nfailed_names=""\n' + ROSTER_DISPATCHER
    ci = ("name: Quality\non:\n  push:\n    branches: [main]\npermissions:\n  contents: read\n"
          "jobs:\n  quality:\n    runs-on: ubuntu-latest\n    steps:\n")
    for sid in ids:
        launch = "python3 -I -B " + SUITE_LAUNCH.format(sid)
        local += 'run_gate "{}" {}\n'.format(sid, launch)
        ci += "      - name: {}\n        run: {}\n".format(sid, launch)
    return {LAUNCH_ROSTERS[0][0]: local, LAUNCH_ROSTERS[1][0]: ci}


def _lane_body(lanes):
    """A fake runner tail that records its argv beside itself, then reports, under the suite id its
    lane maps to, the ids of the lane its --lane argument names (lane None with no --lane)."""
    return ("open(os.path.join(here, 'argv.json'), 'w').write(json.dumps(sys.argv))\n"
            "_lanes = {!r}\n"
            "_lane = sys.argv[sys.argv.index('--lane') + 1] if '--lane' in sys.argv else None\n"
            "_suite, _ids = _lanes[_lane]\n"
            "_selftest_exit_report.arm(report, _suite, _ids)\n"
            "_selftest_exit_report.exit_with(0)\n".format(lanes))


def _report_body(ids, rc, suite="demo", before_exit=""):
    """A fake runner tail on the real child contract: arm the exit finalizer, run before_exit (a fault
    vector), then exit through exit_with, so the report is finalized at interpreter exit."""
    return ("_selftest_exit_report.arm(report, {suite!r}, {ids!r})\n{before}"
            "_selftest_exit_report.exit_with({rc})\n".format(
                suite=suite, ids=ids, before=before_exit, rc=rc))


def _raw_body(raw, rc=0, before_exit=""):
    """A fake runner tail that writes raw report bytes IN BAND (never through the finalizer)."""
    return "open(report, 'w').write({raw!r})\n{before}sys.exit({rc})\n".format(
        raw=raw, before=before_exit, rc=rc)


def _report_json(**overrides):
    """A finalized-shape report document, with keys replaced (or, given None, dropped)."""
    data = {"format_version": REPORT_FORMAT_VERSION, "suite": "demo", "check_ids": GOOD_IDS,
            "exit_code": 0, "finalized": True}
    for key, value in overrides.items():
        if value is None:
            data.pop(key)
        else:
            data[key] = value
    return json.dumps(data)


# The three fault vectors the child survives AFTER its last check: each leaves exit 0 and a complete
# report, and only its error stream carries the fault.
FAULT_ATEXIT = ("import atexit\n"
                "def _shutdown_fault():\n    raise RuntimeError('ST_ATEXIT_FAULT')\n"
                "atexit.register(_shutdown_fault)\n")
FAULT_DESTRUCTOR = ("class _Faulty:\n    def __del__(self):\n"
                    "        raise RuntimeError('ST_DESTRUCTOR_FAULT')\n"
                    "_cycle = _Faulty()\n_cycle.self = _cycle\ndel _cycle\n")
FAULT_THREAD = ("import threading, time\n"
                "def _thread_fault():\n    time.sleep(0.2)\n"
                "    raise RuntimeError('ST_THREAD_FAULT')\n"
                "threading.Thread(target=_thread_fault).start()\n")
# Each vector's fault-free twin: the same machinery, no fault.
TWIN_ATEXIT = "import atexit\natexit.register(int)\n"
TWIN_DESTRUCTOR = ("class _Quiet:\n    def __del__(self):\n        pass\n"
                   "_cycle = _Quiet()\n_cycle.self = _cycle\ndel _cycle\n")
TWIN_THREAD = "import threading\nthreading.Thread(target=int).start()\n"
# A destructor of an object the runner's module still holds runs at interpreter TEARDOWN, after the
# report is written and after module globals are cleared, while the audit hook is still installed;
# each binds what it uses, so only the helper's own hooks could fault there. A clean one that opens
# a file (the open audit event) or calls a captured sys.audit must pass; its faulting twin must not.
TEARDOWN_OPEN = ("class _LateOpen:\n    def __del__(self, op=open, dn=os.devnull):\n"
                 "        with op(dn, 'w') as f:\n            f.write('ok')\n"
                 "_late = _LateOpen()\n")
TEARDOWN_AUDIT = ("class _LateAudit:\n    def __del__(self, au=sys.audit):\n"
                  "        au('qa.clean')\n_late = _LateAudit()\n")
TEARDOWN_FAULT = ("class _LateFault:\n"
                  "    def __del__(self, op=open, dn=os.devnull, err=RuntimeError):\n"
                  "        with op(dn, 'w') as f:\n            f.write('ok')\n"
                  "        raise err('ST_TEARDOWN_FAULT')\n_late = _LateFault()\n")
# The DISCLOSED late-teardown residual class: a faulting destructor of an object whose last reference
# is held by interpreter-level state that CPython clears after it has dropped sys.stderr, so its fault
# is reported nowhere. It records that it ran in a marker file beside the runner before raising; the
# three witnessed examples are an object held by the codec search registry, one held by an audit hook,
# and a cycle anchored on a sys attribute (TEARDOWN_FAULT, held by a module global, is the refused
# control). The examples are not an exhaustive list of the class.
RESIDUAL_LATE_FAULT = ("class _LateMarked:\n"
                       "    def __init__(self, path):\n        self.path = path\n"
                       "    def __del__(self, op=open, err=RuntimeError):\n"
                       "        with op(self.path, 'w') as f:\n            f.write('ran')\n"
                       "        raise err('ST_RESIDUAL_LATE_FAULT')\n"
                       "_marker = os.path.join(here, 'late.marker')\n")
RESIDUAL_CODEC_REGISTRY = ("class _Search:\n    def __init__(self, held):\n        self.held = held\n"
                           "    def __call__(self, name):\n        return None\n"
                           "import codecs\ncodecs.register(_Search(_LateMarked(_marker)))\n")
RESIDUAL_AUDIT_HOOK = ("class _Audit:\n    def __init__(self, held):\n        self.held = held\n"
                       "    def __call__(self, *args):\n        return None\n"
                       "sys.addaudithook(_Audit(_LateMarked(_marker)))\n")
RESIDUAL_SYS_CYCLE = ("_cycle = _LateMarked(_marker)\n_cycle.me = _cycle\n"
                      "sys._st_residual = [_cycle]\ndel _cycle\n")
# Not residual: a faulting cycle anchored on a module global or a class attribute. It was silent
# while the finalizer kept the exit exception, whose traceback held the runner's module globals past
# module teardown; it must be refused.
TEARDOWN_CYCLE_GLOBAL = "_cycle = _LateMarked(_marker)\n_cycle.me = _cycle\n"
TEARDOWN_CYCLE_CLASS = ("class _Holder:\n    pass\n_Holder.held = _LateMarked(_marker)\n"
                        "_Holder.held.me = _Holder.held\n")
# The opcodes that read (or write) a name through the module globals or the builtins at call time,
# and the import opcodes (an import at teardown fails once sys.meta_path is cleared).
_GLOBAL_NAME_OPS = frozenset(("LOAD_GLOBAL", "LOAD_NAME", "LOAD_FROM_DICT_OR_GLOBALS",
                              "STORE_GLOBAL", "DELETE_GLOBAL", "STORE_NAME", "DELETE_NAME",
                              "IMPORT_NAME", "IMPORT_FROM"))


def _global_name_reads(function):
    """The names a function's code (nested code objects included) reads or writes through the
    module globals or the builtins, or imports, at call time, as sorted (code name, opcode, name)
    triples."""
    found, stack = set(), [function.__code__]
    while stack:
        code = stack.pop()
        for instruction in dis.get_instructions(code):
            if instruction.opname in _GLOBAL_NAME_OPS:
                found.add((code.co_name, instruction.opname, str(instruction.argval)))
        stack.extend(const for const in code.co_consts if isinstance(const, types.CodeType))
    return sorted(found)


def _thread_exit(arg):
    """A non-daemon thread that ends through sys.exit(arg), joined before the suite exits: the default
    threading.excepthook ignores SystemExit, so it writes nothing to stderr."""
    return ("import threading\n_t = threading.Thread(target=sys.exit, args=({!r},))\n"
            "_t.start()\n_t.join()\n".format(arg))


def self_test():
    """Synthetic roots, manifests, and fake runners in a private tempdir; children launched
    [sys.executable, -I, -B]; verdicts asserted on returned codes and captured output, never by
    grepping the real suite. The tempdir is removed in a finally."""
    failures = []

    def expect(label, got, want):
        if got != want:
            failures.append("{}: got {!r}, want {!r}".format(label, got, want))

    try:
        base = Path(tempfile.mkdtemp(prefix="aiqt-selftest-exec-st-"))
    except OSError as exc:
        _cannot("self-test temp storage unavailable: {}".format(exc))
        return 2
    counter = [0]
    # The REAL child-side finalizer, copied into every synthetic root beside its fake runner.
    finalizer_source = (Path(__file__).resolve().parent / "_selftest_exit_report.py").read_text(
        encoding="utf-8")

    def dead_checks(ids):
        """A statically present but never-executed check() block: the fake runner writes its report
        directly, so its source satisfies the pre-launch static reconcile without a live choke
        point."""
        return "if False:\n" + "".join("    check({!r}, 0, 0)\n".format(cid) for cid in ids)

    def build(manifest_text, runner_body=None, source_block=None, rosters=None):
        """A synthetic root. Both launch rosters launch every suite id the manifest names unless
        rosters replaces one: {repo-relative path: text, or None for no file}."""
        counter[0] += 1
        root = base / "case-{:02d}".format(counter[0])
        (root / "tools").mkdir(parents=True)
        (root / "tools" / "selftest_checks.toml").write_text(manifest_text, encoding="utf-8")
        texts = _roster_texts(re.findall(r'^id = "([A-Za-z0-9._-]+)"$', manifest_text, re.MULTILINE))
        texts.update(rosters or {})
        for rel, text in texts.items():
            if text is not None:
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(text, encoding="ascii")
        if runner_body is not None:
            (root / "tools" / "_selftest_exit_report.py").write_text(
                finalizer_source, encoding="utf-8")
            (root / "tools" / "fake_runner.py").write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "here = os.path.dirname(os.path.abspath(__file__))\n"
                "sys.path.insert(0, here)\n"
                "import _selftest_exit_report\n"
                "open(os.path.join(here, 'launched.marker'), 'w').close()\n"
                "report = sys.argv[-1]\n"
                + (dead_checks(GOOD_IDS) if source_block is None else source_block)
                + runner_body, encoding="utf-8")
        return root

    def run(root, suite_id="demo"):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run_suite(root, suite_id)
        return code, out.getvalue(), err.getvalue()

    def launched(root):
        return (root / "tools" / "launched.marker").exists()

    def recorded_argv(root):
        """The argv the fake runner recorded, consumed so that the next run must record afresh."""
        path = root / "tools" / "argv.json"
        if not path.exists():
            return None
        argv = json.loads(path.read_text(encoding="utf-8"))
        path.unlink()
        return argv

    try:
        # 1: exact match -> 0 with the machine-derived PASS line
        root = build(_manifest_text(), _report_body(GOOD_IDS, 0))
        code, out, _err = run(root)
        expect("st/exact-match-passes", code, 0)
        expect("st/pass-line-shape",
               "PASS: suite demo executed 3 unique registered checks; set-sha256=" in out, True)

        # 2: reordered report -> still 0 (set comparison; report order is diagnostics only)
        code, _out, _err = run(build(_manifest_text(), _report_body(list(reversed(GOOD_IDS)), 0)))
        expect("st/reorder-passes", code, 0)

        # 3: one id omitted -> 1, named under missing, nothing under extra
        code, out, _err = run(build(_manifest_text(), _report_body(["a/one", "a/two"], 0)))
        expect("st/omit-fails", code, 1)
        expect("st/omit-names-missing", "missing (registered, never executed): a/three" in out, True)
        expect("st/omit-no-extra", "extra (executed, not registered)" in out, False)

        # 4: one extra id -> 1, named under extra
        code, out, _err = run(build(_manifest_text(), _report_body(GOOD_IDS + ["a/rogue"], 0)))
        expect("st/extra-fails", code, 1)
        expect("st/extra-named", "extra (executed, not registered): a/rogue" in out, True)

        # 5: duplicate observed id in the report -> 2
        code, _out, _err = run(build(_manifest_text(), _report_body(GOOD_IDS + ["a/one"], 0)))
        expect("st/dup-observed-2", code, 2)

        # 6: duplicate expected id in the manifest -> 2, and the child is NEVER launched
        root = build(_manifest_text(ids=["a/one", "a/one"]), _report_body(GOOD_IDS, 0))
        code, _out, _err = run(root)
        expect("st/dup-expected-2", code, 2)
        expect("st/dup-expected-no-launch", launched(root), False)

        # 7: malformed expectation manifests, each 2 BEFORE any launch
        for label, text in (
                ("bad-toml", "format-version = \n"),
                ("extra-top-key", _manifest_text() + "\nstray = 1\n"),
                ("missing-row-key",
                 'format-version = 1\n\n[[suite]]\nid = "demo"\nexpected-check-ids = ["a/one"]\n'),
                ("extra-row-key", _manifest_text().replace('runner = ', 'note = "x"\nrunner = ')),
                ("non-string-id", _manifest_text().replace('"a/three"', '3')),
                ("control-char-id", _manifest_text().replace('"a/three"', '"a/\\u0001"')),
                ("empty-id-list", _manifest_text(ids=[])),
                ("bool-format-version", _manifest_text(header="format-version = true"))):
            root = build(text, _report_body(GOOD_IDS, 0))
            code, _out, _err = run(root)
            expect("st/manifest-{}-2".format(label), code, 2)
            expect("st/manifest-{}-no-launch".format(label), launched(root), False)

        # 7b: an integer literal past CPython's 4300-digit int-string limit makes tomllib raise a BARE
        #     ValueError (not TOMLDecodeError); the manifest is still refused 2 BEFORE any launch
        #     (F-TOML-BARE-VALUEERROR-CLASS). The digit limit is pinned to the default 4300
        #     (test-hermeticity) and restored in finally.
        #     A parser overflow (RecursionError, not a ValueError) is refused the same way, with the
        #     overflow in the refusal.
        #     The overflow is INJECTED (tomllib.loads and tomllib.load raise RecursionError on a marked,
        #     otherwise valid input) rather than provoked by a deeply nested body: the depth at which tomllib
        #     overflows is an interpreter limit, so a fixed body overflows under one recursion limit and parses
        #     (or trips an unrelated refusal) under another.
        real_loads, real_load = tomllib.loads, tomllib.load

        def overflowing_loads(text, **kwargs):
            if "injected-overflow" in text:
                raise RecursionError("injected parser overflow")
            return real_loads(text, **kwargs)

        tomllib.loads = overflowing_loads
        tomllib.load = lambda fp, **kwargs: overflowing_loads(fp.read().decode("utf-8"), **kwargs)
        try:
            for label, value in (("over-long-int", "9" * 4400), ("parser-overflow", "1  # injected-overflow")):
                prev_digits = sys.get_int_max_str_digits()
                sys.set_int_max_str_digits(4300)
                err = ""
                try:
                    root = build(_manifest_text(header="format-version = " + value), _report_body(GOOD_IDS, 0))
                    try:
                        code, _out, err = run(root)
                    except (ValueError, RecursionError) as exc:
                        code = "a bare {} escaped".format(type(exc).__name__)
                finally:
                    sys.set_int_max_str_digits(prev_digits)
                expect("st/manifest-{}-2".format(label), code, 2)
                expect("st/manifest-{}-no-launch".format(label), launched(root), False)
                if label == "parser-overflow":
                    expect("st/manifest-parser-overflow-finding", "injected parser overflow" in err, True)
        finally:
            tomllib.loads, tomllib.load = real_loads, real_load

        # 8: child exits 0 but writes no report -> 2, never a pass
        code, _out, _err = run(build(_manifest_text(), "sys.exit(0)\n"))
        expect("st/missing-report-2", code, 2)

        # 9: malformed reports -> 2, whatever the child's exit code
        for label, raw in (
                ("truncated-json", '{"format_version": 2, "suite": "demo", "check_ids": ["a/one"'),
                ("missing-key", _report_json(suite=None)),
                ("extra-key", _report_json(count=3)),
                ("ids-not-list", _report_json(check_ids="a/one")),
                ("bool-format-version", _report_json(format_version=True)),
                ("dup-member", _report_json(check_ids=["a/wrong"])[:-1]
                 + ', "check_ids": ["a/one", "a/two", "a/three"]}'),
                # an in-band report, even complete and well formed, was never finalized at exit
                ("in-band-format-1", json.dumps(
                    {"format_version": 1, "suite": "demo", "check_ids": GOOD_IDS})),
                ("finalized-false", _report_json(finalized=False)),
                ("finalized-missing", _report_json(finalized=None)),
                ("exit-code-mismatch", _report_json(exit_code=1)),
                ("exit-code-bool", _report_json(exit_code=False))):
            code, _out, _err = run(build(_manifest_text(), _raw_body(raw)))
            expect("st/report-{}-2".format(label), code, 2)

        # 10: wrong-suite report -> 2
        code, _out, _err = run(build(_manifest_text(), _report_body(GOOD_IDS, 0, suite="other")))
        expect("st/wrong-suite-2", code, 2)

        # 11: non-regular report: a directory, then a symlink to a valid payload -> 2
        code, _out, _err = run(build(_manifest_text(), "os.mkdir(report)\nsys.exit(0)\n"))
        expect("st/report-dir-2", code, 2)
        code, _out, _err = run(build(_manifest_text(), (
            "open(report + '.real', 'w').write({!r})\n"
            "os.symlink(report + '.real', report)\nsys.exit(0)\n".format(_report_json()))))
        expect("st/report-symlink-2", code, 2)

        # 12: child rc 1 with a complete report -> 1, never masked to 0
        code, out, _err = run(build(_manifest_text(), _report_body(GOOD_IDS, 1)))
        expect("st/child-fail-complete-set-1", code, 1)
        expect("st/child-fail-stated",
               "execution set complete; child reported assertion failures" in out, True)

        # 12b: a member diagnostic on STDOUT is forwarded, and the exit 1 of the child stays a FAIL
        # (exit 1): failure evidence a suite prints to stdout is never downgraded to the CANNOT
        # EVALUATE that any stderr byte becomes (the suites declare 0 stderr bytes).
        diag = ("print(" + repr("CONFIG-INJECTION demo member "
                                "(exited 3 before its bound fired): evidence") + ")\n")
        code, out, err = run(build(_manifest_text(), _report_body(GOOD_IDS, 1, before_exit=diag)))
        expect("st/child-fail-stdout-diagnostic-1", code, 1)
        expect("st/child-fail-stdout-forwarded",
               "CONFIG-INJECTION demo member (exited 3 before its bound fired): evidence" in out,
               True)
        expect("st/child-fail-stdout-no-cannot", "CANNOT EVALUATE" in err, False)

        # 13: child rc 0 with an incomplete report: missing ids -> 1 naming them; truncated JSON -> 2
        code, out, _err = run(build(_manifest_text(), _report_body(["a/one"], 0)))
        expect("st/rc0-missing-1", code, 1)
        expect("st/rc0-missing-named", "missing (registered, never executed): a/two" in out, True)
        code, _out, _err = run(build(_manifest_text(), _raw_body('{"format_version": 1, ')))
        expect("st/rc0-truncated-2", code, 2)

        # 14: child rc 2 -> 2 (a harness error is no verdict)
        code, _out, _err = run(build(_manifest_text(), _report_body(GOOD_IDS, 2)))
        expect("st/child-rc2-2", code, 2)

        # 15: runner mismatch -> 2, no launch: escaping, non-regular, and symlinked runners
        root = build(_manifest_text(runner="../../outside.py"), _report_body(GOOD_IDS, 0))
        code, _out, _err = run(root)
        expect("st/runner-escape-2", code, 2)
        expect("st/runner-escape-no-launch", launched(root), False)
        root = build(_manifest_text(runner="tools"), _report_body(GOOD_IDS, 0))
        code, _out, _err = run(root)
        expect("st/runner-non-regular-2", code, 2)
        expect("st/runner-non-regular-no-launch", launched(root), False)
        root = build(_manifest_text(runner="tools/link_runner.py"), _report_body(GOOD_IDS, 0))
        os.symlink("fake_runner.py", str(root / "tools" / "link_runner.py"))
        code, _out, _err = run(root)
        expect("st/runner-symlink-2", code, 2)
        expect("st/runner-symlink-no-launch", launched(root), False)

        # 16: the motivating near-miss: a GREEN child that never executed one registered check fails,
        # naming exactly that id; its complete-report twin passes
        code, out, _err = run(build(_manifest_text(), _report_body(["a/one", "a/two"], 0)))
        expect("st/near-miss-caught", code, 1)
        expect("st/near-miss-names-id", "missing (registered, never executed): a/three" in out, True)
        expect("st/near-miss-names-only-it", out.count("missing (registered, never executed)"), 1)
        code, _out, _err = run(build(_manifest_text(), _report_body(GOOD_IDS, 0)))
        expect("st/near-miss-twin-passes", code, 0)

        # 17 (FIX 1): the static source-to-manifest reconcile refuses, BEFORE any launch, a source
        # check absent from the manifest (even an unreachable one), a registered id with no source
        # site, a dead duplicate-id alias, and an unresolvable dynamic id; the loop-literal pattern
        # resolves, and a clean source==manifest twin still passes
        root = build(_manifest_text(), _report_body(GOOD_IDS, 0),
                     source_block=dead_checks(GOOD_IDS + ["a/dead"]))
        code, _out, err = run(root)
        expect("st/static-unregistered-2", code, 2)
        expect("st/static-unregistered-named", "source check not registered: a/dead" in err, True)
        expect("st/static-unregistered-no-launch", launched(root), False)
        root = build(_manifest_text(), _report_body(GOOD_IDS, 0),
                     source_block=dead_checks(["a/one", "a/two"]))
        code, _out, err = run(root)
        expect("st/static-sourceless-2", code, 2)
        expect("st/static-sourceless-named",
               "registered id has no source check(): a/three" in err, True)
        expect("st/static-sourceless-no-launch", launched(root), False)
        root = build(_manifest_text(), _report_body(GOOD_IDS, 0),
                     source_block=dead_checks(GOOD_IDS + ["a/one"]))
        code, _out, err = run(root)
        expect("st/static-dup-alias-2", code, 2)
        expect("st/static-dup-alias-named", "duplicate source check id 'a/one'" in err, True)
        expect("st/static-dup-alias-no-launch", launched(root), False)
        root = build(_manifest_text(), _report_body(GOOD_IDS, 0),
                     source_block=dead_checks(GOOD_IDS)
                     + 'if False:\n    check("x-%s" % label, 0, 0)\n')
        code, _out, err = run(root)
        expect("st/static-dynamic-id-2", code, 2)
        expect("st/static-dynamic-id-named", "unresolvable check id at" in err, True)
        expect("st/static-dynamic-id-no-launch", launched(root), False)
        root = build(_manifest_text(), _report_body(GOOD_IDS, 0), source_block=(
            'if False:\n'
            '    for check_id, other in (("a/one", 1), ("a/two", 2)):\n'
            '        check(check_id, 0, 0)\n'
            '    check("a/three", 0, 0)\n'))
        code, _out, _err = run(root)
        expect("st/static-loop-literal-passes", code, 0)
        code, _out, _err = run(build(_manifest_text(), _report_body(GOOD_IDS, 0)))
        expect("st/static-clean-twin-passes", code, 0)

        # 18 (FIX 3): an exported copy of this gate run from outside a git checkout refuses to guess
        # its target instead of validating whatever tree the cwd happens to sit in
        exported = base / "exported"
        exported.mkdir()
        gate_copy = exported / "check_selftest_execution.py"
        gate_copy.write_text(Path(__file__).resolve().read_text(encoding="utf-8"),
                             encoding="utf-8")
        proc = subprocess.run([sys.executable, "-I", "-B", str(gate_copy), "--suite", "demo"],
                              capture_output=True)
        expect("st/exported-no-git-2", proc.returncode, 2)
        expect("st/exported-no-git-named",
               "cannot confirm the gate's own repo root"
               in proc.stderr.decode("utf-8", errors="replace"), True)

        # 19 (FIX 4): unavailable temp storage for the private report directory is cannot-evaluate,
        # never a finding, and the child is never launched; the pinned tempdir is restored after
        root = build(_manifest_text(), _report_body(GOOD_IDS, 0))
        real_tempdir = tempfile.tempdir
        tempfile.tempdir = str(base / "no-such-dir" / "nested")
        try:
            code, _out, _err = run(root)
        finally:
            tempfile.tempdir = real_tempdir
        expect("st/mkdtemp-oserror-2", code, 2)
        expect("st/mkdtemp-oserror-no-launch", launched(root), False)

        # 20 (round 3, FIX A): a reference to the bare name check outside a direct call (an alias
        # that could carry a call site beyond the static scan) is refused fail-closed, no launch
        root = build(_manifest_text(), _report_body(GOOD_IDS, 0),
                     source_block=dead_checks(GOOD_IDS)
                     + 'renamed = check\nif False:\n    renamed("a/ghost", 0, 0)\n')
        code, _out, err = run(root)
        expect("st/static-alias-ref-2", code, 2)
        expect("st/static-alias-ref-named", "referenced outside a direct call" in err, True)
        expect("st/static-alias-ref-no-launch", launched(root), False)

        # 21 (round 3, FIX B): a check() id Name bound inside a comprehension is refused fail-closed
        # (the lexical for-stack does not model comprehension scope, so an enclosing for could
        # mis-attribute it); a literal-id check() inside a comprehension still resolves
        root = build(_manifest_text(), _report_body(GOOD_IDS, 0),
                     source_block=dead_checks(GOOD_IDS) + (
                         'if False:\n'
                         '    for check_id, other in (("a/one", 1), ("a/two", 2)):\n'
                         '        [check(check_id, 0, 0) for check_id in ("a/one", "a/rogue")]\n'))
        code, _out, err = run(root)
        expect("st/static-comp-id-2", code, 2)
        expect("st/static-comp-id-named", "inside a comprehension" in err, True)
        expect("st/static-comp-id-no-launch", launched(root), False)
        root = build(_manifest_text(), _report_body(GOOD_IDS, 0),
                     source_block=dead_checks(["a/one", "a/two"])
                     + 'if False:\n    [check("a/three", 0, 0) for _ in (1,)]\n')
        code, _out, _err = run(root)
        expect("st/static-comp-literal-passes", code, 0)

        # 22 (round 3, FIX C; round 4, FIX 3; round 5, FIX 1; round 6, FIX 1): the child is
        # launched under a git-neutral, interpreter-neutral environment: every ambient GIT_* and
        # PYTHON* variable is dropped, the config surfaces pin
        # to os.devnull, core.excludesFile and core.attributesFile are overridden to os.devnull by
        # injected GIT_CONFIG_* entries (the global ignore and attributes surfaces the pinned
        # config files do not cover), and HOME and XDG_CONFIG_HOME are pinned to a fresh empty
        # directory, the transitive layer a descendant GIT_* re-scrub cannot strip, so a poisoned
        # GIT_CONFIG_GLOBAL (for example a core.hooksPath naming an attacker hook), a hostile
        # global ignore, or a hostile XDG_CONFIG_HOME/git/config never reaches the child, and a
        # hostile ambient PYTHONPATH never reaches a descendant interpreter; the fake
        # runner dumps the GIT_* and PYTHON* environment and the HOME and XDG_CONFIG_HOME it
        # actually saw, asserted as the exact injected GIT_* set, an empty PYTHON* set, and one
        # fresh empty directory distinct from the
        # ambient values this leg pins (a behavioural no-fire witness for a scrubbing descendant,
        # for example a hostile XDG core.fsmonitor, needs aiqt_hooks importable in the child, so it
        # remains a real-suite manual verification, disclosed)
        root = build(_manifest_text(),
                     "git_env = dict((k, v) for k, v in os.environ.items() "
                     "if k.startswith('GIT_'))\n"
                     "open(os.path.join(here, 'git-env.json'), 'w').write(json.dumps(git_env))\n"
                     "py_env = dict((k, v) for k, v in os.environ.items() "
                     "if k.startswith('PYTHON'))\n"
                     "open(os.path.join(here, 'py-env.json'), 'w').write(json.dumps(py_env))\n"
                     "home = os.environ.get('HOME')\n"
                     "open(os.path.join(here, 'home-env.json'), 'w').write(json.dumps(\n"
                     "    {'HOME': home, 'XDG_CONFIG_HOME': os.environ.get('XDG_CONFIG_HOME'),\n"
                     "     'home_is_dir': os.path.isdir(home) if home else False,\n"
                     "     'home_entries': os.listdir(home) if home and os.path.isdir(home) "
                     "else None}))\n"
                     + _report_body(GOOD_IDS, 0))
        saved_env = dict((k, os.environ.get(k)) for k in
                         ("GIT_CONFIG_GLOBAL", "GIT_DIR", "HOME", "XDG_CONFIG_HOME", "PYTHONPATH"))
        os.environ["GIT_CONFIG_GLOBAL"] = str(base / "poisoned-gitconfig")
        os.environ["GIT_DIR"] = str(base / "decoy-git")
        os.environ["HOME"] = str(base / "ambient-home")
        os.environ["XDG_CONFIG_HOME"] = str(base / "ambient-xdg")
        os.environ["PYTHONPATH"] = str(base / "hostile-pythonpath")
        try:
            code, _out, _err = run(root)
        finally:
            for key, value in saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        expect("st/git-env-passes", code, 0)
        env_dump = root / "tools" / "git-env.json"
        expect("st/git-env-dump-written", env_dump.exists(), True)
        if env_dump.exists():
            expect("st/git-env-neutralized", json.loads(env_dump.read_text(encoding="utf-8")),
                   dict(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
                        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_COUNT="2",
                        GIT_CONFIG_KEY_0="core.excludesFile", GIT_CONFIG_VALUE_0=os.devnull,
                        GIT_CONFIG_KEY_1="core.attributesFile", GIT_CONFIG_VALUE_1=os.devnull))
        py_dump = root / "tools" / "py-env.json"
        expect("st/py-env-dump-written", py_dump.exists(), True)
        if py_dump.exists():
            expect("st/py-env-neutralized",
                   json.loads(py_dump.read_text(encoding="utf-8")), {})
        home_dump = root / "tools" / "home-env.json"
        expect("st/home-env-dump-written", home_dump.exists(), True)
        if home_dump.exists():
            home_env = json.loads(home_dump.read_text(encoding="utf-8"))
            expect("st/home-xdg-pinned-together",
                   home_env["HOME"] is not None
                   and home_env["HOME"] == home_env["XDG_CONFIG_HOME"], True)
            expect("st/home-not-ambient", home_env["HOME"] in
                   (str(base / "ambient-home"), str(base / "ambient-xdg")), False)
            expect("st/home-fresh-empty-dir",
                   (home_env["home_is_dir"], home_env["home_entries"]), (True, []))

        # 23 (round 4, FIX 1): a statically EMPTY literal loop is refused fail-closed, no launch: an
        # empty iterable resolves its check() site to zero ids, so without the refusal the site
        # would be silently invisible (the exact "loop that no longer iterates" failure)
        root = build(_manifest_text(), _report_body(GOOD_IDS, 0),
                     source_block=dead_checks(GOOD_IDS)
                     + 'for check_id, other in ():\n    check(check_id, 0, 0)\n')
        code, _out, err = run(root)
        expect("st/static-empty-loop-2", code, 2)
        expect("st/static-empty-loop-named", "unresolvable check id at" in err, True)
        expect("st/static-empty-loop-no-launch", launched(root), False)

        # 24 (fail-closed child contract): a fault the child survives AFTER its last check (an atexit
        # callback, a destructor of collectable garbage, a thread joined at shutdown) leaves exit 0
        # and a complete set, and is refused 2 through the error stream, under both the finalized
        # report and the in-band report the gate formerly accepted (an armed child also records the
        # thread fault itself and exits 2); each vector's own fault-free twin passes
        stream_refusal = "error stream carries a fault"
        for label, vector, token, twin, refusal in (
                ("atexit", FAULT_ATEXIT, "ST_ATEXIT_FAULT", TWIN_ATEXIT, stream_refusal),
                ("destructor", FAULT_DESTRUCTOR, "ST_DESTRUCTOR_FAULT", TWIN_DESTRUCTOR,
                 stream_refusal),
                ("thread", FAULT_THREAD, "ST_THREAD_FAULT", TWIN_THREAD, "child exited 2")):
            code, _out, err = run(build(_manifest_text(), _report_body(GOOD_IDS, 0, before_exit=twin)))
            expect("st/fault-{}-twin-passes".format(label), (code, err), (0, ""))
            code, _out, err = run(build(_manifest_text(),
                                        _report_body(GOOD_IDS, 0, before_exit=vector)))
            expect("st/fault-{}-finalized-2".format(label), code, 2)
            expect("st/fault-{}-finalized-named".format(label),
                   (token in err, refusal in err), (True, True))
            code, _out, err = run(build(_manifest_text(), _raw_body(
                json.dumps({"format_version": 1, "suite": "demo", "check_ids": GOOD_IDS}),
                before_exit=vector)))
            expect("st/fault-{}-in-band-2".format(label), code, 2)
            expect("st/fault-{}-in-band-named".format(label), token in err, True)
        code, _out, _err = run(build(_manifest_text(), _report_body(
            GOOD_IDS, 0, before_exit="import atexit, threading\natexit.register(int)\n"
            "threading.Thread(target=int).start()\n")))
        expect("st/fault-free-twin-passes", code, 0)
        # a non-daemon thread's FAILURE exit (SystemExit with a nonzero or non-int code) writes
        # nothing to stderr, and is still refused 2: the armed child records it and writes no report;
        # its clean twin (SystemExit(0)) passes
        for label, arg in (("one", 1), ("message", "thread failed")):
            code, _out, err = run(build(_manifest_text(),
                                        _report_body(GOOD_IDS, 0, before_exit=_thread_exit(arg))))
            expect("st/fault-thread-exit-{}-2".format(label), code, 2)
            expect("st/fault-thread-exit-{}-named".format(label),
                   "ended with SystemExit({!r})".format(arg) in err, True)
        code, _out, err = run(build(_manifest_text(),
                                    _report_body(GOOD_IDS, 0, before_exit=_thread_exit(0))))
        expect("st/fault-thread-exit-zero-twin-passes", (code, err), (0, ""))

        # 25: the finalizer contract itself: an armed child whose exit status bypassed exit_with
        # writes no report (no verdict), one that wrote its report in band before the finalizer
        # (exclusive creation) is refused, and an arm after any other atexit registration is a
        # harness error; the child's exit status, not its report, is decisive in each
        root = build(_manifest_text(),
                     "_selftest_exit_report.arm(report, 'demo', {!r})\nsys.exit(0)\n".format(GOOD_IDS))
        code, _out, err = run(root)
        expect("st/finalizer-unrecorded-status-2", code, 2)
        expect("st/finalizer-unrecorded-status-named", "no exit status was recorded" in err, True)
        code, _out, err = run(build(_manifest_text(),
                                    "open(report, 'w').write({!r})\n".format(_report_json())
                                    + _report_body(GOOD_IDS, 0)))
        expect("st/finalizer-preexisting-report-2", code, 2)
        expect("st/finalizer-preexisting-report-named", "cannot finalize execution report" in err,
               True)
        code, _out, err = run(build(_manifest_text(),
                                    "import atexit\natexit.register(int)\n"
                                    + _report_body(GOOD_IDS, 0)))
        expect("st/finalizer-not-first-2", code, 2)
        expect("st/finalizer-not-first-named", "must be the first atexit registration" in err, True)
        # a recorded status stands only if its own exit ends the process: a caught exit_with followed
        # by a failure exit, by a fall-through, or by a code read and a different exit is refused 2,
        # and so is exit_with from a non-main thread; an exit_with that unwinds through a finally or
        # is re-raised by a handler still ends the process, and passes
        arm_line = "_selftest_exit_report.arm(report, 'demo', {!r})\n".format(GOOD_IDS)
        for label, tail in (
                ("then-exit-1", "try:\n    _selftest_exit_report.exit_with(0)\n"
                                "except SystemExit:\n    pass\nsys.exit(1)\n"),
                ("then-fall-through", "try:\n    _selftest_exit_report.exit_with(0)\n"
                                      "except SystemExit:\n    pass\n"),
                ("code-read-then-exit-0", "try:\n    _selftest_exit_report.exit_with(1)\n"
                                          "except SystemExit as exc:\n    exc.code\nsys.exit(0)\n")):
            code, _out, err = run(build(_manifest_text(), arm_line + tail))
            expect("st/finalizer-caught-{}-2".format(label), code, 2)
            expect("st/finalizer-caught-{}-named".format(label),
                   "is not the one the process exited with" in err, True)
        code, _out, err = run(build(_manifest_text(), arm_line + (
            "import threading\n"
            "_t = threading.Thread(target=_selftest_exit_report.exit_with, args=(0,))\n"
            "_t.start()\n_t.join()\n_selftest_exit_report.exit_with(0)\n")))
        expect("st/finalizer-thread-exit-with-2", code, 2)
        expect("st/finalizer-thread-exit-with-named", "outside the main thread" in err, True)
        for label, tail in (
                ("finally", "def _leave():\n    try:\n        _selftest_exit_report.exit_with(0)\n"
                            "    finally:\n        int()\n_leave()\n"),
                ("reraise", "try:\n    _selftest_exit_report.exit_with(0)\n"
                            "except SystemExit:\n    raise\n")):
            code, _out, err = run(build(_manifest_text(), arm_line + tail))
            expect("st/finalizer-unwound-{}-passes".format(label), (code, err), (0, ""))
        # 25b: a thread whose exit cannot be observed, and a status confirmed by anything but the
        # interpreter's own exit, are refused 2: a thread started through _thread directly (a Python
        # target ending in SystemExit(1), a C-callable target through an alias bound before arm, and
        # the joinable entry point where it exists), an earlier exit_with whose exception outlives a
        # later caught one, a caught exit_with whose code a C callable reads as an atexit callback or
        # as a _thread target before another exit or a fall-through, a thread started from an atexit
        # callback, and an atexit callback registered from one; a thread that a non-daemon thread
        # starts while the exit-time join waits on it still passes
        raw_wait = "import _thread, time\nwhile _thread._count():\n    time.sleep(0.001)\n"
        caught_zero = ("import atexit, operator\ntry:\n    _selftest_exit_report.exit_with(0)\n"
                       "except SystemExit as _e:\n    ")
        unobserved, unconfirmed = "outside threading", "is not the one the process exited with"
        late = "after the exit-time thread join"
        for label, head, tail, named in (
                ("raw-thread-exit-1", "", "import _thread, threading\n_ready = threading.Event()\n"
                 "def _worker():\n    _ready.set()\n    sys.exit(1)\n"
                 "_thread.start_new_thread(_worker, ())\n_ready.wait(5)\n" + raw_wait
                 + "_selftest_exit_report.exit_with(0)\n", unobserved),
                ("raw-thread-alias-c-target", "from _thread import start_new_thread as _snt\n",
                 "_snt(sys.exit, (1,))\n" + raw_wait + "_selftest_exit_report.exit_with(0)\n",
                 unobserved),
                ("raw-thread-joinable", "", "import _thread, functools\n"
                 "_start = getattr(_thread, 'start_joinable_thread', None)\n"
                 "if _start is None:\n    _thread.start_new_thread(sys.exit, (1,))\n"
                 "else:\n    _start(functools.partial(sys.exit, 1)).join()\n" + raw_wait
                 + "_selftest_exit_report.exit_with(0)\n", unobserved),
                ("earlier-exit-outlives-caught", "", "try:\n    _selftest_exit_report.exit_with(1)\n"
                 "finally:\n    try:\n        _selftest_exit_report.exit_with(0)\n"
                 "    except SystemExit:\n        pass\n", unconfirmed),
                ("atexit-code-read-then-exit-1", "", caught_zero
                 + "atexit.register(operator.attrgetter('code'), _e)\nsys.exit(1)\n", unconfirmed),
                ("atexit-code-read-fall-through", "", caught_zero
                 + "atexit.register(operator.attrgetter('code'), _e)\n", unconfirmed),
                ("raw-thread-code-read-then-exit-1", "", caught_zero
                 + "import _thread\n    _thread.start_new_thread(operator.attrgetter('code'), (_e,))\n"
                 + raw_wait + "sys.exit(1)\n", unobserved),
                ("thread-from-atexit", "", "import atexit, threading, time\n"
                 "def _late():\n    time.sleep(0.3)\n    raise RuntimeError('ST_LATE_THREAD')\n"
                 "atexit.register(lambda: threading.Thread(target=_late).start())\n"
                 "_selftest_exit_report.exit_with(0)\n", late),
                ("atexit-from-atexit", "", "import atexit\n"
                 "def _late_fault():\n    raise RuntimeError('ST_LATE_ATEXIT')\n"
                 "atexit.register(lambda: atexit.register(_late_fault))\n"
                 "_selftest_exit_report.exit_with(0)\n", late)):
            code, _out, err = run(build(_manifest_text(), head + arm_line + tail))
            expect("st/finalizer-{}-2".format(label), code, 2)
            expect("st/finalizer-{}-named".format(label), named in err, True)
        code, _out, err = run(build(_manifest_text(), arm_line + (
            "import threading, time\n"
            "def _parent():\n    time.sleep(0.2)\n"
            "    threading.Thread(target=time.sleep, args=(0.1,)).start()\n"
            "threading.Thread(target=_parent).start()\n_selftest_exit_report.exit_with(0)\n")))
        expect("st/finalizer-thread-during-join-twin-passes", (code, err), (0, ""))
        # 25c (round 3): the observation hooks the gate's bootstrap installs BEFORE any suite code
        # close the remaining thread-class routes through the one mechanism: a raw thread started
        # before arm is recorded at its start, whenever that is; a failing threading.Thread joined
        # before arm is recorded by the already-installed excepthook wrapper; a fresh import of
        # subinterpreter machinery is itself the fault (code in another interpreter is invisible to
        # every per-interpreter hook); and a caught exit_with whose code a C weakref callback
        # re-reads with no frame on the stack no longer decides anything, because the finalizer
        # defers to the interpreter's real exit status, which must equal the report's exit_code
        # (on an interpreter where the callback fires differently the child refuses with 2 itself,
        # so the vector asserts the refusal, not the route). A clean pre-arm threading.Thread still
        # passes, and arming in a child launched WITHOUT the bootstrap (a raw direct launch) is
        # refused with no report.
        pre_arm_raw = ("import _thread, threading, time\n"
                       "_go = threading.Event()\n_done = threading.Event()\n"
                       "def _worker():\n    _go.wait()\n"
                       "    try:\n        sys.exit(1)\n    finally:\n        _done.set()\n"
                       "_thread.start_new_thread(_worker, ())\n"
                       + arm_line
                       + "_go.set()\nassert _done.wait(5)\n" + raw_wait
                       + "_selftest_exit_report.exit_with(0)\n")
        pre_arm_thread_exit = ("import threading\n"
                               "_t = threading.Thread(target=sys.exit, args=(1,))\n"
                               "_t.start()\n_t.join()\n"
                               + arm_line + "_selftest_exit_report.exit_with(0)\n")
        subinterp_import = ("import _interpreters\ndel sys.modules['_interpreters']\n"
                            "import _interpreters\n"
                            + arm_line + "_selftest_exit_report.exit_with(0)\n")
        weakref_confirm = (arm_line
                           + "import functools, weakref\n"
                           "try:\n    _selftest_exit_report.exit_with(0)\n"
                           "except SystemExit as _e:\n    _saved = _e\n"
                           "class _Holder:\n    pass\n"
                           "def _fail():\n    global _ref\n    _holder = _Holder()\n"
                           "    _ref = weakref.ref(_holder, functools.partial(getattr, _saved, 'code'))\n"
                           "    sys.exit(1)\n"
                           "_fail()\n")
        for label, body, named in (
                ("pre-arm-raw-thread-exit-1", pre_arm_raw, unobserved),
                ("pre-arm-thread-exit-1", pre_arm_thread_exit, "ended with SystemExit(1)"),
                ("subinterp-fresh-import", subinterp_import, "cannot be observed from this one")):
            code, _out, err = run(build(_manifest_text(), body))
            expect("st/finalizer-{}-2".format(label), code, 2)
            expect("st/finalizer-{}-named".format(label), named in err, True)
        # 25c1 (round 10): importing _interpreters is no fault (CPython 3.14's concurrent.futures, and
        # so asyncio, imports it at module level); the bootstrap imports it first and replaces its
        # interpreter-creating and code-running functions with refusals, so a clean
        # concurrent.futures pool and a clean asyncio.run pass, while creating an interpreter, through
        # _interpreters (the refusal's RuntimeError caught, so only the recorded fault refuses) or
        # through concurrent.interpreters, and a reload that tries to restore the functions, are
        # refused
        create_named = "_interpreters.create was called"
        for label, body, named in (
                ("subinterp-create", "import _interpreters\ntry:\n    _interpreters.create()\n"
                 "except RuntimeError:\n    pass\n"
                 + arm_line + "_selftest_exit_report.exit_with(0)\n", create_named),
                ("subinterp-concurrent-interpreters-create", "import concurrent.interpreters\n"
                 "try:\n    concurrent.interpreters.create()\nexcept RuntimeError:\n    pass\n"
                 + arm_line + "_selftest_exit_report.exit_with(0)\n", create_named),
                ("subinterp-reload-create", "import importlib, _interpreters\n"
                 "importlib.reload(_interpreters)\n"
                 "try:\n    _interpreters.create()\nexcept RuntimeError:\n    pass\n"
                 + arm_line + "_selftest_exit_report.exit_with(0)\n", create_named)):
            code, _out, err = run(build(_manifest_text(), body))
            expect("st/finalizer-{}-2".format(label), code, 2)
            expect("st/finalizer-{}-named".format(label), named in err, True)
        for label, body in (
                ("concurrent-futures-pool", "from concurrent.futures import ThreadPoolExecutor\n"
                 "with ThreadPoolExecutor(max_workers=2) as _pool:\n"
                 "    _got = list(_pool.map(abs, (-1, -2, -3)))\n"
                 "assert _got == [1, 2, 3], _got\n"),
                ("asyncio-run", "import asyncio\nasync def _main():\n"
                 "    await asyncio.sleep(0)\n    return 7\n"
                 "assert asyncio.run(_main()) == 7\n")):
            code, _out, err = run(build(_manifest_text(), _report_body(GOOD_IDS, 0,
                                                                      before_exit=body)))
            expect("st/finalizer-{}-twin-passes".format(label), (code, err), (0, ""))
        # 25c3 (round 10): a trace, profile or sys.monitoring callback runs in a thread's frames after
        # run(), and a SystemExit(1) it raises there (in Thread._delete, or at the return of
        # _bootstrap_inner) escapes to _thread, which drops it silently, so threading.excepthook
        # never sees it; each passed the gate before installing such a callback was itself refused
        installed = "callback was installed"
        for label, vector in (
                ("settrace-delete", "import threading\n"
                 "def _tracer(frame, event, arg):\n"
                 "    if event == 'call' and frame.f_code.co_name == '_delete':\n"
                 "        raise SystemExit(1)\n"
                 "    return None\n"
                 "def _worker():\n    sys.settrace(_tracer)\n"
                 "_t = threading.Thread(target=_worker)\n_t.start()\n_t.join()\n"),
                ("setprofile-bootstrap-return", "import threading\n"
                 "def _prof(frame, event, arg):\n"
                 "    if event == 'return' and frame.f_code.co_name == '_bootstrap_inner':\n"
                 "        raise SystemExit(1)\n"
                 "threading.setprofile(_prof)\n"
                 "_t = threading.Thread(target=int)\n_t.start()\n_t.join()\n"
                 "threading.setprofile(None)\n"),
                ("monitoring-delete", "import threading\n_mon = sys.monitoring\n"
                 "_mon.use_tool_id(3, 'st')\n"
                 "def _start(code, offset):\n"
                 "    if code.co_name == '_delete':\n        raise SystemExit(1)\n"
                 "_mon.register_callback(3, _mon.events.PY_START, _start)\n"
                 "_mon.set_events(3, _mon.events.PY_START)\n"
                 "_t = threading.Thread(target=int)\n_t.start()\n_t.join()\n"
                 "_mon.set_events(3, 0)\n")):
            code, _out, err = run(build(_manifest_text(), _report_body(GOOD_IDS, 0,
                                                                      before_exit=vector)))
            expect("st/finalizer-thread-end-{}-2".format(label), code, 2)
            expect("st/finalizer-thread-end-{}-named".format(label), installed in err, True)
        # 25c2 (round 9): the interpreter-creating machinery is identified by its extension FILE, not
        # by the name it is loaded under: the _interpreters extension loaded as qa._interpreters
        # (a qualified alias through spec_from_file_location) creates a subinterpreter whose fault no
        # hook here can see, and so does a byte copy of the file loaded from another path; both are
        # refused like the plain import above. A delivered interpreter-creation audit event is
        # refused too (sys.audit raises it here, since CPython 3.14.4 delivers it to no in-process
        # hook when it creates an interpreter). The twin, another extension file loaded under a
        # qualified alias, passes.
        alias_load = ("import importlib.util\n"
                      "_spec = importlib.util.find_spec('_interpreters')\n"
                      "_origin = _spec.origin\n")
        alias_tail = ("_alias = importlib.util.spec_from_file_location('qa._interpreters', _origin)\n"
                      "_mod = importlib.util.module_from_spec(_alias)\n"
                      "_alias.loader.exec_module(_mod)\n"
                      "_interp = _mod.create()\n"
                      "try:\n"
                      "    print('ST fault observed:', _mod.run_string(\n"
                      "        _interp, \"raise RuntimeError('ST_SUBINTERP_FAULT')\"), flush=True)\n"
                      "finally:\n    _mod.destroy(_interp)\n"
                      + arm_line + "_selftest_exit_report.exit_with(0)\n")
        copy_load = ("import shutil\n"
                     "_origin = shutil.copyfile(_origin, os.path.join(here, 'copied_interp.so'))\n")
        for label, body, named in (
                ("subinterp-qualified-alias", alias_load + alias_tail,
                 "was loaded as qa._interpreters"),
                ("subinterp-copied-file", alias_load + copy_load + alias_tail,
                 "was loaded as qa._interpreters"),
                ("subinterp-creation-event", "sys.audit('cpython.PyInterpreterState_New')\n"
                 + arm_line + "_selftest_exit_report.exit_with(0)\n",
                 "another interpreter was created")):
            code, _out, err = run(build(_manifest_text(), body))
            expect("st/finalizer-{}-2".format(label), code, 2)
            expect("st/finalizer-{}-named".format(label), named in err, True)
        code, _out, err = run(build(_manifest_text(), (
            "import importlib.util\n"
            "_spec = importlib.util.find_spec('_queue')\n"
            "if _spec.has_location:\n"
            "    _alias = importlib.util.spec_from_file_location('qa._queue', _spec.origin)\n"
            "    _alias.loader.exec_module(importlib.util.module_from_spec(_alias))\n"
            "else:\n    import _queue\n"
            + arm_line + "_selftest_exit_report.exit_with(0)\n")))
        expect("st/finalizer-unrelated-extension-alias-twin-passes", (code, err), (0, ""))
        code, _out, err = run(build(_manifest_text(), weakref_confirm))
        expect("st/finalizer-weakref-forged-confirm-2", code, 2)
        expect("st/finalizer-weakref-forged-confirm-named",
               ("is not the child's exit status" in err) or ("child exited 2" in err), True)
        code, _out, err = run(build(_manifest_text(),
                                    "import threading\n_t = threading.Thread(target=int)\n"
                                    "_t.start()\n_t.join()\n"
                                    + arm_line + "_selftest_exit_report.exit_with(0)\n"))
        expect("st/pre-arm-clean-thread-twin-passes", (code, err), (0, ""))
        root = build(_manifest_text(), _report_body(GOOD_IDS, 0))
        direct_report = root / "tools" / "direct-report.json"
        direct = subprocess.run(
            [sys.executable, "-I", "-B", str(root / "tools" / "fake_runner.py"),
             "--execution-report", str(direct_report)],
            cwd=str(root), capture_output=True)
        expect("st/arm-without-bootstrap-2", direct.returncode, 2)
        expect("st/arm-without-bootstrap-named",
               "observation hooks were not installed"
               in direct.stderr.decode("utf-8", errors="replace"), True)
        expect("st/arm-without-bootstrap-no-report", direct_report.exists(), False)
        # 25d (round 4): no hook trusts code the suite controls before its fault is recorded. A thread
        # exception whose __repr__ raises SystemExit(0), and a SystemExit subclass with a nonzero code
        # whose code property raises SystemExit(0), are still recorded (the end is classified by real
        # type and SystemExit's own code slot, and the fixed fault precedes any description); a raw
        # _thread target that supplies its own __func__, and a genuine Thread._bootstrap bound to an
        # object that is not a threading.Thread, are raw starts; a str-subclass module name whose own
        # hashing hides it from the membership test is still the subinterpreter import; a finalizer
        # failure whose exception's repr raises is still named; and an exit handler that imports
        # __main__ sees the runner's module, as under a direct launch
        evil_repr = ("class _Fault(RuntimeError):\n    def __repr__(self):\n"
                     "        raise SystemExit(0)\n")
        unclean = "other than a clean SystemExit"
        for label, body, named in (
                ("thread-repr-raises", arm_line + "import threading\n" + evil_repr
                 + "def _worker():\n    raise _Fault('ST_REPR_FAULT')\n"
                 "_t = threading.Thread(target=_worker)\n_t.start()\n_t.join()\n"
                 "_selftest_exit_report.exit_with(0)\n", unclean),
                ("thread-code-property-raises", arm_line + "import threading\n"
                 "class _Exit(SystemExit):\n    @property\n    def code(self):\n"
                 "        raise SystemExit(0)\n"
                 "def _worker():\n    raise _Exit(1)\n"
                 "_t = threading.Thread(target=_worker)\n_t.start()\n_t.join()\n"
                 "_selftest_exit_report.exit_with(0)\n", unclean),
                ("raw-thread-forged-func", arm_line + "import _thread, threading\n"
                 "class _Worker:\n    __func__ = staticmethod(threading.Thread._bootstrap)\n"
                 "    def __call__(self):\n        sys.exit(1)\n"
                 "_thread.start_new_thread(_Worker(), ())\n" + raw_wait
                 + "_selftest_exit_report.exit_with(0)\n", unobserved),
                ("raw-thread-bootstrap-non-thread", arm_line + "import _thread, threading, types\n"
                 "class _NotThread:\n    _daemonic = False\n"
                 "    def _bootstrap_inner(self):\n        sys.exit(1)\n"
                 "_thread.start_new_thread(types.MethodType(threading.Thread._bootstrap, "
                 "_NotThread()), ())\n" + raw_wait
                 + "_selftest_exit_report.exit_with(0)\n", unobserved),
                ("subinterp-import-str-subclass", "class _Name(str):\n"
                 "    def __hash__(self):\n        return 1\n"
                 "    def __eq__(self, other):\n        return False\n"
                 "try:\n    __import__(_Name('_interpreters'))\nexcept ImportError:\n"
                 "    __import__(_Name('_xxsubinterpreters'))\n"
                 + arm_line + "_selftest_exit_report.exit_with(0)\n",
                 "cannot be observed from this one"),
                ("finalize-repr-raises", evil_repr
                 + "class _Ids:\n    def __iter__(self):\n        raise _Fault('ST_FINALIZE')\n"
                 "_selftest_exit_report.arm(report, 'demo', _Ids())\n"
                 "_selftest_exit_report.exit_with(0)\n", "cannot finalize execution report")):
            code, _out, err = run(build(_manifest_text(), body))
            expect("st/finalizer-{}-2".format(label), code, 2)
            expect("st/finalizer-{}-named".format(label), named in err, True)
        expect("st/finalizer-finalize-repr-raises-guarded",
               "the finalizer's failure could not be described" in err, True)
        code, _out, err = run(build(_manifest_text(), _report_body(GOOD_IDS, 0, before_exit=(
            "import atexit\nMARKER = 42\n"
            "def _late():\n    import __main__\n"
            "    if getattr(__main__, 'MARKER', None) != 42 or __main__.__file__ != __file__:\n"
            "        raise RuntimeError('ST_MAIN_LOST')\n"
            "atexit.register(_late)\n"))))
        expect("st/main-module-at-exit-passes", (code, err), (0, ""))
        # 25e (round 5): every hook and the finalizer keep, through interpreter teardown, what they
        # read: teardown clears module globals while the audit hook is still installed, so a hook
        # that read a global then faulted and refused a clean run. A retained clean destructor that
        # opens a file, and one that calls a captured sys.audit, run at teardown and pass; their
        # faulting twin is still refused through the error stream; and
        # no hook or finalizer looks up a global or builtin name at call time (a static witness
        # over every one, including those no teardown vector reaches)
        for label, vector in (("open", TEARDOWN_OPEN), ("captured-audit", TEARDOWN_AUDIT)):
            code, _out, err = run(build(_manifest_text(),
                                        _report_body(GOOD_IDS, 0, before_exit=vector)))
            expect("st/teardown-clean-{}-passes".format(label), (code, err), (0, ""))
        code, _out, err = run(build(_manifest_text(),
                                    _report_body(GOOD_IDS, 0, before_exit=TEARDOWN_FAULT)))
        expect("st/teardown-fault-2", code, 2)
        expect("st/teardown-fault-named",
               ("ST_TEARDOWN_FAULT" in err, "error stream carries a fault" in err), (True, True))
        spec = importlib.util.spec_from_file_location(
            "_selftest_exit_report_probe",
            str(Path(__file__).resolve().parent / "_selftest_exit_report.py"))
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        for label, function in (
                ("audit", probe._audit), ("file-digest", probe._file_digest),
                ("genuine-bootstrap", probe._genuine_bootstrap),
                ("thread-hook", probe._thread_hook), ("clean-status", probe._clean_status),
                ("suite-exit-code", probe._SuiteExit.code.fget),
                ("shutdown-wrapper", probe._wrap_shutdown(int)),
                ("interpreters-refusal", probe._refuse_interp_entry("create")),
                ("harness-error", probe._harness_error), ("finalize", probe._finalize)):
            expect("st/teardown-bound-{}".format(label), _global_name_reads(function), [])
        # 25f (round 7): the static witness names an import at call time (IMPORT_NAME, and
        # IMPORT_FROM for a from-import), which fails at teardown once sys.meta_path is cleared

        def _imports_name(event, args):
            import json
            return json

        def _imports_from(event, args):
            from os import path
            return path
        expect("st/teardown-bound-import-name-named", _global_name_reads(_imports_name),
               [("_imports_name", "IMPORT_NAME", "json")])
        expect("st/teardown-bound-import-from-named", _global_name_reads(_imports_from),
               [("_imports_from", "IMPORT_FROM", "path"), ("_imports_from", "IMPORT_NAME", "os")])
        # 25g (round 7), a RESIDUAL WITNESS, not a guarantee: a faulting destructor that CPython runs
        # at late teardown, after it has dropped sys.stderr, raises with an empty error stream, so
        # the gate PASSES the run while the marker shows the destructor ran; the three named
        # examples of the disclosed class (the codec search registry, an audit hook (round 8), a
        # cycle anchored on a sys attribute) are pinned, so if the interpreter ever reports such a
        # fault this leg fails and the disclosure must change; the class is not enumerated further
        for label, placement in (("codec-registry", RESIDUAL_CODEC_REGISTRY),
                                 ("audit-hook", RESIDUAL_AUDIT_HOOK),
                                 ("sys-attribute-cycle", RESIDUAL_SYS_CYCLE)):
            root = build(_manifest_text(), _report_body(
                GOOD_IDS, 0, before_exit=RESIDUAL_LATE_FAULT + placement))
            code, _out, err = run(root)
            marker = root / "tools" / "late.marker"
            ran = marker.read_text(encoding="utf-8") if marker.exists() else None
            expect("st/residual-witness-late-teardown-{}-passes".format(label), (code, err, ran),
                   (0, "", "ran"))
        # 25h (round 7): a faulting cycle anchored on a module global or a class attribute is freed
        # while sys.stderr exists, because the finalizer drops the exit exception (whose traceback
        # held the runner's globals) after writing the report; each is refused, its destructor ran
        for label, placement in (("module-global", TEARDOWN_CYCLE_GLOBAL),
                                 ("class-attribute", TEARDOWN_CYCLE_CLASS)):
            root = build(_manifest_text(), _report_body(
                GOOD_IDS, 0, before_exit=RESIDUAL_LATE_FAULT + placement))
            code, _out, err = run(root)
            marker = root / "tools" / "late.marker"
            ran = marker.read_text(encoding="utf-8") if marker.exists() else None
            expect("st/teardown-cycle-{}-refused".format(label),
                   (code, "ST_RESIDUAL_LATE_FAULT" in err, ran), (2, True, "ran"))

        # 26: the declared stderr allowance is byte-exact over the WHOLE stream: the declared bytes
        # pass, and a repetition, a line-separator variant (CRLF, vertical tab, file separator), a
        # missing trailing newline, a prefix, an empty stream where bytes are declared, invalid UTF-8
        # against its replacement character, and a declaration that is not bytes (a str whose
        # substrings, or a tuple whose lines, would otherwise match) are each a fault; the pinned
        # allowance is restored after
        noisy = "sys.stderr.write('declared framing\\n')\n"
        framing = b"declared framing\n"
        real_declared = dict(DECLARED_STDERR)
        DECLARED_STDERR["demo"] = framing
        try:
            code, _out, _err = run(build(_manifest_text(), _report_body(GOOD_IDS, 0,
                                                                        before_exit=noisy)))
            expect("st/stderr-declared-passes", code, 0)
            code, _out, _err = run(build(_manifest_text(), _report_body(
                GOOD_IDS, 0, before_exit=noisy + "sys.stderr.write('declared\\n')\n")))
            expect("st/stderr-undeclared-2", code, 2)
            code, _out, _err = run(build(_manifest_text(), _report_body(
                GOOD_IDS, 0, before_exit=noisy + noisy)))
            expect("st/stderr-declared-repeated-2", code, 2)
            for label, blob in (("repeated", framing * 3),
                                ("crlf", b"declared framing\r\n"),
                                ("no-newline", b"declared framing"),
                                ("vertical-tab", b"declared framing\x0b"),
                                ("file-separator", b"declared framing\x1c" + framing),
                                ("prefix", b"declared\n"),
                                ("empty", b"")):
                expect("st/stderr-exact-{}".format(label), _stderr_fault(blob, "demo") is None, False)
            expect("st/stderr-exact-match", _stderr_fault(framing, "demo"), None)
            DECLARED_STDERR["demo"] = "\ufffd\n".encode("utf-8")
            expect("st/stderr-exact-invalid-utf8", _stderr_fault(b"\xff\n", "demo") is None, False)
            for label, declared in (("str", "declared framing\n"), ("tuple", ("declared framing",))):
                DECLARED_STDERR["demo"] = declared
                expect("st/stderr-declared-{}-refused".format(label),
                       (_stderr_fault(framing, "demo") is None, _stderr_fault(b"\n", "demo") is None),
                       (False, False))
        finally:
            DECLARED_STDERR.clear()
            DECLARED_STDERR.update(real_declared)
        code, _out, _err = run(build(_manifest_text(), _report_body(GOOD_IDS, 0, before_exit=noisy)))
        expect("st/stderr-undeclared-suite-2", code, 2)
        expect("st/stderr-undeclared-newline", _stderr_fault(b"\n", "demo") is None, False)
        expect("st/stderr-undeclared-empty", _stderr_fault(b"", "demo"), None)

        # 27 (split suites): rows that share a runner form a group whose expected sets must be pairwise
        # disjoint and union to the runner's static check() set exactly, each row carrying args no
        # other row of the group carries, forwarded ahead of the gate's own --execution-report pair
        # (the child records its argv); a gap, an overlap, an extra id, a missing or repeated args
        # list, and a malformed argument or one naming the report option are each refused 2 before any
        # launch; two spellings of one runner form one group; a singleton row is held to the whole set
        # as before, its behaviour byte-identical beside a split group
        lanes = {"one": ("demo-one", ["a/one"]), "two": ("demo-two", ["a/two", "a/three"]),
                 None: ("demo", GOOD_IDS)}
        one, two = ["--lane", "one"], ["--lane", "two"]
        split = [("demo-one", ["a/one"], one), ("demo-two", ["a/two", "a/three"], two)]
        root = build(_group_text(split), _lane_body(lanes))
        runner_path = str(root / "tools" / "fake_runner.py")
        for sid, lane, count in (("demo-one", "one", 1), ("demo-two", "two", 2)):
            code, out, err = run(root, sid)
            expect("st/group-{}-passes".format(lane), (code, err), (0, ""))
            expect("st/group-{}-pass-line".format(lane),
                   "PASS: suite {} executed {} unique registered checks".format(sid, count) in out, True)
            argv = recorded_argv(root)
            expect("st/group-{}-args-forwarded".format(lane),
                   (argv[:-1], os.path.isabs(argv[-1])) if argv else None,
                   ([runner_path, "--lane", lane, "--execution-report"], True))
        valid_args = ["--lane-free", "-v", "--shard=1/3", "x.y_z", "A:B,C+D"]
        root = build(_group_text([("demo", GOOD_IDS, valid_args)]), _lane_body(lanes))
        code, _out, err = run(root)
        expect("st/args-singleton-passes", (code, err), (0, ""))
        argv = recorded_argv(root)
        expect("st/args-singleton-forwarded", argv[:-1] if argv else None,
               [str(root / "tools" / "fake_runner.py")] + valid_args + ["--execution-report"])
        gap_named = "gap: source check registered by no suite of the group: a/three"
        for label, rows, sids, named in (
                ("gap", [("demo-one", ["a/one"], one), ("demo-two", ["a/two"], two)],
                 ("demo-one", "demo-two"), gap_named),
                ("overlap", [("demo-one", ["a/one", "a/two"], one),
                             ("demo-two", ["a/two", "a/three"], two)],
                 ("demo-one", "demo-two"), "overlap: a/two registered by suites demo-one, demo-two"),
                ("extra", [("demo-one", ["a/one"], one),
                           ("demo-two", ["a/two", "a/three", "a/rogue"], two)],
                 ("demo-one", "demo-two"), "extra: registered id has no source check(): a/rogue"),
                ("args-missing", [("demo-one", ["a/one"], None),
                                  ("demo-two", ["a/two", "a/three"], two)],
                 ("demo-one", "demo-two"), "suite demo-one shares its runner but carries no args"),
                ("args-repeated", [("demo-one", ["a/one"], one),
                                   ("demo-two", ["a/two", "a/three"], one)],
                 ("demo-two",), "suites demo-one and demo-two carry the same args"),
                ("respelled-runner-gap", [("demo-one", ["a/one"], one),
                                          ("demo-two", ["a/two"], two, "tools/./fake_runner.py")],
                 ("demo-one", "demo-two"), gap_named)):
            for sid in sids:
                root = build(_group_text(rows), _lane_body(lanes))
                code, _out, err = run(root, sid)
                expect("st/group-{}-{}-2".format(label, sid), code, 2)
                expect("st/group-{}-{}-named".format(label, sid),
                       ("do not partition" in err, named in err), (True, True))
                expect("st/group-{}-{}-no-launch".format(label, sid), launched(root), False)
        # group membership is resolved strictly for EVERY row: an unrelated row whose runner is a
        # missing file, runs through a file used as a directory, sits behind a symlink loop, or (off
        # root only, where permissions bind) sits in an unsearchable directory leaves membership
        # undecidable, so the requested singleton is refused 2, naming that row, before any report
        # directory is allocated or any runner launched; beside a resolvable unrelated row it passes
        allocated = []
        real_mkdtemp = tempfile.mkdtemp

        def counting_mkdtemp(*args, **kwargs):
            allocated.append(kwargs.get("prefix"))
            return real_mkdtemp(*args, **kwargs)

        def run_counted(root, suite_id="demo"):
            del allocated[:]
            tempfile.mkdtemp = counting_mkdtemp
            try:
                return run(root, suite_id)
            finally:
                tempfile.mkdtemp = real_mkdtemp

        def beside(other_runner):
            rows = [("demo", GOOD_IDS, None), ("other", ["b/one"], None, other_runner)]
            return build(_group_text(rows), _report_body(GOOD_IDS, 0))

        root = beside("tools/other_runner.py")
        (root / "tools" / "other_runner.py").write_text("print('other')\n", encoding="utf-8")
        code, _out, err = run_counted(root)
        expect("st/strict-member-resolvable-passes", (code, err, len(allocated)), (0, "", 1))
        unresolvable = [("missing-file", "tools/no_such_runner.py", None),
                        ("file-as-directory", "tools/fake_runner.py/runner.py", None),
                        ("symlink-loop", "tools/loop_a/runner.py", "loop")]
        probe = base / "permission-probe"
        (probe / "inner").mkdir(parents=True)
        (probe / "inner" / "file").write_text("", encoding="utf-8")
        probe.chmod(0)
        try:
            os.lstat(probe / "inner" / "file")
        except PermissionError:
            unresolvable.append(("unsearchable-directory", "tools/denied/runner.py", "denied"))
        else:
            # Never a silent skip: where permissions do not bind (root, CAP_DAC_OVERRIDE), the
            # unsearchable-directory vectors cannot run, and that is a named failure of this run.
            failures.append(
                "st/strict-member-unsearchable-directory-not-run: the chmod-0 probe did not raise "
                "PermissionError, so permissions do not bind for this uid and the "
                "unsearchable-directory vectors cannot run here; run the self-test as a uid "
                "directory permissions bind for")
        finally:
            probe.chmod(0o700)
        for label, other_runner, setup in unresolvable:
            root = beside(other_runner)
            tools = root / "tools"
            if setup == "loop":
                os.symlink("loop_b", tools / "loop_a")
                os.symlink("loop_a", tools / "loop_b")
            elif setup == "denied":
                (tools / "denied").mkdir()
                (tools / "denied" / "runner.py").write_text("print('denied')\n", encoding="utf-8")
                (tools / "denied").chmod(0)
            try:
                code, _out, err = run_counted(root)
            finally:
                if setup == "denied":
                    (tools / "denied").chmod(0o700)
            expect("st/strict-member-{}-2".format(label), code, 2)
            expect("st/strict-member-{}-named".format(label),
                   ("suite demo: cannot decide which rows share its runner" in err,
                    "the runner {} of suite other does not resolve".format(other_runner) in err),
                   (True, True))
            expect("st/strict-member-{}-no-allocation".format(label), allocated, [])
            expect("st/strict-member-{}-no-launch".format(label), launched(root), False)
        # a runner path carrying a control or format character (C0, DEL, C1, a bidi control, U+2028,
        # U+2029) is refused by the schema, in the requested row or in an unrelated one, so no
        # cross-row resolution message names a row that shares nothing
        for label, value, index in (("nul", "\\u0000", 1), ("soh", "\\u0001", 1), ("tab", "\\t", 1),
                                    ("del", "\\u007f", 1), ("c1-nel", "\\u0085", 1),
                                    ("c1-csi", "\\u009b", 1), ("line-separator", "\\u2028", 1),
                                    ("paragraph-separator", "\\u2029", 1),
                                    ("bidi-rlo", "\\u202e", 1), ("requested-row", "\\u0001", 0)):
            rows = [("demo", GOOD_IDS, None), ("other", ["b/one"], None, "tools/other_runner.py")]
            text = _group_text(rows).replace(
                '"tools/{}"'.format("fake_runner.py" if index == 0 else "other_runner.py"),
                '"tools/x{}.py"'.format(value))
            root = build(text, _report_body(GOOD_IDS, 0))
            (root / "tools" / "other_runner.py").write_text("print('other')\n", encoding="utf-8")
            code, _out, err = run_counted(root)
            expect("st/runner-ctrl-{}-2".format(label), code, 2)
            expect("st/runner-ctrl-{}-named".format(label),
                   ("suite[{}]: runner must be a repo-relative path with no '..' and no control "
                    "or format character".format(index) in err,
                    "cannot decide which rows share" in err), (True, False))
            expect("st/runner-ctrl-{}-no-launch".format(label), (launched(root), allocated),
                   (False, []))
        # a suite id outside the closed grammar is refused BY NAME before any resolution, report
        # allocation or launch: the id is one launch-line argument in both rosters and the one CLI
        # argument after --suite, so a name the grammar excludes could never be launched as written
        for label, bad in (("space", "demo two"), ("leading-dash", "-demo"), ("slash", "a/b"),
                           ("empty", ""), ("control", "demo\u0001"), ("non-ascii", "caf\u00e9")):
            text = _manifest_text().replace('id = "demo"', "id = " + json.dumps(bad))
            root = build(text, _report_body(GOOD_IDS, 0))
            code, _out, err = run_counted(root)
            expect("st/suite-id-{}-2".format(label), code, 2)
            expect("st/suite-id-{}-named".format(label),
                   "suite[0]: id {!r} must match the closed suite-id grammar".format(bad) in err,
                   True)
            expect("st/suite-id-{}-no-launch".format(label), (launched(root), allocated),
                   (False, []))
        # the round-2 reviewer's reproduction: a sibling row whose id carries a space, with the
        # space-joined launch line in BOTH rosters, is refused by the schema naming that id (the
        # CLI accepts exactly ["--suite", <id>], so that line could never launch the row), and
        # nothing is resolved, allocated or launched
        spaced = [("demo-one", ["a/one"], one), ("demo-two extra", ["a/two", "a/three"], two)]
        spaced_rosters = {rel: text.replace("--suite demo-two", "--suite demo-two extra")
                          for rel, text in _roster_texts(["demo-one", "demo-two"]).items()}
        root = build(_group_text(spaced), _lane_body(lanes), rosters=spaced_rosters)
        code, _out, err = run_counted(root, "demo-one")
        expect("st/suite-id-spaced-sibling-2", code, 2)
        expect("st/suite-id-spaced-sibling-named",
               "suite[1]: id 'demo-two extra' must match the closed suite-id grammar" in err,
               True)
        expect("st/suite-id-spaced-sibling-no-launch", (launched(root), allocated), (False, []))
        # launch coverage: every registered row, a split group's included, must have its launch line in
        # both launch rosters, each read as a regular file through the parity gate's loader; a group row
        # never launched from either roster (absent, or only commented out), an unrelated row never
        # launched, a launch line that is not exactly the two-argument CLI form (a near-miss id, an
        # extra argument), and a roster that is missing, a directory, a symlink, rejected by its
        # loader (a CI-side diagnostic included), carrying a byte outside the loader's alphabet, or
        # unreadable for want of the loader module are each refused 2 before any allocation or launch
        local_rel, ci_rel = LAUNCH_ROSTERS[0][0], LAUNCH_ROSTERS[1][0]
        both = _roster_texts(["demo-one", "demo-two"])
        unrelated = [("demo-one", GOOD_IDS, None),
                     ("demo-two", ["b/one"], None, "tools/other_runner.py")]
        unrelated_body = _lane_body({None: ("demo-one", GOOD_IDS)})
        root = build(_group_text(unrelated), unrelated_body)
        (root / "tools" / "other_runner.py").write_text("print('other')\n", encoding="utf-8")
        code, _out, err = run_counted(root, "demo-one")
        expect("st/launch-unrelated-row-launched-passes", (code, err, len(allocated)), (0, "", 1))
        unlaunched_named = ("suite demo-two has no launch line (tools/check_selftest_execution.py "
                            "--suite demo-two) in {}")
        for label, rows, rosters, named in (
                ("group-row-not-in-ci", split, {ci_rel: _roster_texts(["demo-one"])[ci_rel]},
                 unlaunched_named.format(ci_rel)),
                ("group-row-not-in-local", split, {local_rel: _roster_texts(["demo-one"])[local_rel]},
                 unlaunched_named.format(local_rel)),
                ("group-row-commented-in-local", split,
                 {local_rel: both[local_rel].replace('run_gate "demo-two"', '# run_gate "demo-two"')},
                 unlaunched_named.format(local_rel)),
                ("group-row-commented-in-ci", split,
                 {ci_rel: both[ci_rel].replace(
                     "run: python3 -I -B tools/check_selftest_execution.py --suite demo-two",
                     "# run: (not launched)")},
                 unlaunched_named.format(ci_rel)),
                ("unrelated-row-not-in-ci", unrelated, {ci_rel: _roster_texts(["demo-one"])[ci_rel]},
                 unlaunched_named.format(ci_rel)),
                ("near-miss-id-in-ci", split,
                 {ci_rel: _roster_texts(["demo-one", "demo-two-x"])[ci_rel]},
                 unlaunched_named.format(ci_rel)),
                ("extra-argument-in-local", split,
                 {local_rel: both[local_rel].replace(
                     "--suite demo-two", "--suite demo-two --verbose")},
                 unlaunched_named.format(local_rel)),
                ("extra-argument-in-ci", split,
                 {ci_rel: both[ci_rel].replace(
                     "--suite demo-two", "--suite demo-two --verbose")},
                 unlaunched_named.format(ci_rel)),
                ("ci-loader-diagnostic", split, {ci_rel: both[ci_rel] + "stray: 1\n"},
                 "top-level line 'stray: 1' is outside the recognized workflow keys"),
                ("local-cr-byte", split, {local_rel: None},
                 "byte b'\\r' is outside printable ASCII"),
                ("local-missing", split, {local_rel: None}, "{} unreadable: ".format(local_rel)),
                ("ci-missing", split, {ci_rel: None}, "{} unreadable: ".format(ci_rel)),
                ("local-directory", split, {local_rel: None},
                 "{} is not a regular file".format(local_rel)),
                ("ci-symlink", split, {ci_rel: None}, "{} is not a regular file".format(ci_rel)),
                ("local-loader-diagnostic", split,
                 {local_rel: both[local_rel].replace("failed=0\n", "")},
                 "launch roster {} cannot be evaluated: line 0: missing initial failed=0".format(
                     local_rel)),
                ("loader-unimportable", split, {}, "the parity gate's roster loaders")):
            root = build(_group_text(rows), unrelated_body if rows is unrelated else _lane_body(lanes),
                         rosters=rosters)
            (root / "tools" / "other_runner.py").write_text("print('other')\n", encoding="utf-8")
            if label == "local-directory":
                (root / local_rel).mkdir()
            elif label == "local-cr-byte":
                # one CR byte: read_runner_text screens RAW BYTES, so the roster is refused before
                # universal-newline translation could erase the byte ahead of a text-level screen
                (root / local_rel).write_bytes(both[local_rel].encode("ascii") + b"# tail\r\n")
            elif label == "ci-symlink":
                (root / "elsewhere.yml").write_text(both[ci_rel], encoding="ascii")
                (root / ci_rel).parent.mkdir(parents=True)
                os.symlink(root / "elsewhere.yml", root / ci_rel)
            real_parity = check_ci_parity
            if label == "loader-unimportable":
                globals()["check_ci_parity"], globals()["PARITY_IMPORT_ERROR"] = None, "withheld"
            try:
                code, _out, err = run_counted(root, "demo-one")
            finally:
                globals()["check_ci_parity"] = real_parity
            expect("st/launch-{}-2".format(label), code, 2)
            expect("st/launch-{}-named".format(label), ("CANNOT EVALUATE: " in err, named in err),
                   (True, True))
            expect("st/launch-{}-no-launch".format(label), (launched(root), allocated), (False, []))
        for label, value in (
                ("string", '"--lane"'), ("empty", "[]"), ("int", "[1]"), ("bool", "[true]"),
                ("float", "[1.5]"), ("nested", '[["--lane"]]'), ("table", "[{lane = 1}]"),
                ("empty-string", '[""]'), ("report-option", '["--execution-report"]'),
                ("report-option-attached", '["--execution-report=/tmp/elsewhere.json"]'),
                ("report-option-abbreviated", '["--exec"]'),
                ("report-option-abbreviated-attached", '["--exec=/tmp/elsewhere.json"]'),
                ("report-option-suffixed", '["--execution-reports"]'),
                ("end-of-options", '["--"]'), ("lone-dash", '["-"]'), ("triple-dash", '["---lane"]'),
                ("expansion", '["$HOME"]'), ("glob", '["*"]'), ("whitespace", '["a b"]'),
                ("control", '["a\\u0001"]'), ("newline", '["a\\n"]'), ("absolute", '["/tmp/x"]'),
                ("relative-dot", '["./x"]'), ("at-file", '["@args.txt"]'), ("tilde", '["~"]'),
                ("quote", '["a\'b"]'), ("non-ascii", '["caf\\u00e9"]'),
                ("over-long", json.dumps(["a" * (ROW_ARG_MAX + 1)]))):
            root = build(_group_text([("demo", GOOD_IDS, value)]), _report_body(GOOD_IDS, 0))
            code, _out, err = run(root)
            expect("st/args-{}-2".format(label), code, 2)
            expect("st/args-{}-named".format(label), "suite[0]: args" in err, True)
            expect("st/args-{}-no-launch".format(label), launched(root), False)
        code, _out, err = run(build(_group_text([("demo", GOOD_IDS, ["a" * ROW_ARG_MAX])]),
                                    _report_body(GOOD_IDS, 0)))
        expect("st/args-at-length-bound-passes", (code, err), (0, ""))
        # the singleton row, run alone and beside a split group of another runner: its return code,
        # output, error stream and recorded argv are byte-identical (root paths normalized), on a
        # pass and on a static mismatch
        for label, ids, want in (("pass", GOOD_IDS, 0), ("mismatch", ["a/one", "a/two"], 2)):
            seen = []
            for beside in (False, True):
                text = _manifest_text(ids=ids)
                if beside:
                    text += _group_text(split, runner="tools/split_runner.py", header="")
                root = build(text, _lane_body(lanes))
                if beside:
                    (root / "tools" / "split_runner.py").write_bytes(
                        (root / "tools" / "fake_runner.py").read_bytes())
                code, out, err = run(root)
                argv = recorded_argv(root)
                prefix = str(root)
                seen.append((code, out.replace(prefix, "<root>"), err.replace(prefix, "<root>"),
                             [arg.replace(prefix, "<root>") for arg in argv[:-1]] if argv else None))
            expect("st/singleton-{}-code".format(label), seen[0][0], want)
            expect("st/singleton-{}-byte-identical-beside-group".format(label), seen[1], seen[0])
            if want == 0:
                expect("st/singleton-{}-argv-unchanged".format(label), seen[0][3],
                       ["<root>/tools/fake_runner.py", "--execution-report"])

        # Round 3 (FIX E note): leg 19 witnesses run_suite's mkdtemp guard; self_test's own base
        # tempdir guard above cannot be witnessed from inside this self-test without circularity,
        # and the runner's FAILURES-before-report-write-OSError ordering has no cheap hermetic
        # witness from here, so both remain manual-flip verifications, disclosed rather than forced
        # into a fragile test. Round 6: run_suite's git_home os.mkdir guard is the same kind, a
        # manual-flip verification, unwitnessed here and essentially unreachable (a fresh mkdir
        # inside the just-created private tempdir).
    finally:
        shutil.rmtree(base, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: the execution-set gate passes an exact or reordered set, names a missing and "
          "an extra id, refuses duplicate observed and expected ids (the latter with no launch), refuses "
          "a malformed expectation manifest before any launch, treats a missing, malformed, wrong-suite, "
          "duplicate-member, non-regular, in-band, unfinalized, or exit-status-mismatched report as no "
          "verdict, refuses an atexit, destructor, or thread fault the child survived (an error stream "
          "differing by any byte from the declared bytes) while each fault-free twin passes, refuses a "
          "silent failure SystemExit in a non-daemon thread, a thread fault whose repr or code "
          "property raises, a thread started through _thread directly at any moment from process "
          "start (a forged or non-Thread bootstrap target included), a thread or atexit callback "
          "added after the exit-time join, a trace, profile or monitoring callback (each "
          "raising SystemExit(1) in a thread after run()), an interpreter created through "
          "_interpreters (after a reload too) or concurrent.interpreters, a fresh load of "
          "subinterpreter machinery (a str-subclass name, a qualified alias of its extension file, "
          "and a copy of that file included), a delivered interpreter-creation audit event, a "
          "finalizer failure whose repr raises, while a clean concurrent.futures pool and a clean "
          "asyncio.run pass, "
          "and an armed child whose "
          "observation hooks were not installed at process start, refuses an armed "
          "child whose status or report bypassed the exit finalizer, whose recorded status the "
          "interpreter's own exit did not confirm, or whose report's exit_code differs from the "
          "real exit status the finalizer defers to, never masks a failing child behind a "
          "complete set, refuses a child harness error, refuses an escaping, non-regular, or symlinked "
          "runner without launching, reconciles the runner's static check() source set against the "
          "manifest before any launch (refusing an unregistered source check, a registered id with no "
          "source site, a dead duplicate-id alias, an unresolvable dynamic id, a statically empty "
          "literal loop, a non-call reference to "
          "the bare name check, and a comprehension-bound id, while the loop-literal pattern and a "
          "literal id inside a comprehension resolve and a clean twin passes), launches the child under "
          "a git-neutral, interpreter-neutral environment (ambient GIT_* and PYTHON* dropped, config "
          "surfaces pinned to os.devnull, "
          "global ignore and attributes overridden to os.devnull by injected config, and HOME and "
          "XDG_CONFIG_HOME pinned to a fresh empty directory a descendant GIT_* re-scrub cannot strip), "
          "treats unavailable temp storage as "
          "cannot-evaluate, refuses to guess its repo root when run outside a checkout, and catches the "
          "near-miss (a green child that never executed a registered check) while its complete-report "
          "twin passes, keeps the runner's module as __main__ for its exit handlers, and passes a "
          "clean destructor run at interpreter teardown (a file open or a captured sys.audit) while "
          "refusing its faulting twin, every hook and the finalizer reading no module global or "
          "builtin and importing nothing at call time (an import named by the static witness), and "
          "witnesses the disclosed late-teardown residual class (a faulting destructor held by the "
          "codec search registry, by an audit hook, or in a cycle anchored on a sys attribute, "
          "passes while its marker shows it ran) while refusing a faulting cycle anchored on a "
          "module global or a class attribute, and, for rows that share a runner, passes each row "
          "of a group that partitions the static set with its own args forwarded ahead of the "
          "report option, while refusing before any launch a gap, an overlap, an extra id, a "
          "missing or repeated args list, and a malformed argument or one naming the report "
          "option, a singleton row behaving byte-identically beside a split group, refuses before any "
          "report directory or launch a row whose runner does not resolve strictly (a missing file, a "
          "file used as a directory, a symlink loop, and an unsearchable directory where permissions "
          "bind, a not-run failure named where they do not) and a runner path carrying a control or "
          "format character (C1, U+2028, U+2029 and a bidi control included), refuses by name a "
          "suite id outside the closed suite-id grammar (a space-carrying sibling id, its "
          "space-joined launch line in both rosters, included) with nothing resolved, allocated or "
          "launched, and refuses before any launch a "
          "registered row, of a split group or not, whose launch line is missing from at least one "
          "launch roster (a commented-out line, a near-miss id, and a launch line carrying any "
          "extra argument included) and a launch roster that is missing, a directory, a symlink, "
          "rejected by the parity gate's loader (a CI-side diagnostic included), carrying a byte "
          "outside the loader's alphabet (a carriage return included), or unreadable for want of "
          "that loader")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--self-test"]:
        return self_test()
    if len(argv) == 2 and argv[0] == "--suite" and argv[1]:
        # Anchored to this file, never the cwd: an exported copy run from inside another checkout
        # must refuse rather than validate the wrong tree. A worktree's .git is a file, so .exists().
        root = Path(__file__).resolve().parent.parent
        if not (root / ".git").exists():
            _cannot("cannot confirm the gate's own repo root (no .git at {})".format(root))
            return 2
        return run_suite(root, argv[1])
    print("usage: check_selftest_execution.py --suite <id> | --self-test", file=sys.stderr)
    return 2


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. Inline, not imported, because this file's
# own self-test copies it alone into an exported directory. This file launches every registered suite and
# reads each child's exit status, so under an ignored SIGCHLD it would read a failing suite as a pass; it
# refuses at its entry line instead, before main() runs.
# The status the probe child exits with; any value other than 0 and the refusal exit would do.
_CHILD_STATUS_PROBE_CODE = 77


def _child_status_refusal():
    """None when this process can read a child's exit status, otherwise the reason it cannot."""
    import os
    import signal
    if not hasattr(signal, "SIGCHLD"):
        return None
    disposition = signal.getsignal(signal.SIGCHLD)
    if disposition is not signal.SIG_DFL:
        return ("SIGCHLD is ignored (SIG_IGN) in this process" if disposition is signal.SIG_IGN
                else "SIGCHLD is not at its default disposition in this process")
    if not hasattr(os, "fork"):
        return None
    try:
        pid = os.fork()
    except OSError as exc:
        return "the child-status probe could not fork ({})".format(type(exc).__name__)
    if pid == 0:
        os._exit(_CHILD_STATUS_PROBE_CODE)
    try:
        waited, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return "the probe child was reaped before its exit status could be read (ECHILD)"
    except OSError as exc:
        return "the probe child's exit status could not be read ({})".format(type(exc).__name__)
    if waited != pid or os.waitstatus_to_exitcode(status) != _CHILD_STATUS_PROBE_CODE:
        return "a probe child that exited {} was read back as wait status {}".format(
            _CHILD_STATUS_PROBE_CODE, status)
    return None


def _child_status_refusal_exit(name):
    """0 when a child's exit status is observable; otherwise write the named refusal and exit 2 at once
    (os._exit, so no exit-time stream flush can replace the status)."""
    import os
    reason = _child_status_refusal()
    if reason is None:
        return 0
    try:
        sys.stderr.write("error: {} refuses to run: {}, so a failed child would read as a success. Nothing "
                         "was run (cannot evaluate). Start it from a parent that leaves SIGCHLD at "
                         "SIG_DFL.\n".format(name, reason))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)



if __name__ == "__main__":
    sys.exit(_child_status_refusal_exit("check_selftest_execution.py") or main())
