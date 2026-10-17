#!/usr/bin/env python3
"""Measure the wall time of the shipped PreToolUse hooks on synthetic worst-case inputs: a measurement tool,
never a gate. It decides nothing on timing; it prints what it measured and the conditions it measured under,
so a later change to the hook timeout (tools/gen_hooks.py TIMEOUT) can be sized from figures.

WHAT RUNS. Each hook runs as hooks.json launches it: the command and args of its PreToolUse entry in
plugin/aiqt-guardrails-hooks/hooks/hooks.json, with ${CLAUDE_PLUGIN_ROOT} replaced by the plugin directory,
the event JSON on stdin, and the interpreter in isolated mode as those args already say (-I). The hook id is
the dispatcher mode the entry passes as its last argument; hooks.json carries no other name (the kebab-case
control ids live only in .aiqt/core/hooks/manifest.toml). A matcher is applied to the tool name as a
whole-string regular expression (re.fullmatch), and every hook whose matcher matches a case's tool runs for
that case. Each case runs every matching hook --runs times (default 5), one child at a time, the runs
interleaved across the hooks of the case; a sample is the wall time from launch to exit. A call that matches
several hooks may run them concurrently on the platform; contention between sibling hooks is not in these
figures. Every PreToolUse hook in hooks.json must be run by at least one case that calls a tool, and every
such case must match at least one hook: a hook that no case runs, or a case that no hook matches, ends the run
with exit 2 naming it before the fixture root exists, never a silent skip (the startup case does not count,
because it times only the dispatcher's start). With --hook, a case that no selected hook matches is skipped
and listed as skipped.

THE CASES (every child runs in the git fixture below, and every event but startup's names it as cwd):
  startup          the first PreToolUse hook (hooks.json order) given the payload [] (a JSON array, not an
                   object): the dispatcher rejects it before any handler runs and exits 2, so the figure is
                   interpreter start, the dispatcher's import and one json.loads;
  bash-<size>      Bash, a command of exactly the dispatcher's command budget (_COMMIT_MAX_COMMAND, read from
                   the dispatcher; 65536 characters at this writing, so the case is bash-64kib): at most 64
                   read-only `git log` segments joined by && naming quoted absolute paths, padded to the exact
                   length (the commit grammar refuses a command of more than 64 segments at once, so more
                   segments would end that scan early);
  write-<size>     Write, a content of 1, 4 and 16 MiB of lines `password = "${DB_PASSWORD}"`: each line
                   reaches the secrets scan's credential-assignment and entropy checks and holds a
                   placeholder, so no line ends the scan early;
  ask-user-question, schedule-wakeup
                   one small AskUserQuestion and ScheduleWakeup call, so every PreToolUse hook id has a figure
                   (small inputs, not worst cases);
  git-discard      Bash `git clean -f -d` in the fixture with --files untracked files (default 20000);
  branch-root      Bash `git switch -c measure/new measure/orphan`, an orphan start with no merge base with
                   main, over --commits commits of history on main (default 1000);
  protected-line   Bash `git push --force origin HEAD` with HEAD on the non-protected branch measure/feature,
                   which takes the push path's HEAD probe.
Every Bash hook runs in each Bash case, the three git cases included; the hook id in a GIT_CASES entry names
the hook whose path that case's fixture state is built to reach.
The Bash and Write shapes are chosen to reach the scanning paths; they are not proven worst cases for every
handler. The outcomes column shows what each hook decided, so a sample that ended early on a deny is visible.

ISOLATION. Every fixture lives under one tempfile.mkdtemp() root: the git fixture (a repository with main, an
orphan branch, a feature branch, a local bare origin and the untracked files) and the directories HOME,
XDG_CONFIG_HOME, XDG_STATE_HOME, XDG_CACHE_HOME, XDG_DATA_HOME and TMPDIR point at. The root is removed at the
normal end of a run (timed-out samples included), on an error, and on SIGINT, SIGTERM and SIGHUP. For the
measurement's duration this tool handles those three signals and restores the previous handlers afterwards
(a signal ignored when the run starts, as under nohup, stays ignored): the handler kills the process group of
the hook child or interpreter version probe then running and stops the run (of a fixture git call in progress,
only the direct git child is killed, by subprocess.run as the interruption passes through it), the root is
removed, and the tool exits 128 plus the signal number. A signal that arrives while the root is being made or
removed, or while a child is being launched, is held until that step ends. A root that cannot be removed is an
error (exit 2); when an error or a signal stopped the run first, that cause is the one reported and the root
left behind is reported with it. Residual: SIGKILL or a power loss leaves the root, and a hook child running at
that moment, behind; and the helpers a fixture git push starts are not the direct git child, so they can
outlive an interrupt. Each hook child gets PATH (inherited), LC_ALL=C.UTF-8,
those directories, CLAUDE_PLUGIN_ROOT and CLAUDE_PROJECT_DIR, and nothing else: every other ambient variable,
each GIT_* and PYTHON* one included, is dropped. This tool's own git calls (the fixture build, the git
version) get the same plus GIT_CONFIG_GLOBAL=os.devnull and GIT_CONFIG_NOSYSTEM=1. Those two are NOT given
to a hook child: the git-probing hooks read any GIT_* variable outside their cosmetic list as a
repository-view override and take a fail-safe path (branch_root and protected_line then deny without their
ancestry and HEAD probes; git_discard takes its override path), and the hooks drop every GIT_* variable
before their own git calls, so in a hook's environment the two would change what is measured and keep no
configuration out. The pinned HOME and XDG_CONFIG_HOME keep user configuration away from every child's git
calls. Outside the root this tool writes only the --json file. Residuals, disclosed:
the hooks' own git calls read the system git configuration (they drop GIT_CONFIG_NOSYSTEM by design); a hook
that writes to an absolute path of its own choosing is outside what the pins control; and the fixture's git
objects and the hooks' recovery refs grow inside the root from one run to the next.

CONDITIONS. The table is preceded by the conditions it was measured under: the load average at start and end
(os.getloadavg), the CPU count, the harness and hook interpreters, the git version and the fixture sizes.
--json PATH writes the same figures and conditions (a PATH that already exists is refused, never
overwritten).

SAMPLES. A completed sample is valid when its outcome is a hook decision: exit 0 with nothing on stdout
(allow), exit 0 with a permissionDecision of allow, deny or ask (or the older top-level decision approve or
block, read as allow or deny), or exit 0 with a systemMessage and no decision (warn). The startup case's one
valid outcome is the dispatcher's rejection of its payload: exit 2, nothing on stdout, and "payload is not a
JSON object" on stderr. Every other outcome is a failed sample: a nonzero exit (the handlers deny on exit 0,
so a nonzero exit from a handler is an error path, not a decision), a death by signal, a launch failure, or
stdout that names no decision. A failed sample contributes no time: it is counted in the row's failed column
and in its outcomes, never in min, median or max, and a row with no valid and no timed-out sample reads NO
VALID SAMPLE in place of all three figures, in the table and in --json. A run in which any row holds a failed
sample (a NO VALID SAMPLE row always does) cannot be evaluated: once the report is written it exits 3 (see
Exit). A timed-out sample is TIMED OUT, never a time: it is known only to exceed --case-timeout, so it sorts
after every valid sample, and a statistic whose position lands on it reads TIMED OUT. A timed-out sample is
not a failed sample, so timed-out samples alone never change the exit.

  measure_hook_latency.py [--runs N] [--files N] [--commits N] [--case-timeout SECONDS] [--hook ID]...
                          [--json PATH]
  measure_hook_latency.py --self-test     the harness's own checks, on stub hooks and tiny inputs
  measure_hook_latency.py [--self-test] --execution-report ABS_PATH
                                          the self-test, also writing its execution report, finalized at
                                          interpreter exit (tools/_selftest_exit_report.py), as
                                          tools/check_selftest_execution.py runs it

Exit: 0 the measurement ran and every row holds only valid and timed-out samples (whatever the times; timed-out
samples alone never change the exit); 3 cannot evaluate: the measurement ran and its report was written, but
at least one row holds a failed sample or reads NO VALID SAMPLE, and one stderr line gives how many rows and
names each as case/hook id (in an id, a backslash and every character str.isprintable() rejects, such as a
newline or a carriage return, are written as Python's repr writes them, so the line stays one line); 2 cannot
measure (a harness Python older than 3.14, a bad argument, an unreadable or malformed hooks.json, a hook id it
needs that hooks.json lacks, a hook that no case runs or a case that no hook matches, a dispatcher that names
no command budget, git or the hook interpreter missing, a hook interpreter whose version probe fails, a fixture
that cannot be built or removed, signal handlers that cannot be installed, a --json PATH that exists or cannot
be written (exit 2 then, whatever the rows hold), a host that is not POSIX); 128 plus the signal number (130,
143 or 129) when SIGINT, SIGTERM or SIGHUP stopped the run, after the fixture root is removed. The self-test
exits 0 pass, 1 a failed check.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: measure_hook_latency.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import builtins
import collections
import contextlib
import datetime
import io
import json
import math
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _selftest_exit_report  # noqa: E402  (the execution report, finalized at interpreter exit)

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_REL = "plugin/aiqt-guardrails-hooks"
HOOKS_JSON_REL = PLUGIN_REL + "/hooks/hooks.json"
PLUGIN_ROOT_TOKEN = "${CLAUDE_PLUGIN_ROOT}"
EVENT = "PreToolUse"
REGISTRY_REL = "tools/selftest_checks.toml"
SUITE_ID = "hook-latency-selftest"
ROOT_PREFIX = "aiqt-hook-latency-"

DEFAULT_RUNS = 5
DEFAULT_FILES = 20000
DEFAULT_COMMITS = 1000
DEFAULT_CASE_TIMEOUT = 60.0
# Inclusive bounds on the integer options, so one command line cannot ask for an unbounded run or fixture.
INT_OPTIONS = {"--runs": (1, 1000), "--files": (1, 1000000), "--commits": (1, 1000000)}
# --case-timeout must be greater than 0 (an exclusive lower end) and at most this (an inclusive upper end).
CASE_TIMEOUT_MAX = 3600.0
DECIMAL_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?")
INTEGER_RE = re.compile(r"[0-9]+")

# The startup case's payload: a JSON array, not an object, which the dispatcher rejects before any handler.
STARTUP_PAYLOAD = b"[]"
MIB = 1 << 20
WRITE_SIZES = (1 * MIB, 4 * MIB, 16 * MIB)
# A credential-named assignment whose value is a placeholder: the secrets scan runs its assignment and
# entropy checks on the line and finds nothing, so it reads the whole content.
WRITE_LINE = 'password = "${DB_PASSWORD}"\n'
# The dispatcher's Bash command budget, read from the dispatcher so the Bash case follows it.
BUDGET_RE = re.compile(r"^_COMMIT_MAX_COMMAND = ([0-9]+)[ \t]*(?:#.*)?$", re.M)
SEGMENT_LIMIT = 64
SEGMENT_HEAD = "git log --oneline -n 1 --"
SEGMENT_JOIN = " && "
SEGMENT_MIN = len(SEGMENT_HEAD) + 2   # the head, a space and a one-character pad word

FEATURE = "measure/feature"
ORPHAN = "measure/orphan"
# (case, the hook id its fixture is built for, the Bash command). Every Bash PreToolUse hook runs in each of
# these cases, as in any Bash case; the hook id names the one hook whose path the case's fixture state is
# built to reach, and it must be a Bash PreToolUse hook in hooks.json.
GIT_CASES = (
    ("git-discard", "git_discard", "git clean -f -d"),
    ("branch-root", "branch_root", "git switch -c measure/new " + ORPHAN),
    ("protected-line", "protected_line", "git push --force origin HEAD"),
)
FILES_PER_DIR = 200
FIXTURE_TIME = 1767225600            # 2026-01-01T00:00:00Z, the fixture's fixed commit time
FIXTURE_NAME = "Measure"
FIXTURE_EMAIL = "measure@example.invalid"
GIT_TIMEOUT = 600                    # one fixture git call
PROBE_TIMEOUT = 60                   # the interpreter and git version probes
REAP_SECONDS = 10                    # draining a killed child's output
# The directories each child's environment points into the fixture root.
PINNED_DIRS = (("HOME", "home"), ("XDG_CONFIG_HOME", "xdg/config"), ("XDG_STATE_HOME", "xdg/state"),
               ("XDG_CACHE_HOME", "xdg/cache"), ("XDG_DATA_HOME", "xdg/data"), ("TMPDIR", "tmp"))
TIMED_OUT = "TIMED OUT"
NO_VALID_SAMPLE = "NO VALID SAMPLE"
# The startup case's one valid outcome names this on stderr: the dispatcher's rejection of a payload that is
# not a JSON object.
STARTUP_REJECTION = b"payload is not a JSON object"
# The signals that stop a measurement through its handler (see _interrupt_handlers).
INTERRUPT_SIGNALS = tuple(getattr(signal, name) for name in ("SIGINT", "SIGTERM", "SIGHUP")
                          if hasattr(signal, name))
USAGE = ("usage: measure_hook_latency.py [--runs N] [--files N] [--commits N] [--case-timeout SECONDS] "
         "[--hook ID]... [--json PATH]\n"
         "       measure_hook_latency.py [--self-test] [--execution-report ABS_PATH]")

Hook = collections.namedtuple("Hook", ("hook_id", "matcher", "argv", "script", "timeout"))
# tool is None for the startup case, whose one hook is target and whose payload is fixed.
Case = collections.namedtuple("Case", ("name", "tool", "tool_input", "target", "payload", "note"))
# One launch: the seconds from launch to exit (None when it timed out or never launched), the exit code
# (negative for a death by signal, None when it timed out or never launched), stdout, stderr, whether it timed
# out, and why it could not launch (None when it launched).
Sample = collections.namedtuple("Sample", ("seconds", "code", "out", "err", "timed_out", "launch_error"))


class UsageError(Exception):
    """A command line this tool does not accept; exit 2."""


class MeasureError(Exception):
    """A measurement that cannot be made; exit 2."""


class Interrupted(BaseException):
    """SIGINT, SIGTERM or SIGHUP stopped a measurement (see _interrupt_handlers); exit 128 plus signum. A
    BaseException, so no `except Exception` on its way out can swallow it before the root is removed."""

    def __init__(self, signum):
        super().__init__(signum)
        self.signum = signum

    def __str__(self):
        return "interrupted by {}".format(_signal_name(self.signum))


def _signal_name(signum):
    try:
        return signal.Signals(signum).name
    except ValueError:
        return "signal {}".format(signum)


class _Interrupts:
    """What the interrupt handler works from: the hook child running now (registered by _sample from its
    launch until it is reaped), whether a signal may stop the run, and a signal held meanwhile. Armed, a signal
    kills that child's process group and raises Interrupted; not armed (the root being made or removed, a
    child being launched), it kills that group when one is registered and is held."""

    def __init__(self):
        self.armed = False
        self.held = None
        self.child = None

    def handle(self, signum, _frame):
        self.kill_child()
        if self.armed:
            self.armed = False
            raise Interrupted(signum)
        if self.held is None:
            self.held = signum

    def kill_child(self):
        """SIGKILL the registered child's process group while that child is not yet reaped, so the group id
        is still this tool's own child's (it leads a group of its own)."""
        child = self.child
        if child is not None and child.returncode is None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except OSError:
                pass

    def arm(self):
        """Let a signal stop the run from here on; a signal held meanwhile kills the registered child's group
        and raises Interrupted now."""
        if self.held is not None:
            signum, self.held = self.held, None
            self.kill_child()
            raise Interrupted(signum)
        self.armed = True


@contextlib.contextmanager
def _interrupt_handlers():
    """Install the interrupt handler for SIGINT, SIGTERM and SIGHUP for the block, restore the previous
    handlers when it ends, and yield the _Interrupts the handler works from. A signal ignored on entry (as
    under nohup) stays ignored. A signal still held when the block ends normally raises Interrupted once the
    previous handlers are back. MeasureError when the handlers cannot be installed (not the main thread)."""
    interrupts = _Interrupts()
    previous = []
    try:
        try:
            for signum in INTERRUPT_SIGNALS:
                if signal.getsignal(signum) != signal.SIG_IGN:
                    previous.append((signum, signal.signal(signum, interrupts.handle)))
        except ValueError as exc:
            raise MeasureError("cannot install the interrupt handlers: {}".format(exc))
        yield interrupts
    finally:
        for signum, handler in reversed(previous):
            signal.signal(signum, signal.SIG_DFL if handler is None else handler)
    if interrupts.held is not None:
        raise Interrupted(interrupts.held)


def parse_args(argv):
    """The measurement options, or UsageError. Each numeric value is plain decimal digits; each bound is
    checked at its own end (see INT_OPTIONS and CASE_TIMEOUT_MAX)."""
    opts = {"runs": DEFAULT_RUNS, "files": DEFAULT_FILES, "commits": DEFAULT_COMMITS,
            "case_timeout": DEFAULT_CASE_TIMEOUT, "hooks": [], "json": None}
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg not in INT_OPTIONS and arg not in ("--case-timeout", "--hook", "--json"):
            raise UsageError("unknown argument {!r}".format(arg))
        if index + 1 >= len(argv):
            raise UsageError("{} needs a value".format(arg))
        value = argv[index + 1]
        index += 2
        if arg in INT_OPTIONS:
            low, high = INT_OPTIONS[arg]
            if not INTEGER_RE.fullmatch(value) or not low <= int(value) <= high:
                raise UsageError("{} takes a whole number from {} to {}, not {!r}".format(arg, low, high, value))
            opts[arg[2:]] = int(value)
        elif arg == "--case-timeout":
            seconds = float(value) if DECIMAL_RE.fullmatch(value) else math.nan
            if not 0 < seconds <= CASE_TIMEOUT_MAX:
                raise UsageError("--case-timeout takes seconds greater than 0 and at most {:g}, not {!r}"
                                 .format(CASE_TIMEOUT_MAX, value))
            opts["case_timeout"] = seconds
        elif arg == "--hook":
            if not value:
                raise UsageError("--hook needs a hook id")
            opts["hooks"].append(value)
        else:
            opts["json"] = value
    return opts


def load_hooks(hooks_json, plugin_root):
    """The PreToolUse hooks of hooks_json in file order, each with its launch argv. MeasureError on an
    unreadable or malformed file, an entry this tool cannot launch, or a hook id that appears twice."""
    name = str(hooks_json)
    try:
        with open(hooks_json, "rb") as handle:
            data = json.loads(handle.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise MeasureError("cannot read {}: {}".format(name, exc))
    events = data.get("hooks") if isinstance(data, dict) else None
    entries = events.get(EVENT) if isinstance(events, dict) else None
    if not isinstance(entries, list) or not entries:
        raise MeasureError("{} has no {} list".format(name, EVENT))
    hooks = []
    for index, entry in enumerate(entries):
        where = "{} {}[{}]".format(name, EVENT, index)
        if not (isinstance(entry, dict) and isinstance(entry.get("matcher"), str)
                and isinstance(entry.get("hooks"), list) and entry["hooks"]):
            raise MeasureError("{}: an entry needs a matcher string and a non-empty hooks list".format(where))
        try:
            matcher = re.compile(entry["matcher"])
        except re.error as exc:
            raise MeasureError("{}: the matcher is not a regular expression: {}".format(where, exc))
        for hook in entry["hooks"]:
            args = hook.get("args") if isinstance(hook, dict) else None
            timeout = hook.get("timeout") if isinstance(hook, dict) else None
            if not (isinstance(hook, dict) and hook.get("type") == "command"
                    and isinstance(hook.get("command"), str) and hook["command"]
                    and isinstance(args, list) and len(args) >= 2
                    and all(isinstance(arg, str) and arg for arg in args)
                    and type(timeout) in (int, float) and timeout > 0):
                raise MeasureError("{}: a hook needs type command, a command, at least two string args (the "
                                   "script, then the hook id) and a positive timeout".format(where))
            argv = [hook["command"]] + [arg.replace(PLUGIN_ROOT_TOKEN, str(plugin_root)) for arg in args]
            if any(known.hook_id == args[-1] for known in hooks):
                raise MeasureError("{}: hook id {!r} appears twice".format(where, args[-1]))
            hooks.append(Hook(args[-1], matcher, argv, argv[-2], timeout))
    return hooks


def command_budget(script):
    """The dispatcher's Bash command budget, its one _COMMIT_MAX_COMMAND assignment, or MeasureError."""
    try:
        with open(script, "rb") as handle:
            text = handle.read().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise MeasureError("cannot read the dispatcher {}: {}".format(script, exc))
    found = BUDGET_RE.findall(text)
    if len(found) != 1:
        raise MeasureError("{} assigns _COMMIT_MAX_COMMAND {} time(s); the Bash case needs exactly one"
                           .format(script, len(found)))
    return int(found[0])


def bash_command(cap, repo):
    """A Bash command of exactly cap characters: at most SEGMENT_LIMIT read-only `git log` segments joined
    by ' && ', each naming quoted absolute paths under repo and padded with one plain word to its share."""
    if "'" in str(repo):
        raise MeasureError("the fixture path {} holds a single quote, which the quoted paths cannot carry"
                           .format(repo))
    count = max(1, min(SEGMENT_LIMIT, (cap + len(SEGMENT_JOIN)) // (SEGMENT_MIN + len(SEGMENT_JOIN))))
    total = cap - len(SEGMENT_JOIN) * (count - 1)
    if total < SEGMENT_MIN * count:
        raise MeasureError("a command budget of {} characters is below one {}-character segment"
                           .format(cap, SEGMENT_MIN))
    segments = []
    for number in range(count):
        size = total // count + (1 if number < total % count else 0)
        text = SEGMENT_HEAD
        path = 0
        while True:
            word = " '{}/dir {:02d}/file {:03d}.txt'".format(repo, number, path)
            if len(text) + len(word) + 2 > size:
                break
            text += word
            path += 1
        segments.append(text + " " + "p" * (size - len(text) - 1))
    return SEGMENT_JOIN.join(segments)


def write_content(size):
    """size bytes of WRITE_LINE lines (ASCII, so characters and bytes agree), followed by a run of x for the
    remainder when size is not a whole number of lines."""
    lines, rest = divmod(size, len(WRITE_LINE))
    return WRITE_LINE * lines + "x" * rest


def summarize(samples):
    """(min, median, max) of samples, each seconds or None for a timed-out sample. A timed-out sample is
    known only to exceed the case timeout, so it sorts after every completed one, and a statistic whose
    position lands on one is None (TIMED OUT), never a time; an even count's median is the mean of its two
    middle samples, None when either timed out."""
    done = sorted(sample for sample in samples if sample is not None)
    ordered = done + [None] * (len(samples) - len(done))
    if not ordered:
        return None, None, None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        median = ordered[middle]
    else:
        low, high = ordered[middle - 1], ordered[middle]
        median = None if low is None or high is None else (low + high) / 2
    return ordered[0], median, ordered[-1]


def child_env(root, plugin_root, project):
    """The whole environment of every hook child: PATH, a fixed locale, the pinned directories under root
    and the two platform variables. Nothing else from os.environ passes, and no GIT_* variable at all: the
    git-probing hooks read any GIT_* variable outside their cosmetic list as a repository-view override and
    take a fail-safe path instead of their probes, and the hooks drop every GIT_* variable before their own
    git calls, so a GIT_* pin here would change what is measured and keep no configuration out. The pinned
    HOME and XDG_CONFIG_HOME are what keep user configuration away from the hooks' git calls."""
    env = {"PATH": os.environ.get("PATH") or os.defpath, "LC_ALL": "C.UTF-8",
           "CLAUDE_PLUGIN_ROOT": str(plugin_root), "CLAUDE_PROJECT_DIR": str(project)}
    for variable, rel in PINNED_DIRS:
        env[variable] = str(Path(root, rel))
    return env


def git_env(env):
    """The environment of this tool's own git calls: the hook environment plus GIT_CONFIG_GLOBAL=os.devnull
    and GIT_CONFIG_NOSYSTEM=1, so building the fixture reads neither user nor system configuration."""
    return dict(env, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")


def _git(args, env, stdin=None):
    """Run one fixture git command; its stdout, or MeasureError. The three -c pairs in option position turn
    automatic maintenance off, so no detached gc or maintenance run outlives a call in the fixture."""
    try:
        done = subprocess.run(["git", "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false",
                               *args], input=stdin, capture_output=True, env=env, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise MeasureError("git {} could not run: {}".format(args[0], exc))
    if done.returncode != 0:
        raise MeasureError("git {} exited {}: {}".format(" ".join(args), done.returncode,
                                                         done.stderr.decode("utf-8", "replace").strip()[-400:]))
    return done.stdout.decode("utf-8", "replace")


def _commit_record(ref, number, path, content):
    message = "measure commit {}\n".format(number)
    return "commit {}\ncommitter {} <{}> {} +0000\ndata {}\n{}M 644 inline {}\ndata {}\n{}\n".format(
        ref, FIXTURE_NAME, FIXTURE_EMAIL, FIXTURE_TIME + number, len(message), message, path, len(content),
        content)


def build_fixture(root, files, commits, env):
    """Build the git fixture under root and return the repository path: main with `commits` commits, the
    orphan branch (one root commit of its own), HEAD on the feature branch at main's tip, a local bare
    origin holding main with origin/HEAD set, and `files` untracked files in the work tree."""
    genv = dict(git_env(env), GIT_AUTHOR_NAME=FIXTURE_NAME, GIT_AUTHOR_EMAIL=FIXTURE_EMAIL,
                GIT_COMMITTER_NAME=FIXTURE_NAME, GIT_COMMITTER_EMAIL=FIXTURE_EMAIL)
    repo = Path(root, "repo")
    origin = Path(root, "origin.git")
    history = "".join(_commit_record("refs/heads/main", number, "tracked.txt", "measure {}\n".format(number))
                      for number in range(1, commits + 1))
    history += _commit_record("refs/heads/" + ORPHAN, commits + 1, "orphan.txt", "orphan\n")
    _git(["init", "-q", "-b", "main", str(repo)], genv)
    _git(["-C", str(repo), "fast-import", "--quiet"], genv, stdin=history.encode("utf-8"))
    _git(["-C", str(repo), "checkout", "-q", "-f", "-B", FEATURE, "main"], genv)
    _git(["init", "-q", "--bare", str(origin)], genv)
    _git(["-C", str(repo), "remote", "add", "origin", str(origin)], genv)
    _git(["-C", str(repo), "push", "-q", "origin", "main"], genv)
    _git(["-C", str(repo), "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"], genv)
    for number in range(files):
        folder = repo / "untracked" / "d{:03d}".format(number // FILES_PER_DIR)
        if number % FILES_PER_DIR == 0:
            folder.mkdir(parents=True)
        with open(folder / "f{:05d}.txt".format(number), "w", encoding="utf-8") as handle:
            handle.write("untracked {}\n".format(number))
    return repo


def _sample(argv, payload, env, cwd, timeout, interrupts=None):
    """One launch, as a Sample. A child still running at timeout has its whole process group killed and
    reaped first; a child that cannot be launched is a Sample with its launch_error. The child leads a process
    group of its own, so the group id is its pid and only this tool's own child is signalled. Given
    interrupts (an _Interrupts), the child is registered with it from launch to reaping, so an interrupt
    kills the child's group; a signal that arrives while the child is being launched is held until the child
    is registered."""
    armed = interrupts is not None and interrupts.armed
    if armed:
        interrupts.armed = False
    start = time.perf_counter()
    try:
        child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 cwd=cwd, env=env, process_group=0)
    except (OSError, ValueError) as exc:
        if armed:
            interrupts.arm()
        return Sample(None, None, b"", b"", False, "{}: {}".format(type(exc).__name__, exc))
    if interrupts is not None:
        interrupts.child = child
    try:
        with child:
            if armed:
                interrupts.arm()
            try:
                out, err = child.communicate(payload, timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    child.communicate(timeout=REAP_SECONDS)
                except subprocess.TimeoutExpired:
                    child.kill()
                return Sample(None, None, b"", b"", True, None)
            elapsed = time.perf_counter() - start
    finally:
        if interrupts is not None:
            interrupts.child = None
    return Sample(elapsed, child.returncode, out, err, False, None)


def _decision(code, out):
    """The hook decision a completed sample's exit code and stdout express (allow, deny, ask or warn), or
    None when they express none (see SAMPLES in the module docstring)."""
    if code != 0:
        return None
    if not out.strip():
        return "allow"
    try:
        data = json.loads(out.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    specific = data.get("hookSpecificOutput")
    if isinstance(specific, dict) and "permissionDecision" in specific:
        decision = specific["permissionDecision"]
        return decision if decision in ("allow", "deny", "ask") else None
    if "decision" in data:
        legacy = data["decision"]
        return {"approve": "allow", "block": "deny"}.get(legacy) if isinstance(legacy, str) else None
    if isinstance(data.get("systemMessage"), str) and data["systemMessage"]:
        return "warn"
    return None


def _outcome(sample, startup=False):
    """(the sample's label, True when it is a failed sample), as SAMPLES in the module docstring sets out: a
    timed-out sample is TIMED OUT and not failed; the startup case's sample is valid only as the dispatcher's
    rejection of its payload; any other completed sample is valid only with a hook decision."""
    if sample.timed_out:
        return TIMED_OUT, False
    if sample.launch_error is not None:
        return "launch failed ({})".format(sample.launch_error), True
    if sample.code < 0:
        return "killed by {}".format(_signal_name(-sample.code)), True
    if startup and sample.code == 2 and not sample.out.strip() and STARTUP_REJECTION in sample.err:
        return "exit 2 rejected", False
    decision = _decision(sample.code, sample.out)
    if decision is None:
        return "exit {}{}".format(sample.code, " no decision" if sample.code == 0 else ""), True
    # The startup payload is built to be rejected, so a decision there means the sample took another path.
    return "exit 0 " + decision, startup


def _loadavg():
    try:
        return [round(value, 2) for value in os.getloadavg()]
    except (OSError, AttributeError):
        return None


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _interpreters(hooks, env, cwd, interrupts=None):
    """For each distinct hook command: where PATH resolves it and the Python version it reports. MeasureError
    when a command is not on PATH or its version probe fails, since the report would then name no version."""
    found = {}
    for hook in hooks:
        command = hook.argv[0]
        if command in found:
            continue
        path = shutil.which(command, path=env["PATH"])
        if path is None:
            raise MeasureError("the hook command {!r} is not on PATH".format(command))
        sample = _sample([command, "-I", "-c", "import sys; print(sys.version)"], b"", env, cwd, PROBE_TIMEOUT,
                         interrupts)
        version = sample.out.decode("utf-8", "replace").strip() if sample.code == 0 else ""
        if not version:
            why = _outcome(sample)[0] if sample.code != 0 else "exit 0 with no version on stdout"
            raise MeasureError("the version probe of the hook command {} ({}) failed ({}), so the report would "
                               "name no interpreter version".format(command, path, why))
        found[command] = {"path": path, "version": version}
    return found


def _size_name(size):
    for unit, name in ((MIB, "mib"), (1024, "kib")):
        if size >= unit and size % unit == 0:
            return "{}{}".format(size // unit, name)
    return "{}b".format(size)


def _event(tool, tool_input, repo, transcript):
    return json.dumps({"session_id": "aiqt-hook-latency", "transcript_path": str(transcript), "cwd": str(repo),
                       "permission_mode": "default", "hook_event_name": EVENT, "tool_name": tool,
                       "tool_input": tool_input}).encode("utf-8")


def build_cases(first, repo, cap, write_sizes, files, commits):
    """Every case, in run order (see the module docstring)."""
    cases = [Case("startup", None, None, first, STARTUP_PAYLOAD,
                  "{} given the payload {} (a JSON array, not an object): the dispatcher rejects it before any "
                  "handler runs and exits 2".format(first, STARTUP_PAYLOAD.decode("ascii"))),
             Case("bash-" + _size_name(cap), "Bash",
                  {"command": bash_command(cap, repo), "description": "measure hook latency"}, None, None,
                  "Bash, a {}-character command (the dispatcher's command budget): read-only git log segments "
                  "joined by &&, quoted absolute paths".format(cap))]
    for size in write_sizes:
        cases.append(Case("write-" + _size_name(size), "Write",
                          {"file_path": str(repo / "measure" / "latency-probe.txt"), "content": write_content(size)},
                          None, None, "Write, {} bytes of content in lines {}".format(size, WRITE_LINE.strip())))
    cases.append(Case("ask-user-question", "AskUserQuestion",
                      {"questions": [{"question": "Measure hook latency now?", "header": "Measure",
                                      "multiSelect": False,
                                      "options": [{"label": "Yes", "description": "Run it"},
                                                  {"label": "No", "description": "Skip it"}]}]},
                      None, None, "AskUserQuestion, one short question (a small input, not a worst case)"))
    cases.append(Case("schedule-wakeup", "ScheduleWakeup",
                      {"delaySeconds": 60, "prompt": "continue the measurement"}, None, None,
                      "ScheduleWakeup, one short wakeup (a small input, not a worst case)"))
    notes = {"git-discard": "in the fixture with {} untracked file(s)".format(files),
             "branch-root": "an orphan start with no merge base with main ({} commit(s))".format(commits),
             "protected-line": "HEAD on the non-protected branch {}".format(FEATURE)}
    for name, target, command in GIT_CASES:
        cases.append(Case(name, "Bash", {"command": command, "description": "measure hook latency"}, target, None,
                          "Bash {!r}, {}".format(command, notes[name])))
    return cases


def _matching(case, hooks):
    """The hooks a case runs: the startup case its one target; any other case every hook whose matcher matches
    its tool as a whole string."""
    if case.tool is None:
        return [hook for hook in hooks if hook.hook_id == case.target]
    return [hook for hook in hooks if hook.matcher.fullmatch(case.tool)]


def coverage_problems(hooks, cap, write_sizes, files, commits):
    """What would leave hooks.json not fully measured: each hook that no case calling a tool runs, and each
    such case that no hook matches (the startup case does not count; it times only the dispatcher's start).
    The cases are built here for their tools only, under a path that is never created, and one Write size
    stands for every Write case, since each calls the same tool."""
    cases = build_cases(hooks[0].hook_id, Path(ROOT_PREFIX + "unbuilt", "repo"), cap, write_sizes[:1], files,
                        commits)
    tool_cases = [case for case in cases if case.tool is not None]
    measured = {hook.hook_id for case in tool_cases for hook in _matching(case, hooks)}
    problems = ["hook id {!r} (matcher {!r}) is run by no case".format(hook.hook_id, hook.matcher.pattern)
                for hook in hooks if hook.hook_id not in measured]
    problems += ["the {} case calls {}, which no {} hook matches".format(case.name, case.tool, EVENT)
                 for case in tool_cases if not _matching(case, hooks)]
    return problems


def measure(hooks_json, plugin_root, runs=DEFAULT_RUNS, files=DEFAULT_FILES, commits=DEFAULT_COMMITS,
            case_timeout=DEFAULT_CASE_TIMEOUT, selected=(), write_sizes=WRITE_SIZES, progress=None):
    """Run every case and return the figures and conditions. MeasureError when it cannot measure; Interrupted
    when SIGINT, SIGTERM or SIGHUP stops it. Every hook id it needs, and the coverage of hooks.json by the
    cases, are checked before the fixture root exists. The root is removed at the normal end, on an error and
    on those signals; a root left behind is added as a note to the error or interruption that stopped the run
    first, never put in its place."""
    hooks = load_hooks(hooks_json, plugin_root)
    known = [hook.hook_id for hook in hooks]
    unknown = [hook_id for hook_id in selected if hook_id not in known]
    if unknown:
        raise MeasureError("hook id(s) not in {} as {} hooks: {}".format(hooks_json, EVENT, ", ".join(unknown)))
    for name, target, _command in GIT_CASES:
        hook = next((hook for hook in hooks if hook.hook_id == target), None)
        if hook is None or not hook.matcher.fullmatch("Bash"):
            raise MeasureError("hook id {!r}, which the {} case measures, is not a {} hook for Bash in {}"
                               .format(target, name, EVENT, hooks_json))
    active = [hook for hook in hooks if not selected or hook.hook_id in selected]
    if not active:
        raise MeasureError("no {} hook in {} is selected".format(EVENT, hooks_json))
    cap = command_budget(next(hook for hook in hooks if hook.matcher.fullmatch("Bash")).script)
    problems = coverage_problems(hooks, cap, write_sizes, files, commits)
    if problems:
        raise MeasureError("the cases do not measure every {} hook in {}: {}".format(EVENT, hooks_json,
                                                                                    "; ".join(problems)))
    with _interrupt_handlers() as interrupts:
        # Not armed yet, so a signal while the root is made is held, and arm() raises it inside the try.
        root = Path(tempfile.mkdtemp(prefix=ROOT_PREFIX))
        failure = None
        try:
            interrupts.arm()
            result = _measure_in(root, hooks_json, plugin_root, active, runs, files, commits, case_timeout, cap,
                                 write_sizes, progress, interrupts)
        except BaseException as exc:
            interrupts.armed = False
            failure = exc
        # Disarmed before the removal: a signal during it is held, so it cannot cut the removal short.
        interrupts.armed = False
        leftover = _remove_root(root)
        held, interrupts.held = interrupts.held, None
        if held is not None and failure is None:
            failure = Interrupted(held)
        elif held is not None:
            failure.add_note("{} also arrived while the run was stopping".format(_signal_name(held)))
        if failure is not None:
            if leftover is not None:
                failure.add_note(leftover)
            raise failure
        if leftover is not None:
            raise MeasureError(leftover)
    return result


def _remove_root(root):
    """Remove the fixture root; None when it is gone, else what was left."""
    problems = []

    def note(_function, path, exc):
        if not isinstance(exc, FileNotFoundError):
            problems.append("{}: {}".format(path, exc))
    shutil.rmtree(root, onexc=note)
    if problems or os.path.lexists(root):
        return "the fixture root {} could not be removed: {}".format(
            root, "; ".join(problems[:3]) or "it is still present")
    return None


def _measure_in(root, hooks_json, plugin_root, active, runs, files, commits, case_timeout, cap, write_sizes,
                progress, interrupts):
    repo = root / "repo"
    env = child_env(root, plugin_root, repo)
    for _variable, rel in PINNED_DIRS:
        Path(root, rel).mkdir(parents=True)
    started, load_start = _utc_now(), _loadavg()
    interpreters = _interpreters(active, env, root, interrupts)
    git_version = _git(["--version"], git_env(env)).strip()
    if progress is not None:
        progress("building the git fixture: {} commit(s), {} untracked file(s)".format(commits, files))
    build_fixture(root, files, commits, env)
    transcript = root / "transcript.jsonl"
    transcript.write_bytes(b"")
    rows, ran, skipped = [], [], []
    for case in build_cases(active[0].hook_id, repo, cap, write_sizes, files, commits):
        matching = _matching(case, active)
        if not matching:
            skipped.append(case.name)
            continue
        ran.append({"case": case.name, "tool": case.tool, "note": case.note})
        payload = case.payload if case.payload is not None else _event(case.tool, case.tool_input, repo,
                                                                         transcript)
        if progress is not None:
            progress("measuring {}: {} hook(s) x {} run(s)".format(case.name, len(matching), runs))
        # samples holds the valid samples' seconds and None for each timed-out one; a failed sample is only
        # counted in failed_samples and labelled in outcomes, never timed.
        case_rows = [{"case": case.name, "hook_id": hook.hook_id, "platform_timeout_seconds": hook.timeout,
                      "load_average_at_case_start": _loadavg(), "samples": [], "failed_samples": 0,
                      "outcomes": {}}
                     for hook in matching]
        for _run in range(runs):
            for hook, row in zip(matching, case_rows):
                sample = _sample(hook.argv, payload, env, repo, case_timeout, interrupts)
                label, failed = _outcome(sample, startup=case.tool is None)
                if failed:
                    row["failed_samples"] += 1
                else:
                    row["samples"].append(sample.seconds)
                row["outcomes"][label] = row["outcomes"].get(label, 0) + 1
        for row in case_rows:
            row["min"], row["median"], row["max"] = (summarize(row["samples"]) if row["samples"]
                                                     else (NO_VALID_SAMPLE,) * 3)
        rows.extend(case_rows)
    hooks_name = Path(hooks_json)
    if hooks_name.is_relative_to(ROOT):
        hooks_name = hooks_name.relative_to(ROOT)
    conditions = {
        "started_utc": started, "finished_utc": _utc_now(),
        "load_average_start": load_start, "load_average_end": _loadavg(),
        "cpu_count": os.cpu_count(), "platform": platform.platform(),
        "harness_python": sys.version, "harness_executable": sys.executable,
        "hook_interpreters": interpreters, "git_version": git_version,
        "hooks_json": hooks_name.as_posix(), "runs": runs, "untracked_files": files, "commits": commits,
        "case_timeout_seconds": case_timeout, "command_budget": cap, "write_sizes": list(write_sizes),
        "order": "one child at a time; each run goes once through the hooks of the case",
    }
    return {"format": "aiqt-hook-latency/1", "conditions": conditions, "cases": ran, "rows": rows,
            "skipped_cases": skipped}


def _seconds(value):
    """A statistic as the table shows it: seconds, TIMED OUT (None) or NO VALID SAMPLE (that string)."""
    if value is None:
        return TIMED_OUT
    return value if isinstance(value, str) else "{:.3f}".format(value)


def render(result):
    """The plain text report: the conditions, the cases, then one table row per case and hook id."""
    conditions = result["conditions"]

    def load(values):
        return "unavailable" if values is None else " ".join("{:.2f}".format(value) for value in values)

    timeouts = sorted({row["platform_timeout_seconds"] for row in result["rows"]})
    lines = ["hook latency (a measurement, not a gate): wall seconds per hook child, {} run(s) per case, one "
             "child at a time".format(conditions["runs"]),
             "hooks.json: {} (platform timeout per hook there: {} s)".format(
                 conditions["hooks_json"], ", ".join("{:g}".format(value) for value in timeouts) or "none"),
             "host: {} CPU(s), {}; load average (1, 5, 15 min) at start {}, at end {}".format(
                 conditions["cpu_count"], conditions["platform"], load(conditions["load_average_start"]),
                 load(conditions["load_average_end"])),
             "started {}, finished {}".format(conditions["started_utc"], conditions["finished_utc"]),
             "harness python: {}".format(conditions["harness_python"])]
    for command, found in sorted(conditions["hook_interpreters"].items()):
        lines.append("hook command {}: {} ({})".format(command, found["path"], found["version"]))
    lines.append("git: {}".format(conditions["git_version"]))
    lines.append("fixture: {} untracked file(s), {} commit(s) on main; case timeout {:g} s".format(
        conditions["untracked_files"], conditions["commits"], conditions["case_timeout_seconds"]))
    lines.append("samples: a failed sample (a nonzero exit, a death by signal, a launch failure, or output that "
                 "names no decision) is counted under failed and contributes no time; {} is known only to exceed "
                 "the case timeout; {} means the row has no valid or timed-out sample".format(TIMED_OUT,
                                                                                         NO_VALID_SAMPLE))
    lines.append("cases:")
    width = max([len(case["case"]) for case in result["cases"]] + [4])
    lines.extend("  {}  {}".format(case["case"].ljust(width), case["note"]) for case in result["cases"])
    if result["skipped_cases"]:
        lines.append("skipped (no selected hook matches): {}".format(", ".join(result["skipped_cases"])))
    header = ("case", "hook id", "runs", "failed", "min s", "median s", "max s", "outcomes")
    table = [header]
    for row in result["rows"]:
        outcomes = ", ".join("{} x{}".format(label, count) for label, count in sorted(row["outcomes"].items()))
        table.append((row["case"], row["hook_id"], str(len(row["samples"]) + row["failed_samples"]),
                      str(row["failed_samples"]), _seconds(row["min"]), _seconds(row["median"]),
                      _seconds(row["max"]), outcomes))
    widths = [max(len(line[column]) for line in table) for column in range(len(header))]
    lines.append("")
    for line in table:
        cells = [line[0].ljust(widths[0]), line[1].ljust(widths[1])]
        cells += [cell.rjust(width) for cell, width in zip(line[2:7], widths[2:7])]
        lines.append("  ".join(cells + [line[7]]).rstrip())
    return "\n".join(lines) + "\n"


def _cause(exc):
    """exc's message, then each note added to it (a fixture root left behind, a second signal)."""
    return "; ".join([str(exc)] + [str(note) for note in getattr(exc, "__notes__", ())])


def _one_line(text):
    """text as part of a one-line diagnostic: a backslash and every character str.isprintable() rejects (each
    Unicode Other or Separator character but the space, a newline and a carriage return among them) written as
    Python's repr writes it."""
    return "".join(char if char.isprintable() and char != "\\" else repr(char)[1:-1] for char in text)


def _unevaluated(rows):
    """Each row, as case/hook id (each written by _one_line), that holds a failed sample or reads NO VALID
    SAMPLE: a run with one cannot be evaluated (exit 3). A timed-out sample is not a failed one, so it never
    puts a row here."""
    return ["{}/{}".format(_one_line(row["case"]), _one_line(row["hook_id"])) for row in rows
            if row["failed_samples"] or row["min"] == NO_VALID_SAMPLE]


def run_cli(argv, hooks_json, plugin_root, write_sizes=WRITE_SIZES, out=None, err=None):
    """The measurement command line: print the report, write --json, and return the exit code, 3 (cannot
    evaluate) after the report when a row holds a failed sample (see _unevaluated)."""
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    try:
        opts = parse_args(argv)
    except UsageError as exc:
        print("error: {}\n{}".format(exc, USAGE), file=err)
        return 2
    if os.name != "posix":
        print("error: measure_hook_latency.py measures on POSIX hosts only (a timed-out child's process group "
              "is killed)", file=err)
        return 2
    target = None
    if opts["json"] is not None:
        target = Path(opts["json"]).absolute()
        if os.path.lexists(target):
            print("error: --json {} already exists; it is never overwritten".format(target), file=err)
            return 2
        if not target.parent.is_dir():
            print("error: --json {}: its directory does not exist".format(target), file=err)
            return 2
    try:
        result = measure(hooks_json, plugin_root, runs=opts["runs"], files=opts["files"], commits=opts["commits"],
                         case_timeout=opts["case_timeout"], selected=opts["hooks"], write_sizes=write_sizes,
                         progress=lambda text: print(text, file=err, flush=True))
    except Interrupted as exc:
        print("error: {} (the measurement stopped; nothing was reported)".format(_cause(exc)), file=err)
        return 128 + exc.signum
    except MeasureError as exc:
        print("error: {}".format(_cause(exc)), file=err)
        return 2
    out.write(render(result))
    if target is not None:
        try:
            with open(target, "x", encoding="utf-8") as handle:
                handle.write(json.dumps(result, indent=2) + "\n")
        except OSError as exc:
            print("error: cannot write --json {}: {}".format(target, exc), file=err)
            return 2
        print("figures written to {}".format(target), file=out)
    unevaluated = _unevaluated(result["rows"])
    if unevaluated:
        print("cannot evaluate: {} row(s) hold a failed sample or read {} (case/hook id): {}".format(
            len(unevaluated), NO_VALID_SAMPLE, ", ".join(unevaluated)), file=err)
        return 3
    return 0


# --- self-test -------------------------------------------------------------------------------------------
# The harness's own checks on stub hooks and tiny inputs, each id registered under hook-latency-selftest in
# tools/selftest_checks.toml. The stubs reject a payload that is not a JSON object as the dispatcher does
# (the rejection on stderr, exit 2), except stub_lax.py, which exits 0 with nothing on stdout (an allow, so its
# startup sample is a failed one). Otherwise they read their payload and exit 0, except: stub_slow.py sleeps
# 30 s as branch_root (the timeout case and the interrupt checks; it first writes its process group id and
# working directory to held-branch_root in the plugin directory) and 0.2 s as protected_line (a sample inside
# the timeout); stub_fail.py kills itself with SIGKILL as protected_line, as branch_root exits 1 on its first,
# third, ... launch and naps MIXED_NAP before exiting 0 on the others (a row mixing failed and valid samples),
# and exits 1 as any other hook id (git_discard and CONTROL_ID among them). Two real dispatcher launches confirm
# the premises of two cases (the startup payload is rejected, the Write line scans clean); neither runs git.

STUB_SOURCE = '''"""A stub dispatcher for the hook-latency self-test (see the self-test comment in the harness)."""
import json
import os
import signal
import sys
import time

_COMMIT_MAX_COMMAND = 512

mode = sys.argv[1] if len(sys.argv) > 1 else ""
name = os.path.basename(__file__)
plugin = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
try:
    payload = json.loads(sys.stdin.read())
except ValueError:
    payload = None
if not isinstance(payload, dict):
    if name == "stub_lax.py":
        sys.exit(0)
    sys.stderr.write("stub: unreadable hook payload (payload is not a JSON object); failing closed\\n")
    sys.exit(2)
if name == "stub_slow.py" and mode == "branch_root":
    held = os.path.join(plugin, "held-branch_root")
    with open(held + ".part", "w", encoding="utf-8") as handle:
        handle.write("{} {}\\n".format(os.getpgrp(), os.getcwd()))
    os.replace(held + ".part", held)
    time.sleep(30)
if name == "stub_slow.py" and mode == "protected_line":
    time.sleep(0.2)
if name == "stub_fail.py" and mode not in ("branch_root", "protected_line"):
    sys.exit(1)
if name == "stub_fail.py" and mode == "protected_line":
    os.kill(os.getpid(), signal.SIGKILL)
if name == "stub_fail.py" and mode == "branch_root":
    count = os.path.join(plugin, "count-branch_root")
    try:
        with open(count, encoding="utf-8") as handle:
            launches = int(handle.read())
    except FileNotFoundError:
        launches = 0
    with open(count, "w", encoding="utf-8") as handle:
        handle.write(str(launches + 1))
    if launches % 2 == 0:
        sys.exit(1)
    time.sleep(0.25)
if mode == "env_dump":
    sys.stdout.write(json.dumps(dict(os.environ)))
'''
STUB_HOOKS = (("Bash", "git_discard"), ("Bash", "branch_root"), ("Bash", "protected_line"),
              ("Write", "secrets_shift_left"), ("AskUserQuestion", "orch_ask_guard"),
              ("ScheduleWakeup|CronCreate", "orch_yield_tool"))
# A hook id holding a newline and a carriage return: load_hooks takes any non-empty string as a hook id, and
# the one cannot-evaluate line must stay one line when it names this one.
CONTROL_ID = "line\nbreak\rhook"
# Each variant's script and the hook ids that run it; every other hook runs stub_fast.py.
STUB_VARIANTS = {"fast": ("stub_fast.py", ()), "slow": ("stub_slow.py", ("branch_root", "protected_line")),
                 "fail": ("stub_fail.py", ("git_discard", "branch_root", "protected_line")),
                 "lax": ("stub_lax.py", ("git_discard",)), "control": ("stub_fail.py", (CONTROL_ID,))}
MIXED_NAP = 0.25                     # stub_fail.py's nap as branch_root on a valid sample (in STUB_SOURCE)
SELF_TEST_WRITE_SIZES = (1024, 2048, 4096)
SELF_TEST_CASES = ["startup", "bash-512b", "write-1kib", "write-2kib", "write-4kib", "ask-user-question",
                   "schedule-wakeup", "git-discard", "branch-root", "protected-line"]
SLOW_TIMEOUT = 1.5
# The interrupt checks' runner: a child process that runs one stub measurement through run_cli.
INTERRUPT_RUNNER = '''"""Run one stub measurement for the hook-latency self-test's interrupt checks. argv: the tools
directory, the stub hooks.json, its plugin root, then the measurement's own arguments."""
import signal
import sys
from pathlib import Path

# A foreground process's dispositions, whatever this one inherited: a background launch can start with SIGINT
# ignored, and the measurement leaves an ignored signal ignored.
signal.signal(signal.SIGINT, signal.default_int_handler)
signal.signal(signal.SIGTERM, signal.SIG_DFL)
signal.signal(signal.SIGHUP, signal.SIG_DFL)
sys.path.insert(0, sys.argv[1])
import measure_hook_latency  # noqa: E402

sys.exit(measure_hook_latency.run_cli(sys.argv[4:], Path(sys.argv[2]), Path(sys.argv[3])))
'''
INTERRUPT_WAIT = 20.0                # seconds for the held hook to start, and for the run to end once signalled
GROUP_WAIT = 5.0                     # seconds for the hook child's process group to be gone


def _stub_plugin(base, name, variant="fast", drop=None, extra=()):
    """A plugin tree whose hooks.json runs the stub dispatcher for STUB_HOOKS (less drop) and then the extra
    (matcher, hook id) pairs, the variant's script for the hook ids it names: (hooks.json, root)."""
    plugin = Path(base, name)
    scripts = plugin / "hooks" / "scripts"
    scripts.mkdir(parents=True)
    for script in ("stub_fast.py", "stub_slow.py", "stub_fail.py", "stub_lax.py"):
        (scripts / script).write_text(STUB_SOURCE, encoding="utf-8")
    special, special_ids = STUB_VARIANTS[variant]
    entries = []
    for matcher, hook_id in STUB_HOOKS + tuple(extra):
        if hook_id == drop:
            continue
        script = special if hook_id in special_ids else "stub_fast.py"
        entries.append({"matcher": matcher, "hooks": [{"type": "command", "command": "python3", "timeout": 10,
                                                        "args": ["-I", PLUGIN_ROOT_TOKEN + "/hooks/scripts/" + script,
                                                                 hook_id]}]})
    hooks_json = plugin / "hooks" / "hooks.json"
    hooks_json.write_text(json.dumps({"hooks": {EVENT: entries}}, indent=2), encoding="utf-8")
    return hooks_json, plugin


@contextlib.contextmanager
def _patched(owner, name, replacement):
    original = getattr(owner, name)
    setattr(owner, name, replacement)
    try:
        yield
    finally:
        setattr(owner, name, original)


@contextlib.contextmanager
def _recording_roots():
    """Record every directory tempfile.mkdtemp makes meanwhile, so a check can see the fixture root."""
    made = []
    original = tempfile.mkdtemp

    def recording(*args, **kwargs):
        path = original(*args, **kwargs)
        made.append(path)
        return path
    with _patched(tempfile, "mkdtemp", recording):
        yield made


@contextlib.contextmanager
def _planted_environ(values):
    saved = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _try_measure(*args, **kwargs):
    """measure(), with any exception kept as the failing run's detail, so one broken run fails its own
    checks and the rest of the self-test still runs."""
    try:
        return measure(*args, **kwargs), None
    except MeasureError as exc:
        return None, str(exc)
    except Exception as exc:
        return None, "raised {}: {}".format(type(exc).__name__, exc)


def _try_cli(*args, **kwargs):
    """run_cli()'s exit code, or the exception it raised as a string (never an exit code)."""
    try:
        return run_cli(*args, **kwargs)
    except Exception as exc:
        return "raised {}: {}".format(type(exc).__name__, exc)


def _bounds_vectors():
    """Each option's ends: just outside refused, exactly at and just inside accepted."""
    table = []
    for option, (low, high) in INT_OPTIONS.items():
        table += [([option, str(low - 1)], False), ([option, str(low)], True), ([option, str(low + 1)], True),
                  ([option, str(high - 1)], True), ([option, str(high)], True), ([option, str(high + 1)], False)]
    table += [(["--case-timeout", "0"], False), (["--case-timeout", "0.0"], False),
              (["--case-timeout", "0.001"], True), (["--case-timeout", "3599.999"], True),
              (["--case-timeout", "3600"], True), (["--case-timeout", "3600.001"], False)]
    wrong = []
    for argv, accepted in table:
        try:
            parse_args(argv)
            got = True
        except UsageError:
            got = False
        if got != accepted:
            wrong.append((argv, got))
    return not wrong, wrong


def _unknown_vectors():
    refused = (["--nonesuch"], ["--runs"], ["--runs", "five"], ["--runs", "-1"], ["--runs", "+2"],
               ["--case-timeout", "nan"], ["--case-timeout", "1e3"], ["--case-timeout", "inf"], ["--hook", ""],
               ["--self-test", "--runs", "2"], ["--execution-report", "relative/report.json"],
               ["--runs", "2", "--json"])
    wrong = []
    for argv in refused:
        try:
            parse_args(argv)
            wrong.append(argv)
        except UsageError:
            pass
    err = io.StringIO()
    code = run_cli(["--execution-report", "relative/report.json"], Path(os.devnull), Path(os.devnull),
                   out=io.StringIO(), err=err)
    if code != 2 or "unknown argument" not in err.getvalue():
        wrong.append(("run_cli", code, err.getvalue()[:200]))
    return not wrong, wrong


def _summary_vectors():
    table = (([1.0], (1.0, 1.0, 1.0)), ([None], (None, None, None)), ([2.0, 1.0], (1.0, 1.5, 2.0)),
             ([1.0, None], (1.0, None, None)), ([None, None], (None, None, None)),
             ([3.0, 1.0, 2.0], (1.0, 2.0, 3.0)), ([1.0, 2.0, None], (1.0, 2.0, None)),
             ([1.0, None, None], (1.0, None, None)), ([4.0, 1.0, 3.0, 2.0], (1.0, 2.5, 4.0)),
             ([1.0, 2.0, 3.0, None], (1.0, 2.5, None)), ([1.0, 2.0, None, None], (1.0, None, None)),
             ([], (None, None, None)))
    wrong = [(samples, summarize(samples)) for samples, want in table if summarize(samples) != want]
    return not wrong, wrong


def _outcome_vectors():
    """Each outcome class with its label and whether it is a failed sample (see SAMPLES in the module
    docstring), the startup case's included."""
    def done(code, out=b"", err=b""):
        return Sample(0.01, code, out, err, False, None)
    deny = b'{"hookSpecificOutput": {"permissionDecision": "deny"}, "systemMessage": "blocked"}'
    table = (
        (done(0), False, ("exit 0 allow", False)),
        (done(0, b'{"hookSpecificOutput": {"permissionDecision": "allow"}}'), False, ("exit 0 allow", False)),
        (done(0, deny), False, ("exit 0 deny", False)),
        (done(0, b'{"hookSpecificOutput": {"permissionDecision": "ask"}}'), False, ("exit 0 ask", False)),
        (done(0, b'{"systemMessage": "a note"}'), False, ("exit 0 warn", False)),
        (done(0, b'{"decision": "approve"}'), False, ("exit 0 allow", False)),
        (done(0, b'{"decision": "block"}'), False, ("exit 0 deny", False)),
        (done(0, b'{"hookSpecificOutput": {"permissionDecision": "maybe"}}'), False, ("exit 0 no decision", True)),
        (done(0, b'{"decision": ["block"]}'), False, ("exit 0 no decision", True)),
        (done(0, b"{}"), False, ("exit 0 no decision", True)),
        (done(0, b"not json"), False, ("exit 0 no decision", True)),
        (done(1, deny), False, ("exit 1", True)),
        (done(2, b"", b"aiqt_hooks: handler failed; failing closed"), False, ("exit 2", True)),
        (done(2, b"", STARTUP_REJECTION), False, ("exit 2", True)),
        (done(-signal.SIGKILL), False, ("killed by SIGKILL", True)),
        (done(-signal.SIGHUP), False, ("killed by SIGHUP", True)),
        (Sample(None, None, b"", b"", True, None), False, (TIMED_OUT, False)),
        (Sample(None, None, b"", b"", False, "OSError: x"), False, ("launch failed (OSError: x)", True)),
        (done(2, b"", b"aiqt_hooks: (" + STARTUP_REJECTION + b")"), True, ("exit 2 rejected", False)),
        (done(2, b"{}", STARTUP_REJECTION), True, ("exit 2", True)),
        (done(2, b"", b"python3: can't open file"), True, ("exit 2", True)),
        (done(0), True, ("exit 0 allow", True)),
    )
    wrong = [(sample, startup, _outcome(sample, startup), want) for sample, startup, want in table
             if _outcome(sample, startup) != want]
    return not wrong, wrong


def _bash_vectors():
    wrong = []
    repo = "/fixture root/repo"
    for cap in (SEGMENT_MIN, 512, 65535, 65536, 65537):
        command = bash_command(cap, repo)
        segments = command.split(SEGMENT_JOIN)
        try:
            shlex.split(command)
            quoted = True
        except ValueError:
            quoted = False
        if not (len(command) == cap and command.isascii() and quoted and len(segments) <= SEGMENT_LIMIT
                and all(segment.startswith(SEGMENT_HEAD) for segment in segments)):
            wrong.append((cap, len(command), len(segments), quoted))
    if len(bash_command(65536, repo).split(SEGMENT_JOIN)) != SEGMENT_LIMIT:
        wrong.append("a 65536-character command is not cut into the full segment count")
    for cap, path in ((SEGMENT_MIN - 1, repo), (512, "/fixture'root/repo")):
        try:
            bash_command(cap, path)
            wrong.append((cap, path, "accepted"))
        except MeasureError:
            pass
    return not wrong, wrong


def _write_vectors():
    line = len(WRITE_LINE)
    wrong = []
    for size in (1, line - 1, line, line + 1) + WRITE_SIZES:
        content = write_content(size)
        if len(content.encode("utf-8")) != size or (size >= line and not content.startswith(WRITE_LINE)):
            wrong.append(size)
    return not wrong, wrong


def _real_launch(base, hook_id, payload):
    """Launch the real dispatcher once as hooks.json does, for a hook that runs no git on this payload."""
    hooks = load_hooks(ROOT / HOOKS_JSON_REL, ROOT / PLUGIN_REL)
    hook = next(hook for hook in hooks if hook.hook_id == hook_id)
    root = Path(base, "real-" + hook_id)
    for _variable, rel in PINNED_DIRS:
        Path(root, rel).mkdir(parents=True)
    return _sample(hook.argv, payload, child_env(root, ROOT / PLUGIN_REL, root), root, 60)


def _write_scan_vectors(base):
    payload = json.dumps({"session_id": "aiqt-hook-latency", "cwd": str(base), "hook_event_name": EVENT,
                          "tool_name": "Write", "tool_input": {"file_path": str(Path(base, "probe.txt")),
                                                               "content": write_content(64 * len(WRITE_LINE))}})
    sample = _real_launch(base, "secrets_shift_left", payload.encode("utf-8"))
    # The exact allow (a clean scan), not merely no deny: an ask or a warn would mean the scan reacted.
    return _outcome(sample) == ("exit 0 allow", False), sample


def _startup_vectors(base):
    hooks = load_hooks(ROOT / HOOKS_JSON_REL, ROOT / PLUGIN_REL)
    sample = _real_launch(base, hooks[0].hook_id, STARTUP_PAYLOAD)
    return (_outcome(sample, startup=True) == ("exit 2 rejected", False) and sample.out == b"",
            (hooks[0].hook_id, sample))


def _real_hooks_vectors():
    hooks_json = ROOT / HOOKS_JSON_REL
    try:
        hooks = load_hooks(hooks_json, ROOT / PLUGIN_REL)
        raw = json.loads(hooks_json.read_text(encoding="utf-8"))["hooks"][EVENT]
        budget = command_budget(hooks[0].script)
    except (MeasureError, OSError, ValueError, KeyError) as exc:
        return False, str(exc)
    wrong = []
    launchers = [hook for entry in raw for hook in entry["hooks"]]
    if len(hooks) != len(launchers):
        wrong.append(("count", len(hooks), len(launchers)))
    for hook, launcher in zip(hooks, launchers):
        want = [launcher["command"]] + [arg.replace(PLUGIN_ROOT_TOKEN, str(ROOT / PLUGIN_REL))
                                        for arg in launcher["args"]]
        if (hook.argv != want or any("${" in arg for arg in hook.argv) or not Path(hook.script).is_file()
                or "-I" not in hook.argv[1:hook.argv.index(hook.script)]):
            wrong.append(hook.argv)
    for _name, target, _command in GIT_CASES:
        if not any(hook.hook_id == target and hook.matcher.fullmatch("Bash") for hook in hooks):
            wrong.append(("missing", target))
    if not budget > 0:
        wrong.append(("budget", budget))
    else:
        problems = coverage_problems(hooks, budget, WRITE_SIZES, DEFAULT_FILES, DEFAULT_COMMITS)
        if problems:
            wrong.append(("not every hook measured", problems))
    return not wrong, (wrong, len(hooks), budget)


def _malformed_vectors(base):
    good_hook = {"type": "command", "command": "python3", "args": ["-I", "x.py", "git_discard"], "timeout": 10}
    variants = {
        "not-json": "{",
        "top-list": json.dumps([]),
        "no-pretooluse": json.dumps({"hooks": {"Stop": []}}),
        "empty-pretooluse": json.dumps({"hooks": {EVENT: []}}),
        "no-matcher": json.dumps({"hooks": {EVENT: [{"hooks": [good_hook]}]}}),
        "bad-matcher": json.dumps({"hooks": {EVENT: [{"matcher": "(", "hooks": [good_hook]}]}}),
        "prompt-type": json.dumps({"hooks": {EVENT: [{"matcher": "Bash", "hooks": [dict(good_hook, type="prompt")]}]}}),
        "args-string": json.dumps({"hooks": {EVENT: [{"matcher": "Bash", "hooks": [dict(good_hook, args="x")]}]}}),
        "args-short": json.dumps({"hooks": {EVENT: [{"matcher": "Bash", "hooks": [dict(good_hook, args=["x"])]}]}}),
        "no-timeout": json.dumps({"hooks": {EVENT: [{"matcher": "Bash", "hooks": [
            {key: value for key, value in good_hook.items() if key != "timeout"}]}]}}),
        "bool-timeout": json.dumps({"hooks": {EVENT: [{"matcher": "Bash", "hooks": [dict(good_hook, timeout=True)]}]}}),
        "duplicate-id": json.dumps({"hooks": {EVENT: [{"matcher": "Bash", "hooks": [good_hook]},
                                                      {"matcher": "Write", "hooks": [good_hook]}]}}),
    }
    wrong = []
    for name, text in variants.items():
        path = Path(base, "malformed-{}.json".format(name))
        path.write_text(text, encoding="utf-8")
        try:
            load_hooks(path, base)
            wrong.append(name)
        except MeasureError:
            pass
    control = Path(base, "malformed-control.json")
    control.write_text(json.dumps({"hooks": {EVENT: [{"matcher": "Bash", "hooks": [good_hook]}]}}), encoding="utf-8")
    try:
        load_hooks(control, base)
    except MeasureError as exc:
        wrong.append(("control refused", str(exc)))
    return not wrong, wrong


def _fixture_vectors(base):
    root = Path(base, "fixture")
    env = git_env(child_env(root, root, root / "repo"))
    for _variable, rel in PINNED_DIRS:
        Path(root, rel).mkdir(parents=True)
    try:
        repo = build_fixture(root, 3, 3, env)
        status = _git(["-C", str(repo), "status", "--porcelain", "--untracked-files=all"], env).splitlines()
        shape = (_git(["-C", str(repo), "rev-list", "--count", "main"], env).strip(),
                 _git(["-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"], env).strip(),
                 _git(["-C", str(repo), "rev-parse", "origin/HEAD"], env) == _git(["-C", str(repo), "rev-parse",
                                                                                   "main"], env),
                 sorted(status))
        merge = subprocess.run(["git", "-C", str(repo), "merge-base", "main", ORPHAN], capture_output=True,
                               env=env, timeout=PROBE_TIMEOUT).returncode
    except (MeasureError, OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    want = ("3", FEATURE, True, ["?? untracked/d000/f00000.txt", "?? untracked/d000/f00001.txt",
                                 "?? untracked/d000/f00002.txt"])
    return shape == want and merge == 1, (shape, merge)


def _env_vectors(base):
    hooks_json, plugin = _stub_plugin(base, "env-plugin")
    root = Path(base, "env-root")
    with _planted_environ({"GIT_DIR": "/elsewhere/.git", "PYTHONPATH": "/elsewhere", "AIQT_PLANTED": "1"}):
        env = child_env(root, plugin, root / "repo")
    for _variable, rel in PINNED_DIRS:
        Path(root, rel).mkdir(parents=True)
    hook = load_hooks(hooks_json, plugin)[0]
    sample = _sample(hook.argv[:-1] + ["env_dump"], b"{}", env, root, 30)
    try:
        seen = json.loads(sample.out.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        seen = None
    if not isinstance(seen, dict):
        return False, sample
    pinned = {variable for variable, _rel in PINNED_DIRS}
    wrong = [name for name in seen if name not in pinned | {"PATH", "LC_ALL", "CLAUDE_PLUGIN_ROOT",
                                                            "CLAUDE_PROJECT_DIR"}]
    wrong += [name for name in pinned if not Path(seen.get(name, "/")).is_relative_to(root)]
    if (seen.get("CLAUDE_PLUGIN_ROOT"), seen.get("CLAUDE_PROJECT_DIR")) != (str(plugin), str(root / "repo")):
        wrong.append("platform variables")
    pins = git_env(env)
    if (set(pins) - set(env), pins.get("GIT_CONFIG_GLOBAL"), pins.get("GIT_CONFIG_NOSYSTEM")) != (
            {"GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM"}, os.devnull, "1"):
        wrong.append("the git configuration pins of this tool's own git calls")
    return sample.code == 0 and not wrong, (wrong, sorted(seen))


def _samples_verdict(result, error, runs):
    if result is None:
        return False, error
    wrong = []
    cases = []
    for row in result["rows"]:
        if row["case"] not in cases:
            cases.append(row["case"])
        label = "exit 2 rejected" if row["case"] == "startup" else "exit 0 allow"
        if (len(row["samples"]) != runs or not all(isinstance(sample, float) and sample >= 0
                                                    for sample in row["samples"])
                or row["failed_samples"] != 0 or row["outcomes"] != {label: runs}):
            wrong.append((row["case"], row["hook_id"], row["samples"], row["failed_samples"], row["outcomes"]))
    bash = sum(1 for matcher, _hook_id in STUB_HOOKS if matcher == "Bash")
    want_rows = 1 + 4 * bash + len(SELF_TEST_WRITE_SIZES) + 2
    startup = [row["hook_id"] for row in result["rows"] if row["case"] == "startup"]
    if cases != SELF_TEST_CASES or len(result["rows"]) != want_rows or startup != [STUB_HOOKS[0][1]]:
        wrong.append((cases, len(result["rows"]), want_rows, startup))
    return not wrong, wrong


def _stub_cli_rows(base, name, argv, variant="fail", extra=(), startup=False):
    """run_cli over a stub plugin of its own (with the extra hooks), with --json: (the exit code, stdout,
    stderr, the JSON rows less the startup case's unless startup), the rows one item naming the error when the
    JSON cannot be read."""
    hooks_json, plugin = _stub_plugin(base, name, variant=variant, extra=extra)
    figures = Path(base, name + "-figures.json")
    out, err = io.StringIO(), io.StringIO()
    code = _try_cli(argv + ["--json", str(figures)], hooks_json, plugin, write_sizes=SELF_TEST_WRITE_SIZES,
                    out=out, err=err)
    try:
        rows = [row for row in json.loads(figures.read_text(encoding="utf-8"))["rows"]
                if startup or row["case"] != "startup"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        rows = [{"error": str(exc)}]
    return code, out.getvalue(), err.getvalue(), rows


def _unevaluated_named(err_text, rows):
    """True when err_text holds exactly one cannot-evaluate line, and it gives the count of rows and names each
    of them as case/hook id, in report order, and no other row."""
    want = "cannot evaluate: {} row(s) hold a failed sample or read {} (case/hook id): {}".format(
        len(rows), NO_VALID_SAMPLE, ", ".join("{}/{}".format(row.get("case"), row.get("hook_id")) for row in rows))
    return [line for line in err_text.splitlines() if line.startswith("cannot evaluate")] == [want]


def _group_gone(group, seconds):
    """True once no process is left in the process group, probed with signal 0 (which delivers nothing) for
    up to seconds."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            os.killpg(group, 0)
        except ProcessLookupError:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _interrupt_vectors(base, signum):
    """signum sent to a child process running a stub measurement while its hook child (stub_slow.py as
    branch_root) sleeps: the run exits 128 plus signum and names the signal, and both the fixture root and the
    hook child's process group are gone. Whatever this check started that is still running afterwards is
    killed by its process group: the runner leads its own, and the hook child leads its own and wrote its id."""
    tag = _signal_name(signum).lower()
    hooks_json, plugin = _stub_plugin(base, "interrupt-" + tag, variant="slow")
    runner = Path(base, "interrupt-runner.py")
    runner.write_text(INTERRUPT_RUNNER, encoding="utf-8")
    tmp = Path(base, "interrupt-tmp-" + tag)
    tmp.mkdir()
    held = plugin / "held-branch_root"
    argv = [sys.executable, "-I", "-B", str(runner), str(Path(__file__).resolve().parent), str(hooks_json),
            str(plugin), "--runs", "1", "--files", "1", "--commits", "1", "--case-timeout", "60", "--hook",
            "branch_root"]
    env = {"PATH": os.environ.get("PATH") or os.defpath, "LC_ALL": "C.UTF-8", "TMPDIR": str(tmp)}
    group = root = code = None
    gone = False
    err = b""
    try:
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE, env=env, process_group=0)
    except OSError as exc:
        return False, "cannot launch the runner: {}".format(exc)
    with child:
        try:
            deadline = time.monotonic() + INTERRUPT_WAIT
            while group is None and child.poll() is None and time.monotonic() < deadline:
                try:
                    words = held.read_text(encoding="utf-8").split()
                    group, root = int(words[0]), Path(words[1]).parent
                except (OSError, ValueError, IndexError):
                    time.sleep(0.05)
            if group is not None:
                child.send_signal(signum)
            try:
                _out, err = child.communicate(timeout=INTERRUPT_WAIT)
                code = child.returncode
            except subprocess.TimeoutExpired:
                pass
        finally:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except OSError:
                    pass
            if group is not None:
                gone = _group_gone(group, GROUP_WAIT)
                if not gone:
                    try:
                        os.killpg(group, signal.SIGKILL)
                    except OSError:
                        pass
    leftovers = sorted(path.name for path in tmp.iterdir() if path.name.startswith(ROOT_PREFIX))
    text = (err or b"").decode("utf-8", "replace")
    ok = (group is not None and code == 128 + signum and "interrupted by " + _signal_name(signum) in text
          and gone and leftovers == [] and root.name.startswith(ROOT_PREFIX) and not os.path.lexists(root))
    return ok, (code, group, gone, leftovers, str(root), text[-400:])


def _registered_check_ids():
    """This suite's registered check ids, read through the execution gate's own schema validator: (ids,
    None), or (None, reason)."""
    import check_selftest_execution
    with contextlib.redirect_stderr(io.StringIO()):
        suites = check_selftest_execution._manifest_suites(ROOT / REGISTRY_REL)
    row = next((row for row in suites or () if row["id"] == SUITE_ID), None)
    if row is None:
        return None, "{} registers no valid suite {}".format(REGISTRY_REL, SUITE_ID)
    return set(row["expected-check-ids"]), None


def self_test(report_path=None):
    results = []
    executed = []
    if report_path is not None:
        # The execution report is finalized at interpreter exit, after this run's cleanup
        # (tools/_selftest_exit_report.py); nothing writes it in band.
        _selftest_exit_report.arm(report_path, SUITE_ID, executed)

    def check(check_id, ok, detail=""):
        results.append((check_id, bool(ok), detail))
        executed.append(check_id)

    this = sys.modules[__name__]
    handlers_before = {signum: signal.getsignal(signum) for signum in INTERRUPT_SIGNALS}
    base = Path(tempfile.mkdtemp(prefix="hook-latency-selftest-"))
    try:
        # 1. the command line: each bound at both of its ends, the documented defaults, and refusals
        check("args/bounds", *_bounds_vectors())
        defaults = parse_args([])
        check("args/defaults", (defaults["runs"], defaults["files"], defaults["commits"], defaults["case_timeout"],
                                defaults["hooks"], defaults["json"]) == (5, 20000, 1000, 60.0, [], None), defaults)
        check("args/unknown-refused", *_unknown_vectors())

        # 2. the statistics and the synthetic inputs
        check("summary/order-statistics", *_summary_vectors())
        check("summary/outcome-classified", *_outcome_vectors())
        check("inputs/bash-command-at-cap", *_bash_vectors())
        check("inputs/write-sizes-exact", *_write_vectors())
        check("inputs/write-line-scans-clean", *_write_scan_vectors(base))
        check("inputs/startup-payload-rejected", *_startup_vectors(base))

        # 3. hooks.json: the real file resolves, a malformed one is refused, a missing or unknown id is named
        check("hooks/real-hooks-json-resolves", *_real_hooks_vectors())
        check("hooks/malformed-refused", *_malformed_vectors(base))
        missing_json, missing_root = _stub_plugin(base, "missing", drop="branch_root")
        with _recording_roots() as made:
            result, error = _try_measure(missing_json, missing_root, runs=1, files=1, commits=1)
        check("hooks/missing-id-named", result is None and made == [] and "'branch_root'" in (error or "")
              and str(missing_json) in (error or ""), (error, made))
        err = io.StringIO()
        with _recording_roots() as made:
            code = _try_cli(["--hook", "nonesuch", "--runs", "1"], ROOT / HOOKS_JSON_REL, ROOT / PLUGIN_REL,
                            out=io.StringIO(), err=err)
        check("hooks/unknown-selection-named", code == 2 and "nonesuch" in err.getvalue() and made == [],
              (code, err.getvalue()[:300], made))
        # every hook must be run by a case, and every case that calls a tool must match a hook: a hook whose
        # matcher matches no case's tool, and a case left with no hook, are refused by name (exit 2) before
        # the fixture root exists
        unmeasured_json, unmeasured_root = _stub_plugin(base, "unmeasured",
                                                        extra=(("NotebookEdit", "notebook_guard"),))
        err = io.StringIO()
        with _recording_roots() as made:
            code = _try_cli(["--runs", "1", "--files", "1", "--commits", "1"], unmeasured_json, unmeasured_root,
                            write_sizes=SELF_TEST_WRITE_SIZES, out=io.StringIO(), err=err)
        check("hooks/unmeasured-id-refused", code == 2 and "'notebook_guard'" in err.getvalue() and made == [],
              (code, err.getvalue()[:400], made))
        unhooked_json, unhooked_root = _stub_plugin(base, "unhooked", drop="orch_yield_tool")
        err = io.StringIO()
        with _recording_roots() as made:
            code = _try_cli(["--runs", "1", "--files", "1", "--commits", "1"], unhooked_json, unhooked_root,
                            write_sizes=SELF_TEST_WRITE_SIZES, out=io.StringIO(), err=err)
        check("hooks/unhooked-case-refused", code == 2 and "the schedule-wakeup case" in err.getvalue()
              and made == [], (code, err.getvalue()[:400], made))

        # 4. the fixture and the child environment
        check("fixture/git-fixture-shape", *_fixture_vectors(base))
        check("env/child-pinned", *_env_vectors(base))

        # 5. a whole measurement over the stub hooks: every case runs, each row holds --runs samples
        fast_json, fast_root = _stub_plugin(base, "fast")
        with _recording_roots() as made_fast:
            result, error = _try_measure(fast_json, fast_root, runs=2, files=3, commits=3, case_timeout=30.0,
                                         write_sizes=SELF_TEST_WRITE_SIZES)
        check("measure/each-case-n-samples", *_samples_verdict(result, error, 2))
        # the same stubs through the command line: every row valid, so the run exits 0 with no cannot-evaluate
        # line, and every case was measured
        err = io.StringIO()
        code = _try_cli(["--runs", "1", "--files", "1", "--commits", "1", "--case-timeout", "30"], fast_json,
                        fast_root, write_sizes=SELF_TEST_WRITE_SIZES, out=io.StringIO(), err=err)
        check("report/exit-zero-all-valid", code == 0 and "cannot evaluate" not in err.getvalue()
              and err.getvalue().count("measuring ") == len(SELF_TEST_CASES), (code, err.getvalue()[-400:]))

        # 6. the timeout: the slow stub's branch_root is TIMED OUT, never a time, and is killed, while its
        # protected_line naps inside the timeout and keeps a time; the exit stays 0 with no cannot-evaluate
        # line, since a timed-out sample is not a failed one (and timing is never a gate). With only those two
        # hooks selected, branch_root is also the startup hook: five branch_root rows (startup, whose payload
        # the stub rejects at once as the dispatcher does, and the four Bash cases, each TIMED OUT), four
        # protected_line rows, the Write and other cases skipped.
        slow_json, slow_root = _stub_plugin(base, "slow", variant="slow")
        figures = Path(base, "figures.json")
        out, err = io.StringIO(), io.StringIO()
        began = time.monotonic()
        with _recording_roots() as made_slow:
            code = _try_cli(["--runs", "1", "--files", "2", "--commits", "2", "--case-timeout", str(SLOW_TIMEOUT),
                             "--hook", "branch_root", "--hook", "protected_line", "--json", str(figures)],
                            slow_json, slow_root, write_sizes=SELF_TEST_WRITE_SIZES, out=out, err=err)
        took = time.monotonic() - began
        try:
            written = json.loads(figures.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            written = {"error": str(exc)}
        rows = written.get("rows", [])
        slow = [row for row in rows if row["hook_id"] == "branch_root" and row["case"] != "startup"]
        nap = [row for row in rows if row["hook_id"] == "protected_line"]
        check("measure/timed-out-never-a-time", len(slow) == 4 and all(
            row["samples"] == [None] and (row["min"], row["median"], row["max"]) == (None, None, None)
            and row["outcomes"] == {TIMED_OUT: 1} and row["failed_samples"] == 0 for row in slow), slow)
        check("measure/timeout-kills-child", code == 0 and took < 4 * SLOW_TIMEOUT + 15, (code, took))
        check("measure/inside-timeout-is-a-time", len(nap) == 4 and all(
            len(row["samples"]) == 1 and isinstance(row["samples"][0], float)
            and 0.2 <= row["samples"][0] < SLOW_TIMEOUT and row["max"] == row["samples"][0] for row in nap), nap)
        check("report/exit-zero-with-timeouts", code == 0 and err.getvalue().count("measuring ") == 5
              and "cannot evaluate" not in err.getvalue(), (code, err.getvalue()[-300:]))
        conditions = written.get("conditions", {})
        check("report/json-conditions", all(key in conditions for key in (
            "load_average_start", "load_average_end", "cpu_count", "harness_python", "hook_interpreters",
            "git_version", "runs", "untracked_files", "commits", "case_timeout_seconds", "started_utc",
            "finished_utc")) and conditions.get("git_version", "").startswith("git version")
            and conditions.get("cpu_count") == os.cpu_count() and len(rows) == 9, sorted(conditions))
        text = out.getvalue()
        lines = [line for line in text.partition("\ncase ")[2].splitlines()[1:]
                 if " branch_root " in line and not line.startswith("startup ")]
        check("report/table-plain", "case" in text and "median s" in text and len(lines) == 4 and all(
            line.count(TIMED_OUT) == 4 and not re.search(r"[0-9]\.[0-9]{3}", line) for line in lines),
            text[-1200:])
        before = figures.read_bytes() if figures.exists() else None
        err = io.StringIO()
        with _recording_roots() as made_again:
            again = _try_cli(["--runs", "1", "--files", "1", "--commits", "1", "--case-timeout", "1", "--hook",
                              "orch_ask_guard", "--json", str(figures)], slow_json, slow_root,
                             write_sizes=SELF_TEST_WRITE_SIZES, out=io.StringIO(), err=err)
        check("report/json-existing-refused", again == 2 and made_again == []
              and (figures.read_bytes() if figures.exists() else None) == before, (again, err.getvalue()[:300]))

        # 7. failed samples carry no time, and a run with one exits 3 (cannot evaluate) once its report is
        # written, naming each such row on one stderr line. With the failing stub, git_discard exits 1 and
        # protected_line dies by SIGKILL in every Bash case (each such row NO VALID SAMPLE with two failed
        # samples); git_discard is also the startup hook. In a run of its own, branch_root fails one launch of
        # two (each of its rows timed from its one valid sample only, so no row of that run reads NO VALID
        # SAMPLE); it is that run's startup hook. A launch that fails is a failed sample with no time.
        code, text, err_text, fail_rows = _stub_cli_rows(base, "fail", [
            "--runs", "2", "--files", "1", "--commits", "1", "--case-timeout", "30", "--hook", "git_discard",
            "--hook", "protected_line"])
        mixed_code, _text, mixed_err, mixed_rows = _stub_cli_rows(base, "mixed", [
            "--runs", "2", "--files", "1", "--commits", "1", "--case-timeout", "30", "--hook", "branch_root"])
        dead = [row for row in fail_rows if row.get("hook_id") in ("git_discard", "protected_line")]
        mixed = [row for row in mixed_rows if row.get("hook_id") == "branch_root"]
        unlaunched = _sample([str(Path(base, "no-such-dir", "no-such-hook"))], b"{}", {"PATH": os.defpath}, base,
                             5)
        died = {"git_discard": {"exit 1": 2}, "protected_line": {"killed by SIGKILL": 2}}
        check("measure/failed-sample-no-time", code == 3 and len(dead) == 8 and _unevaluated_named(err_text, dead)
              and all(row["samples"] == [] and row["failed_samples"] == 2
                      and (row["min"], row["median"], row["max"]) == (NO_VALID_SAMPLE,) * 3
                      and row["outcomes"] == died[row["hook_id"]] for row in dead)
              and unlaunched.seconds is None and _outcome(unlaunched)[1]
              and _outcome(unlaunched)[0].startswith("launch failed"), (code, err_text[-600:], dead, unlaunched))
        check("measure/mixed-row-valid-only", mixed_code == 3 and len(mixed) == 4
              and _unevaluated_named(mixed_err, mixed) and all(
                  len(row["samples"]) == 1 and isinstance(row["samples"][0], float)
                  and MIXED_NAP <= row["samples"][0] < 30 and row["failed_samples"] == 1
                  and row["min"] == row["median"] == row["max"] == row["samples"][0]
                  and row["outcomes"] == {"exit 1": 1, "exit 0 allow": 1} for row in mixed),
              (mixed_code, mixed_err[-600:], mixed))
        lines = [line for line in text.partition("\ncase ")[2].splitlines()[1:]
                 if not line.startswith("startup ") and (" git_discard " in line or " protected_line " in line)]
        check("report/no-valid-sample-shown", len(lines) == 8 and all(
            line.count(NO_VALID_SAMPLE) == 3 and re.search(r"\b2 +2 +" + NO_VALID_SAMPLE, line)
            and not re.search(r"[0-9]\.[0-9]{3}", line) for line in lines), text[-1500:])
        # a --json file that cannot be written is exit 2 with its diagnostic, never 3, even once rows holding
        # failed samples (git_discard's four Bash rows) are measured and reported: an OSError is injected at
        # the open of that --json path alone, and every other open passes through
        unwritable = Path(base, "unwritable-figures.json")
        real_open = builtins.open

        def unwritable_json(file, *args, **kwargs):
            if isinstance(file, (str, os.PathLike)) and os.fspath(file) == str(unwritable):
                raise OSError("injected --json write failure")
            return real_open(file, *args, **kwargs)
        with _patched(builtins, "open", unwritable_json):
            lost_code, lost_text, lost_err, _rows = _stub_cli_rows(base, "unwritable", [
                "--runs", "1", "--files", "1", "--commits", "1", "--case-timeout", "30", "--hook", "git_discard"])
        lines = [line for line in lost_text.partition("\ncase ")[2].splitlines()[1:]
                 if " git_discard " in line and not line.startswith("startup ")]
        check("report/json-write-failure-exit-two", lost_code == 2 and "cannot evaluate" not in lost_err
              and [line for line in lost_err.splitlines() if line.startswith("error:")]
              == ["error: cannot write --json {}: injected --json write failure".format(unwritable)]
              and len(lines) == 4 and all(line.count(NO_VALID_SAMPLE) == 3 for line in lines)
              and "figures written" not in lost_text and not os.path.lexists(unwritable),
              (lost_code, lost_err[-600:], lines))
        # the startup case's row is held to the exit like any other: with stub_lax.py as git_discard (the
        # startup hook), the startup sample is an allow, so a failed one, while every other row is valid, and
        # the run exits 3 naming that row alone
        lax_code, _text, lax_err, lax_rows = _stub_cli_rows(base, "lax", [
            "--runs", "1", "--files", "1", "--commits", "1", "--case-timeout", "30"], variant="lax", startup=True)
        first = [row for row in lax_rows if row.get("case") == "startup"]
        rest = [row for row in lax_rows if row.get("case") != "startup"]
        bash = sum(1 for matcher, _hook_id in STUB_HOOKS if matcher == "Bash")
        check("measure/failed-startup-sample-exit-three", lax_code == 3 and _unevaluated_named(lax_err, first)
              and [(row.get("hook_id"), row.get("failed_samples"), row.get("min"), row.get("outcomes"))
                   for row in first] == [("git_discard", 1, NO_VALID_SAMPLE, {"exit 0 allow": 1})]
              and len(rest) == 4 * bash + len(SELF_TEST_WRITE_SIZES) + 2 and all(
                  row["failed_samples"] == 0 and len(row["samples"]) == 1 and isinstance(row["samples"][0], float)
                  and row["outcomes"] == {"exit 0 allow": 1} for row in rest),
              (lax_code, lax_err[-600:], first, len(rest)))
        # a hook id holding a newline and a carriage return (CONTROL_ID, failing in the four Bash cases) is
        # escaped where the cannot-evaluate line names it, so the line stays one line and is stderr's last;
        # then the escape itself on a backslash, a tab, an escape character, a line separator and a printable
        # non-ASCII letter (kept)
        control_code, _text, control_err, control_rows = _stub_cli_rows(base, "control", [
            "--runs", "1", "--files", "1", "--commits", "1", "--case-timeout", "30", "--hook", CONTROL_ID],
            variant="control", extra=(("Bash", CONTROL_ID),))
        named = [row for row in control_rows if row.get("hook_id") == CONTROL_ID]
        want = "cannot evaluate: 4 row(s) hold a failed sample or read {} (case/hook id): {}\n".format(
            NO_VALID_SAMPLE, ", ".join(case + "/line\\nbreak\\rhook"
                                       for case in ("bash-512b", "git-discard", "branch-root", "protected-line")))
        escapes = (("git_discard", "git_discard"), ("back\\slash", "back\\\\slash"), ("tab\there", "tab\\there"),
                   ("\x1b[0m", "\\x1b[0m"), ("a\u2028b", "a\\u2028b"), ("caf\u00e9", "caf\u00e9"))
        wrong = [(text, _one_line(text)) for text, escaped in escapes if _one_line(text) != escaped]
        check("report/control-ids-escaped", control_code == 3 and len(named) == 4 and control_err.endswith(want)
              and control_err.count("cannot evaluate") == 1 and "\r" not in control_err and wrong == [],
              (control_code, control_err[-600:], wrong))

        # 8. an interpreter version probe that fails ends the run with exit 2, not a report naming no version
        real_sample = _sample

        def probe_fails(argv, *args, **kwargs):
            if "-c" in argv:
                return Sample(0.01, 1, b"", b"injected probe failure", False, None)
            return real_sample(argv, *args, **kwargs)
        err = io.StringIO()
        with _recording_roots() as made_probe, _patched(this, "_sample", probe_fails):
            code = _try_cli(["--runs", "1", "--files", "1", "--commits", "1"], fast_json, fast_root,
                            write_sizes=SELF_TEST_WRITE_SIZES, out=io.StringIO(), err=err)
        check("measure/version-probe-failure-refused", code == 2 and "version probe" in err.getvalue()
              and len(made_probe) == 1 and not os.path.lexists(made_probe[0]),
              (code, err.getvalue()[:400], made_probe))

        # 9. the fixture root is gone after a clean run, a run with a timed-out child, and a run that fails
        def failing(*_args, **_kwargs):
            raise MeasureError("injected measurement failure")
        with _recording_roots() as made_failing, _patched(this, "_sample", failing):
            failed, failure = _try_measure(fast_json, fast_root, runs=1, files=1, commits=1)
        roots = made_fast + made_slow + made_failing
        check("measure/fixture-root-removed", len(made_fast) == 1 and len(made_slow) == 1
              and len(made_failing) == 1 and failed is None and "injected measurement failure" in (failure or "")
              and not any(os.path.lexists(path) for path in roots), (roots, failure))

        # 10. an interruption stays the reported cause when the root cannot be removed, and so does an error;
        # the root left behind is reported with it (the removal is real; only its result is injected)
        real_remove = _remove_root
        during = {}

        def interrupted(*_args, **_kwargs):
            during.update({signum: signal.getsignal(signum) for signum in INTERRUPT_SIGNALS})
            raise Interrupted(signal.SIGTERM)

        def broken(*_args, **_kwargs):
            raise MeasureError("injected measurement error")

        def leaky(root):
            real_remove(root)
            return "the fixture root {} could not be removed: injected".format(root)
        causes = []
        for run_in in (interrupted, broken):
            err = io.StringIO()
            with _patched(this, "_measure_in", run_in), _patched(this, "_remove_root", leaky):
                try:
                    code = run_cli(["--runs", "1", "--files", "1", "--commits", "1"], fast_json, fast_root,
                                   write_sizes=SELF_TEST_WRITE_SIZES, out=io.StringIO(), err=err)
                except Interrupted as exc:
                    code = "raised {}".format(exc)
            causes.append((code, err.getvalue()))
        check("measure/interrupt-cause-kept", causes[0][0] == 128 + signal.SIGTERM
              and "interrupted by SIGTERM" in causes[0][1] and "could not be removed: injected" in causes[0][1]
              and causes[1][0] == 2 and "injected measurement error" in causes[1][1]
              and "could not be removed: injected" in causes[1][1], causes)

        # a SIGTERM that arrives as the root's removal starts is held: the removal runs to its end, and the
        # run then reports the interruption (this process signals itself only while this tool's own
        # interrupt handler is installed)
        def signalled_removal(root):
            if not isinstance(getattr(signal.getsignal(signal.SIGTERM), "__self__", None), _Interrupts):
                return "no interrupt handler was installed, so nothing was signalled"
            os.kill(os.getpid(), signal.SIGTERM)
            return real_remove(root)
        with (_recording_roots() as made_held, _patched(this, "_measure_in", lambda *_args, **_kwargs: {}),
              _patched(this, "_remove_root", signalled_removal)):
            try:
                measure(fast_json, fast_root, runs=1, files=1, commits=1, write_sizes=SELF_TEST_WRITE_SIZES)
                held_result = "returned without the interruption"
            except Interrupted as exc:
                held_result = exc.signum
            except MeasureError as exc:
                held_result = str(exc)
        held_left = [path for path in made_held if os.path.lexists(path)]
        for path in held_left:
            shutil.rmtree(path, ignore_errors=True)
        check("measure/signal-during-removal-held", held_result == signal.SIGTERM and len(made_held) == 1
              and held_left == [], (held_result, made_held, held_left))

        # 11. the interrupt handlers are installed for a measurement's duration and the previous ones are back
        # afterwards (after every in-process run above: clean, timed out, failed, refused and interrupted)
        handlers_after = {signum: signal.getsignal(signum) for signum in INTERRUPT_SIGNALS}
        check("measure/signal-handlers-restored", handlers_after == handlers_before and all(
            during.get(signum) not in (None, handlers_before[signum]) for signum in INTERRUPT_SIGNALS
            if handlers_before[signum] != signal.SIG_IGN), (handlers_before, during, handlers_after))

        # 12. SIGTERM, SIGINT and SIGHUP in turn, each sent to a child process running a stub measurement
        # while its hook child sleeps: the root and the hook child's process group are gone afterwards
        check("measure/interrupt-sigterm-cleans-up", *_interrupt_vectors(base, signal.SIGTERM))
        check("measure/interrupt-sigint-cleans-up", *_interrupt_vectors(base, signal.SIGINT))
        check("measure/interrupt-sighup-cleans-up", *_interrupt_vectors(base, signal.SIGHUP))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    # the in-run self-guard: the executed set is exactly this suite's registered set
    expected, problem = _registered_check_ids()
    harness = []
    if problem is not None:
        harness.append(("execution-set/registry", problem))
    elif len(set(executed)) != len(executed) or set(executed) != expected:
        harness.append(("execution-set/reconciled", {"missing": sorted(expected - set(executed)),
                                                     "extra": sorted(set(executed) - expected)}))
    failed = [(check_id, detail) for check_id, ok, detail in results if not ok] + harness
    if failed:
        print("SELF-TEST FAIL: hook-latency {} of {} check(s) failed".format(len(failed), len(results)))
        for check_id, detail in failed:
            print("  {}: {}".format(check_id, repr(detail)[:600]))
        return 1
    print("SELF-TEST PASS: hook-latency {} checks (option bounds at both ends, order statistics with timed-out "
          "samples, the Bash command at its budget and the Write sizes exact, the real hooks.json resolved and "
          "fully measured, malformed, missing and unmeasured hook ids refused by name, the git fixture shape, "
          "the pinned child environment, every case with its runs, TIMED OUT never a time, failed samples "
          "never timed and a run with one (in the startup row too) exiting 3 while an all-valid or timed-out "
          "run exits 0, a --json write failure exiting 2 whatever the rows hold, control characters in a named "
          "hook id escaped onto one line, a failed version probe refused, the JSON conditions, and the fixture "
          "root removed at the normal end, on a timeout, on an error and on SIGINT, SIGTERM and SIGHUP)"
          .format(len(results)))
    return 0


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    if (len(args) in (2, 3) and args[-2] == "--execution-report" and os.path.isabs(args[-1])
            and args[:-2] in ([], ["--self-test"])):
        return self_test(args[-1])
    if args == ["--self-test"]:
        return self_test()
    return run_cli(args, ROOT / HOOKS_JSON_REL, ROOT / PLUGIN_REL)


if __name__ == "__main__":
    _selftest_exit_report.exit_with(main())
