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
is produced by this lane and by no other suite.

  selftest_git_fixture_env_config_system.py                              exit 0 pass, 1 failure
  selftest_git_fixture_env_config_system.py --execution-report ABS_PATH  also write check IDs

Exit 2 is cannot-evaluate (a harness/setup error or a member that reached its time bound), never
a pass; the engine fails closed exactly as the shared module documents."""
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
    OPF_LIFECYCLE_EXEMPTIONS, ROOT, _command_identity, _config_poison_fixture, _config_results,
    _lifecycle_finding, _opf_lifecycle_delegates, _registered_offenders, _registered_selftests)
# Tree scans read through the shared non-blocking reader, so a special file (a FIFO above all) at a
# scanned path is refused by name instead of blocking the suite.
from _walk import read_text_nonblocking  # noqa: E402

SUITE_ID = "git-fixture-env-config-system"
# check() is the engine choke point: every id this runner records flows into the one
# EXECUTED/FAILURES record the engine verdict, execution report and exit code read.
check = _gfe.check


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
        "entry, delegate = sys.argv[3:5]",
        "saved = dict(os.environ)",
        "seen = []",
        "class StopProbe(Exception): pass",
        "def stop(*args):",
        "    home = os.environ.get('HOME')",
        "    seen.append([home != saved.get('HOME'),",
        "                 home == os.environ.get('XDG_CONFIG_HOME'), os.path.isdir(home),",
        "                 os.environ.get('GIT_CONFIG_NOSYSTEM') == '1'])",
        "    raise StopProbe()",
        "setattr(module, delegate, stop)",
        "try:",
        "    fn = getattr(module, entry)",
        "    args = [None for p in inspect.signature(fn).parameters.values()",
        "            if p.default is inspect.Parameter.empty]",
        "    fn(*args)",
        "except StopProbe:",
        "    pass",
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
            read_text_nonblocking(ROOT / relative)) for relative in sorted(paths)}
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
            try:
                child = subprocess.run(
                    [sys.executable, "-I", "-B", "-c", code,
                     str(ROOT / "opf" / "tools"), module, entry, delegate],
                    env=env, capture_output=True, text=True, timeout=60)
                try:
                    got = (child.returncode, json.loads(child.stdout))
                except ValueError:
                    got = (child.returncode, child.stdout, child.stderr)
            except (OSError, subprocess.SubprocessError) as exc:
                got = str(exc)
            results[module, entry] = got
    if not paths or not results:
        problems.append("no registered lifecycle observations")
    problems.extend("{}.{}: {!r}".format(module, entry, value)
                    for (module, entry), value in sorted(results.items())
                    if value != (0, [[[True, True, True, True]], True]))
    for check_id, module, entry in (
            ("env/opf-upgrade-home-lifecycle", "check_opf_upgrade", "_suite"),
            ("env/opf-tooling-home-lifecycle", "opf", "run_self_tests"),
    ):
        if (module, entry) not in results:
            problems.append("{}.{}: missing lifecycle observation".format(module, entry))
        check(check_id, results.get((module, entry)), (0, [[[True, True, True, True]], True]))
    got, cause = _lifecycle_finding(roster, config_results, definite, problems)
    check("env/registered-opf-lifecycles", got, ([], []), cause)


def _system_lane(base):
    """The system-config lane, and the OPF home-lifecycle checks over its results; see the
    module docstring."""
    import shlex

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
