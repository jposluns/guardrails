#!/usr/bin/env python3
"""git-fixture-env-config-malformed: the MALFORMED-CONFIG lane of the git-fixture-env suite family,
over the shared engine tools/selftest_git_fixture_env.py (see its module docstring).

No hooks or ignore poison in this lane: read-only production helpers must be tested
independently of fixture writes. The lane builds the shared poison tree and control repository
(_config_poison_fixture), removes the hook/ignore/attributes poison, proves the fsmonitor and
malformed-config controls discriminate (config/fsmonitor-control, config/malformed-control,
config/xdg-malformed-control), runs the four fsmonitor member probes, then runs every registered
member except the two real-checkout archive readers (which retain caller config by contract)
under malformed HOME and XDG configuration and asserts each member rc. Malformed HOME/XDG config
also covers reads such as rev-parse, which hooks/ignore poison cannot see.

  selftest_git_fixture_env_config_malformed.py                              exit 0 pass, 1 failure
  selftest_git_fixture_env_config_malformed.py --execution-report ABS_PATH  also write check IDs

Exit 2 is cannot-evaluate (a harness/setup error or a member that reached its time bound), never
a pass; the engine fails closed exactly as the shared module documents."""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: selftest_git_fixture_env_config_malformed.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import json
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import selftest_git_fixture_env as _gfe  # noqa: E402
import _selftest_exit_report  # noqa: E402
from selftest_git_fixture_env import (  # noqa: E402
    CONFIG_EXCLUSIONS, _command_identity, _config_poison_fixture, _config_results,
    _member_result, _registered_offenders, _run_config_member)

SUITE_ID = "git-fixture-env-config-malformed"
# check() is the engine choke point: every id this runner records flows into the one
# EXECUTED/FAILURES record the engine verdict, execution report and exit code read.
check = _gfe.check


def _malformed_lane(base):
    """The malformed-config lane; see the module docstring."""
    fx = _config_poison_fixture(base)
    roster, env = fx["roster"], fx["env"]
    marker, monitor_marker = fx["marker"], fx["monitor_marker"]
    control, home, xdg = fx["control"], fx["home"], fx["xdg"]
    ignore, attributes, monitor = fx["ignore"], fx["attributes"], fx["monitor"]
    # The fixture build itself fired the caller hooks and the fsmonitor; clear both markers so
    # every byte this lane reads was produced by its own probes, never by the fixture build.
    marker.write_bytes(b"")
    monitor_marker.write_bytes(b"")
    # No hooks or ignore poison in these lanes: read-only production helpers
    # must be tested independently of fixture writes.
    for path in (ignore, attributes, xdg / "git" / "ignore", xdg / "git" / "attributes",
                 home / ".config" / "git" / "ignore", home / ".config" / "git" / "attributes",
                 home / ".config" / "git" / "config"):
        path.unlink()
    fsconfig = '[core]\n\tfsmonitor = {}\n'.format(json.dumps(str(monitor)))
    malformed = "[invalid\n"
    (home / ".gitconfig").write_text(fsconfig, encoding="utf-8")
    (xdg / "git" / "config").write_text(fsconfig, encoding="utf-8")
    probe = subprocess.run(["git", "-C", str(control), "ls-files"], env=env,
                           capture_output=True, timeout=60)
    check("config/fsmonitor-control", (probe.returncode, bool(monitor_marker.read_bytes())),
          (0, True))
    (home / ".gitconfig").write_text(malformed, encoding="utf-8")
    probe = subprocess.run(["git", "-C", str(control), "rev-parse", "HEAD"], env=env,
                           capture_output=True, timeout=60)
    check("config/malformed-control", probe.returncode != 0, True)
    (home / ".gitconfig").write_text(fsconfig, encoding="utf-8")
    (xdg / "git" / "config").write_text(malformed, encoding="utf-8")
    probe = subprocess.run(["git", "-C", str(control), "rev-parse", "HEAD"], env=env,
                           capture_output=True, timeout=60)
    check("config/xdg-malformed-control", probe.returncode != 0, True)

    for fs_rc_id, fs_marker_id, member in (
            ("config/check_manifest/fsmonitor-rc", "config/check_manifest/fsmonitor",
             "tools/check_manifest.py"),
            ("config/gen_manifest/fsmonitor-rc", "config/gen_manifest/fsmonitor",
             "tools/gen_manifest.py"),
            ("config/check_record_sections/fsmonitor-rc", "config/check_record_sections/fsmonitor",
             "tools/check_record_sections.py"),
            ("config/_qa_adapter/fsmonitor-rc", "config/_qa_adapter/fsmonitor",
             "tools/_qa_adapter.py"),
    ):
        (home / ".gitconfig").write_text(fsconfig, encoding="utf-8")
        (xdg / "git" / "config").write_text(fsconfig, encoding="utf-8")
        monitor_marker.write_bytes(b"")
        rc = _run_config_member((member, "--self-test"), env)
        check(fs_rc_id, rc, 0)
        check(fs_marker_id, monitor_marker.read_bytes(), b"")
    (home / ".gitconfig").write_text(malformed, encoding="utf-8")
    (xdg / "git" / "config").write_text(malformed, encoding="utf-8")
    malformed_results = _config_results(
        [argv for argv in roster if _command_identity(argv) not in CONFIG_EXCLUSIONS["malformed"]],
        env, marker, monitor_marker)
    offenders, cause = _registered_offenders(malformed_results)
    check("config/registered-malformed", offenders, [], cause)
    for malformed_id, member in (
            ("config/selftest_aiqt_corpus/malformed", "tools/selftest_aiqt_corpus.py"),
            ("config/selftest_orch_hooks/malformed", "tools/selftest_orch_hooks.py"),
            ("config/selftest_aiqt_hooks/malformed", "tools/selftest_aiqt_hooks.py"),
            ("config/_qa_adapter/malformed", "tools/_qa_adapter.py"),
            ("config/check_record_drift/malformed", "tools/check_record_drift.py"),
            ("config/check_mistakes_register/malformed", "tools/check_mistakes_register.py"),
            ("config/check_version_monotonicity/malformed", "tools/check_version_monotonicity.py"),
            ("config/check_record_sections/malformed", "tools/check_record_sections.py"),
            ("config/check_portability/malformed", "tools/check_portability.py"),
            ("config/gen_manifest/malformed", "tools/gen_manifest.py"),
            ("config/check_manifest/malformed", "tools/check_manifest.py"),
            ("config/check_branch_root/malformed", "tools/check_branch_root.py"),
            ("config/check_gensrc_failclose/malformed", "tools/check_gensrc_failclose.py"),
            ("config/selftest_ci_status/malformed", "tools/selftest_ci_status.py"),
            ("config/check_opf_init/malformed", "opf/tools/check_opf_init.py"),
            ("config/check_opf_upgrade/malformed", "opf/tools/check_opf_upgrade.py"),
            ("config/check_opf_doctor/malformed", "opf/tools/check_opf_doctor.py"),
            ("config/_opf_init_operation/malformed", "opf/tools/_opf_init_operation.py"),
            ("config/_opf_oplock/malformed", "opf/tools/_opf_oplock.py"),
            ("config/_opf_observe/malformed", "opf/tools/_opf_observe.py"),
            ("config/opf/malformed", "opf/tools/opf.py"),
    ):
        check(malformed_id, _member_result(malformed_results, member, 0), 0)


if __name__ == "__main__":
    _gfe.SUITE_ID = SUITE_ID
    _selftest_exit_report.exit_with(_gfe.main(_gfe._parse_argv(sys.argv[1:]), _malformed_lane))
