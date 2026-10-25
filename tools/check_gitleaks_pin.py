#!/usr/bin/env python3
"""Require the gitleaks version .github/workflows/quality.yml pins on the local PATH. Offline, stdlib only.

Quality runs once per merge train, on the train head, so a member head gets no Quality run at all and its
evidence is the local tools/run_all_checks.sh run before its push, which includes the gitleaks scan only
when gitleaks is installed. This gate makes the scan required locally: tools/run_all_checks.sh runs it
right after its gitleaks block, so the user-local ~/.local/bin fallback that block appends to PATH applies
here too. Nothing enforces that the local run happens before each member push: that is a process rule,
not enforced per head.

  check_gitleaks_pin.py              check the local gitleaks against the pin
  check_gitleaks_pin.py --self-test  run the deterministic vectors (stub binaries, no real gitleaks)

The pin is the GITLEAKS_VERSION: X.Y.Z entry of the env: of the one step named Secrets (gitleaks) in
quality.yml, the step that downloads that version and verifies it by checksum. Only that entry decides:
any other line of quality.yml naming GITLEAKS_VERSION (another step's or job's env:, a run script
assigning it), other than a comment and a ${GITLEAKS_VERSION} expansion in that step's run: script, is
refused, so a value set elsewhere can never stand in for the step's own. The local binary is the first
gitleaks on PATH; its version is the whole output of gitleaks version, read as X.Y.Z with an optional
leading v. It fails (exit 1) when no gitleaks is on PATH and when the version it reports differs from the
pin, so a member push cannot rest on a local run that skipped the scan or ran another version. Any other
argument is refused (exit 2).

Exit convention (matches the repo's gates):
  0  the gitleaks on PATH reports the pinned version
  1  gitleaks is not on PATH, or it reports another version
  2  cannot evaluate: quality.yml is unreadable; it holds no step named Secrets (gitleaks) or more than
     one; that step has no env: or more than one, or an env: line that is not a plain KEY: value entry;
     its env: has no GITLEAKS_VERSION entry or more than one, or one whose value is not a bare X.Y.Z;
     another line names GITLEAKS_VERSION; gitleaks version fails, times out or prints anything but one
     version; or the arguments are not recognized

NOT CHECKED: the binary's checksum (CI verifies the downloaded archive; this gate trusts the version a
local binary reports, so a binary that misreports its version passes), the scan's own result (the gitleaks
block of tools/run_all_checks.sh runs it), a gitleaks reached other than through PATH, and a version the
workflow reaches without naming GITLEAKS_VERSION literally (a hard-coded download URL, or a name built at
run time and written to GITHUB_ENV or assigned in a script).
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
# The gitleaks step's first line, once its indentation is removed, and the name its env: pins.
STEP_LINE = "- name: Secrets (gitleaks)"
PIN_KEY = "GITLEAKS_VERSION"
# The pin entry, once the indentation of the step's env: entries is removed.
PIN_RE = re.compile(r"GITLEAKS_VERSION: ([0-9]+\.[0-9]+\.[0-9]+)")
# Every entry of the step's env:, once that indentation is removed: a plain key and a value, so a quoted
# or escaped key, a merge key, a flow mapping or a continuation line cannot set the name unseen.
ENV_ENTRY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*: \S.*")
# The only spelling of the name the step's run: script may carry: its expansion.
EXPANSION = "${GITLEAKS_VERSION}"
# The whole output of gitleaks version, once surrounding whitespace is removed.
VERSION_RE = re.compile(r"v?([0-9]+\.[0-9]+\.[0-9]+)")
VERSION_TIMEOUT = 60


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _block_end(lines, start, indent):
    """The index of the first line after lines[start] that is not blank and is indented at most indent."""
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or _indent(lines[end]) > indent):
        end += 1
    return end


def read_pin(workflow_text):
    """(version, None) for the GITLEAKS_VERSION entry of the gitleaks step's env:, or (None, why not)."""
    lines = workflow_text.split("\n")
    steps = [index for index, line in enumerate(lines) if line.lstrip(" ") == STEP_LINE]
    if len(steps) != 1:
        return None, "{} steps are named Secrets (gitleaks), not one".format(len(steps))
    step = steps[0]
    key_indent = _indent(lines[step]) + 2
    step_end = _block_end(lines, step, key_indent - 2)
    envs = [index for index in range(step + 1, step_end) if lines[index] == " " * key_indent + "env:"]
    runs = [index for index in range(step + 1, step_end)
            if lines[index].startswith(" " * key_indent + "run:")]
    if len(envs) != 1 or len(runs) != 1:
        return None, "the Secrets (gitleaks) step has {} env: and {} run: keys, not one each".format(
            len(envs), len(runs))
    env_end = _block_end(lines, envs[0], key_indent)
    run_end = _block_end(lines, runs[0], key_indent)
    entry_indent = " " * (key_indent + 2)
    entries = []
    for index in range(envs[0] + 1, env_end):
        line = lines[index]
        if not line.strip() or line.lstrip(" ").startswith("#"):
            continue
        if not (line.startswith(entry_indent) and ENV_ENTRY_RE.fullmatch(line[len(entry_indent):])):
            return None, "line {} of the step's env: is not a plain KEY: value entry".format(index + 1)
        if PIN_KEY in line:
            entries.append(index)
    if len(entries) != 1:
        return None, "the step's env: names {} on {} lines, not one".format(PIN_KEY, len(entries))
    for index, line in enumerate(lines):
        if (PIN_KEY not in line or index == entries[0] or line.lstrip(" ").startswith("#")
                or (runs[0] <= index < run_end and PIN_KEY not in line.replace(EXPANSION, ""))):
            continue
        return None, ("line {} names {} outside the step's env: entry, a comment and the step's {} "
                      "expansions".format(index + 1, PIN_KEY, EXPANSION))
    pin = PIN_RE.fullmatch(lines[entries[0]][len(entry_indent):])
    if pin is None:
        return None, "the step's {} value on line {} is not a bare X.Y.Z".format(PIN_KEY, entries[0] + 1)
    return pin.group(1), None


def evaluate(workflow_text, path):
    """(exit code, report line) for the pin in workflow_text and the first gitleaks on path."""
    pin, why = read_pin(workflow_text)
    if pin is None:
        return 2, "CANNOT EVALUATE: {}: {}, so there is no pin to compare".format(WORKFLOW_REL, why)
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

    pin_line = "          GITLEAKS_VERSION: 8.30.1\n"
    step = ("      # Bump GITLEAKS_VERSION and GITLEAKS_SHA256 together.\n"
            "      - name: Secrets (gitleaks)\n"
            "        shell: bash\n"
            "        env:\n" + pin_line +
            "          GITLEAKS_SHA256: 00\n"
            "        run: |\n"
            "          set -euo pipefail\n"
            "          curl -o g.tgz \"https://x.invalid/v${GITLEAKS_VERSION}/g_${GITLEAKS_VERSION}.tgz\"\n")
    other = ("      - name: Leak denylist\n"
             "        shell: bash\n"
             "        run: python3 -I -B tools/check_leaks.py\n")
    head = "jobs:\n  secrets:\n    runs-on: ubuntu-latest\n    steps:\n"
    workflow = head + step + "\n" + other
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
        sha_line = "          GITLEAKS_SHA256: 00\n"
        step_pin = workflow.replace(pin_line, pin_line.replace("8.30.1", "'8.30.2'"))
        stray_env = "        env:\n          GITLEAKS_VERSION: 8.30.1\n"
        other_run = "        run: python3 -I -B tools/check_leaks.py\n"
        job_env = "    runs-on: ubuntu-latest\n    env:\n      GITLEAKS_VERSION: 8.30.1\n"
        # A comment and the run script's expansions name the pin too; only the step's env: entry decides.
        check("controls-pass", evaluate(workflow, pinned_path)[0], 0)
        # Each of these is cannot-evaluate. The local stub reports 8.30.1, so a reader that takes the pin
        # from any GITLEAKS_VERSION: X.Y.Z line passes these: the step's own value quoted (CI downloads
        # 8.30.2) beside an unquoted copy in another step's or the job's env:, the step's entry missing
        # beside such a copy, no step named Secrets (gitleaks), a step with no env:, a second env: key,
        # a pin nested deeper than an env: entry, a run script assigning the name, and a quoted or
        # escaped second key that YAML reads as GITLEAKS_VERSION.
        for name, text in (
                ("no-pin-line", workflow.replace(pin_line, "")),
                ("two-pin-lines", workflow.replace(sha_line, sha_line + pin_line)),
                ("quoted-pin", workflow.replace("8.30.1", "'8.30.1'")),
                ("trailing-comment", workflow.replace("8.30.1", "8.30.1  # pin")),
                ("two-component-pin", workflow.replace("8.30.1", "8.30")),
                ("stray-copy-other-step", step_pin.replace(other_run, stray_env + other_run)),
                ("stray-copy-step-key-missing",
                 workflow.replace(pin_line, "").replace(other_run, stray_env + other_run)),
                ("stray-copy-job-env", step_pin.replace("    runs-on: ubuntu-latest\n", job_env)),
                ("no-gitleaks-step", workflow.replace("Secrets (gitleaks)", "Secret scan (gitleaks)")),
                ("two-gitleaks-steps", head + step + step),
                ("step-without-env", workflow.replace("        env:\n" + pin_line, "").replace(
                    "    runs-on: ubuntu-latest\n", job_env)),
                ("two-env-keys", workflow.replace("        run: |\n", "        env:\n        run: |\n")),
                ("pin-nested-deeper", workflow.replace(pin_line, "  " + pin_line)),
                ("run-assigns-pin", workflow.replace(
                    "          set -euo pipefail\n",
                    "          set -euo pipefail\n          GITLEAKS_VERSION=8.30.2\n")),
                ("quoted-key-copy", workflow.replace(sha_line, sha_line
                                                     + "          \"GITLEAKS_VERSION\": '8.30.2'\n")),
                ("escaped-key-copy", workflow.replace(sha_line, sha_line
                                                      + "          \"GITLEAKS_\\x56ERSION\": '8.30.2'\n"))):
            check(name, evaluate(text, pinned_path)[0], 2)
        # End to end over this checkout's quality.yml: the gate itself, launched isolated with PATH set to
        # a stub directory, exits 0 on the pinned version and 1 on another version and on no gitleaks.
        live_pin, live_why = read_pin((ROOT / WORKFLOW_REL).read_bytes().decode("utf-8"))
        if live_pin is None:
            failures.append("live: {}: {}".format(WORKFLOW_REL, live_why))
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
