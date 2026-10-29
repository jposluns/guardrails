#!/usr/bin/env python3
"""git-fixture-env-config-combined: the COMBINED config-injection lane of the git-fixture-env suite
family, over the shared engine tools/selftest_git_fixture_env.py (see its module docstring).

No inherited GIT_* pins may hide the caller on-disk poison: the lane builds the shared poison
tree and control repository (_config_poison_fixture), proves the marker hooks execute under this
caller configuration (config/injection-control), exercises each on-disk source independently
(explicit/xdg/home ignore and attributes, the archive attribute legs, the fsmonitor control),
pins the observer-wrapper fail-closed and prepared-before-pool controls, then runs every
registered member under the combined poison (_config_results) and asserts each member rc and
hook/fsmonitor marker bytes. Each member gets rc and hook-byte assertions; neither substitutes
for the other. Literal legacy IDs report results; they never select which commands run.

  selftest_git_fixture_env_config_combined.py                              exit 0 pass, 1 failure
  selftest_git_fixture_env_config_combined.py --execution-report ABS_PATH  also write check IDs

Exit 2 is cannot-evaluate (a harness/setup error or a member that reached its time bound), never
a pass; the engine fails closed exactly as the shared module documents."""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: selftest_git_fixture_env_config_combined.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import os
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import selftest_git_fixture_env as _gfe  # noqa: E402
import _selftest_exit_report  # noqa: E402
from selftest_git_fixture_env import (  # noqa: E402
    HolderNotReady, _WRITE_RECORDERS, _await_holder_ready, _config_poison_fixture,
    _config_results, _dispatch_write_audit, _member_result, _registered_offenders,
    _reopen_members, _require_wrapper_observed)

SUITE_ID = "git-fixture-env-config-combined"
# check() is the engine choke point: every id this runner records flows into the one
# EXECUTED/FAILURES record the engine verdict, execution report and exit code read.
check = _gfe.check


def _wrapper_bypass_control(base, env, marker, monitor_marker):
    """POSITIVE CONTROL for the fall-through tripwire and its fail-closed guard.
    Mechanism leg: hold an observer-shaped wrapper open for writing from a live
    child (the ETXTBSY condition a concurrently forked child creates) and exec
    git through the member-shaped PATH: CPython's exec PATH search skips the
    busy wrapper and continues, so without the tripwire the REAL git would run
    unobserved, a false clean. The tripwire must intercept (rc 66, bypass
    recorded, wrapper silent), _require_wrapper_observed must refuse the
    recorded bypass, and the released leg must run the wrapper itself with the
    tripwire silent; this leg's PATH is pinned to the two controlled
    directories only.
    Production leg: _config_results itself runs one synthetic member whose
    member launch holds the REAL observer wrapper open for writing and launches
    git; the run must fail closed with the bypass diagnostic naming the member,
    never return a clean result."""
    import shlex
    from unittest.mock import patch

    probe = base / "bypass-control"
    trap = probe / "fallback-trap"
    trap.mkdir(parents=True)
    wrapper_log = probe / "wrapper-log"
    bypass_log = probe / "bypass-log"
    for path in (wrapper_log, bypass_log):
        path.write_bytes(b"")
    wrapper = probe / "git"
    wrapper.write_text("#!/bin/sh\nprintf 'wrapper\\n' >> {}\n".format(
        shlex.quote(str(wrapper_log))), encoding="utf-8")
    wrapper.chmod(0o700)
    tripwire = trap / "git"
    tripwire.write_text("#!/bin/sh\nprintf 'bypassed\\n' >> {}\nexit 66\n".format(
        shlex.quote(str(bypass_log))), encoding="utf-8")
    tripwire.chmod(0o700)
    probe_env = {"PATH": str(probe) + os.pathsep + str(trap)}

    def launch():
        try:
            done = subprocess.run(["git"], env=probe_env, capture_output=True, timeout=60)
            rc = done.returncode
        except (OSError, subprocess.SubprocessError) as exc:
            rc = "control launch failed: {}".format(exc)
        return (rc, bypass_log.read_bytes(), wrapper_log.read_bytes())

    holder_code = "\n".join((
        "import sys",
        "handle = open(sys.argv[1], 'ab')",
        "print('held', flush=True)",
        "sys.stdin.read()",
        "handle.close()",
    ))
    holder = subprocess.Popen([sys.executable, "-I", "-B", "-c", holder_code, str(wrapper)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        try:
            _await_holder_ready(holder)
            held = launch()
        except HolderNotReady as exc:
            held = "HolderNotReady: {}".format(exc)
        try:
            _require_wrapper_observed(bypass_log, "control")
            refused = "guard accepted a recorded bypass"
        except ValueError as exc:
            refused = str(exc).startswith("config observer wrapper was bypassed")
    finally:
        holder.stdin.close()
        holder.stdout.close()
        try:
            holder.wait(timeout=60)
        except subprocess.TimeoutExpired:
            holder.kill()
            holder.wait(timeout=60)
    for path in (wrapper_log, bypass_log):
        path.write_bytes(b"")
    released = launch()

    def bypassing_member(argv, member_env):
        busy = Path(member_env["PATH"].split(os.pathsep)[0]) / "git"
        with open(busy, "ab"):
            done = subprocess.run(["git", "config", "--get", "user.name"], env=member_env,
                                  capture_output=True, timeout=60)
        return done.returncode

    outcome = "member bypass returned a clean result"
    try:
        with patch.object(_gfe, "_run_config_member", bypassing_member):
            _config_results((("tools/bypass-control-probe", "--self-test"),),
                            env, marker, monitor_marker)
    except ValueError as exc:
        message = str(exc)
        outcome = (message.startswith("config observer wrapper was bypassed")
                   and "member run: tools/bypass-control-probe --self-test" in message)
    _reopen_members()
    check("config/wrapper-bypass-fails-closed",
          (held, refused, released, outcome),
          ((66, b"bypassed\n", b""), True, (0, b"", b"wrapper\n"), True))


def _prepared_before_pool_control(env, marker, monitor_marker):
    """DETERMINISTIC guard for the root-cause ordering in _config_results (no
    stress, no timing): every file a member will exec (the observer wrapper,
    the tripwire, the copied hooks and fsmonitor) must be written and chmodded
    on the MAIN thread before the worker pool is constructed. A CPython audit
    hook records every open-for-write, chmod, rename, link and symlink with its
    thread and whether the pool exists yet; each synthetic member snapshots the
    executable files in its private tree from inside the pool. Moving member
    preparation back into the pooled body turns this red on every run, whether
    or not a fork happens to race a write. The red member chmods its own
    wrapper from inside the pool and must be flagged, so a recorder that sees
    nothing cannot pass; the required files and the every-executable-was-seen
    leg keep the clean member from passing vacuously. File-descriptor-only
    operations (os.fchmod, a write through an inherited descriptor) are outside
    what these audit events name."""
    import concurrent.futures
    import threading
    from unittest.mock import patch

    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    target = {"open": 0, "os.chmod": 0, "os.rename": 1, "os.link": 1, "os.symlink": 1}
    main_thread = threading.main_thread().ident
    pool_started = []
    events = []
    snapshots = {}

    def record(event, args):
        if event not in target or (event == "open" and not args[2] & write_flags):
            return
        path = args[target[event]]
        if not isinstance(path, int):
            events.append((os.path.abspath(os.fsdecode(path)), event,
                           threading.get_ident() == main_thread and not pool_started))

    class MarkedPool(concurrent.futures.ThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            pool_started.append(True)
            super().__init__(*args, **kwargs)

    def snapshot_member(argv, member_env):
        private = member_env["PATH"].split(os.pathsep)[0]
        found = set()
        for folder, _, names in os.walk(private):
            for name in names:
                path = os.path.join(folder, name)
                if os.path.isfile(path) and os.stat(path).st_mode & 0o111:
                    found.add(os.path.abspath(path))
        snapshots[argv[0]] = (private, found)
        if argv[0].endswith("-red"):
            os.chmod(os.path.join(private, "git"), 0o700)
        return 0

    required = ["git", os.path.join("fallback-trap", "git"), os.path.join("caller", "fsmonitor"),
                os.path.join("caller", "config-hooks", "pre-commit")]

    def verdict(member):
        if member not in snapshots:
            return "member did not run: " + member
        private, found = snapshots[member]
        written = {path for path, _, _ in events}
        return (sorted(set(required) - {os.path.relpath(p, private) for p in found}),
                sorted(os.path.relpath(p, private) for p in found - written),
                sorted({(os.path.relpath(p, private), event)
                        for p, event, early in events if p in found and not early}))

    clean, red = "tools/prepared-before-pool-probe", "tools/prepared-before-pool-probe-red"
    if not _gfe._WRITE_AUDIT_INSTALLED:
        sys.addaudithook(_dispatch_write_audit)
        _gfe._WRITE_AUDIT_INSTALLED = True
    _WRITE_RECORDERS.append(record)
    try:
        with patch.object(concurrent.futures, "ThreadPoolExecutor", MarkedPool), patch.object(
                _gfe, "_run_config_member", snapshot_member):
            _config_results(((clean, "--self-test"), (red, "--self-test")),
                            env, marker, monitor_marker)
        got = (bool(pool_started), verdict(clean), verdict(red))
    except ValueError as exc:
        got = "config run raised: {}".format(exc)
    finally:
        _WRITE_RECORDERS.remove(record)
    _reopen_members()
    check("config/executables-prepared-before-pool", got,
          (True, ([], [], []), ([], [], [("git", "os.chmod")])))


def _combined_lane(base):
    """The combined config-injection lane; see the module docstring."""
    fx = _config_poison_fixture(base)
    roster, env = fx["roster"], fx["env"]
    marker, monitor_marker = fx["marker"], fx["monitor_marker"]
    control = fx["control"]
    check("config/injection-control", bool(marker.read_bytes()), True)
    # Exercise each on-disk source independently, including HOME's fallback.
    for ignore_id, attributes_id, mode in (
            ("config/explicit-ignore-control", "config/explicit-attributes-control", "explicit"),
            ("config/xdg-ignore-control", "config/xdg-attributes-control", "xdg"),
            ("config/home-ignore-control", "config/home-attributes-control", "home"),
    ):
        probe_env = dict(env)
        if mode != "explicit":
            probe_env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
                             GIT_CONFIG_NOSYSTEM="1")
        if mode == "home":
            probe_env.pop("XDG_CONFIG_HOME")
        probe = subprocess.run(["git", "-C", str(control), "status", "--porcelain"],
                               env=probe_env, capture_output=True, timeout=60, check=True)
        check(ignore_id, probe.stdout, b"")
        probe = subprocess.run(["git", "-C", str(control), "check-attr", "fixture-poison", "--", "seed"],
                               env=probe_env, capture_output=True, timeout=60, check=True)
        check(attributes_id, probe.stdout, b"seed: fixture-poison: set\n")
    for archive_id, pin in (
            ("config/archive-attributes-live", False),
            ("config/archive-attributes-neutralized", True),
    ):
        import io
        import tarfile
        args = ["-c", "core.attributesFile=/dev/null"] if pin else []
        archive = subprocess.run(["git", "-C", str(control), "-c", "gc.auto=0",
                                  "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false",
                                  *args, "archive", "HEAD"],
                                 env=env, capture_output=True, timeout=60, check=True)
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
            names = tar.getnames()
        check(archive_id, "seed" in names, pin)
    monitor_marker.write_bytes(b"")
    probe = subprocess.run(["git", "-C", str(control), "ls-files"], env=env,
                           capture_output=True, timeout=60)
    check("config/combined-fsmonitor-control", (probe.returncode, bool(monitor_marker.read_bytes())),
          (0, True))
    _wrapper_bypass_control(base, env, marker, monitor_marker)
    _prepared_before_pool_control(env, marker, monitor_marker)
    combined = _config_results(roster, env, marker, monitor_marker)
    offenders, cause = _registered_offenders(combined)
    check("config/registered-combined", offenders, [], cause)
    for rc_id, hooks_id, member in (
            ("config/selftest_aiqt_corpus/rc", "config/selftest_aiqt_corpus/hooks",
             "tools/selftest_aiqt_corpus.py"),
            ("config/selftest_orch_hooks/rc", "config/selftest_orch_hooks/hooks",
             "tools/selftest_orch_hooks.py"),
            ("config/selftest_aiqt_hooks/rc", "config/selftest_aiqt_hooks/hooks",
             "tools/selftest_aiqt_hooks.py"),
            ("config/_qa_adapter/rc", "config/_qa_adapter/hooks",
             "tools/_qa_adapter.py"),
            ("config/check_record_drift/rc", "config/check_record_drift/hooks",
             "tools/check_record_drift.py"),
            ("config/check_mistakes_register/rc", "config/check_mistakes_register/hooks",
             "tools/check_mistakes_register.py"),
            ("config/check_version_monotonicity/rc", "config/check_version_monotonicity/hooks",
             "tools/check_version_monotonicity.py"),
            ("config/check_release_build/rc", "config/check_release_build/hooks",
             "tools/check_release_build.py"),
            ("config/check_release_delta/rc", "config/check_release_delta/hooks",
             "tools/check_release_delta.py"),
            ("config/check_record_sections/rc", "config/check_record_sections/hooks",
             "tools/check_record_sections.py"),
            ("config/check_portability/rc", "config/check_portability/hooks",
             "tools/check_portability.py"),
            ("config/gen_manifest/rc", "config/gen_manifest/hooks",
             "tools/gen_manifest.py"),
            ("config/check_manifest/rc", "config/check_manifest/hooks",
             "tools/check_manifest.py"),
            ("config/check_branch_root/rc", "config/check_branch_root/hooks",
             "tools/check_branch_root.py"),
            ("config/check_gensrc_failclose/rc", "config/check_gensrc_failclose/hooks",
             "tools/check_gensrc_failclose.py"),
            ("config/selftest_ci_status/rc", "config/selftest_ci_status/hooks",
             "tools/selftest_ci_status.py"),
            ("config/check_opf_init/rc", "config/check_opf_init/hooks",
             "opf/tools/check_opf_init.py"),
            ("config/check_opf_doctor/rc", "config/check_opf_doctor/hooks",
             "opf/tools/check_opf_doctor.py"),
            ("config/_opf_init_operation/rc", "config/_opf_init_operation/hooks",
             "opf/tools/_opf_init_operation.py"),
            ("config/_opf_oplock/rc", "config/_opf_oplock/hooks",
             "opf/tools/_opf_oplock.py"),
            ("config/_opf_observe/rc", "config/_opf_observe/hooks",
             "opf/tools/_opf_observe.py"),
            ("config/opf/rc", "config/opf/hooks",
             "opf/tools/opf.py"),
    ):
        check(rc_id, _member_result(combined, member, 0), 0)
        check(hooks_id, (_member_result(combined, member, 1),
                         _member_result(combined, member, 2)), (b"", b""))


if __name__ == "__main__":
    _gfe.SUITE_ID = SUITE_ID
    _selftest_exit_report.exit_with(_gfe.main(_gfe._parse_argv(sys.argv[1:]), _combined_lane))
