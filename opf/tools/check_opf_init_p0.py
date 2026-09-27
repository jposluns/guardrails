#!/usr/bin/env python3
"""P0 contract vectors and source reversions; runner fixtures use private scratch.

The D2a fixture retains the landed source-only builders' bytes in memory before
any P0 guard is exercised. Reversions receive those same intact fixtures.
Runner verification executes the real standalone shell dispatcher with other
gates intercepted, then requires this suite's actual assertion identities.
That check proves P0 dispatch, not that the other standalone gates passed.
"""
import argparse
import copy
import hashlib
import subprocess
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _opf_init as builders  # noqa: E402
import _opf_init_contract as contract  # noqa: E402
import _opf_init_operation as operation  # noqa: E402
import _opf_init_p0 as p0  # noqa: E402

OP_ID = "abcdef01-2345-4678-9abc-def012345678"


def check(condition, identity):
    if not condition:
        raise AssertionError(identity)


def fixtures():
    ctx = contract._mk_ctx([])
    plan, raw = operation.build_init_plan(
        operation_id=OP_ID, binding=ctx["binding"], head=ctx["head"],
        inventory_digest_value=contract._digest(contract.canonical_json_bytes(ctx["inventory"])),
        application_time="2026-01-01T00:00:00Z")
    kept, kept_raw = operation.build_init_plan(
        operation_id=OP_ID, binding=ctx["binding"], head=ctx["head"],
        inventory_digest_value=plan["inventory_digest"], application_time=plan["application_time"],
        existing_changelog={"path": "CHANGELOG.md", "mode": 0o644, "size": 3,
                            "digest": contract._digest(b"old")})
    # The source-only fixture follows opf._cmd_init, not the coupled plan's provenance roster.
    home = operation._MACHINE_HOME + "/"
    d2a = {
        home + "manifest.toml": builders.build_manifest().encode(),
        home + "counters.toml": builders.build_counters().encode(),
        home + "version.toml": builders.build_version().encode(),
        home + "worklog.toml": builders.build_worklog().encode(),
        ".opf.toml": b'[store]\ntarget = "dir:."\n',
        "CHANGELOG.md": b"# Changelog\n",
    }
    for name in builders.INDEX_TYPES:
        d2a[home + name + ".index.toml"] = builders.build_index(name).encode()
    check(operation.PROVENANCE_RELPATH not in d2a, "fixture/d2a-no-provenance")
    old = dict(d2a)
    old[p0.MANIFEST_PATH] = old[p0.MANIFEST_PATH].replace(b'"1.2.0"', b'"1.1.0"')
    result = operation.InitResult()
    result.status = operation.VIEWS_READY
    run = types.SimpleNamespace(result=result, plan=plan, phases=[], checks=[], back=None)
    outcome = contract.canonical_json_bytes(operation._outcome(run, None))
    return plan, raw, kept, kept_raw, d2a, old, outcome


def vectors(f):
    plan, raw, kept, kept_raw, d2a, old, outcome = f
    cases = []

    def add(name, call, status="VALID", code=None):
        cases.append((name, call, status, code))

    def altered(change, source=plan):
        doc = copy.deepcopy(source)
        change(doc)
        doc["plan_digest"] = operation.compute_plan_digest(doc)
        return contract.canonical_json_bytes(doc)

    def plan_case(name, change, code, source=plan, completed=False):
        data = altered(change, source)
        add(name, lambda m: m.validate_plan(data, completed=completed), "REFUSED", code)

    add("accept/plan", lambda m: m.validate_plan(raw))
    add("accept/preserved-changelog", lambda m: m.validate_plan(kept_raw))
    add("accept/outcome", lambda m: m.validate_outcome(outcome, operation_id=OP_ID))
    add("accept/empty-staging", lambda m: m.validate_staging_membership(raw, []))
    add("accept/hypothetical-S",
        lambda m: m.validate_staging_membership(raw, [e["path"] for e in plan["sets"]["S"]]))
    plan_case("refuse/overlap",
              lambda d: d["sets"]["V"].append(copy.deepcopy(d["sets"]["C"][0])), "OVERLAP")
    plan_case("refuse/unclassified-S",
              lambda d: d["sets"]["S"].append({"path": ".working/unknown.toml"}), "UNCLASSIFIED")
    plan_case("refuse/unclassified-V",
              lambda d: d["sets"]["V"].append({"path": ".working/EXTRA.md"}), "UNCLASSIFIED")
    plan_case("refuse/sets-key", lambda d: d["sets"].update(I=[]), "SETS")
    plan_case("refuse/K", lambda d: d["sets"]["K"].append({"path": ".working/kept.txt"}), "SETS")
    plan_case("refuse/C", lambda d: d["sets"].update(C=[]), "UNCLASSIFIED")
    plan_case("refuse/E", lambda d: d["sets"].update(E=[{"path": "OTHER.md"}]), "UNCLASSIFIED")
    plan_case("refuse/order", lambda d: d["sets"]["S"].reverse(), "SETS")
    for name, value in (("unknown", "opf.init.plan/v999"), ("older", "opf.init.plan/v0")):
        plan_case("refuse/plan-version-" + name, lambda d, v=value: d.update(format=v), "VERSION")
    plan_case("refuse/plan-schema", lambda d: d.update(schema=True), "VERSION")
    for key in ("spec_version", "generator", "provenance_format", "views_generator"):
        plan_case("refuse/pin-" + key,
                  lambda d, k=key: d["versions"].update({k: "unknown"}), "VERSION")
    historical = altered(lambda d: d["versions"]["views_generator"].update(version="recorded-old"))
    add("accept/completed-recorded-generator",
        lambda m: m.validate_plan(historical, completed=True))
    add("refuse/interrupted-recorded-generator",
        lambda m: m.validate_plan(historical), "REFUSED", "VERSION")
    plan_case("refuse/completed-spec-pin",
              lambda d: d["versions"].update(spec_version="1.1.0"), "VERSION", completed=True)
    plan_case("refuse/staging-v1",
              lambda d: d.update(staging_set=[d["sets"]["S"][0]["path"]]), "STAGING")
    plan_case("refuse/boundaries",
              lambda d: d["publication_boundaries"].update(index="staged"), "STAGING")
    plan_case("refuse/integration-key", lambda d: d.update(integration_set=[]), "MALFORMED")
    plan_case("refuse/recovery", lambda d: d.update(recovery_policy="reinterpret"), "MALFORMED")
    plan_case("refuse/source-mode",
              lambda d: d["sets"]["S"][0].update(mode=0o600), "MALFORMED")
    plan_case("refuse/hidden-stage",
              lambda d: d["sets"]["S"][0].update(staging="other"), "MALFORMED")
    plan_case("refuse/payload",
              lambda d: d["sets"]["S"][0].update(payload=""), "MALFORMED")
    plan_case("refuse/E-schema",
              lambda d: d["sets"]["E"][0].update(extra=True), "MALFORMED", source=kept)
    for label, path in (
        ("V", plan["sets"]["V"][0]["path"]), ("K", ".working/kept.txt"),
        ("E", "CHANGELOG.md"), ("C", operation.LEASE_RELPATH),
        ("directory", ".working/toml"), ("unclassified", ".adapter"),
    ):
        # K is hypothetical: v1 has no admitted Keep entry. This path remains non-S.
        add("refuse/staged-" + label,
            lambda m, p=path: m.validate_staging_membership(kept_raw, [p]), "REFUSED", "STAGING")
    add("refuse/staged-duplicate",
        lambda m: m.validate_staging_membership(raw, [".opf.toml", ".opf.toml"]),
        "REFUSED", "STAGING")
    doc = operation._opf_init_substrate._strict_json_loads(outcome, "fixture")
    for name, value in (("unknown", "opf.init.outcome/v999"), ("older", "opf.init.outcome/v0")):
        bad = contract.canonical_json_bytes(dict(doc, format=value))
        add("refuse/outcome-version-" + name,
            lambda m, b=bad: m.validate_outcome(b, operation_id=OP_ID), "REFUSED", "VERSION")
    add("refuse/outcome-binding",
        lambda m: m.validate_outcome(outcome, operation_id="0" * 36), "REFUSED", "SCHEMA")
    for status in sorted(p0.RESULT_STATUSES):
        def result_case(m, s=status):
            r = operation.InitResult()
            r.status = s
            return m.validate_result(r, contract_kind="coupled")
        add("accept/result-" + status, result_case)
    add("accept/source-only-zero", lambda m: m.validate_result(0, contract_kind="source-only"))
    add("refuse/zero-as-coupled", lambda m: m.validate_result(0, contract_kind="coupled"),
        "REFUSED", "RESULT")
    add("refuse/coupled-as-source",
        lambda m: m.validate_result(operation.InitResult(), contract_kind="source-only"),
        "REFUSED", "RESULT")
    def bad_status(m):
        r = operation.InitResult()
        r.status = "SOURCES-READY"
        return m.validate_result(r, contract_kind="coupled")
    add("refuse/result-status", bad_status, "REFUSED", "RESULT")
    def missing_status(m):
        r = operation.InitResult()
        del r.status
        return m.validate_result(r, contract_kind="coupled")
    add("refuse/result-missing-status", missing_status, "REFUSED", "RESULT")
    manifest = d2a[p0.MANIFEST_PATH]
    add("compat/d2a-retained", lambda m: m.validate_compatibility(manifest))
    provenance = operation.plan_payloads(plan)[operation.PROVENANCE_RELPATH]
    add("compat/provenance-present",
        lambda m: m.validate_compatibility(manifest, provenance=provenance))
    add("compat/bad-provenance",
        lambda m: m.validate_compatibility(manifest, provenance=b"not toml"),
        "REFUSED", "COMPATIBILITY")
    add("compat/old-needs-upgrade", lambda m: m.validate_compatibility(old[p0.MANIFEST_PATH]),
        "REFUSED", "VERSION")
    add("compat/version-only-upgrade", lambda m: m.validate_upgrade_delta(old, d2a))
    invented = dict(d2a, **{operation.PROVENANCE_RELPATH: provenance})
    add("compat/no-invented-provenance",
        lambda m: m.validate_upgrade_delta(old, invented), "REFUSED", "UPGRADE")
    changed = dict(d2a)
    changed[operation.COUNTERS_RELPATH] += b"# changed\n"
    add("compat/no-counter-change",
        lambda m: m.validate_upgrade_delta(old, changed), "REFUSED", "UPGRADE")
    other = dict(d2a)
    other[p0.MANIFEST_PATH] = manifest.replace(b'posture = "required"', b'posture = "optional"')
    add("compat/no-other-field", lambda m: m.validate_upgrade_delta(old, other), "REFUSED", "UPGRADE")
    def homes(m):
        # Verify the generation fence even if a later resolver activates generation 2.
        from unittest.mock import patch
        with patch.object(m.store, "SUPPORTED_HOMES", 2):
            return m.validate_compatibility(manifest)
    add("compat/homes-fence", homes, "REFUSED", "HOMES")
    for name, value in (("missing", None), ("empty", b""), ("not-object", b"[]\n"),
                        ("duplicate", b'{"schema":1,"schema":1}\n'), ("truncated", raw[:-4])):
        add("input/" + name, lambda m, v=value: m.validate_plan(v),
            "CANNOT-EVALUATE" if value is None else "REFUSED",
            "INPUT" if value is None or value == b"" else "MALFORMED")
    return cases


def fingerprint(f):
    # Includes each retained file body, not a success marker or merely its path.
    return hashlib.sha256(repr(f).encode()).hexdigest()


def run_case(module, case, f):
    name, call, status, code = case
    before = fingerprint(f)
    got = call(module)
    check(got.status == status and (code is None or
          len(got.findings) == 1 and got.findings[0][0] == code), name)
    check(fingerprint(f) == before, name + "/fixture-unchanged")


def run_vectors(module, f):
    cases = vectors(f)
    check(len({c[0] for c in cases}) == len(cases), "harness/unique-identities")
    for case in cases:
        run_case(module, case, f)
        print("PASS " + case[0])
    return tuple(c[0] for c in cases)


# Exact source edits, each disabling one admission/refusal guard or dispatch.
# A named assertion is the only accepted RED. Other exceptions are harness failures.
REVERSIONS = (
    ("plan-dispatch", 'return _plan(raw, completed)',
     'raise _Refusal("DISPATCH", "plan", "v1 dispatch removed")', "accept/plan"),
    ("outcome-dispatch", 'return substrate._validate_outcome(raw, operation_id)',
     'raise _Refusal("DISPATCH", "outcome", "v1 dispatch removed")', "accept/outcome"),
    ("overlap", 'len(paths) == len(set(paths))', 'True', "refuse/overlap"),
    ("source-roster", 'set(sources) == roster and len(sources) == len(roster)',
     'True', "refuse/unclassified-S"),
    ("view-roster", 'sets["V"] == views', 'True', "refuse/unclassified-V"),
    ("plan-version", '_version(doc, PLAN_FORMATS, "plan")', 'pass', "refuse/plan-version-unknown"),
    ("outcome-version", '_version(doc, OUTCOME_FORMATS, "outcome")', 'pass',
     "refuse/outcome-version-unknown"),
    ("pins", 'plan["versions"] == expected', 'True', "refuse/pin-generator"),
    ("v1-staging", 'plan["staging_set"] == []', 'True', "refuse/staging-v1"),
    ("publication", 'plan["publication_boundaries"] == operation.PUBLICATION_BOUNDARIES',
     'True', "refuse/boundaries"),
    ("F19-admission", 'set(staged_paths) <= sources', 'False', "accept/empty-staging"),
    ("result-admission", 'type(value) is operation.InitResult and type(getattr(value, "status", None)) is str\n                 and value.status in RESULT_STATUSES',
     'False', "accept/result-VIEWS-READY"),
    ("source-type", 'type(value) is int and value in (0, 2)',
     'True', "refuse/coupled-as-source"),
    ("F19", 'set(staged_paths) <= sources', 'True', "refuse/staged-V"),
    ("optional-provenance", 'if provenance is not None:', 'if True:', "compat/d2a-retained"),
    ("upgrade-files", 'set(before) == set(after)\n             and all(before[p] == after[p] for p in before if p != MANIFEST_PATH)',
     'True', "compat/no-invented-provenance"),
    ("upgrade-field", 'new == expected', 'True', "compat/no-other-field"),
    ("homes", 'store.SUPPORTED_HOMES == 1 and store.homes_generation(manifest) == 1',
     'True', "compat/homes-fence"),
    ("source-result", 'type(value) is int and value in (0, 2)',
     'False', "accept/source-only-zero"),
    ("upgrade-dispatch", 'return new', 'raise _Refusal("DISPATCH", "upgrade", "removed")',
     "compat/version-only-upgrade"),
    ("completed-generator", 'if completed:', 'if False:', "accept/completed-recorded-generator"),
    ("coupled-type", 'type(value) is operation.InitResult and type(getattr(value, "status", None)) is str\n                 and value.status in RESULT_STATUSES',
     'True', "refuse/zero-as-coupled"),
)


def red_on_revert(source, f):
    cases = {c[0]: c for c in vectors(f)}
    expanded = []
    for row in REVERSIONS:
        expanded.append(row)
        guard, old, new, _identity = row
        extra = {
            "F19-admission": ("accept/hypothetical-S",),
            "result-admission": tuple("accept/result-" + s for s in sorted(p0.RESULT_STATUSES)
                                      if s != "VIEWS-READY"),
            "coupled-type": ("refuse/result-status", "refuse/result-missing-status"),
            "F19": ("refuse/staged-K", "refuse/staged-E", "refuse/staged-C"),
            "upgrade-files": ("compat/no-counter-change",),
            "pins": ("refuse/pin-spec_version", "refuse/pin-provenance_format",
                     "refuse/pin-views_generator", "refuse/interrupted-recorded-generator",
                     "refuse/completed-spec-pin"),
            "plan-version": ("refuse/plan-version-older", "refuse/plan-schema"),
            "outcome-version": ("refuse/outcome-version-older",),
            "plan-dispatch": ("accept/preserved-changelog",),
        }.get(guard, ())
        expanded.extend((guard, old, new, identity) for identity in extra)
    for guard, old, new, identity in expanded:
        check(source.count(old) == 1, "revert/" + guard + "/unique-source")
        module = types.ModuleType("_opf_init_p0_reverted")
        module.__file__ = p0.__file__
        exec(compile(source.replace(old, new), module.__file__, "exec"), module.__dict__)
        before = fingerprint(f)
        try:
            run_case(module, cases[identity], f)
        except AssertionError as exc:
            check(str(exc) == identity, "revert/" + guard + "/wrong-assertion")
        else:
            raise AssertionError("revert/" + guard + "/not-red")
        check(fingerprint(f) == before, "revert/" + guard + "/fixture-changed")
        print("RED {} -> {}".format(guard, identity))


def runner_check(expected, text=None, *, fail_own=0):
    import errno
    import fcntl
    import os
    import shlex
    import shutil
    import signal
    import stat
    import tempfile
    from contextlib import ExitStack

    identity = "runner/declared-test-executes"
    # Vector-only entry points never call this registration check.
    # Refuse an escaped fixture invocation before any shell can launch.
    if "p0_log" in os.environ:
        raise RuntimeError(identity + "/cannot-evaluate/recursion")
    # env -i removes the environment marker, but preserves pass_fds. Both
    # twins recognize the same private-file payload, without an env-supplied
    # descriptor number. /dev/fd must be enumerable; inspection errors refuse.
    marker_bytes = b"OPF runner registration recursion v1\n"
    try:
        for entry in os.listdir("/dev/fd"):
            fd = int(entry)
            try:
                info = os.fstat(fd)
            except OSError as exc:
                # The descriptor used to list /dev/fd has already closed.
                if exc.errno == errno.EBADF:
                    continue
                raise
            if stat.S_ISREG(info.st_mode) and info.st_size == len(marker_bytes):
                # An unrelated write-only log cannot be our readable marker.
                if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_WRONLY:
                    continue
                # Readable candidates still fail closed on inspection errors.
                if os.pread(fd, len(marker_bytes), 0) == marker_bytes:
                    raise RuntimeError(identity + "/cannot-evaluate/recursion")
    except (OSError, ValueError) as exc:
        raise RuntimeError(identity + "/cannot-evaluate/recursion-marker") from exc
    if type(fail_own) is not int or fail_own not in (0, 1, 2, 7):
        raise ValueError(identity + "/invalid-failure-code")
    here = Path(__file__).resolve().parent
    runner = here / "run_all_checks.sh"
    source = runner.read_text(encoding="utf-8") if text is None else text
    bash = shutil.which("bash")
    if bash is None:
        raise RuntimeError(identity + "/cannot-evaluate/bash")
    bash = os.path.abspath(bash)

    # This self-test asserts that the runner dispatches THIS suite exactly
    # once with its exact argv (RED duplicate-own-call and wrong-own-argv,
    # using the runtime argv log), and propagates exits 1, 2 and 7 (RED
    # own-suite-failure variants, discriminated by swallowed-own-failure
    # and tolerate-1/tolerate-2). Other nonzero statuses are not injected.
    # It does not assert that other registered suites are dispatched:
    # sibling dispatch completeness is outside this check; a runner-level
    # dispatch audit would be a separate control.
    # Fix-4 accounting: in-runner PATH changes remain covered by the
    # function shim (PASS in-runner-path; RED removed-function-shim).
    # The PATH fixture also covers child shells, command and env forms.
    # PD-SFS-THREAT-BOUND: runners written to defeat this check are outside
    # scope, including wrappers recognizing injected output and swallowing
    # real SELF-TEST FAIL, PACK-MANIFEST FAIL or CANNOT diagnostics, and bare
    # alternate interpreter names such as python3.14. No mechanism covers them.
    # Absolute paths, or bypassing the function together with changing PATH,
    # remain outside interception. Deliberately closing inherited descriptors
    # while clearing the environment can bypass recursion refusal and spawn
    # nested sessions outside timeout killpg containment. Not a process sandbox.
    # Intercepted siblings return 0; their failure propagation is outside
    # this check too.
    fixture = r'''#!/bin/sh
printf '%s\0' "$#" "$@" >> "$p0_log" || exit 2
if [ "$#" -eq 5 ] && [ "$1" = "-I" ] && [ "$2" = "-B" ] \
    && [ "$3" = "$p0_test" ] && [ "$4" = "--self-test" ] \
    && [ "$5" = "--red-on-revert" ]; then
  if [ "$p0_fail_own" -ne 0 ]; then
    "$p0_python" -I -B "$p0_test" --self-test --vectors-only || exit "$?"
    exit "$p0_fail_own"
  fi
  exec "$p0_python" -I -B "$p0_test" --self-test --vectors-only
fi
case " $* " in *check_opf_init_p0.py*) exit 2;; esac
exit 0
'''
    # Preserve ordinary caller variables (including CI) so conditional
    # dispatch is exercised. Remove the execution controls listed below, then
    # pin configuration and fixture variables; not an environment sandbox.
    # Match main's caller-cwd dispatch while keeping fixture files private.
    caller_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="opf-p0-registration-") as tmp, ExitStack() as resources:
        os.chmod(tmp, 0o700)
        marker = resources.enter_context(tempfile.TemporaryFile(dir=tmp))
        marker.write(marker_bytes)
        marker.flush()
        if os.pathsep in tmp:
            raise RuntimeError(identity + "/cannot-evaluate/pathsep")
        executable = Path(tmp) / "python3"
        executable.write_text(fixture, encoding="utf-8")
        executable.chmod(0o700)
        log = Path(tmp) / "argv.log"
        log.write_bytes(b"")
        log.chmod(0o600)
        env = {name: value for name, value in os.environ.items()
               if name not in ("BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "PS4")
               and not name.startswith(("GIT_", "BASH_FUNC_", "PYTHON", "LD_"))}
        env.update({name: tmp for name in env if name.startswith("XDG_")})
        env.update({"PATH": tmp + os.pathsep + os.defpath, "TMPDIR": tmp,
                    "HOME": tmp, "XDG_CONFIG_HOME": tmp, "XDG_CACHE_HOME": tmp,
                    "XDG_DATA_HOME": tmp, "XDG_STATE_HOME": tmp,
                    "XDG_CONFIG_DIRS": tmp, "XDG_DATA_DIRS": tmp,
                    "XDG_RUNTIME_DIR": tmp, "LC_ALL": "C",
                    "PYTHONDONTWRITEBYTECODE": "1", "p0_log": str(log),
                    "p0_python": sys.executable,
                    "p0_test": str(here / "check_opf_init_p0.py"),
                    "p0_fail_own": str(fail_own)})

        def run_shell(body):
            with subprocess.Popen(
                    [bash, "--noprofile", "--norc", "-c", body, str(runner)],
                    cwd=caller_cwd, env=env, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, start_new_session=True,
                    pass_fds=(marker.fileno(),)) as proc:
                try:
                    stdout, stderr = proc.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    # Kill descendants even when the shell itself has exited.
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    try:
                        proc.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        # An escaped session may still hold a pipe. Closing it
                        # bounds collection; such processes are not contained.
                        proc.stdout.close()
                        proc.stderr.close()
                    raise RuntimeError(identity + "/cannot-evaluate/timeout") from None
                return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)

        # Probe with exactly the runner's cwd, flags and environment. A noexec
        # fixture or unusable PATH must never fall through to the real gates.
        # Requires dirname on this PATH and executable /bin/sh; absence
        # fails closed as cannot-evaluate/interception, never a clean skip.
        probe = run_shell("type -P dirname >/dev/null && test -x /bin/sh && "
                          "type -P python3")
        if probe.returncode != 0 or probe.stdout != str(executable) + "\n":
            raise RuntimeError(identity + "/cannot-evaluate/interception")
        # Exec only in the function subshell, so the runner can continue.
        shim = "python3() ( exec " + shlex.quote(str(executable)) + ' "$@" );\n'
        proc = run_shell(shim + source)
        try:
            argv_log = log.read_bytes()
        except OSError as exc:
            raise RuntimeError(identity + "/cannot-evaluate/argv-log") from exc

    # NUL-framed records begin with argc. Any argument containing THIS
    # basename counts as an own-call attempt, even embedded -c source; require
    # one exact argv. Incidental mentions are conservatively attempts too.
    # Dynamically constructed names without that substring are not classified.
    # Diagnose malformed own calls before their exit or missing output.
    # Absent calls retain the existing return-code/pass-lines identities.
    fields = argv_log.split(b"\0")
    if fields.pop() != b"":
        raise AssertionError(identity + "/own-argv")
    own = []
    offset = 0
    basename = os.fsencode(Path(env["p0_test"]).name)
    while offset < len(fields):
        count = fields[offset]
        offset += 1
        try:
            argc = int(count)
        except ValueError as exc:
            raise AssertionError(identity + "/own-argv") from exc
        if count != str(argc).encode("ascii") or argc < 0 or argc > len(fields) - offset:
            raise AssertionError(identity + "/own-argv")
        argv = tuple(fields[offset:offset + argc])
        offset += argc
        if any(basename in arg for arg in argv):
            own.append(argv)
    own_argv = tuple(os.fsencode(arg) for arg in (
        "-I", "-B", env["p0_test"], "--self-test", "--red-on-revert"))
    if own and own != [own_argv]:
        raise AssertionError(identity + "/own-argv")

    reached = tuple(line[5:] for line in proc.stdout.splitlines() if line.startswith("PASS "))
    check(proc.returncode == 0, identity + "/return-code")
    check(reached == expected, identity + "/pass-lines")

    if own != [own_argv]:
        raise AssertionError(identity + "/own-argv")


def _runner_write_only_fd_check():
    import os
    import subprocess
    import tempfile

    identity = "runner/declared-test-executes/inherited-write-only-fd"
    # Run the full self-test with a genuine inherited 37-byte O_WRONLY fd.
    # Suppress only this self-spawning case in the child; keep its other
    # vectors, registration checks and REDs enabled.
    with tempfile.TemporaryDirectory(prefix="opf-write-only-fd-") as tmp:
        os.chmod(tmp, 0o700)
        log = Path(tmp) / "ordinary.log"
        log.write_bytes(b"x" * 37)
        log.chmod(0o600)
        fd = os.open(log, os.O_WRONLY | os.O_APPEND)
        try:
            child = (
                "import fcntl, importlib, os, sys\n"
                "from pathlib import Path\n"
                "from unittest.mock import patch\n"
                f"assert os.fstat({fd}).st_size == 37\n"
                f"assert fcntl.fcntl({fd}, fcntl.F_GETFL) & os.O_ACCMODE == os.O_WRONLY\n"
                "sys.argv = sys.argv[1:]\n"
                "sys.path.insert(0, str(Path(sys.argv[0]).parent))\n"
                "module = importlib.import_module(Path(sys.argv[0]).stem)\n"
                "with patch.object(module, '_runner_write_only_fd_check'):\n"
                "    raise SystemExit(module.main())\n")
            try:
                # Deliberate fast-fail ceiling for this regression child.
                proc = subprocess.run(
                    [sys.executable, "-I", "-B", "-c", child,
                     str(Path(__file__).resolve()), "--self-test", "--red-on-revert"],
                    pass_fds=(fd,), capture_output=True, text=True, timeout=300)
            except Exception as exc:
                raise AssertionError(identity) from exc
            if proc.returncode != 0:
                raise AssertionError(identity)
        finally:
            os.close(fd)
    print("PASS " + identity)


def runner_red_checks(expected):
    import os
    import shlex
    import subprocess
    import tempfile
    from unittest.mock import patch

    runner = Path(__file__).resolve().parent / "run_all_checks.sh"
    source = runner.read_text(encoding="utf-8")
    identity = "runner/declared-test-executes"
    anchor = '  local name="$1"; shift\n'
    if source.count(anchor) != 1:
        raise AssertionError(identity + "/red-fixture")

    def red(label, call, error, wanted):
        try:
            call()
        except error as exc:
            if str(exc) != wanted:
                raise AssertionError(identity + "/" + label + "/wrong-red") from exc
        except Exception as exc:
            raise AssertionError(identity + "/" + label + "/wrong-error") from exc
        else:
            raise AssertionError(identity + "/" + label + "/not-red")
        print("RED {} -> {}".format(label, wanted))

    # Set CI in the caller, not in the constructed runner environment.
    with patch.dict(os.environ, {"CI": "true"}):
        red("ci-conditional-skip", lambda: runner_check(
            expected, source.replace(
                anchor, anchor + '  case "${CI:-}:$name" in '
                '?*:opf-init-p0-selftest) return 0;; esac\n', 1)),
            AssertionError, identity + "/pass-lines")

    # Exercise the lost main case from the repository (or standalone) root.
    from contextlib import chdir
    root = runner.parents[2]
    relative_runner = runner.relative_to(root)
    with chdir(root):
        if not relative_runner.is_file():
            raise AssertionError(identity + "/cwd-fixture")
        red("cwd-conditional-skip", lambda: runner_check(
            expected, source.replace(
                anchor, anchor + '  if [ -e ' + shlex.quote(str(relative_runner)) + ' ]; then '
                'case "$name" in opf-init-p0-selftest) return 0;; esac; fi\n', 1)),
            AssertionError, identity + "/pass-lines")

    # Exit from the dispatcher before the runner can report success.
    red("return-code", lambda: runner_check(
        expected, source.replace(anchor, anchor + "  exit 1\n", 1)),
        AssertionError, identity + "/return-code")

    # Run the real vector leg, then fail THIS suite inside the fixture.
    def own_failure(text, status=7):
        label = "own-suite-failure" if status == 7 else "own-suite-failure-" + str(status)
        red(label, lambda: runner_check(
            expected, text, fail_own=status), AssertionError, identity + "/return-code")

    for status in (1, 2, 7):
        own_failure(source, status)
    propagation = '  if "$@"; then :; else failed=1; fi'
    if source.count(propagation) != 1:
        raise AssertionError(identity + "/red-fixture")

    # The failure RED must go not-red if run_gate swallows the suite's exit.
    red("swallowed-own-failure", lambda: own_failure(
        source.replace(propagation, '  "$@" || true', 1)),
        AssertionError, identity + "/own-suite-failure/not-red")

    # Selective wrappers must defeat the corresponding failure RED.
    for status in (1, 2):
        wrapper = ('tolerate_own() { "$@"; local rc=$?; if [ "$rc" -eq '
                   + str(status) + ' ]; then return 0; fi; return "$rc"; }\n')
        mutant = wrapper + source.replace(
            anchor, anchor + '  set -- tolerate_own "$@"\n', 1)
        red("tolerate-" + str(status),
            lambda: own_failure(mutant, status), AssertionError,
            identity + "/own-suite-failure-" + str(status) + "/not-red")

    # Suppressed output cannot hide additional or re-argued own-suite calls.
    for label, command in (
        ("duplicate-own-call", '  "$@" >/dev/null 2>&1'),
        ("wrong-own-argv", '  "$@" --unexpected >/dev/null 2>&1 || true'),
        ("embedded-own-call", '  python3 -I -B -c "import runpy; '
         "runpy.run_path('$here/check_opf_init_p0.py', run_name='__main__')"
         '" --self-test --red-on-revert >/dev/null 2>&1 || true'),
    ):
        red(label, lambda command=command: runner_check(
            expected, source.replace(propagation, propagation + "\n" + command, 1)),
            AssertionError, identity + "/own-argv")

    # Drop a required flag from exactly this suite's registration.
    own_lines = [line for line in source.splitlines(keepends=True)
                 if line.startswith('run_gate "opf-init-p0-selftest"')]
    if len(own_lines) != 1 or own_lines[0].count(" --red-on-revert") != 1:
        raise AssertionError(identity + "/red-fixture")
    red("dropped-own-flag", lambda: runner_check(
        expected, source.replace(own_lines[0],
                                 own_lines[0].replace(" --red-on-revert", "", 1), 1)),
        AssertionError, identity + "/own-argv")

    # Presence, including an empty value, must refuse before any bash launch.
    for value in ("", "nested-argv.log"):
        with patch.dict(os.environ, {"p0_log": value}):
            with patch("subprocess.Popen", side_effect=AssertionError(
                    identity + "/recursion/unexpected-launch")) as launch:
                red("nested-invocation", lambda: runner_check(expected), RuntimeError,
                    identity + "/cannot-evaluate/recursion")
                if launch.call_count:
                    raise AssertionError(identity + "/recursion/unexpected-launch")

    # Scrub only our registration's environment. Substitute a bounded entry
    # probe for the suite script: it loads the real runner_check, but forbids
    # every nested Popen even if the recursion guard is reverted. A nonzero
    # outer runner exit alone is insufficient: require the exact refusal and
    # zero launch attempts recorded by the child after env -i.
    with tempfile.TemporaryDirectory(prefix="opf-recursion-red-") as tmp:
        os.chmod(tmp, 0o700)
        report = Path(tmp) / "refusal.txt"
        nested = Path(tmp) / Path(__file__).name
        nested.write_text(
            "import os, runpy\n"
            "from pathlib import Path\n"
            "from unittest.mock import patch\n"
            f"scope = runpy.run_path({str(Path(__file__).resolve())!r})\n"
            f"assert {'p0_log'!r} not in os.environ\n"
            "with patch('subprocess.Popen', side_effect=AssertionError('nested launch')) as launch:\n"
            "    try:\n"
            "        scope['runner_check'](())\n"
            "    except RuntimeError as exc:\n"
            "        assert not launch.called\n"
            f"        Path({str(report)!r}).write_text(str(exc), encoding='utf-8')\n"
            "    else:\n"
            "        raise AssertionError('recursion accepted')\n"
            "raise SystemExit(2)\n", encoding="utf-8")
        nested.chmod(0o600)
        own_line = own_lines[0]
        script_arg = '"$here/check_opf_init_p0.py"'
        if own_line.count("python3 ") != 1 or own_line.count(script_arg) != 1:
            raise AssertionError(identity + "/red-fixture")
        scrubbed = own_line.replace(
            "python3 ", "env -i PATH=/usr/bin:/bin " + shlex.quote(sys.executable) + " ",
            1).replace(script_arg, shlex.quote(str(nested)), 1)
        refusal_identity = identity + "/scrubbed-environment/wrong-refusal"
        try:
            try:
                runner_check(expected, source.replace(own_line, scrubbed, 1))
            except AssertionError as exc:
                if str(exc) != identity + "/return-code":
                    raise
            else:
                raise AssertionError("scrubbed runner accepted")
            refusal = report.read_text(encoding="utf-8")
        except Exception as exc:
            # Missing/unreadable reports and unexpected runner outcomes are
            # failures of this RED, not harness cannot-evaluate outcomes.
            raise AssertionError(refusal_identity) from exc
        if refusal != identity + "/cannot-evaluate/recursion":
            raise AssertionError(refusal_identity)
        # The child writes this exact report only after asserting zero Popen
        # attempts. Announce the RED only once that evidence has been read.
        print("RED scrubbed-environment -> " + identity + "/cannot-evaluate/recursion")
        print("PASS " + identity + "/scrubbed-environment/no-nested-launch")

    # A harmless competing executable makes reverting the function safe.
    # The normal check requires exactly one own call in the fixture's log.
    with tempfile.TemporaryDirectory(prefix="opf-runner-path-") as tmp:
        os.chmod(tmp, 0o700)
        if os.pathsep in tmp:
            raise RuntimeError(identity + "/cannot-evaluate/pathsep")
        stub = Path(tmp) / "python3"
        stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o700)
        changed_path = "PATH=" + shlex.quote(tmp) + ":$PATH\n" + source
        runner_check(expected, changed_path)
        print("PASS " + identity + "/in-runner-path")

        original_popen = subprocess.Popen
        removed = 0

        def without_function(*args, **kwargs):
            nonlocal removed
            command = list(args[0])
            if command[4].startswith("python3() ( exec "):
                command[4] = command[4].split("\n", 1)[1]
                removed += 1
            return original_popen(command, *args[1:], **kwargs)

        with patch("subprocess.Popen", side_effect=without_function):
            red("removed-function-shim", lambda: runner_check(expected, changed_path),
                AssertionError, identity + "/pass-lines")
        if removed != 1:
            raise AssertionError(identity + "/in-runner-path/removed-count")

    # Remove every execute bit, including for root. Permit only the probe:
    # a reverted interception guard must never launch the real runner.
    original_chmod = Path.chmod
    original_popen = subprocess.Popen
    launches = 0
    probe_body = ("type -P dirname >/dev/null && test -x /bin/sh && "
                  "type -P python3")

    def non_executable(path, mode, *args, **kwargs):
        if path.name == "python3":
            mode = 0o600
        return original_chmod(path, mode, *args, **kwargs)

    def probe_only(*args, **kwargs):
        nonlocal launches
        launches += 1
        command = args[0] if args else kwargs.get("args", ())
        if (launches > 1 or len(command) != 6 or command[3] != "-c"
                or command[4] != probe_body):
            raise AssertionError(identity + "/interception/unexpected-launch")
        return original_popen(*args, **kwargs)

    with patch.object(Path, "chmod", non_executable), \
            patch("subprocess.Popen", side_effect=probe_only):
        red("non-executable-fixture", lambda: runner_check(expected), RuntimeError,
            identity + "/cannot-evaluate/interception")
        if launches != 1:
            raise AssertionError(identity + "/interception/launch-count")

    # Remove dirname from the probe's PATH while retaining the fixture.
    # The exact-body/launch-count guard also prevents a bypassed probe from
    # reaching a real runner when this interception check is reverted.
    launches = 0

    def without_dirname(*args, **kwargs):
        kwargs["env"] = dict(kwargs["env"], PATH=kwargs["env"]["TMPDIR"])
        return probe_only(*args, **kwargs)

    with patch("subprocess.Popen", side_effect=without_dirname):
        red("missing-dirname", lambda: runner_check(expected), RuntimeError,
            identity + "/cannot-evaluate/interception")
        if launches != 1:
            raise AssertionError(identity + "/interception/launch-count")

    # Discriminates only where TMPDIR has no default ACL and the process
    # lacks CAP_DAC_OVERRIDE (root commonly has it in CI containers).
    # Either a default ACL overriding umask or that capability makes this
    # case non-discriminating: it can pass with or without the chmods.
    saved = os.umask(0o200)
    try:
        try:
            runner_check(expected)
        except Exception as exc:
            raise AssertionError(identity + "/umask-0200") from exc
    finally:
        os.umask(saved)
    print("PASS " + identity + "/umask-0200")

    # Block every launch so a reverted guard cannot execute a real gate.
    # Reset tempfile's cache as well as TMPDIR to exercise this exact directory.
    with tempfile.TemporaryDirectory(prefix="opf-path" + os.pathsep) as tmp:
        with patch.dict(os.environ, {"TMPDIR": tmp}), patch.object(tempfile, "tempdir", tmp):
            with patch("subprocess.Popen", side_effect=AssertionError(
                    identity + "/pathsep/unexpected-launch")) as launch:
                red("tmpdir-pathsep", lambda: runner_check(expected), RuntimeError,
                    identity + "/cannot-evaluate/pathsep")
                if launch.call_count:
                    raise AssertionError(identity + "/pathsep/unexpected-launch")

    _runner_write_only_fd_check()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--red-on-revert", action="store_true")
    parser.add_argument("--vectors-only", action="store_true")
    args = parser.parse_args()
    try:
        f = fixtures()
        ids = run_vectors(p0, f)
        if not args.vectors_only:
            if args.red_on_revert:
                red_on_revert(Path(p0.__file__).read_text(encoding="utf-8"), f)
            runner_check(ids)
            print("PASS runner/declared-test-executes")
            if args.red_on_revert:
                runner = Path(__file__).resolve().parent / "run_all_checks.sh"
                text = runner.read_text(encoding="utf-8")
                lines = [line for line in text.splitlines(keepends=True)
                         if line.startswith('run_gate "opf-init-p0-selftest"')]
                check(len(lines) == 1, "runner/unique-registration")
                try:
                    runner_check(ids, text.replace(lines[0], ""))
                except AssertionError as exc:
                    check(str(exc) == "runner/declared-test-executes/pass-lines",
                          "runner/wrong-red")
                else:
                    raise AssertionError("runner/registration-not-red")
                print("RED own-dispatch -> runner/declared-test-executes/pass-lines")
                runner_red_checks(ids)
        return 0
    except AssertionError as exc:
        print("SELF-TEST FAIL " + str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print("CANNOT-EVALUATE P0 harness: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
