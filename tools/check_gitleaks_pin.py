#!/usr/bin/env python3
"""Require the gitleaks version .github/workflows/quality.yml pins on the local PATH. Offline, stdlib only.

Quality runs once per merge train, on the train head, so a member head's evidence is the local
tools/run_all_checks.sh run before its push, and that run includes the gitleaks scan only when gitleaks
is installed. This gate makes the scan required locally: tools/run_all_checks.sh runs it right after its
gitleaks block, so the user-local ~/.local/bin fallback that block appends to PATH applies here too.

  check_gitleaks_pin.py              check the local gitleaks against the pin
  check_gitleaks_pin.py --self-test  run the deterministic vectors (stub binaries, no real gitleaks)

The pin is the one GITLEAKS_VERSION: X.Y.Z line of quality.yml, the version CI downloads and verifies by
checksum. The local binary is the first gitleaks on PATH; its version is the whole output of
gitleaks version, read as X.Y.Z with an optional leading v. It fails (exit 1) when no gitleaks is on
PATH and when the version it reports differs from the pin, so a member push cannot rest on a local run
that skipped the scan or ran another version. Any other argument is refused (exit 2).

Exit convention (matches the repo's gates):
  0  the gitleaks on PATH reports the pinned version
  1  gitleaks is not on PATH, or it reports another version
  2  cannot evaluate: quality.yml is unreadable or holds no single pin line, gitleaks version fails, times
     out or prints anything but one version, or the arguments are not recognized

NOT CHECKED: the binary's checksum (CI verifies the downloaded archive; this gate trusts the version a
local binary reports, so a binary that misreports its version passes), the scan's own result (the gitleaks
block of tools/run_all_checks.sh runs it), and a gitleaks reached other than through PATH.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_gitleaks_pin.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_REL = ".github/workflows/quality.yml"
# The pin line as quality.yml writes it under the gitleaks step's env:.
PIN_RE = re.compile(r" +GITLEAKS_VERSION: ([0-9]+\.[0-9]+\.[0-9]+)")
# The whole output of gitleaks version, once surrounding whitespace is removed.
VERSION_RE = re.compile(r"v?([0-9]+\.[0-9]+\.[0-9]+)")
VERSION_TIMEOUT = 60


def pinned_version(workflow_text):
    """The version of the one pin line, or None when there is no pin line or more than one."""
    pins = [match.group(1) for match in map(PIN_RE.fullmatch, workflow_text.split("\n")) if match]
    return pins[0] if len(pins) == 1 else None


def evaluate(workflow_text, path):
    """(exit code, report line) for the pin in workflow_text and the first gitleaks on path."""
    pin = pinned_version(workflow_text)
    if pin is None:
        return 2, ("CANNOT EVALUATE: {} holds no single GITLEAKS_VERSION: X.Y.Z line, so there is no "
                   "pin to compare".format(WORKFLOW_REL))
    binary = shutil.which("gitleaks", path=path)
    if binary is None:
        return 1, ("FAIL: gitleaks is not on PATH; the local run requires gitleaks {} (the version and "
                   "checksum {} pins) on PATH or in ~/.local/bin".format(pin, WORKFLOW_REL))
    # The literal name, resolved by the child's own PATH search over the same path, launches the binary
    # shutil.which found.
    try:
        proc = subprocess.run(["gitleaks", "version"], capture_output=True, text=True, errors="replace",
                              timeout=VERSION_TIMEOUT, env=dict(os.environ, PATH=path))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 2, "CANNOT EVALUATE: {} version did not complete: {}".format(binary, exc)
    if proc.returncode != 0:
        return 2, "CANNOT EVALUATE: {} version exited {}".format(binary, proc.returncode)
    reported = VERSION_RE.fullmatch(proc.stdout.strip())
    if reported is None:
        return 2, "CANNOT EVALUATE: {} version printed {!r}, not one version".format(binary, proc.stdout)
    if reported.group(1) != pin:
        return 1, ("FAIL: {} reports gitleaks {}, but {} pins {}; install the pinned version".format(
            binary, reported.group(1), WORKFLOW_REL, pin))
    return 0, "PASS: {} reports gitleaks {}, the version {} pins".format(binary, pin, WORKFLOW_REL)


def run(root=ROOT):
    try:
        text = (root / WORKFLOW_REL).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print("CANNOT EVALUATE: {} cannot be read: {}".format(WORKFLOW_REL, exc))
        return 2
    code, line = evaluate(text, os.environ.get("PATH", ""))
    print(line)
    return code


def self_test():
    failures = []
    count = 0

    def check(name, got, want):
        nonlocal count
        count += 1
        if got != want:
            failures.append("{}: got {!r}, expected {!r}".format(name, got, want))

    workflow = "        env:\n          GITLEAKS_VERSION: 8.30.1\n          GITLEAKS_SHA256: 00\n"
    base = Path(tempfile.mkdtemp(prefix="gitleaks-pin-"))
    try:
        def stub(name, body):
            directory = base / name
            directory.mkdir()
            script = directory / "gitleaks"
            script.write_text("#!/bin/sh\n" + body)
            script.chmod(0o755)
            return str(directory)

        empty = str(base / "empty")
        (base / "empty").mkdir()
        # The two required red cases: no gitleaks on PATH, and another version on PATH.
        check("absent", evaluate(workflow, empty)[0], 1)
        check("absent-empty-path", evaluate(workflow, "")[0], 1)
        for name, output, want in (
                ("pinned", "8.30.1", 0),
                ("pinned-v-prefix", "v8.30.1", 0),
                ("pinned-trailing-newline", "8.30.1\\n", 0),
                ("older-patch", "8.30.0", 1),
                ("newer-patch", "8.30.2", 1),
                ("longer-patch", "8.30.10", 1),
                ("pin-prefix-of-longer", "18.30.1", 1),
                ("newer-minor", "8.31.1", 1),
                ("suffix", "8.30.1-rc1", 2),
                ("two-versions", "8.30.1 8.30.1", 2),
                ("empty-output", "", 2)):
            check(name, evaluate(workflow, stub(name, "printf '" + output + "'\n"))[0], want)
        check("version-fails", evaluate(workflow, stub("fails", "exit 3\n"))[0], 2)
        # The first gitleaks on PATH decides: a pinned binary behind another version is still a finding.
        check("first-on-path", evaluate(workflow, os.pathsep.join((
            stub("first", "printf '8.29.0'\n"), stub("second", "printf '8.30.1'\n"))))[0], 1)
        pinned_path = stub("pin-source", "printf '8.30.1'\n")
        for name, text in (
                ("no-pin-line", "        env:\n          GITLEAKS_SHA256: 00\n"),
                ("two-pin-lines", workflow + "          GITLEAKS_VERSION: 8.30.1\n"),
                ("quoted-pin", workflow.replace("8.30.1", "'8.30.1'")),
                ("trailing-comment", workflow.replace("8.30.1", "8.30.1  # pin"))):
            check(name, evaluate(text, pinned_path)[0], 2)
        # End to end over this checkout's quality.yml: the gate itself, launched isolated with PATH set to
        # a stub directory, exits 0 on the pinned version and 1 on another version and on no gitleaks.
        live_pin = pinned_version((ROOT / WORKFLOW_REL).read_bytes().decode("utf-8"))
        if live_pin is None:
            failures.append("live: {} holds no single pin line".format(WORKFLOW_REL))
        else:
            major, minor, patch = live_pin.split(".")
            other = "{}.{}.{}".format(major, minor, int(patch) + 1)
            for name, path, want in (
                    ("live-pinned", stub("live-pinned", "printf '" + live_pin + "'\n"), 0),
                    ("live-other-version", stub("live-other", "printf '" + other + "'\n"), 1),
                    ("live-absent", empty, 1)):
                proc = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).resolve())],
                                      capture_output=True, text=True, timeout=VERSION_TIMEOUT * 2,
                                      env={"PATH": path})
                check(name, proc.returncode, want)
    finally:
        shutil.rmtree(base, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: {} gitleaks-pin vectors, the absent and other-version cases red".format(count))
    return 0


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args == ["--self-test"]:
        return self_test()
    if args:
        print("CANNOT EVALUATE: unrecognized arguments {!r}; only --self-test is accepted".format(args))
        return 2
    return run()


if __name__ == "__main__":
    sys.exit(main())
