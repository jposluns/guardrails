#!/usr/bin/env python3
"""Require the gitleaks version .github/workflows/quality.yml pins on the local PATH. Offline, stdlib only.

Quality runs on a pull request only when a label is added to it, and the merge lane labels only the train
pull request, so a member head gets no Quality run at all and its evidence is the local
tools/run_all_checks.sh run before its push, which includes the gitleaks scan only when gitleaks is
installed. This gate makes the scan required locally: tools/run_all_checks.sh runs it right after its
gitleaks block, so the user-local ~/.local/bin fallback that block appends to PATH applies here too, and
the run FAILS, never ends in a pass with the scan not run. Nothing enforces that the local run happens
before each member push: that is a process rule, not enforced per head.

  check_gitleaks_pin.py              check the local gitleaks against the pin
  check_gitleaks_pin.py --self-test  run the deterministic vectors (stub binaries, no real gitleaks)

The pin is the GITLEAKS_VERSION: X.Y.Z entry of the env: of the one step named Secrets (gitleaks) in
quality.yml, the step that downloads that version and verifies it by checksum. That step is read only as a
step item of a job's steps: list under the top-level jobs: mapping: every line that is not blank, a comment
or block scalar text sits under the nearest such line above it indented less, and the step's item must sit
under steps:, steps: under a job id key, the job id under jobs: at column 0, each at the indentation of its
parent's first child (so an indentless steps: list is not read). A line that reads as the step once its
indentation is removed anywhere else is refused and named, block scalar text included (the lines under a
key or sequence item whose value is | or >, indented past it), and so are a YAML document marker or
directive and a quoted scalar its line does not close, behind which a YAML reader can read as text a line
this reader would place. Only that entry decides:
any other line of quality.yml naming GITLEAKS_VERSION (another step's or job's env:, a run script
assigning it), other than a comment and a ${GITLEAKS_VERSION} expansion in that step's run: script, is
refused, so a value set elsewhere can never stand in for the step's own. The file is read as LF lines
once one trailing carriage return is removed from each: a line that still carries a carriage return, or
carries U+0085, U+2028 or U+2029, is refused, because a YAML reader may break a line there and read a
key this line reader would not see. The local binary is the first
gitleaks on PATH; its version is the whole output of gitleaks version, read as X.Y.Z with an optional
leading v. It fails (exit 1) when no gitleaks is on PATH and when the version it reports differs from the
pin, so a member push cannot rest on a local run that skipped the scan or ran another version. Any other
argument is refused (exit 2).

Exit convention (matches the repo's gates):
  0  the gitleaks on PATH reports the pinned version
  1  gitleaks is not on PATH, or it reports another version
  2  cannot evaluate: quality.yml is unreadable; a line of it still carries a carriage return once its
     one trailing CR is removed, or carries U+0085, U+2028 or U+2029; a line of it is a YAML document
     marker or directive, or opens a quoted scalar it does not close; a line reads as the step named
     Secrets (gitleaks) but is not a step item of a job's steps: list; it holds no such step item or more
     than one; that step has no env: or more than one, or an env: line that is not a
     plain KEY: value entry; its env: has no GITLEAKS_VERSION entry or more than one, or one whose value
     is not a bare X.Y.Z; another line names GITLEAKS_VERSION; gitleaks version fails, times out or
     prints anything but one version; or the arguments are not recognized

NOT CHECKED: the binary's checksum (CI verifies the downloaded archive; this gate trusts the version a
local binary reports, so a binary that misreports its version passes), the scan's own result (the gitleaks
block of tools/run_all_checks.sh runs it), a gitleaks reached other than through PATH, a version the
workflow reaches without naming GITLEAKS_VERSION literally (a hard-coded download URL, or a name built at
run time and written to GITHUB_ENV or assigned in a script), and a repeated jobs:, job id or steps: key, of
which a YAML reader keeps one: this reader reads the step under either copy, so a step under the copy that
reader drops still passes.
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
# A job id key of the top-level jobs: mapping, once its indentation is removed: a plain id with no value.
JOB_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*:")
# A workflow line once its indentation is removed: its sequence and explicit-key indicators, its key (one-line
# quoted or plain) with the separator after it, if it has one, then the tags and anchors its value starts
# with and the | or > that opens a block scalar, if the value is one.
NODE_RE = re.compile(r"((?:[-?:](?: +|$))*)"
                     r"((?:'(?:[^']|'')*'|\"(?:[^\"\\]|\\.)*\"|[^\s#'\"](?:\S| +(?=[^\s#]))*?):(?: +|$))?"
                     r"(?:[!&]\S*(?: +|$))*([|>])?")
# A quoted scalar its line closes.
QUOTED_RE = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"\\]|\\.)*\"")
# A YAML document marker or directive, at column 0.
DOCUMENT_RE = re.compile(r"(?:---|\.\.\.)(?: |$)|%")
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
# Characters a YAML reader may take as a line break where this reader splits on LF alone, so a key
# hidden behind one would be read there and not here; each is refused once a line's one trailing CR
# is removed.
LINE_BREAKS = ("\r", "\x85", "\u2028", "\u2029")


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _block_end(lines, start, indent):
    """The index of the first line after lines[start] that is not blank and is indented at most indent."""
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or _indent(lines[end]) > indent):
        end += 1
    return end


def _step_items(lines):
    """(the indices of the step items named Secrets (gitleaks), None), or (None, why not).

    Every line that is not blank, a comment or block scalar text is placed under its parent, the nearest such
    line above it indented less. A step item is a sequence item whose parent is a steps: key, whose parent is
    a job id key, whose parent is jobs: at column 0, each at the indentation of its parent's first child. A
    line that reads as the step once its indentation is removed and is not such an item is refused, block
    scalar text included (the lines under a key or sequence item whose value is | or >, indented past it),
    and so are a YAML document marker or directive and a quoted scalar its line does not close, behind which a
    YAML reader can read as text a line this reader would place."""
    parents = {}
    children = {}
    placed = []
    scalar = None
    for index, line in enumerate(lines):
        text = line.lstrip(" ")
        indent = len(line) - len(text)
        if scalar is not None and (not text or indent > scalar):
            if text == STEP_LINE:
                return None, ("line {} names the Secrets (gitleaks) step inside a block scalar, which "
                              "YAML reads as text".format(index + 1))
            continue
        scalar = None
        if not text or text.startswith("#"):
            continue
        if indent == 0 and DOCUMENT_RE.match(text):
            return None, "line {} is a YAML document marker or directive".format(index + 1)
        while placed and placed[-1][0] >= indent:
            placed.pop()
        parents[index] = placed[-1][1] if placed else None
        children.setdefault(parents[index], []).append(index)
        placed.append((indent, index))
        node = NODE_RE.match(text)
        indicators, key, block = node.groups()
        if block:
            # The scalar's text is every later line indented past its key, or past its last indicator.
            if key:
                scalar = indent + len(indicators)
            elif indicators:
                scalar = indent + len(indicators.rstrip(" ")) - 1
            else:
                scalar = -1 if parents[index] is None else _indent(lines[parents[index]])
        elif text[node.end():node.end() + 1] in ("'", "\"") and QUOTED_RE.match(text, node.end()) is None:
            return None, "line {} opens a quoted scalar it does not close".format(index + 1)
    items = []
    for index in parents:
        if lines[index].lstrip(" ") != STEP_LINE:
            continue
        chain = [index]
        while parents[chain[-1]] is not None:
            chain.append(parents[chain[-1]])
        if not (len(chain) == 4 and lines[chain[1]].lstrip(" ") == "steps:"
                and JOB_KEY_RE.fullmatch(lines[chain[2]].lstrip(" ")) and lines[chain[3]] == "jobs:"
                and all(_indent(lines[link]) == _indent(lines[children[parents[link]][0]])
                        for link in chain[:3])):
            return None, ("line {} names the Secrets (gitleaks) step outside a step item of a job's steps: "
                          "list".format(index + 1))
        items.append(index)
    return items, None


def read_pin(workflow_text):
    """(version, None) for the GITLEAKS_VERSION entry of the gitleaks step's env:, or (None, why not)."""
    lines = [line[:-1] if line.endswith("\r") else line for line in workflow_text.split("\n")]
    for index, line in enumerate(lines):
        if any(mark in line for mark in LINE_BREAKS):
            return None, "line {} carries a carriage return, U+0085, U+2028 or U+2029".format(index + 1)
    steps, why = _step_items(lines)
    if steps is None:
        return None, why
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

        def indented(text, spaces):
            return "".join(" " * spaces + line if line.strip() else line for line in text.splitlines(True))

        # The step is read only as a step item of a job's steps: list under the top-level jobs:. Each of
        # these holds the step's lines, its pin included, only where YAML reads no step (the real step
        # removed), so a reader that takes any line reading as the step once its indentation is removed
        # passes each: inside another step's run: | text, inside a job id's block scalar, after a document
        # marker that opens a root block scalar, after a run: value that opens a quote its line does not
        # close, as an item of a steps: key under another step's with:, as an item of a job's needs:, under
        # a sequence item of jobs:, under a top-level key other than jobs:, and as an item indented less
        # than the first item of its steps: list.
        script_step = ("      - name: Leak denylist\n"
                       "        shell: bash\n"
                       "        run: |\n"
                       "          python3 -I -B tools/check_leaks.py\n"
                       "          exit 0\n")
        in_script = head + script_step + indented(step, 4)
        for name, text in (
                ("step-in-run-text", in_script),
                ("step-in-job-scalar",
                 head + other + "  copy: |\n    runs-on: ubuntu-latest\n    steps:\n" + step),
                ("step-in-root-scalar", "--- |\n" + workflow),
                ("step-after-open-quote", head + other.replace(
                    other_run, "        run: 'python3 -I -B tools/check_leaks.py\n") + step + "        '\n"),
                ("step-under-with", head + "      - uses: actions/checkout@v4\n        with:\n"
                 "          steps:\n" + indented(step, 6)),
                ("step-under-needs", "jobs:\n  secrets:\n    runs-on: ubuntu-latest\n    needs:\n" + step),
                ("step-under-jobs-item", "jobs:\n  - secrets:\n      runs-on: ubuntu-latest\n      steps:\n"
                 + indented(step, 2)),
                ("step-outside-jobs", head + other + "copy:\n  secrets:\n    steps:\n" + step),
                ("step-indented-less-than-first-item", head + indented(other, 2) + step)):
            check(name, evaluate(text, pinned_path)[0], 2)
        # The refusal names the line, and a copy inside a block scalar is refused beside the real step too.
        beside = workflow + script_step + indented(step, 4)
        for name, text in (("step-in-run-text-names-line", in_script),
                           ("step-copy-beside-real-step", beside)):
            check(name, read_pin(text)[1],
                  "line {} names the Secrets (gitleaks) step inside a block scalar, which YAML reads as "
                  "text".format(text.split("\n").index("          " + STEP_LINE) + 1))
        # Controls: a run: | block that ends at the step's item, quoted values closed on their lines and a
        # comment at column 0 between steps each leave the step read.
        for name, text in (
                ("control-scalar-before-step", head + script_step + step),
                ("control-closed-quotes", head + "      - uses: actions/setup-python@v5\n"
                 "        name: \"Set up \\\"Python\\\"\"\n"
                 "        with:\n          python-version: '3.14'\n" + step),
                ("control-column-0-comment", head + other + "# a note\n" + step)):
            check(name, evaluate(text, pinned_path)[0], 0)
        # A carriage return, U+0085, U+2028 or U+2029 inside a line can hide a second key from this LF
        # reader that a YAML reader breaks out (the step's value stays 8.30.1 here, so a reader without
        # the refusal passes each); a trailing CR on every line (a CRLF file) is removed and reads as LF.
        hidden = "          \"GITLEAKS_\\x56ERSION\": '8.30.2'\n"
        check("crlf-file", evaluate(workflow.replace("\n", "\r\n"), pinned_path)[0], 0)
        for name, mark in (("cr-hides-key", "\r"), ("nel-hides-key", "\x85"),
                           ("ls-hides-key", "\u2028"), ("ps-hides-key", "\u2029")):
            check(name, evaluate(workflow.replace(sha_line, sha_line[:-1] + mark + hidden), pinned_path)[0], 2)
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
