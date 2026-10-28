#!/usr/bin/env python3
"""git-fixture-env-config-system: the SYSTEM-CONFIG lane of the git-fixture-env suite family, over
the shared engine tools/selftest_git_fixture_env.py (see its module docstring).

System poison uses a PATH shim, never /etc: the lane builds the shared poison tree and control
repository (_config_poison_fixture), simulates an installed system config through a git wrapper
that reasserts GIT_CONFIG_SYSTEM only when a child did not disable or explicitly replace system
configuration, proves the control discriminates (config/system-control), runs every registered
member under that shim (_config_results, system=True) and asserts the lane clean. The OPF
home-lifecycle checks run here because their roster verdict (env/registered-opf-lifecycles)
folds each registered OPF module observed SYSTEM-lane run into its finding: every exempt module
still requires a successful observed system-config-lane run, so the evidence this check consumes
is produced by this lane and by no other suite. Each lifecycle probe runs under its own short
bound (_LIFECYCLE_PROBE_SECONDS); a probe still running there is recorded as cannot-evaluate,
naming the last stage it reported, never as a lifecycle finding (_lifecycle_probe), and
env/lifecycle-probe-timeout-vectors pins that path over scripted launches.

  selftest_git_fixture_env_config_system.py                              exit 0 pass, 1 failure
  selftest_git_fixture_env_config_system.py --execution-report ABS_PATH  also write check IDs

Exit 2 is cannot-evaluate (a harness/setup error, or a member or lifecycle probe that reached its
time bound), never a pass; the engine fails closed exactly as the shared module documents."""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: selftest_git_fixture_env_config_system.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import json
import os
import shutil
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import selftest_git_fixture_env as _gfe  # noqa: E402
import _selftest_exit_report  # noqa: E402
from selftest_git_fixture_env import (  # noqa: E402
    CANNOT_EVALUATE, OPF_LIFECYCLE_EXEMPTIONS, ROOT, _command_identity, _config_poison_fixture,
    _config_results, _is_timeout, _lifecycle_finding, _opf_lifecycle_delegates,
    _registered_offenders, _registered_selftests)

SUITE_ID = "git-fixture-env-config-system"
# check() is the engine choke point: every id this runner records flows into the one
# EXECUTED/FAILURES record the engine verdict, execution report and exit code read.
check = _gfe.check

# The lifecycle probe's bound: a deliberately short FAST-FAIL deadline (the kill-timeout rule's
# stated exception), not a completion budget. The probe's whole intended work is one module import
# plus the entry's own isolation setup up to its FIRST delegate call, where the stub stops it;
# nothing it is meant to run waits on anything. Reaching it is attributed to the deadline: the
# probe's timeout message names the probe and ONLY what was observed, the last stage it reported
# or that it reported none (the probe emits a stage inside the delegate stub and another after the
# entry unwound, so a hang after the delegate is never read as one before it, and an import can
# finish without its stage line being observed, so no observed stage never reads as an unfinished
# import) and is recorded as cannot-evaluate, never a lifecycle finding (which needs an observed
# wrong isolation) and never "scheduling" by assumption: a hanging entry and a starved host both
# end here, and the reported stage, or its absence, is all this message asserts.
_LIFECYCLE_PROBE_SECONDS = 60


def _lifecycle_probe(code, module, entry, delegate, env, run=None, cannot_evaluate=None,
                     report=None):
    """Launch one lifecycle probe through run (default subprocess.run); return its observation (a
    (returncode, payload) pair, (returncode, stdout, stderr) when the payload does not parse, or
    the launch error's text, all lifecycle EVIDENCE). A probe still running at its bound returns
    its timeout message instead, recorded FIRST in cannot_evaluate (default CANNOT_EVALUATE) and
    then announced on report (default sys.stdout, never stderr), as _run_bounded records a
    member's: cannot-evaluate, never a lifecycle finding (_lifecycle_problems)."""
    run = subprocess.run if run is None else run
    try:
        child = run([sys.executable, "-I", "-B", "-c", code,
                     str(ROOT / "opf" / "tools"), module, entry, delegate],
                    env=env, capture_output=True, text=True, timeout=_LIFECYCLE_PROBE_SECONDS)
        try:
            return (child.returncode, json.loads(child.stdout))
        except ValueError:
            return (child.returncode, child.stdout, child.stderr)
    except subprocess.TimeoutExpired as exc:
        partial = exc.stderr or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", "replace")
        stages = [line.split(":", 1)[1].strip() for line in partial.split("\n")
                  if line.startswith("probe stage:")]
        try:
            load = os.getloadavg()
        except OSError:
            load = "unavailable"
        # Only the OBSERVED stage reports are asserted: a wrapper can reach
        # its delegate and hang afterwards (unwinding, restoration), so
        # "stage entered" never licenses the claim that the delegate was not
        # reached, and NO observed stage asserts only that none was received
        # (the import can have finished without its stage line being
        # observed), never an unfinished import.
        message = ("TIMEOUT: lifecycle probe {}.{} reached its {} s bound {} (loadavg {}) "
                   "(cannot evaluate)".format(
                       module, entry, _LIFECYCLE_PROBE_SECONDS,
                       "after its last reported stage: {}".format(stages[-1]) if stages
                       else "before it reported any stage", load))
        (CANNOT_EVALUATE if cannot_evaluate is None else cannot_evaluate).append(message)
        print("LIFECYCLE " + message, file=sys.stdout if report is None else report, flush=True)
        return message
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)


def _lifecycle_problems(results, recorded=None):
    """(problems, probes) over the lifecycle observations, in key order: probes holds each
    observation that is a timeout message recorded in recorded (default CANNOT_EVALUATE), a probe
    that reached its bound (_lifecycle_probe), which _lifecycle_finding keeps out of a definite
    finding; problems holds every other observation that is not the isolated-and-restored one,
    an unrecorded timeout message included."""
    recorded = CANNOT_EVALUATE if recorded is None else recorded
    problems, probes = [], []
    for (module, entry), value in sorted(results.items()):
        if _is_timeout(value) and value in recorded:
            probes.append(value)
        elif value != (0, [[[True, True, True, True]], True]):
            problems.append("{}.{}: {!r}".format(module, entry, value))
    return problems, probes


_LIFECYCLE_TIMEOUT_EXPECTED = [
    ("bound-kill-entered", True, True, True, True, True, "", True),
    ("bound-kill-delegated", True, True, True, True, True, "", True),
    ("bound-kill-import", True, True, True, True, True, "", True),
    ("bound-kill-unreported", True, True, True, True, True, "", True),
    ("exit-1", (1, "not json", ""), [], "", "", True),
    ("oserror", "[Errno 5] vector", [], "", "", True),
    ("clean", (0, [[], True]), [], "", "", True),
    ("launch-bound-control", True, (_LIFECYCLE_PROBE_SECONDS, None)),
    ("problems-split", ["m.e: 'TIMEOUT: unrecorded (cannot evaluate)'", "m.g: (1, [])"],
     ["TIMEOUT: recorded (cannot evaluate)"]),
    ("finding-attributed", (([], ["TIMEOUT: recorded (cannot evaluate)"]),
                            ("TIMEOUT: recorded (cannot evaluate)",))),
    ("finding-definite", (([], ["m.g: (1, [])"]), ())),
]


def _lifecycle_timeout_vectors():
    """env/lifecycle-probe-timeout-vectors, through scripted launches (nothing runs): every
    scripted launch records the launch SHAPE the probe actually requested (interpreter head,
    isolation flags, probe arguments, the probe bound as the subprocess timeout, captured text
    streams and the environment), and every case compares that record against the one bounded
    shape, so removing or disabling the production timeout turns every case red, not only the
    exception handling; the launch-bound-control case pins that an otherwise identical unbounded
    record compares unequal. A probe still running at its bound returns its timeout message,
    which names the probe and ONLY the stage reports observed, never a claim that the delegate
    was not reached (a wrapper can reach its delegate and hang unwinding: the post-delegation
    case holds the message to the observed stage) and never, when no stage was observed, a
    claim that the import had not finished (a probe can finish its import and be killed before
    its stage line is observed: both no-stage cases hold the message to that absence),
    recorded in the record passed and announced
    on the report passed, nothing on stderr; a nonzero exit, an unparsable payload and a launch
    error stay lifecycle evidence, recorded nowhere. _lifecycle_problems keeps a recorded probe
    timeout out of the problems (an unrecorded one stays a problem), and _lifecycle_finding
    attributes the finding to the probe timeouts only when no definite path or problem stands
    beside them (a definite finding outranks cannot-evaluate)."""
    import contextlib
    import io

    launch_expected = (True, ("-I", "-B", "-c"),
                       (str(ROOT / "opf" / "tools"), "vector_module", "vector_entry",
                        "vector_delegate"),
                       _LIFECYCLE_PROBE_SECONDS, True, True, {})

    def scripted(effect, launches):
        def run(argv, **kwargs):
            launches.append((argv[0] == sys.executable, tuple(argv[1:4]), tuple(argv[5:]),
                             kwargs.get("timeout"), kwargs.get("capture_output") is True,
                             kwargs.get("text") is True, kwargs.get("env")))
            if isinstance(effect, BaseException):
                raise effect
            return subprocess.CompletedProcess(argv, effect[0], effect[1], "")
        return run

    got = []
    for label, effect, stage in (
            ("bound-kill-entered", subprocess.TimeoutExpired(
                ["vector"], 60, output=b"",
                stderr=b"probe stage: imported\nprobe stage: entered\n"),
             "after its last reported stage: entered"),
            ("bound-kill-delegated", subprocess.TimeoutExpired(
                ["vector"], 60, output=b"",
                stderr=b"probe stage: imported\nprobe stage: entered\n"
                       b"probe stage: delegated\n"),
             "after its last reported stage: delegated"),
            ("bound-kill-import", subprocess.TimeoutExpired(["vector"], 60),
             "before it reported any stage"),
            ("bound-kill-unreported", subprocess.TimeoutExpired(
                ["vector"], 60, output=b"",
                stderr=b"a line that is not a stage report\n"),
             "before it reported any stage"),
            ("exit-1", (1, "not json"), None), ("oserror", OSError(5, "vector"), None),
            ("clean", (0, "[[], true]"), None)):
        recorded, announced, launches = [], io.StringIO(), []
        with contextlib.redirect_stderr(io.StringIO()) as leaked:
            value = _lifecycle_probe("", "vector_module", "vector_entry", "vector_delegate", {},
                                     run=scripted(effect, launches), cannot_evaluate=recorded,
                                     report=announced)
        if stage is None:
            got.append((label, value, recorded, announced.getvalue(), leaked.getvalue(),
                        launches == [launch_expected]))
            continue
        got.append((label, _is_timeout(value), recorded == [value],
                    announced.getvalue() == "LIFECYCLE {}\n".format(value),
                    "lifecycle probe vector_module.vector_entry" in value and stage in value,
                    "before its delegate" not in value, leaked.getvalue(),
                    launches == [launch_expected]))
    # The launch-shape comparison itself discriminates the bound: the same
    # record with the timeout slot emptied must compare unequal, so a
    # comparison that stops carrying the timeout turns this control red.
    unbounded = launch_expected[:3] + (None,) + launch_expected[4:]
    got.append(("launch-bound-control", unbounded != launch_expected,
                (launch_expected[3], unbounded[3])))
    recorded = "TIMEOUT: recorded (cannot evaluate)"
    problems, probes = _lifecycle_problems(dict([
        (("m", "d"), recorded), (("m", "e"), "TIMEOUT: unrecorded (cannot evaluate)"),
        (("m", "f"), (0, [[[True, True, True, True]], True])), (("m", "g"), (1, []))]),
        [recorded])
    got.append(("problems-split", problems, probes))
    got.append(("finding-attributed", _lifecycle_finding([], {}, set(), [], [], probes)))
    got.append(("finding-definite", _lifecycle_finding([], {}, set(), ["m.g: (1, [])"], [],
                                                       probes)))
    return got


def _opf_home_lifecycles(config_results):
    """Account for every registered OPF module and its observed command variants.
    Wrapped entries must isolate before delegation and restore after an exception.
    Explicit non-git exemptions still run under the config observer; its absolute
    executable/replaced-PATH residual applies. The derived graph rejects direct
    wrapper bypasses; dynamic routes retain the graph helper's disclosed residual.
    """
    code = "\n".join((
        "import importlib, inspect, json, os, sys",
        "sys.path.insert(0, sys.argv[1])",
        "module = importlib.import_module(sys.argv[2])",
        "print('probe stage: imported', file=sys.stderr, flush=True)",
        "entry, delegate = sys.argv[3:5]",
        "saved = dict(os.environ)",
        "seen = []",
        "class StopProbe(Exception): pass",
        "def stop(*args):",
        "    home = os.environ.get('HOME')",
        "    seen.append([home != saved.get('HOME'),",
        "                 home == os.environ.get('XDG_CONFIG_HOME'), os.path.isdir(home),",
        "                 os.environ.get('GIT_CONFIG_NOSYSTEM') == '1'])",
        "    print('probe stage: delegated', file=sys.stderr, flush=True)",
        "    raise StopProbe()",
        "setattr(module, delegate, stop)",
        "try:",
        "    fn = getattr(module, entry)",
        "    args = [None for p in inspect.signature(fn).parameters.values()",
        "            if p.default is inspect.Parameter.empty]",
        "    print('probe stage: entered', file=sys.stderr, flush=True)",
        "    fn(*args)",
        "except StopProbe:",
        "    pass",
        "print('probe stage: unwound', file=sys.stderr, flush=True)",
        "print(json.dumps([seen, dict(os.environ) == saved]))",
    ))
    results = {}
    roster = [argv for argv in _registered_selftests()
              if _command_identity(argv)[0].startswith("opf/tools/")]
    paths = {_command_identity(argv)[0] for argv in roster}
    # Paths missing for a reason other than their config run; _lifecycle_finding adds the config
    # runs and keeps a member whose run only timed out out of a definite finding.
    definite = set(OPF_LIFECYCLE_EXEMPTIONS) - paths
    problems = []
    try:
        trees = {Path(relative).stem: ast.parse(
            (ROOT / relative).read_text(encoding="utf-8")) for relative in sorted(paths)}
        registrations = _opf_lifecycle_delegates(trees)
    except (OSError, SyntaxError, ValueError) as exc:
        registrations = {}
        problems.append(str(exc))
    for relative in sorted(paths):
        module = Path(relative).stem
        delegates = {entry: callee[1] for (owner, entry), callee in registrations.items()
                     if owner == module}
        reason = OPF_LIFECYCLE_EXEMPTIONS.get(relative)
        if not delegates:
            if not reason or not reason.strip():
                definite.add(relative)
        elif reason is not None:
            definite.add(relative)  # A stale exemption must be reviewed and removed.
        for entry, delegate in sorted(delegates.items()):
            env = dict(os.environ, HOME="/caller-home", XDG_CONFIG_HOME="/caller-xdg",
                       GIT_CONFIG_NOSYSTEM="0")
            got = _lifecycle_probe(code, module, entry, delegate, env)
            results[module, entry] = got
    if not paths or not results:
        problems.append("no registered lifecycle observations")
    observed, probes = _lifecycle_problems(results)
    problems.extend(observed)
    for check_id, module, entry in (
            ("env/opf-tooling-home-lifecycle", "opf", "run_self_tests"),
    ):
        if (module, entry) not in results:
            problems.append("{}.{}: missing lifecycle observation".format(module, entry))
        check(check_id, results.get((module, entry)), (0, [[[True, True, True, True]], True]))
    got, cause = _lifecycle_finding(roster, config_results, definite, problems, probes=probes)
    check("env/registered-opf-lifecycles", got, ([], []), cause)


def _system_lane(base):
    """The system-config lane, and the OPF home-lifecycle checks over its results; see the
    module docstring."""
    import shlex

    check("env/lifecycle-probe-timeout-vectors", _lifecycle_timeout_vectors(),
          _LIFECYCLE_TIMEOUT_EXPECTED)
    fx = _config_poison_fixture(base)
    roster, env = fx["roster"], fx["env"]
    marker, monitor_marker = fx["marker"], fx["monitor_marker"]
    control, config = fx["control"], fx["config"]
    # The fixture build itself fired the caller hooks and the fsmonitor; clear both markers so
    # every byte this lane reads was produced by its own probes, never by the fixture build.
    marker.write_bytes(b"")
    monitor_marker.write_bytes(b"")
    # Simulate an installed system config without writing /etc. Reassert its path
    # only when a child did not disable or explicitly replace system configuration.
    system = base / "system-gitconfig"
    system.write_text(config, encoding="utf-8")
    empty_home = base / "system-lane-home"
    empty_home.mkdir()
    bin_dir = base / "system-bin"
    bin_dir.mkdir()
    real_git = shutil.which("git")
    if real_git is None:
        raise ValueError("system lane requires git")
    wrapper = bin_dir / "git"
    wrapper.write_text(
        '#!/bin/sh\n'
        'if [ "${{GIT_CONFIG_NOSYSTEM:-}}" != 1 ] && [ "${{GIT_CONFIG_SYSTEM+x}}" != x ]; then\n'
        '  export GIT_CONFIG_SYSTEM={}\n'
        'fi\nexec {} "$@"\n'.format(shlex.quote(str(system)), shlex.quote(real_git)),
        encoding="utf-8")
    wrapper.chmod(0o700)
    system_env = dict(env, HOME=str(empty_home), XDG_CONFIG_HOME=str(empty_home),
                      PATH=str(bin_dir) + os.pathsep + env.get("PATH", os.defpath))
    monitor_marker.write_bytes(b"")
    probe = subprocess.run(["git", "-C", str(control), "ls-files"], env=system_env,
                           capture_output=True, timeout=60)
    check("config/system-control", (probe.returncode, bool(monitor_marker.read_bytes())),
          (0, True))
    system_results = _config_results(roster, system_env, marker, monitor_marker, system=True)
    offenders, cause = _registered_offenders(system_results)
    check("config/registered-system", offenders, [], cause)
    _opf_home_lifecycles(system_results)


if __name__ == "__main__":
    _gfe.SUITE_ID = SUITE_ID
    _selftest_exit_report.exit_with(_gfe.main(_gfe._parse_argv(sys.argv[1:]), _system_lane))
