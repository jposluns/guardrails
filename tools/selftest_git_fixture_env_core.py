#!/usr/bin/env python3
"""git-fixture-env-core: every check of the git-fixture-env suite family outside the three
config-injection lanes, run over the shared engine tools/selftest_git_fixture_env.py (see its
module docstring for the architecture and the disclosed residuals).

In the original suite order this runner carries: layer 1, the shared scrub itself (unit checks,
the F-367 auto-maintenance probes, the repo-wide maintenance-pin tripwire scan and its planted
red/contract legs, the in-place and caller-env child probes, the OPF inherited-env probe and the
lifecycle system-pin checks); layer 2, the per-member routing, function-scoped routing and
real-repository trust checks; the registry roster checks (including the prefixed-recursion
exclusion over all four git-fixture-env suite ids); the OPF lifecycle graph checks; the
member-timeout, member-close and verdict controls for the bounded config-member machinery; the
gen-manifest fixture-setup guards; and layer 3, the end-to-end caller-repository leak probe.

  selftest_git_fixture_env_core.py                              exit 0 on pass, 1 on failure
  selftest_git_fixture_env_core.py --execution-report ABS_PATH  also write the executed check IDs

Exit 2 is cannot-evaluate (a harness/setup error or a bounded child that reached its time bound),
never a pass; the engine fails closed exactly as the shared module documents."""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: selftest_git_fixture_env_core.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
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
import signal
import subprocess
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import selftest_git_fixture_env as _gfe  # noqa: E402
import _selftest_exit_report  # noqa: E402
import _git_fixture_env  # noqa: E402
import _optlevel  # noqa: E402  the engine import put opf/tools on sys.path
from selftest_git_fixture_env import (  # noqa: E402
    CANNOT_EVALUATE, CHECKS_MANIFEST, CONFIG_EXCLUSIONS, CONFIG_MEMBER_BOUNDS,
    CONFIG_MEMBER_BOUND_DEFAULT, CONFIG_MEMBER_DRAIN_SECONDS, CONFIG_MEMBER_REAP_SECONDS,
    CORPUS_SELFTEST, LANE_RETURN_EXPECTED, MAIN_VERDICT_EXPECTED, POISON_VARS, ROOT, SENTINEL,
    SIZING_ACCOUNT_ROSTER, SIZING_ACCOUNT_TIMED, SIZING_LATER_MEASURED, SIZING_MALFORMED_MEASURED,
    SIZING_MEASURED, _CLOSE_PROBE_CHILD,
    _CLOSE_SWEEP_CHILD, _DELIBERATE_EMPTY_SEND, _GROUP_MEMBER_FIXTURE, _LIFECYCLE_GRAPH_FIXTURE,
    _MEMBERS, _STATE_EVIDENCE, _SWEEP_MODES, _SWEEP_REACHED, _SWEEP_WANT, _allowlist_producers,
    _archive_reads_use_caller_env, _auto_maintenance_children, _binding_calls, _build_decoy,
    _call_name, _caller_env_archive_only, _calls_any, _close_lock_sites, _command_identity,
    _closing_refusal_control, _config_member_bound, _handshake_expired, _is_timeout,
    _kill_recorded, _lane_return_fail_closed, _lifecycle_finding, _main_verdict_scenarios,
    _maintenance_pin_scan,
    _member_group_empty, _member_timeout_timely, _opf_lifecycle_delegates, _oracle_down_sites,
    _partial_timeout_cause, _pid_state, _pidfd_group_signal_supported, _poison_environ,
    _recorded_states, _registered_offenders, _registered_selftests, _release_member_group,
    _roster_figures, _run_bounded, _run_config_member, _run_corpus, _run_corpus_imported,
    _scan_contract_failures,
    _scenario_timeout_record, _scrub_scoped_first, _slow_control_record, _snapshot_git_dir,
    _stood_down_wants, _stop_member_group, _sweep_call_sites, _sweep_observed, _system_pin_probe,
    _timeout_cause, _trace2_maintenance_children, _verdict)

SUITE_ID = "git-fixture-env-core"
# check() is the engine choke point: every id this runner records flows into the one
# EXECUTED/FAILURES record the engine verdict, execution report and exit code read.
check = _gfe.check


def _roster_checks():
    from unittest.mock import patch
    import check_ci_parity
    import check_selftest_execution

    local = "tools/run_all_checks.sh"
    opf = "opf/tools/run_all_checks.sh"
    ci = ".github/workflows/quality.yml"
    real_reader = check_ci_parity.read_runner_text
    local_text, local_diagnostic = real_reader(ROOT / local, local)
    ci_text, ci_diagnostic = real_reader(ROOT / ci, ci)
    # The live registries must come through the byte-level reader clean;
    # everything below mutates these texts.
    check("roster/live-registry-reads", (local_diagnostic, ci_diagnostic),
          (None, None))
    if local_diagnostic is not None or ci_diagnostic is not None:
        return
    binding = 'here="$(cd "$(dirname "$0")" && pwd)" || exit 2'
    # The valid failure-state initializers, so an empty-roster fixture reaches
    # its own guard rather than the missing-initializer diagnostic.
    state = 'failed=0\nfailed_names=""\n'
    # The real dispatcher definition, so a fixture that declares a gate
    # reaches its own guard rather than the definition-order diagnostic.
    definition_start = local_text.index("run_gate() {")
    definition = local_text[
        definition_start:local_text.index("\n}\n", definition_start) + 3]

    def add_local(command):
        # Keep the exact terminal summary last so each fixture reaches its own guard.
        summary = 'if [ "$failed" -ne 0 ]; then\n'
        if local_text.count(summary) != 1:  # explicit, not an assert: python -O strips asserts
            raise AssertionError("the runner's terminal summary is not unique")
        return local_text.replace(summary, command + "\n" + summary, 1)

    def refusal():
        try:
            _registered_selftests()
        except ValueError as exc:
            return str(exc)
        return ""

    scratch = Path(tempfile.mkdtemp(prefix="roster-fixture-"))

    def disk_reader(name, relative, data):
        """Write the fixture to a REAL file and redirect only the path, so
        the raw fixture bytes flow through the actual byte-level reader;
        injecting already-decoded text here formerly hid Path.read_text()'s
        universal-newline translation from every roster fixture."""
        fixture = scratch / name.replace("/", "-")
        fixture.write_bytes(data if isinstance(data, bytes)
                            else data.encode("utf-8"))
        target = ROOT / relative

        def read(path, source, reader=None):
            return real_reader(fixture if path == target else path,
                               source, reader=reader)
        return read

    # Require THIS guard's diagnostic: an unrelated downstream refusal is not
    # evidence that the intended guard ran. Bad commands augment a valid roster.
    for check_id, relative, text, diagnostic in (
            ("roster/empty-local-refused", local, state, local + ": empty registry"),
            ("roster/unparseable-local-refused", local,
             add_local('\nrun_gate "cont" python3 -I -B tools/check_secrets.py \\\n  --self-test\n'),
             local + ": registry diagnostics:"),
            ("roster/empty-opf-refused", opf, binding + "\n" + state + "exit 0\n",
             opf + ": empty registry"),
            ("roster/standalone-scaffold-refused", opf, binding + "\n" + binding + "\nexit 0\n",
             "unsupported standalone runner scaffold"),
            # The byte-level reader refuses the form feed on the raw
            # bytes at read time; the shared adapter's raw-text screen
            # stays behind it for text that arrives already decoded
            # (vector 26 pins that screen directly).
            ("roster/standalone-text-format-refused", opf,
             binding + "\n" + state
             + 'run_gate "probe-selftest" python3 -I -B tools/check_secrets.py --self-test\n'
             + "# hidden\fexit 0\n",
             opf + ": registry diagnostics:"),
            ("roster/unparseable-ci-refused", ci,
             ci_text + '\n      - run: python3 -I -B tools/check_secrets.py --self-test | cat\n',
             ci + ": registry diagnostics:"),
            # A reviewed masked expression survives extraction only in the CI
            # workflow; the selection guard must still refuse the masked
            # member as a dynamic self-test argument.
            ("roster/dynamic-arguments-refused", ci,
             ci_text + '\n      - name: Dynamic probe'
                       '\n        run: python3 -I -B tools/check_secrets.py'
                       ' --self-test --base "$PUSH_BEFORE"\n',
             "dynamic self-test arguments:"),
            # In the LOCAL runner the same spelling is refused at extraction:
            # the gate-word character allowlist admits no expansion, so a
            # masked flag value never reaches selection there.
            ("roster/local-masked-flag-refused", local,
             add_local('\nrun_gate "dynamic" python3 -I -B tools/check_secrets.py --self-test --base "$PUSH_BEFORE"\n'),
             local + ": registry diagnostics:"),
            ("roster/launcher-refused", local,
             add_local('\nrun_gate "launcher" python3 -B -I tools/check_secrets.py --self-test\n'),
             "unsupported self-test launcher:"),
            ("roster/empty-selftests-refused", local,
             state + definition
             + 'run_gate "live" python3 -I -B tools/check_secrets.py\n',
             local + ": empty self-test roster"),
            ("roster/prefixed-invalid-suite-refused", local,
             add_local('\nrun_gate "bad-suite" python3 -I -B ./tools/check_selftest_execution.py --suite git-fixture-env-core --extra\n'),
             "unparseable suite invocation:"),
            # Byte-level reader: Path.read_text() translated a planted
            # carriage return into a newline BEFORE any screen ran, so a
            # \r-hidden gate line in a REAL runner file on disk read as a
            # clean extra gate and the roster stayed green. Every fixture
            # in this table is now written to disk and read through the
            # actual byte-level reader; the two \r fixtures fail against
            # any decode-first reader, whose universal-newline translation
            # erases the byte ahead of a text screen.
            ("roster/local-carriage-return-refused", local,
             add_local('# hidden\rrun_gate "cr-probe" python3 -I -B tools/check_secrets.py --self-test'),
             local + ": registry diagnostics:"),
            ("roster/opf-carriage-return-refused", opf,
             binding + "\n" + state + definition
             + 'run_gate "probe-selftest" python3 -I -B tools/check_secrets.py --self-test\n'
             + '# hidden\rrun_gate "cr-shadow" python3 -I -B tools/check_secrets.py --self-test\n'
             + "exit 0\n",
             opf + ": registry diagnostics:"),
            # U+2028 (spelled via chr to keep this source ASCII) survives a
            # UTF-8 decode, so only the byte-level screen refuses it at
            # read time with the raw-byte diagnostic.
            ("roster/ci-line-separator-refused", ci,
             ci_text + "# probe" + chr(0x2028) + "trailer\n",
             ci + ": registry diagnostics:"),
    ):
        with patch.object(check_ci_parity, "read_runner_text",
                          disk_reader(check_id, relative, text)):
            got = refusal()
        check(check_id, got.startswith(diagnostic), True)

    # A parser/member provenance mismatch must not silently drop a selected member.
    extract = check_ci_parity.extract_local
    def missing_origin(text):
        result = extract(text)
        return result._replace(origins={})
    with patch.object(check_ci_parity, "extract_local", missing_origin):
        got = refusal()
    check("roster/missing-origin-refused",
          got.startswith("cannot recover exact self-test arguments:"), True)

    stale = ("tools/check_secrets.py", "--self-test", "--stale-exclusion")
    with patch.dict(CONFIG_EXCLUSIONS["all"], {stale: "Synthetic stale exclusion."}):
        got = refusal()
    check("roster/stale-exclusion-refused",
          got.startswith("stale or unreasoned all exclusion:"), True)

    extra = '\nrun_gate "argument-probe" python3 -I -B tools/check_secrets.py --self-test --red-on-revert\n'
    read = disk_reader("roster-registered-arguments", local, add_local(extra))
    with patch.object(check_ci_parity, "read_runner_text", read):
        roster = _registered_selftests()
    check("roster/registered-arguments", ("tools/check_secrets.py", "--self-test",
                                         "--red-on-revert") in roster, True)

    suites = check_selftest_execution._manifest_suites(CHECKS_MANIFEST)
    extra = '\nrun_gate "new-suite" python3 -I -B ./tools/check_selftest_execution.py --suite roster-probe\n'
    read = disk_reader("roster-prefixed-suite-expanded", local, add_local(extra))
    with patch.object(check_ci_parity, "read_runner_text", read), patch.object(
            check_selftest_execution, "_manifest_suites", return_value=suites + [
                {"id": "roster-probe", "runner": "tools/check_secrets.py",
                 "expected-check-ids": ["probe"]}]):
        roster = _registered_selftests()
    check("roster/prefixed-suite-expanded",
          ("./tools/check_selftest_execution.py", "--suite", "roster-probe") in roster
          and ("tools/check_secrets.py",) in roster, True)

    lane_ids = ("git-fixture-env-core", "git-fixture-env-config-combined",
                "git-fixture-env-config-malformed", "git-fixture-env-config-system")
    lane_runners = ("tools/selftest_git_fixture_env.py",
                    "tools/selftest_git_fixture_env_core.py",
                    "tools/selftest_git_fixture_env_config_combined.py",
                    "tools/selftest_git_fixture_env_config_malformed.py",
                    "tools/selftest_git_fixture_env_config_system.py")
    extra = "".join(
        '\nrun_gate "recursive-suite-{}" python3 -I -B ./tools/check_selftest_execution.py --suite {}\n'
        .format(index, suite_id) for index, suite_id in enumerate(lane_ids))
    read = disk_reader("roster-prefixed-recursion", local, add_local(extra))
    with patch.object(check_ci_parity, "read_runner_text", read):
        roster = _registered_selftests()
    check("roster/prefixed-recursion-excluded",
          not any(("tools/check_selftest_execution.py", "--suite", suite_id) in roster
                  or ("./tools/check_selftest_execution.py", "--suite", suite_id) in roster
                  for suite_id in lane_ids)
          and not any((runner,) in roster for runner in lane_runners), True)
    shutil.rmtree(scratch, ignore_errors=True)


def _system_pin_checks(base):
    import types
    # Level 0 parse and compile: the mutant must not follow -O/-OO.
    tree = _optlevel.parse(Path(_git_fixture_env.__file__).read_text(encoding="utf-8"))
    assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   and any(ast.unparse(t) == "os.environ['PATH']" for t in node.targets)]
    if len(assignments) != 1:
        raise ValueError("cannot uniquely mutate lifecycle PATH installation")
    assignments[0].value = _optlevel.parse('saved.get("PATH", os.defpath)', mode="eval").body
    mutant = types.ModuleType("fixture_path_mutant")
    exec(compile(ast.fix_missing_locations(tree), "<path-removal-mutant>", "exec", optimize=0),
         mutant.__dict__)
    pins, restored = _system_pin_probe(base, _git_fixture_env.fixture_git_lifecycle)
    check("env/lifecycle-system-pins", pins, (0, ["1", os.devnull]))
    check("env/lifecycle-restores-caller", restored, True)
    pins, restored = _system_pin_probe(base, mutant.fixture_git_lifecycle)
    check("env/lifecycle-path-removal-red", (pins, restored), ((0, ["", ""]), True))


def _main_verdict_control(sleeper, fixture, run=None):
    """The production config/member-timeout-main-verdict check: _main_verdict_scenarios through
    run (default _run_bounded), checked with its own partial cause. _mixed_timeout_controls runs
    this same function over an injected runner, so the cause this check passes is pinned."""
    observed, cause = _main_verdict_scenarios(sleeper, fixture, run)
    check("config/member-timeout-main-verdict", observed, MAIN_VERDICT_EXPECTED, cause)
    return observed


def _mixed_timeout_controls(sleeper):
    """config/member-timeout-main-verdict-mixed: _main_verdict_control (the production check
    call, against a private finding record) over an injected runner in which one scenario's
    child reaches its bound while the other returns, in BOTH scenario orders: a definite
    mismatch beside the timeout (the attributed child times out and the definite child exits 2,
    or the definite child times out and the attributed child exits 1) keeps the finding definite
    and _verdict exits 1; a correct result beside the timeout is attributed and exits 2.
    config/timeout-offenders-split: a lane with one member that only timed out and one definite
    offender lists ONLY the definite one, in _registered_offenders and in _lifecycle_finding, in
    both results orders; with only the timed-out member the finding is attributed; an
    unrecorded timeout message is definite; a lifecycle probe problem beside a member that only
    timed out keeps the lifecycle finding definite."""
    import contextlib
    import io
    from unittest.mock import patch

    this = _gfe

    line = "CONFIG-INJECTION TIMEOUT: {} reached its 5 s bound (cannot evaluate)\n".format(sleeper)
    right = {"attributed": (2, line + "SELF-TEST CANNOT EVALUATE\n  - probe/attributed-rc\n", ""),
             "definite": (1, line + "SELF-TEST FAIL:\n  - probe/definite\n  - "
                          "probe/attributed-rc\n", "")}
    codes = []
    for plan in ({"attributed": "timeout", "definite": (2, "", "")},
                 {"attributed": (1, "", ""), "definite": "timeout"},
                 {"attributed": "timeout", "definite": "right"},
                 {"attributed": "right", "definite": "timeout"}):
        def fake(argv, cwd, env, bound, label, banner, plan=plan):
            outcome = plan[argv[-1]]
            if outcome == "timeout":
                return "TIMEOUT: {} reached its {} s bound (cannot evaluate)".format(
                    label, bound), "", ""
            return right[argv[-1]] if outcome == "right" else outcome

        failures, caused, executed = [], {}, []
        with patch.object(this, "FAILURES", failures), \
                patch.object(this, "TIMEOUT_CAUSED", caused), \
                patch.object(this, "EXECUTED", executed), \
                patch.object(this, "_EXECUTED_SET", set()):
            observed = _main_verdict_control(sleeper, sleeper.parent, fake)
        timeouts = [entry for entry in observed if _is_timeout(entry)]
        with contextlib.redirect_stdout(io.StringIO()):
            code = _verdict(failures, timeouts, caused)
        codes.append((executed, len(failures), [len(cause) for cause in caused.values()], code))
    ran = ["config/member-timeout-main-verdict"]
    check("config/member-timeout-main-verdict-mixed", codes,
          [(ran, 1, [], 1), (ran, 1, [], 1), (ran, 1, [1], 2), (ran, 1, [1], 2)])

    timeout = "TIMEOUT: opf/tools/x.py --self-test reached its 1 s bound (cannot evaluate)"
    stalled = (("opf/tools/x.py", "--self-test"), (timeout, b"", b"", b""))
    failed = (("opf/tools/y.py", "--self-test"), (1, b"", b"", b""))
    got = []
    for pairs in ((stalled, failed), (failed, stalled), (stalled,)):
        results = dict(pairs)
        got.append((_registered_offenders(results, [timeout]),
                    _lifecycle_finding(list(results), results, set(), [], [timeout])))
    got.append(_registered_offenders(dict((stalled,)), []))
    got.append(_lifecycle_finding([stalled[0]], dict((stalled,)), set(), ["m.e: (1, [])"],
                                  [timeout]))
    check("config/timeout-offenders-split", got,
          [(([("opf/tools/y.py", "--self-test")], ()), ((["opf/tools/y.py"], []), ())),
           (([("opf/tools/y.py", "--self-test")], ()), ((["opf/tools/y.py"], []), ())),
           (([("opf/tools/x.py", "--self-test")], (timeout,)),
            ((["opf/tools/x.py"], []), (timeout,))),
           ([("opf/tools/x.py", "--self-test")], ()),
           (([], ["m.e: (1, [])"]), ())])


def _timeout_cleanup_order_controls(sleeper):
    """config/member-timeout-record-before-cleanup: when _run_bounded's cleanup starts, the
    timeout is already in the record (a cleanup moved ahead of the record fails this).
    config/member-timeout-cleanup-on-raise: when the announcement stream raises, or the record
    itself raises, the exception still propagates AND the child has been killed and reaped
    (returncode -SIGKILL) first (a cleanup skipped by the raise leaves returncode None and fails
    this). config/member-timeout-diagnostic-guarded: with a cleanup problem injected and a
    stream that refuses the cleanup diagnostic, a successful record still returns the timeout
    tuple, and a failed record still propagates the record's own exception, the child reaped in
    both. config/member-timeout-interrupt-cleanup: a KeyboardInterrupt injected into the wait
    still propagates, after the child has been killed and reaped. Every child spawned here is
    killed afterwards whatever the code under test did."""
    import contextlib
    import io
    from unittest.mock import patch

    this = _gfe
    real_stop = _stop_member_group
    real_popen = subprocess.Popen
    spawned, interrupt = [], []

    def spawn(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        spawned.append(child)
        if interrupt:
            interrupt_first_wait(child)
        return child

    class RaisingStream:
        def write(self, text):
            raise RuntimeError("injected announcement failure")

        def flush(self):
            pass

    class RaisingRecord:
        def append(self, entry):
            raise RuntimeError("injected record failure")

    class RefusingCleanupReport(io.StringIO):
        def write(self, text):
            if "cleanup after" in text:
                raise OSError("injected cleanup-report failure")
            return super().write(text)

    class InjectedInterrupt(KeyboardInterrupt):
        pass

    def interrupt_first_wait(child):
        real_communicate, calls = child.communicate, []

        def communicate(*c_args, **c_kwargs):
            calls.append(c_kwargs.get("timeout"))
            if len(calls) == 1:
                raise InjectedInterrupt("injected interrupt in the wait")
            return real_communicate(*c_args, **c_kwargs)

        child.communicate = communicate

    def stop_with_problem(proc):
        return real_stop(proc) + ["injected cleanup problem"]

    argv = [sys.executable, "-I", "-B", str(sleeper)]
    snapshots = []
    recorded = []

    def stop_after_record(proc):
        snapshots.append(list(recorded))
        return real_stop(proc)

    order, outcomes, guarded, interrupted = "not run", [], [], "not run"
    try:
        with patch.object(subprocess, "Popen", spawn), \
                patch.object(this, "_stop_member_group", stop_after_record), \
                contextlib.redirect_stderr(io.StringIO()):
            rc, _, _ = _run_bounded(argv, str(sleeper.parent), dict(os.environ), 2,
                                    "record-order control", "CONTROL ", recorded, io.StringIO())
        order = (_is_timeout(rc), recorded == [rc], snapshots == [[rc]])
        for record, stream in (([], RaisingStream()), (RaisingRecord(), io.StringIO())):
            first = len(spawned)
            try:
                with patch.object(subprocess, "Popen", spawn), \
                        contextlib.redirect_stderr(io.StringIO()):
                    _run_bounded(argv, str(sleeper.parent), dict(os.environ), 2,
                                 "cleanup-on-raise control", "CONTROL ", record, stream)
                raised = None
            except RuntimeError as exc:
                raised = str(exc)
            outcomes.append((raised, [child.returncode for child in spawned[first:]],
                             [_is_timeout(entry) for entry in record]
                             if isinstance(record, list) else None))
        for record in ([], RaisingRecord()):
            first, stream, rc, raised = len(spawned), RefusingCleanupReport(), None, None
            try:
                with patch.object(subprocess, "Popen", spawn), \
                        patch.object(this, "_stop_member_group", stop_with_problem), \
                        contextlib.redirect_stderr(io.StringIO()):
                    rc, _, _ = _run_bounded(argv, str(sleeper.parent), dict(os.environ), 2,
                                            "diagnostic-guard control", "CONTROL ", record,
                                            stream)
            except Exception as exc:
                raised = "{}: {}".format(type(exc).__name__, exc)
            guarded.append((raised, _is_timeout(rc),
                            [entry == rc for entry in record] if isinstance(record, list)
                            else None,
                            stream.getvalue() == ("CONTROL {}\n".format(rc)
                                                  if _is_timeout(rc) else ""),
                            [child.returncode for child in spawned[first:]]))
        first, raised = len(spawned), None
        interrupt.append(True)
        try:
            with patch.object(subprocess, "Popen", spawn), \
                    contextlib.redirect_stderr(io.StringIO()):
                _run_bounded(argv, str(sleeper.parent), dict(os.environ), 60,
                             "interrupt control", "CONTROL ", [], io.StringIO())
        except (InjectedInterrupt, Exception) as exc:
            raised = "{}: {}".format(type(exc).__name__, exc)
        finally:
            interrupt.clear()
        interrupted = (raised, [child.returncode for child in spawned[first:]])
    finally:
        for child in spawned:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except OSError:
                    child.kill()
                try:
                    child.wait(timeout=CONFIG_MEMBER_REAP_SECONDS)
                except subprocess.TimeoutExpired:
                    pass
    check("config/member-timeout-record-before-cleanup", order, (True, True, True))
    check("config/member-timeout-cleanup-on-raise", outcomes,
          [("injected announcement failure", [-signal.SIGKILL], [True]),
           ("injected record failure", [-signal.SIGKILL], None)])
    check("config/member-timeout-diagnostic-guarded", guarded,
          [(None, True, [True], True, [-signal.SIGKILL]),
           ("RuntimeError: injected record failure", False, None, True, [-signal.SIGKILL])])
    check("config/member-timeout-interrupt-cleanup", interrupted,
          ("InjectedInterrupt: injected interrupt in the wait", [-signal.SIGKILL]))


def _member_close_controls(fixture):
    """config/member-off-main-thread: _run_bounded called from the main thread starts the child
    (Popen) and waits in communicate() on another thread, returns its exit code, and leaves the
    registry empty. config/member-closing-refuses-start: once closing is set no child starts and
    the refusal comes back as the launch-error message (_closing_refusal_control).
    config/member-grandchild-after-normal-exit:
    a member whose leader exits 0 leaving a grandchild in its group (stdio on /dev/null, so the
    wait returns), run on BOTH signalling paths whatever the kernel: the pidfd process-group path
    (where the kernel has it) has the grandchild killed by the cleanup and the group released; the
    numeric fallback, forced by giving pidfd_send_signal a flag every kernel refuses with EINVAL
    (and the expectation on a kernel without the pidfd flag), leaves the grandchild alive and the
    group RETAINED (one group registered, the cleanup diagnostic naming it), and releases it once
    the recorded survivor is killed here. The _CLOSE_PROBE_CHILD scenarios, each in its own
    interpreter, with the handlers restored after main() (a real SIGINT raises again), the
    registry lock free and every recorded process dead unless noted, within 30 s of each signal
    or injected failure, less the time the harness's own lock-holder releases measurably spent
    inside that window (the members' bound is 90 s): config/member-close-on-interrupt,
    "pooled", "communicate",
    "leader-first" and "setsid" end in KeyboardInterrupt with no group registered (the setsid
    holder alive, out of reach of the group kill); config/member-close-handlers, "close" ends in
    KeyboardInterrupt, which _raise_held raises once after the second close, with the handler
    installed at the entry of BOTH closes and both SIGINTs deferred until then, "sigterm"
    ends in SystemExit(143), "install" in KeyboardInterrupt with SIGINT restored, and "split-T-I",
    "split-T-II", "split-TI-I" and "split-IT-I" (a SIGTERM held inside _closing_refusal_control
    and raised by _reopen_members, SIGINTs held at the entry of main()'s closes) each in
    SystemExit(143), with the signals each drain held reported, and "late-monitor" a monitor
    released only into the post-main teardown (its in_communicate observation gated, the member's
    own 10 s bound ending main() first, so main() returns 2 with the CANNOT EVALUATE line): its
    SIGINT lands inside the post-main worker joins, is absorbed by the teardown's deferring
    handler and is reported as the deliberate "late SIGINT" handshake, so the report survives a
    signal delivered during the join (in every other scenario a non-deliberate absorbed late
    signal is timing alone, never a definite finding: the run is a harness timeout,
    cannot-evaluate, with every field outside the _STATE_EVIDENCE allowlist stood down and
    the allowlist still compared, _scenario_timeout_record);
    config/member-close-lock, "lock": no KeyboardInterrupt and the lock free after the direct
    close, no close left blocked in the real-signal loop, with the lock held by another thread a
    member start and the close each give up within the lock bound (the start a recorded timeout,
    the close a problem), main() ends in KeyboardInterrupt (the SIGINT at the close's acquire is
    deferred, and _raise_held raises it once after the second close); the same scenario pins
    the child's timeout producers on deliberate quick exercises, each consumed in the child so no
    timeout is recorded: the monitor's never-sent record for both of its early returns
    ("monitor_pin": the missing and unsent entries, and that nothing was sent), the lock
    holder's own expiry record ("expiry_pin": its count and the final lock state, both read
    only AFTER release() joined the holder, so a holder paused between recording and
    releasing never pins a held lock; a producer that never records within 30 s is itself a
    recorded harness handshake, fail closed, standing the pin's fields down), and the
    reached-bound recorder over_bound on an over-budget and an under-budget value against a
    private sink ("bound_pin"), so removing any of these producers fails its pin;
    config/member-failed-no-late-launch, "failed" and "failed-signal": member b's late start is
    refused, and the ValueError propagates ("failed") or the SIGINT deferred at the failed-member
    branch's acquire is raised by _raise_held once after the second close ("failed-signal"), and
    with member b never reporting running, member a's expired handshake raises HandshakeTimeout and
    never the injected failure ("failed-handshake"); "failed-stalled" is the deterministic twin
    of a merely slow git: its one member fails WITHOUT arriving (nothing is sent, deliberately,
    _DELIBERATE_EMPTY_SEND), so a_arrived, a_raises and the send list hold the slow-git state in
    every run while every compared field stays structural, and an edit that ties an
    allowlisted field to member a's arrival or raise or to the send list, on code that run
    executes and not also dependent on any other state that differs from a slow "failed"
    run, is a definite mismatch there in a healthy run (THE RESIDUAL, stated in
    _allowlist_producers: ANY producer that neither net catches passes both nets (the value
    pin catches only by a compared field differing from its want, a missing report
    included, in a run within the scenarios' bounds, and the producer oracle only by a
    refusal or by a change to a pinned signature, such as a traced read adding a name to
    it), whatever the condition under which it changes an allowlisted field: what a slow
    positive control alters (member b's presence, the outcome text, or member a's arrival
    or raise or the send list on member b's path and run_member, which this run never
    executes), the scenario's name, a stall's length, the Python version, wall clock or
    environment, or anything else, and whatever carries it, a form the oracle neither
    traces nor refuses or a traced test whose names are all pinned already (pinned_version,
    a pure-local gate); only a run in which that condition holds can catch it, a slow
    positive control being one such run for a condition it alters);
    TIMING ALONE IS NEVER A DEFINITE FINDING
    in these scenarios: member b times the kill sweep in bounded stages (member a's raise, the
    failed-member branch entering the sweep, the harness lock holders' release, the real
    sweep's return), and every reached bound, whoever was slow, is recorded by the parent as a
    cannot-evaluate timeout (_scenario_timeout_record), failing the run closed, never passing
    it and never reading as a definite finding; the definite evidence is non-timing state the
    finished run left, read after main() ended: the outcome, the registry and recorded process
    state, the refusal records, the late-launch records, the "member_a" flags (member a's
    positive control ran and raised; the positive control's 60 s bound shapes them, and a
    wrong member_a is never definite: it leaves the send list empty, so the completion
    budget is recorded beside it and it stands down, and a pool that never ran member a is
    cannot-evaluate, fail closed, never a pass) and the "evidence"
    flags (the failed-member branch itself called the kill sweep, witnessed by the sweep
    wrapper from its caller, never by a deadline, and that caller check discriminates: a
    sweep the harness calls after main() is not counted), so a branch that never sweeps
    stays definite while a merely slow run is cannot-evaluate; the same check pins the
    sweep's ordering statically
    ("sweep-site", _sweep_call_sites): exactly one kill-sweep call inside the failed-member
    except handler, with no worker wait in the handler before it, before the finally block
    that waits for the workers, a source-order fact with no deadline involved, so the sweep
    moved after the worker wait, or a waiting shutdown placed before it in the handler
    (only a literal wait=False is exempt: a bare shutdown(), a positional
    shutdown(True, ...) and shutdown(wait=True, cancel_futures=False), whose literal False
    names another keyword, count as waits, pinned by the bare, positional and cancel
    probes), is
    definite, and the oracle's own discrimination is pinned ("sweep-oracle"); EVERY recorded
    timeout in any scenario (an expired harness handshake, _handshake_expired, whose own
    mapping is pinned there too; a reached code-owned bound in the "missing" extras; an owed
    signal never sent, "unsent"; the completion budget, main() ending within 30 s of every
    send less the measured harness stalls; the expiry pin) is graded by ONE shared rule,
    never a per-site choice: a handshake kind naming a thread its bounded join left alive
    replaces the whole observation, and otherwise every field outside the _STATE_EVIDENCE
    allowlist (the restored handlers, the free lock, the evidence flags) stands down
    (_stood_down), so a restoration defect, a held lock or a false evidence flag beside any
    timeout stays definite while timing alone never is (the "record-map" entry pins the
    reached-bound, unsent, budget, combined-failure, per-kind, outlive, expiry-pin,
    stray-bound, late-signal and slow-positive-control mappings, all classifier pins on
    hand-written observations, the _STATE_EVIDENCE tuple's exact membership verbatim and
    the stand-down of every extras key the child writes; "producer-map" pins the producers
    themselves, read from the child's source: the names every write of an allowlisted key
    reads, closed over their origins, so a child tying evidence, lock_free or restored to
    timing again through a form that oracle traces, by a read that adds a name to a
    pinned signature, fails definitively even in a healthy run, and "producer-oracle" that
    oracle's discrimination on mutated copies of the source, the residual's examples
    included at their empty diff; "slow-control" pins the stalled run's slow-control
    record, read from the child's source (_slow_control_record);
    "down-map" pins the oracle-down stand-down);
    config/member-close-unresolved-verdict, "unresolved":
    main() returns 2 with
    the CANNOT EVALUATE line and no PASS line, the group still registered, then released once the
    injection is removed; "release-timeout" and "start-timeout": a registry lock refused once and
    then free, so the closes end cleanly, still makes main() return 2 with the CANNOT EVALUATE
    line, no PASS line, no group registered and the refusal recorded; "sweep-refused",
    "close-refused", "close-wait-refused", "close-left-refused" and "failed-refused" the same for
    a refusal at the first close's kill sweep, at each of its own three acquires (the refused line
    offset matching _close_lock_sites) and at the failed-member branch, so a refusal is pinned to
    persist at all five sites; the same check requires _close_lock_sites to see exactly one
    with-form acquisition per refused-site scenario ("close-sites"), so a fourth acquisition of
    the registry lock in _close_members, a direct acquire call or a form the oracle refuses fails
    it definitively, and an oracle that cannot read _close_members at all is cannot-evaluate,
    with only the site= fields it informs stood down and every other field of the refused-site
    scenarios still compared (_oracle_down_sites, whose mapping the synthetic "down-map" entry
    pins on a probe got whatever the real oracle's health); the
    same check pins the oracle's own discrimination ("close-oracle"): a probe def named
    _close_members yields its three with-form sites, the same body renamed at the same
    recorded line raises LookupError, never a fail-open site list, and a probe spelling the lock
    through globals(), a module attribute or getattr yields "unrecognized" for each such
    reference, never a silently shorter site list.
    config/member-closing-refuses-start also requires the "closing-control" scenario:
    a real SIGINT inside the control is held there and raised right after it (KeyboardInterrupt,
    nothing after the control runs). config/member-close-signal-sweep: the _CLOSE_SWEEP_CHILD
    child exited 0 and its sweep reports no failing run in any mode (line, instruction, pair;
    SIGINT first and SIGTERM first; the plain body and the failed-member branch body), clean
    baselines and every window function reached, and the report parser accepts a clean synthetic
    report after exit 0 and refuses the same report after exit 1; the number of runs per mode is
    printed on a MEMBER CLOSE SWEEP line.
    Every process recorded here is killed afterwards whatever the code under test did
    (_kill_recorded)."""
    import contextlib
    import io
    import types
    from unittest.mock import patch

    this = _gfe
    member = fixture / "group_member.py"
    member.write_text(_GROUP_MEMBER_FIXTURE, encoding="utf-8")
    real_popen = subprocess.Popen
    threads, waits = [], []

    def spawn(*args, **kwargs):
        threads.append(threading.current_thread() is threading.main_thread())
        child = real_popen(*args, **kwargs)
        real_communicate = child.communicate

        def communicate(*c_args, **c_kwargs):
            waits.append(threading.current_thread() is threading.main_thread())
            return real_communicate(*c_args, **c_kwargs)

        child.communicate = communicate
        return child

    with patch.object(subprocess, "Popen", spawn):
        rc, _, _ = _run_bounded([sys.executable, "-I", "-B", "-c", "pass"], str(fixture),
                                dict(os.environ), 60, "off-main control", "CONTROL ", [],
                                io.StringIO())
    check("config/member-off-main-thread", (rc, threads, waits[:1], len(_MEMBERS["groups"])),
          (0, [False], [False], 0), (rc,) if _is_timeout(rc) else None)

    del threads[:]
    refused_start = (_closing_refusal_control(fixture, spawn), list(threads))

    def normal_exit(path, fallback):
        """One normal-exit member on one signalling path: (rc, recorded states, groups it left
        registered, whether the cleanup diagnostic says the group stays registered, whether each
        retained group was confirmed empty and released after the recorded survivor was killed,
        groups still registered after that)."""
        folder = fixture / "normal-exit-{}".format(path)
        folder.mkdir()
        forced = (patch.object(this, "PIDFD_SIGNAL_PROCESS_GROUP", 1 << 30) if fallback
                  else contextlib.nullcontext())
        before, diagnostic, released = set(_MEMBERS["groups"]), io.StringIO(), []
        with forced:
            try:
                rc, _, _ = _run_bounded([sys.executable, "-I", "-B", str(member), str(folder),
                                         "n", "exit"], str(folder), dict(os.environ), 120,
                                        "normal-exit control ({})".format(path), "CONTROL ", [],
                                        diagnostic)
                states = _recorded_states(folder)
                retained = [group for group in _MEMBERS["groups"] if group not in before]
            finally:
                _kill_recorded(folder)
            for group in retained:
                # On a helper thread, so the main thread never takes the registry lock while closing
                # is clear (a signal handler raising inside that acquire would leave it held).
                helper = threading.Thread(target=lambda group=group: released.append(
                    _member_group_empty(group) is None and _release_member_group(group) is None))
                helper.start()
                helper.join()
        return (rc, states, len(retained), "stays registered" in diagnostic.getvalue(), released,
                len([group for group in _MEMBERS["groups"] if group not in before]))

    retention = (0, {"n.grandchild": "alive", "n.leader": "dead"}, 1, True, [True], 0)
    got = [normal_exit("pidfd", False), normal_exit("fallback", True)]
    want = [(0, {"n.grandchild": "dead", "n.leader": "dead"}, 0, False, [], 0)
            if _pidfd_group_signal_supported() else retention, retention]
    timed_out = tuple(entry[0] for entry in got if _is_timeout(entry[0]))
    check("config/member-grandchild-after-normal-exit", got, want,
          timed_out if timed_out and all(_is_timeout(entry[0]) or entry == expected
                                         for entry, expected in zip(got, want)) else None)

    got = {}
    for scenario in ("pooled", "communicate", "leader-first", "setsid", "close", "sigterm",
                     "install", "split-T-I", "split-T-II", "split-TI-I", "split-IT-I", "lock",
                     "failed", "failed-signal", "failed-handshake", "failed-stalled",
                     "unresolved",
                     "release-timeout", "start-timeout", "sweep-refused", "close-refused",
                     "close-wait-refused", "close-left-refused", "failed-refused",
                     "closing-control", "late-monitor"):
        folder = fixture / "close-{}".format(scenario)
        folder.mkdir()
        try:
            rc, out, err = _run_bounded(
                [sys.executable, "-I", "-B", "-c", _CLOSE_PROBE_CHILD, str(ROOT / "tools"),
                 scenario, str(folder), str(member)], str(folder), dict(os.environ), 300,
                "member-close child ({})".format(scenario), "CONTROL ")
        finally:
            _kill_recorded(folder)
        if _is_timeout(rc):
            got[scenario] = rc
            continue
        report = out.rpartition("PROBE-REPORT ")[2].partition("\n")[0]
        try:
            outcome, registered, states, timely, extra = json.loads(report)
            got[scenario] = (scenario, outcome, registered, sorted(set(states.values())),
                             len(states), timely, extra)
            got[scenario] = _scenario_timeout_record(scenario, got[scenario], extra)
        except (TypeError, ValueError, AttributeError) as exc:
            got[scenario] = (scenario, "no report ({}): rc={} err={!r}".format(
                exc, rc, err[-400:]))
            continue
    got["handshake-map"] = ("handshake-map", [
        _is_timeout(_handshake_expired("failed", dict(handshake=["member b running"]))),
        _handshake_expired("failed-handshake", dict(handshake=["member b running"])) is None,
        _is_timeout(_handshake_expired("failed-handshake", dict(handshake=["member b arrived"]))),
        _handshake_expired("failed", dict()) is None])
    recorded = []
    observation = ("record-probe",)
    kept_state = dict(restored=[True, True, True], lock_free=True)
    bound_message = ("TIMEOUT: member-close child (failed-refused): code-owned bound reached: "
                     "kill sweep (cannot evaluate)")
    bound_extra = dict(missing=["kill sweep"])
    bound_recorded = []
    bound_down = _scenario_timeout_record("failed-refused", observation, bound_extra,
                                          bound_recorded)
    late_message = ("TIMEOUT: member-close child (communicate): handshake not met: "
                    "late SIGINT (cannot evaluate)")
    late_extra = dict(handshake=["late SIGINT"], lines=[False, True])
    late_want = ("communicate", "KeyboardInterrupt", 0, ["dead"], 2, True,
                 dict(restored=[True, True, True], lock_free=True, lines=[False, False]))
    late_recorded = []
    stood_down = _scenario_timeout_record(
        "communicate", ("communicate", "returned 2", 0, ["dead"], 2, True, late_extra),
        late_extra, late_recorded)
    budget_message = ("TIMEOUT: member-close child (failed): completion budget reached "
                      "(cannot evaluate)")
    budget_recorded = []
    budget_down = _scenario_timeout_record(
        "failed", ("failed", "returned 2", 0, ["dead"], 2, False, {}), {}, budget_recorded)
    unsent_message = ("TIMEOUT: member-close child (pooled): owed signal never sent: SIGINT "
                      "(cannot evaluate)")
    registered_message = ("TIMEOUT: member-close child (pooled): code-owned bound reached: "
                          "members registered (cannot evaluate)")
    unsent_extra = dict(missing=["members registered"], unsent=["SIGINT"],
                        restored=[True, True, True], lock_free=True, lines=[False, True])
    unsent_recorded = []
    unsent_down = _scenario_timeout_record(
        "pooled", ("pooled", "returned 2", 0, ["dead"], 4, False, unsent_extra),
        unsent_extra, unsent_recorded)
    in_comm_message = ("TIMEOUT: member-close child (communicate): owed signal never sent: "
                       "SIGINT (cannot evaluate)")
    in_comm_bound = ("TIMEOUT: member-close child (communicate): code-owned bound reached: "
                     "member in communicate (cannot evaluate)")
    in_comm_extra = dict(missing=["member in communicate"], unsent=["SIGINT"],
                         restored=[True, True, True], lock_free=True, lines=[False, True])
    in_comm_recorded = []
    in_comm_down = _scenario_timeout_record(
        "communicate", ("communicate", "returned 2", 0, ["dead"], 2, False, in_comm_extra),
        in_comm_extra, in_comm_recorded)
    partial_message = ("TIMEOUT: member-close child (failed-refused): handshake not met: "
                       "lock hold expired (cannot evaluate)")
    restoration_extra = dict(handshake=["lock hold expired"], restored=[False, True, False],
                             lock_free=True, lines=[False, True], refused=1,
                             caught="injected failed member a", member_a=[True, True],
                             evidence=[True, True])
    restoration_recorded = []
    restoration_down = _scenario_timeout_record(
        "failed-refused",
        ("failed-refused", "returned 2", 0, [], 0, True, restoration_extra),
        restoration_extra, restoration_recorded)
    flag_extra = dict(handshake=["lock hold expired"], restored=[True, True, True],
                      lock_free=True, lines=[False, True], refused=1, member_a=[True, True],
                      evidence=[False, True])
    flag_recorded = []
    flag_down = _scenario_timeout_record(
        "failed-refused", ("failed-refused", "returned 2", 0, [], 0, True, flag_extra),
        flag_extra, flag_recorded)
    # The stray-member registration bound of "close" and "lock" (rounds 14 to 16): the
    # member's very existence at the report is timing, so the recorded states, their count
    # and the in-run pins stand down with every other field off the allowlist, while an
    # allowlisted defect beside the reached bound stays definite.
    stray_message = ("TIMEOUT: member-close child (close): code-owned bound reached: "
                     "members registered (cannot evaluate)")
    stray_extra = dict(missing=["members registered"], restored=[True, True, True],
                       lock_free=True, lines=[False, True], protected=[True])
    stray_recorded = []
    stray_down = _scenario_timeout_record(
        "close", ("close", "KeyboardInterrupt", 0, [], 0, True, stray_extra),
        stray_extra, stray_recorded)
    stray_want = ("close", "KeyboardInterrupt", 0, ["dead"], 2, True,
                  dict(restored=[True, True, True], lock_free=True, lines=[False, False],
                       protected=[True, True]))
    stray_defect_extra = dict(missing=["members registered"], restored=[False, True, False],
                              lock_free=True, lines=[False, True])
    stray_defect = _scenario_timeout_record(
        "close", ("close", "KeyboardInterrupt", 0, [], 0, True, stray_defect_extra),
        stray_defect_extra, [])
    lock_message = ("TIMEOUT: member-close child (lock): code-owned bound reached: "
                    "members registered (cannot evaluate)")
    lock_extra = dict(missing=["members registered"], restored=[True, True, True],
                      lock_free=True, expiry_pin=[0, False],
                      monitor_pin=[[["never"], ["SIGINT"], 0]])
    lock_recorded = []
    lock_down = _scenario_timeout_record(
        "lock", ("lock", "KeyboardInterrupt", 0, [], 0, True, lock_extra),
        lock_extra, lock_recorded)
    # The expiry-pin deadline exhausted (a holder merely slow, round 16): the "expiry pin"
    # handshake is a recorded timeout, so the pin's own fields stand down on both sides and
    # a scheduling delay is never definite.
    pin_message = ("TIMEOUT: member-close child (lock): handshake not met: expiry pin "
                   "(cannot evaluate)")
    pin_extra = dict(handshake=["expiry pin"], expiry_pin=[0, False],
                     restored=[True, True, True], lock_free=True)
    pin_recorded = []
    pin_down = _scenario_timeout_record(
        "lock", ("lock", "KeyboardInterrupt", 0, ["dead"], 2, True, pin_extra),
        pin_extra, pin_recorded)
    pin_want = ("lock", "KeyboardInterrupt", 0, ["dead"], 2, True,
                dict(restored=[True, True, True], lock_free=True, expiry_pin=[1, True]))
    # Every handshake kind the child can record, graded by the one rule: a kind naming a
    # thread its bounded join left alive replaces the whole observation (a live thread can
    # poison the allowlist too), and every other kind stands down to exactly the allowlist,
    # so a restoration defect beside any such expiry stays definite. A kind moved between
    # these tuples fails its vector below.
    joined_kinds = ("lock held", "lock hold expired", "lock released", "member a raised",
                    "member b arrived", "member b running", "member records", "expiry pin",
                    "late SIGINT")
    alive_kinds = ("lock holder finished", "signal sender finished", "monitor finished",
                   "stray member finished")

    def kind_down(kind, restored):
        # Every extras key the child can write rides along, so the per-kind vectors pin
        # BEHAVIOURALLY that everything off the _STATE_EVIDENCE tuple (member_a, late,
        # direct and the rest) is dropped: admitting any of them to the allowlist fails
        # the exact-keys vector over these probes.
        kind_extra = dict(handshake=[kind], restored=restored, lock_free=True,
                          lines=[False, True], refused=1, evidence=[True, True],
                          member_a=[True, True], late=[["b", "closing"]], caught="injected",
                          direct=[None, True, []], loop=[[], True],
                          held=[True, True, [True]], run=["0", 0], released=[[], 0],
                          protected=[True], monitor_pin=[], expiry_pin=[1, True],
                          bound_pin=[], control=["closing", [], True], closes=[[]], site=2,
                          rc=0, after=True)
        return _scenario_timeout_record(
            "failed-refused", ("failed-refused", "returned 2", 0, [], 0, True, kind_extra),
            kind_extra, [])
    # The slow positive control (round 17): member a's git call reaching its 60 s
    # subprocess bound leaves member_a [False, False] while member b's 120 s wait records
    # the "member a observed" bound and the empty send list reaches the completion budget;
    # the TimeoutExpired raised INTO the failed-member handler, which swept, so the
    # allowlisted evidence matches its want, member_a stands down on both sides and the
    # verdict is cannot-evaluate, never a definite mismatch (the classifier vector below,
    # a pin of the mapping on this hand-written observation, not of the child's
    # producer, which "producer-map" pins), while a false evidence flag beside the same
    # record stays definite (the flag_down vector above).
    slow_bound = ("TIMEOUT: member-close child (failed): code-owned bound reached: "
                  "member a observed (cannot evaluate)")
    slow_budget = ("TIMEOUT: member-close child (failed): completion budget reached "
                   "(cannot evaluate)")
    slow_extra = dict(missing=["member a observed"], restored=[True, True, True],
                      lock_free=True, lines=[False, True], late=[],
                      member_a=[False, False], evidence=[True, True])
    slow_recorded = []
    slow_down = _scenario_timeout_record(
        "failed", ("failed", "TimeoutExpired: positive control", 0, [], 0, False,
                   slow_extra), slow_extra, slow_recorded)
    slow_want = ("failed", "ValueError: injected failed member a", 0, [], 0, True,
                 dict(restored=[True, True, True], lock_free=True, lines=[False, False],
                      late=[["b", "the suite is closing: no new member starts"]],
                      member_a=[True, True], evidence=[True, True]))
    stalled_extra = dict(restored=[True, True, True], lock_free=True, lines=[False, False],
                         late=[], member_a=[False, False], evidence=[True, True])
    stalled_obs = ("failed-stalled", "ValueError: injected failed member a", 0, [], 0,
                   False, stalled_extra)
    stalled_recorded = []
    stalled_kept = _scenario_timeout_record("failed-stalled", stalled_obs,
                                            dict(stalled_extra), stalled_recorded)
    stalled_bound = ("TIMEOUT: member-close child (failed-stalled): code-owned bound "
                     "reached: positive control a observed (cannot evaluate)")
    stalled_slow_extra = dict(missing=["positive control a observed"],
                              restored=[True, True, True], lock_free=True,
                              lines=[False, False], late=[], member_a=[False, False],
                              evidence=[True, True])
    stalled_slow_recorded = []
    stalled_slow_down = _scenario_timeout_record(
        "failed-stalled",
        ("failed-stalled", "TimeoutExpired: positive control", 0, [], 0, False,
         stalled_slow_extra), stalled_slow_extra, stalled_slow_recorded)
    got["record-map"] = ("record-map", [
        # A reached code-owned bound ("missing") is a recorded timeout like any other: the
        # probe observation (not a report tuple, so nothing to stand down) becomes the
        # message, the entry is popped and recorded.
        bound_down == bound_message and bound_extra == {}
        and bound_recorded == [bound_message],
        # A reached code-owned bound beside an expired harness handshake: both timeouts are
        # recorded, in that order.
        _is_timeout(_scenario_timeout_record("failed-refused", observation,
                                             dict(missing=["kill sweep"],
                                                  handshake=["lock held"]), recorded)),
        recorded == [bound_message,
                     "TIMEOUT: member-close child (failed-refused): handshake not met: "
                     "lock held (cannot evaluate)"],
        _is_timeout(_scenario_timeout_record("failed-refused", observation,
                                             dict(handshake=["lock held"]), recorded)),
        # A scenario's own deliberate handshake is no timeout: the observation passes
        # through untouched.
        _scenario_timeout_record("failed-handshake", observation,
                                 dict(handshake=["member b running"]), recorded) is observation,
        # A non-deliberate signal absorbed after main() is timing alone, whoever was slow:
        # recorded, and the observation stands down to the allowlist (nothing of it here).
        stood_down == ("communicate",) + (late_message,) * 5 + (
            dict(stood_down=late_message),)
        and late_recorded == [late_message],
        # A False timeliness field (the completion budget reached) is timing alone:
        # recorded, and stood down by the same one rule.
        budget_down == ("failed",) + (budget_message,) * 5 + (
            dict(stood_down=budget_message),)
        and budget_recorded == [budget_message],
        # The wants-side mirror: a stood-down entry reshapes its want with the same
        # allowlist, and every other observation leaves its want unchanged.
        _stood_down_wants([stood_down, budget_down, observation],
                          [late_want, late_want, late_want])
        == [("communicate",) + (late_message,) * 5 + (
                dict(kept_state, stood_down=late_message),),
            ("communicate",) + (budget_message,) * 5 + (
                dict(kept_state, stood_down=budget_message),),
            late_want],
        # One more expired handshake in the same run is recorded too (the probe is not a
        # report tuple, so the message comes back).
        _is_timeout(_scenario_timeout_record("communicate", observation,
                                             dict(handshake=["late SIGINT", "lock held"]),
                                             recorded)),
        len(recorded) == 4,
        # An owed signal the monitor never sent: the reached bound and the unsent signal are
        # both recorded (never also the completion budget: the empty send list is not a late
        # finish), and the observation stands down to the allowlist.
        unsent_down == ("pooled",) + (unsent_message,) * 5 + (
            dict(kept_state, stood_down=unsent_message),)
        and unsent_recorded == [registered_message, unsent_message],
        in_comm_down == ("communicate",) + (in_comm_message,) * 5 + (
            dict(kept_state, stood_down=in_comm_message),)
        and in_comm_recorded == [in_comm_bound, in_comm_message],
        # A lock holder that released at its bound: the allowlist stays compared, so a
        # restoration defect or a false evidence flag beside the expiry is still definite.
        restoration_down == ("failed-refused",) + (partial_message,) * 5 + (
            dict(restored=[False, True, False], lock_free=True,
                 evidence=[True, True], stood_down=partial_message),)
        and restoration_recorded == [partial_message],
        flag_down[6].get("evidence") == [False, True]
        and flag_recorded == [partial_message],
        # The wants-side mirrors for the unsent and the holder-expiry stand-down.
        _stood_down_wants(
            [unsent_down, restoration_down],
            [("pooled", "KeyboardInterrupt", 0, ["dead"], 4, True,
              dict(restored=[True, True, True], lock_free=True, lines=[False, False])),
             ("failed-refused", "returned 2", 0, [], 0, True,
              dict(restored=[True, True, True], lock_free=True, lines=[False, True],
                   refused=1, evidence=[True, True]))])
        == [("pooled",) + (unsent_message,) * 5 + (
                dict(kept_state, stood_down=unsent_message),),
            ("failed-refused",) + (partial_message,) * 5 + (
                dict(kept_state, evidence=[True, True],
                     stood_down=partial_message),)],
        # The stray-member registration bound of "close": the states, their count and every
        # extras key off the allowlist stand down, the mirror makes the timing-only
        # difference neutral, and an allowlisted restoration defect beside the same bound
        # stays definite.
        stray_down == ("close",) + (stray_message,) * 5 + (
            dict(kept_state, stood_down=stray_message),)
        and stray_recorded == [stray_message],
        _stood_down_wants([stray_down], [stray_want]) == [stray_down],
        stray_defect[6].get("restored") == [False, True, False]
        and _stood_down_wants([stray_defect], [stray_want])[0][6].get("restored")
        == [True, True, True],
        # The same bound in "lock" stands the in-run pins down too.
        "expiry_pin" not in lock_down[6] and "monitor_pin" not in lock_down[6]
        and lock_down[1] == lock_message and lock_recorded == [lock_message],
        # The expiry-pin deadline exhausted: the pin's fields stand down on both sides, so
        # a merely slow holder is cannot-evaluate, never definite.
        "expiry_pin" not in pin_down[6] and pin_recorded == [pin_message]
        and _stood_down_wants([pin_down], [pin_want]) == [pin_down],
        # Per kind: a restoration defect beside any joined-thread expiry stays definite,
        # the kept keys are exactly the allowlist plus the marker, and a kind naming a
        # thread its join left alive replaces the whole observation even beside that defect.
        all(kind_down(kind, [False, True, False])[6].get("restored") == [False, True, False]
            for kind in joined_kinds),
        all(sorted(kind_down(kind, [True, True, True])[6])
            == ["evidence", "lock_free", "restored", "stood_down"] for kind in joined_kinds),
        all(_is_timeout(kind_down(kind, [False, True, False])) for kind in alive_kinds),
        # The allowlist's exact membership, pinned VERBATIM: adding ANY key (an existing
        # child field such as "late", "direct" or "member_a" included) or removing one
        # fails here, beside the behavioural per-kind vector above.
        _STATE_EVIDENCE == ("evidence", "lock_free", "restored"),
        # The slow positive control: both bounds recorded, member_a dropped, the
        # allowlisted evidence kept at its want.
        slow_down == ("failed",) + (slow_budget,) * 5 + (
            dict(kept_state, evidence=[True, True], stood_down=slow_budget),)
        and slow_recorded == [slow_bound, slow_budget],
        # Classifier pin (the mapping, not the producer): the mirrored want equals the
        # stood-down observation, so a merely slow positive control whose evidence is at its
        # want is cannot-evaluate, never a definite mismatch. That the child's evidence IS
        # at its want under a slow positive control rests on "producer-map" and the
        # "failed-stalled" value pin, short of THE RESIDUAL (_allowlist_producers).
        _stood_down_wants([slow_down], [slow_want]) == [slow_down],
        # The deliberate empty send list (round 20): "failed-stalled" owes no signal by
        # construction, so its False timeliness field alone records nothing and the whole
        # observation stays compared; the exemption is scenario-keyed, pinned verbatim,
        # and the SAME shape under "failed" is the budget stand-down above (budget_down).
        stalled_kept is stalled_obs and stalled_recorded == [],
        _DELIBERATE_EMPTY_SEND == ("failed-stalled",),
        # Any OTHER record in a stalled run (here the slow positive control's reached
        # bound) still stands it down to the allowlist, the budget never doubling it.
        stalled_slow_down == ("failed-stalled",) + (stalled_bound,) * 5 + (
            dict(kept_state, evidence=[True, True], stood_down=stalled_bound),)
        and stalled_slow_recorded == [stalled_bound],
        _stood_down_wants([stalled_slow_down],
                          [("failed-stalled", "ValueError: injected failed member a", 0,
                            [], 0, False, dict(stalled_extra))]) == [stalled_slow_down]])
    # The allowlist's PRODUCER pin, beside the classifier pins above: the names every write
    # of an allowlisted extras key reads in the child source, closed over their origins
    # (_allowlist_producers), so a child tying a kept field to timing again through a form
    # the oracle traces, by a read that adds a name to a pinned signature, fails here even
    # in a healthy run, where no timeout exercises the classifier (the "failed-stalled"
    # value pin is the second net, and THE RESIDUAL in _allowlist_producers is what
    # neither net catches). Its own discrimination
    # ("producer-oracle"): each probe rewrites one statement of the child source and must
    # change exactly the named entries, so each rule named here has a probe built to fail
    # when that rule regresses (a rule not named here is not pinned by this list):
    # evidence tied to member a's arrival
    # (conj), the round-17 flags re-added (flag), the sweep wrapper's append gated on
    # member a's arrival (guard), the lock read tied to the measured stalls (lock), a
    # restoration flag tied to the send list (restored), an allowlisted key written through
    # an alias (alias) or through ** arguments (star); the gate spelled as a short-circuit
    # (boolop), a while test (while), a conditional expression (condexp), a walrus
    # (walrus), a global rebinding inside a def (rebound), a slice-store index (sliceidx);
    # a carrier erased or escaping: del (removal), clear (clear), pop on restored (popper),
    # an alias taken (escape), the bare name passed to a helper (callarg); the extras dict
    # reached reflectively (reflect), by a dynamic key (dynkey), rebound (rebind),
    # augmented (augstore), an allowlisted key aliased (keyref), a bound method taken
    # (uncalled), the dict used bare (bare); an origin augmented from the send list
    # (augorigin); each _PRODUCER_MUTATORS entry traced (extendm, insertm, addm, updatem,
    # setdefm); getattr aimed at a non-module (getat); a call argument traced when passed
    # by keyword (kwcallarg) or starred (starcallarg), a mutator's and an allowlisted
    # writer's keyword arguments traced (kwmutator, kwwriter); each carrier literal type
    # besides the list (dictlit, setlit, listcomp, setcomp, dictcomp); the two disclosed
    # boundaries pinned AT their empty diff, a module attribute store (moduleattr) and a
    # for-loop gate on member a's arrival (forloop, which the "failed-stalled" value pin
    # catches); and examples of THE RESIDUAL pinned AT their empty diff, which pass the
    # value pin too (examples, not the class: _allowlist_producers states the class, ANY
    # producer that neither net catches, the value pin catching only by a compared field
    # differing from its want (a missing report included) in a run within the scenarios'
    # bounds and this oracle only by a refusal or by a change to a pinned signature, such
    # as a traced read adding a name to it, whatever the condition and whatever carries
    # it): round 20's R1 (raisegate), R2 (trygate) and R4 (sendgate) in run_member, which
    # the stalled run never calls (its late=[]); four gates on facts that differ between
    # the stalled run and a slow "failed" run, round 20's on the scenario's name
    # (scenariogate), round 21's on member b's absence (bgate) and on the outcome text with
    # member a's arrival (outcomegate), and round 22's on the outcome text alone
    # (outcomeonlygate); round 24's on a stall's length (stallgate), a condition no
    # scenario arranges and a slow positive control does not produce; and round 25's traced
    # if whose reads are all pinned already (pinned_version: a test on sys.version_info
    # before the evidence read, adding no name to the signature). An anchor no longer in
    # the source is "anchor missing", never a silently clean probe. ast.parse alone reads
    # each source; no probe source is executed.
    witnessed_line = "    witnessed = len(branch_swept)\n"
    late_line = "        late.append([member_argv[2], rc])\n"
    evidence_line = "    extra['evidence'] = [bool(branch_swept)]\n"
    producer_probes = ((
        ("conj", "extra['evidence'] = [bool(branch_swept)]",
         "extra['evidence'] = [a_arrived.is_set() and bool(branch_swept)]"),
        ("flag", "extra['evidence'] = [bool(branch_swept)]",
         "extra['evidence'] = [a_arrived.is_set(), a_raises.is_set(), bool(branch_swept)]"),
        ("guard", "        if frame is not None:\n",
         "        if frame is not None and a_arrived.is_set():\n"),
        ("lock", "    free = suite._MEMBERS_LOCK.acquire(0)\n",
         "    free = suite._MEMBERS_LOCK.acquire(0) and not stalls\n"),
        ("restored", "    restored.append(False)\n", "    restored.append(bool(sent))\n"),
        ("alias", "extra['evidence'].append(", "extra.get('evidence').append("),
        ("star", witnessed_line,
         witnessed_line + "    extra.update(**dict(evidence=[True]))\n"),
        ("boolop", "            branch_swept.append(True)\n",
         "            a_arrived.is_set() and branch_swept.append(True)\n"),
        ("while", "extra['evidence'] = [bool(branch_swept)]",
         "while not a_arrived.is_set():\n        extra['evidence'] = "
         "[bool(branch_swept)]\n        break"),
        ("condexp", "            branch_swept.append(True)\n",
         "            (branch_swept.append(True) if a_arrived.is_set() else None)\n"),
        ("walrus", witnessed_line, witnessed_line
         + "    (branch_swept := [] if a_arrived.is_set() else list(branch_swept))\n"),
        ("rebound", witnessed_line, witnessed_line
         + "    def _drop():\n        global branch_swept\n        branch_swept = "
         "list(branch_swept) if a_arrived.is_set() else []\n"),
        ("sliceidx", witnessed_line,
         witnessed_line + "    branch_swept[a_arrived.is_set():] = []\n"),
        ("removal", witnessed_line, witnessed_line + "    del branch_swept[:]\n"),
        ("clear", witnessed_line, witnessed_line + "    branch_swept.clear()\n"),
        ("popper", "    restored.append(False)\n",
         "    restored.append(False)\n    restored.pop()\n"),
        ("escape", witnessed_line, witnessed_line + "    sweep_alias = branch_swept\n"),
        ("callarg", witnessed_line, witnessed_line + "    _inspect(branch_swept)\n"),
        ("reflect", witnessed_line,
         witnessed_line + "    globals()['extra']['evidence'] = [True]\n"),
        ("dynkey", witnessed_line,
         witnessed_line + "    extra[str('evidence')] = [True]\n"),
        ("rebind", witnessed_line, witnessed_line + "    extra = {}\n"),
        ("augstore", witnessed_line,
         witnessed_line + "    extra['evidence'] += [a_arrived.is_set()]\n"),
        ("keyref", witnessed_line, witnessed_line + "    _flags = extra['evidence']\n"),
        ("uncalled", witnessed_line, witnessed_line + "    _writer = extra.update\n"),
        ("bare", witnessed_line, witnessed_line + "    _record = extra\n"),
        ("augorigin", witnessed_line, witnessed_line + "    witnessed += len(sent)\n"),
        ("extendm", witnessed_line,
         witnessed_line + "    branch_swept.extend([a_arrived.is_set()])\n"),
        ("insertm", witnessed_line,
         witnessed_line + "    branch_swept.insert(0, a_arrived.is_set())\n"),
        ("addm", witnessed_line,
         witnessed_line + "    branch_swept.add(a_arrived.is_set())\n"),
        ("updatem", witnessed_line,
         witnessed_line + "    branch_swept.update(a_arrived.is_set())\n"),
        ("setdefm", witnessed_line,
         witnessed_line + "    branch_swept.setdefault(a_arrived.is_set())\n"),
        ("getat", witnessed_line,
         witnessed_line + "    getattr(witnessed, 'bit_length')()\n"),
        ("kwcallarg", witnessed_line, witnessed_line + "    _inspect(into=branch_swept)\n"),
        ("starcallarg", witnessed_line, witnessed_line + "    _inspect(*branch_swept)\n"),
        ("kwmutator", witnessed_line,
         witnessed_line + "    branch_swept.update(flag=a_arrived.is_set())\n"),
        ("kwwriter", witnessed_line,
         witnessed_line + "    extra['evidence'].extend(items=[a_arrived.is_set()])\n"))
        + tuple((kind, witnessed_line, witnessed_line + "    _held = " + literal + "\n"
                 "    witnessed += len(_held)\n    _held.clear()\n")
                for kind, literal in (("dictlit", "{}"), ("setlit", "{0}"),
                                      ("listcomp", "[0 for _ in ()]"),
                                      ("setcomp", "{0 for _ in ()}"),
                                      ("dictcomp", "{0: 0 for _ in ()}")))
        + (("moduleattr", witnessed_line,
            witnessed_line + "    sys.a_flag = a_arrived.is_set()\n"),
           ("forloop", witnessed_line, witnessed_line
            + "    for _ in range(0 if a_arrived.is_set() else 1):\n"
            "        branch_swept[:] = []\n"),
           ("raisegate", late_line, late_line
            + "        for _ in () if a_raises.is_set() else (0,):\n"
            "            branch_swept[:] = []\n"),
           ("trygate", late_line, late_line
            + "        try:\n            assert a_raises.is_set()\n"
            "        except AssertionError:\n            branch_swept[:] = []\n"),
           ("sendgate", late_line, late_line
            + "        for _ in () if sent else (0,):\n            branch_swept[:] = []\n"),
           ("scenariogate", evidence_line,
            "    for _ in range(0 if scenario == 'failed-stalled' or a_arrived.is_set()"
            " else 1):\n        branch_swept[:] = []\n" + evidence_line),
           ("bgate", evidence_line,
            "    for _ in () if a_arrived.is_set() or not b_arrived.is_set() else (0,):\n"
            "        branch_swept[:] = []\n" + evidence_line),
           ("outcomegate", evidence_line,
            "    for _ in range(1 if outcome.startswith('TimeoutExpired')"
            " and not a_arrived.is_set() else 0):\n        branch_swept[:] = []\n"
            + evidence_line),
           ("outcomeonlygate", evidence_line,
            "    for _ in range(1 if outcome.startswith('TimeoutExpired') else 0):\n"
            "        branch_swept[:] = []\n" + evidence_line),
           ("stallgate", evidence_line,
            "    for _ in range(1 if stalls and max(stalls) > 0.5 else 0):\n"
            "        branch_swept[:] = []\n" + evidence_line),
           ("pinned_version", evidence_line,
            "    if sys.version_info >= (3, 99):\n        branch_swept[:] = []\n"
            + evidence_line)))
    try:
        producers = _allowlist_producers()
        got["producer-map"] = ("producer-map", producers)
        producer_oracle = []
        for _, anchor, mutant in producer_probes:
            if _CLOSE_PROBE_CHILD.count(anchor) != 1:
                producer_oracle.append("anchor missing")
                continue
            probe = _allowlist_producers(_CLOSE_PROBE_CHILD.replace(anchor, mutant))
            producer_oracle.append(sorted(key for key in probe if probe[key] != producers[key]))
        got["producer-oracle"] = ("producer-oracle", producer_oracle)
    except LookupError as exc:
        producer_down = "TIMEOUT: allowlist producer oracle: {} (cannot evaluate)".format(exc)
        CANNOT_EVALUATE.append(producer_down)
        got["producer-map"] = got["producer-oracle"] = producer_down
    # The slow-control record (_slow_control_record): no healthy run reaches it, so it is
    # read from the source, and each probe must name the fact it breaks; every condition
    # of the reader whose removal weakens it has a probe built to fail when that condition
    # is removed, except the binding forms named after the probes. The probes: the gate
    # replaced by if False (round 20's NOREC), the record replaced by pass, the
    # enclosing failed-scenario block disabled, outcome rebound ahead of the gate, the
    # exception outcome respelled; round 21's earlier
    # handlers that catch TimeoutExpired ahead of the Exception handler (earlyhandler,
    # ahead of KeyboardInterrupt; timeouthandler, ahead of Exception), the same through a
    # try nested in the body (innertry), a second statement rebinding outcome in the
    # Exception handler (handlerextra), that handler's type changed (handlertype), a
    # finally rebinding outcome (finallystore), a second module-level try calling main()
    # (secondrun), outcome bound outside that try by an import alias (importbind), a del
    # (delbind) or a match capture (matchbind), a second record deeper in the tree
    # (duprecord), the gate moved into a def that is never called (nested) or into the
    # failed block's else (elsebranch), that block spelled as a while (whileouter),
    # scenario rebound ahead of the gate (scenariorebind) or bound by another statement
    # (scenariosplit); and the record removed through the key: popped after the gate
    # (popmissing), deleted (delmissing), popped inside the sweep wrapper the failed block
    # calls after the gate (sweeppop), cleared through setdefault (setdefclear), replaced
    # by update (updatemissing), popped as an append receiver (popappend), cleared
    # through the append method taken uncalled (boundappend) and through that method
    # passed to a call it is not the callee of (lambdaappend: pins that the append is
    # called); and outcome bound by a plain import (plainimport) and a dotted one
    # (dottedimport: pins that a dotted import binds its first name). The other binding
    # forms the reader reads (a def or async def, class or except-as name, a match star
    # or mapping rest, a parameter) have no probe of their own. Five conditions have no
    # probe because removing one does not weaken the reader: in appended, isinstance(call,
    # ast.Call) (the dump comparison beside it implies it) and isinstance(receiver,
    # ast.Attribute) (without it the reader accepts nothing more: a receiver that is no
    # attribute crashes it); in the run selector, isinstance(node, ast.Try) (without it
    # the reader is only stricter) and isinstance(call.func, ast.Attribute) (without it
    # the reader accepts nothing more: a call of a bare name it reaches crashes it); and
    # isinstance(test, ast.If) in the lock scope (without it the reader crashes on the
    # committed child, and keeping only a test check admits just a while on the same
    # test, whose body also runs only in the lock scenario).
    # Last, seven instances of routes the reader does NOT read (instances, not the
    # class, which the NOT READ text of _slow_control_record states by construction),
    # held AT [] so a reader that starts catching one must update that text: an
    # exception name the handlers use rebound ahead of the try (exceptbind), contextlib
    # rebound by a class whose redirect_stdout turns a TimeoutExpired into RuntimeError
    # on exit (ctxclass), outcome stored through the child's own module attribute
    # (mainattr) and through sys's, then bound by a wildcard import (wildcard), an
    # assert after the record that ends the child before its report (exitafter), the
    # exception class's __name__ stored ahead of the try (clsname), and outcome written
    # through a function's __globals__ ahead of the gate (funcglobals). No probe source
    # is executed.
    stalled_gate = ("    if scenario == 'failed-stalled' and "
                    "outcome.startswith('TimeoutExpired'):\n")
    stalled_record = ("        extra.setdefault('missing', [])"
                      ".append('positive control a observed')\n")
    failed_block = "registered = len(suite._MEMBERS['groups'])\nif scenario.startswith('failed')"
    raised_outcome = "    outcome = '{}: {}'.format(type(exc).__name__, exc)\n"
    raised_handler = "except Exception as exc:\n" + raised_outcome
    interrupt_handler = "except KeyboardInterrupt:\n    outcome = 'KeyboardInterrupt'\n"
    returned_line = "        outcome = 'returned {}'.format(suite.main(None, lanes))\n"
    scenario_line = "scenario, folder, fixture = sys.argv[2], sys.argv[3], sys.argv[4]\n"
    released_line = "    extra['released'] = [problems, len(suite._MEMBERS['groups'])]\n"
    # The gate with its record as one span ("" when either is not found, which then
    # counts as an anchor missing).
    gate_start = _CLOSE_PROBE_CHILD.find(stalled_gate)
    gate_end = _CLOSE_PROBE_CHILD.find(stalled_record, max(gate_start, 0))
    gate_span = (_CLOSE_PROBE_CHILD[gate_start:gate_end + len(stalled_record)]
                 if gate_start >= 0 and gate_end >= 0 else "")
    hidden = "outcome = outcome.replace('TimeoutExpired', 'slow')\n"
    control_probes = (
        ("norec", stalled_gate, "    if False:\n"),
        ("passrecord", stalled_record, "        pass\n"),
        ("failedoff", failed_block, failed_block + " and False"),
        ("rebound", stalled_gate, "    outcome = 'returned 0'\n" + stalled_gate),
        ("respelled", raised_outcome, "    outcome = 'raised: {}'.format(exc)\n"),
        ("earlyhandler", interrupt_handler,
         "except suite.subprocess.TimeoutExpired as exc:\n    outcome = 'slow: ' + str(exc)\n"
         + interrupt_handler),
        ("timeouthandler", raised_handler,
         "except suite.subprocess.TimeoutExpired:\n    outcome = 'hidden timeout'\n"
         + raised_handler),
        ("innertry", returned_line,
         "        try:\n    " + returned_line
         + "        except suite.subprocess.TimeoutExpired:\n"
         "            outcome = 'returned 2'\n"),
        ("handlerextra", raised_outcome, raised_outcome + "    " + hidden),
        ("handlertype", raised_handler, "except LookupError as exc:\n" + raised_outcome),
        ("finallystore", raised_outcome, raised_outcome + "finally:\n    " + hidden),
        ("secondrun", "ended = time.monotonic()\n",
         "try:\n    suite.main(None, [])\nexcept Exception:\n    pass\n"
         "ended = time.monotonic()\n"),
        ("importbind", stalled_gate, "    import os as outcome\n" + stalled_gate),
        ("delbind", stalled_gate, "    del outcome\n" + stalled_gate),
        ("matchbind", stalled_gate,
         "    match 'returned 0':\n        case outcome:\n            pass\n" + stalled_gate),
        ("duprecord", released_line, released_line + "    if False:\n" + stalled_record),
        ("nested", gate_span,
         "    def _later():\n        if scenario.startswith('failed'):\n        "
         + gate_span[:-1].replace("\n", "\n        ") + "\n"),
        ("elsebranch", stalled_gate, "    pass\nelse:\n" + stalled_gate),
        ("whileouter", failed_block, failed_block.replace("\nif ", "\nwhile ")),
        ("scenariorebind", stalled_gate, "    scenario = 'failed'\n" + stalled_gate),
        ("scenariosplit", scenario_line,
         "scenario = sys.argv[2]\nfolder, fixture = sys.argv[3], sys.argv[4]\n"),
        ("popmissing", stalled_record, stalled_record + "    extra.pop('missing', None)\n"),
        ("delmissing", stalled_record, stalled_record + "    del extra['missing']\n"),
        ("sweeppop", "        released.set()\n",
         "        released.set()\n        extra.pop('missing', None)\n"),
        ("setdefclear", stalled_record,
         stalled_record + "    extra.setdefault('missing', []).clear()\n"),
        ("updatemissing", stalled_record, stalled_record + "    extra.update(missing=[])\n"),
        ("popappend", stalled_record,
         stalled_record + "    extra.pop('missing', []).append(0)\n"),
        ("boundappend", stalled_record,
         stalled_record + "    extra.setdefault('missing', []).append.__self__.clear()\n"),
        ("lambdaappend", stalled_record, stalled_record
         + "    (lambda f: f.__self__.clear())(extra.setdefault('missing', []).append)\n"),
        ("plainimport", stalled_gate, "    import outcome\n" + stalled_gate),
        ("dottedimport", stalled_gate, "    import outcome.path\n" + stalled_gate),
        ("exceptbind", "captured = io.StringIO()\n",
         "if scenario == 'failed-stalled':\n"
         "    KeyboardInterrupt = suite.subprocess.TimeoutExpired\n"
         "captured = io.StringIO()\n"),
        ("ctxclass", "captured = io.StringIO()\n",
         "real_redirect = contextlib.redirect_stdout\n"
         "class contextlib:\n"
         "    class redirect_stdout:\n"
         "        def __init__(self, target):\n"
         "            self.inner = real_redirect(target)\n"
         "        def __enter__(self):\n"
         "            return self.inner.__enter__()\n"
         "        def __exit__(self, kind, exc, tb):\n"
         "            self.inner.__exit__(kind, exc, tb)\n"
         "            if kind is not None and issubclass(\n"
         "                    kind, suite.subprocess.TimeoutExpired):\n"
         "                raise RuntimeError('slow')\n"
         "captured = io.StringIO()\n"),
        ("mainattr", stalled_gate,
         "    import __main__\n    __main__." + hidden + stalled_gate),
        ("wildcard", stalled_gate,
         "    sys." + hidden + "    from sys import *\n" + stalled_gate),
        ("exitafter", stalled_record,
         stalled_record + "    assert not outcome.startswith('TimeoutExpired')\n"),
        ("clsname", "captured = io.StringIO()\n",
         "if scenario == 'failed-stalled':\n"
         "    suite.subprocess.TimeoutExpired.__name__ = 'Slow'\n"
         "captured = io.StringIO()\n"),
        ("funcglobals", stalled_gate,
         "    def namespace_handle():\n        pass\n"
         "    namespace_handle.__globals__['outcome'] = "
         "outcome.replace('TimeoutExpired', 'slow')\n" + stalled_gate))
    try:
        got["slow-control"] = ("slow-control", [_slow_control_record()] + [
            _slow_control_record(_CLOSE_PROBE_CHILD.replace(anchor, mutant))
            if _CLOSE_PROBE_CHILD.count(anchor) == 1 else "anchor missing"
            for _, anchor, mutant in control_probes])
    except LookupError as exc:
        control_down = "TIMEOUT: slow-control reader: {} (cannot evaluate)".format(exc)
        CANNOT_EVALUATE.append(control_down)
        got["slow-control"] = control_down
    # One with-form acquisition per refused-site scenario: a fourth acquisition, a direct acquire
    # call or a form the oracle refuses fails the "close-sites" pin definitively; an oracle that
    # cannot read _close_members at all is recorded as cannot-evaluate, never fail-open, and
    # stands down ONLY the site= comparisons it informs: each refused-site scenario's own
    # observation keeps every definite field (_oracle_down_sites).
    refused_site_scenarios = ("close-refused", "close-wait-refused", "close-left-refused")
    try:
        close_sites = _close_lock_sites()
        with_sites = [offset for offset, form in close_sites if form == "with"]
        got["close-sites"] = ("close-sites", [form for _, form in close_sites])
        site_wants = [with_sites[1:2] and with_sites[1], with_sites[2:3] and with_sites[2]]
    except LookupError as exc:
        oracle_down = "TIMEOUT: member-close lock-site oracle: {} (cannot evaluate)".format(exc)
        CANNOT_EVALUATE.append(oracle_down)
        site_wants = _oracle_down_sites(got, oracle_down)
    # The oracle's own mutants: a probe def NAMED _close_members read from a fixture file yields
    # its three with-form sites; the same body under another name at the SAME line must raise
    # LookupError (cannot-evaluate), so the oracle never accepts a renamed def at _close_members'
    # recorded line; and a probe spelling the lock indirectly (a globals() subscript on the
    # string constant, a module-attribute access, getattr on the string) must yield
    # "unrecognized" for every indirect reference, never a silently shorter site list. compile()
    # alone builds the probe functions; no fixture code is executed.
    plain = 3 * "    with _MEMBERS_LOCK:\n        pass\n"
    indirect = ("    with _MEMBERS_LOCK:\n        pass\n"
                "    with globals()['_MEMBERS_LOCK']:\n        pass\n"
                "    with sys.modules[__name__]._MEMBERS_LOCK:\n        pass\n"
                "    getattr(sys, '_MEMBERS_LOCK')._lock.acquire()\n")
    oracle = []
    for label, name, body in (("named", "_close_members", plain),
                              ("renamed", "unrelated", plain),
                              ("indirect", "_close_members", indirect)):
        source = "def {}():\n".format(name) + body
        path = fixture / "close-oracle-{}.py".format(label)
        path.write_text(source, encoding="utf-8")
        probe_code = next(const for const in compile(source, str(path), "exec").co_consts
                          if isinstance(const, types.CodeType))
        try:
            oracle.append(_close_lock_sites(types.FunctionType(probe_code, {})))
        except LookupError:
            oracle.append("LookupError")
    got["close-oracle"] = ("close-oracle", oracle)

    # The oracle-down stand-down's own control (_oracle_down_sites on a synthetic got): ONLY the
    # site= entries and the "close-sites" entry become the oracle's timeout message; a wrong
    # definite field in the same observation (refused=0 here) is untouched, so its mismatch
    # stays definite, and a scenario that never recorded a site entry keeps that absence.
    down_message = "TIMEOUT: member-close lock-site oracle: probe (cannot evaluate)"
    down_got = dict([
        ("close-wait-refused", ("close-wait-refused", "returned 2", 0, [], 0, True,
                                dict(refused=0, site=2))),
        ("close-left-refused", ("close-left-refused", "returned 2", 0, [], 0, True,
                                dict(refused=1)))])
    down_wants = _oracle_down_sites(down_got, down_message)
    got["down-map"] = ("down-map", [
        down_wants == [down_message, down_message],
        down_got["close-sites"] == down_message,
        down_got["close-wait-refused"][6] == dict(refused=0, site=down_message),
        down_got["close-left-refused"][6] == dict(refused=1)])

    # The kill-sweep ordering pin, a source-order fact with no deadline involved: exactly one
    # call of _signal_member_groups in _config_results, inside the failed-member except
    # handler of the try statement whose finally block waits for the workers, with no worker
    # wait in the handler before it, so the sweep precedes every wait structurally; the sweep
    # moved after the wait, a waiting shutdown placed before it in the handler, or the sweep
    # removed, fails this definitively. An oracle that cannot read _config_results is
    # cannot-evaluate, never fail-open. Its own mutants: a probe def NAMED _config_results
    # with the call so placed yields its one "except-before-wait" site, the call moved after
    # the wait into the finally block is "unrecognized", a waiting shutdown placed in the
    # handler BEFORE the call is "unrecognized" too, whether it spells wait=True, a bare
    # shutdown(), a positional shutdown(True, ...) or wait=True beside cancel_futures=False
    # (only a literal wait=False is exempt, so a detector counting only a literal
    # wait=True fails the bare and positional probes, and one exempting a literal False
    # on any keyword fails the cancel probe), a handler whose finally never waits is
    # "unrecognized" (so a neutralised finally-wait detector is loud), and the same body
    # under another name at the SAME recorded line raises LookupError. compile() alone
    # builds the probe functions; no fixture code is executed.
    ordered = ("    try:\n        pass\n    except BaseException:\n"
               "        _signal_member_groups()\n        raise\n"
               "    finally:\n        pool.shutdown(wait=True)\n")
    moved = ("    try:\n        pass\n    finally:\n"
             "        pool.shutdown(wait=True)\n        _signal_member_groups()\n")
    waitfirst = ("    try:\n        pass\n    except BaseException:\n"
                 "        pool.shutdown(wait=True)\n        _signal_member_groups()\n"
                 "        raise\n"
                 "    finally:\n        pool.shutdown(wait=True)\n")
    nowait = ("    try:\n        pass\n    except BaseException:\n"
              "        _signal_member_groups()\n        raise\n"
              "    finally:\n        pool.shutdown(wait=False)\n")
    bare = ("    try:\n        pass\n    except BaseException:\n"
            "        pool.shutdown()\n        _signal_member_groups()\n        raise\n"
            "    finally:\n        pool.shutdown(wait=True)\n")
    positional = ("    try:\n        pass\n    except BaseException:\n"
                  "        pool.shutdown(True, cancel_futures=True)\n"
                  "        _signal_member_groups()\n        raise\n"
                  "    finally:\n        pool.shutdown(wait=True)\n")
    cancel = ("    try:\n        pass\n    except BaseException:\n"
              "        pool.shutdown(wait=True, cancel_futures=False)\n"
              "        _signal_member_groups()\n        raise\n"
              "    finally:\n        pool.shutdown(wait=True)\n")
    sweep_oracle = []
    for label, name, body in (("ordered", "_config_results", ordered),
                              ("moved", "_config_results", moved),
                              ("renamed", "unrelated", ordered),
                              ("waitfirst", "_config_results", waitfirst),
                              ("nowait", "_config_results", nowait),
                              ("bare", "_config_results", bare),
                              ("positional", "_config_results", positional),
                              ("cancel", "_config_results", cancel)):
        source = "def {}():\n".format(name) + body
        path = fixture / "sweep-oracle-{}.py".format(label)
        path.write_text(source, encoding="utf-8")
        probe_code = next(const for const in compile(source, str(path), "exec").co_consts
                          if isinstance(const, types.CodeType))
        try:
            sweep_oracle.append(_sweep_call_sites(types.FunctionType(probe_code, {})))
        except LookupError:
            sweep_oracle.append("LookupError")
    got["sweep-oracle"] = ("sweep-oracle", sweep_oracle)
    try:
        got["sweep-site"] = ("sweep-site", [form for _, form in _sweep_call_sites()])
    except LookupError as exc:
        sweep_down = "TIMEOUT: config kill-sweep site oracle: {} (cannot evaluate)".format(exc)
        CANNOT_EVALUATE.append(sweep_down)
        got["sweep-site"] = sweep_down

    def expect(scenario, outcome, registered, states, records, lines=(False, False),
               timely=True, **more):
        return (scenario, outcome, registered, states, records, timely,
                dict(restored=[True, True, True], lock_free=True, lines=list(lines), **more))

    closing = "the suite is closing: no new member starts"
    for check_id, wants in (
            ("config/member-close-on-interrupt", [
                expect("pooled", "KeyboardInterrupt", 0, ["dead"], 4),
                expect("communicate", "KeyboardInterrupt", 0, ["dead"], 2),
                expect("leader-first", "KeyboardInterrupt", 0, ["dead"], 2),
                expect("setsid", "KeyboardInterrupt", 0, ["alive", "dead"], 2)]),
            ("config/member-close-handlers", [
                expect("close", "KeyboardInterrupt", 0, ["dead"], 2, protected=[True, True]),
                expect("sigterm", "SystemExit 143", 0, ["dead"], 2),
                expect("install", "KeyboardInterrupt", 0, [], 0),
                expect("split-T-I", "SystemExit 143", 0, [], 0,
                       control=[closing, [signal.SIGTERM], True],
                       closes=[[signal.SIGINT], [signal.SIGINT]]),
                expect("split-T-II", "SystemExit 143", 0, [], 0,
                       control=[closing, [signal.SIGTERM], True],
                       closes=[[signal.SIGINT], [signal.SIGINT, signal.SIGINT]]),
                expect("split-TI-I", "SystemExit 143", 0, [], 0,
                       control=[closing, [signal.SIGTERM, signal.SIGINT], True],
                       closes=[[signal.SIGINT], [signal.SIGINT]]),
                expect("split-IT-I", "SystemExit 143", 0, [], 0,
                       control=[closing, [signal.SIGINT, signal.SIGTERM], True],
                       closes=[[signal.SIGINT], [signal.SIGINT]]),
                expect("late-monitor", "returned 2", 0, ["dead"], 2, (False, True),
                       handshake=["late SIGINT"])]),
            ("config/member-close-lock", [
                expect("lock", "KeyboardInterrupt", 0, ["dead"], 2,
                       monitor_pin=[[["members registered"], ["SIGINT"], 0],
                                    [["member in communicate"], ["SIGINT"], 0]],
                       expiry_pin=[1, True], bound_pin=[True, ["pin"], False, ["pin"]],
                       direct=[None, True, []], loop=[[], True],
                       held=[True, True, [True]])]),
            ("config/member-failed-no-late-launch", [
                expect("failed", "ValueError: injected failed member a", 0, [], 0,
                       late=[["b", closing]], member_a=[True, True],
                       evidence=[True, True]),
                expect("failed-signal", "KeyboardInterrupt", 0, [], 0,
                       late=[["b", closing]], member_a=[True, True],
                       evidence=[True, True]),
                expect("failed-handshake", "HandshakeTimeout: member b running not within 1 s",
                       0, [], 0, late=[["b", closing]], handshake=["member b running"],
                       member_a=[True, True], evidence=[True, True]),
                # The deterministic stalled twin (round 20): one member, failing WITHOUT
                # arriving, so a_arrived, a_raises and the send list hold, in every run,
                # the state a slow git leaves them in, while every compared field is
                # structural. An edit that ties evidence, lock_free or restored to member
                # a's arrival or raise or to the send list on code this run executes, not
                # also dependent on any other state that differs from a slow "failed" run,
                # yields a wrong VALUE here, a definite mismatch in a healthy run; a stall's
                # presence is pinned the same way by "failed-refused" (stalls nonempty)
                # beside "failed" (stalls empty). late=[] shows run_member never runs
                # here, which is why the residual probes raisegate, trygate and sendgate
                # pass this pin: a change that gives this run a member b must revisit
                # THE RESIDUAL (_allowlist_producers). The empty send list is deliberate
                # (_DELIBERATE_EMPTY_SEND), so a healthy stalled run stands nothing down.
                expect("failed-stalled", "ValueError: injected failed member a", 0, [], 0,
                       timely=False, late=[], member_a=[False, False],
                       evidence=[True, True]),
                ("handshake-map", [True, True, True, True]),
                ("record-map", [True] * 30),
                # The stalled run's slow-control record, read from the child's source: intact
                # in the committed child, and each probe, in control_probes order (norec
                # through dottedimport), names the fact it breaks; the last seven
                # (exceptbind, ctxclass, mainattr, wildcard, exitafter, clsname,
                # funcglobals) are instances of disclosed routes the reader does not read,
                # held at [] so a reader that starts catching one must update the NOT READ
                # text of _slow_control_record.
                ("slow-control", [[], ["gate"], ["gate", "record"], ["gate"], ["outcome"],
                                  ["outcome"], ["outcome"], ["outcome"], ["outcome"],
                                  ["outcome"], ["outcome"], ["outcome"], ["outcome"],
                                  ["outcome"], ["outcome"], ["outcome"], ["record"],
                                  ["gate"], ["gate"], ["gate"], ["gate"], ["gate"],
                                  ["kept"], ["kept"], ["kept"], ["kept"], ["kept"],
                                  ["kept"], ["kept"], ["kept"], ["outcome"], ["outcome"],
                                  [], [], [], [], [], [], []]),
                # Each allowlisted field's producer reads only its traced bound-free source:
                # evidence the sweep wrapper's branch_swept appends (guarded only by its pure
                # frame walk, frame), their count before the direct call (witnessed), the
                # builtins and the failed-scenario test on argv (scenario, sys); lock_free the
                # lock_free helper's zero-timeout acquire on the registry lock (suite);
                # restored the getsignal reads and the appended constants (signal).
                ("producer-map", dict(
                    evidence=["bool", "branch_swept", "frame", "len", "scenario", "sys",
                              "witnessed"],
                    lock_free=["lock_free", "suite"], restored=["restored", "signal"],
                    unrecognized=[])),
                # One expectation per probe, in producer_probes order. The eleven final
                # empty diffs pass this net by design: moduleattr and forloop are disclosed
                # boundaries (forloop is caught by the "failed-stalled" value pin);
                # raisegate, trygate, sendgate, scenariogate, bgate, outcomegate,
                # outcomeonlygate, stallgate and pinned_version are examples of THE
                # RESIDUAL (_allowlist_producers), which passes the value pin too, so an
                # oracle or scenario change that starts catching one of them must update
                # that text.
                ("producer-oracle", [["evidence"], ["evidence"], ["evidence"],
                                     ["lock_free", "unrecognized"],
                                     ["restored", "unrecognized"],
                                     ["evidence", "unrecognized"], ["unrecognized"],
                                     ["evidence"], ["evidence"], ["evidence"],
                                     ["evidence"], ["evidence"], ["evidence"],
                                     ["unrecognized"], ["unrecognized"],
                                     ["unrecognized"], ["unrecognized"], ["evidence"],
                                     ["unrecognized"], ["unrecognized"],
                                     ["unrecognized"], ["unrecognized"],
                                     ["unrecognized"], ["unrecognized"],
                                     ["unrecognized"], ["evidence", "unrecognized"],
                                     ["evidence"], ["evidence"], ["evidence"],
                                     ["evidence"], ["evidence"],
                                     ["evidence", "unrecognized"],
                                     ["evidence"], ["evidence"], ["evidence"], ["evidence"],
                                     ["evidence", "unrecognized"],
                                     ["evidence", "unrecognized"],
                                     ["evidence", "unrecognized"],
                                     ["evidence", "unrecognized"],
                                     ["evidence", "unrecognized"],
                                     [], [], [], [], [], [], [], [], [], [], []]),
                ("sweep-site", ["except-before-wait"]),
                ("sweep-oracle", [[(4, "except-before-wait")], [(5, "unrecognized")],
                                  "LookupError", [(5, "unrecognized")],
                                  [(4, "unrecognized")], [(5, "unrecognized")],
                                  [(5, "unrecognized")], [(5, "unrecognized")]])]),
            ("config/member-close-unresolved-verdict", [
                expect("unresolved", "returned 2", 1, [], 0, (False, True),
                       released=[[], 0]),
                expect("release-timeout", "returned 2", 0, [], 0, (False, True),
                       run=["0", 0], refused=1),
                expect("start-timeout", "returned 2", 0, [], 0, (False, True),
                       run=["TIMEOUT: ", 1], refused=1),
                expect("sweep-refused", "returned 2", 0, [], 0, (False, True), refused=1),
                expect("close-refused", "returned 2", 0, [], 0, (False, True), refused=1),
                expect("close-wait-refused", "returned 2", 0, [], 0, (False, True), refused=1,
                       site=site_wants[0]),
                expect("close-left-refused", "returned 2", 0, [], 0, (False, True), refused=1,
                       site=site_wants[1]),
                expect("failed-refused", "returned 2", 0, [], 0, (False, True),
                       late=[["b", closing]], caught="injected failed member a", refused=1,
                       member_a=[True, True], evidence=[True, True]),
                ("close-sites", ["with"] * len(refused_site_scenarios)),
                ("close-oracle",
                 [[(1, "with"), (3, "with"), (5, "with")], "LookupError",
                  [(1, "with"), (3, "unrecognized"), (5, "unrecognized"),
                   (7, "unrecognized")]]),
                ("down-map", [True, True, True, True])])):
        observed = [got[want[0]] for want in wants]
        wants = _stood_down_wants(observed, wants)
        check(check_id, observed, wants, _partial_timeout_cause(observed, wants) or None)
    observed = [refused_start, got["closing-control"]]
    wants = _stood_down_wants(observed, [
        (closing, []), expect("closing-control", "KeyboardInterrupt", 0, [], 0,
                              control=[closing, [signal.SIGINT], True])])
    check("config/member-closing-refuses-start", observed, wants,
          _partial_timeout_cause(observed, wants) or None)

    rc, out, err = _run_bounded([sys.executable, "-I", "-B", "-c", _CLOSE_SWEEP_CHILD,
                                 str(ROOT / "tools")], str(fixture), dict(os.environ), 300,
                                "member-close signal sweep child", "CONTROL ")
    got, sweep = _sweep_observed(rc, out, err)
    if sweep is not None:
        print("MEMBER CLOSE SWEEP: {} ({} s)".format(", ".join(
            "{} {} runs".format(mode, sweep[mode][0]) for mode in _SWEEP_MODES),
            sweep["seconds"]), flush=True)
    # The parser's own controls, on a synthetic report every mode of which is clean: accepted
    # after exit 0, refused after the same report followed by exit 1.
    synthetic = dict({mode: [1, []] for mode in _SWEEP_MODES}, names=list(_SWEEP_REACHED),
                     seconds=0, baseline=_SWEEP_WANT[0][0])
    synthetic["branch baseline"] = _SWEEP_WANT[0][1]
    synthetic = "SWEEP-REPORT " + json.dumps(synthetic) + "\n"
    controls = [_sweep_observed(0, synthetic, "")[0] == _SWEEP_WANT,
                _sweep_observed(1, synthetic, "injected child failure")[0] == _SWEEP_WANT]
    check("config/member-close-signal-sweep", (got, controls), (_SWEEP_WANT, [True, False]),
          (rc,) if _is_timeout(rc) else None)


def _member_timeout_controls(base):
    """Controls for the bounded member runs.
    config/member-timeout-cannot-evaluate: a fixture member spawns a sleeping grandchild that
    inherits (holds) its stdout, then sleeps past a short bound; the timeout comes back as the
    cannot-evaluate marker and is recorded, its diagnostic on the control's own stream and nothing
    on the suite's stderr. config/member-timeout-kills-group: the grandchild is "dead" afterwards
    (_pid_state; unreadable /proc evidence is cannot-evaluate and fails the check closed).
    config/pid-state-fail-closed: _pid_state over a scratch process table.
    config/member-timeout-cleanup-failure: with the group kill refused (PermissionError injected
    into both the pidfd and the numeric group signal),
    the timeout stays recorded and cannot-evaluate, the refusal is reported as a second stdout
    line, nothing reaches stderr, and the leader is killed alone.
    config/member-failure-diagnostic-stdout: a member exiting non-zero prints its output on
    stdout, never stderr. config/timeout-verdict-exit and config/timeout-attribution: _verdict's
    ordering and the attribution helpers. config/member-timeout-main-verdict: main() in a child,
    with production defaults, exits 2 for a timeout with only attributed findings and 1 when a
    definite finding is also present, the diagnostic on stdout and stderr empty; one scenario's
    timeout never hides the other's definite mismatch (_mixed_timeout_controls). The timeout
    record and cleanup order, the guarded cleanup diagnostic and the cleanup on an interrupted
    wait are pinned by _timeout_cleanup_order_controls; the member thread, the registry, the
    group kill after a normal return and the suite's close under a real SIGINT by
    _member_close_controls.
    config/member-bound-table and config/member-bound-opf-selftest pin the per-member bound table
    against the registered roster, and config/member-bound-reaches-run pins that the looked-up
    bound is the one handed to _run_bounded (_member_bound_checks). A fixture that never got going
    inside its bound is attributed to that timeout, never a red."""
    import contextlib
    import io
    import time
    from unittest.mock import patch

    fixture = base / "timeout-fixture"
    fixture.mkdir()
    pidfile = fixture / "grandchild.pid"
    member = fixture / "member.py"
    member.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-I', '-B', '-c', "
        "'import os, sys, time; open(sys.argv[1] + \".tmp\", \"w\").write(str(os.getpid())); "
        "os.replace(sys.argv[1] + \".tmp\", sys.argv[1]); time.sleep(600)', sys.argv[1]])\n"
        "time.sleep(600)\n", encoding="utf-8")
    recorded = []
    diagnostic = io.StringIO()
    started = time.monotonic()
    with contextlib.redirect_stderr(io.StringIO()) as leaked:
        rc = _run_config_member((str(member), str(pidfile)), dict(os.environ), bound=20,
                                cannot_evaluate=recorded, report=diagnostic)
    elapsed = time.monotonic() - started
    budget = 20 + CONFIG_MEMBER_DRAIN_SECONDS + CONFIG_MEMBER_REAP_SECONDS + 30
    # Timing alone is never a definite finding: a control past this budget is recorded as a
    # cannot-evaluate timeout (fail closed, never a pass) and the field is stood down on both
    # sides, so every other field stays compared; the producer itself is pinned below on a
    # synthetic over-budget value, so removing the recording fails this check definitively.
    timely = _member_timeout_timely(elapsed, budget)
    over_recorded, under_recorded = [], []
    over = _member_timeout_timely(budget + 1.0, budget, over_recorded)
    producer_pin = [_is_timeout(over), over_recorded == [over],
                    _member_timeout_timely(0.0, budget, under_recorded) is True,
                    under_recorded == []]
    check("config/member-timeout-cannot-evaluate",
          (isinstance(rc, str) and rc.startswith("TIMEOUT: "), recorded == [rc],
           diagnostic.getvalue() == "CONFIG-INJECTION {}\n".format(rc), leaked.getvalue(),
           timely, producer_pin),
          (True, True, True, "", timely, [True, True, True, True]))
    cause = None
    try:
        grandchild = int(pidfile.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # The fixture itself never got going inside its bound: host load, no verdict either way.
        message = ("TIMEOUT: member-timeout control: the grandchild never started within the "
                   "20 s bound ({})".format(exc))
        CANNOT_EVALUATE.append(message)
        got, cause = "not observed: " + message, (message,)
    else:
        deadline = time.monotonic() + 10
        got = _pid_state(grandchild)
        while got == "alive" and time.monotonic() < deadline:
            time.sleep(0.2)
            got = _pid_state(grandchild)
        if got == "alive":
            os.kill(grandchild, signal.SIGKILL)
    check("config/member-timeout-kills-group", got, "dead", cause)

    table = fixture / "proc"
    for pid, content in (("101", "101 (sleep) S 1 2 3\n"), ("102", "102 (a) b) Z 1\n"),
                         ("103", "103 no-state\n"), ("104", "104 (x) Q 1\n")):
        (table / pid).mkdir(parents=True)
        (table / pid / "stat").write_text(content, encoding="utf-8")
    (table / "105" / "stat").mkdir(parents=True)
    states = [_pid_state(pid, str(table)) for pid in (101, 102, 103, 104, 105, 106)]
    (table / "self").mkdir()
    states.append(_pid_state(106, str(table)))
    check("config/pid-state-fail-closed",
          [state.split(":")[0] for state in states],
          ["alive", "dead", "cannot-evaluate", "cannot-evaluate", "cannot-evaluate",
           "cannot-evaluate", "dead"])

    sleeper = fixture / "sleeper.py"
    sleeper.write_text("import time\ntime.sleep(600)\n", encoding="utf-8")
    refused, leaders = [], []
    real_killpg, real_pidfd_signal, real_popen = os.killpg, signal.pidfd_send_signal, \
        subprocess.Popen

    def refusing(real):
        def refuse(target, sig, *rest):
            if sig != signal.SIGKILL:
                return real(target, sig, *rest)
            refused.append(sig)
            raise PermissionError(1, "injected group-kill refusal")
        return refuse

    def spawn(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        leaders.append(child.pid)
        return child

    recorded = []
    diagnostic = io.StringIO()
    with patch.object(os, "killpg", refusing(real_killpg)), \
            patch.object(signal, "pidfd_send_signal", refusing(real_pidfd_signal)), \
            patch.object(subprocess, "Popen", spawn), \
            contextlib.redirect_stderr(io.StringIO()) as leaked:
        rc = _run_config_member((str(sleeper),), dict(os.environ), bound=3,
                                cannot_evaluate=recorded, report=diagnostic)
    lines = diagnostic.getvalue().splitlines()
    check("config/member-timeout-cleanup-failure",
          (isinstance(rc, str) and rc.startswith("TIMEOUT: "), recorded == [rc],
           lines[:1] == ["CONFIG-INJECTION " + str(rc)],
           len(lines) == 2 and "PermissionError" in lines[1] and "cannot-evaluate" in lines[1],
           leaked.getvalue(), refused[:1], [_pid_state(pid) for pid in leaders]),
          (True, True, True, True, "", [signal.SIGKILL], ["dead"]))

    failing = fixture / "failing.py"
    failing.write_text("import sys\nprint('member-out')\nprint('member-err', file=sys.stderr)\n"
                       "sys.exit(3)\n", encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()) as out, \
            contextlib.redirect_stderr(io.StringIO()) as leaked:
        rc = _run_config_member((str(failing),), dict(os.environ), bound=120)
    check("config/member-failure-diagnostic-stdout",
          (rc, "member-out" in out.getvalue(), "member-err" in out.getvalue(), leaked.getvalue()),
          (3, True, True, ""), (rc,) if _is_timeout(rc) else None)

    codes = []
    timeout = "TIMEOUT: m reached its 1 s bound (cannot evaluate)"
    attributed = "config/m/rc: got {!r}, want 0".format(timeout)
    for failures, timeouts, caused in (
            ([], [], {}), (["a finding"], [], {}), ([], [timeout], {}),
            (["a finding"], [timeout], {}),
            ([attributed], [timeout], {attributed: (timeout,)}),
            ([attributed, "a finding"], [timeout], {attributed: (timeout,)}),
            ([attributed], [], {attributed: (timeout,)})):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = _verdict(failures, timeouts, caused)
        codes.append((code, ("CANNOT EVALUATE" in out.getvalue(),
                             "SELF-TEST FAIL" in out.getvalue(),
                             "no verdict on that member" in out.getvalue())))
    check("config/timeout-verdict-exit", codes,
          [(0, (False, False, False)), (1, (False, True, False)), (2, (True, False, True)),
           (1, (False, True, True)), (2, (True, False, True)), (1, (False, True, True)),
           (1, (False, True, False))])

    clean = (0, b"", b"", b"")
    check("config/timeout-attribution",
          ([_timeout_cause(got) for got in (timeout, [0, timeout], [1, timeout], [False, timeout],
                                            "[Errno 2] launch", [0], 0)],
           [_registered_offenders(results, [timeout]) for results in (
               {("a",): clean}, {("a",): (timeout, b"", b"", b"")},
               {("a",): (timeout, b"hook\n", b"", b"")},
               {("a",): (timeout, b"", b"", b""), ("b",): (1, b"", b"", b"")},
               {("a",): ("[Errno 2] launch", b"", b"", b"")}, {("a",): None})]),
          ([(timeout,), (timeout,), (), (), (), (), ()],
           [([], ()), ([("a",)], (timeout,)), ([("a",)], ()), ([("b",)], ()),
            ([("a",)], ()), ([("a",)], ())]))

    _main_verdict_control(sleeper, fixture)
    _mixed_timeout_controls(sleeper)
    _timeout_cleanup_order_controls(sleeper)
    _member_close_controls(fixture)

    _member_bound_checks(set(_registered_selftests()))


def _member_bound_checks(roster):
    """config/member-bound-table: no CONFIG_MEMBER_BOUNDS key outside the registered roster, the
    bound applied to each key is exactly the override pinned here, the default is 1200 s, and
    every other roster member gets the default. config/member-bound-opf-selftest:
    opf/tools/opf.py --self-test is registered and the bound applied to it is its own 3600 s
    entry, not the default (red when that entry is removed or changed).
    config/member-bound-reaches-run: _run_config_member hands the bound it looks up to
    _run_bounded (a stub patched into the engine module records it; nothing is launched), for
    every override and for one non-member argv that keeps the default (red when the lookup is
    bypassed).
    config/member-roster-figures: the frozen sizing account (SIZING_ACCOUNT_ROSTER,
    SIZING_ACCOUNT_TIMED, SIZING_MEASURED and SIZING_MALFORMED_MEASURED in the engine) still
    applies to the live roster (_roster_figures reports no problem), keeps its sizes, and keeps
    its exact content (a digest of those four frozen constants is pinned, so swapping one frozen
    entry for another of the same size is refused). The figures _roster_figures derives are
    printed, with one line per member outside the account, and not pinned, so registering a
    member needs no edit here or in the engine's sizing comment.
    config/member-roster-oracle: the live figures equal this check's own derivation from the
    roster and the frozen constants, computed here rather than taken from _roster_figures but
    not independent of it: every expression except members mirrors the derivation's (red when
    a derived figure drifts, since the relative vectors below shift actual and expected
    together and cannot see a constant offset; blind to a formula error shared with the
    derivation, which the synthetic vector covers for its own inputs).
    config/member-roster-synthetic: a five-member synthetic account and roster produce figures
    pinned here as hand-counted literals (red when a derived figure drifts on those inputs,
    including by an error the oracle recomputation shares; an error that shows only on other
    inputs, such as a later measurement of an account member, is left to the relative vectors
    below).
    Each config/member-roster-* vector runs _roster_figures over a changed roster or table
    (nothing is launched). -added: a member registered after the account raises every derived
    figure by one, the two malformed-lane figures only when it is in that lane, and stays green
    (red when such a member is refused, not counted, or counted in a lane it is excluded from).
    -account-removed: an account member no longer registered is refused (red when that rule is
    dropped). -later-removed: a member outside the account no longer registered lowers every
    derived figure by one and stays green (red when the whole live roster is pinned); when the
    live roster has no removable member outside the account, a synthetic one is added and then
    removed, so an account-only roster stays green too.
    -measured-lane-changed: a measured member moved into or out of the malformed lane is refused
    (red when either direction is dropped). -bound-unmeasured: a bound on a member with no
    measurement behind it is refused (red when the bound rule is dropped).
    -bound-later-measured: the same bound with a SIZING_LATER_MEASURED citation stays green and
    counts as a recorded time (red when the bound rule ignores that table).
    -account-later-measured: a citation for an account member (tools/check_secrets.py
    --self-test, which the check pins as in the malformed lane and outside
    SIZING_MALFORMED_MEASURED) stays green and changes no figure (red when such a citation
    lowers the count of members with no recorded time or of malformed-lane members not
    measured in that lane).
    -later-measurement-refused: a citation for an unregistered member, a blank one, and one
    holding a line boundary are each refused. The boundaries tested are LF, CR, a CR LF pair,
    VT, FF, FS, GS, RS, NEL and U+2029, each inside the line, and U+2028 at the end of the line,
    which the padding must turn into a split (red when the stale rule or the blank rule is
    dropped, when any one of those ten characters is no longer refused, when the padding after
    the citation is dropped, or when CR LF pairs are stripped before the split).
    -measured-outside-account: a measured member outside the account and a malformed-lane
    measurement outside SIZING_MEASURED are each refused (red when either rule is dropped)."""
    import hashlib
    from unittest.mock import patch

    record = ("opf/tools/check_opf_record.py", "--self-test", "--red-on-revert")
    opf_selftest = ("opf/tools/opf.py", "--self-test")
    adopt_observe = ("opf/tools/_opf_adopt_observe.py", "--self-test")
    release_delta = ("tools/check_release_delta.py", "--self-test")
    opf_init = ("opf/tools/check_opf_init.py", "--self-test")
    closure_a = ("tools/check_opf_standalone_closure.py", "--self-test", "--ci-job",
                 "opf-closure-a")
    closure_b = ("tools/check_opf_standalone_closure.py", "--self-test", "--ci-job",
                 "opf-closure-b")
    closure_c = ("tools/check_opf_standalone_closure.py", "--self-test", "--ci-job",
                 "opf-closure-c")
    closure_d = ("tools/check_opf_standalone_closure.py", "--self-test", "--ci-job",
                 "opf-closure-d")
    closure_e = ("tools/check_opf_standalone_closure.py", "--self-test", "--ci-job",
                 "opf-closure-e")

    figures, problems = _roster_figures(roster)
    print("CONFIG ROSTER FIGURES: {} members, {} in the malformed lane, {} outside the sizing"
          " account, {} with no recorded time, {} malformed-lane members not measured in that"
          " lane".format(figures["members"], figures["malformed"], len(figures["later"]),
                         figures["no_recorded_time"], figures["malformed_unmeasured"]),
          flush=True)
    for argv in figures["later"]:
        citation = SIZING_LATER_MEASURED.get(argv)
        print("CONFIG ROSTER OUTSIDE SIZING ACCOUNT: " + " ".join(argv)
              + ("" if citation is None else " (measured later: {})".format(citation)),
              flush=True)
    frozen = repr((sorted(SIZING_ACCOUNT_ROSTER), SIZING_ACCOUNT_TIMED, sorted(SIZING_MEASURED),
                   sorted(SIZING_MALFORMED_MEASURED)))
    check("config/member-roster-figures",
          (problems, len(SIZING_ACCOUNT_ROSTER), SIZING_ACCOUNT_TIMED, len(SIZING_MEASURED),
           len(SIZING_MALFORMED_MEASURED), hashlib.sha256(frozen.encode("utf-8")).hexdigest()),
          ([], 100, 88, 10, 9,
           "7e2bb7449914bbeb00a01c96d7d680cf24ddf57755bd2b56306815577e37b94d"))
    # The oracle recomputes every live figure from the roster and the frozen constants, so an
    # expectation no longer inherits a drifted figure through the baseline above. It is not an
    # independent derivation: every expression but members mirrors _roster_figures's, so an
    # error shared with the derivation passes here and is left to the hand-counted synthetic
    # vector and the relative vectors below, each of which sees only its own inputs. members is
    # derived by a different route (account size plus members outside it; the account is fully
    # registered whenever problems is empty, which the figures check pins).
    lane = {argv for argv in roster
            if _command_identity(argv) not in CONFIG_EXCLUSIONS["malformed"]}
    outside = sorted(set(roster) - SIZING_ACCOUNT_ROSTER)
    check("config/member-roster-oracle", figures,
          {"members": len(SIZING_ACCOUNT_ROSTER) + len(outside), "malformed": len(lane),
           "later": outside,
           "no_recorded_time": len(SIZING_ACCOUNT_ROSTER) - SIZING_ACCOUNT_TIMED
           + len([argv for argv in outside if argv not in SIZING_LATER_MEASURED]),
           "malformed_unmeasured": len(lane - SIZING_MALFORMED_MEASURED)})
    # A synthetic account whose figures are pinned as hand-counted literals: a, b and c are the
    # account (a and b timed and measured, a measured in the malformed lane, b excluded from it,
    # c unmeasured), d and e registered later (e measured later and carrying a bound).
    a, b, c, d, e = (("tools/check_secrets.py", "--self-test", "--synthetic-" + name)
                     for name in ("alpha", "beta", "gamma", "delta", "epsilon"))
    with patch.object(_gfe, "SIZING_ACCOUNT_ROSTER", frozenset({a, b, c})), \
            patch.object(_gfe, "SIZING_ACCOUNT_TIMED", 2), \
            patch.object(_gfe, "SIZING_MEASURED", frozenset({a, b})), \
            patch.object(_gfe, "SIZING_MALFORMED_MEASURED", frozenset({a})), \
            patch.dict(SIZING_LATER_MEASURED, {e: "Synthetic later measurement."}, clear=True), \
            patch.dict(CONFIG_MEMBER_BOUNDS, {e: 1800}, clear=True), \
            patch.dict(CONFIG_EXCLUSIONS["malformed"],
                       {b: "Synthetic malformed-lane exclusion."}, clear=True):
        synthetic = _roster_figures({a, b, c, d, e})
    check("config/member-roster-synthetic", synthetic,
          ({"members": 5, "malformed": 4, "later": [d, e], "no_recorded_time": 2,
            "malformed_unmeasured": 3}, []))

    def shifted(step, member, recorded=False, lane=True, base=None):
        # The figures (base, the live ones by default) with member registered (step 1) or
        # unregistered (step -1), for a member outside the account, in the malformed lane unless
        # lane is false, and with no recorded time unless recorded is set.
        base = figures if base is None else base
        later = sorted([argv for argv in base["later"] if argv != member]
                       + ([member] if step > 0 else []))
        in_lane = step if lane else 0
        return ({"members": base["members"] + step,
                 "malformed": base["malformed"] + in_lane, "later": later,
                 "no_recorded_time": base["no_recorded_time"] + (0 if recorded else step),
                 "malformed_unmeasured": base["malformed_unmeasured"] + in_lane}, [])

    probe = ("tools/check_secrets.py", "--self-test", "--roster-probe")
    added = _roster_figures(roster | {probe})
    with patch.dict(CONFIG_EXCLUSIONS["malformed"],
                    {probe: "Synthetic malformed-lane exclusion."}):
        added_excluded = _roster_figures(roster | {probe})
    check("config/member-roster-added", [added, added_excluded],
          [shifted(1, probe), shifted(1, probe, lane=False)])
    secrets = ("tools/check_secrets.py", "--self-test")
    check("config/member-roster-account-removed", _roster_figures(roster - {secrets})[1],
          ["sizing account member not registered: {!r}".format(secrets)])
    # Remove a live member outside the account, in the malformed lane and with no later
    # measurement; a roster with no such member (every member in the account, measured later or
    # excluded) falls back to adding a synthetic one and removing that, so the check stays green
    # there instead of going red with nothing to remove.
    removed = next((argv for argv in figures["later"]
                    if _command_identity(argv) not in CONFIG_EXCLUSIONS["malformed"]
                    and argv not in SIZING_LATER_MEASURED),
                   ("tools/check_secrets.py", "--self-test", "--roster-removal-probe"))
    check("config/member-roster-later-removed", _roster_figures((roster | {removed}) - {removed}),
          shifted(-1, removed, base=_roster_figures(roster | {removed})[0]))

    with patch.dict(CONFIG_EXCLUSIONS["malformed"],
                    {opf_selftest: "Synthetic malformed-lane exclusion."}):
        moved_out = _roster_figures(roster)[1]
    with patch.dict(CONFIG_EXCLUSIONS["malformed"],
                    {argv: reason for argv, reason in CONFIG_EXCLUSIONS["malformed"].items()
                     if argv != release_delta}, clear=True):
        moved_in = _roster_figures(roster)[1]
    check("config/member-roster-measured-lane-changed", [moved_out, moved_in],
          [["malformed-lane membership changed on a measured member: {!r}".format(argv)]
           for argv in (opf_selftest, release_delta)])

    later_probe = ("tools/check_secrets.py", "--self-test", "--later-measurement-probe")
    with patch.dict(CONFIG_MEMBER_BOUNDS, {later_probe: 3600}):
        unmeasured = _roster_figures(roster | {later_probe})[1]
    check("config/member-roster-bound-unmeasured", unmeasured,
          ["bound with no measurement behind it: {!r}".format(later_probe)])
    with patch.dict(CONFIG_MEMBER_BOUNDS, {later_probe: 3600}), patch.dict(
            SIZING_LATER_MEASURED, {later_probe: "Synthetic later measurement."}):
        later_measured = _roster_figures(roster | {later_probe})
    check("config/member-roster-bound-later-measured", later_measured,
          shifted(1, later_probe, recorded=True))
    # A later measurement of an account member changes no figure: the account does not name its
    # members with no recorded time, so a citation cannot take one off that count, and the count
    # of malformed-lane members not measured there reflects only the account's malformed-lane run.
    # The member must sit in that lane outside SIZING_MALFORMED_MEASURED for the second count to
    # be able to move, so both facts are pinned with the figures.
    with patch.dict(SIZING_LATER_MEASURED, {secrets: "Synthetic later measurement."}):
        account_later = _roster_figures(roster)
    check("config/member-roster-account-later-measured",
          (account_later, _command_identity(secrets) not in CONFIG_EXCLUSIONS["malformed"],
           secrets in SIZING_MALFORMED_MEASURED),
          ((figures, []), True, False))
    refused = []
    for changed, citation in ((roster, "Synthetic later measurement."),
                              (roster | {later_probe}, " "),
                              (roster | {later_probe}, "Synthetic\nlater measurement."),
                              (roster | {later_probe}, "Synthetic\rlater measurement."),
                              (roster | {later_probe}, "Synthetic\r\nlater measurement."),
                              (roster | {later_probe}, "Synthetic\x0blater measurement."),
                              (roster | {later_probe}, "Synthetic\x0clater measurement."),
                              (roster | {later_probe}, "Synthetic\x1clater measurement."),
                              (roster | {later_probe}, "Synthetic\x1dlater measurement."),
                              (roster | {later_probe}, "Synthetic\x1elater measurement."),
                              (roster | {later_probe}, "Synthetic\x85later measurement."),
                              (roster | {later_probe}, "Synthetic\u2029later measurement."),
                              (roster | {later_probe}, "Synthetic later measurement.\u2028")):
        with patch.dict(SIZING_LATER_MEASURED, {later_probe: citation}):
            refused.append(_roster_figures(changed)[1])
    check("config/member-roster-later-measurement-refused", refused,
          [["stale or unreasoned later measurement: {!r}".format(later_probe)]] * 13)

    # _roster_figures reads the engine's SIZING_* names, so the frozen sets are patched there.
    with patch.object(_gfe, "SIZING_MEASURED", SIZING_MEASURED | {later_probe}), patch.object(
            _gfe, "SIZING_MALFORMED_MEASURED", SIZING_MALFORMED_MEASURED | {later_probe}):
        outside = _roster_figures(roster)[1]
    with patch.object(_gfe, "SIZING_MALFORMED_MEASURED", SIZING_MALFORMED_MEASURED | {secrets}):
        unmatched = _roster_figures(roster)[1]
    check("config/member-roster-measured-outside-account", [outside, unmatched],
          [["measured member outside the sizing account: {!r}".format(later_probe)],
           ["malformed-lane measurement outside SIZING_MEASURED: {!r}".format(secrets)]])

    overrides = {record: 5100, opf_selftest: 3600, adopt_observe: 1800, release_delta: 1800,
                 opf_init: 1800, closure_a: 3600, closure_b: 3600, closure_c: 3600,
                 closure_d: 3600, closure_e: 3600}
    check("config/member-bound-table",
          (sorted(key for key in CONFIG_MEMBER_BOUNDS if key not in roster),
           {key: _config_member_bound(key) for key in CONFIG_MEMBER_BOUNDS},
           CONFIG_MEMBER_BOUND_DEFAULT,
           sorted({_config_member_bound(argv) for argv in roster if argv not in overrides})),
          ([], overrides, 1200, [1200]))
    check("config/member-bound-opf-selftest",
          (opf_selftest in roster, CONFIG_MEMBER_BOUNDS.get(opf_selftest),
           _config_member_bound(opf_selftest)),
          (True, 3600, 3600))
    handed = []

    def recording(argv, cwd, env, bound, label, banner, cannot_evaluate=None, report=None):
        handed.append((label, bound))
        return 0, "", ""

    # _run_config_member resolves _run_bounded in the engine module's namespace, so the stub
    # is patched there, not into this runner.
    with patch.object(_gfe, "_run_bounded", recording):
        for member in (record, opf_selftest, adopt_observe, release_delta, opf_init, closure_a,
                       closure_b, closure_c, closure_d, closure_e, ("opf/tools/opf.py",)):
            _run_config_member(member, {})
    check("config/member-bound-reaches-run", handed,
          [(" ".join(record), 5100), (" ".join(opf_selftest), 3600),
           (" ".join(adopt_observe), 1800), (" ".join(release_delta), 1800),
           (" ".join(opf_init), 1800), (" ".join(closure_a), 3600), (" ".join(closure_b), 3600),
           (" ".join(closure_c), 3600), (" ".join(closure_d), 3600), (" ".join(closure_e), 3600),
           ("opf/tools/opf.py", 1200)])


_MEMBER_TIMEOUT_EXPECTED = [
    ("bound-kill", "str", None, "timed-out", True, ""),
    ("exit-1", "int", 1, "definite", True, ""),
    ("signal", "int", -signal.SIGKILL, "definite", True, ""),
    ("exit-2", "int", 2, "definite", True, ""),
    ("oserror", "str", None, "definite", True, ""),
    ("clean", "int", 0, "clean", True, ""),
    ("bound-kill-unrecorded", [("tools/vector_member.py", "--self-test")], ()),
    ("findings-printed-first", 1, True, True, ["vector_hooked", "vector_killed", "vector_other"],
     [True, True], ""),
]


def _member_timeout_vectors(base):
    """config/member-timeout-vectors, under the engine's exit and stream rules. Through the real
    launch path (_run_config_member, each case against a private record and report): only a run
    still going at its bound becomes a timeout message, recorded, and _registered_offenders
    attributes a lane holding only it to that timeout; a member's own exit 1, signal status and
    exit 2 (the member's own exit, never the suite's cannot-evaluate) and a launch error stay
    member evidence, a definite offender, recorded nowhere; a clean exit is clean; nothing
    reaches stderr. findings-printed-first: _verdict over a collected definite finding and a lane
    holding a member that only timed out, one that timed out after leaving evidence (definite:
    its evidence was observed), one that failed, one that ran a hook under exit 0 and a clean one
    exits 1, its stdout leading with the definite findings, which name exactly the three definite
    members, and only then listing both timeout messages under "also cannot evaluate"; stderr
    stays empty. bound-kill-unrecorded pins the recording boundary's OTHER side: the same timeout
    message left out of the record is a definite offender with no attribution (never folded into
    cannot-evaluate), so dropping the recording turns a bound kill red here instead of silently
    reclassifying it. These are regression pins over the engine's long-standing bound-kill path,
    the same recording rule the lifecycle probes feed (their own vectors run in the system-config
    lane). A case other than bound-kill that never finished inside its bound is recorded
    (host load, no verdict either way) and attributed (_partial_timeout_cause)."""
    import contextlib
    import io
    from unittest.mock import patch

    fixture = base / "member-timeout-vectors"
    fixture.mkdir()
    member = fixture / "member.py"
    member.write_text(
        "import os, signal, sys, time\n"
        "if sys.argv[1] == 'sleep':\n"
        "    time.sleep(600)\n"
        "if sys.argv[1] == 'signal':\n"
        "    os.kill(os.getpid(), signal.SIGKILL)\n"
        "sys.exit(int(sys.argv[1]))\n", encoding="utf-8")

    def refuse(*args, **kwargs):
        raise OSError(5, "injected launch refusal")

    observed = []
    for label, mode, bound in (("bound-kill", "sleep", 3), ("exit-1", "1", 120),
                               ("signal", "signal", 120), ("exit-2", "2", 120),
                               ("oserror", "0", 120), ("clean", "0", 120)):
        argv, recorded = (str(member), mode), []
        with contextlib.ExitStack() as stack:
            leaked = stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
            if label == "oserror":
                stack.enter_context(patch.object(subprocess, "Popen", refuse))
            rc = _run_config_member(argv, dict(os.environ), bound=bound,
                                    cannot_evaluate=recorded, report=io.StringIO())
        if label != "bound-kill" and _is_timeout(rc):
            CANNOT_EVALUATE.append(rc)
            observed.append(rc)
            continue
        offenders, cause = _registered_offenders({argv: (rc, b"", b"", b"")}, recorded)
        observed.append((label, type(rc).__name__, rc if type(rc) is int else None,
                         "timed-out" if cause else "definite" if offenders else "clean",
                         recorded == ([rc] if label == "bound-kill" else []), leaked.getvalue()))

    timeout = "TIMEOUT: tools/vector_member.py --self-test reached its 1 s bound (cannot evaluate)"
    killed = "TIMEOUT: tools/vector_killed.py --self-test reached its 1 s bound (cannot evaluate)"
    # The recording boundary's other side: the SAME timeout message, left
    # unrecorded, is a definite offender with no attribution.
    offenders, cause = _registered_offenders(
        {("tools/vector_member.py", "--self-test"): (timeout, b"", b"", b"")}, [])
    observed.append(("bound-kill-unrecorded", offenders, cause))
    lane = dict([
        (("tools/vector_member.py", "--self-test"), (timeout, b"", b"", b"")),
        (("tools/vector_killed.py", "--self-test"),
         (killed, b"killed-hook\n", b"killed-fsmonitor\n", b"killed-home\n")),
        (("tools/vector_other.py", "--self-test"), (1, b"", b"", b"home\n")),
        (("tools/vector_hooked.py", "--self-test"), (0, b"hook-ran\n", b"fsmonitor-ran\n", b"")),
        (("tools/vector_clean.py", "--self-test"), (0, b"", b"", b""))])
    # The lane's finding as check() records it (the id, got, want), attributed only when
    # _registered_offenders returns a cause; this vector's ids never enter the suite's record.
    offenders, cause = _registered_offenders(lane, [timeout, killed])
    finding = "vector/registered-lane: got {!r}, want []".format(offenders)
    failures = ["vector/collected-finding: got 1, want 0", finding]
    with contextlib.redirect_stdout(io.StringIO()) as out, \
            contextlib.redirect_stderr(io.StringIO()) as leaked:
        code = _verdict(failures, [timeout, killed], {finding: cause} if cause else {})
    head, also, tail = out.getvalue().partition("  also cannot evaluate")
    printed = "".join(line for line in head.split("\n") if "vector/registered-lane" in line)
    observed.append(("findings-printed-first", code, head.startswith("SELF-TEST FAIL:\n"),
                     "vector/collected-finding" in head,
                     [name for name in ("vector_clean", "vector_hooked", "vector_killed",
                                        "vector_member", "vector_other") if name in printed],
                     [bool(also) and entry in tail for entry in (timeout, killed)],
                     leaked.getvalue()))
    check("config/member-timeout-vectors", observed, _MEMBER_TIMEOUT_EXPECTED,
          _partial_timeout_cause(observed, _MEMBER_TIMEOUT_EXPECTED))


def _manifest_setup_failures(base):
    """A failed add or commit must raise even when the earlier init succeeded."""
    import gen_manifest

    original = gen_manifest._git
    try:
        for check_id, operation in (
                ("setup/gen-manifest-add-checked", "add"),
                ("setup/gen-manifest-commit-checked", "commit"),
        ):
            def fake_git(root, *args):
                return subprocess.CompletedProcess(args, int(args[0] == operation))
            gen_manifest._git = fake_git
            try:
                gen_manifest._build_fixture(base / ("failed-" + operation))
            except subprocess.CalledProcessError:
                refused = True
            else:
                refused = False
            check(check_id, refused, True)
    finally:
        gen_manifest._git = original


def _manifest_extra_setup_failures():
    """Execute each standalone setup expression with a failing git result.
    These are the eight stale/binok/marker calls outside _build_fixture; parsing
    the actual expressions catches removal of their check_returncode calls.
    This bounded probe does not simulate the rest of the generator's self-test.
    """
    tree = _optlevel.parse((ROOT / "tools" / "gen_manifest.py").read_text(encoding="utf-8"))
    owners = [n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == "_self_test_main_isolated"]
    for check_id, fixture, operation in (
            ("setup/gen-manifest-stale-add", "stale", "add"),
            ("setup/gen-manifest-stale-commit", "stale", "commit"),
            ("setup/gen-manifest-binok-init", "binok", "init"),
            ("setup/gen-manifest-binok-add", "binok", "add"),
            ("setup/gen-manifest-binok-commit", "binok", "commit"),
            ("setup/gen-manifest-marker-init", "marker", "init"),
            ("setup/gen-manifest-marker-add", "marker", "add"),
            ("setup/gen-manifest-marker-commit", "marker", "commit"),
    ):
        expressions = [n for owner in owners for n in ast.walk(owner)
                       if isinstance(n, ast.Expr) and any(
                           isinstance(call, ast.Call) and _call_name(call) == "_git"
                           and len(call.args) >= 2 and isinstance(call.args[0], ast.Name)
                           and call.args[0].id == fixture
                           and isinstance(call.args[1], ast.Constant)
                           and call.args[1].value == operation for call in ast.walk(n))]
        refused = False
        if len(expressions) == 1:
            def failed_git(*args):
                return subprocess.CompletedProcess(args, 1)
            try:
                exec(compile(ast.Module(body=expressions, type_ignores=[]),
                             "<fixture-setup-probe>", "exec", optimize=0),
                     {"_git": failed_git, fixture: Path("/unused-fixture")})
            except subprocess.CalledProcessError:
                refused = True
        check(check_id, refused, True)


def _opf_lifecycle_graph_checks():
    """Mutation D uses a synthetic route mirroring the retired two-level wrapper shape (the fixture
    above); rename cases guard discovery."""
    import copy
    names = denial = None
    expected_names = "derivable lifecycle graph"
    expected_denial = "named wrapper bypass"
    try:
        tree = _optlevel.parse(_LIFECYCLE_GRAPH_FIXTURE)
        module = "lifecycle_fixture"
        entry = "self_test"
        delegates = _opf_lifecycle_delegates({module: tree})
        delegate = delegates[module, entry][1]
        route_name = delegates[module, "_self_test_main"][1]
        renamed = copy.deepcopy(tree)
        for node in ast.walk(renamed):
            if isinstance(node, ast.FunctionDef) and node.name == delegate:
                node.name = "body_without_a_suffix"
            elif isinstance(node, ast.Name) and node.id == delegate:
                node.id = "body_without_a_suffix"
        renamed.body.append(_optlevel.parse("def unrelated_isolated(): pass").body[0])
        expected_names = dict(delegates)
        expected_names[module, entry] = (module, "body_without_a_suffix")
        names = _opf_lifecycle_delegates({module: renamed})

        mutant = copy.deepcopy(tree)
        route = next(node for node in mutant.body
                     if isinstance(node, ast.FunctionDef) and node.name == route_name)
        calls = [node for node in ast.walk(route) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == entry]
        if len(calls) != 1:
            raise ValueError("cannot uniquely mutate the fixture wrapper route")
        calls[0].func.id = delegate
        expected_denial = "lifecycle bypass: {}.{} -> {}.{}".format(
            module, route.name, module, delegate)
        try:
            _opf_lifecycle_delegates({module: mutant})
        except ValueError as exc:
            denial = str(exc)
    except (OSError, SyntaxError, ValueError, KeyError, StopIteration) as exc:
        names = denial = str(exc)
    check("env/opf-lifecycle-names-independent", names, expected_names)
    check("env/opf-lifecycle-bypass-denied", denial, expected_denial)


def _trace2_tail_checks(base, probe=_trace2_maintenance_children):
    """The maintenance probe's trace2 read over seeded files, with a scripted clock (read after
    every trace read) and a sleep that can complete the final event, so each leg is deterministic.
    The red leg's detached child keeps appending after the commit returned, so a read can see an
    unterminated final event. With a deadline of 1.0: an event completed during the wait is
    parsed when the read that sees it finishes one tick before the deadline, and is a loud
    failure when that read finishes exactly at or one tick past it; a tail still unterminated
    fails loudly at the read whose clock is at or past the deadline, after one more wait when the
    clock after the first read is one tick inside it and with no wait when that clock is exactly
    at or one tick past it. Events are split at LF only, so a raw U+2028, U+2029 or U+0085
    inside a string value stays in its event; a malformed complete event, a non-object event, a
    child_start argv that is not a list of strings and an empty file are each loud."""
    started = json.dumps({"event": "child_start",
                          "argv": ["git", "maintenance", "run", "--auto"]}).encode("ascii") + b"\n"
    late = json.dumps({"event": "child_start", "argv": ["git", "gc", "--auto"]}).encode("ascii")
    head = started + late[:20]
    parsed = [["git", "maintenance", "run", "--auto"], ["git", "gc", "--auto"]]
    completed_late = "trace2 tail completed only after the 1.0 s bound"

    def probe_trace(name, content, completion, ticks):
        trace = base / name
        trace.write_bytes(content)
        ticks, sleeps = list(ticks), []

        def sleep(seconds):
            sleeps.append(seconds)
            if completion is not None:
                trace.write_bytes(completion)
        return probe(trace, 1.0, lambda: ticks.pop(0), sleep), len(sleeps)

    awaited_legs = []
    for name, tick in (("inside", 0.999), ("at", 1.0), ("past", 1.001)):
        awaited_legs.append((name,) + probe_trace(
            "trace2-awaited-" + name + ".jsonl", head, started + late + b"\n",
            (0.0, 0.5, tick, 200.0)))
    check("env/trace2-tail-awaited", awaited_legs,
          [("inside", parsed, 1), ("at", completed_late, 1), ("past", completed_late, 1)])
    bound_legs = []
    for name, tick in (("inside", 100.999), ("at", 101.0), ("past", 101.001)):
        got, sleeps = probe_trace("trace2-bound-" + name + ".jsonl", head, None,
                                  (100.0, tick, 200.0, 200.0))
        bound_legs.append((name, isinstance(got, str)
                           and got.startswith("unterminated trace2 event"), sleeps))
    check("env/trace2-tail-bound", bound_legs,
          [("inside", True, 1), ("at", True, 0), ("past", True, 0)])
    got, sleeps = probe_trace("trace2-malformed.jsonl", b'{"event":"child_start",\n' + started,
                              None, (0.0, 0.5))
    check("env/trace2-complete-malformed-loud",
          (isinstance(got, str) and got.startswith("unparseable trace2 event"), sleeps), (True, 0))
    shape_legs = []
    for name, line in (("array", b'["child_start"]\n'),
                       ("argv-string", b'{"event":"child_start","argv":"git maintenance run"}\n')):
        # A probe that raises on the shape instead of returning its string is recorded as this
        # leg's mismatch, so the remaining legs and checks still run.
        try:
            got, sleeps = probe_trace("trace2-shape-" + name + ".jsonl", started + line, None,
                                      (0.0, 0.5))
        except (AttributeError, TypeError) as exc:
            got, sleeps = "raised %s" % (type(exc).__name__,), None
        shape_legs.append((name, isinstance(got, str)
                           and got.startswith("unparseable trace2 event"), sleeps))
    check("env/trace2-malformed-shape-loud", shape_legs,
          [("array", True, 0), ("argv-string", True, 0)])
    check("env/trace2-empty-loud", probe_trace("trace2-empty.jsonl", b"", None, (0.0, 0.5)),
          ("empty trace2 event file: no event recorded", 0))
    separated = ["git", "gc", "a\u2028b\u2029c\x85d"]
    check("env/trace2-split-newline-only",
          probe_trace("trace2-separators.jsonl", started + json.dumps(
              {"event": "child_start", "argv": separated}, ensure_ascii=False).encode("utf-8")
              + b"\n", None, (0.0, 0.5)),
          ([["git", "maintenance", "run", "--auto"], separated], 0))


def _core_lanes(base):
    """Everything outside the three config-injection lanes, in the original suite order; see
    the module docstring. Returns None on completion, 2 for a harness error (cannot evaluate,
    never a pass); an exception propagates to the engine, whose gate run refuses it."""
    # ---------- layer 1: the scrub itself ----------
    ambient_home = os.environ.get("HOME")
    restore = _poison_environ()
    try:
        env = _git_fixture_env.git_fixture_env(GIT_AUTHOR_NAME="Fixture Author")
    finally:
        restore()
    check("env/repo-selectors-dropped",
          sorted(k for k, v in POISON_VARS.items() if env.get(k) == v), [])
    check("env/allowlist-drops-novel-var", "GIT_FIXTURE_NOVEL_VARIABLE" in env, False)
    check("env/config-pins",
          (env.get("GIT_CONFIG_NOSYSTEM"), env.get("GIT_CONFIG_GLOBAL"),
           env.get("GIT_CONFIG_SYSTEM")),
          ("1", os.devnull, os.devnull))
    check("env/scratch-home",
          (env.get("HOME") == env.get("XDG_CONFIG_HOME"),
           bool(env.get("HOME")) and os.path.isdir(env.get("HOME", "")),
           env.get("HOME") != ambient_home),
          (True, True, True))
    check("env/override-kept", env.get("GIT_AUTHOR_NAME"), "Fixture Author")
    check("env/non-git-preserved", env.get(SENTINEL), "kept")

    # F-367: a fixture commit must not spawn automatic maintenance (an unpinned commit
    # LAUNCHES the DETACHED `git maintenance run --auto` / `git gc --auto` child; once the
    # automatic-work thresholds are met, that child can repack or prune the fixture's
    # .git/objects after the commit returned, racing a later copytree/rmtree/read of that
    # repository). The green leg proves the pinned env spawns none; the red leg strips
    # exactly the maintenance pins and must OBSERVE the child launching, so the probe is
    # proven discriminating, never vacuous.
    check("env/no-auto-maintenance",
          _auto_maintenance_children(base / "maintenance-green",
                                     _git_fixture_env.git_fixture_env()), [])
    unpinned = _git_fixture_env.git_fixture_env()
    for name in _git_fixture_env._MAINTENANCE_PIN_VARS:
        del unpinned[name]
    red = _auto_maintenance_children(base / "maintenance-red", unpinned)
    check("env/no-auto-maintenance-red",
          red if isinstance(red, str) else bool(red), True)
    (base / "trace2-tail").mkdir()
    _trace2_tail_checks(base / "trace2-tail")

    # Layer 4, the tripwire leg: the repo-wide scan must report NO unpinned
    # maintenance-capable launch (green), and a planted unpinned fixture commit must be
    # reported while its pinned twin is not (the red leg proves the scan discriminating,
    # never vacuous).
    check("env/maintenance-pin-scan", _maintenance_pin_scan(ROOT), [])
    planted_root = base / "pin-scan-planted"
    (planted_root / "tools").mkdir(parents=True)
    (planted_root / "opf" / "tools").mkdir(parents=True)
    (planted_root / "tools" / "planted_unpinned.py").write_text(
        "import subprocess\n\n\n"
        "def _seed(repo):\n"
        "    subprocess.run(['git', '-C', str(repo), 'commit', '-q', '-m', 'x'],\n"
        "                   check=True, capture_output=True, timeout=30)\n",
        encoding="utf-8")
    (planted_root / "tools" / "planted_pinned.py").write_text(
        "import subprocess\n\n\n"
        "def _seed(repo):\n"
        "    subprocess.run(['git', '-C', str(repo), '-c', 'gc.auto=0',\n"
        "                    '-c', 'gc.autoDetach=false',\n"
        "                    '-c', 'maintenance.auto=false',\n"
        "                    'commit', '-q', '-m', 'x'],\n"
        "                   check=True, capture_output=True, timeout=30)\n",
        encoding="utf-8")
    planted = _maintenance_pin_scan(planted_root, allow_missing_files=True)
    got = "planted scan: %r" % (planted,)
    if len(planted) == 1 and "tools/planted_unpinned.py" in planted[0] \
            and "planted_pinned" not in planted[0]:
        got = True
    check("env/maintenance-pin-scan-red", got, True)

    # Layer 4, the contract leg: the planted red/green table over every launch form and
    # false-flag form round-2 review demonstrated (see _SCAN_CONTRACT_CASES).
    check("env/maintenance-pin-scan-contract",
          _scan_contract_failures(base / "pin-scan-contract"), [])

    # The in-place form, in a CHILD interpreter under a fully poisoned environment: after
    # scrub_git_environment() the only GIT_-prefixed variables left are the three pins, and
    # HOME has left the poisoned value for the scratch home.
    child_code = "\n".join((
        "import json, os, sys",
        "sys.path.insert(0, sys.argv[1])",
        "import _git_fixture_env",
        "_git_fixture_env.scrub_git_environment()",
        "left = sorted([k, v] for k, v in os.environ.items() if k.startswith('GIT_'))",
        "print(json.dumps([left, os.environ.get('HOME')]))",
    ))
    child_env = dict(os.environ)
    child_env.update(POISON_VARS)
    child_env["HOME"] = "/decoy/home"
    try:
        child = subprocess.run(
            [sys.executable, "-I", "-c", child_code, str(ROOT / "tools")],
            env=child_env, capture_output=True, text=True, timeout=60)
        payload = json.loads(child.stdout)
        got = (child.returncode, payload[0], payload[1] != "/decoy/home")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        got = "scrub child failed: {}".format(exc)
    check("env/scrub-in-place-child", got,
          (0, [["GIT_CONFIG_COUNT", "3"], ["GIT_CONFIG_GLOBAL", os.devnull],
               ["GIT_CONFIG_KEY_0", "gc.auto"], ["GIT_CONFIG_KEY_1", "gc.autoDetach"],
               ["GIT_CONFIG_KEY_2", "maintenance.auto"], ["GIT_CONFIG_NOSYSTEM", "1"],
               ["GIT_CONFIG_SYSTEM", os.devnull], ["GIT_CONFIG_VALUE_0", "0"],
               ["GIT_CONFIG_VALUE_1", "false"], ["GIT_CONFIG_VALUE_2", "false"]], True))

    # caller_env_without_git(), in a CHILD interpreter under full poison, with HOME at a
    # scratch caller home whose .gitconfig carries the trust surface: after the in-place
    # scrub, the caller env drops every GIT_* variable, retains the PRE-scrub HOME (while the
    # process environment stays scrubbed), and a `git config --global` read under it sees the
    # caller's global config again (the mechanism behind safe.directory trust on a checkout
    # owned by another uid), while the fixture env still cannot.
    caller_home = base / "caller-home"
    caller_home.mkdir()
    (caller_home / ".gitconfig").write_text(
        "[safe]\n\tdirectory = *\n[aiqt]\n\tfixtureprobe = caller\n", encoding="utf-8")
    trust_code = "\n".join((
        "import json, os, subprocess, sys",
        "sys.path.insert(0, sys.argv[1])",
        "import _git_fixture_env",
        "pre_home = os.environ.get('HOME')",
        "_git_fixture_env.scrub_git_environment()",
        "_git_fixture_env.scrub_git_environment()",
        "caller = _git_fixture_env.caller_env_without_git()",
        "fixture = _git_fixture_env.git_fixture_env()",
        "probe = ['git', 'config', '--global', 'aiqt.fixtureprobe']",
        "c = subprocess.run(probe, env=caller, capture_output=True, text=True, timeout=60)",
        "f = subprocess.run(probe, env=fixture, capture_output=True, text=True, timeout=60)",
        "print(json.dumps([sorted(k for k in caller if k.startswith('GIT_')),",
        "                  caller.get('HOME') == pre_home,",
        "                  os.environ.get('HOME') == pre_home,",
        "                  [c.returncode, c.stdout.strip()], f.returncode == 0]))",
    ))
    trust_env = dict(os.environ)
    trust_env.update(POISON_VARS)
    trust_env["HOME"] = str(caller_home)
    try:
        child = subprocess.run(
            [sys.executable, "-I", "-c", trust_code, str(ROOT / "tools")],
            env=trust_env, capture_output=True, text=True, timeout=120)
        payload = json.loads(child.stdout)
        got = (child.returncode, payload[0], payload[1], payload[2],
               payload[3], payload[4])
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        got = "caller-env child failed: {}".format(exc)
    check("env/caller-env-restores-trust-config", got,
          (0, [], True, False, [0, "caller"], False))

    # Stop the OPF suite at its first in-process invocation, after its fixture
    # isolation. Observe the actual inherited environment and its finally restoration.
    opf_code = "\n".join((
        "import json, os, sys",
        "sys.path.insert(0, sys.argv[1])",
        "import check_opf_init",
        "saved = dict(os.environ)",
        "seen = []",
        "class StopProbe(Exception): pass",
        "def invoke(args):",
        "    seen.append(dict(os.environ))",
        "    raise StopProbe()",
        "check_opf_init._suite(invoke)",
        "expected = {'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,",
        "            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0',",
        "            'GIT_OPTIONAL_LOCKS': '0'}",
        "print(json.dumps([len(seen) == 1 and",
        "    {k: v for k, v in seen[0].items() if k.startswith('GIT_')} == expected,",
        "    bool(seen) and seen[0].get('PATH') == saved.get('PATH'),",
        "    dict(os.environ) == saved]))",
    ))
    opf_env = dict(os.environ)
    opf_env.update(POISON_VARS)
    try:
        child = subprocess.run(
            [sys.executable, "-I", "-B", "-c", opf_code, str(ROOT / "opf" / "tools")],
            env=opf_env, capture_output=True, text=True, timeout=60)
        got = (child.returncode, json.loads(child.stdout))
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        got = "OPF isolation child failed: {}".format(exc)
    check("env/opf-init-inherited-allowlist", got, (0, [True, True, True]))

    _system_pin_checks(base)

    # ---------- layer 2: per-member routing ----------
    for check_id, member_rel, scrub_names in (
            ("route/selftest-aiqt-corpus", "tools/selftest_aiqt_corpus.py",
             ("git_fixture_env", "scrub_git_environment")),
            ("route/selftest-orch-hooks", "tools/selftest_orch_hooks.py",
             ("scrub_git_environment",)),
            ("route/qa-adapter", "tools/_qa_adapter.py", ("git_fixture_env",)),
            ("route/check-record-drift", "tools/check_record_drift.py",
             ("scrub_git_environment",)),
            ("route/check-mistakes-register", "tools/check_mistakes_register.py",
             ("scrub_git_environment",)),
            ("route/check-version-monotonicity", "tools/check_version_monotonicity.py",
             ("scrub_git_environment",)),
            ("route/check-release-build", "tools/check_release_build.py",
             ("scrub_git_environment",)),
            ("route/check-release-delta", "tools/check_release_delta.py",
             ("scrub_git_environment",)),
            ("route/selftest-aiqt-hooks", "tools/selftest_aiqt_hooks.py",
             ("scrub_git_environment",)),
    ):
        check(check_id, _calls_any(ROOT / member_rel, scrub_names), True)

    for check_id, member, owner, binding, factory, launches in (
            ("route/check-record-sections", "tools/check_record_sections.py",
             "_selftest_git", "env", "git_fixture_env", True),
            ("route/check-portability", "tools/check_portability.py",
             "_self_test_main_isolated", "git_env", "git_fixture_env", True),
            ("route/gen-manifest", "tools/gen_manifest.py",
             "_git", "env", "git_fixture_env", "computed"),
            ("route/check-release-delta-env", "tools/check_release_delta.py",
             "_selftest_env", "env", "git_fixture_env", False),
            ("route/check-release-delta-init", "tools/check_release_delta.py",
             "_git_init_commit", "env", "git_fixture_env", True),
            ("route/check-release-delta-spy", "tools/check_release_delta.py",
             "_spy_index", "senv", "git_fixture_env", True),
            ("route/check-release-build-attestation", "tools/check_release_build.py",
             "_self_test_main_isolated", "ge", "git_fixture_env", "attestation"),
            ("route/check-branch-root", "tools/check_branch_root.py",
             "_fixture_git", "env", "git_fixture_env", "computed"),
            ("route/check-gensrc-failclose", "tools/check_gensrc_failclose.py",
             "_git_fixture", "env", "git_fixture_env", True),
            ("route/qa-adapter-fixture-env", "tools/_qa_adapter.py",
             "_self_test_isolated", "genv", "git_fixture_env", True),
            ("route/check-opf-init", "opf/tools/check_opf_init.py",
             "_suite_isolated", "fixture_env", "_scrubbed_env", False),
    ):
        check(check_id, _binding_calls(ROOT / member, owner, binding, factory, launches), True)
    for check_id, member, owner in (
            ("scope/check-manifest", "tools/check_manifest.py", "self_test_main"),
            ("scope/check-record-sections", "tools/check_record_sections.py", "self_test"),
            ("scope/qa-adapter", "tools/_qa_adapter.py", "_self_test"),
            ("scope/gen-manifest", "tools/gen_manifest.py", "self_test_main"),
    ):
        check(check_id, _scrub_scoped_first(ROOT / member, owner,
                                           "fixture_git_lifecycle"), True)
    check("trust/caller-env-archive-only", _caller_env_archive_only(), True)

    check("route/selftest-aiqt-hooks-fixture-env",
          _archive_reads_use_caller_env(ROOT / "tools" / "selftest_aiqt_hooks.py",
                                       fixture_calls=True), True)
    check("route/selftest-aiqt-corpus-fixture-env",
          _scrub_scoped_first(CORPUS_SELFTEST, "GitTests._init_repo", "git_fixture_env"),
          True)

    # Function-SCOPED routing: _calls_any proves the call SITE exists somewhere in the file,
    # so the corpus row (either name accepted) stayed green with only one of its two
    # protections removed. Each named self-test entry must itself lead with the in-place
    # scrub, a top-level call with no process-launching call before it.
    for check_id, member_rel, func_name in (
            ("scope/check-portability", "tools/check_portability.py", "self_test_main"),
            ("scope/selftest-aiqt-corpus-setup", "tools/selftest_aiqt_corpus.py", "GitTests.setUp"),
            ("scope/selftest-orch-hooks", "tools/selftest_orch_hooks.py", "main"),
            ("scope/selftest-aiqt-hooks", "tools/selftest_aiqt_hooks.py", "main"),
            ("scope/check-record-drift", "tools/check_record_drift.py", "self_test"),
            ("scope/check-mistakes-register", "tools/check_mistakes_register.py",
             "self_test"),
            ("scope/check-version-monotonicity", "tools/check_version_monotonicity.py",
             "self_test_main"),
            ("scope/check-release-build", "tools/check_release_build.py", "self_test_main"),
            ("scope/check-release-delta", "tools/check_release_delta.py", "self_test_main"),
    ):
        check(check_id, _scrub_scoped_first(ROOT / member_rel, func_name,
                                            "scrub_git_environment"), True)

    # The REAL-repository reads (the safe.directory regression): every literal
    # `git ... archive ...` launch in the two members that read the checkout under test must
    # pass env=caller_env_without_git(), or a checkout trusted only through the caller's
    # global config regresses to a rc=128 refusal under the in-place scrub.
    for check_id, member_rel in (
            ("trust/check-release-build-archive-env", "tools/check_release_build.py"),
            ("trust/check-release-delta-archive-env", "tools/check_release_delta.py"),
    ):
        check(check_id, _archive_reads_use_caller_env(ROOT / member_rel), True)

    _roster_checks()
    _opf_lifecycle_graph_checks()
    _member_timeout_controls(base)
    _member_timeout_vectors(base)

    # A lane RETURN VALUE can only fail the suite, never pass it: each vector runs the
    # production main() in a child over an injected lane that returns 0 (nothing executed;
    # every expected id failed; a missing expectation manifest; a pending cannot-evaluate
    # record), exits 0 on the reverted lane-return guard, and must exit 2 here.
    lane_fixture = base / "lane-return"
    lane_fixture.mkdir()
    observed = _lane_return_fail_closed(lane_fixture)
    check("config/lane-return-fail-closed", observed, LANE_RETURN_EXPECTED,
          _partial_timeout_cause(observed, LANE_RETURN_EXPECTED))

    _manifest_setup_failures(base)
    _manifest_extra_setup_failures()

    # ---------- layer 3: the end-to-end leak probe ----------
    decoy = _build_decoy(base)
    if decoy is None:
        return 2
    neutral_cwd = base / "neutral"
    neutral_cwd.mkdir()
    index_path = decoy / ".git" / "index"
    index_before = index_path.read_bytes()
    snap_before = _snapshot_git_dir(decoy)

    rc = _run_corpus("GIT_INDEX_FILE", str(index_path), neutral_cwd)
    check("leak/index-poison-fixture-green", rc, 0)
    check("leak/index-poison-caller-unchanged",
          (index_path.read_bytes() == index_before,
           _snapshot_git_dir(decoy) == snap_before),
          (True, True))

    rc = _run_corpus("GIT_DIR", str(decoy / ".git"), neutral_cwd)
    check("leak/gitdir-poison-fixture-green", rc, 0)
    check("leak/gitdir-poison-caller-unchanged", _snapshot_git_dir(decoy) == snap_before, True)

    # The IMPORTED corpus surface: run_self_test() bypasses __main__ and its entry-point
    # scrub, so this lane stays green only if the fixture lifecycle (GitTests.setUp) scrubs
    # itself.
    rc = _run_corpus_imported("GIT_DIR", str(decoy / ".git"), neutral_cwd)
    check("leak/imported-gitdir-poison-fixture-green", rc, 0)
    check("leak/imported-gitdir-poison-caller-unchanged",
          _snapshot_git_dir(decoy) == snap_before, True)


if __name__ == "__main__":
    _gfe.SUITE_ID = SUITE_ID
    _selftest_exit_report.exit_with(_gfe.main(_gfe._parse_argv(sys.argv[1:]), _core_lanes))
